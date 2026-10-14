"""History-lite publishes its first answer before news retrieval completes."""

from __future__ import annotations

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
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


QUESTION = "华为与江淮汽车的合作历史如何解释当前尊界汽车的合作关系？"
FIRST = ("华为与江淮汽车目前有整车合作关系，但其形成过程仍需核对。"
         "接下来会检索双方过去的合作新闻，了解分工如何形成及发生了哪些变化。")


class HistoryProvider:
    model = "history-fixture"

    def __init__(self):
        self.stages = []
        self.payloads = []
        self.prompts = []
        self.initial_partial = None
        self.initial_started = None
        self.initial_release = None
        self.report_started = None
        self.report_release = None
        self.events_calls = 0

    def events(self, messages, model, stopped, on_response, *, tools=None, **_options):
        self.events_calls += 1
        self.stages.append("history_initial")
        self.payloads.append(json.loads(messages[1]["content"]))
        self.prompts.append(messages[0]["content"])
        if self.initial_started is not None:
            self.initial_started.set()
        if self.initial_partial is not None:
            yield {"type": "token", "text": self.initial_partial}
        if self.initial_release is not None:
            self.initial_release.wait(3)
        yield {"type": "token", "text": FIRST if self.initial_partial is None else "迟到的不完整内容"}

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        self.stages.append(payload.get("stage"))
        self.payloads.append(payload)
        self.prompts.append(messages[0]["content"])
        if self.report_started is not None:
            self.report_started.set()
            self.report_release.wait(3)
        refs = [row["id"] for row in payload.get("sources") or []]
        value = {"title": "华为与江淮汽车历史关联分析",
            "summary": "历史报道显示双方合作分工逐步形成，但具体合同安排仍需核对。",
            "key_findings": ["双方合作关系应结合过去报道和当前正式披露理解。"],
            "sections": [{"heading": "历史关系与原因", "body":
                "历史报道提及双方共同推进项目；不能仅凭时间先后推断全部当前职责。" +
                ("[S1]" if refs else "本轮没有可引用新闻。"),
                "source_refs": refs[:1]}],
            "recommendations": [
                {"module": "event", "question": "尊界汽车当前有哪些已确认的合作进展？", "reason": "核对当前事实"},
                {"module": "relation", "question": "华为与江淮汽车现阶段的分工依据是什么？", "reason": "厘清合作机制"},
                {"module": "history", "question": "双方过往合作安排在哪些节点发生变化？", "reason": "继续验证关系变化"}]}
        yield json.dumps(value, ensure_ascii=False)


class HistoryNews:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block = False
        self.empty = False

    def search(self):
        def handler(arguments, context):
            self.calls.append(dict(arguments))
            self.started.set()
            if self.block:
                self.release.wait(3)
            if self.empty:
                return ToolResult(content={"searchedAt": timestamp()}, sources=[])
            rows = [{"id": str(i), "title": f"华为江淮汽车历史合作报道{i}",
                     "url": f"https://example.org/history/{i}",
                     "snippet": "华为与江淮汽车过去推进合作，项目分工逐渐形成。",
                     "publishedAt": "2025-05-01T08:00:00+08:00"}
                    for i in range(1, 7)]
            rows[4]["url"] = rows[0]["url"] + "#more"
            return ToolResult(content={"searchedAt": timestamp()}, sources=rows)
        return ToolSpec("web_search", "本地历史搜索", {"type": "object"}, handler, max_calls=1)


class HistoryReader:
    def __init__(self):
        self.calls = []

    def page(self, _dns_mode):
        def handler(arguments, context):
            self.calls.append(dict(arguments))
            return ToolResult(content={"status": "ok", "text":
                "报道介绍华为与江淮汽车过去的合作安排及项目推进。",
                "final_url": arguments["url"], "publisher": "测试新闻"})
        return ToolSpec("fetch_public_page", "本地新闻页", {"type": "object"},
                        handler, max_calls=5)


class HistoryLiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-history-lite-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "test-only",
            "IMPORT_LEGACY_HISTORY": False, "EXPLORE_HISTORY_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        self.client = self.app.test_client()
        response = self.client.post("/desktop/api/register", json={
            "email": "history-lite@example.test", "password": "long-password"})
        self.assertEqual(response.status_code, 200, response.json)
        self.owner = response.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.provider = HistoryProvider()
        self.news = HistoryNews()
        self.reader = HistoryReader()
        self.service.provider = self.provider
        self.service.search_tools = ToolRegistry((self.news.search(),))
        response = self.client.post("/desktop/api/explore/sessions", json={"title": "历史测试"})
        self.assertEqual(response.status_code, 201, response.json)
        self.session_id = response.json["session"]["id"]

    def tearDown(self):
        self.news.release.set()
        if self.provider.initial_release is not None:
            self.provider.initial_release.set()
        if self.provider.report_release is not None:
            self.provider.report_release.set()
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def submit(self):
        response = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs",
            json={"module": "history", "question": QUESTION,
                  "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"]["id"]

    def get_run(self, run_id):
        response = self.client.get(f"/desktop/api/explore/runs/{run_id}")
        self.assertEqual(response.status_code, 200, response.json)
        return response.json["run"]

    def wait(self, run_id, condition):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = self.get_run(run_id)
            if condition(run):
                return run
            time.sleep(.01)
        self.fail("history-lite run did not reach expected state")

    def test_first_answer_visible_and_in_pool_while_search_is_blocked(self):
        self.news.block = True
        with patch("backend.app.modules.explore.research_runtime.ResearchRuntime.__init__",
                   side_effect=AssertionError("old runtime called")), \
             patch("backend.app.modules.explore.claim_review.apply_claim_review",
                   side_effect=AssertionError("claim review called")), \
             patch("backend.app.modules.explore.history_lite_runtime.build_public_reader_tool",
                   self.reader.page):
            run_id = self.submit()
            self.assertTrue(self.news.started.wait(3))
            running = self.wait(run_id, lambda row: row.get("initial_answer") is not None)
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["phase"], "history_report")
            self.assertIn("检索双方过去的合作新闻", running["initial_answer"])
            self.assertIsNone(running["report"])
            self.assertEqual(len(self.provider.stages), 1)
            # A fresh client sees the persisted first answer before search returns.
            fresh = self.app.test_client()
            login = fresh.post("/desktop/api/login", json={
                "email": "history-lite@example.test", "password": "long-password"})
            self.assertEqual(login.status_code, 200, login.json)
            refreshed = fresh.get(f"/desktop/api/explore/runs/{run_id}")
            self.assertEqual(refreshed.status_code, 200, refreshed.json)
            self.assertEqual(refreshed.json["run"]["initial_answer"], FIRST)
            pool = self.service.repository.list_files(self.owner, self.session_id)
            self.assertEqual(len(pool), 2)
            contents = [self.service.repository.get_file(self.owner, self.session_id, row["id"])["content"]
                        for row in pool]
            self.assertTrue(any(QUESTION in content for content in contents))
            self.assertTrue(any(FIRST in content for content in contents))
            self.assertFalse(any("https://example.org" in content for content in contents))
            self.news.release.set()
            done = self.wait(run_id, lambda row: row["status"] in {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        report = done["report"]
        self.assertEqual(report["workflow_version"], "history-lite@1")
        self.assertEqual(report["report_format"], "history_lite@1")
        self.assertEqual(done["next_step_state"], "pending")
        self.assertEqual([row["module"] for row in report["recommendations"]],
                         ["event", "relation", "history"])
        self.assertEqual(self.provider.stages, ["history_initial", "history_lite_report"])
        self.assertEqual(self.provider.events_calls, 1)
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 5)
        self.assertEqual(self.news.calls[0]["freshness"], "noLimit")
        self.assertIn("华为", self.news.calls[0]["query"])
        self.assertIn("江淮汽车", self.news.calls[0]["query"])
        self.assertLessEqual(len(report["sources"]), 5)
        self.assertEqual(len(report["sources"]), len({row["url"].split("#")[0]
            for row in report["sources"]}))
        self.assertLessEqual(len(self.reader.calls), 5)
        self.assertIn("## 第一步：", self.provider.prompts[0])
        self.assertNotIn("## 第二步：", self.provider.prompts[0])
        self.assertEqual(self.provider.payloads[1]["initial_answer_unverified"], FIRST)
        self.assertIsNone(self.provider.payloads[0]["research_memory"])
        self.assertIn("历史验证", self.provider.payloads[1]["research_memory"]["content"])

    def test_cancellation_during_blocked_search_cannot_publish_late_report(self):
        self.news.block = True
        with patch("backend.app.modules.explore.history_lite_runtime.build_public_reader_tool",
                   self.reader.page):
            run_id = self.submit()
            self.assertTrue(self.news.started.wait(3))
            self.wait(run_id, lambda row: row.get("initial_answer") is not None)
            response = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
            self.assertEqual(response.status_code, 200, response.json)
            self.news.release.set()
            cancelled = self.wait(run_id, lambda row: row["status"] == "cancelled")
            time.sleep(.05)
            self.assertIsNone(cancelled["report"])
            self.assertEqual(self.get_run(run_id)["status"], "cancelled")
            self.assertEqual(self.provider.stages, ["history_initial"])

    def test_deleted_session_during_search_does_not_recreate_run(self):
        self.news.block = True
        with patch("backend.app.modules.explore.history_lite_runtime.build_public_reader_tool",
                   self.reader.page):
            run_id = self.submit()
            self.assertTrue(self.news.started.wait(3))
            self.wait(run_id, lambda row: row.get("initial_answer") is not None)
            deleted = self.client.delete(f"/desktop/api/explore/sessions/{self.session_id}")
            self.assertEqual(deleted.status_code, 200, deleted.json)
            self.news.release.set()
            time.sleep(.05)
            gone = self.client.get(f"/desktop/api/explore/runs/{run_id}")
            self.assertEqual(gone.status_code, 404, gone.json)
            self.assertEqual(self.provider.stages, ["history_initial"])

    def test_initial_timeout_publishes_complete_sentence_and_ignores_late_tokens(self):
        self.provider.initial_partial = "历史关联有助于判断双方当前分工如何形成。接下来"
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        self.news.block = True
        with patch("backend.app.modules.explore.history_lite_runtime._INITIAL_SECONDS", .15), \
             patch("backend.app.modules.explore.history_lite_runtime.build_public_reader_tool",
                   self.reader.page):
            run_id = self.submit()
            self.assertTrue(self.provider.initial_started.wait(3))
            self.assertTrue(self.news.started.wait(3))
            running = self.wait(run_id, lambda row: row.get("initial_answer") is not None)
            self.assertEqual(running["initial_answer_mode"], "model_partial")
            self.assertEqual(running["initial_answer"], "历史关联有助于判断双方当前分工如何形成。")
            self.provider.initial_release.set()
            time.sleep(.05)
            self.assertEqual(self.get_run(run_id)["initial_answer"], running["initial_answer"])
            self.news.release.set()
            done = self.wait(run_id, lambda row: row["status"] in {"succeeded", "partial", "failed"})
            self.assertEqual(done["status"], "succeeded", done.get("report"))

    def test_no_news_still_delivers_limited_report_with_three_choices(self):
        self.news.empty = True
        with patch("backend.app.modules.explore.history_lite_runtime.build_public_reader_tool",
                   self.reader.page):
            run_id = self.submit()
            done = self.wait(run_id, lambda row: row["status"] in {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual(done["next_step_state"], "pending")
        self.assertEqual(done["report"]["sources"], [])
        self.assertEqual(len(done["report"]["recommendations"]), 3)
        self.assertEqual(self.reader.calls, [])
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.provider.stages, ["history_initial", "history_lite_report"])


if __name__ == "__main__":
    unittest.main()
