"""The event choice pauses durably and resumes only after an authenticated click."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.explore.event_lite_runtime import _relevant
from backend.app.modules.explore.repository import ExploreRepository, timestamp
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


class ChoiceProvider:
    model = "choice-fixture"

    def __init__(self):
        self.initial_calls = 0
        self.initial_payloads = []
        self.initial_prompts = []
        self.initial_started = None
        self.initial_release = None
        self.initial_response = None
        self.initial_error = None
        self.initial_partial_text = None
        self.initial_empty_output = False
        self.report_calls = 0
        self.report_started = None
        self.report_release = None
        self.report_response = None
        self.bad_recommendations = False
        self.whitespace_recommendations = False
        self.empty_recommendation_reason = False

    def events(self, messages, model, stopped, on_response, *, tools=None, **_options):
        assert tools is None
        payload = json.loads(messages[1]["content"])
        self.initial_payloads.append(payload)
        self.initial_prompts.append(messages[0]["content"])
        self.initial_calls += 1
        if self.initial_response is not None:
            on_response(self.initial_response)
        if self.initial_started is not None:
            self.initial_started.set()
            self.initial_release.wait(timeout=5)
        if self.initial_partial_text is not None:
            yield {"type": "token", "text": self.initial_partial_text}
        if self.initial_error is not None:
            raise self.initial_error
        if self.initial_empty_output:
            return
        status = payload["search"]["status"]
        if status == "complete" and payload["sources"]:
            answer = "股价变化通常与预期和资金有关；具体走势需结合当期公开信息核对。"
        else:
            answer = "股价变化通常与预期、资金和企业盈利有关；分析时要看同一时期的成交与业绩变化。"
        yield {"type": "token", "text": answer}
        yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        assert payload["stage"] == "event_deep_lite"
        self.report_calls += 1
        if self.report_response is not None:
            on_response(self.report_response)
        if self.report_started is not None:
            self.report_started.set()
            self.report_release.wait(timeout=5)
        refs = [source["id"] for source in payload["sources"]]
        recommendations = [
            {"module": "event", "question": "本周市场变化有哪些最新证据仍需核实？", "reason": "补齐当前事实"},
            {"module": "relation", "question": "市场预期和成交资金如何共同影响这次波动？", "reason": "梳理作用关系"},
            {"module": "history", "question": "过去类似波动之后市场表现有什么不同？", "reason": "比较历史情形"},
        ]
        if self.bad_recommendations:
            recommendations = recommendations[:2]
        if self.whitespace_recommendations:
            for row in recommendations:
                row["question"] = " \n " + row["question"] + " \t "
        if self.empty_recommendation_reason:
            recommendations[0]["reason"] = " \n "
        yield json.dumps({
            "title": "本周 A 股进一步解释", "summary": (
                "新闻报道了本周市场变化。" if refs else "本轮未取得可用相关新闻，暂不能核实本周具体变化。"),
            "key_findings": ["市场变化应结合报道和金融机制理解。"],
            "sections": [{"heading": "结合新闻解释", "body": (
                "市场报道提到指数变化，股价机制还受到预期和资金影响。" if refs else
                "市场变化通常受到预期和资金影响，具体本周变化尚未由新闻核实。"),
                          "source_refs": refs[:1]}],
            "recommendations": recommendations}, ensure_ascii=False)


class NewsTool:
    def __init__(self):
        self.calls = []
        self.reads = []

    def search(self):
        def handler(arguments, context):
            self.calls.append(arguments)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[{
                "id": "one", "title": "A股本周收盘观察", "url": "https://example.org/a-share",
                "publishedAt": timestamp(), "snippet": "报道提到本周指数变化。"}])
        return ToolSpec("web_search", "测试搜索", {"type": "object"}, handler, max_calls=2)

    def page(self, _dns_mode):
        def handler(arguments, context):
            self.reads.append(arguments)
            return ToolResult(content={"status": "ok", "text": "本周 A 股市场指数出现变化，成交活跃。",
                                       "final_url": arguments["url"], "publisher": "测试新闻"})
        return ToolSpec("fetch_public_page", "测试原文", {"type": "object"}, handler, max_calls=5)


class AbortProbe:
    def __init__(self):
        self.aborted = threading.Event()

    def abort(self):
        self.aborted.set()


class EventLiteChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-event-choice-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "test-only",
            "IMPORT_LEGACY_HISTORY": False, "EXPLORE_EVENT_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        self.client = self.app.test_client()
        register = self.client.post("/desktop/api/register", json={
            "email": "event-choice@example.test", "password": "long-password"})
        self.assertEqual(register.status_code, 200, register.json)
        self.owner = register.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.provider = ChoiceProvider()
        self.tools = NewsTool()
        self.service.provider = self.provider
        self.service.search_tools = ToolRegistry((self.tools.search(),))
        session = self.client.post("/desktop/api/explore/sessions", json={"title": "选择测试"})
        self.assertEqual(session.status_code, 201, session.json)
        self.session_id = session.json["session"]["id"]

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def submit(self, question="A股本周股票情况怎么样？"):
        created = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs", json={
            "module": "event", "question": question, "idempotency_key": str(uuid4())})
        self.assertEqual(created.status_code, 202, created.json)
        return created.json["run"]["id"]

    def wait(self, run_id, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.client.get(f"/desktop/api/explore/runs/{run_id}")
            self.assertEqual(result.status_code, 200, result.json)
            run = result.json["run"]
            if run["status"] in statuses:
                return run
            time.sleep(.01)
        self.fail("event choice did not settle")

    def feedback(self, run_id, choice, key=None):
        return self.client.post(f"/desktop/api/explore/runs/{run_id}/feedback",
                                json={"choice": choice, "idempotency_key": key or str(uuid4())})

    def test_initial_searches_snippets_once_and_waits_durably(self):
        run_id = self.submit()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertIn("股价变化", waiting["initial_answer"])
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(self.provider.report_calls, 0)
        self.assertEqual(len(self.tools.calls), 1)
        self.assertEqual(self.tools.calls[0]["count"], 2)
        self.assertEqual(self.tools.reads, [])
        self.assertIsNone(waiting["report"])
        self.assertTrue(waiting["initial_search_attempted"])
        self.assertEqual(waiting["initial_sources"], [])
        self.assertEqual(waiting["initial_retrieval"], {})
        self.assertEqual(waiting["materials"], [])
        self.assertEqual(waiting["retrieval"], {})
        self.assertEqual(self.provider.initial_payloads[0]["search"]["status"], "complete")
        self.assertEqual(len(self.provider.initial_payloads[0]["sources"]), 1)
        self.assertNotIn("id", self.provider.initial_payloads[0]["sources"][0])
        self.assertNotIn("url", self.provider.initial_payloads[0]["sources"][0])
        self.assertIn("报道提到本周指数变化", self.provider.initial_payloads[0]["sources"][0]["text"])
        self.assertTrue(self.provider.initial_payloads[0]["as_of_date"])
        self.assertTrue(self.provider.initial_payloads[0]["window_start"])
        self.assertIn("## 第一步：", self.provider.initial_prompts[0])
        self.assertNotIn("## 用户反馈", self.provider.initial_prompts[0])
        self.assertNotIn("## 第二步：", self.provider.initial_prompts[0])
        pool = self.service.repository.list_files(self.owner, self.session_id)
        self.assertEqual(len(pool), 2)
        self.assertEqual(set(waiting["initial_file_ids"]), {"input", "answer"})
        answer_file = self.service.repository.get_file(
            self.owner, self.session_id, waiting["initial_file_ids"]["answer"])
        self.assertEqual(answer_file["source_ids"], [])
        self.assertEqual(answer_file["source_file_ids"], [])
        self.assertTrue(answer_file["initial_search_attempted"])
        self.assertIn("AI生成的简明解释", answer_file["content"])
        self.assertNotIn("第一步联网来源", answer_file["content"])
        contents = [self.service.repository.get_file(self.owner, self.session_id, row["id"])["content"]
                    for row in pool]
        self.assertTrue(any("A股本周股票情况怎么样？" in content for content in contents))
        self.assertTrue(any("股价变化通常与预期和资金有关" in content for content in contents))
        self.assertTrue(all("https://example.org/a-share" not in content for content in contents))
        self.assertTrue(all("A股本周收盘观察" not in content for content in contents))
        self.assertNotIn("https://example.org/a-share", json.dumps(waiting, ensure_ascii=False))
        self.assertNotIn("searching_web", [row["stage"] for row in waiting["stage_history"]])
        # Startup recovery interrupts active workers but must preserve a choice.
        ExploreRepository(self.service.repository.path.parent)
        self.assertEqual(self.service.repository.get_run(self.owner, run_id)["status"], "awaiting_feedback")
        key = str(uuid4())
        first = self.feedback(run_id, "understood", key)
        self.assertEqual(first.status_code, 202, first.json)
        self.assertEqual(first.json["run"]["status"], "succeeded")
        again = self.feedback(run_id, "understood", key)
        self.assertEqual(again.status_code, 202, again.json)
        self.assertEqual(again.json["run"]["status"], "succeeded")
        self.assertEqual(self.feedback(run_id, "explain_more").status_code, 409)
        self.assertEqual(self.provider.report_calls, 0)
        self.assertEqual(len(self.tools.calls), 1)

    def test_explain_more_searches_once_and_saves_one_sourced_report(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        choice_key = str(uuid4())
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            chosen = self.feedback(run_id, "explain_more", choice_key)
            self.assertEqual(chosen.status_code, 202, chosen.json)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(self.provider.report_calls, 1)
        self.assertEqual(len(self.tools.calls), 2)
        self.assertEqual([call["count"] for call in self.tools.calls], [2, 5])
        self.assertEqual(len(self.tools.reads), 1)
        self.assertEqual(done["report"]["report_format"], "event_lite@2")
        self.assertEqual(done["next_step_state"], "pending")
        self.assertEqual([row["module"] for row in done["report"]["recommendations"]],
                         ["event", "relation", "history"])
        self.assertIn("理解事件：本周市场变化有哪些最新证据仍需核实？", done["report"]["markdown"])
        self.assertEqual(len(done["report"]["sources"]), 1)
        self.assertEqual(done["report"]["sources"][0]["id"], "S1")
        self.assertEqual(done["report"]["sources"][0]["phase"], "event_deep_report")
        self.assertEqual(done["initial_sources"], [])
        self.assertEqual(done["report"]["sources"][0]["kind"], "public_page")
        self.assertEqual(done["report"]["sources"][0]["content_kind"], "extracted_page_text")
        self.assertEqual([row["name"] for row in done["report"]["retrieval"]["tool_trace"]],
                         ["web_search", "fetch_public_page"])
        self.assertIn("## 新闻来源", done["report"]["markdown"])
        self.assertIn("网页原文片段", done["report"]["markdown"])
        self.assertIn("来源：测试新闻", done["report"]["markdown"])
        pool = self.service.repository.list_files(self.owner, self.session_id)
        kinds = [row["kind"] for row in pool]
        self.assertEqual(kinds.count("material"), 2)
        self.assertEqual(kinds.count("report"), 2)
        self.assertEqual(len(pool), 4)
        deep_source = self.service.repository.get_file(
            self.owner, self.session_id, done["report"]["sources"][0]["file_id"])
        self.assertTrue(deep_source["name"].startswith("第二步网页原文-"))
        self.assertEqual(deep_source["phase"], "event_deep_report")
        self.assertIn("所属阶段：理解事件第二步", deep_source["content"])
        self.assertIn("来源编号：S1", deep_source["content"])
        same = self.feedback(run_id, "explain_more", choice_key)
        self.assertEqual(same.status_code, 202, same.json)
        self.assertEqual(same.json["run"]["id"], run_id)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 4)
        self.assertEqual(self.feedback(run_id, "explain_more").status_code, 409)

    def test_second_stage_starts_new_source_numbers_and_shares_the_tool_budget(self):
        def search(arguments, context):
            self.tools.calls.append(arguments)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[
                {"id": str(index), "title": f"A股本周收盘观察{index}",
                 "url": f"https://example.org/a-share-{index}",
                 "publishedAt": timestamp(), "snippet": f"A股本周指数变化观察{index}。"}
                for index in (1, 2)])

        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试搜索", {"type": "object"}, search, max_calls=2),))
        self.service.research_limits["tool_calls"] = 3
        run_id = self.submit()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_sources"], [])
        self.assertEqual(waiting["materials"], [])
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual([item["id"] for item in done["report"]["sources"]], ["S1", "S2"])
        self.assertEqual(len(self.tools.calls), 2)
        self.assertEqual(len(self.tools.reads), 1)
        self.assertEqual(done["initial_sources"], [])

    def test_legacy_stored_first_source_keeps_its_number_and_file_on_replay(self):
        with patch.object(self.service, "_start"):
            run_id = self.submit()
        queued = self.service.repository.get_run(self.owner, run_id)
        source = {"id": "S1", "phase": "initial_explanation", "kind": "web_search",
                  "source": "web_search", "source_name": "联网搜索摘要",
                  "title": "旧版首轮新闻", "summary": "A股本周指数变化。",
                  "text_used": "A股本周指数变化。", "url": "https://example.org/legacy-news",
                  "published_at": timestamp(), "retrieved_at": timestamp(),
                  "content_kind": "search_snippet", "provider_id": "legacy"}
        old_retrieval = {"searches": [{"status": "complete", "count": 1}],
                         "news": [], "tool_trace": [{"name": "web_search"}]}
        saved = self.service.repository.save_materials(
            self.owner, run_id, [source], old_retrieval,
            execution_id=queued["execution_id"], stage="explaining")
        self.assertIsNotNone(saved)
        old_source = saved["materials"][0]
        waiting = self.service.repository.save_event_initial(
            self.owner, run_id, "旧版解释 [S1]", execution_id=queued["execution_id"],
            fields={"initial_sources": [old_source], "initial_retrieval": old_retrieval,
                    "initial_search_attempted": True, "initial_answer_mode": "model_complete"})
        self.assertEqual(waiting["status"], "awaiting_feedback")
        self.assertEqual(waiting["initial_sources"][0]["id"], "S1")
        legacy_file_id = old_source["file_id"]
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["report"]["sources"][0]["id"], "S2")
        self.assertEqual(done["initial_sources"][0]["file_id"], legacy_file_id)
        self.assertEqual(self.service.repository.get_file(
            self.owner, self.session_id, legacy_file_id)["source_id"], "S1")

    def test_first_search_limits_sources_and_model_snippets(self):
        def search(arguments, context):
            self.tools.calls.append(arguments)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[
                {"id": str(index), "title": f"A股本周市场观察{index}",
                 "url": f"https://example.org/a-share-{index}",
                 "publishedAt": timestamp(), "snippet": "A股本周" + "走势" * 300}
                for index in range(5)])

        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试搜索", {"type": "object"}, search, max_calls=2),))
        run_id = self.submit()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(self.tools.calls[0]["count"], 2)
        self.assertEqual(waiting["initial_sources"], [])
        self.assertEqual(len(self.provider.initial_payloads[0]["sources"]), 2)
        self.assertTrue(all(len(item["text"]) <= 500
                            for item in self.provider.initial_payloads[0]["sources"]))
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
        self.assertIn("150至250中文字符", self.provider.initial_prompts[0])

    def test_complete_model_answer_hides_temporary_source_handle_and_link(self):
        self.provider.initial_partial_text = (
            "股价波动可能受到成交资金影响。[S1] https://example.org/a-share\n")
        run_id = self.submit()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_answer_mode"], "model_complete")
        self.assertIn("成交资金影响", waiting["initial_answer"])
        self.assertNotIn("S1", waiting["initial_answer"])
        self.assertNotIn("https://example.org/a-share", waiting["initial_answer"])
        answer_file = self.service.repository.get_file(
            self.owner, self.session_id, waiting["initial_file_ids"]["answer"])
        self.assertEqual(answer_file["source_ids"], [])
        self.assertEqual(answer_file["source_file_ids"], [])
        self.assertNotIn("example.org", answer_file["content"])

    def test_search_budget_expires_then_one_model_answers_without_late_source(self):
        entered, release = threading.Event(), threading.Event()
        response = AbortProbe()

        def search(arguments, context):
            self.tools.calls.append(arguments)
            context.on_response(response)
            entered.set()
            release.wait(timeout=3)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[{
                "id": "late", "title": "A股本周收盘观察", "url": "https://example.org/late",
                "publishedAt": timestamp(), "snippet": "A股本周市场变化。"}])

        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试慢搜索", {"type": "object"}, search, max_calls=2),))
        with patch("backend.app.modules.explore.event_lite_runtime._INITIAL_SEARCH_SECONDS", 0.12):
            run_id = self.submit()
            try:
                self.assertTrue(entered.wait(timeout=3))
                waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
                self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
                self.assertEqual(waiting["initial_answer_mode"], "model_complete")
                self.assertEqual(waiting["initial_sources"], [])
                self.assertEqual(waiting["initial_retrieval"], {})
                self.assertTrue(waiting["initial_search_attempted"])
                self.assertIn("股价变化通常与预期", waiting["initial_answer"])
                self.assertNotIn("联网", waiting["initial_answer"])
                self.assertEqual(self.provider.initial_calls, 1)
                self.assertTrue(response.aborted.wait(timeout=1))
            finally:
                release.set()
        time.sleep(.05)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
        self.assertEqual(self.service.repository.get_run(self.owner, run_id)["initial_sources"], [])

    def test_late_search_response_cannot_replace_active_model_response(self):
        search_entered, release_search = threading.Event(), threading.Event()
        late_search_response, model_response = AbortProbe(), AbortProbe()
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        self.provider.initial_response = model_response

        def search(arguments, context):
            self.tools.calls.append(arguments)
            search_entered.set()
            release_search.wait(timeout=3)
            context.on_response(late_search_response)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[])

        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试迟到搜索", {"type": "object"}, search, max_calls=2),))
        with patch("backend.app.modules.explore.event_lite_runtime._INITIAL_SEARCH_SECONDS", 0.12):
            run_id = self.submit()
            try:
                self.assertTrue(search_entered.wait(timeout=3))
                self.assertTrue(self.provider.initial_started.wait(timeout=3))
                with self.service._lock:
                    control = self.service._active[(self.owner, run_id)]
                self.assertIs(control["response"], model_response)
                release_search.set()
                self.assertTrue(late_search_response.aborted.wait(timeout=1))
                self.assertIs(control["response"], model_response)
                self.assertFalse(model_response.aborted.is_set())
            finally:
                release_search.set()
                self.provider.initial_release.set()
            waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_sources"], [])

    def test_total_timeout_during_search_saves_knowledge_fallback_without_late_source(self):
        entered, release = threading.Event(), threading.Event()
        response = AbortProbe()

        def search(arguments, context):
            self.tools.calls.append(arguments)
            context.on_response(response)
            entered.set()
            release.wait(timeout=3)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[{
                "id": "one", "title": "A股本周收盘观察", "url": "https://example.org/a-share",
                "publishedAt": timestamp(), "snippet": "A股本周指数变化。"}])

        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试搜索", {"type": "object"}, search, max_calls=2),))
        with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
            run_id = self.submit()
            try:
                self.assertTrue(entered.wait(timeout=3))
                waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
                self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
                self.assertEqual(waiting["initial_answer_mode"], "knowledge_fallback")
                self.assertIn("看股票行情", waiting["initial_answer"])
                self.assertTrue(response.aborted.wait(timeout=1))
            finally:
                release.set()
        self.assertEqual(self.provider.initial_calls, 0)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
        time.sleep(.05)
        self.assertEqual(self.service.repository.get_run(self.owner, run_id)["status"],
                         "awaiting_feedback")

    def test_total_timeout_during_model_saves_excerpt_without_late_answer(self):
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        response = AbortProbe()
        self.provider.initial_response = response
        with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.initial_started.wait(timeout=3))
                in_progress = self.service.repository.get_run(self.owner, run_id)
                self.assertEqual(in_progress["materials"], [])
                self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])
                waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
                self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
                self.assertEqual(waiting["initial_answer_mode"], "source_excerpt")
                self.assertIn("报道提到本周指数变化", waiting["initial_answer"])
                self.assertNotIn("S1", waiting["initial_answer"])
                self.assertTrue(response.aborted.wait(timeout=1))
            finally:
                self.provider.initial_release.set()
        time.sleep(.05)
        final = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(final["status"], "awaiting_feedback")
        self.assertEqual(final["initial_answer_mode"], "source_excerpt")
        pool = self.service.repository.list_files(self.owner, self.session_id)
        self.assertEqual(len(pool), 2)

    def test_first_stage_never_saves_materials_even_when_timeout_uses_a_private_excerpt(self):
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        with patch.object(self.service.repository, "save_materials",
                          side_effect=AssertionError("first stage must not save news")):
            with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
                run_id = self.submit()
                try:
                    self.assertTrue(self.provider.initial_started.wait(timeout=3))
                    self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])
                    waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
                    self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
                    self.assertEqual(waiting["initial_answer_mode"], "source_excerpt")
                    self.assertEqual(waiting["initial_sources"], [])
                    self.assertEqual(waiting["materials"], [])
                    self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
                finally:
                    self.provider.initial_release.set()
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual([item["id"] for item in done["report"]["sources"]], ["S1"])

    def test_fallback_feedback_starts_deep_run_while_old_model_returns_late(self):
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        self.provider.initial_response = AbortProbe()
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        self.provider.report_response = AbortProbe()
        with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.initial_started.wait(timeout=3))
                waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
                self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
                self.assertEqual(waiting["initial_answer_mode"], "source_excerpt")
                with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                           self.tools.page):
                    self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
                    try:
                        self.assertTrue(self.provider.report_started.wait(timeout=3))
                        with self.service._lock:
                            deep_control = self.service._active[(self.owner, run_id)]
                        self.assertIs(deep_control["response"], self.provider.report_response)
                        self.provider.initial_release.set()
                        time.sleep(.05)
                        with self.service._lock:
                            self.assertIs(self.service._active[(self.owner, run_id)], deep_control)
                        self.assertIs(deep_control["response"], self.provider.report_response)
                        self.assertFalse(self.provider.report_response.aborted.is_set())
                    finally:
                        self.provider.report_release.set()
                    done = self.wait(run_id, {"succeeded", "partial", "failed"})
                self.assertEqual(done["status"], "succeeded", done.get("error"))
                self.assertEqual([item["id"] for item in done["report"]["sources"]], ["S1"])
            finally:
                self.provider.initial_release.set()
                self.provider.report_release.set()

    def test_initial_transport_failure_does_not_retry_inside_23_second_budget(self):
        self.provider.initial_error = GatewayError("模型流中断", 504, "model_stream_timeout")
        run_id = self.submit()
        waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_answer_mode"], "source_excerpt")
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(waiting.get("model_retries") or [], [])

    def test_partial_model_sentence_wins_over_source_excerpt_on_timeout(self):
        self.provider.initial_partial_text = "市场走势还受到预期和成交资金影响。没有写完的尾句"
        self.provider.initial_error = GatewayError("模型流超时", 504, "model_stream_timeout")
        run_id = self.submit()
        waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_answer_mode"], "model_partial")
        self.assertEqual(waiting["initial_answer"], "市场走势还受到预期和成交资金影响。")
        self.assertEqual(self.provider.initial_calls, 1)

    def test_interrupted_stream_uses_complete_partial_sentence_without_retry(self):
        self.provider.initial_partial_text = "这次波动可能和预期变化有关。后面尚未完成"
        self.provider.initial_error = GatewayError("模型流中断", 502, "model_stream_interrupted")
        run_id = self.submit()
        waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_answer_mode"], "model_partial")
        self.assertEqual(waiting["initial_answer"], "这次波动可能和预期变化有关。")
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(waiting.get("model_retries") or [], [])

    def test_connection_failure_uses_search_excerpt_without_retry(self):
        self.provider.initial_error = GatewayError("模型连接失败", 502, "model_connection_failed")
        run_id = self.submit()
        waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_answer_mode"], "source_excerpt")
        self.assertIn("报道提到本周指数变化", waiting["initial_answer"])
        self.assertNotIn("S1", waiting["initial_answer"])
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(waiting.get("model_retries") or [], [])

    def test_empty_model_output_uses_search_excerpt_without_retry(self):
        self.provider.initial_empty_output = True
        run_id = self.submit()
        waiting = self.wait(run_id, {"failed", "awaiting_feedback"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["initial_answer_mode"], "source_excerpt")
        self.assertIn("报道提到本周指数变化", waiting["initial_answer"])
        self.assertNotIn("S1", waiting["initial_answer"])
        self.assertEqual(self.provider.initial_calls, 1)
        self.assertEqual(waiting.get("model_retries") or [], [])

    def test_successful_initial_answer_wins_a_late_timeout_callback(self):
        class ManualTimer:
            def __init__(self, interval, callback):
                self.interval, self.callback = interval, callback

            def start(self):
                pass

            def cancel(self):
                pass

        with patch.object(self.service, "_start"):
            run_id = self.submit()
        run = self.service.repository.get_run(self.owner, run_id)
        control = {"stop": threading.Event(), "timed_out": threading.Event(), "response": None,
                   "session_id": self.session_id, "execution_id": run["execution_id"],
                   "owner": self.owner, "run_id": run_id, "model_transport_trace": []}
        timers = []

        def capture(interval, callback):
            timer = ManualTimer(interval, callback)
            timers.append(timer)
            return timer

        with patch("backend.app.modules.explore.service.threading.Timer", side_effect=capture):
            self.service._execute(self.owner, run_id, control)
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0].interval, 23.0)
        self.assertEqual(self.service.repository.get_run(self.owner, run_id)["status"],
                         "awaiting_feedback")
        timers[0].callback()
        self.assertEqual(self.service.repository.get_run(self.owner, run_id)["status"],
                         "awaiting_feedback")
        self.assertFalse(control["timed_out"].is_set())
        self.assertFalse(control["stop"].is_set())

    def test_deep_report_retains_full_run_timeout(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"],
                         "awaiting_feedback")
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            try:
                self.assertTrue(self.provider.report_started.wait(timeout=3))
                with self.service._lock:
                    control = self.service._active[(self.owner, run_id)]
                remaining = control["deadline"] - time.monotonic()
                self.assertGreater(remaining, 25)
                self.assertLessEqual(remaining, self.service.timeout)
            finally:
                self.provider.report_release.set()
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))

    def test_invalid_choice_and_cancelled_wait_do_not_search(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        self.assertEqual(self.feedback(run_id, ["understood"]).status_code, 400)
        stopped = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
        self.assertEqual(stopped.status_code, 200, stopped.json)
        self.assertEqual(stopped.json["run"]["status"], "cancelled")
        self.assertEqual(self.feedback(run_id, "explain_more").status_code, 409)
        self.assertEqual(len(self.tools.calls), 1)

    def test_named_stock_keeps_market_context_as_lower_ranked_candidate(self):
        generic = {"title": "A股指数本周收盘", "snippet": "股票市场成交变化",
                   "url": "https://example.org/generic"}
        self.assertTrue(_relevant("A股这周的股票情况怎么样？", generic))
        self.assertTrue(_relevant("A股本周为什么波动？", generic))
        self.assertTrue(_relevant("华为概念股票这周怎么样？", generic))

    def test_unlisted_company_stock_news_keeps_sparse_background_candidates(self):
        question = "近期英伟达股价为什么容易受到人工智能算力需求预期影响？"
        related = {"title": "英伟达股价连日回调，美股AI泡沫论升温",
                   "snippet": "市场讨论AI算力需求预期。",
                   "url": "https://example.org/nvidia-ai-stock"}
        alias = {"title": "NVIDIA AI算力需求展望影响股价",
                 "snippet": "人工智能芯片需求预期变化。",
                 "url": "https://example.org/nvidia-alias"}
        other_company = {"title": "AMD股价因AI算力需求波动",
                         "snippet": "人工智能芯片需求预期变化。",
                         "url": "https://example.org/amd"}
        other_topic = {"title": "英伟达游戏新品发布",
                       "snippet": "游戏显卡外观曝光。",
                       "url": "https://example.org/nvidia-game"}
        self.assertTrue(_relevant(question, related))
        self.assertTrue(_relevant(question, alias))
        self.assertTrue(_relevant(question, other_company))
        self.assertTrue(_relevant(question, other_topic))

    def test_sparse_subject_news_reaches_both_models_without_first_step_pool_news(self):
        def search(arguments, context):
            self.tools.calls.append(dict(arguments))
            published = timestamp()
            rows = [
                {"id": "market", "title": "A股本周收盘观察",
                 "url": "https://example.org/market-context", "publishedAt": published,
                 "snippet": "股票市场成交变化。"},
                {"id": "subject", "title": "华为发布新产品",
                 "url": "https://example.org/subject-context", "publishedAt": published,
                 "snippet": "产业链预期变化，报道未提股价。"},
                {"id": "direct", "title": "华为概念股票本周观察",
                 "url": "https://example.org/direct", "publishedAt": published,
                 "snippet": "股票市场出现变化。"},
            ]
            return ToolResult(content={"searchedAt": timestamp()}, sources=rows)
        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "稀疏摘要测试", {"type": "object"}, search, max_calls=2),))
        run_id = self.submit("华为概念股票本周怎么样？")
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual([row["title"] for row in self.provider.initial_payloads[0]["sources"]],
                         ["华为发布新产品", "A股本周收盘观察"])
        self.assertEqual(len(self.tools.calls), 1)
        self.assertEqual(self.tools.calls[0]["count"], 2)
        self.assertEqual(waiting["materials"], [])
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                   self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual([row["title"] for row in done["report"]["sources"]],
                         ["华为概念股票本周观察", "华为发布新产品", "A股本周收盘观察"])
        self.assertEqual([row["module"] for row in done["report"]["recommendations"]],
                         ["event", "relation", "history"])
        self.assertEqual(len(self.tools.calls), 2)
        self.assertEqual([call["count"] for call in self.tools.calls], [2, 5])
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 6)
        self.assertEqual(done["report"]["retrieval"]["tool_trace"][0]["filter_counts"], {})

    def test_zero_score_context_reaches_model_but_not_unreviewed_excerpt_fallback(self):
        def search(arguments, context):
            self.tools.calls.append(dict(arguments))
            return ToolResult(content={"searchedAt": timestamp()}, sources=[{
                "id": "background", "title": "高股息风格获资金关注",
                "url": "https://example.org/sector-context", "publishedAt": timestamp(),
                "snippet": "银行与公用事业出现防御性配置。"}])
        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "零分背景测试", {"type": "object"}, search, max_calls=2),))
        self.provider.initial_empty_output = True
        run_id = self.submit("近期A股红利低波方向资金为何抱团？")
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(len(self.provider.initial_payloads[0]["sources"]), 1)
        self.assertEqual(self.provider.initial_payloads[0]["sources"][0]["title"],
                         "高股息风格获资金关注")
        self.assertEqual(waiting["initial_answer_mode"], "knowledge_fallback")
        self.assertNotIn("银行与公用事业出现防御性配置", waiting["initial_answer"])
        self.assertEqual(len(self.tools.calls), 1)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 2)

    def test_colloquial_csi300_keeps_news_and_records_each_exclusion(self):
        def search(arguments, context):
            self.tools.calls.append(arguments)
            now = datetime.now(timezone.utc)
            base = {"title": "沪深300指数收盘观察", "snippet": "沪深300走势和成交额变化。",
                    "publishedAt": now.isoformat(), "url": "https://example.org/csi300"}
            return ToolResult(content={"searchedAt": timestamp(), "excludedCount": 0}, sources=[
                base, {**base, "url": "https://example.org/duplicate"},
                {**base, "title": "沪深300此前行情", "url": "https://example.org/old",
                 "publishedAt": (now - timedelta(days=3)).isoformat()},
                {**base, "title": "中证500指数收盘观察", "snippet": "中证500指数走势。",
                 "url": "https://example.org/unrelated"},
                {**base, "title": "沪深300行情解读", "publishedAt": None,
                 "url": "https://example.org/undated"}])
        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试搜索", {"type": "object"}, search, max_calls=2),))
        run_id = self.submit("带我了解一下这两天的沪深300")
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(len(self.tools.calls), 2)
        args = self.tools.calls[1]
        self.assertEqual(args["freshness"], "oneWeek")
        self.assertEqual(args["count"], 5)
        self.assertIn("沪深300", args["query"])
        self.assertNotIn("带我了解一下", args["query"])
        report = done["report"]
        self.assertEqual(len(report["sources"]), 1)
        trace = report["retrieval"]["tool_trace"][0]
        self.assertEqual(trace["returned_count"], 5)
        self.assertEqual(trace["result_count"], 1)
        self.assertEqual(trace["filter_counts"], {"duplicate": 1, "outside_requested_window": 1,
                                                 "identity_conflict": 1, "missing_date": 1})
        self.assertEqual(sum(row["accepted"] for row in trace["candidates"]), 1)
        self.assertEqual(self.provider.report_calls, 1)

    def test_search_failure_is_distinct_from_zero_relevant_news(self):
        def search(arguments, context):
            self.tools.calls.append(arguments)
            raise GatewayError("联网搜索超时，请稍后重试。", 504)
        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试失败搜索", {"type": "object"}, search, max_calls=2),))
        run_id = self.submit()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback")
        self.assertEqual(waiting["initial_sources"], [])
        self.assertEqual(self.provider.initial_payloads[0]["search"]["status"], "failed")
        self.assertIn("股价变化通常与预期", waiting["initial_answer"])
        self.assertNotIn("联网", waiting["initial_answer"])
        self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
        done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial")
        self.assertEqual(done["report"]["report_variant"], "search_unavailable")
        self.assertIn("搜索", done["report"]["completion_warning"])
        self.assertEqual(len(done["report"]["recommendations"]), 3)
        self.assertEqual(done["report"]["retrieval"]["searches"][0]["status"], "failed")
        self.assertEqual(self.provider.report_calls, 1)
        self.assertEqual(len(self.tools.calls), 2)

    def test_zero_news_still_gets_one_limited_model_report_and_three_questions(self):
        def search(arguments, context):
            self.tools.calls.append(arguments)
            return ToolResult(content={"searchedAt": timestamp()}, sources=[])
        self.service.search_tools = ToolRegistry((ToolSpec(
            "web_search", "测试空搜索", {"type": "object"}, search, max_calls=2),))
        run_id = self.submit()
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback")
        self.assertEqual(waiting["initial_sources"], [])
        self.assertEqual(self.provider.initial_payloads[0]["search"]["status"], "empty")
        self.assertIn("股价变化通常与预期", waiting["initial_answer"])
        self.assertNotIn("未找到相关新闻", waiting["initial_answer"])
        self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
        done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial")
        self.assertEqual(done["report"]["report_variant"], "insufficient_news")
        self.assertEqual(done["report"]["sources"], [])
        self.assertEqual([row["module"] for row in done["report"]["recommendations"]],
                         ["event", "relation", "history"])
        self.assertEqual(self.provider.report_calls, 1)
        self.assertEqual(len(self.tools.calls), 2)

    def test_invalid_recommendations_are_explicit_partial_without_fake_options(self):
        self.provider.bad_recommendations = True
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial")
        self.assertEqual(done["report"]["recommendations"], [])
        self.assertIn("三个后续建议", done["report"]["completion_warning"])
        self.assertEqual(self.provider.report_calls, 1)

    def test_empty_recommendation_reason_is_explicit_partial(self):
        self.provider.empty_recommendation_reason = True
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial")
        self.assertEqual(done["report"]["recommendations"], [])
        self.assertIn("三个后续建议", done["report"]["completion_warning"])
        self.assertEqual(self.provider.report_calls, 1)

    def test_ok_finishes_session_and_repeated_click_is_idempotent(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded")
        waiting_sibling = self.submit("A股今天的资金流向是什么？")
        self.assertEqual(self.wait(waiting_sibling, {"awaiting_feedback", "failed"})["status"],
                         "awaiting_feedback")
        key = str(uuid4())
        first = self.client.post(f"/desktop/api/explore/runs/{run_id}/finish",
                                 json={"idempotency_key": key})
        self.assertEqual(first.status_code, 200, first.json)
        self.assertEqual(first.json["run"]["next_step_state"], "finished")
        self.assertTrue(first.json["session"]["closed_at"])
        self.assertEqual(self.service.repository.get_run(self.owner, waiting_sibling)["status"], "cancelled")
        again = self.client.post(f"/desktop/api/explore/runs/{run_id}/finish",
                                 json={"idempotency_key": key})
        self.assertEqual(again.status_code, 200, again.json)
        self.assertEqual(again.json["run"]["id"], run_id)
        self.assertEqual(self.submit_after_closed_status(), 409)
        self.assertEqual(self.feedback(waiting_sibling, "explain_more").status_code, 409)
        self.assertEqual(self.client.post(f"/desktop/api/explore/runs/{run_id}/retry",
                                          json={"idempotency_key": str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.patch(f"/desktop/api/explore/sessions/{self.session_id}",
                                           json={"title": "尝试修改已结束会话"}).status_code, 409)
        self.assertEqual(self.client.delete(f"/desktop/api/explore/sessions/{self.session_id}").status_code,
                         409)
        self.assertEqual(self.client.get(f"/desktop/api/explore/sessions/{self.session_id}").status_code,
                         200)
        file_id = self.service.repository.list_files(self.owner, self.session_id)[0]["id"]
        self.assertEqual(self.client.get(
            f"/desktop/api/explore/sessions/{self.session_id}/files/{file_id}/download").status_code, 200)
        self.assertEqual(self.provider.report_calls, 1)

    def submit_after_closed_status(self):
        return self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs", json={
            "module": "event", "question": "新的问题", "idempotency_key": str(uuid4())}).status_code

    def test_recommendation_click_starts_one_new_explore_run(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        source_report_id = done["report"]["id"]
        selected = done["report"]["recommendations"][1]
        key = str(uuid4())
        payload = {"module": selected["module"], "question": selected["question"],
                   "source_report_id": source_report_id, "idempotency_key": key}
        with patch.object(self.service, "_start"):
            child = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs", json=payload)
            self.assertEqual(child.status_code, 202, child.json)
            same = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs", json=payload)
        self.assertEqual(same.status_code, 202, same.json)
        self.assertEqual(same.json["run"]["id"], child.json["run"]["id"])
        self.assertEqual(child.json["run"]["module"], "relation")
        parent = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(parent["next_step_state"], "continued")
        self.assertEqual(parent["continuation_run_id"], child.json["run"]["id"])
        another = {**payload, "idempotency_key": str(uuid4()), "module": "history",
                   "question": done["report"]["recommendations"][2]["question"]}
        self.assertEqual(self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs",
                                          json=another).status_code, 409)
        self.service.cancel(self.owner, child.json["run"]["id"])

    def test_whitespace_model_questions_are_saved_as_clickable_for_all_angles(self):
        self.provider.whitespace_recommendations = True
        for module in ("event", "relation", "history"):
            with self.subTest(module=module):
                run_id = self.submit()
                self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"],
                                 "awaiting_feedback")
                with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                           self.tools.page):
                    self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
                    done = self.wait(run_id, {"succeeded", "partial", "failed"})
                self.assertEqual(done["status"], "succeeded", done.get("error"))
                options = done["report"]["recommendations"]
                self.assertEqual([row["module"] for row in options], ["event", "relation", "history"])
                self.assertTrue(all(row["question"] == row["question"].strip() for row in options))
                selected = next(row for row in options if row["module"] == module)
                with patch.object(self.service, "_start"):
                    clicked = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs",
                                               json={"module": module, "question": selected["question"],
                                                     "source_report_id": done["report"]["id"],
                                                     "idempotency_key": str(uuid4())})
                self.assertEqual(clicked.status_code, 202, clicked.json)
                self.assertEqual(clicked.json["run"]["module"], module)
                self.service.cancel(self.owner, clicked.json["run"]["id"])

    def test_finish_and_recommendation_race_accepts_only_one_action(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        report = done["report"]
        selected = report["recommendations"][0]
        gate = threading.Barrier(2)

        def finish():
            gate.wait(timeout=2)
            try:
                return "finished", self.service.finish(self.owner, run_id, {
                    "idempotency_key": str(uuid4())})[0]["next_step_state"]
            except GatewayError as exc:
                return "finished", exc.status

        def continue_run():
            gate.wait(timeout=2)
            try:
                result = self.service.create_run(self.owner, self.session_id, {
                    "module": selected["module"], "question": selected["question"],
                    "source_report_id": report["id"], "idempotency_key": str(uuid4())})
                return "continued", result["id"]
            except GatewayError as exc:
                return "continued", exc.status

        with patch.object(self.service, "_start"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                outcomes = list(pool.map(lambda fn: fn(), (finish, continue_run)))
        self.assertEqual(sum(value != 409 for _action, value in outcomes), 1, outcomes)
        parent = self.service.repository.get_run(self.owner, run_id)
        self.assertIn(parent["next_step_state"], {"finished", "continued"})
        if parent["next_step_state"] == "continued":
            self.service.cancel(self.owner, parent["continuation_run_id"])

    def test_two_different_feedback_actions_race_only_one_is_accepted(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        gate = threading.Barrier(2)

        def choose(choice):
            gate.wait(timeout=2)
            try:
                return self.service.feedback(self.owner, run_id, {
                    "choice": choice, "idempotency_key": str(uuid4())})["status"]
            except GatewayError as exc:
                return exc.status

        with patch.object(self.service, "_start"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(choose, ("understood", "explain_more")))
        self.assertIn(409, values)
        self.assertEqual(sum(value in {"succeeded", "queued"} for value in values), 1)
        self.assertEqual(len(self.tools.calls), 1)
        if self.service.repository.get_run(self.owner, run_id)["status"] == "queued":
            self.service.cancel(self.owner, run_id)

    def test_other_account_cannot_click_feedback(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        outsider = self.app.test_client()
        joined = outsider.post("/desktop/api/register", json={
            "email": "outside-event@example.test", "password": "long-password"})
        self.assertEqual(joined.status_code, 200, joined.json)
        denied = outsider.post(f"/desktop/api/explore/runs/{run_id}/feedback", json={
            "choice": "explain_more", "idempotency_key": str(uuid4())})
        self.assertEqual(denied.status_code, 404, denied.json)
        finish_denied = outsider.post(f"/desktop/api/explore/runs/{run_id}/finish", json={
            "idempotency_key": str(uuid4())})
        self.assertEqual(finish_denied.status_code, 404, finish_denied.json)
        self.assertEqual(len(self.tools.calls), 1)

    def test_cancel_while_deep_model_returns_late_cannot_save_report(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            self.assertTrue(self.provider.report_started.wait(timeout=3))
            stopped = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
            self.assertEqual(stopped.status_code, 200, stopped.json)
            self.provider.report_release.set()
            time.sleep(.05)
        final = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(final["report"])

    def test_cancelled_first_stage_has_no_temporary_news_to_revive(self):
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.initial_started.wait(timeout=3))
                in_progress = self.service.repository.get_run(self.owner, run_id)
                self.assertEqual(in_progress["stage"], "explaining")
                self.assertEqual(in_progress["materials"], [])
                self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])
                cancelled = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
                self.assertEqual(cancelled.status_code, 200, cancelled.json)
            finally:
                self.provider.initial_release.set()
        final = self.wait(run_id, {"cancelled", "failed", "awaiting_feedback"})
        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(final.get("initial_answer"))
        self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])

    def test_cancelled_first_stage_cannot_be_revived_by_timeout_fallback(self):
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.initial_started.wait(timeout=3))
                cancelled = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
                self.assertEqual(cancelled.status_code, 200, cancelled.json)
                time.sleep(.6)
            finally:
                self.provider.initial_release.set()
        final = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(final["status"], "cancelled")
        self.assertIsNone(final.get("initial_answer"))

    def test_deleted_session_cannot_be_revived_by_first_stage_timeout_fallback(self):
        self.provider.initial_started = threading.Event()
        self.provider.initial_release = threading.Event()
        with patch("backend.app.modules.explore.service.EVENT_INITIAL_TIMEOUT_SECONDS", 0.5):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.initial_started.wait(timeout=3))
                deleted = self.client.delete(f"/desktop/api/explore/sessions/{self.session_id}")
                self.assertEqual(deleted.status_code, 200, deleted.json)
                time.sleep(.6)
            finally:
                self.provider.initial_release.set()
        self.assertEqual(self.client.get(f"/desktop/api/explore/runs/{run_id}").status_code, 404)

    def test_deleted_session_blocks_late_deep_report(self):
        run_id = self.submit()
        self.assertEqual(self.wait(run_id, {"awaiting_feedback", "failed"})["status"], "awaiting_feedback")
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", self.tools.page):
            self.assertEqual(self.feedback(run_id, "explain_more").status_code, 202)
            self.assertTrue(self.provider.report_started.wait(timeout=3))
            deleted = self.client.delete(f"/desktop/api/explore/sessions/{self.session_id}")
            self.assertEqual(deleted.status_code, 200, deleted.json)
            self.provider.report_release.set()
            time.sleep(.05)
        with self.assertRaises(GatewayError):
            self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(self.client.get(f"/desktop/api/explore/runs/{run_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
