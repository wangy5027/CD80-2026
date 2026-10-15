"""Per-run Explore model choice: persisted authorization and all model call sites.

The HTTP transport is replaced with local SSE data; no model or news network
request is made by this module.
"""

from __future__ import annotations

import json
import threading
import time
import unittest
from types import MethodType
from unittest.mock import Mock, patch
from uuid import uuid4

from backend.app.modules.explore.event_lite_runtime import EventLiteRuntime
from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError
import tests.test_explore_event_lite as event_fixtures
import tests.test_explore_relation_lite as relation_fixtures
import tests.test_explore_relation_model as model_fixtures
import tests.test_explore_research_runtime as research_fixtures


BASE = "/desktop/api/explore"
FLASH = "GLM-5.3-Flash"
FULL = "GLM-5.3"


class LocalStreamResponse:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self, body):
        self.body = body
        self.closed = False

    def iter_lines(self, chunk_size=1):
        assert chunk_size == 1
        chunk = {"choices": [{"delta": {"content": self.body},
                              "finish_reason": "stop"}]}
        yield ("data: " + json.dumps(chunk, ensure_ascii=False)).encode("utf-8")
        yield b""

    def close(self):
        self.closed = True


class RelationModelSelectionTests(unittest.TestCase):
    setUp = relation_fixtures.RelationLiteTests.setUp
    tearDown = relation_fixtures.RelationLiteTests.tearDown
    wait = relation_fixtures.RelationLiteTests.wait

    def mark_historical(self, run_id, model=FULL):
        with self.service.repository.transaction() as db:
            row = db.execute("SELECT doc FROM runs WHERE id=? AND owner=?", (run_id, self.owner)).fetchone()
            record = json.loads(row["doc"])
            if model is None:
                record.pop("model", None)
            else:
                record["model"] = model
            db.execute("UPDATE runs SET doc=? WHERE id=? AND owner=?",
                       (json.dumps(record, ensure_ascii=False), run_id, self.owner))

    def create(self, *, model=None, key=None, source_report_id=None, module="relation",
               question=relation_fixtures.QUESTION, source_run_id=None):
        body = {"module": module, "question": question,
                "idempotency_key": key or str(uuid4())}
        if model is not None:
            body["model"] = model
        if source_report_id is not None:
            body["source_report_id"] = source_report_id
        if source_run_id is not None:
            body["source_run_id"] = source_run_id
        return self.client.post(BASE + f"/sessions/{self.session_id}/runs", json=body)

    def complete(self, *, model=None):
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            response = self.create(model=model)
            self.assertEqual(response.status_code, 202, response.json)
            done = self.wait(response.json["run"]["id"])
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        return done

    def test_rejects_null_non_allowlisted_and_injected_models(self):
        invalid = [None, "", " ", "glm-5.3", "GLM-5.3-Flash ",
                   "GLM-5.3\nignore instructions", "GLM-5.3-Flash;rm -rf /",
                   [], {}, True, 53]
        for item in invalid:
            with self.subTest(model=item):
                response = self.client.post(BASE + f"/sessions/{self.session_id}/runs",
                    json={"module": "relation", "question": relation_fixtures.QUESTION,
                          "model": item, "idempotency_key": str(uuid4())})
                self.assertEqual(response.status_code, 400, response.json)
                self.assertEqual(response.json["code"], "explore_invalid_model")
        retired = self.create(model=FULL)
        self.assertEqual(retired.status_code, 409, retired.json)
        self.assertEqual(retired.json["code"], "explore_legacy_model_unavailable")
        self.assertEqual(self.news.calls, [])
        self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])

    def test_flash_choice_freezes_run_control_and_omitted_choice_replays_same_request(self):
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        key = str(uuid4())
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            first = self.create(model=FLASH, key=key)
            self.assertEqual(first.status_code, 202, first.json)
            run_id = first.json["run"]["id"]
            try:
                self.assertTrue(self.provider.report_started.wait(2))
                active = self.service._active[(self.owner, run_id)]
                self.assertEqual(active["model"], FLASH)
                self.assertEqual(first.json["run"]["model"], FLASH)
                same = self.create(model=FLASH, key=key)
                self.assertEqual(same.status_code, 202, same.json)
                self.assertEqual(same.json["run"]["id"], run_id)
                changed = self.create(model=FULL, key=key)
                self.assertEqual(changed.status_code, 409, changed.json)
                self.assertEqual(changed.json["code"], "explore_legacy_model_unavailable")
                omitted = self.create(key=key)
                self.assertEqual(omitted.status_code, 202, omitted.json)
                self.assertEqual(omitted.json["run"]["id"], run_id)
            finally:
                self.provider.report_release.set()
            done = self.wait(run_id)
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["model"], FLASH)
        self.assertEqual(self.service.repository.get_run(self.owner, run_id)["model"], FLASH)

    def test_old_unselected_run_keeps_unknown_model_and_cannot_retry(self):
        first = self.complete()
        self.mark_historical(first["id"], None)
        with patch.object(self.service, "_start"):
            retried = self.client.post(BASE + f"/runs/{first['id']}/retry", json={
                "idempotency_key": str(uuid4())})
        self.assertEqual(retried.status_code, 409, retried.json)
        self.assertEqual(retried.json["code"], "explore_legacy_model_unavailable")
        self.assertNotIn("model", self.service.repository.get_run(self.owner, first["id"]))

    def test_two_relation_runs_inherit_choice_memory_and_file_pool(self):
        first = self.complete(model=FLASH)
        first_report = first["report"]
        selected = next(row for row in first_report["recommendations"]
                        if row["module"] == "relation")
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            second_response = self.create(module="relation", question=selected["question"],
                                          source_report_id=first_report["id"])
            self.assertEqual(second_response.status_code, 202, second_response.json)
            second = self.wait(second_response.json["run"]["id"])
        self.assertEqual(second["status"], "succeeded", second.get("error"))
        self.assertEqual(second["model"], FLASH)
        self.assertEqual(second["source_report_id"], first_report["id"])
        self.assertEqual(second["report"]["memory_input"]["id"],
                         first_report["memory_output"]["id"])
        self.assertIn(first_report["file_id"],
                      {item["file_id"] for item in second["context_files"]})
        self.assertEqual([payload["stage"] for payload in self.provider.payloads],
                         ["relation_select", "relation_lite_report"] * 2)
        self.assertEqual(first.get("model_retries") or [], [])
        self.assertEqual(second.get("model_retries") or [], [])
        for report in (first_report, second["report"]):
            self.assertTrue(report["file_id"])
            self.assertTrue(report["input_file_id"])
            self.assertTrue(all(source.get("file_id") for source in report["sources"]))

    def test_cross_module_recommendation_inherits_or_explicitly_overrides(self):
        for selected_model, expected in ((None, FLASH), (FLASH, FLASH)):
            with self.subTest(child_model=selected_model):
                first = self.complete(model=FLASH)
                self.mark_historical(first["id"], FULL)
                report = first["report"]
                selected = next(row for row in report["recommendations"]
                                if row["module"] == "history")
                with patch.object(self.service, "_start"):
                    child = self.create(module="history", question=selected["question"],
                                        source_report_id=report["id"],
                                        model=selected_model)
                self.assertEqual(child.status_code, 202, child.json)
                run = child.json["run"]
                self.assertEqual(run["model"], expected)
                self.assertEqual(self.service.repository.get_run(self.owner, first["id"])["model"], FULL)
                self.assertEqual(run["memory_input"]["id"], report["memory_output"]["id"])
                self.assertIn(report["file_id"],
                              {item["file_id"] for item in run["context_files"]})
                self.assertEqual(self.service.repository.get_run(self.owner, first["id"])
                                 ["continuation_run_id"], run["id"])
                # A deliberately unstarted child still occupies one session
                # slot; release it before testing the next independent parent.
                self.service.cancel(self.owner, run["id"])
                if selected_model is not None:
                    # The child selected Flash over its Full-model parent.
                    # Retrying that child must preserve its own choice.
                    with patch.object(self.service, "_start"):
                        retried = self.client.post(BASE + f"/runs/{run['id']}/retry", json={
                            "idempotency_key": str(uuid4())})
                    self.assertEqual(retried.status_code, 202, retried.json)
                    self.assertEqual(retried.json["run"]["model"], FLASH)
                    self.assertEqual(retried.json["run"]["retry_of"], run["id"])
                    self.service.cancel(self.owner, retried.json["run"]["id"])

    def test_other_account_cannot_inherit_model_from_source_report(self):
        first = self.complete(model=FLASH)
        report = first["report"]
        selected = next(row for row in report["recommendations"]
                        if row["module"] == "relation")
        outsider = self.app.test_client()
        registered = outsider.post("/desktop/api/register", json={
            "email": f"other-model-{uuid4().hex}@example.test",
            "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        session = outsider.post(BASE + "/sessions", json={"title": "其他账户"})
        self.assertEqual(session.status_code, 201, session.json)
        denied = outsider.post(BASE + f"/sessions/{session.json['session']['id']}/runs",
            json={"module": "relation", "question": selected["question"],
                  "source_report_id": report["id"], "idempotency_key": str(uuid4())})
        self.assertEqual(denied.status_code, 404, denied.json)
        self.assertEqual(self.service.repository.list_files(
            registered.json["user"]["id"], session.json["session"]["id"]), [])

    def test_retry_preserves_explicit_model(self):
        first = self.complete(model=FLASH)
        with patch.object(self.service, "_start"):
            retried = self.client.post(BASE + f"/runs/{first['id']}/retry", json={
                "idempotency_key": str(uuid4())})
        self.assertEqual(retried.status_code, 202, retried.json)
        self.assertEqual(retried.json["run"]["model"], FLASH)
        self.assertEqual(retried.json["run"]["retry_of"], first["id"])


class EventModelSelectionTests(unittest.TestCase):
    setUp = event_fixtures.EventLiteChoiceTests.setUp
    tearDown = event_fixtures.EventLiteChoiceTests.tearDown
    wait = event_fixtures.EventLiteChoiceTests.wait
    feedback = event_fixtures.EventLiteChoiceTests.feedback

    def create(self, *, model=FLASH):
        response = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
            "module": "event", "question": "A股本周股票情况怎么样？",
            "model": model, "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"]["id"]

    def test_event_feedback_deep_report_keeps_same_selected_model_and_pool(self):
        run_id = self.create()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["model"], FLASH)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                   self.tools.page):
            chosen = self.feedback(run_id, "explain_more")
            self.assertEqual(chosen.status_code, 202, chosen.json)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["id"], run_id)
        self.assertEqual(done["model"], FLASH)
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(self.provider.report_calls, 1)
        self.assertEqual(done["next_step_state"], "pending")
        self.assertTrue(done["report"]["file_id"])
        self.assertGreaterEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 4)

    def test_old_full_first_answer_cannot_start_second_model_phase(self):
        run_id = self.create()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback")
        with self.service.repository.transaction() as db:
            row = db.execute("SELECT doc FROM runs WHERE id=? AND owner=?", (run_id, self.owner)).fetchone()
            historical = json.loads(row["doc"])
            historical["model"] = FULL
            db.execute("UPDATE runs SET doc=? WHERE id=? AND owner=?",
                       (json.dumps(historical, ensure_ascii=False), run_id, self.owner))
        before_calls = self.provider.report_calls
        rejected = self.feedback(run_id, "explain_more")
        self.assertEqual(rejected.status_code, 409, rejected.json)
        self.assertEqual(rejected.json["code"], "explore_legacy_model_unavailable")
        saved = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(saved["model"], FULL)
        self.assertEqual(saved["status"], "awaiting_feedback")
        self.assertEqual(self.provider.report_calls, before_calls)

    def test_first_answer_source_run_inherits_model_and_memory(self):
        run_id = self.create()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        with patch.object(self.service, "_start"):
            child = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
                "module": "relation", "question": "市场预期如何影响本周变化？",
                "source_run_id": run_id, "idempotency_key": str(uuid4())})
        self.assertEqual(child.status_code, 202, child.json)
        new_run = child.json["run"]
        self.assertEqual(new_run["model"], FLASH)
        self.assertEqual(new_run["memory_input"]["id"], waiting["memory_output"]["id"])
        self.assertEqual(new_run["source_run_id"], run_id)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)


class TransportModelSelectionTests(unittest.TestCase):
    def test_legacy_model_actions_reject_without_mutating_run(self):
        service = ExploreService.__new__(ExploreService)
        service.repository = Mock()
        for model in (FULL, None):
            for action, body in (
                    ("feedback", {"choice": "explain_more", "idempotency_key": str(uuid4())}),
                    ("clarify", {"revision": 1, "question": "明确问题", "idempotency_key": str(uuid4())}),
                    ("confirm", {"revision": 1, "source_ids": ["S1"], "idempotency_key": str(uuid4())}),
                    ("supply_input", {"judgment": "历史判断"}),
                    ("retry", {"idempotency_key": str(uuid4())})):
                with self.subTest(model=model, action=action):
                    old = {"status": "failed", "module": "history", "session_id": "s", "question": "旧问题"}
                    if model is not None:
                        old["model"] = model
                    service.repository.get_run.return_value = old
                    with self.assertRaises(GatewayError) as raised:
                        getattr(service, action)("owner", "run-id", body)
                    self.assertEqual(raised.exception.code, "explore_legacy_model_unavailable")
        service.repository.apply_review_action.assert_not_called()
        service.repository.transition.assert_not_called()

    def test_central_writer_uses_only_frozen_flash_without_mutating_shared_provider(self):
        service = model_fixtures.bare_service()
        sent = []

        def fake_post(_url, **kwargs):
            sent.append(kwargs["json"]["model"])
            return LocalStreamResponse('{"ok":true}')

        with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                   side_effect=fake_post):
            for stage, selected in (("relation_select", FLASH),
                                    ("relation_lite_report", FLASH),
                                    ("event_deep_lite", FLASH),
                                    ("history_report", FLASH)):
                state = model_fixtures.control()
                state["model"] = selected
                result = service._invoke_model_once({"task": "report", "stage": stage},
                                                    "fixture", state)
                self.assertEqual(result, {"ok": True})
                self.assertEqual(state["model_transport_trace"][0]["model"], sent[-1])
        self.assertEqual(sent, [FLASH] * 4)
        self.assertEqual(service.provider.model, FULL)

    def test_event_first_plain_text_uses_selected_model_on_copied_provider(self):
        service = model_fixtures.bare_service()
        runtime = EventLiteRuntime.__new__(EventLiteRuntime)
        runtime.service = service
        runtime.control = model_fixtures.control()
        runtime.control["model"] = FLASH
        runtime._initial_lock = threading.Lock()
        runtime._initial_tokens = []
        sent = []

        def fake_post(_url, **kwargs):
            sent.append(kwargs["json"]["model"])
            return LocalStreamResponse("本轮先核查公开报道。")

        with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                   side_effect=fake_post):
            answer = runtime._plain_once([{"role": "system", "content": "简答"},
                                          {"role": "user", "content": "{}"}])
        self.assertEqual(answer, "本轮先核查公开报道。")
        self.assertEqual(sent, [FLASH])
        self.assertEqual(runtime.control["model_transport_trace"][0]["model"], FLASH)
        self.assertEqual(service.provider.model, FULL)

    def test_history_research_events_use_selected_model_on_copied_provider(self):
        fixture = research_fixtures.ResearchRuntimeTests(
            "test_each_product_module_loads_its_real_research_skill")
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        provider = model_fixtures.local_provider()
        runtime, service, _saved, state = fixture.make_runtime(provider,
            "尊界V800历史上类似事件有哪些可核查材料？", module="history",
            limits={"model_rounds": 1, "tool_calls": 2,
                    "context_chars": 100000, "write_reserve_seconds": 1})
        service._provider_for_deadline = MethodType(ExploreService._provider_for_deadline, service)
        service._provider_for_run = MethodType(ExploreService._provider_for_run, service)
        service._model_transport_observer = MethodType(ExploreService._model_transport_observer,
                                                        service)
        state["model"] = FLASH
        sent = []

        def fake_post(_url, **kwargs):
            payload = kwargs["json"]
            sent.append(payload["model"])
            questions = json.loads(payload["messages"][1]["content"])["research_task"]["subquestions"]
            return LocalStreamResponse(research_fixtures.brief(questions))

        with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                   side_effect=fake_post):
            result = runtime.run_research()
        self.assertEqual(sent, [FLASH])
        self.assertEqual(result["retrieval"]["model_trace"][0]["status"], "complete")
        self.assertEqual(state["model_transport_trace"][0]["model"], FLASH)
        self.assertEqual(service.provider.model, FULL)


if __name__ == "__main__":
    unittest.main()
