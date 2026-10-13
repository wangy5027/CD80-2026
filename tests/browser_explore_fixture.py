"""Isolated Explore browser server with synthetic news and model responses.

Run after building the frontend::

    .venv/bin/python tests/browser_explore_fixture.py

This is an acceptance fixture, never production data or a live integration test.
Accounts and SQLite files live in a temporary directory. No dotenv file is read,
and no network request is made to a news or model provider. Ordinary chat and
Build use the existing browser fixture's separate model replacement.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Never inherit an actual model credential, even if this script is run from a
# configured shell. The application factory does not load the local .env file.
for _name in tuple(os.environ):
    if _name.startswith("GITEE_AI_"):
        os.environ.pop(_name, None)

from flask import jsonify
from werkzeug.serving import WSGIRequestHandler, make_server

from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.explore.contracts import explicit_judgment
from backend.app.modules.workbench.builtin_tools import make_search_tool
from backend.app.modules.workbench.tool_registry import ToolRegistry
from tests.browser_fixture import fixture_app


CURRENT_ARTICLE = {
    "id": "browser-event-2026",
    "source": "eastmoney",
    "source_name": "东方财富财经快讯（合成测试材料）",
    "title": "合成测试材料：测试甲集团宣布收购测试乙公司",
    "summary": "这条合成新闻仅用于 Explore 浏览器验收。测试甲集团宣布拟收购测试乙公司，尚需监管与股东批准。",
    "published_at": "2026-10-12T10:00:00+08:00",
    "url": "https://example.test/news/browser-event-2026",
    "title_generated": False,
}
PAST_ARTICLE = {
    "id": "browser-history-2022",
    "source": "eastmoney",
    "source_name": "东方财富财经快讯（合成测试材料）",
    "title": "合成测试材料：2022 年测试丙集团并购测试丁公司",
    "summary": "这条合成过往材料仅用于验收历史案例筛选。该并购在 2022 年披露，后续整合情况未知。",
    "published_at": "2022-06-18T09:00:00+08:00",
    "url": "https://example.test/news/browser-history-2022",
    "title_generated": False,
}


class FixtureObservations:
    def __init__(self):
        self.lock = threading.Lock()
        self.news_calls = []
        self.web_search_calls = []
        self.model_calls = []
        self.clarify_model_calls = 0
        self.report_model_calls = 0
        self.report_context_file_ids = []
        self.cancellation_observed = 0

    def news(self, source, limit, offset, query):
        with self.lock:
            self.news_calls.append({"source": source, "limit": limit,
                                    "offset": offset, "query": query})

    def model(self, module, question, task, context_file_ids=()):
        with self.lock:
            self.model_calls.append({"module": module, "question": question, "task": task})
            if task == "clarify":
                self.clarify_model_calls += 1
            elif task == "report":
                self.report_model_calls += 1
                self.report_context_file_ids.append(list(context_file_ids))

    def web_search(self, arguments):
        with self.lock:
            self.web_search_calls.append(dict(arguments))

    def cancelled(self):
        with self.lock:
            self.cancellation_observed += 1

    def snapshot(self):
        with self.lock:
            return {
                "fixture": True, "realNews": False, "realModel": False,
                "newsCalls": len(self.news_calls),
                "webSearchCalls": len(self.web_search_calls),
                "modelCalls": len(self.model_calls),
                "clarifyModelCalls": self.clarify_model_calls,
                "reportModelCalls": self.report_model_calls,
                "reportContextFileIds": list(self.report_context_file_ids),
                "lastReportContextFileIds": (self.report_context_file_ids[-1]
                                             if self.report_context_file_ids else []),
                "cancellationObserved": self.cancellation_observed,
            }


class SyntheticNewsService:
    """NewsService-compatible response with labelled synthetic articles."""

    def __init__(self, observations):
        self.observations = observations

    def get_news(self, source="eastmoney", limit=20, offset=0, q=""):
        self.observations.news(source, limit, offset, q)
        if "[NEWS_FAILURE]" in q:
            raise GatewayError("合成新闻服务失败，用于浏览器验收。", 502)
        # The fixture intentionally returns both the current event and a dated
        # historical article for any search query. Production keyword matching
        # and source freshness are exercised by the dedicated NewsService tests.
        articles = [dict(CURRENT_ARTICLE), dict(PAST_ARTICLE)]
        if source == "cls":
            for item in articles:
                item.update(id=item["id"] + "-cls", source="cls",
                            source_name="财联社电报（合成测试材料）", url=None)
        selected = [dict(item) for item in articles[offset:offset + limit]]
        fetched_at = datetime.now(timezone(timedelta(hours=8)))
        return {
            "provider": "browser-synthetic-news", "source": source,
            "source_name": "财联社电报（合成测试材料）" if source == "cls" else "东方财富财经快讯（合成测试材料）",
            "items": selected,
            "pagination": {"limit": limit, "offset": offset,
                           "total": len(articles), "has_more": offset + limit < len(articles)},
            "latest_published_at": CURRENT_ARTICLE["published_at"],
            "cache": {"hit": False, "stale": False,
                      "fetched_at": fetched_at.isoformat(timespec="seconds"),
                      "expires_at": (fetched_at + timedelta(minutes=1)).isoformat(timespec="seconds"),
                      "age_seconds": 0, "ttl_seconds": 60,
                      "refresh_retry_after_seconds": 0},
            "warning": None,
        }

    def get_sources(self):
        return {"provider": "browser-synthetic-news", "default_source": "eastmoney",
                "sources": [{"id": "eastmoney", "name": "东方财富财经快讯（合成测试材料）",
                             "interface": "fixture", "has_article_url": True},
                            {"id": "cls", "name": "财联社电报（合成测试材料）",
                             "interface": "fixture", "has_article_url": False}],
                "refresh": {"mode": "on_request", "ttl_seconds": 60,
                            "timeout_seconds": 1, "max_stale_seconds": 60,
                            "retry_seconds": 1}}


class SyntheticWebSearch:
    """Registered-tool-compatible, labelled synthetic search without network."""

    available = True

    def __init__(self, observations):
        self.observations = observations

    def search(self, arguments, stopped, on_response):
        self.observations.web_search(arguments)
        if stopped.is_set():
            raise GatewayError("合成搜索已取消。", 409)
        if "[WEB_SEARCH_FAILURE]" in arguments["query"]:
            raise GatewayError("合成联网搜索失败。", 502)
        if "[WEB_SEARCH_EMPTY]" in arguments["query"]:
            sources = []
        else:
            malicious = '[UNSAFE_HTML]' in arguments["query"]
            sources = [
                {"id": "browser-web-current", "title": "合成联网摘要：测试甲集团拟收购测试乙公司",
                 "snippet": ('<img src=x onerror="window.__exploreXss=1"> 此为合成安全验收摘要。' if malicious else
                             "此为浏览器验收的合成联网摘要，交易条件仍待核实。"),
                 "publishedAt": CURRENT_ARTICLE["published_at"],
                 "url": "https://example.test/web/browser-current"},
                {"id": "browser-web-history", "title": "合成联网历史摘要：2022 年测试丙集团并购测试丁公司",
                 "snippet": "此为浏览器验收的合成过往摘要，后续整合未知。",
                 "publishedAt": PAST_ARTICLE["published_at"],
                 "url": "https://example.test/web/browser-history"},
            ]
        return {"query": arguments["query"], "freshness": arguments["freshness"],
                "searchedAt": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
                "sources": sources[:arguments["count"]]}


class SyntheticExploreProvider:
    model = "browser-explore-no-network"
    key = "fixture-only"

    def __init__(self, observations):
        self.observations = observations
        self.failed_once = set()
        self.lock = threading.Lock()

    @staticmethod
    def _field(payload, key, default=""):
        if isinstance(payload, dict):
            if key in payload:
                return payload[key]
            for value in payload.values():
                result = SyntheticExploreProvider._field(value, key, None)
                if result is not None:
                    return result
        return default

    @staticmethod
    def _source_refs(payload):
        sources = SyntheticExploreProvider._field(payload, "sources", [])
        if isinstance(sources, list):
            refs = []
            historical = []
            for source in sources:
                if not isinstance(source, dict):
                    continue
                value = source.get("ref") or source.get("reference_id") or source.get("id")
                if not isinstance(value, str) or not value.startswith("S"):
                    continue
                refs.append(value)
                published = str(source.get("published_at") or "")
                if published.startswith("2022-") or "2022" in str(source.get("title") or ""):
                    historical.append(value)
            current = next((value for value in refs if value not in historical), None)
            return ([current] if current else []), historical[:1]
        return [], []

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        task = payload.get("task", "report")
        serialized = json.dumps(payload, ensure_ascii=False)
        module = self._field(payload, "module")
        question = self._field(payload, "question")
        if not isinstance(question, str):
            question = serialized[:200]
        if module not in {"event", "relation", "history"}:
            system = messages[0]["content"]
            module = ("relation" if "关系" in system else
                      "history" if "历史" in system else "event")
        context_file_ids = [item.get("file_id") for item in payload.get("context_files", [])
                            if isinstance(item, dict)]
        self.observations.model(module, question, task, context_file_ids)
        if task == "clarify":
            ambiguous = ("[AMBIGUOUS_EXPLORE]" in question or
                         (module == "history" and not explicit_judgment(question)) or
                         ("发生了什么" in question and "拟收购" not in question and "并购" not in question) or
                         "有什么新闻" in question)
            sources = self._field(payload, "sources", [])
            external = next((item for item in sources if isinstance(item, dict) and item.get("kind") != "user"), None)
            angles = (
                ("交易经过与主体", "并购关系与传导机制", "历史条件与失效因素")
                if module != "history" else
                ("行业集中度", "整合条件", "审批失败风险"))
            options = ([{"id": f"source_{index}", "label": f"{external['title']} · {angle}",
                         "question": (f"待验证判断：测试甲集团收购测试乙公司是否影响{angle}？请用历史材料定性对照。"
                                      if module == "history" else
                                      f"请分析测试甲集团拟收购测试乙公司的{angle}。"),
                         "reason": "根据已检索的合成材料提出方向。",
                         "source_refs": [external["id"]]}
                        for index, angle in enumerate(angles, 1)] if ambiguous and external else [])
            answer = json.dumps({"needs_clarification": ambiguous,
                                 "interpreted_question": question,
                                 "summary": "已取得合成搜索摘要和快讯摘录，请核对后选择材料。",
                                 "question": "请明确您要研究的具体事件或判断。" if ambiguous else "",
                                 "options": options, "limitations": []}, ensure_ascii=False)
            for offset in range(0, len(answer), 101):
                if stopped.is_set():
                    self.observations.cancelled()
                    raise GatewayError("合成意图预判已取消。", 409, "cancelled")
                yield answer[offset:offset + 101]
            return
        try:
            if "[WAIT_EXPLORE]" in serialized:
                limit = time.monotonic() + 25
                while time.monotonic() < limit and not stopped.wait(.05):
                    pass
                if stopped.is_set():
                    self.observations.cancelled()
                    raise GatewayError("合成模型调用已取消。", 409, "cancelled")
            if "[FAIL_EXPLORE]" in serialized:
                raise GatewayError("合成模型失败，用于浏览器重试验收。", 502)
            if "[FAIL_ONCE_EXPLORE]" in serialized:
                with self.lock:
                    first_attempt = question not in self.failed_once
                    self.failed_once.add(question)
                if first_attempt:
                    raise GatewayError("合成模型首次失败，用于浏览器重试验收。", 502)
            if "[INVALID_EXPLORE]" in serialized:
                answer = "{invalid JSON"
            else:
                current_ref, past_ref = self._source_refs(payload)
                unsafe = "[UNSAFE_HTML]" in serialized
                detail = ('<img src=x onerror="window.__exploreXss=1"> '
                          '[恶意链接](javascript:alert(1))' if unsafe else
                          "合成模型说明：并购交易仍取决于审批与整合条件。")
                answer_object = {
                    "title": f"合成测试报告：{module}",
                    "summary": detail,
                    "key_findings": ["合成模型发现：交易公告说明拟收购，结果尚未确定。"],
                    "uncertainties": ["监管审批和后续整合尚不明确。"],
                    "sections": [{"heading": "证据与判断", "body": detail,
                                  "source_refs": current_ref}],
                    "limitations": ["仅使用浏览器验收的合成材料，不代表真实财经结论。"],
                    "recommendations": [
                        {"question": "继续分析审批条件", "module": "event",
                         "reason": "核对交易条件（合成测试推荐）"},
                        {"question": "追踪并购关系路径", "module": "relation",
                         "reason": "检查主体之间的传导机制（合成测试推荐）"},
                        {"question": "验证判断：测试甲集团收购测试乙公司是否推动行业集中度提升？",
                         "module": "history", "reason": "用历史材料对照条件（合成测试推荐）"},
                    ],
                }
                if module == "event":
                    answer_object.update({
                        "event_overview": "合成模型概述：测试甲集团拟收购测试乙公司。",
                        "background": "双方均为虚构的浏览器验收主体。",
                        "actors": ["测试甲集团", "测试乙公司"],
                        "drivers": ["合成测试中的业务整合动因"],
                        "impact_paths": ["交易获批后可能影响相关业务竞争格局"],
                        "timeline": [{"time": CURRENT_ARTICLE["published_at"],
                                      "event": "合成新闻披露拟收购事项",
                                      "source_refs": current_ref}],
                    })
                elif module == "relation":
                    answer_object.update({
                        "entities": ["测试甲集团", "测试乙公司"],
                        "relationships": [{"from": "测试甲集团", "to": "测试乙公司",
                                           "relation_type": "拟收购", "direction": "甲集团指向乙公司",
                                           "mechanism": "股权交易完成后可能产生控制关系",
                                           "conditions": "监管和股东审批完成",
                                           "source_refs": current_ref}],
                    })
                else:
                    answer_object.update({
                        "judgment": "该并购可能推动行业集中度提升",
                        "conclusion": "合成过往案例仅供定性对照，无法证明收益。",
                        "cases": ([{"event": "2022 年测试丙集团并购测试丁公司",
                                    "similarities": "均涉及并购公告",
                                    "differences": "交易对象和市场背景不同",
                                    "supporting_conditions": "审批和整合顺利",
                                    "failure_conditions": "审批未通过或整合不及预期",
                                    "source_refs": past_ref}] if past_ref else []),
                    })
                answer = json.dumps(answer_object, ensure_ascii=False)
            # [STREAM_EXPLORE] is a fixture-only switch. Delaying each synthetic
            # JSON chunk lets browser tests observe persisted public progress
            # before the report is complete. It is never a real model stream.
            stream_delay = .4 if task == "report" and "[STREAM_EXPLORE]" in serialized else 0
            for offset in range(0, len(answer), 101):
                if stopped.is_set():
                    self.observations.cancelled()
                    raise GatewayError("合成模型调用已取消。", 409, "cancelled")
                yield answer[offset:offset + 101]
                if stream_delay and offset + 101 < len(answer) and stopped.wait(stream_delay):
                    self.observations.cancelled()
                    raise GatewayError("合成模型调用已取消。", 409, "cancelled")
        finally:
            on_response(None)


class QuietHandler(WSGIRequestHandler):
    def log(self, _type, _message, *args):
        pass


def fixture_app_with_explore(data_dir, *, port=48994, frontend_dist=None):
    app = fixture_app(data_dir, port=port, frontend_dist=frontend_dist)
    observations = FixtureObservations()
    synthetic_news = SyntheticNewsService(observations)
    app.extensions["news_service"] = synthetic_news
    explore = app.extensions["explore_service"]
    explore.provider = SyntheticExploreProvider(observations)
    explore.search_tools = ToolRegistry((make_search_tool(SyntheticWebSearch(observations)),))
    # The production service may hold a reference to the NewsService created by
    # the app factory. Replace that exact dependency as well as the route lookup.
    if hasattr(explore, "news_service"):
        explore.news_service = synthetic_news

    @app.get("/__explore_fixture__/stats")
    def explore_stats():
        return jsonify(observations.snapshot())

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=48994)
    parser.add_argument("--frontend-dist", type=Path, default=ROOT / "frontend" / "dist")
    options = parser.parse_args()
    if not (options.frontend_dist / "index.html").is_file():
        parser.error("Build the frontend first: npm --prefix frontend run build")

    signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    with tempfile.TemporaryDirectory(prefix="cd80-explore-browser-") as directory:
        app = fixture_app_with_explore(directory, port=options.port,
                                       frontend_dist=options.frontend_dist)
        server = None
        try:
            server = make_server("127.0.0.1", options.port, app, threaded=True,
                                 request_handler=QuietHandler)
            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{options.port}",
                              "realNews": False, "realModel": False,
                              "temporaryData": True}), flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server is not None:
                server.server_close()
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    main()
