"""Research memory is sent to the actual model calls across Explore modules.

The Flask app, SQLite database, search tool, page reader and model are all local
test doubles.  The tests inspect the messages handed to the provider, not only
the run metadata that records which memory should have been used.
"""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.explore.repository import timestamp
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


BASE = "/desktop/api/explore"
A_MARKER = "A轮观察：制动支架责任边界仍待核实"
A_DEEP_MARKER = "A轮深入：先区分公开说法与合同责任"
B_MARKER = "B轮关联：华为与江淮的具体分工尚待原文"
C_MARKER = "C轮对照：旧案例只支持条件性比较"
OLD_QUOTE = "2019-05-02 公开材料记载某汽车零部件争议，后续责任认定仍须核对合同。"
OLD_URL = "https://public.example.test/2019-auto-part"
HISTORY_QUESTION = f"待验证判断：类似汽车零部件争议均由技术参与方承担责任？请用历史材料对照 {OLD_URL}"


class LocalSearch:
    def __init__(self):
        self.calls = []

    def tool(self):
        def search(arguments, _context):
            self.calls.append(copy.deepcopy(arguments))
            return ToolResult(content={"searchedAt": timestamp()}, sources=[{
                "id": "local-v800", "title": "尊界V800制动支架事件中华为与江淮的公开说法",
                "url": "https://public.example.test/current-v800",
                "publishedAt": timestamp(),
                "snippet": "尊界V800制动支架事件涉及华为与江淮；公开说法尚不足以证明合同责任。",
            }])

        return ToolSpec("web_search", "本地新闻检索", {"type": "object",
            "properties": {"query": {"type": "string"}, "count": {"type": "integer"},
                           "freshness": {"type": "string"}},
            "required": ["query"]}, search, max_calls=8)


class LocalReader:
    def __init__(self):
        self.calls = []

    def tool(self, _dns_mode="system"):
        def read(arguments, _context):
            self.calls.append(copy.deepcopy(arguments))
            url = arguments["url"]
            body = OLD_QUOTE if url == OLD_URL else (
                "尊界V800制动支架事件中，华为与江淮的公开表述可以查阅；合同条款仍待核对。")
            return ToolResult(content={"status": "ok", "text": body,
                               "title": "本地公开材料", "publisher": "测试来源",
                               "final_url": url})

        return ToolSpec("fetch_public_page", "本地网页正文", {"type": "object",
            "properties": {"url": {"type": "string"},
                           "target_section": {"type": "string"},
                           "max_chars": {"type": "integer"}},
            "required": ["url"]}, read, max_calls=8)


class ChainProvider:
    model = "memory-chain-fixture"

    def __init__(self):
        self.calls = []
        self.fail_relation_select = False
        self.relation_started = None
        self.relation_release = None

    def _capture(self, kind, messages):
        copied = copy.deepcopy(messages)
        payload = json.loads(copied[1]["content"])
        self.calls.append({"kind": kind, "messages": copied, "payload": payload})
        return payload

    def events(self, messages, model, stopped, on_response, *, tools=None, **_options):
        payload = self._capture("events", messages)
        if "research_task" not in payload:
            # This is the first, plain-text answer of event-lite.
            yield {"type": "token", "text": A_MARKER + "。这是本轮 AI 初步解释，尚未独立核实。"}
            yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}
            return

        # This is the real ResearchRuntime's first turn in history.  It must
        # have read the explicitly supplied old source before asking the model.
        seed = payload["url_reads"][0]
        assert seed["result"]["final_url"] == OLD_URL
        assert OLD_QUOTE in json.dumps(seed, ensure_ascii=False)
        evidence_id = seed["result"]["evidence_id"]
        task = payload["research_task"]
        claim = {"id": "C1", "text": "2019年曾出现汽车零部件责任争议",
                 "type": "sourced_statement", "event_at": "2019-05-02",
                 "event_date_quote": OLD_QUOTE,
                 "supports": [{"evidence_id": evidence_id, "quote": OLD_QUOTE}]}
        answer = {"coverage": [{"question_id": row["id"], "status": "answered",
                                "claim_ids": ["C1"], "gap": "", "next_action": ""}
                               for row in task["subquestions"]],
                  "claims": [claim], "conflicts": [], "done": True}
        yield {"type": "token", "text": json.dumps(answer, ensure_ascii=False)}
        yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}

    def json_tokens(self, messages, model, stopped, on_response):
        payload = self._capture("json_tokens", messages)
        stage = payload.get("stage")
        if stage == "event_deep_lite":
            refs = [row["id"] for row in payload.get("sources") or []]
            result = {"title": "制动支架事件进一步解释", "summary": A_DEEP_MARKER,
                "key_findings": [A_DEEP_MARKER],
                "sections": [{"heading": "事实与推测", "body": A_DEEP_MARKER,
                              "source_refs": refs[:1]}],
                "recommendations": [
                    {"module": "event", "question": "本次事件还有哪些公开进展？", "reason": "补查事实"},
                    {"module": "relation", "question": "华为与江淮在尊界V800制动支架事件中有什么关联？",
                     "reason": "梳理主体关系"},
                    {"module": "history", "question": HISTORY_QUESTION, "reason": "对照旧案例"},
                ]}
        elif stage == "relation_select":
            if self.relation_started is not None:
                self.relation_started.set()
                self.relation_release.wait(timeout=3)
            if self.fail_relation_select:
                raise GatewayError("测试关联选择失败", 502, "fixture_relation_failed")
            result = {"needs_clarification": False, "question": "",
                      "entities": [{"name": "华为", "reason": "核对技术参与的边界"},
                                   {"name": "江淮汽车", "reason": "核对制造方的边界"}]}
        elif stage == "relation_lite_report":
            refs = [row["id"] for row in payload.get("sources") or []]
            result = {"title": "主体关联", "summary": B_MARKER,
                "key_findings": [B_MARKER], "uncertainties": [], "limitations": [],
                "sections": [{"heading": "关系边界", "body": B_MARKER,
                              "source_refs": refs[:1]}],
                "entities": ["华为", "江淮汽车"], "relationships": [],
                "recommendations": [
                    {"module": "event", "question": "尊界V800制动支架事件还有什么进展？", "reason": "核查进展"},
                    {"module": "relation", "question": "华为与江淮汽车的分工依据是什么？", "reason": "核对分工"},
                    {"module": "history", "question": HISTORY_QUESTION, "reason": "对照旧案例"},
                ]}
        elif stage == "claim_review":
            brief = payload["research_brief"]
            question_ids = [row["question_id"] for row in brief["coverage"]]
            result = {"claim_reviews": [{"id": item["id"], "decision": "approve",
                         "text": item["text"], "type": item["type"],
                         "support_indices": list(range(len(item["supports"]))),
                         "question_ids": question_ids, "reason": "本地旧材料包含对应日期和原文"}
                        for item in brief["claims"]],
                      "coverage": [{"question_id": row["question_id"],
                                    "status": row["status"], "gap": row.get("gap", ""),
                                    "next_action": row.get("next_action", "")}
                                   for row in brief["coverage"]]}
        elif stage == "review":
            result = {"issues": []}
        elif payload.get("task") == "research_report":
            historical = payload["historical_source_ids"]
            assert historical, "fixture must exercise a real eligible historical case"
            result = {"title": "历史对照", "summary": C_MARKER,
                "key_findings": [C_MARKER], "uncertainties": [], "limitations": [],
                "sections": [{"heading": "历史材料", "body": C_MARKER,
                              "source_refs": historical[:1]}],
                "judgment": payload["judgment"],
                "conclusion": C_MARKER + "，不据此预测本轮走势。",
                "cases": [{"event": "2019年汽车零部件争议",
                           "similarities": "都需要区分技术参与和合同责任",
                           "differences": "事件及合同不同",
                           "supporting_conditions": "有合同和一手公告",
                           "failure_conditions": "资料不足时不能推定相同责任",
                           "source_refs": historical[:1]}],
                "recommendations": []}
        else:
            raise AssertionError(f"unexpected model request: {payload.get('task')}/{stage}")
        yield json.dumps(result, ensure_ascii=False)


class ExploreMemoryChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-memory-chain-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "test-only",
            "IMPORT_LEGACY_HISTORY": False, "EXPLORE_EVENT_LITE_ENABLED": True,
            "EXPLORE_RELATION_LITE_ENABLED": True, "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={
            "email": "memory-chain@example.test", "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.owner = registered.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.provider = ChainProvider()
        self.search = LocalSearch()
        self.reader = LocalReader()
        self.service.provider = self.provider
        self.service.search_tools = ToolRegistry((self.search.tool(),))
        session = self.client.post(BASE + "/sessions", json={"title": "连续研究记忆测试"})
        self.assertEqual(session.status_code, 201, session.json)
        self.session_id = session.json["session"]["id"]

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def submit(self, module, question, **extra):
        response = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
            "module": module, "question": question, "idempotency_key": str(uuid4()), **extra})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"]["id"]

    def wait(self, run_id, statuses=None):
        statuses = statuses or {"succeeded", "partial", "failed", "cancelled"}
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            response = self.client.get(BASE + "/runs/" + run_id)
            self.assertEqual(response.status_code, 200, response.json)
            run = response.json["run"]
            if run["status"] in statuses:
                return run
            time.sleep(.01)
        self.fail(f"run {run_id} did not reach {statuses}")

    def first_event(self, *, explain_more=True):
        run_id = self.submit("event", "尊界V800制动支架事件目前有什么公开说法？")
        initial = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(initial["status"], "awaiting_feedback", initial.get("error"))
        self.assertIn(A_MARKER, initial["initial_answer"])
        self.assertIsNotNone(initial.get("memory_output"))
        if not explain_more:
            feedback = self.client.post(BASE + f"/runs/{run_id}/feedback", json={
                "choice": "understood", "idempotency_key": str(uuid4())})
            self.assertEqual(feedback.status_code, 202, feedback.json)
            return initial, self.wait(run_id, {"succeeded"})
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            feedback = self.client.post(BASE + f"/runs/{run_id}/feedback", json={
                "choice": "explain_more", "idempotency_key": str(uuid4())})
            self.assertEqual(feedback.status_code, 202, feedback.json)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertIn(done["status"], {"succeeded", "partial"}, done.get("error"))
        self.assertIn(A_DEEP_MARKER, done["report"]["summary"])
        return initial, done

    def relation(self, parent, *, source_run_id=False, manual_empty=False):
        kwargs = ({"source_run_id": parent["id"]} if source_run_id else
                  {"source_report_id": parent["report"]["id"]})
        if manual_empty:
            kwargs.update(context_mode="manual", file_ids=[])
        question = ("华为与江淮在尊界V800制动支架事件中有什么关联？" if source_run_id else
                    next(row["question"] for row in parent["report"]["recommendations"]
                         if row["module"] == "relation"))
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit("relation", question, **kwargs)
            done = self.wait(run_id)
        self.assertIn(done["status"], {"succeeded", "partial"}, done.get("error"))
        self.assertIn(B_MARKER, done["report"]["summary"])
        return done

    @staticmethod
    def memory_calls(provider, *, stage=None):
        return [row for row in provider.calls if stage is None or row["payload"].get("stage") == stage]

    def assert_model_received(self, call, memory, *markers):
        actual = call["payload"]["research_memory"]
        self.assertIsInstance(actual, dict, call)
        self.assertEqual(actual["id"], memory["id"])
        self.assertEqual(actual["version"], memory["version"])
        self.assertEqual(actual["content_hash"], memory["content_hash"])
        for marker in markers:
            self.assertIn(marker, actual["content"])
            # The marker must be in the serialized user message sent to GLM.
            self.assertIn(marker, call["messages"][1]["content"])

    def test_event_relation_history_chain_passes_exact_memory_to_each_model_step(self):
        initial, event = self.first_event()
        m1, m2 = initial["memory_output"], event["memory_output"]
        self.assertEqual(m1["version"], 1)
        self.assertEqual(m2["parent_id"], m1["id"])
        self.assertEqual(m2["version"], 2)
        self.assertEqual(m2["chain_id"], m1["chain_id"])
        self.assertIn(A_MARKER, m1["content"])
        self.assertIn(A_MARKER, m2["content"])
        self.assertIn(A_DEEP_MARKER, m2["content"])
        self.assertIsNone(self.provider.calls[0]["payload"]["research_memory"])
        self.assert_model_received(self.memory_calls(self.provider, stage="event_deep_lite")[0],
                                   m1, A_MARKER)

        relation = self.relation(event, manual_empty=True)
        self.assertEqual(relation["context_files"], [])
        self.assertEqual(relation["memory_input"]["id"], m2["id"])
        for stage in ("relation_select", "relation_lite_report"):
            self.assert_model_received(self.memory_calls(self.provider, stage=stage)[0],
                                       m2, A_MARKER, A_DEEP_MARKER)
        m3 = relation["memory_output"]
        self.assertEqual(m3["parent_id"], m2["id"])
        self.assertEqual(m3["version"], 3)
        self.assertIn(B_MARKER, m3["content"])

        history_question = next(row["question"] for row in relation["report"]["recommendations"]
                                if row["module"] == "history")
        with patch("backend.app.modules.explore.research_runtime.build_public_reader_tool",
                   self.reader.tool):
            history_id = self.submit("history", history_question,
                                     source_report_id=relation["report"]["id"])
            history = self.wait(history_id)
        self.assertIn(history["status"], {"succeeded", "partial"}, history.get("error"))
        self.assertEqual(history["workflow_version"], "explore-research@1")
        self.assertEqual(history["memory_input"]["id"], m3["id"])
        self.assertIn(OLD_URL, [row["url"] for row in history["report"]["sources"]])
        history_calls = [row for row in self.provider.calls
                         if "research_task" in row["payload"] and row["kind"] == "events"]
        self.assertTrue(history_calls)
        self.assert_model_received(history_calls[0], m3, A_MARKER, A_DEEP_MARKER, B_MARKER)
        writer_calls = [row for row in self.provider.calls[
                        self.provider.calls.index(history_calls[0]) + 1:]
                        if row["payload"].get("task") == "research_report"]
        self.assertEqual(len(writer_calls), 3)
        for call in writer_calls:
            self.assert_model_received(call, m3, A_MARKER, B_MARKER)
        m4 = history["memory_output"]
        self.assertEqual(m4["parent_id"], m3["id"])
        self.assertEqual(m4["version"], 4)
        for marker in (A_MARKER, A_DEEP_MARKER, B_MARKER, C_MARKER):
            self.assertIn(marker, m4["content"])

        # Historical versions are immutable and reads do not schedule model work.
        before = len(self.provider.calls)
        listing = self.client.get(BASE + f"/sessions/{self.session_id}/memory",
                                  query_string={"run_id": history_id})
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(listing.json["latest"]["id"], m4["id"])
        self.assertEqual([row["id"] for row in listing.json["memories"]],
                         [m1["id"], m2["id"], m3["id"], m4["id"]])
        first = self.client.get(BASE + f"/sessions/{self.session_id}/memory/{m1['id']}")
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json["memory"]["content"], m1["content"])
        self.assertNotIn(B_MARKER, first.json["memory"]["content"])
        download = self.client.get(BASE + f"/sessions/{self.session_id}/memory/{m1['id']}/download")
        self.assertEqual(download.status_code, 200)
        self.assertIn(A_MARKER, download.get_data(as_text=True))
        self.assertEqual(len(self.provider.calls), before)
        outsider = self.app.test_client()
        registered = outsider.post("/desktop/api/register", json={
            "email": "other-memory-reader@example.test", "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        for path in (BASE + f"/sessions/{self.session_id}/memory?run_id={history_id}",
                     BASE + f"/sessions/{self.session_id}/memory/{m1['id']}",
                     BASE + f"/sessions/{self.session_id}/memory/{m1['id']}/download"):
            self.assertEqual(outsider.get(path).status_code, 404)

    def test_understood_answer_can_be_parent_and_unrelated_root_stays_separate(self):
        initial, understood = self.first_event(explain_more=False)
        m1 = understood["memory_output"]
        self.assertEqual(m1["id"], initial["memory_output"]["id"])
        child = self.relation(understood, source_run_id=True)
        self.assert_model_received(self.memory_calls(self.provider, stage="relation_select")[0],
                                   m1, A_MARKER)
        self.assertEqual(child["memory_output"]["parent_id"], m1["id"])

        # Starting a new root in the same session must not inherit the last
        # chain merely because its run happened later in time.
        second_id = self.submit("event", "另一条独立研究：尊界V800制动支架事件后续？")
        second = self.wait(second_id, {"awaiting_feedback", "failed"})
        self.assertEqual(second["status"], "awaiting_feedback", second.get("error"))
        self.assertIsNone(second["memory_input"])
        self.assertNotEqual(second["memory_output"]["chain_id"], m1["chain_id"])
        self.assertIsNone(self.provider.calls[-1]["payload"]["research_memory"])

    def test_sibling_branches_inherit_parent_snapshot_without_each_other(self):
        _initial, event = self.first_event()
        relation = self.relation(event)
        history_question = next(row["question"] for row in relation["report"]["recommendations"]
                                if row["module"] == "history")
        with patch("backend.app.modules.explore.research_runtime.build_public_reader_tool",
                   self.reader.tool):
            history_id = self.submit("history", history_question,
                                     source_report_id=relation["report"]["id"])
            history = self.wait(history_id)
        self.assertIn(history["status"], {"succeeded", "partial"}, history.get("error"))
        parent = history["memory_output"]
        first_question = "分支甲专属问题：华为与江淮的合同责任如何核对？"
        second_question = "分支乙专属问题：华为与江淮的新闻说法如何核对？"
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            first_id = self.submit("relation", first_question,
                                   source_report_id=history["report"]["id"])
            first = self.wait(first_id)
            self.assertIn(first["status"], {"succeeded", "partial"}, first.get("error"))
            before = len(self.provider.calls)
            second_id = self.submit("relation", second_question,
                                    source_report_id=history["report"]["id"])
            second = self.wait(second_id)
        self.assertIn(second["status"], {"succeeded", "partial"}, second.get("error"))
        self.assertEqual(first["memory_input"]["id"], parent["id"])
        self.assertEqual(second["memory_input"]["id"], parent["id"])
        self.assertEqual(first["memory_output"]["parent_id"], parent["id"])
        self.assertEqual(second["memory_output"]["parent_id"], parent["id"])
        self.assertEqual(first["memory_output"]["version"], parent["version"] + 1)
        self.assertEqual(second["memory_output"]["version"], parent["version"] + 1)
        self.assertNotEqual(first["memory_output"]["id"], second["memory_output"]["id"])
        self.assertIn("分支甲专属问题", first["memory_output"]["content"])
        self.assertNotIn("分支甲专属问题", second["memory_output"]["content"])
        second_calls = self.provider.calls[before:]
        self.assertEqual([row["payload"].get("stage") for row in second_calls],
                         ["relation_select", "relation_lite_report"])
        for call in second_calls:
            self.assert_model_received(call, parent, A_MARKER, B_MARKER, C_MARKER)
            self.assertNotIn("分支甲专属问题", call["messages"][1]["content"])

    def test_failed_child_does_not_publish_new_version_and_retry_uses_same_input(self):
        _initial, event = self.first_event()
        parent_memory = event["memory_output"]
        self.provider.fail_relation_select = True
        question = next(row["question"] for row in event["report"]["recommendations"]
                        if row["module"] == "relation")
        run_id = self.submit("relation", question,
                             source_report_id=event["report"]["id"])
        failed = self.wait(run_id, {"failed"})
        self.assertIsNone(failed.get("memory_output"))
        self.assertEqual(failed["memory_input"]["id"], parent_memory["id"])
        self.assert_model_received(self.memory_calls(self.provider, stage="relation_select")[0],
                                   parent_memory, A_MARKER)
        listing = self.client.get(BASE + f"/sessions/{self.session_id}/memory",
                                  query_string={"run_id": run_id})
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(listing.json["latest"]["id"], parent_memory["id"])

        # A retry resumes the frozen input, even after the earlier worker failed.
        self.provider.fail_relation_select = False
        retried = self.client.post(BASE + f"/runs/{run_id}/retry",
                                   json={"idempotency_key": str(uuid4())})
        self.assertEqual(retried.status_code, 202, retried.json)
        retry_done = self.wait(retried.json["run"]["id"])
        self.assertIn(retry_done["status"], {"succeeded", "partial"})
        self.assertEqual(retry_done["memory_input"]["id"], parent_memory["id"])
        self.assertEqual(retry_done["memory_output"]["parent_id"], parent_memory["id"])

    def test_cancelled_child_does_not_publish_new_version(self):
        _initial, event = self.first_event()
        parent_memory = event["memory_output"]
        self.provider.relation_started = threading.Event()
        self.provider.relation_release = threading.Event()
        question = next(row["question"] for row in event["report"]["recommendations"]
                        if row["module"] == "relation")
        try:
            run_id = self.submit("relation", question,
                                 source_report_id=event["report"]["id"])
            self.assertTrue(self.provider.relation_started.wait(3))
            cancelled = self.client.post(BASE + f"/runs/{run_id}/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.json)
        finally:
            self.provider.relation_release.set()
        run = self.wait(run_id, {"cancelled"})
        self.assertIsNone(run.get("memory_output"))
        self.assertEqual(run["memory_input"]["id"], parent_memory["id"])
        listing = self.client.get(BASE + f"/sessions/{self.session_id}/memory",
                                  query_string={"run_id": run_id})
        self.assertEqual(listing.status_code, 200, listing.json)
        self.assertEqual(listing.json["latest"]["id"], parent_memory["id"])

    def test_removed_memory_dependency_stops_before_glm_receives_old_text(self):
        initial, understood = self.first_event(explain_more=False)
        answer_file = understood["initial_file_ids"]["answer"]
        with patch.object(self.service, "_start"):
            child_id = self.submit("relation", "华为与江淮在尊界V800事件中有什么关联？",
                                   source_run_id=understood["id"])
        deleted = self.client.delete(BASE + f"/sessions/{self.session_id}/files/{answer_file}")
        self.assertEqual(deleted.status_code, 200, deleted.json)
        before = len(self.provider.calls)
        self.service._start(self.owner, child_id, self.session_id)
        child = self.wait(child_id, {"failed", "cancelled"})
        self.assertEqual(child["status"], "failed", child.get("error"))
        self.assertIsNone(child.get("memory_output"))
        self.assertEqual(len(self.provider.calls), before)
        self.assertEqual(child["memory_input"]["id"], initial["memory_output"]["id"])


if __name__ == "__main__":
    unittest.main()
