"""Two-step event explanation: one knowledge answer, then one bounded news report."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urldefrag

from ..workbench.errors import GatewayError
from ..workbench.graph_runtime import GiteeProvider
from ..workbench.search import _safe_source_url
from ..workbench.tool_registry import ToolContext, ToolRegistry
from .contracts import render_markdown, safe_url, validate_model_report
from .event_search import relevant_news, search_intent
from .public_reader import build_public_reader_tool
from .repository import ACTIVE, timestamp, uid


_OPTIONS = re.compile(r"\n*(?:这次解释是否清楚[？?].*)?\n*1\s*[：:]\s*我已明白\s*\n*2\s*[：:]\s*我需要进一步解释\s*$", re.S)


def _freshness(question):
    return search_intent(question, timestamp())["freshness"]


def _relevant(question, item):
    return relevant_news(question, item)


class EventLiteRuntime:
    def __init__(self, service, owner, run, control):
        self.service = service
        self.repo = service.repository
        self.owner = owner
        self.run = run
        self.control = control
        try:
            self.skill = service.research_skills.load("explore-event")
        except ValueError:
            raise GatewayError("理解事件 Skill 无效或未安装。", 503, "explore_skill_unavailable") from None
        self.skill_hash = hashlib.sha256(self.skill["instructions"].encode()).hexdigest()

    def execute(self):
        phase = self.run.get("phase")
        if phase == "initial_explanation":
            self._initial()
        elif phase == "event_deep_report" and self.run.get("feedback_choice") == "explain_more":
            self._deep_report()
        else:
            raise GatewayError("理解事件阶段已改变。", 409, "explore_stale_feedback")

    def _on_response(self, response):
        self.control["response"] = response
        if self.control["stop"].is_set() and response is not None:
            response.abort()

    def _context(self, query=""):
        return ToolContext(stopped=self.control["stop"], on_response=self._on_response,
                           emit=lambda _event: None, query=query)

    def _plain_once(self, messages):
        provider = self.service._provider_for_deadline(self.control.get("deadline"))
        if isinstance(provider, GiteeProvider):
            provider.on_diagnostic = self.service._model_transport_observer(self.control, "event_initial")
        stream = None
        pieces, length = [], 0
        try:
            if callable(getattr(provider, "tokens", None)):
                stream = provider.tokens(messages, provider.model, self.control["stop"], self._on_response)
                iterator = stream
            else:
                stream = provider.events(messages, provider.model, self.control["stop"], self._on_response)
                iterator = (event["text"] for event in stream if event.get("type") == "token")
            for token in iterator:
                self.service._check_stop(self.control)
                if not isinstance(token, str):
                    raise GatewayError("模型返回了非文本内容。", 502, "explore_invalid_model_output")
                length += len(token)
                if length > 8000:
                    raise GatewayError("首次解释超过长度上限。", 502, "explore_invalid_model_output")
                pieces.append(token)
            self.service._check_stop(self.control)
        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                stream.close()
            self.control["response"] = None
        answer = _OPTIONS.sub("", "".join(pieces).strip()).strip()
        if not answer:
            raise GatewayError("模型未返回可用解释。", 502, "explore_invalid_model_output")
        return answer

    def _initial(self):
        self.service._stage(self.owner, self.run["id"], "explaining", self.control)
        prompt = (
            "你是一个智能金融分析助手，你非常了解股票、证券市场，是一个高级股票分析员。"
            "现在我需要你利用你的知识直接回答我这个问题。\n"
            "要求：把你知道的经济金融知识用简单直白的语言来解释输入的问题，通常考虑股价涨跌或股市异动的原因；"
            "如果不是股票问题，直接回答实际问题。先回答，再解释最相关原因或机制。"
            "仅根据已有知识作解释，不编造最新新闻、行情、数字或确定因果。"
            "通常两三段短文；多个子问题分别简答。只输出解释正文，绝不输出反馈问题、数字选项或按钮文案。"
            "本阶段没有工具，也不能把普通聊天历史作为研究资料。\n"
            "以下是本轮理解事件 Skill 的工作要求：\n" + self.skill["instructions"] +
            "\n宿主已在解释后提供两个可点击按钮；只输出解释正文，不输出选项。"
        )
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": self.run["question"]}]
        while True:
            try:
                answer = self._plain_once(messages)
                break
            except GatewayError as exc:
                if not self.service._reserve_model_retry(self.control, exc,
                                                         self.control.get("deadline"), "event_initial"):
                    raise
        saved = self.repo.transition(self.owner, self.run["id"], ACTIVE,
            status="awaiting_feedback", stage="awaiting_feedback",
            execution_id=self.control["execution_id"], fields={
                "initial_answer": answer, "model_progress": None,
                "skill_hash": self.skill_hash,
                "model_transport_trace": copy.deepcopy(self.control.get("model_transport_trace", [])),
            })
        if saved is None:
            raise GatewayError("本轮已停止。", 409, "explore_cancelled")

    def _query(self):
        return search_intent(self.run["question"], self.run["time_policy"]["as_of"])["query"]

    def _check_selected_files(self):
        for item in self.run.get("context_files") or []:
            file_id = item.get("file_id")
            if file_id:
                self.repo.get_file(self.owner, self.run["session_id"], file_id)

    def _search_news(self, retrieval):
        self.service._stage(self.owner, self.run["id"], "searching_web", self.control)
        intent = search_intent(self.run["question"], self.run["time_policy"]["as_of"])
        query = intent["query"]
        args = {"query": query, "count": 5, "freshness": intent["freshness"]}
        started = time.monotonic()
        record = {"name": "web_search", "arguments": args, "started_at": timestamp(),
                  "initiator": "event_lite", "returned_count": 0, "filter_counts": {},
                  "candidates": [], "window_start": intent.get("window_start")}
        try:
            result = self.service.search_tools.execute("web_search", args, self._context(query), call_count=0)
            raw = (result.sources or [])[:5]
            record["returned_count"] = len(raw)
            record["provider_excluded_count"] = result.content.get("excludedCount", 0)
            as_of = datetime.fromisoformat(self.run["time_policy"]["as_of"].replace("Z", "+00:00"))
            window_start = (datetime.fromisoformat(intent["window_start"].replace("Z", "+00:00"))
                            if intent.get("window_start") else None)
            news, seen, seen_titles = [], set(), set()
            for item in raw:
                url = _safe_source_url(item.get("url"))
                identity = urldefrag(url).url if url else None
                title = str(item.get("title") or "")[:300]
                title_key = re.sub(r"\W", "", title).casefold()
                published = item.get("publishedAt")
                reason = None
                date = None
                if published:
                    try:
                        date = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
                        date = date if date.tzinfo else date.replace(tzinfo=timezone.utc)
                        if date > as_of + timedelta(minutes=5):
                            reason = "future_date"
                    except (TypeError, ValueError):
                        published = None
                if not reason and window_start and (date is None or date < window_start):
                    reason = "missing_date" if date is None else "outside_requested_window"
                if not reason:
                    if not url or not identity or not title_key:
                        reason = "invalid_source"
                    elif identity in seen or title_key in seen_titles:
                        reason = "duplicate"
                    elif not _relevant(self.run["question"], item):
                        reason = "unrelated"
                record["candidates"].append({"title": title, "url": url, "published_at": published,
                                              "accepted": reason is None, "reason": reason})
                if reason:
                    record["filter_counts"][reason] = record["filter_counts"].get(reason, 0) + 1
                    continue
                seen.add(identity)
                seen_titles.add(title_key)
                news.append({
                    "id": f"S{len(news) + 1}", "kind": "web_search", "source": "web_search",
                    "source_name": "联网搜索摘要", "title": title,
                    "summary": str(item.get("snippet") or "")[:1000],
                    "text_used": str(item.get("snippet") or item.get("title") or "")[:1000],
                    "url": url, "published_at": published,
                    "retrieved_at": result.content.get("searchedAt") or timestamp(),
                    "content_kind": "search_snippet", "provider_id": str(item.get("id") or "")[:100],
                })
            record.update(status="complete" if news else "empty", result_count=len(news))
            return news
        except GatewayError as exc:
            self.service._check_stop(self.control)
            record.update(status="failed", result_count=0, error=str(exc))
            return []
        finally:
            record["duration_seconds"] = round(time.monotonic() - started, 3)
            retrieval["tool_trace"].append(record)
            retrieval["searches"].append({"query": query, "freshness": args["freshness"],
                "searched_at": record["started_at"], "status": record.get("status", "failed"),
                "count": record.get("result_count", 0), "error": record.get("error"),
                "returned_count": record["returned_count"], "filter_counts": record["filter_counts"],
                "window_start": record["window_start"]})
            self.control["response"] = None

    def _read_news(self, sources, retrieval):
        if not sources:
            return
        # Snippets can answer some questions as snippets. Read only when they
        # are too thin for further explanation or the question asks for exact
        # facts; reserve model-writing time even with slow websites.
        precise = bool(re.search(r"为什么|具体|原文|公告|数据|多少|数值|原因|依据", self.run["question"]))
        reader = build_public_reader_tool(self.service.public_dns_mode)
        read_limit = max(0, min(3, self.service.research_limits["tool_calls"] - 1,
                                reader.max_calls))
        candidates = [item for item in sources if precise or len(item.get("summary") or "") < 220][:read_limit]
        if not candidates:
            return
        self.service._stage(self.owner, self.run["id"], "reading_pages", self.control)
        registry = ToolRegistry((reader,))
        reserve = max(8.0, min(60.0, self.service.timeout * .35))
        for index, source in enumerate(candidates):
            self.service._check_stop(self.control)
            if self.control["deadline"] - time.monotonic() <= reserve + 15.0:
                retrieval["read_stop_reason"] = "write_time_reserved"
                break
            args = {"url": source["url"], "max_chars": 4000}
            started = time.monotonic()
            record = {"name": "fetch_public_page", "arguments": args, "started_at": timestamp(),
                      "initiator": "event_lite"}
            try:
                result = registry.execute("fetch_public_page", args, self._context(), call_count=index)
                page = result.content
                text = page.get("text") if page.get("status") == "ok" else None
                if isinstance(text, str) and text.strip():
                    source.update(kind="public_page", source="fetch_public_page",
                        text_used=text[:4000], evidence_text=text[:4000],
                        content_kind="extracted_page_text", source_name=page.get("publisher") or "网页原文")
                    if safe_url(page.get("final_url")):
                        source["final_url"] = page["final_url"]
                    record.update(status="complete", returned_chars=len(source["text_used"]))
                else:
                    record.update(status=page.get("status") or "empty", returned_chars=0)
            except GatewayError as exc:
                self.service._check_stop(self.control)
                record.update(status="failed", error=str(exc), returned_chars=0)
            finally:
                record["duration_seconds"] = round(time.monotonic() - started, 3)
                retrieval["tool_trace"].append(record)
                self.control["response"] = None

    @staticmethod
    def _fallback_report(sources, initial_answer, reason=None):
        if sources:
            lines = [f"{item['id']} {item['title']}：{item['text_used'][:350]}" for item in sources]
            return {"title": "相关新闻材料整理", "summary": "模型未能完成深度解释；以下仅整理本轮实际取得的报道内容。",
                "key_findings": ["本次只保留相关新闻线索，具体股价因果尚未形成完整分析。"],
                "sections": [{"heading": "新闻线索", "body": "\n".join(lines),
                              "source_refs": [item["id"] for item in sources]}]}
        if reason == "explore_search_failed":
            return {"title": "新闻检索暂时未完成", "summary": "本轮新闻搜索服务未能完成请求，暂时保留已有解释。",
                "key_findings": ["搜索请求未完成，目前无法判断相关新闻是否充足。"],
                "sections": [{"heading": "已有解释", "body": initial_answer[:3000], "source_refs": []}]}
        return {"title": "相关新闻不足", "summary": "本轮未取得可用的相关新闻，无法据此核实最新事实。",
                "key_findings": ["当前没有可引用的相关新闻；此前的金融机制解释仅供理解问题。"],
                "sections": [{"heading": "金融机制", "body": initial_answer[:3000], "source_refs": []}]}

    def _report_model(self, sources, retrieval):
        payload = {"task": "research_report", "stage": "event_deep_lite", "module": "event",
            "question": self.run["question"], "as_of": self.run["time_policy"]["as_of"],
            "initial_answer_unverified": self.run.get("initial_answer"),
            "sources": [{"id": item["id"], "title": item["title"], "url": item["url"],
                "published_at": item.get("published_at"), "content_kind": item["content_kind"],
                "text": item["text_used"]} for item in sources], "retrieval": retrieval["searches"]}
        prompt = (
            "你是智能金融分析助手。根据本轮原问题和最多五篇相关新闻，直接写一份通俗的进一步解释报告。"
            "先回答问题，再把新闻和相关金融机制连起来；不逐篇复述，不编造价格、日期、确定因果。"
            "只把 sources 明确支持的内容写成新闻事实。initial_answer_unverified 仅作沟通背景，不能当成核实证据。"
            "搜索摘要不是阅读全文；只有 content_kind=extracted_page_text 才是已读网页原文。"
            "按 question 的时间范围和 as_of 回答；只取得其中一天的资料时，不写成整个时间范围都已核实。"
            "指数与跟踪它的 ETF 是不同标的，不把 ETF 涨跌幅写成指数涨跌幅；评论中的预测不能写成已发生的事实。"
            "只输出 JSON 对象：title 短标题，summary 核心结论，key_findings 为1至3条短句，"
            "sections 为1至2节，每节 {heading,body,source_refs}，source_refs 只能使用输入的 S 编号。"
            "通常正文总长600至1000中文字符，材料少则更短。来源不足时如实说未能核实，"
            "可解释一般金融原理但不得编造新闻。不要输出新一轮反馈选项。"
            "以下是本轮理解事件 Skill 的工作要求：\n" + self.skill["instructions"] +
            "\n本次已经选择进一步解释，完成报告后结束；不要输出选择提示。"
        )
        return self.service._invoke_model(payload, prompt, self.control,
            self.service._model_progress(self.owner, self.run["id"], self.control, "event_deep_lite"))

    def _deep_report(self):
        retrieval = {"searches": [], "news": [], "tool_trace": []}
        self._check_selected_files()
        sources = self._search_news(retrieval)
        self._read_news(sources, retrieval)
        self._check_selected_files()
        saved = self.repo.save_materials(self.owner, self.run["id"], sources, retrieval,
            execution_id=self.control["execution_id"], stage="analyzing")
        if saved is None:
            raise GatewayError("本轮已停止。", 409, "explore_cancelled")
        self.service._check_stop(self.control)
        valid = False
        try:
            if not sources:
                if any(row.get("status") == "failed" for row in retrieval["searches"]):
                    raise GatewayError("新闻搜索服务未能完成请求。", 502, "explore_search_failed")
                raise GatewayError("没有找到与问题直接相关的新闻。", 404, "explore_no_related_news")
            raw = self._report_model(sources, retrieval)
            if not isinstance(raw, dict):
                raise GatewayError("报告格式无效。", 502, "explore_invalid_model_output")
            defaults = {"uncertainties": [], "limitations": [], "actors": [], "drivers": [],
                        "impact_paths": [], "timeline": [], "recommendations": []}
            raw = {**defaults, **raw}
            data = validate_model_report(raw, "event", sources, set(), compact_event=True)
            if sources and not any(section["source_refs"] for section in data["sections"]):
                raise GatewayError("报告未引用已取得的新闻。", 502, "explore_invalid_source_ref")
            valid = True
        except GatewayError as exc:
            self.service._check_stop(self.control)
            # A failed report generation must not convert headlines into facts.
            raw = {**{"uncertainties": [], "limitations": [], "actors": [], "drivers": [],
                      "impact_paths": [], "timeline": [], "recommendations": []},
                   **self._fallback_report(sources, self.run.get("initial_answer") or "", exc.code)}
            data = validate_model_report(raw, "event", sources, set(), compact_event=True)
            retrieval["report_fallback_reason"] = exc.code or "model_error"
        retrieval["model_trace"] = copy.deepcopy(self.control.get("model_transport_trace", []))
        self.service._stage(self.owner, self.run["id"], "saving", self.control)
        status = "succeeded" if valid and sources else "partial"
        fallback_reason = retrieval.get("report_fallback_reason")
        warning = ("本轮未找到相关新闻；仅保留有限解释。" if fallback_reason == "explore_no_related_news"
                   else "本轮新闻搜索请求未完成；暂时保留已有解释。" if fallback_reason == "explore_search_failed"
                   else "模型未能完成深度解释；以下仅整理已取得的材料。" if not valid else None)
        report = {"id": uid(), "run_id": self.run["id"], "module": "event",
            "question": self.run["question"], "resolved_question": self.run.get("resolved_question"),
            "source_report_id": self.run.get("source_report_id"), "initial_answer": self.run.get("initial_answer"),
            "context_selection": self.run.get("context_selection"),
            "context_files": self.run.get("context_files") or [],
            "context_limitations": self.run.get("context_limitations") or [],
            "created_at": timestamp(), "as_of": self.run["time_policy"]["as_of"],
            "status": status, "mode": "current_research", "sources": sources,
            "retrieval": retrieval, "skills_used": [{"name": "explore-event", "content_hash": self.skill_hash}],
            "workflow_version": "event-lite@1", "report_format": "event_deep_lite@1",
            "answer_status": "answered" if status == "succeeded" else "partial",
            "report_variant": "news_explanation" if valid else (
                "insufficient_news" if fallback_reason == "explore_no_related_news" else
                "search_unavailable" if fallback_reason == "explore_search_failed" else "generation_fallback"),
            "completion_warning": warning,
            "model_transport_trace": copy.deepcopy(self.control.get("model_transport_trace", [])),
            **data}
        report["markdown"] = render_markdown(report)
        self.service._check_stop(self.control)
        live = self.repo.get_run(self.owner, self.run["id"])
        required_evidence = [item["evidence_id"] for item in live.get("materials") or []
                             if item.get("evidence_id")]
        required_files = [item["file_id"] for item in self.run.get("context_files") or []
                          if item.get("file_id")]
        if not self.repo.complete(self.owner, self.run["id"], report, status,
                                  execution_id=self.control["execution_id"],
                                  required_evidence_ids=required_evidence,
                                  required_selected_file_ids=required_files):
            raise GatewayError("研究材料已改变，本次报告未保存。", 409, "explore_evidence_changed")
