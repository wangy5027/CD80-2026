"""The relation selector, reporter, and recommended continuation have separate contracts.

All runs use local Flask, model, news, and reader fixtures; no live service is
called. The tests exercise the persisted run and file-pool behavior, not only a
prompt helper in isolation.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry
from tests.test_explore_relation_lite import (
    QUESTION, RelationNews, RelationProvider, RelationReader,
)


BASE = "/desktop/api/explore"
RAW_MARKER = "PRIVATE_RELATION_MODEL_RESPONSE_MUST_NOT_PERSIST"


class SelectionContractProvider(RelationProvider):
    def __init__(self):
        super().__init__()
        self.prompts = []
        self.selection_override = None
        self.selection_raw = None

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        stage = payload["stage"]
        self.prompts.append((stage, messages[0]["content"]))
        if stage == "relation_select" and self.selection_raw is not None:
            self.payloads.append(payload)
            yield self.selection_raw
            return
        if stage == "relation_select" and self.selection_override is not None:
            self.payloads.append(payload)
            yield json.dumps(self.selection_override, ensure_ascii=False)
            return
        yield from super().json_tokens(messages, model, stopped, on_response)


class RelationSelectionContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="relation-selection-contract-")
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"),
            "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False,
            "EXPLORE_RELATION_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30,
        })
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={
            "email": f"relation-contract-{uuid4().hex}@example.test",
            "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.owner = registered.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.provider = SelectionContractProvider()
        self.news = RelationNews()
        self.reader = RelationReader()
        self.service.provider = self.provider
        self.service.search_tools = ToolRegistry((self.news.tool(),))
        session = self.client.post(BASE + "/sessions", json={"title": "关联选择契约"})
        self.assertEqual(session.status_code, 201, session.json)
        self.session_id = session.json["session"]["id"]

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def _submit(self, question=QUESTION, *, source_report_id=None):
        payload = {"module": "relation", "question": question,
                   "idempotency_key": str(uuid4())}
        if source_report_id is not None:
            payload["source_report_id"] = source_report_id
        created = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json=payload)
        self.assertEqual(created.status_code, 202, created.json)
        return created.json["run"]["id"]

    def _wait(self, run_id, statuses=None):
        statuses = statuses or {"succeeded", "partial", "failed"}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(BASE + f"/runs/{run_id}")
            self.assertEqual(response.status_code, 200, response.json)
            run = response.json["run"]
            if run["status"] in statuses:
                return run
            time.sleep(.01)
        self.fail(f"relation run {run_id} did not settle")

    def _completed(self, *, question=QUESTION, source_report_id=None):
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            done = self._wait(self._submit(question, source_report_id=source_report_id))
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        return done

    def test_selection_and_report_receive_only_their_skill_steps_and_schema(self):
        done = self._completed()
        self.assertEqual(done["report"]["report_format"], "relation_lite@2")
        self.assertEqual([stage for stage, _ in self.provider.prompts],
                         ["relation_select", "relation_lite_report"])
        selection = self.provider.prompts[0][1]
        report = self.provider.prompts[1][1]
        self.assertIn("## 第一步：选择实体", selection)
        self.assertNotIn("## 第二步：", selection)
        self.assertNotIn("## 第三步：", selection)
        self.assertIn('"needs_clarification":false', selection)
        self.assertIn('"needs_clarification":true', selection)
        self.assertNotIn("source_refs", selection)
        self.assertNotIn("recommendations 必须恰好三项", selection)
        self.assertIn("## 第二步：", report)
        self.assertIn("## 第三步：", report)
        self.assertNotIn("## 第一步：", report)
        self.assertNotIn("needs_clarification", report)
        self.assertIn("source_refs", report)
        self.assertIn("recommendations", report)
        self.assertIn("完整问题", report)

    def test_invalid_selection_records_typed_field_without_model_text(self):
        entities = [{"name": "华为", "reason": "核对合作职责"}]
        cases = (
            ({"question": "", "entities": entities},
             "needs_clarification", "missing", "boolean", "missing", None),
            ({"needs_clarification": None, "question": "", "entities": entities},
             "needs_clarification", "null", "boolean", "null", None),
            ({"needs_clarification": "false", "question": RAW_MARKER, "entities": entities},
             "needs_clarification", "string", "boolean", "wrong_type", None),
            ({"needs_clarification": 0, "question": "", "entities": entities},
             "needs_clarification", "integer", "boolean", "wrong_type", None),
            ({"needs_clarification": False, "question": ""},
             "entities", "missing", "array", "missing", None),
            ({"needs_clarification": False, "question": "", "entities": None},
             "entities", "null", "array", "null", None),
            ({"needs_clarification": False, "question": "",
              "entities": [{"name": 42, "reason": "核对主体"}]},
             "entities[0].name", "integer", "string", "wrong_type", 0),
        )
        for value, field, actual, expected, reason, index in cases:
            with self.subTest(field=field, actual=actual):
                self.provider.selection_override = value
                run = self._wait(self._submit())
                self.assertEqual(run["status"], "failed")
                self.assertEqual(run["error_code"], "explore_invalid_intent")
                self.assertIsNone(run["report"])
                errors = run.get("model_output_errors")
                self.assertIsInstance(errors, list)
                self.assertEqual(len(errors), 1, errors)
                issue = errors[0]
                self.assertEqual(issue["stage"], "relation_select")
                self.assertEqual(issue["code"], "explore_invalid_intent")
                self.assertEqual(issue["field"], field)
                self.assertEqual(issue["actual_type"], actual)
                self.assertEqual(issue["expected_type"], expected)
                self.assertEqual(issue["reason"], reason)
                self.assertTrue(issue["message"])
                self.assertLess(len(issue["message"]), 120)
                self.assertFalse({"raw", "value", "response", "content"} & set(issue))
                if index is not None:
                    self.assertEqual(issue.get("index"), index)
                self.assertNotIn(RAW_MARKER, json.dumps(run, ensure_ascii=False))
        self.assertEqual(len(self.provider.prompts), len(cases))
        self.assertEqual(self.news.calls, [])

    def test_native_true_still_waits_for_clarification_without_search(self):
        self.provider.selection_override = {
            "needs_clarification": True,
            "question": "这里的 V800 指哪一款具体车型？",
            "entities": [],
        }
        run = self._wait(self._submit("这个 V800 是谁负责的？"),
                         {"awaiting_clarification", "failed"})
        self.assertEqual(run["status"], "awaiting_clarification", run.get("error"))
        self.assertIn("V800", run["review"]["question"])
        self.assertEqual([payload["stage"] for payload in self.provider.payloads],
                         ["relation_select"])
        self.assertEqual(self.news.calls, [])
        self.assertIsNone(run["report"])
        self.assertEqual(run.get("model_output_errors") or [], [])

    def test_truncated_selection_json_records_decoder_metadata_without_raw(self):
        raw = ('{"needs_clarification":false,"question":"","entities":'
               '[{"name":"' + RAW_MARKER + '"')
        self.provider.selection_raw = raw
        run = self._wait(self._submit())
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "explore_invalid_model_output")
        self.assertIsNone(run["report"])
        errors = run["model_output_errors"]
        self.assertEqual(len(errors), 1)
        issue = errors[0]
        self.assertEqual(issue["stage"], "json_parse")
        self.assertEqual(issue["model_stage"], "relation_select")
        self.assertEqual(issue["code"], "explore_invalid_model_output")
        self.assertEqual(issue["reason"], "syntax")
        self.assertEqual(issue["response_chars"], len(raw))
        self.assertIs(type(issue["position"]), int)
        self.assertEqual(issue["line"], 1)
        self.assertIs(type(issue["column"]), int)
        self.assertEqual(issue["coordinate_system"], "normalized_json")
        self.assertIn(issue["decoder_message"],
                      {"Expecting ',' delimiter", "Expecting value",
                       "Unterminated string starting at"})
        self.assertEqual(self.news.calls, [])
        self.assertNotIn(RAW_MARKER, json.dumps(run, ensure_ascii=False))
        self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])

    def test_failed_diagnostic_write_does_not_replace_selection_error(self):
        self.provider.selection_override = {
            "needs_clarification": "false", "question": RAW_MARKER,
            "entities": [{"name": "华为", "reason": "核对主体"}],
        }
        repository = self.service.repository
        original_transition = repository.transition
        diagnostic_attempts = []

        def reject_diagnostic(*args, **kwargs):
            if "model_output_errors" in (kwargs.get("fields") or {}):
                diagnostic_attempts.append(1)
                # Simulate a deleted or stale run rejecting this auxiliary
                # write. The original validation failure must still surface.
                raise GatewayError("诊断记录不可写", 409, "explore_cancelled")
            return original_transition(*args, **kwargs)

        with patch.object(repository, "transition", side_effect=reject_diagnostic):
            run = self._wait(self._submit())
        self.assertEqual(diagnostic_attempts, [1])
        self.assertEqual(run["status"], "failed")
        self.assertEqual(run["error_code"], "explore_invalid_intent")
        self.assertNotIn("诊断记录不可写", run.get("error") or "")
        self.assertEqual(run.get("model_output_errors") or [], [])
        self.assertIsNone(run["report"])
        self.assertEqual(self.news.calls, [])
        self.assertNotIn(RAW_MARKER, json.dumps(run, ensure_ascii=False))

    def test_recommended_relation_followup_keeps_memory_pool_and_final_choice(self):
        first = self._completed()
        first_report = first["report"]
        relation_option = next(row for row in first_report["recommendations"]
                               if row["module"] == "relation")
        second = self._completed(question=relation_option["question"],
                                 source_report_id=first_report["id"])
        second_report = second["report"]
        self.assertEqual(first["next_step_state"], "pending")
        current_first = self.service.repository.get_run(self.owner, first["id"])
        self.assertEqual(current_first["next_step_state"], "continued")
        self.assertEqual(current_first["continuation_run_id"], second["id"])
        self.assertEqual(second["source_report_id"], first_report["id"])
        self.assertEqual(second["question"], relation_option["question"])
        self.assertEqual([payload["stage"] for payload in self.provider.payloads],
                         ["relation_select", "relation_lite_report",
                          "relation_select", "relation_lite_report"])
        self.assertEqual(len(self.provider.prompts), 4)
        self.assertEqual(first.get("model_retries") or [], [])
        self.assertEqual(second.get("model_retries") or [], [])

        selected_files = second["context_files"]
        self.assertIn(first_report["file_id"],
                      {item["file_id"] for item in selected_files})
        self.assertTrue(any(first_report["summary"][:40] in item.get("content_excerpt", "")
                            for item in selected_files))
        for payload in self.provider.payloads[2:]:
            self.assertEqual(payload["context_files"], selected_files)
            self.assertIn(first_report["summary"], payload["research_memory"]["content"])
        self.assertEqual(second_report["memory_input"]["id"], first_report["memory_output"]["id"])
        self.assertEqual(second_report["memory_output"]["state"]["stages"][-1]["phase"], "report")

        for report in (first_report, second_report):
            self.assertEqual([row["module"] for row in report["recommendations"]],
                             ["event", "relation", "history"])
            self.assertTrue(report["sources"])
            self.assertTrue(report["input_file_id"])
            self.assertTrue(report["file_id"])
            self.assertTrue(all(source.get("file_id") for source in report["sources"]))
        expected_files = {file_id for report in (first_report, second_report)
                          for file_id in ([report["input_file_id"], report["file_id"]] +
                                          [source["file_id"] for source in report["sources"]])}
        pool = self.service.repository.list_files(self.owner, self.session_id)
        self.assertEqual({item["id"] for item in pool}, expected_files)

        finished = self.client.post(BASE + f"/runs/{second['id']}/finish", json={
            "idempotency_key": str(uuid4())})
        self.assertEqual(finished.status_code, 200, finished.json)
        self.assertEqual(finished.json["run"]["next_step_state"], "finished")
        self.assertTrue(finished.json["session"]["closed_at"])
        self.assertEqual(self.client.get(BASE + f"/reports/{second_report['id']}").status_code, 200)
        self.assertEqual(self.client.get(BASE + f"/sessions/{self.session_id}/files").status_code, 200)
        self.assertEqual(self.client.get(BASE + f"/sessions/{self.session_id}/files/"
                                         f"{first_report['file_id']}").status_code, 200)
        self.assertEqual(self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
            "module": "relation", "question": "会话结束后再问",
            "idempotency_key": str(uuid4())}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
