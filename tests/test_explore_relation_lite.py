"""The new relation Skill runs a bounded selection, news, and report lifecycle.

The Flask client, repository, registry, model, and public reader all run locally.
No external model or website is contacted.
"""

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
from backend.app.modules.explore.relation_lite_runtime import (
    _entity_search_name, _matches_question, _query,
)
from backend.app.modules.explore.repository import timestamp
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


QUESTION = "华为与江淮汽车在尊界V800制动支架事件中有什么关联？两者分别承担什么作用？"
SHARED_URL = "https://example.org/v800/shared"
SECOND_URL = "https://example.org/v800/second"
THIRD_URL = "https://example.org/v800/third"
FOURTH_URL = "https://example.org/v800/fourth"


class RelationProvider:
    model = "relation-lite-fixture"

    def __init__(self, entities=None, clarification=False):
        self.entities = entities if entities is not None else [
            {"name": "华为", "reason": "需核对合作中的产品和技术职责"},
            {"name": "江淮汽车", "reason": "需核对车辆制造和质量职责"},
        ]
        self.clarification = clarification
        self.payloads = []
        self.event_calls = 0
        self.bad_recommendations = False
        self.whitespace_recommendations = False
        self.report_error = None
        self.report_started = None
        self.report_release = None

    def events(self, messages, model, stopped, on_response, *, tools=None, **_options):
        # Selecting entities and writing the report are structured calls. The
        # old research planner must never request model tool-calling events.
        self.event_calls += 1
        raise AssertionError("relation-lite unexpectedly entered model tool-calling")
        yield  # pragma: no cover

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        self.payloads.append(payload)
        stage = payload.get("stage")
        if stage == "relation_select":
            if self.clarification:
                self.clarification = False
                value = {"needs_clarification": True,
                         "question": "这里的尊界V800是指哪一款具体车型？", "entities": []}
            else:
                value = {"needs_clarification": False, "question": "", "entities": self.entities}
        elif stage == "relation_lite_report":
            if self.report_started is not None:
                self.report_started.set()
                self.report_release.wait(3)
            if self.report_error is not None:
                raise self.report_error
            sources = payload.get("sources") or []
            refs = [row["id"] for row in sources[:2]]
            if refs:
                if "红利低波" in payload["question"]:
                    summary = "本轮新闻讨论红利低波方向的资金配置，具体个股与成因仍需核对。"
                    body = "新闻涉及红利低波方向的资金配置，但不能仅凭同日报道推断因果。"
                else:
                    summary = "本轮报道涉及尊界V800事件；公开材料尚不足以确定双方合同责任。"
                    body = "报道提及事件和相关主体。合作分工与具体法律责任仍须核对协议及官方说明。"
            else:
                summary = "本轮未取得相关新闻，无法核实尊界V800事件中的最新表态与责任划分。"
                body = "通常应区分技术参与、整车制造和合同责任；本轮没有新闻支持具体归责。"
            value = {"title": "尊界V800主体关联", "summary": summary,
                     "key_findings": [summary], "uncertainties": [], "limitations": [],
                     "sections": [{"heading": "关键关联分析", "body": body,
                                   "source_refs": refs}],
                     "entities": [item["name"] for item in self.entities[:2]],
                     "relationships": [],
                     "recommendations": [
                         {"module": "event", "question": "尊界V800制动支架事件目前有哪些已核实的进展？",
                          "reason": "先核对事件事实和时间顺序"},
                         {"module": "relation", "question": "华为与江淮汽车在尊界V800事件中的分工依据是什么？",
                          "reason": "继续厘清主体职责与证据"},
                         {"module": "history", "question": "过去类似汽车零部件事件的责任划分有何差异？",
                          "reason": "对照历史情形和适用条件"}]}
            if self.bad_recommendations:
                value["recommendations"] = value["recommendations"][:2]
            if self.whitespace_recommendations:
                for row in value["recommendations"]:
                    row["question"] = " \n " + row["question"] + " \t "
        else:
            raise AssertionError(f"unexpected model stage: {stage!r}")
        yield json.dumps(value, ensure_ascii=False)


class RelationNews:
    def __init__(self, mode="two"):
        self.mode = mode
        self.calls = []
        self.started = None
        self.release = None
        self.rows_override = None

    def tool(self, max_calls=2):
        def handler(arguments, context):
            self.calls.append(dict(arguments))
            if self.started is not None:
                self.started.set()
                self.release.wait(3)
            if self.mode == "failure":
                raise GatewayError("测试搜索失败", 502)
            if self.mode == "empty":
                return ToolResult(content={"searchedAt": timestamp()}, sources=[])
            if self.rows_override is not None:
                return ToolResult(content={"searchedAt": timestamp()},
                                  sources=self.rows_override)
            # The registry may return more than the requested count. The
            # runtime must retain only three per selected entity.
            if len(self.calls) == 1:
                rows = [
                    ("shared", "华为江淮尊界V800事件联合报道", SHARED_URL),
                    ("second", "尊界V800事件中华为江淮合作模式观察", SECOND_URL),
                    ("third", "华为尊界V800制动事件公开回应", THIRD_URL),
                    ("ignored", "尊界V800第四篇溢出报道", "https://example.org/ignored-1"),
                ]
            else:
                rows = [
                    ("shared-again", "华为江淮尊界V800事件联合报道", SHARED_URL + "#details"),
                    ("same-title", "尊界V800事件中华为江淮合作模式观察", "https://example.org/duplicate-title"),
                    ("fourth", "江淮汽车尊界V800制动事件回应", FOURTH_URL),
                    ("ignored", "尊界V800第四篇溢出报道", "https://example.org/ignored-2"),
                ]
            return ToolResult(content={"searchedAt": timestamp()}, sources=[
                {"id": ident, "title": title, "url": url,
                 "snippet": "尊界V800制动支架事件涉及华为与江淮汽车，职责仍待核对。",
                 "publishedAt": "2026-10-12T12:00:00+08:00"} for ident, title, url in rows])

        return ToolSpec("web_search", "本地搜索测试", {"type": "object"},
                        handler, max_calls=max_calls)


class RelationReader:
    def __init__(self):
        self.calls = []

    def tool(self, _dns_mode):
        def handler(arguments, context):
            self.calls.append(dict(arguments))
            return ToolResult(content={"status": "ok", "text":
                              "公开报道说明尊界V800事件涉及华为和江淮，具体合同责任仍待核对。",
                              "title": "公开报道", "publisher": "测试新闻",
                              "final_url": arguments["url"]})

        # A tighter registered cap must win over the Skill's six-page ceiling.
        return ToolSpec("fetch_public_page", "本地公开网页测试", {"type": "object"},
                        handler, max_calls=2)


def synthetic_news(ident, title, snippet, *, url=None, published_at=None):
    return {"id": ident, "title": title, "snippet": snippet,
            "url": url or f"https://example.org/synthetic/{ident}",
            "publishedAt": published_at or timestamp()}


class RelationLiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-relation-lite-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "test-only",
            "IMPORT_LEGACY_HISTORY": False, "EXPLORE_RELATION_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={
            "email": "relation-lite@example.test", "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.owner = registered.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.provider = RelationProvider()
        self.news = RelationNews()
        self.reader = RelationReader()
        self.service.provider = self.provider
        self.service.search_tools = ToolRegistry((self.news.tool(),))
        session = self.client.post("/desktop/api/explore/sessions", json={"title": "关联测试"})
        self.assertEqual(session.status_code, 201, session.json)
        self.session_id = session.json["session"]["id"]

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def submit(self, question=QUESTION):
        response = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs",
            json={"module": "relation", "question": question,
                  "idempotency_key": str(uuid4())})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"]["id"]

    def wait(self, run_id, statuses=None):
        statuses = statuses or {"succeeded", "partial", "failed"}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(f"/desktop/api/explore/runs/{run_id}")
            self.assertEqual(response.status_code, 200, response.json)
            run = response.json["run"]
            if run["status"] in statuses:
                return run
            time.sleep(.01)
        self.fail("relation-lite run did not settle")

    def run_with_old_path_blocked(self, *, question=QUESTION):
        with patch("backend.app.modules.explore.research_runtime.ResearchRuntime.__init__",
                   side_effect=AssertionError("old ResearchRuntime called")), \
             patch("backend.app.modules.explore.claim_review.apply_claim_review",
                   side_effect=AssertionError("old claim review called")), \
             patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit(question)
            done = self.wait(run_id)
        return done

    def test_two_entities_bounded_search_deduplicated_sources_and_one_report(self):
        # A third model suggestion cannot expand this run beyond two searches.
        self.provider.entities.append({"name": "懂车帝", "reason": "测试机构"})
        done = self.run_with_old_path_blocked()
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["workflow_version"], "relation-lite@2")
        report = done["report"]
        self.assertEqual(report["workflow_version"], "relation-lite@2")
        self.assertEqual(report["report_format"], "relation_lite@2")
        self.assertEqual(done["next_step_state"], "pending")
        self.assertEqual([row["module"] for row in report["recommendations"]],
                         ["event", "relation", "history"])
        self.assertEqual(len({row["question"] for row in report["recommendations"]}), 3)
        self.assertEqual(done["input_file_id"], report["input_file_id"])
        pool = self.service.repository.list_files(self.owner, self.session_id)
        self.assertEqual(len(pool), len(report["sources"]) + 2)
        input_file = self.service.repository.get_file(
            self.owner, self.session_id, report["input_file_id"])
        report_file = self.service.repository.get_file(
            self.owner, self.session_id, report["file_id"])
        self.assertIn(QUESTION, input_file["content"])
        self.assertIn("华为", report_file["content"])
        self.assertEqual([row.get("stage") for row in self.provider.payloads],
                         ["relation_select", "relation_lite_report"])
        self.assertEqual(self.provider.event_calls, 0)
        self.assertEqual(len(self.news.calls), 2)
        self.assertTrue(all(call["count"] == 3 for call in self.news.calls))
        self.assertIn("华为", self.news.calls[0]["query"])
        self.assertIn("江淮汽车", self.news.calls[1]["query"])
        self.assertTrue(all(len(call["query"]) <= 200 for call in self.news.calls))
        urls = [source["url"].split("#", 1)[0] for source in report["sources"]]
        self.assertEqual(set(urls), {SHARED_URL, SECOND_URL, THIRD_URL, FOURTH_URL})
        self.assertEqual(len(urls), len(set(urls)))
        titles = [source["title"] for source in report["sources"]]
        self.assertEqual(len(titles), len(set(titles)))
        self.assertLessEqual(len(report["sources"]), 6)
        self.assertNotIn("https://example.org/ignored-1", urls)
        self.assertNotIn("https://example.org/ignored-2", urls)
        self.assertEqual(len(self.reader.calls), 2)
        self.assertEqual(len(self.reader.calls), len({call["url"] for call in self.reader.calls}))
        self.assertTrue(all(call["url"].split("#", 1)[0] in urls
                            for call in self.reader.calls))
        self.assertTrue(all(call["max_chars"] == 4000 for call in self.reader.calls))
        self.assertEqual(sum(source["content_kind"] == "extracted_page_text"
                             for source in report["sources"]), 2)
        self.assertEqual(report["retrieval"]["selection"]["truncated_count"], 1)
        search_trace = [row for row in report["retrieval"]["tool_trace"]
                        if row["name"] == "web_search"]
        self.assertEqual(len(search_trace), 2)
        # Both the repeated URL and the repeated title in entity two must
        # reuse existing evidence instead of making additional source files.
        self.assertEqual([row["reused_count"] for row in search_trace], [0, 2])

    def test_duplicate_decorated_entities_share_one_search_core(self):
        self.provider.entities = [
            {"name": "红利低波板块（银行、公用事业等方向）", "reason": "核对板块"},
            {"name": "红利低波概念", "reason": "核对概念"},
        ]
        self.news.rows_override = [synthetic_news(
            "sector", "红利低波方向获资金关注", "报道讨论高股息方向的资金配置。")]
        done = self.run_with_old_path_blocked(
            question="近期红利低波方向的资金配置与市场风格有什么关联？")
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual([row["name"] for row in done["selected_entities"]],
                         ["红利低波板块（银行、公用事业等方向）"])
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 3)
        self.assertEqual(len(done["report"]["retrieval"]["searches"]), 1)
        self.assertEqual(len(done["report"]["sources"]), 1)
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 3)

    def test_long_entity_explanation_prioritizes_core_without_dropping_background(self):
        # Reproduces the shape of a real search miss with invented names/news.
        # A sector label plus a parenthesized example list must not become an
        # exact-match requirement for an otherwise relevant provider result.
        long_name = "红利低波板块（银行、公用事业等防御方向）"
        self.provider.entities = [{"name": long_name, "reason": "核对防御性资金配置"}]
        self.news.rows_override = [
            synthetic_news("sector", "红利低波方向获资金关注",
                           "报道讨论银行与公用事业等方向的资金配置。"),
            synthetic_news("background", "高股息资产配置观察", "银行与公用事业吸引防御型资金。"),
            synthetic_news("unsafe", "红利低波基金报道", "红利低波方向的新产品。",
                           url="javascript:alert(1)"),
        ]
        done = self.run_with_old_path_blocked(
            question="近期红利低波方向的资金配置与市场风格有什么关联？")
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["selected_entities"][0]["name"], long_name)
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 3)
        self.assertEqual(self.news.calls[0]["freshness"], "oneWeek")
        self.assertTrue(self.news.calls[0]["query"].startswith("红利低波 "))
        self.assertNotIn("银行、公用事业", self.news.calls[0]["query"])
        report = done["report"]
        self.assertEqual([row["title"] for row in report["sources"]],
                         ["红利低波方向获资金关注", "高股息资产配置观察"])
        self.assertEqual([row["module"] for row in report["recommendations"]],
                         ["event", "relation", "history"])
        self.assertIn("红利低波", report["summary"])
        trace = [row for row in report["retrieval"]["tool_trace"]
                 if row["name"] == "web_search"]
        self.assertEqual(len(trace), 1)
        self.assertEqual(trace[0]["entity"], long_name)
        self.assertEqual(trace[0]["entity_search_name"], "红利低波")
        self.assertEqual((trace[0]["returned_count"], trace[0]["examined_count"],
                          trace[0]["filtered_count"]), (3, 3, 1))
        self.assertEqual(trace[0]["filter_reasons"],
                         {"invalid_source": 1})
        self.assertEqual(trace[0]["status"], "complete")
        self.assertIsNone(trace[0].get("empty_reason"))
        self.assertEqual(report["retrieval"]["searches"][0]["source_ids"], ["S1", "S2"])
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), 4)
        self.assertEqual(len(self.provider.payloads[-1]["sources"]), 2)
        source_file = self.service.repository.get_file(
            self.owner, self.session_id, report["sources"][0]["file_id"])
        self.assertIn("红利低波方向获资金关注", source_file["content"])
        self.assertNotIn("高股息资产配置观察", source_file["content"])

    def test_search_trace_distinguishes_filtered_results_from_provider_empty(self):
        self.provider.entities = [{"name": "江淮汽车", "reason": "核对车型与事件"}]
        self.news.rows_override = [
            synthetic_news("wrong-model-suffix", "江淮汽车尊界V8000制动支架公开回应",
                           "报道明确涉及其他车型。"),
            synthetic_news("wrong-model", "江淮汽车尊界V700制动支架公开回应",
                           "报道提及另一车型。"),
            synthetic_news("unsafe", "江淮汽车尊界V800销售配置报道",
                           "报道只涉及销售渠道。", url="javascript:alert(1)"),
        ]
        filtered = self.run_with_old_path_blocked()
        self.assertEqual(filtered["status"], "partial", filtered.get("error"))
        self.assertEqual(filtered["report"]["sources"], [])
        filtered_trace = [row for row in filtered["report"]["retrieval"]["tool_trace"]
                          if row["name"] == "web_search"]
        self.assertEqual(len(filtered_trace), 1)
        self.assertEqual((filtered_trace[0]["returned_count"],
                          filtered_trace[0]["examined_count"],
                          filtered_trace[0]["filtered_count"]), (3, 3, 3))
        self.assertEqual(filtered_trace[0]["filter_reasons"],
                         {"identity_conflict": 2, "invalid_source": 1})
        self.assertEqual(filtered_trace[0]["empty_reason"], "filtered_out")
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 3)

        self.news.calls.clear()
        self.news.rows_override = [synthetic_news(
            "future", "江淮汽车尊界V800制动支架事件回应", "这条日期晚于本轮时间。",
            published_at="2099-01-01T00:00:00+08:00")]
        future = self.run_with_old_path_blocked()
        self.assertEqual(future["status"], "partial", future.get("error"))
        future_trace = [row for row in future["report"]["retrieval"]["tool_trace"]
                        if row["name"] == "web_search"]
        self.assertEqual(len(future_trace), 1)
        self.assertEqual(future_trace[0]["filter_reasons"], {"future_date": 1})
        self.assertEqual(future_trace[0]["empty_reason"], "filtered_out")
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 3)

        self.news.calls.clear()
        self.news.rows_override = []
        empty = self.run_with_old_path_blocked()
        self.assertEqual(empty["status"], "partial", empty.get("error"))
        empty_trace = [row for row in empty["report"]["retrieval"]["tool_trace"]
                       if row["name"] == "web_search"]
        self.assertEqual(len(empty_trace), 1)
        self.assertEqual((empty_trace[0]["returned_count"],
                          empty_trace[0]["examined_count"],
                          empty_trace[0]["filtered_count"]), (0, 0, 0))
        self.assertEqual(empty_trace[0]["filter_reasons"], {})
        self.assertEqual(empty_trace[0]["empty_reason"], "provider_empty")
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 3)

    def test_entity_core_preserves_identity_but_missing_event_words_is_not_rejection(self):
        self.assertEqual(_entity_search_name("红利低波板块（银行、公用事业等方向）"),
                         "红利低波")
        self.assertEqual(_entity_search_name("公用事业行业"), "公用事业")
        self.assertEqual(_entity_search_name("招商银行(香港)"), "招商银行(香港)")
        self.assertEqual(_entity_search_name("尊界V800"), "尊界V800")
        self.assertEqual(_entity_search_name("尊界（V800）"), "尊界（V800）")
        query = _query("红利低波板块（银行、公用事业等方向）",
                       "近期资金流向有哪些变化？", timestamp())
        self.assertTrue(query.startswith("红利低波 "))
        self.assertNotIn("银行、公用事业", query)
        self.assertTrue(_matches_question("江淮汽车", QUESTION,
            synthetic_news("right", "江淮汽车尊界V800制动支架事件回应", "相关细节待核对。")))
        self.assertFalse(_matches_question("江淮汽车", QUESTION,
            synthetic_news("model", "江淮汽车尊界V700制动支架事件回应", "相关细节待核对。")))
        self.assertTrue(_matches_question("江淮汽车", QUESTION,
            synthetic_news("event", "江淮汽车尊界V800销量报道", "市场表现相关。")))
        self.assertTrue(_matches_question("江淮汽车", QUESTION,
            synthetic_news("background", "汽车零部件质量管理", "涉及检测与召回流程。")))

    def test_model_token_boundaries_reject_v8000_and_m70(self):
        for model, longer in (("V800", "V8000"), ("M7", "M70")):
            with self.subTest(model=model):
                question = f"江淮汽车{model}制动支架事件有什么进展？"
                matched = synthetic_news(
                    "right-" + model, f"江淮汽车{model}制动支架事件回应", "进展待核对。")
                wrong = synthetic_news(
                    "wrong-" + model, f"江淮汽车{longer}制动支架事件回应", "进展待核对。")
                self.assertTrue(_matches_question("江淮汽车", question, matched))
                self.assertFalse(_matches_question("江淮汽车", question, wrong))
                # A product-model entity must obey the same token boundary,
                # even when its shorter code is a substring of the result.
                self.assertTrue(_matches_question(f"尊界{model}", question,
                    synthetic_news("entity-right-" + model,
                                   f"江淮汽车尊界{model}制动支架事件回应", "进展待核对。")))
                self.assertFalse(_matches_question(f"尊界{model}", question,
                    synthetic_news("entity-wrong-" + model,
                                   f"江淮汽车尊界{longer}制动支架事件回应", "进展待核对。")))

    def test_one_entity_with_no_news_or_search_failure_finishes_partial(self):
        self.provider.entities = [{"name": "华为", "reason": "唯一必要实体"}]
        for mode in ("empty", "failure"):
            with self.subTest(mode=mode):
                self.news.mode = mode
                self.news.calls.clear()
                self.provider.payloads.clear()
                done = self.run_with_old_path_blocked()
                self.assertEqual(done["status"], "partial", done.get("error"))
                self.assertEqual(len(self.news.calls), 1)
                self.assertEqual(self.news.calls[0]["count"], 3)
                self.assertEqual(self.reader.calls, [])
                self.assertEqual([row.get("stage") for row in self.provider.payloads],
                                 ["relation_select", "relation_lite_report"])
                self.assertEqual(done["report"]["sources"], [])
                self.assertIn("未取得", done["report"]["summary"])

    def test_registered_search_limit_skips_second_entity_without_extra_call(self):
        self.service.search_tools = ToolRegistry((self.news.tool(max_calls=1),))
        done = self.run_with_old_path_blocked()
        self.assertIn(done["status"], {"succeeded", "partial"}, done.get("error"))
        self.assertEqual(len(self.news.calls), 1)
        self.assertEqual(self.news.calls[0]["count"], 3)
        searches = done["report"]["retrieval"]["searches"]
        self.assertEqual(len(searches), 2)
        self.assertEqual(searches[1]["status"], "budget_skipped")
        self.assertEqual([row.get("stage") for row in self.provider.payloads],
                         ["relation_select", "relation_lite_report"])

    def test_cancelled_search_cannot_publish_report_and_other_account_is_isolated(self):
        self.news.started = threading.Event()
        self.news.release = threading.Event()
        self.provider.entities = [{"name": "华为", "reason": "核对事件"}]
        with patch("backend.app.modules.explore.research_runtime.ResearchRuntime.__init__",
                   side_effect=AssertionError("old ResearchRuntime called")), \
             patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit()
            self.assertTrue(self.news.started.wait(3), "search was not started")
            other = self.app.test_client()
            registered = other.post("/desktop/api/register", json={
                "email": "another-relation@example.test", "password": "long-password"})
            self.assertEqual(registered.status_code, 200, registered.json)
            self.assertEqual(other.get(f"/desktop/api/explore/runs/{run_id}").status_code, 404)
            cancelled = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.json)
            self.news.release.set()
            done = self.wait(run_id, {"cancelled", "failed", "succeeded", "partial"})
            self.assertEqual(done["status"], "cancelled")
            self.assertIsNone(done["report"])
            self.assertNotIn("relation_lite_report", [row.get("stage")
                            for row in self.provider.payloads])

    def test_ambiguous_entity_waits_for_clarification_then_resumes_new_flow(self):
        self.provider.clarification = True
        resolved = "请分析华为与江淮汽车在尊界V800事件中的职责和关联。"
        with patch("backend.app.modules.explore.research_runtime.ResearchRuntime.__init__",
                   side_effect=AssertionError("old ResearchRuntime called")), \
             patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit("这个 V800 是谁负责的？")
            waiting = self.wait(run_id, {"awaiting_clarification", "failed"})
            self.assertEqual(waiting["status"], "awaiting_clarification", waiting.get("error"))
            self.assertEqual(self.news.calls, [])
            self.assertEqual([row.get("stage") for row in self.provider.payloads],
                             ["relation_select"])
            review = waiting["review"]
            self.assertIn("V800", review["question"])
            response = self.client.post(f"/desktop/api/explore/runs/{run_id}/clarify",
                json={"idempotency_key": str(uuid4()), "revision": review["revision"],
                      "question": resolved})
            self.assertEqual(response.status_code, 202, response.json)
            done = self.wait(run_id)
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["resolved_question"], resolved)
        input_file = self.service.repository.get_file(
            self.owner, self.session_id, done["report"]["input_file_id"])
        self.assertIn("这个 V800 是谁负责的？", input_file["content"])
        self.assertIn(resolved, input_file["content"])
        with patch.object(self.service, "_start"):
            followup = self.service.create_run(self.owner, self.session_id, {
                "module": "relation", "question": "继续核对双方的关联。", "context_mode": "manual",
                "file_ids": [input_file["id"]], "idempotency_key": str(uuid4())})
        self.assertIn(resolved, followup["context_files"][0]["content_excerpt"])
        self.service.cancel(self.owner, followup["id"])
        self.assertIn("明确后的问题：" + resolved, done["report"]["markdown"])
        ancestry = self.service.repository.ancestor_context(self.owner, done["report"]["id"])
        self.assertEqual(ancestry[0]["resolved_question"], resolved)
        self.assertEqual([row.get("stage") for row in self.provider.payloads],
                         ["relation_select", "relation_select", "relation_lite_report"])
        self.assertEqual(len(self.news.calls), 2)

    def test_missing_model_recommendation_is_explicit_partial_with_ok(self):
        self.provider.bad_recommendations = True
        done = self.run_with_old_path_blocked()
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual(done["report"]["recommendations"], [])
        self.assertIn("三个可用的后续建议", done["report"]["completion_warning"])
        self.assertIn("本轮报道涉及尊界V800事件", done["report"]["summary"])
        self.assertIn("合作分工与具体法律责任", done["report"]["sections"][0]["body"])
        self.assertEqual(done["report"]["report_variant"], "recommendations_unavailable")
        self.assertEqual([row.get("stage") for row in self.provider.payloads],
                         ["relation_select", "relation_lite_report"])
        self.assertEqual(done["next_step_state"], "pending")
        key = str(uuid4())
        finished = self.client.post(f"/desktop/api/explore/runs/{done['id']}/finish",
                                    json={"idempotency_key": key})
        self.assertEqual(finished.status_code, 200, finished.json)
        self.assertEqual(finished.json["run"]["next_step_state"], "finished")
        self.assertTrue(finished.json["session"]["closed_at"])
        same = self.client.post(f"/desktop/api/explore/runs/{done['id']}/finish",
                                json={"idempotency_key": key})
        self.assertEqual(same.status_code, 200, same.json)
        self.assertEqual(self.client.post(
            f"/desktop/api/explore/sessions/{self.session_id}/runs",
            json={"module": "relation", "question": QUESTION,
                  "idempotency_key": str(uuid4())}).status_code, 409)

    def test_model_questions_with_whitespace_start_exact_recommended_runs(self):
        self.provider.whitespace_recommendations = True
        for module in ("event", "relation", "history"):
            with self.subTest(module=module):
                done = self.run_with_old_path_blocked()
                self.assertEqual(done["status"], "succeeded", done.get("error"))
                options = done["report"]["recommendations"]
                self.assertEqual([row["module"] for row in options],
                                 ["event", "relation", "history"])
                self.assertTrue(all(row["question"] == row["question"].strip() for row in options))
                selected = next(row for row in options if row["module"] == module)
                payload = {"module": module, "question": selected["question"],
                           "source_report_id": done["report"]["id"],
                           "idempotency_key": str(uuid4())}
                with patch.object(self.service, "_start"):
                    clicked = self.client.post(
                        f"/desktop/api/explore/sessions/{self.session_id}/runs", json=payload)
                    same = self.client.post(
                        f"/desktop/api/explore/sessions/{self.session_id}/runs", json=payload)
                self.assertEqual(clicked.status_code, 202, clicked.json)
                self.assertEqual(same.status_code, 202, same.json)
                self.assertEqual(clicked.json["run"]["id"], same.json["run"]["id"])
                parent = self.service.repository.get_run(self.owner, done["id"])
                self.assertEqual(parent["next_step_state"], "continued")
                self.assertEqual(parent["continuation_run_id"], clicked.json["run"]["id"])
                self.assertEqual(self.client.post(f"/desktop/api/explore/runs/{done['id']}/finish",
                    json={"idempotency_key": str(uuid4())}).status_code, 409)
                self.service.cancel(self.owner, clicked.json["run"]["id"])

    def test_deleted_source_during_model_cannot_publish_report_or_original_input(self):
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.report_started.wait(3))
                in_progress = self.service.repository.get_run(self.owner, run_id)
                self.assertTrue(in_progress["materials"])
                source_file_id = in_progress["materials"][0]["file_id"]
                deleted = self.client.delete(
                    f"/desktop/api/explore/sessions/{self.session_id}/files/{source_file_id}")
                self.assertEqual(deleted.status_code, 200, deleted.json)
            finally:
                self.provider.report_release.set()
            done = self.wait(run_id)
        self.assertEqual(done["status"], "failed")
        self.assertIsNone(done["report"])
        self.assertIsNone(done.get("input_file_id"))
        self.assertFalse(any(row["name"].startswith("发现关联原始输入")
                             for row in self.service.repository.list_files(self.owner, self.session_id)))

    def test_cancel_during_model_cannot_publish_late_report_or_input(self):
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.report_started.wait(3))
                cancelled = self.client.post(f"/desktop/api/explore/runs/{run_id}/cancel")
                self.assertEqual(cancelled.status_code, 200, cancelled.json)
            finally:
                self.provider.report_release.set()
        done = self.wait(run_id, {"cancelled", "failed", "succeeded", "partial"})
        self.assertEqual(done["status"], "cancelled")
        self.assertIsNone(done["report"])
        self.assertIsNone(done.get("input_file_id"))

    def test_deleted_selected_file_during_model_blocks_report_and_input(self):
        previous = self.run_with_old_path_blocked()
        selected_file_id = previous["report"]["file_id"]
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            created = self.client.post(f"/desktop/api/explore/sessions/{self.session_id}/runs",
                json={"module": "relation", "question": QUESTION,
                      "context_mode": "manual", "file_ids": [selected_file_id],
                      "idempotency_key": str(uuid4())})
            self.assertEqual(created.status_code, 202, created.json)
            run_id = created.json["run"]["id"]
            try:
                self.assertTrue(self.provider.report_started.wait(3))
                deleted = self.client.delete(
                    f"/desktop/api/explore/sessions/{self.session_id}/files/{selected_file_id}")
                self.assertEqual(deleted.status_code, 200, deleted.json)
            finally:
                self.provider.report_release.set()
            done = self.wait(run_id)
        self.assertEqual(done["status"], "failed")
        self.assertIsNone(done["report"])
        self.assertIsNone(done.get("input_file_id"))

    def test_deleted_session_during_model_cannot_be_revived(self):
        self.provider.report_started = threading.Event()
        self.provider.report_release = threading.Event()
        with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                   self.reader.tool):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.report_started.wait(3))
                deleted = self.client.delete(f"/desktop/api/explore/sessions/{self.session_id}")
                self.assertEqual(deleted.status_code, 200, deleted.json)
            finally:
                self.provider.report_release.set()
        self.assertEqual(self.client.get(f"/desktop/api/explore/runs/{run_id}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
