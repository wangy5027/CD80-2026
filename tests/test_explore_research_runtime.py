"""Explore's real registry/repository loop with a scripted model and page reader.

No external model, network request or user database is used by these tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.explore.repository import ExploreRepository, file_id_for, timestamp, uid
from backend.app.modules.explore.contracts import parse_json_object
from backend.app.modules.explore.research_runtime import ResearchRuntime, validate_brief
from backend.app.modules.explore.research_task import build_research_task
from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.skills import SkillRegistry
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


def call(call_id, name, args):
    return {"id": call_id, "type": "function", "function": {"name": name,
            "arguments": json.dumps(args, ensure_ascii=False)}}


def brief(questions, claim=None, *, done=True):
    claims = [claim] if claim else []
    return json.dumps({"coverage": [{"question_id": q["id"],
                                     "status": "answered" if claim else "unanswered",
                                     "claim_ids": [claim["id"]] if claim else [],
                                     "gap": "" if claim else "还需原文", "next_action": ""}
                                    for q in questions],
                       "claims": claims, "conflicts": [], "done": done}, ensure_ascii=False)


class ScriptProvider:
    model = "scripted-events"

    def __init__(self, steps):
        self.steps = list(steps)
        self.turns = []

    def events(self, messages, model, stopped, on_response, *, tools=None, **_):
        index = len(self.turns)
        self.turns.append({"messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        body, calls = self.steps[index](messages, tools)
        if body is not None:
            # An event stream can deliver text in fragments before tool calls.
            midpoint = max(1, len(body) // 3)
            yield {"type": "token", "text": body[:midpoint]}
            yield {"type": "token", "text": body[midpoint:]}
        yield {"type": "turn_end", "tool_calls": calls, "finish_reason": "tool_calls" if calls else "stop"}


class LedgerTimeoutProvider(ScriptProvider):
    connect_timeout = 5.0
    read_timeout = 120.0

    def __init__(self, steps, *, error=None, before_error=None):
        super().__init__(steps)
        self.error = error or GatewayError(
            "等待模型服务响应头超时，请稍后重试。", 504, "model_header_timeout")
        self.before_error = before_error

    def events(self, messages, model, stopped, on_response, *, tools=None, **options):
        if tools is None:
            self.turns.append({"messages": copy.deepcopy(messages), "tools": None})
            if self.before_error:
                self.before_error()
            raise self.error
        yield from super().events(messages, model, stopped, on_response,
                                  tools=tools, **options)


class ServiceReportProvider(ScriptProvider):
    def __init__(self, steps, *, review_issues_once=False):
        super().__init__(steps)
        self.writer_payloads = []
        self.review_issues_once = review_issues_once
        self.review_calls = 0

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        self.writer_payloads.append(payload)
        if payload.get("task") != "research_report":
            raise AssertionError("Specific question should not enter legacy clarification")
        if payload.get("stage") == "claim_review":
            brief = payload["research_brief"]
            question_ids_by_claim = {}
            for row in brief["coverage"]:
                for claim_id in row.get("claim_ids", []):
                    question_ids_by_claim.setdefault(claim_id, []).append(row["question_id"])
            reviews = []
            for claim in brief["claims"]:
                if claim["support_status"] != "traceable":
                    raise AssertionError("claim review received unsupported research claim")
                reviews.append({"id": claim["id"], "decision": "approve",
                                "text": claim["text"], "type": claim["type"],
                                "support_indices": list(range(len(claim["supports"]))),
                                "question_ids": question_ids_by_claim.get(claim["id"], []),
                                "reason": "fixture exact support"})
            coverage = [{"question_id": row["question_id"], "status": row["status"],
                         "gap": row.get("gap", ""), "next_action": row.get("next_action", "")}
                        for row in brief["coverage"]]
            yield json.dumps({"claim_reviews": reviews, "coverage": coverage}, ensure_ascii=False)
            return
        if payload.get("stage") == "review":
            self.review_calls += 1
            if self.review_issues_once and self.review_calls == 1:
                self.assert_draft_has_unsupported_number(payload["draft"])
                yield json.dumps({"issues": ["summary 中的 500 亿元无任何主张或来源支持"]}, ensure_ascii=False)
            else:
                yield '{"issues":[]}'
            return
        sources = payload["sources"]
        refs = [sources[0]["id"]] if sources else []
        report = {"title": "尊界V800事件研究", "summary": "据本轮材料，已有公开回应。",
                  "key_findings": ["公开回应已见材料，责任条款仍有待核查。"],
                  "uncertainties": [], "limitations": ["合同全文仍需核对。"],
                  "recommendations": [],
                  "sections": [{"heading": "直接回答", "body": "公开回应已见材料。", "source_refs": refs}],
                  "event_overview": "尊界V800刹车踏板支架断裂相关回应",
                  "background": "本轮根据公开检索摘要说明回应，不推断零件责任。",
                  "actors": ["尊界"], "drivers": [], "impact_paths": [], "timeline": []}
        if payload.get('report_format') == 'event_brief@1':
            report.update(event_overview='', background='', actors=[], limitations=[])
        if self.review_issues_once and "repair" not in payload:
            report["summary"] += "业务损失达 500 亿元。"
        if "repair" in payload:
            if not payload["repair"]["issues"]:
                raise AssertionError("repair must carry the review's concrete issue")
        yield json.dumps(report, ensure_ascii=False)

    @staticmethod
    def assert_draft_has_unsupported_number(draft):
        if "500 亿元" not in draft.get("summary", ""):
            raise AssertionError("review fixture did not receive its defective draft")


class SearchFixture:
    def __init__(self):
        self.calls = []
        self.started = None
        self.release = None
        self.fail_once = False
        self.title = "尊界V800刹车踏板支架断裂：官方回应与责任边界"
        self.snippet = "尊界V800刹车踏板支架断裂事件，官方已发布说明。合作合同责任仍需原文核对。"
        self.url = "https://public.example.test/v800-incident"

    def tool(self, *, max_calls=3):
        def run(args, context):
            self.calls.append(dict(args))
            if self.fail_once:
                self.fail_once = False
                raise GatewayError("测试搜索暂时失败", 502)
            if self.started is not None:
                self.started.set()
                self.release.wait(3)
            return ToolResult(content={"query": args["query"], "searchedAt": timestamp()}, sources=[
                {"id": "fixture-result", "title": self.title,
                 "snippet": self.snippet,
                 "url": self.url, "publishedAt": "2026-10-10"},
            ])

        return ToolSpec(name="web_search", description="测试搜索", parameters={
            "type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}},
            "required": ["query"], "additionalProperties": False,
        }, handler=run, capabilities=frozenset({"search"}), max_calls=max_calls)


class PageFixture:
    def __init__(self, text="公开正文显示尊界V800刹车踏板支架断裂的回应。"):
        self.text = text
        self.calls = []
        self.status = "complete"
        self.links = []
        self.final_url = None
        self.canonical_url = None

    def tool(self):
        def run(args, _context):
            self.calls.append(dict(args))
            return ToolResult(content={"status": self.status,
                               "final_url": self.final_url or args["url"],
                               "canonical_url": self.canonical_url or args["url"],
                               "title": "事件官方说明",
                               "publisher": "示例发布者",
                               "text": self.text if self.status == "complete" else "",
                               "links": self.links})

        return ToolSpec(name="fetch_public_page", description="测试公开原文", parameters={
            "type": "object", "properties": {"url": {"type": "string", "minLength": 1},
                                             "target_section": {"type": "string"},
                                             "max_chars": {"type": "integer", "minimum": 1000,
                                                           "maximum": 65000}},
            "required": ["url"], "additionalProperties": False,
        }, handler=run, capabilities=frozenset({"research_public_read"}), max_calls=3)


class Harness:
    def __init__(self, root, provider, search, *, limits=None, sfc=None):
        self.repository = ExploreRepository(str(root / "data"))
        self.provider = provider
        specs = [search.tool()]
        if sfc:
            specs.append(sfc)
        self.search_tools = ToolRegistry(specs)
        skill_root = root / "skills"
        for module in ("event", "relation", "history"):
            folder = skill_root / f"explore-{module}"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: explore-{module}\ndescription: 测试 {module} 工作流\n---\n只处理本次研究。\n", encoding="utf-8")
        self.research_skills = SkillRegistry(skill_root)
        self.data_skills = SkillRegistry(root / "data_skills")
        self.research_limits = limits or {"model_rounds": 6, "tool_calls": 9,
                                          "context_chars": 100000, "write_reserve_seconds": 1}
        self.timeout = 60

    @staticmethod
    def _check_stop(control):
        if control["stop"].is_set():
            raise GatewayError("已取消本次分析。", 409)

    _reserve_model_retry = ExploreService._reserve_model_retry


class ResearchRuntimeTests(unittest.TestCase):
    owner = "research-fixture-owner"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-research-loop-")
        self.root = Path(self.temp.name)
        self.search = SearchFixture()
        self.page = PageFixture()

    def tearDown(self):
        self.temp.cleanup()

    def make_runtime(self, provider, question, *, module="event", resolved=None,
                     limits=None, sfc=None, selected_report_text=None,
                     research_skills_root=None, authorized_public_urls=None):
        service = Harness(self.root, provider, self.search, limits=limits, sfc=sfc)
        if research_skills_root is not None:
            service.research_skills = SkillRegistry(research_skills_root)
        session = service.repository.create_session(self.owner, "研究循环测试")
        selection = None
        if selected_report_text is not None:
            file_id = file_id_for("old-ai-report:" + uid())
            with service.repository.transaction() as db:
                service.repository._insert_file(
                    db, file_id=file_id, owner=self.owner, session_id=session["id"],
                    origin_key="old-ai-report:" + file_id, kind="report", origin="report",
                    name="旧AI报告.md", content=selected_report_text,
                    metadata={"context_text": selected_report_text}, created_at=timestamp())
            selection = {"mode": "manual", "file_ids": [file_id]}
        now = timestamp()
        run = {"id": uid(), "session_id": session["id"], "module": module,
               "question": question, "resolved_question": resolved or question,
               "time_policy": {"as_of": now}, "created_at": now, "updated_at": now,
               "status": "running", "stage": "researching",
               "stage_history": [{"stage": "researching", "at": now}],
               "execution_id": uid(), "materials": [], "retrieval": {}, "source_report_id": None}
        if authorized_public_urls is not None:
            run["authorized_public_urls"] = authorized_public_urls
        saved, created = service.repository.create_run(self.owner,
            {"idempotency_key": uid(), "module": module, "question": question}, run, 2,
            selection_override=selection)
        self.assertTrue(created)
        control = {"stop": threading.Event(), "response": None,
                   "execution_id": saved["execution_id"]}
        with patch("backend.app.modules.explore.research_runtime.build_public_reader_tool", self.page.tool):
            runtime = ResearchRuntime(service, self.owner, saved, control)
        return runtime, service, saved, control

    @staticmethod
    def use_budget_clipped_provider(service):
        def local_provider(deadline):
            provider = copy.copy(service.provider)
            remaining = max(0.1, deadline - time.monotonic())
            provider.connect_timeout = min(provider.connect_timeout, remaining / 4)
            provider.read_timeout = min(provider.read_timeout,
                                        remaining - provider.connect_timeout)
            return provider

        service._provider_for_deadline = local_provider

    def test_each_product_module_loads_its_real_research_skill(self):
        skill_root = Path(__file__).resolve().parents[1] / "backend/app/modules/explore/skills"
        headings = {"event": "# Explore：理解事件",
                    "relation": "# Explore：发现关联",
                    "history": "# Explore：历史验证"}
        for module, heading in headings.items():
            with self.subTest(module=module):
                def finish(messages, _tools):
                    system = messages[0]["content"]
                    self.assertIn(heading, system)
                    for other in set(headings.values()) - {heading}:
                        self.assertNotIn(other, system)
                    task = json.loads(messages[1]["content"])["research_task"]
                    return brief(task["subquestions"]), []

                provider = ScriptProvider([finish])
                runtime, _service, _saved, _ = self.make_runtime(
                    provider, "尊界V800踏板支架事件有哪些可核查材料？",
                    module=module, research_skills_root=skill_root)
                result = runtime.run_research()
                item = SkillRegistry(skill_root).load("explore-" + module)
                self.assertIn(item["instructions"], provider.turns[0]["messages"][0]["content"])
                self.assertEqual([x["name"] for x in result["skills_used"]],
                                 ["explore-" + module])
                self.assertEqual(result["skills_used"][0]["content_hash"],
                                 hashlib.sha256(item["instructions"].encode()).hexdigest())

    def test_on_demand_skill_is_allowlisted_loaded_once_and_errors_return_to_model(self):
        skill_root = self.root / "data_skills"
        for name in ("news-search", "unrelated"):
            folder = skill_root / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: 测试按需加载\n---\n"
                f"{name} 的测试专用操作说明。\n", encoding="utf-8")
        marker = "news-search 的测试专用操作说明。"

        def first(messages, tools):
            load = next(x for x in tools if x["function"]["name"] == "load_skill")
            self.assertEqual(load["function"]["parameters"]["properties"]["name"]["enum"],
                             ["news-search"])
            self.assertNotIn(marker, messages[0]["content"])
            return None, [call("skill-first", "load_skill", {"name": "news-search"})]

        def second(messages, _tools):
            loaded = json.loads(messages[-2]["content"])
            self.assertEqual(loaded["instructions"], marker)
            return None, [call("skill-repeat", "load_skill", {"name": "news-search"})]

        def third(messages, _tools):
            repeated = json.loads(messages[-2]["content"])
            self.assertEqual(repeated["status"], 409)
            self.assertNotIn("instructions", repeated)
            return None, [call("skill-unlisted", "load_skill", {"name": "unrelated"})]

        def fourth(messages, _tools):
            unlisted = json.loads(messages[-2]["content"])
            self.assertEqual(unlisted["status"], 403)
            return None, [call("skill-missing", "load_skill", {"name": "sfc-news"})]

        def finish(messages, _tools):
            missing = json.loads(messages[-2]["content"])
            self.assertEqual(missing["status"], 503)
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        sfc = ToolSpec(name="sfc_news", description="仅用于测试允许目录", parameters={
            "type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda _args, _context: ToolResult(content={"ok": True}))
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([first, second, third, fourth, finish]),
            "尊界V800踏板支架事件进展", sfc=sfc)
        result = runtime.run_research()
        self.assertEqual([x["name"] for x in result["skills_used"]],
                         ["explore-event", "news-search"])
        self.assertEqual([x["status"] for x in result["retrieval"]["tool_trace"]],
                         ["complete", "error", "error", "error"])
        self.assertNotIn(marker, json.dumps(result["retrieval"]["tool_trace"], ensure_ascii=False))

    def test_multiround_search_fetch_and_evidence_continuation_are_traceable(self):
        quote = "第二页原文明确说明：该项处理仍待权责条款核对。"
        self.page.text = "首段研究资料。" + ("甲" * 4200) + quote

        def first(_messages, tools):
            self.assertIn("web_search", [x["function"]["name"] for x in tools])
            return None, [call("search-1", "web_search", {"query": "尊界V800刹车踏板支架断裂"})]

        def second(messages, _tools):
            result = json.loads(messages[-2]["content"])
            self.assertEqual(messages[-2]["tool_call_id"], "search-1")
            self.assertEqual(result["evidence"][0]["content_kind"], "search_snippet")
            return None, [call("page-1", "fetch_public_page",
                               {"url": "https://public.example.test/v800-incident"})]

        def third(messages, _tools):
            result = json.loads(messages[-2]["content"])
            self.assertEqual(messages[-2]["tool_call_id"], "page-1")
            self.assertTrue(result["result"]["has_more"])
            evidence_id = result["result"]["evidence_id"]
            return None, [call("read-1", "read_evidence",
                               {"evidence_id": evidence_id, "offset": 4000, "limit": 2000})]

        def fourth(messages, _tools):
            result = json.loads(messages[-2]["content"])
            self.assertEqual(messages[-2]["tool_call_id"], "read-1")
            self.assertIn(quote, result["result"]["text"])
            evidence_id = result["result"]["evidence_id"]
            claim = {"id": "C1", "text": "处理条款仍待核对", "type": "sourced_statement",
                     "supports": [{"evidence_id": evidence_id, "quote": quote}]}
            return brief(build_research_task({"question": "尊界V800刹车踏板支架断裂处理情况"})["subquestions"], claim), []

        provider = ScriptProvider([first, second, third, fourth])
        runtime, service, saved, _ = self.make_runtime(provider, "尊界V800刹车踏板支架断裂处理情况")
        result = runtime.run_research()
        self.assertEqual(result["brief"]["answer_status"], "answered")
        self.assertEqual([x["name"] for x in result["retrieval"]["tool_trace"]],
                         ["web_search", "fetch_public_page", "read_evidence"])
        self.assertEqual([x["call_id"] for x in result["retrieval"]["tool_trace"]],
                         ["search-1", "page-1", "read-1"])
        self.assertEqual(result["brief"]["claims"][0]["support_status"], "traceable")
        persisted = service.repository.get_run(self.owner, saved["id"])
        self.assertEqual(persisted["research_budget"]["tool_calls"], 3)
        self.assertEqual(persisted["research_brief"]["answer_status"], "answered")

    def test_unknown_tool_returns_matching_error_then_model_can_continue(self):
        def first(_messages, _tools):
            return None, [call("unknown-1", "shell", {"cmd": "true"})]

        def second(messages, _tools):
            self.assertEqual(messages[-2]["tool_call_id"], "unknown-1")
            self.assertEqual(json.loads(messages[-2]["content"])["status"], 403)
            return None, [call("search-2", "web_search", {"query": "尊界V800刹车踏板支架断裂"})]

        def third(messages, _tools):
            result = json.loads(messages[-2]["content"])
            evidence = result["evidence"][0]
            snippet = "官方已发布说明"
            claim = {"id": "C1", "text": snippet, "type": "sourced_statement",
                     "supports": [{"evidence_id": evidence["evidence_id"], "quote": snippet}]}
            return brief(build_research_task({"question": "尊界V800刹车踏板支架断裂"})["subquestions"], claim), []

        provider = ScriptProvider([first, second, third])
        runtime, _service, _saved, _ = self.make_runtime(provider, "尊界V800刹车踏板支架断裂")
        result = runtime.run_research()
        self.assertEqual([x["status"] for x in result["retrieval"]["tool_trace"]], ["error", "complete"])
        self.assertEqual(len(self.search.calls), 1)
        self.assertEqual(result["brief"]["answer_status"], "answered")

    def test_registered_tool_failure_is_returned_and_gap_query_recovers(self):
        self.search.fail_once = True

        def first(_messages, _tools):
            return None, [call("search-failed", "web_search", {"query": "尊界V800刹车踏板支架断裂"})]

        def second(messages, _tools):
            self.assertEqual(messages[-2]["tool_call_id"], "search-failed")
            self.assertEqual(json.loads(messages[-2]["content"])["status"], 502)
            return None, [call("search-retry", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 官方回应"})]

        def third(messages, _tools):
            self.assertEqual(messages[-2]["tool_call_id"], "search-retry")
            source = json.loads(messages[-2]["content"])["evidence"][0]
            claim = {"id": "C1", "text": "官方发布了说明", "type": "sourced_statement",
                     "supports": [{"evidence_id": source["evidence_id"], "quote": "官方已发布说明"}]}
            return brief(build_research_task({"question": "尊界V800刹车踏板支架断裂"})["subquestions"], claim), []

        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([first, second, third]), "尊界V800刹车踏板支架断裂")
        result = runtime.run_research()
        self.assertEqual([x["status"] for x in result["retrieval"]["tool_trace"]], ["error", "complete"])
        self.assertEqual([x["query"] for x in self.search.calls],
                         ["尊界V800刹车踏板支架断裂", "尊界V800刹车踏板支架断裂 官方回应"])
        self.assertEqual(result["brief"]["answer_status"], "answered")

    def test_duplicate_schema_and_per_tool_budget_are_executed_by_registry(self):
        runtime, _service, _saved, _ = self.make_runtime(ScriptProvider([]), "尊界V800刹车踏板支架断裂")
        bad = runtime.execute("web_search", {"query": 42}, "bad-schema")
        self.assertEqual(bad["status"], 400)
        self.assertEqual(self.search.calls, [])
        first = runtime.execute("web_search", {"query": "尊界V800刹车踏板支架断裂"}, "first")
        self.assertIn("evidence", first)
        repeated = runtime.execute("web_search", {"query": "尊界V800刹车踏板支架断裂"}, "repeated")
        self.assertEqual(repeated["status"], 409)
        self.assertEqual(len(self.search.calls), 1)
        # Every attempted call counts, including schema errors and duplicates.
        exhausted = runtime.execute("web_search", {"query": "尊界V800刹车踏板其他回应"}, "exhausted")
        self.assertEqual(exhausted["status"], 429)
        self.assertEqual(runtime.counts["web_search"], 4)
        self.assertEqual(len(self.search.calls), 1)

    def test_total_budget_stops_new_handler_calls(self):
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), "尊界V800刹车踏板支架断裂",
            limits={"model_rounds": 3, "tool_calls": 1, "context_chars": 50000,
                    "write_reserve_seconds": 1})
        runtime.execute("web_search", {"query": "尊界V800刹车踏板支架断裂"}, "first")
        blocked = runtime.execute("web_search", {"query": "尊界V800刹车踏板其他回应"}, "second")
        self.assertEqual(blocked["status"], 429)
        self.assertEqual(len(self.search.calls), 1)

    def test_explicit_url_is_fetched_before_first_model_turn(self):
        url = "https://public.example.test/article?id=42#data-availability"
        question = url + " 研究一下这个文章"

        def final(messages, tools):
            user = json.loads(messages[1]["content"])
            self.assertEqual(user["research_task"]["urls"], [url])
            self.assertEqual(user["url_reads"][0]["result"]["final_url"], url)
            self.assertEqual(len(self.page.calls), 1)
            self.assertEqual(messages[0]["role"], "system")
            names = {x["function"]["name"] for x in tools}
            self.assertNotIn("sfc_quotes", names)
            return brief(user["research_task"]["subquestions"], done=True), []

        sfc = ToolSpec(name="sfc_quotes", description="模拟金融工具", parameters={
            "type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda _args, _context: ToolResult(content={"ok": True}))
        provider = ScriptProvider([final])
        runtime, _service, _saved, _ = self.make_runtime(provider, question, sfc=sfc)
        result = runtime.run_research()
        self.assertEqual(result["retrieval"]["tool_trace"][0]["initiator"], "task_url")
        self.assertEqual(result["retrieval"]["tool_trace"][0]["name"], "fetch_public_page")
        self.assertEqual(result["brief"]["answer_status"], "unanswered")

    def test_server_authorized_public_entry_can_be_fetched_without_search(self):
        entry = "https://www.swufe.edu.cn/"
        unused = "https://news.swufe.edu.cn/"
        linked = "https://www.swufe.edu.cn/info/1001/1234.htm"
        self.page.text = "西南财经大学公开网站提供学校新闻资料。"
        self.page.links = [{"url": linked, "text": "新闻正文"}]

        def read_entry(messages, tools):
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["research_task"]["urls"], [])
            self.assertEqual(payload["authorized_public_urls"], [entry, unused])
            self.assertEqual(payload["url_reads"], [])
            self.assertEqual(self.page.calls, [])
            self.assertIn("fetch_public_page", {tool["function"]["name"] for tool in tools})
            return None, [call("entry-read", "fetch_public_page", {"url": entry})]

        def follow_link(messages, _tools):
            first = json.loads(messages[-2]["content"])
            self.assertEqual(first["result"]["final_url"], entry)
            self.assertEqual(first["result"]["links"][0]["url"], linked)
            return None, [call("linked-read", "fetch_public_page", {"url": linked})]

        def finish(messages, _tools):
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([read_entry, follow_link, finish]),
            "请研究西南财经大学金融人才培养基地的公开资料",
            authorized_public_urls=[entry, unused])
        result = runtime.run_research()
        self.assertEqual([item["name"] for item in result["retrieval"]["tool_trace"]],
                         ["fetch_public_page", "fetch_public_page"])
        self.assertEqual([item["url"] for item in self.page.calls], [entry, linked])
        self.assertEqual(self.search.calls, [])

    def test_authorized_entry_does_not_grant_other_paths_or_query_variants(self):
        entry = "https://www.swufe.edu.cn/"
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), "请研究西南财经大学公开信息",
            authorized_public_urls=[entry])
        for candidate in ("https://www.swufe.edu.cn/private",
                          "https://www.swufe.edu.cn/?selected_file_excerpt=private",
                          "https://news.swufe.edu.cn/"):
            with self.subTest(candidate=candidate):
                result = runtime.execute("fetch_public_page", {"url": candidate},
                                         "unauthorized-" + str(len(runtime.traces)))
                self.assertEqual(result["status"], 403)
        self.assertEqual(self.page.calls, [])

    def test_qualified_search_result_grants_only_its_same_origin_homepage(self):
        article = "https://public.example.test/news/v800-incident?view=full"
        homepage = "https://public.example.test/"
        self.search.url = article
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), "尊界V800刹车踏板支架断裂处理情况")
        result = runtime.execute("web_search",
                                 {"query": "尊界V800刹车踏板支架断裂"},
                                 "qualified-search")
        self.assertEqual(len(result["evidence"]), 1)
        self.assertIn(article, runtime.allowed_urls)
        self.assertIn(homepage, runtime.allowed_urls)
        read = runtime.execute("fetch_public_page", {"url": homepage}, "read-home")
        self.assertEqual(read["result"]["final_url"], homepage)
        for candidate in ("https://public.example.test/other-article",
                          "https://public.example.test/?query=unapproved",
                          "https://another.example.test/"):
            with self.subTest(candidate=candidate):
                blocked = runtime.execute(
                    "fetch_public_page", {"url": candidate},
                    "blocked-home-expansion-" + str(len(runtime.traces)))
                self.assertEqual(blocked["status"], 403)
        self.assertEqual([item["url"] for item in self.page.calls], [homepage])

    def test_unqualified_search_and_structured_data_do_not_grant_homepage(self):
        article = "https://other.example.test/research/article"
        self.search.title = "天气信息"
        self.search.snippet = "今天天气晴朗。"
        self.search.url = article
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), "尊界V800刹车踏板支架断裂处理情况")
        result = runtime.execute("web_search",
                                 {"query": "尊界V800刹车踏板支架断裂"},
                                 "unqualified-search")
        self.assertEqual(result["evidence"], [])
        blocked = runtime.execute("fetch_public_page",
                                  {"url": "https://other.example.test/"},
                                  "unqualified-home")
        self.assertEqual(blocked["status"], 403)

        sfc = ToolSpec(name="sfc_news", description="结构化资料测试", parameters={
            "type": "object", "properties": {}, "additionalProperties": False},
            handler=lambda _args, _context: ToolResult(content={"ok": True}, sources=[
                {"title": "结构化资料", "snippet": "引用信息", "url": article}]))
        structured_runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), "尊界V800刹车踏板支架断裂处理情况", sfc=sfc)
        structured_runtime.execute("sfc_news", {}, "structured-source")
        self.assertIn(article, structured_runtime.allowed_urls)
        self.assertNotIn("https://other.example.test/", structured_runtime.allowed_urls)
        blocked = structured_runtime.execute(
            "fetch_public_page", {"url": "https://other.example.test/"},
            "structured-home")
        self.assertEqual(blocked["status"], 403)
        self.assertEqual(self.page.calls, [])

    def test_actual_final_and_canonical_urls_grant_only_their_homepages(self):
        entry = "https://public.example.test/start"
        final_url = "https://redirect.example.test/articles/item?view=full"
        canonical_url = "https://canonical.example.test/articles/item"
        self.page.final_url = final_url
        self.page.canonical_url = canonical_url
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), entry + " 请核查正文")
        runtime.execute("fetch_public_page", {"url": entry}, "read-entry")
        for home in ("https://redirect.example.test/",
                     "https://canonical.example.test/"):
            self.assertIn(home, runtime.allowed_urls)
            read = runtime.execute("fetch_public_page", {"url": home},
                                   "read-home-" + str(len(runtime.traces)))
            self.assertEqual(read["result"]["status"], "complete")
        for candidate in ("https://redirect.example.test/private",
                          "https://canonical.example.test/?query=unapproved"):
            blocked = runtime.execute("fetch_public_page", {"url": candidate},
                                      "blocked-canonical-" + str(len(runtime.traces)))
            self.assertEqual(blocked["status"], 403)
        self.assertEqual([item["url"] for item in self.page.calls],
                         [entry, "https://redirect.example.test/",
                          "https://canonical.example.test/"])

    def test_urls_only_mentioned_in_page_body_or_old_ai_report_are_not_authorized(self):
        entry = "https://public.example.test/authorized-start"
        body_url = "https://public.example.test/body-only"
        report_url = "https://public.example.test/report-only"
        self.page.text = "普通正文提到 " + body_url + "，但未作为链接返回。"
        body_runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), entry + " 请核查公开资料")
        body_runtime.execute("fetch_public_page", {"url": entry}, "read-start")
        blocked_body = body_runtime.execute(
            "fetch_public_page", {"url": body_url}, "body-not-authorized")
        self.assertEqual(blocked_body["status"], 403)
        self.assertEqual([item["url"] for item in self.page.calls], [entry])

        report_runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), "请研究西南财经大学公开资料",
            selected_report_text="旧 AI 报告提到 " + report_url)
        selected = report_runtime.evidence.selected_catalog()[0]
        read = report_runtime.execute("read_evidence",
                                      {"evidence_id": selected["evidence_id"]},
                                      "read-old-report-url")
        self.assertIn(report_url, read["result"]["text"])
        blocked_report = report_runtime.execute(
            "fetch_public_page", {"url": report_url}, "report-not-authorized")
        self.assertEqual(blocked_report["status"], 403)
        self.assertEqual([item["url"] for item in self.page.calls], [entry])

    def test_persisted_trace_omits_public_body_and_continuation_text(self):
        first_marker = "VISIBLE_ONLY_IN_MODEL_INITIAL_BODY_2d8f"
        later_marker = "VISIBLE_ONLY_IN_MODEL_CONTINUATION_7a41"
        self.page.text = first_marker + ("甲" * 4100) + later_marker
        url = "https://public.example.test/trace-provenance"

        def continue_read(messages, _tools):
            initial = json.loads(messages[1]["content"])["url_reads"][0]
            self.assertIn(first_marker, initial["evidence"][0]["text"])
            return None, [call("continue-1", "read_evidence", {
                "evidence_id": initial["result"]["evidence_id"],
                "offset": 4000, "limit": 1000})]

        def finish(messages, _tools):
            continued = json.loads(messages[-2]["content"])
            self.assertEqual(messages[-2]["tool_call_id"], "continue-1")
            self.assertIn(later_marker, continued["result"]["text"])
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        runtime, service, saved, _ = self.make_runtime(
            ScriptProvider([continue_read, finish]), url + " 请核对全文")
        runtime.run_research()
        stored = service.repository.get_run(self.owner, saved["id"])
        trace = stored["retrieval"]["tool_trace"]
        self.assertEqual([item["name"] for item in trace],
                         ["fetch_public_page", "read_evidence"])
        self.assertTrue(all(len(item["result_hash"]) == 64 for item in trace))
        trace_text = json.dumps(trace, ensure_ascii=False)
        self.assertNotIn(first_marker, trace_text)
        self.assertNotIn(later_marker, trace_text)
        self.assertNotIn("甲" * 100, trace_text)

    def test_access_restricted_url_is_not_retried_with_changed_max_chars(self):
        self.page.status = "access_restricted"
        url = "https://public.example.test/restricted"

        def first(messages, _tools):
            seed = json.loads(messages[1]["content"])["url_reads"][0]
            self.assertEqual(seed["result"]["status"], "access_restricted")
            return None, [call("retry-small", "fetch_public_page",
                               {"url": url, "max_chars": 1000})]

        def second(messages, _tools):
            self.assertEqual(json.loads(messages[-2]["content"])["status"], 409)
            return None, [call("retry-large", "fetch_public_page",
                               {"url": url, "max_chars": 65000})]

        def finish(messages, _tools):
            self.assertEqual(json.loads(messages[-2]["content"])["status"], 409)
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([first, second, finish]), url + " 有什么内容？")
        result = runtime.run_research()
        self.assertEqual(len(self.page.calls), 1)
        self.assertEqual([item["status"] for item in result["retrieval"]["tool_trace"]],
                         ["complete", "error", "error"])

    def test_deleted_source_body_is_scrubbed_before_next_model_turn(self):
        marker = "REMOVED_SOURCE_BODY_385b1c"
        self.page.text = marker + "公开正文显示尊界V800回应。"
        url = "https://public.example.test/deleted-after-reading"
        service = saved = None

        def delete_after_first_turn(messages, _tools):
            self.assertIn(marker, json.dumps(messages, ensure_ascii=False))
            files = service.repository.list_files(self.owner, saved["session_id"])
            self.assertEqual(len(files), 1)
            service.repository.delete_file(self.owner, saved["session_id"], files[0]["id"])
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"], done=False), []

        def next_turn(messages, _tools):
            self.assertNotIn(marker, json.dumps(messages, ensure_ascii=False))
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        provider = ScriptProvider([delete_after_first_turn, next_turn])
        runtime, service, saved, _ = self.make_runtime(provider, url + " 请核查回应")
        result = runtime.run_research()
        self.assertEqual(len(provider.turns), 2)
        self.assertEqual(result["brief"]["answer_status"], "unanswered")

    def test_deleted_selected_ai_report_stops_before_next_provider_turn(self):
        marker = "OLD_AI_REPORT_BACKGROUND_ONLY_5e4c"

        def first(messages, _tools):
            selected = json.loads(messages[1]["content"])["selected_evidence"]
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected[0]["role"], "background")
            return None, [call("read-old-report", "read_evidence",
                               {"evidence_id": selected[0]["evidence_id"]})]

        def forbidden_second(_messages, _tools):
            self.fail("已删除的旧 AI 报告仍被带入下一轮模型调用")

        provider = ScriptProvider([first, forbidden_second])
        runtime, service, saved, _ = self.make_runtime(
            provider, "尊界V800刹车事件还能得出什么结论？",
            selected_report_text=marker + " 这只是旧模型推论，未经本次核查。")
        file_id = saved["context_selection"]["file_ids"][0]
        real_execute = runtime.execute

        def delete_after_authorized_read(name, args, call_id, seed=False):
            result = real_execute(name, args, call_id, seed=seed)
            if name == "read_evidence":
                self.assertIn(marker, result["result"]["text"])
                self.assertEqual(runtime.visible, {})
                service.repository.delete_file(self.owner, saved["session_id"], file_id)
            return result

        runtime.execute = delete_after_authorized_read
        with self.assertRaises(GatewayError) as caught:
            runtime.run_research()
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("所选研究文件已移除", str(caught.exception))
        self.assertEqual(len(provider.turns), 1)
        self.assertEqual(service.repository.list_files(self.owner, saved["session_id"]), [])

    def test_late_response_is_aborted_and_its_tokens_are_not_used(self):
        timers = []

        class ManualTimer:
            daemon = True

            def __init__(self, _seconds, callback):
                self.callback = callback
                timers.append(self)

            def start(self):
                pass

            def cancel(self):
                pass

        class Response:
            aborted = False

            def abort(self):
                self.aborted = True

        class LateProvider:
            model = "late-events"

            def __init__(self):
                self.response = Response()
                self.emitted_after_first = False

            def events(self, messages, model, stopped, on_response, *, tools=None, **_):
                timers[-1].callback()
                on_response(self.response)
                yield {"type": "token", "text": "LATE_TOKEN_MUST_NOT_ENTER_LEDGER"}
                self.emitted_after_first = True
                yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}

        provider = LateProvider()
        runtime, service, saved, _ = self.make_runtime(provider, "尊界V800刹车事件")
        with patch("backend.app.modules.explore.research_runtime.threading.Timer", ManualTimer):
            result = runtime.run_research()
        self.assertTrue(provider.response.aborted)
        self.assertFalse(provider.emitted_after_first)
        self.assertEqual(result["stop_reason"], "writing_time_reserved")
        self.assertEqual(result["retrieval"]["model_trace"][0]["status"], "timeout")
        self.assertEqual(result["retrieval"]["model_trace"][0]["output_chars"], 0)
        stored = service.repository.get_run(self.owner, saved["id"])
        self.assertNotIn("LATE_TOKEN_MUST_NOT_ENTER_LEDGER",
                         json.dumps(stored["research_brief"], ensure_ascii=False))

    def test_remaining_time_reserves_a_tool_free_final_ledger_turn(self):
        def final(messages, tools):
            self.assertIsNone(tools)
            self.assertIn("工具预算结束", messages[-1]["content"])
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        provider = ScriptProvider([final])
        runtime, _service, _saved, _ = self.make_runtime(provider, "尊界V800刹车事件")
        runtime.research_deadline = time.monotonic() + 2
        result = runtime.run_research()
        self.assertEqual(result["budget"]["model_rounds"], 1)
        self.assertEqual(result["budget"]["tool_calls"], 0)
        self.assertEqual(result["retrieval"]["model_trace"][0]["stage"], "ledger")
        self.assertEqual(result["stop_reason"], "declared_gaps")

    def test_fifty_one_seconds_remaining_reserves_the_final_ledger(self):
        def final(messages, tools):
            self.assertIsNone(tools)
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([final]), "西南财经大学后续研究方向")
        runtime.started = time.monotonic() - 69
        runtime.research_deadline = time.monotonic() + 51
        result = runtime.run_research()
        self.assertEqual(result["budget"]["model_rounds"], 1)
        self.assertEqual(result["retrieval"]["model_trace"][0]["stage"], "ledger")

    def test_clipped_header_timeout_before_timer_preserves_read_sources(self):
        self.search.title = "西南财经大学金融人才培养基地建设信息"
        self.search.snippet = "西南财经大学发布国家卓越金融人才培养基地建设信息。"
        self.search.url = "https://public.example.test/swufe-finance"
        self.page.text = "西南财经大学发布国家卓越金融人才培养基地建设信息。"

        def search(_messages, tools):
            self.assertIsNotNone(tools)
            return None, [call("search-swufe", "web_search",
                               {"query": "西南财经大学国家卓越金融人才培养基地"})]

        def read(messages, _tools):
            self.assertEqual(messages[-2]["tool_call_id"], "search-swufe")
            return None, [call("read-swufe", "fetch_public_page",
                               {"url": self.search.url})]

        provider = LedgerTimeoutProvider([search, read])
        runtime, service, saved, _ = self.make_runtime(
            provider, "西南财经大学金融人才培养基地后续研究")
        self.use_budget_clipped_provider(service)
        execute = runtime.execute

        def finish_read(name, args, call_id, seed=False):
            result = execute(name, args, call_id, seed=seed)
            if name == "fetch_public_page":
                runtime.research_deadline = time.monotonic() + 1.5
            return result

        runtime.execute = finish_read
        result = runtime.run_research()
        self.assertEqual(result["stop_reason"], "ledger_timeout")
        self.assertEqual(result["brief"]["answer_status"], "unanswered")
        self.assertEqual(result["brief"]["claims"], [])
        self.assertGreaterEqual(len(result["sources"]), 2)
        self.assertEqual([item["status"] for item in result["retrieval"]["tool_trace"]],
                         ["complete", "complete"])
        final_record = result["retrieval"]["model_trace"][-1]
        self.assertEqual((final_record["stage"], final_record["status"],
                          final_record["error_code"]),
                         ("ledger", "timeout", "model_header_timeout"))
        stored = service.repository.get_run(self.owner, saved["id"])
        self.assertEqual(stored["research_stop_reason"], "ledger_timeout")
        self.assertEqual(stored["research_brief"]["claims"], [])
        self.assertGreaterEqual(len(stored["retrieval"]["tool_trace"]), 2)

    def test_ledger_timeout_keeps_already_traceable_claim(self):
        self.search.title = "西南财经大学金融人才培养基地建设信息"
        self.search.snippet = "西南财经大学发布国家卓越金融人才培养基地建设信息。"
        self.search.url = "https://public.example.test/swufe-finance"
        self.page.text = self.search.snippet

        def search(_messages, _tools):
            return None, [call("search-claim", "web_search",
                               {"query": "西南财经大学金融人才培养基地"})]

        def read_with_claim(messages, _tools):
            source = json.loads(messages[-2]["content"])["evidence"][0]
            task = json.loads(messages[1]["content"])["research_task"]
            claim = {"id": "C1", "text": "检索摘要提及基地建设信息", "type": "sourced_statement",
                     "supports": [{"evidence_id": source["evidence_id"],
                                   "quote": "国家卓越金融人才培养基地建设信息"}]}
            return brief(task["subquestions"], claim), [
                call("read-claim", "fetch_public_page",
                     {"url": self.search.url})]

        provider = LedgerTimeoutProvider([search, read_with_claim])
        runtime, service, saved, _ = self.make_runtime(
            provider, "西南财经大学金融人才培养基地后续研究")
        self.use_budget_clipped_provider(service)
        execute = runtime.execute

        def finish_read(name, args, call_id, seed=False):
            result = execute(name, args, call_id, seed=seed)
            if name == "fetch_public_page":
                runtime.research_deadline = time.monotonic() + 1.5
            return result

        runtime.execute = finish_read
        result = runtime.run_research()
        self.assertEqual(result["stop_reason"], "ledger_timeout")
        self.assertEqual(result["brief"]["claims"][0]["support_status"], "traceable")
        self.assertEqual(result["brief"]["answer_status"], "answered")
        stored = service.repository.get_run(self.owner, saved["id"])
        self.assertEqual(stored["research_brief"]["claims"][0]["support_status"],
                         "traceable")

    def test_ledger_timeout_never_overrides_cancel_or_deleted_run(self):
        for action in ("cancel", "delete"):
            with self.subTest(action=action):
                control_ref = {}

                def before_error():
                    runtime, service, saved, control = control_ref["runtime"]
                    if action == "cancel":
                        control["stop"].set()
                    else:
                        service.repository.delete_session(self.owner, saved["session_id"])

                provider = LedgerTimeoutProvider([], before_error=before_error)
                runtime, service, saved, control = self.make_runtime(
                    provider, "西南财经大学后续研究")
                control_ref["runtime"] = (runtime, service, saved, control)
                self.use_budget_clipped_provider(service)
                runtime.limits["model_rounds"] = 1
                runtime.research_deadline = time.monotonic() + 45
                with self.assertRaises(GatewayError) as caught:
                    runtime.run_research()
                self.assertIn(caught.exception.status, {404, 409})
                self.assertEqual(len(provider.turns), 1)
                self.assertFalse(control.get("model_retries"))

    def test_transient_retry_reuses_same_round_without_duplicate_tool_calls(self):
        class FailAfterPartialOutput:
            model = "scripted-retry"

            def __init__(self):
                self.turns = []

            def events(self, messages, model, stopped, on_response, *, tools=None, **_):
                self.turns.append(copy.deepcopy(messages))
                index = len(self.turns)
                if index == 1:
                    yield {"type": "turn_end", "tool_calls": [
                        call("first-search", "web_search",
                             {"query": "尊界V800刹车踏板支架断裂"})]}
                elif index == 2:
                    yield {"type": "token", "text": "FAILED_PARTIAL_OUTPUT_MUST_BE_DISCARDED"}
                    yield {"type": "turn_end", "tool_calls": [
                        call("discarded-fetch", "fetch_public_page",
                             {"url": "https://public.example.test/v800-incident"})]}
                    raise GatewayError("模型连接中断", 504, "model_stream_timeout")
                elif index == 3:
                    yield {"type": "turn_end", "tool_calls": [
                        call("retried-fetch", "fetch_public_page",
                             {"url": "https://public.example.test/v800-incident"})]}
                else:
                    task = json.loads(messages[1]["content"])["research_task"]
                    yield {"type": "token", "text": brief(task["subquestions"])}
                    yield {"type": "turn_end", "tool_calls": []}

        provider = FailAfterPartialOutput()
        runtime, service, saved, _ = self.make_runtime(
            provider, "尊界V800刹车踏板支架断裂处理情况")
        result = runtime.run_research()
        self.assertEqual(provider.turns[1], provider.turns[2])
        self.assertEqual(result["budget"]["model_rounds"], 3)
        self.assertEqual(result["budget"]["model_retries"], 1)
        self.assertEqual(result["budget"]["tool_calls"], 2)
        self.assertEqual([trace["call_id"] for trace in result["retrieval"]["tool_trace"]],
                         ["first-search", "retried-fetch"])
        self.assertEqual([trace["attempt"] for trace in result["retrieval"]["model_trace"]],
                         [1, 1, 2, 1])
        self.assertEqual(result["retrieval"]["model_trace"][1]["status"], "timeout")
        self.assertEqual(result["retrieval"]["model_trace"][1]["tool_call_ids"], [])
        self.assertEqual(len(self.search.calls), 1)
        self.assertEqual(len(self.page.calls), 1)
        stored = service.repository.get_run(self.owner, saved["id"])
        self.assertEqual(stored["research_budget"]["model_retries"], 1)
        self.assertNotIn("FAILED_PARTIAL_OUTPUT_MUST_BE_DISCARDED",
                         json.dumps(stored["research_brief"], ensure_ascii=False))

    def test_retry_budget_is_shared_and_does_not_attempt_a_third_request(self):
        provider = LedgerTimeoutProvider([])
        runtime, service, saved, control = self.make_runtime(
            provider, "西南财经大学后续研究")
        runtime.limits["model_rounds"] = 1
        runtime.research_deadline = time.monotonic() + 45
        with self.assertRaises(GatewayError) as caught:
            runtime.run_research()
        self.assertEqual(caught.exception.code, "model_header_timeout")
        self.assertEqual(len(provider.turns), 2)
        self.assertEqual(len(control["model_retries"]), 1)
        self.assertEqual(runtime.rounds, 1)
        self.assertEqual([row["attempt"] for row in runtime.retrieval["model_trace"]],
                         [1, 2])
        stored = service.repository.get_run(self.owner, saved["id"])
        self.assertEqual(stored["research_budget"]["model_retries"], 1)

    def test_unclipped_provider_or_non_timeout_error_still_fails(self):
        for clipped, remaining_seconds, error, expected_status in (
            (False, 1.5, GatewayError("模型响应头超时", 504,
                                      "model_header_timeout"), "timeout"),
            (True, 20, GatewayError("远离预算截止的模型响应头超时", 504,
                                    "model_header_timeout"), "timeout"),
            (True, 1.5, GatewayError("模型服务认证失败", 502,
                                     "model_auth_failed"), "error"),
            (True, 1.5, GatewayError("账本格式无效", 502,
                                     "model_output_invalid"), "error"),
            (True, 1.5, GatewayError("模型 HTTP 403", 403,
                                     "model_http_forbidden"), "error"),
        ):
            with self.subTest(clipped=clipped, remaining=remaining_seconds,
                              error_code=error.code):
                provider = LedgerTimeoutProvider([], error=error)
                runtime, service, _saved, _ = self.make_runtime(
                    provider, "西南财经大学后续研究")
                if clipped:
                    self.use_budget_clipped_provider(service)
                runtime.limits["model_rounds"] = 1
                runtime.research_deadline = time.monotonic() + remaining_seconds
                with self.assertRaises(GatewayError) as caught:
                    runtime.run_research()
                self.assertIs(caught.exception, error)
                trace = runtime.retrieval["model_trace"][-1]
                self.assertEqual(trace["error_code"], error.code)
                self.assertEqual(trace["status"], expected_status)

    def test_model_cannot_append_private_text_to_authorized_fetch_url(self):
        original = "https://public.example.test/article"
        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([]), original + " 请读原文")
        result = runtime.execute("fetch_public_page",
            {"url": original + "?selected_file_excerpt=private"}, "exfiltration-attempt")
        self.assertEqual(result["status"], 403)
        self.assertEqual(self.page.calls, [])

    def test_two_questions_need_two_coverage_rows(self):
        task = build_research_task({"module": "relation",
            "question": "华为官方是否已回应尊界V800刹车事件？合同责任边界如何约定？"})
        self.assertEqual(len(task["subquestions"]), 2)
        checked = validate_brief({"coverage": [{"question_id": "Q1", "status": "answered", "claim_ids": []}],
                                  "claims": [], "done": True}, task, {})
        self.assertEqual([x["status"] for x in checked["coverage"]], ["unanswered", "unanswered"])
        self.assertEqual(checked["answer_status"], "unanswered")

    def test_malformed_evidence_and_conflict_refs_fail_closed(self):
        task = build_research_task({"question": "尊界V800事件责任"})
        base_claim = {"id": "C1", "text": "某方提出一种说法", "type": "sourced_statement"}
        bad_support = {"coverage": [{"question_id": "Q1", "status": "answered", "claim_ids": ["C1"]}],
                       "claims": [{**base_claim, "supports": [{"evidence_id": [], "quote": "无法使用的片段"}]}],
                       "conflicts": [], "done": True}
        bad_conflict = {"coverage": [], "claims": [base_claim],
                        "conflicts": [{"claim_ids": [{"id": "C1"}, "C1"], "description": "畸形引用"}],
                        "done": True}
        for raw in (bad_support, bad_conflict):
            with self.subTest(raw=raw):
                try:
                    checked = validate_brief(raw, task, {})
                except GatewayError:
                    continue
                self.assertNotEqual(checked["answer_status"], "answered")
                self.assertEqual(checked["conflicts"], [])

    def test_non_finite_json_and_tool_arguments_do_not_escape_as_python_errors(self):
        with self.assertRaises(GatewayError):
            parse_json_object('{"coverage":[],"claims":[NaN],"done":true}')

        def malformed(_messages, _tools):
            return None, [{"id": "nan-1", "type": "function",
                           "function": {"name": "web_search", "arguments": '{"query":NaN}'}}]

        def final(messages, _tools):
            self.assertEqual(messages[-2]["tool_call_id"], "nan-1")
            result = json.loads(messages[-2]["content"])
            self.assertIn("error", result)
            return brief(build_research_task({"question": "尊界V800刹车"})["subquestions"]), []

        runtime, _service, _saved, _ = self.make_runtime(
            ScriptProvider([malformed, final]), "尊界V800刹车")
        result = runtime.run_research()
        self.assertEqual(result["brief"]["answer_status"], "unanswered")
        self.assertEqual(self.search.calls, [])

    def test_cancel_and_delete_prevent_late_material_registration(self):
        runtime, service, saved, control = self.make_runtime(
            ScriptProvider([]), "尊界V800刹车踏板支架断裂")
        control["stop"].set()
        with self.assertRaises(GatewayError):
            runtime.execute("web_search", {"query": "尊界V800刹车"}, "cancelled")
        self.assertEqual(self.search.calls, [])
        control["stop"].clear()
        self.search.started = threading.Event()
        self.search.release = threading.Event()
        errors = []

        def work():
            try:
                runtime.execute("web_search", {"query": "尊界V800刹车"}, "late")
            except GatewayError as exc:
                errors.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        self.assertTrue(self.search.started.wait(2))
        service.repository.delete_session(self.owner, saved["session_id"])
        self.search.release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        with self.assertRaises(GatewayError):
            service.repository.get_run(self.owner, saved["id"])


class ExploreServiceEventsTests(unittest.TestCase):
    """Exercise the production ExploreService path with a scripted events provider."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-service-events-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
                               "AGENT_RUNTIME_DIR": str(root / "runtime"),
                               "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False,
                               "EXPLORE_RUN_TIMEOUT_SECONDS": 20})
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={
            "email": "research-events@example.test", "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.owner = registered.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.search = SearchFixture()
        self.service.search_tools = ToolRegistry((self.search.tool(),))

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def submit_and_wait(self, provider, question):
        self.service.provider = provider
        session = self.client.post("/desktop/api/explore/sessions", json={"title": "研究循环"})
        self.assertEqual(session.status_code, 201, session.json)
        session_id = session.json["session"]["id"]
        response = self.client.post(f"/desktop/api/explore/sessions/{session_id}/runs", json={
            "module": "event", "question": question, "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 202, response.json)
        run_id = response.json["run"]["id"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.client.get(f"/desktop/api/explore/runs/{run_id}")
            self.assertEqual(result.status_code, 200, result.json)
            run = result.json["run"]
            if run["status"] in {"succeeded", "partial", "failed"}:
                return run
            time.sleep(.01)
        self.fail("production ExploreService did not complete")

    def test_clipped_ledger_timeout_returns_partial_report_with_retrieved_source(self):
        page = PageFixture("西南财经大学相关公开资料的正文已完成读取。")
        self.service.research_limits["model_rounds"] = 1
        self.service.research_limits["write_reserve_seconds"] = 18
        ResearchRuntimeTests.use_budget_clipped_provider(self.service)
        provider = LedgerTimeoutProvider([])
        with patch("backend.app.modules.explore.research_runtime.build_public_reader_tool",
                   page.tool):
            done = self.submit_and_wait(
                provider, "https://public.example.test/swufe-finance 请研究西南财经大学")
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual(done["research_stop_reason"], "ledger_timeout")
        self.assertEqual(done["research_brief"]["answer_status"], "unanswered")
        self.assertEqual(done["report"]["answer_status"], "unanswered")
        self.assertEqual(done["report"]["sources"][0]["url"],
                         "https://public.example.test/swufe-finance")
        self.assertIn("证据归纳阶段超时", done["report"]["summary"])
        self.assertEqual(done["retrieval"]["model_trace"][-1]["error_code"],
                         "model_header_timeout")

    def test_answered_report_succeeds_even_with_explicit_method_limitations(self):
        question = "尊界V800刹车踏板支架断裂事件的官方回应是什么？"

        def search(_messages, _tools):
            return None, [call("service-search", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 官方回应"})]

        def finish(messages, _tools):
            tool = json.loads(messages[-2]["content"])
            source = tool["evidence"][0]
            task = json.loads(messages[1]["content"])["research_task"]
            claim = {"id": "C1", "text": "已有公开回应", "type": "sourced_statement",
                     "supports": [{"evidence_id": source["evidence_id"], "quote": "官方已发布说明"}]}
            return brief(task["subquestions"], claim), []

        provider = ServiceReportProvider([search, finish])
        done = self.submit_and_wait(provider, question)
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        report = done["report"]
        self.assertEqual(report["answer_status"], "answered")
        self.assertEqual(report["report_variant"], "research")
        self.assertFalse(report["limitations"])
        self.assertEqual(report['report_format'], 'event_brief@1')
        self.assertEqual(report['event_overview'], '')
        self.assertEqual(report['background'], '')
        self.assertNotIn('## 重要不确定性', report['markdown'])
        self.assertNotIn('## 信息缺口与限制', report['markdown'])
        self.assertTrue(report["method_notes"])
        self.assertEqual([x["name"] for x in report["retrieval"]["tool_trace"]], ["web_search"])
        self.assertEqual(len(provider.writer_payloads), 3)
        self.assertEqual(provider.writer_payloads[0]["stage"], "claim_review")
        self.assertEqual(provider.writer_payloads[0]["research_brief"]["answer_status"], "answered")
        self.assertEqual(provider.writer_payloads[1]["research_brief"]["answer_status"], "answered")
        self.assertEqual(provider.writer_payloads[2]["stage"], "review")

    def test_first_event_brief_repairs_expanded_fields_before_saving(self):
        def search(_messages, _tools):
            return None, [call('compact-search', 'web_search',
                               {'query': '尊界V800刹车踏板支架断裂 官方回应'})]

        def finish(messages, _tools):
            source = json.loads(messages[-2]['content'])['evidence'][0]
            task = json.loads(messages[1]['content'])['research_task']
            claim = {'id': 'C1', 'text': '已有公开回应', 'type': 'sourced_statement',
                     'supports': [{'evidence_id': source['evidence_id'], 'quote': '官方已发布说明'}]}
            return brief(task['subquestions'], claim), []

        class VerboseFirstWriter(ServiceReportProvider):
            def json_tokens(self, messages, model, stopped, on_response):
                payload = json.loads(messages[1]['content'])
                output = ''.join(super().json_tokens(messages, model, stopped, on_response))
                if payload.get('stage') is None and 'repair' not in payload:
                    draft = json.loads(output)
                    draft.update(background='重复的完整背景', uncertainties=['合同条款尚未核实'],
                                 drivers=['不能在首轮自动展开的动因'])
                    output = json.dumps(draft, ensure_ascii=False)
                yield output

        provider = VerboseFirstWriter([search, finish])
        done = self.submit_and_wait(provider, '尊界V800刹车踏板支架断裂事件的官方回应是什么？')
        self.assertEqual(done['status'], 'succeeded', done.get('error'))
        report = done['report']
        self.assertEqual(report['report_format'], 'event_brief@1')
        self.assertTrue(report['draft_reviews'][0]['issues'])
        self.assertEqual(report['draft_reviews'][1]['issues'], [])
        self.assertEqual(report['background'], '')
        self.assertEqual(report['uncertainties'], [])
        self.assertEqual(report['drivers'], [])
        self.assertTrue(any('repair' in payload for payload in provider.writer_payloads))
        self.assertIn('责任条款仍有待核查', report['markdown'])
        self.assertNotIn('重复的完整背景', report['markdown'])

    def test_event_inline_coverage_requires_valid_final_review_markers(self):
        def search(_messages, _tools):
            return None, [call('inline-search', 'web_search',
                               {'query': '尊界V800刹车踏板支架断裂 官方回应'})]

        def finish(messages, _tools):
            source = json.loads(messages[-2]['content'])['evidence'][0]
            task = json.loads(messages[1]['content'])['research_task']
            claim = {'id': 'C1', 'text': '已有公开回应', 'type': 'sourced_statement',
                     'supports': [{'evidence_id': source['evidence_id'], 'quote': '官方已发布说明'}]}
            result = json.loads(brief(task['subquestions'], claim))
            for row in result['coverage']:
                row.update(status='partial', gap='尚缺合同原文', next_action='读取合同条款')
            return json.dumps(result, ensure_ascii=False), []

        for marker_mode in ('confirmed', 'missing', 'foreign'):
            with self.subTest(marker_mode=marker_mode):
                class ReviewedCoverageProvider(ServiceReportProvider):
                    def json_tokens(self, messages, model, stopped, on_response):
                        payload = json.loads(messages[1]['content'])
                        if payload.get('stage') == 'review':
                            markers = ([row['question_id'] for row in payload['research_brief']['coverage']]
                                       if marker_mode == 'confirmed' else
                                       ['foreign-Q'] if marker_mode == 'foreign' else [])
                            yield json.dumps({'issues': [], 'inline_coverage_question_ids': markers})
                            return
                        yield from super().json_tokens(messages, model, stopped, on_response)

                result = self.submit_and_wait(ReviewedCoverageProvider([search, finish]),
                                             '尊界V800刹车踏板支架断裂事件的官方回应是什么？')
                if marker_mode == 'foreign':
                    self.assertEqual(result['status'], 'failed')
                    self.assertFalse(result.get('report'))
                    continue
                self.assertEqual(result['status'], 'partial', result.get('error'))
                report = result['report']
                self.assertIn('责任条款仍有待核查', report['markdown'])
                self.assertEqual(bool(report['inline_coverage_question_ids']), marker_mode == 'confirmed')
                self.assertEqual('待补充：尚缺合同原文' in report['markdown'], marker_mode == 'missing')

    def test_invalid_extra_support_is_diagnostic_only_after_claim_review(self):
        question = "尊界V800刹车踏板支架断裂事件的官方回应是什么？"

        def search(_messages, _tools):
            return None, [call("mixed-support-search", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 官方回应"})]

        def finish(messages, _tools):
            source = json.loads(messages[-2]["content"])["evidence"][0]
            task = json.loads(messages[1]["content"])["research_task"]
            claim = {"id": "C1", "text": "已有公开回应", "type": "sourced_statement",
                     "supports": [
                         {"evidence_id": source["evidence_id"], "quote": "官方已发布说明"},
                         {"evidence_id": "invalid-stale-evidence", "quote": "无效引用不应进入写作"},
                     ]}
            return brief(task["subquestions"], claim), []

        provider = ServiceReportProvider([search, finish])
        done = self.submit_and_wait(provider, question)
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        report = done["report"]
        self.assertEqual(report["answer_status"], "answered")
        self.assertEqual(report["validation_notes"], [])
        self.assertTrue(any("C1:证据编号不在本轮实际读取范围" in note
                            for note in report["research_validation_notes"]))
        self.assertEqual([payload.get("stage", "draft") for payload in provider.writer_payloads],
                         ["claim_review", "draft", "review"])
        for payload in provider.writer_payloads[:2]:
            current = payload["research_brief"]
            self.assertEqual(current["validation_notes"], [])
            self.assertEqual(len(current["claims"]), 1)
            self.assertEqual(len(current["claims"][0]["supports"]), 1)
            self.assertNotIn("invalid-stale-evidence", json.dumps(payload, ensure_ascii=False))

    def test_review_flags_unsupported_number_then_repairs_once(self):
        question = "尊界V800刹车踏板支架断裂事件的官方回应是什么？"

        def search(_messages, _tools):
            return None, [call("service-search", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 官方回应"})]

        def finish(messages, _tools):
            source = json.loads(messages[-2]["content"])["evidence"][0]
            task = json.loads(messages[1]["content"])["research_task"]
            claim = {"id": "C1", "text": "已有公开回应", "type": "sourced_statement",
                     "supports": [{"evidence_id": source["evidence_id"], "quote": "官方已发布说明"}]}
            return brief(task["subquestions"], claim), []

        provider = ServiceReportProvider([search, finish], review_issues_once=True)
        done = self.submit_and_wait(provider, question)
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        report = done["report"]
        self.assertNotIn("500 亿元", report["summary"])
        self.assertEqual(provider.review_calls, 2)
        self.assertEqual(len(provider.writer_payloads), 5)
        self.assertEqual(len(report["draft_reviews"]), 2)
        self.assertEqual(len(report["draft_reviews"][0]["issues"]), 1)
        self.assertEqual(report["draft_reviews"][1]["issues"], [])

    def test_unanswered_report_uses_short_insufficient_evidence_variant(self):
        question = "尊界V800刹车踏板支架断裂事件的官方回应是什么？"

        def finish(messages, _tools):
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"], done=True), []

        provider = ServiceReportProvider([finish])
        done = self.submit_and_wait(provider, question)
        self.assertEqual(done["status"], "partial", done.get("error"))
        report = done["report"]
        self.assertEqual(report["answer_status"], "unanswered")
        self.assertEqual(report["report_variant"], "insufficient_evidence")
        self.assertIn("研究未完成", report["title"])
        self.assertEqual(report["event_overview"], "")
        self.assertEqual(provider.writer_payloads, [])

    def test_partial_coverage_with_only_hypothesis_and_unsupported_claim_is_unanswered(self):
        def search(_messages, _tools):
            return None, [call("unsupported-search", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 责任认定"})]

        def attempted_partial(messages, _tools):
            source = json.loads(messages[-2]["content"])["evidence"][0]
            task = json.loads(messages[1]["content"])["research_task"]
            return json.dumps({
                "coverage": [{"question_id": task["subquestions"][0]["id"],
                              "status": "partial", "claim_ids": ["C1", "C2"],
                              "gap": "仍缺责任条款原文"}],
                "claims": [
                    {"id": "C1", "text": "可能由某方承担责任", "type": "hypothesis",
                     "supports": [{"evidence_id": source["evidence_id"],
                                   "quote": "官方已发布说明"}]},
                    {"id": "C2", "text": "责任已经确认", "type": "fact",
                     "supports": [{"evidence_id": source["evidence_id"],
                                   "quote": "生产责任已经确认"}]},
                ],
                "conflicts": [], "done": True,
            }, ensure_ascii=False), []

        provider = ServiceReportProvider([search, attempted_partial])
        done = self.submit_and_wait(provider, "尊界V800刹车踏板支架断裂责任如何认定？")
        self.assertEqual(done["status"], "partial", done.get("error"))
        report = done["report"]
        self.assertEqual(report["coverage"][0]["status"], "unanswered")
        self.assertEqual(report["answer_status"], "unanswered")
        self.assertEqual(report["report_variant"], "insufficient_evidence")
        self.assertEqual({claim["id"]: claim["support_status"] for claim in report["claims"]},
                         {"C1": "traceable", "C2": "unsupported"})
        self.assertEqual(provider.writer_payloads, [])

    def test_minimum_legal_timeout_still_allows_research_tool_turn(self):
        self.assertEqual(self.service.timeout, 20)
        self.assertGreater(self.service.timeout -
                           self.service.research_limits["write_reserve_seconds"], 0)

        def search(_messages, _tools):
            return None, [call("twenty-second-search", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 官方回应"})]

        def finish(messages, _tools):
            self.assertEqual(messages[-2]["tool_call_id"], "twenty-second-search")
            task = json.loads(messages[1]["content"])["research_task"]
            return brief(task["subquestions"]), []

        provider = ServiceReportProvider([search, finish])
        done = self.submit_and_wait(provider, "尊界V800刹车踏板支架断裂事件的官方回应是什么？")
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual(done["research_budget"]["model_rounds"], 2)
        self.assertEqual(done["research_budget"]["tool_calls"], 1)
        self.assertEqual(len(self.search.calls), 1)
        self.assertNotEqual(done["research_stop_reason"], "writing_time_reserved")

    def test_deadline_provider_shortens_only_a_request_local_clone(self):
        shared = self.service.provider
        original = (shared.connect_timeout, shared.read_timeout)
        request_local = self.service._provider_for_deadline(time.monotonic() + 0.5)
        self.assertIsNot(request_local, shared)
        self.assertIs(self.service.provider, shared)
        self.assertEqual((shared.connect_timeout, shared.read_timeout), original)
        self.assertGreater(request_local.connect_timeout, 0)
        self.assertGreater(request_local.read_timeout, 0)
        self.assertLess(request_local.connect_timeout + request_local.read_timeout,
                        sum(original))
        self.assertEqual((request_local.key, request_local.model),
                         (shared.key, shared.model))

    def test_writer_repair_refuses_deleted_evidence_before_model_call(self):
        def search(_messages, _tools):
            return None, [call("writer-guard-search", "web_search",
                               {"query": "尊界V800刹车踏板支架断裂 官方回应"})]

        def finish(messages, _tools):
            source = json.loads(messages[-2]["content"])["evidence"][0]
            task = json.loads(messages[1]["content"])["research_task"]
            claim = {"id": "C1", "text": "已有公开回应", "type": "sourced_statement",
                     "supports": [{"evidence_id": source["evidence_id"], "quote": "官方已发布说明"}]}
            return brief(task["subquestions"], claim), []

        parent = self

        class DeleteOnReview(ServiceReportProvider):
            deleted_file_id = None

            def json_tokens(self, messages, model, stopped, on_response):
                payload = json.loads(messages[1]["content"])
                if payload.get("stage") == "review":
                    self.writer_payloads.append(payload)
                    session_id = parent.service.repository.list_sessions(parent.owner)[0]["id"]
                    files = parent.service.repository.list_files(parent.owner, session_id)
                    self.deleted_file_id = files[0]["id"]
                    parent.service.repository.delete_file(parent.owner, session_id,
                                                          self.deleted_file_id)
                    yield '{"issues":["直接回答需要修正来源表述"]}'
                    return
                yield from super().json_tokens(messages, model, stopped, on_response)

        provider = DeleteOnReview([search, finish])
        done = self.submit_and_wait(provider, "尊界V800刹车踏板支架断裂事件的官方回应是什么？")
        self.assertIsNotNone(provider.deleted_file_id)
        self.assertEqual(done["status"], "failed", done.get("error"))
        self.assertIn("研究材料已移除", done["error"])
        self.assertIsNone(done.get("report"))
        self.assertEqual([p.get("stage", "draft") for p in provider.writer_payloads],
                         ["claim_review", "draft", "review"])


if __name__ == "__main__":
    unittest.main()
