"""Bounded relation analysis: select entities, search their news, write once."""

from __future__ import annotations

import copy
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urldefrag
from zoneinfo import ZoneInfo

from ..workbench.errors import GatewayError
from ..workbench.search import _safe_source_url
from ..workbench.tool_registry import ToolContext, ToolRegistry
from .contracts import render_markdown, safe_url, validate_intent, validate_model_report
from .public_reader import build_public_reader_tool
from .repository import ACTIVE, timestamp, uid


_PRECISE = re.compile(r"具体|原文|依据|数据|多少|数字|比例|合同|合作|供货|控股|持股|责任|条件|原因|为什么|是否")
_ONE_DAY = re.compile(r"今天|今日|昨晚|昨天|近24小时|24小时内")
_ONE_MONTH = re.compile(r"本月|近一个月|最近一个月|近30天")
_HISTORICAL = re.compile(r"历史|去年|前年|过去\d|往年|当年|此前|早年")
_DATE = re.compile(r"(?<!\d)(20\d{2})(?:[-/.年](\d{1,2})(?:[-/.月](\d{1,2}))?)?")
_LOCAL_DATE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日")
_MODEL = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9-]{0,12}\d{2,8}(?![A-Za-z0-9])", re.I)
_FOCUSED_EVENTS = ("制动支架", "刹车踏板", "制动踏板", "质量事故", "交通事故", "召回",
                   "断裂", "停产", "收购", "并购", "诉讼", "财务造假", "研发费用", "业绩预告")
_TZ = ZoneInfo("Asia/Shanghai")


def _parse_datetime(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _as_of(value):
    return (_parse_datetime(value) or datetime.now(timezone.utc)).astimezone(_TZ)


def _freshness(question, as_of):
    if _ONE_DAY.search(question):
        return "oneDay"
    if _ONE_MONTH.search(question):
        return "oneMonth"
    now = _as_of(as_of)
    if _HISTORICAL.search(question):
        return "noLimit"
    for match in _DATE.finditer(question):
        year, month, day = (int(match.group(1)), int(match.group(2) or 1),
                            int(match.group(3) or 1))
        if (year, month, day) < (now.year, now.month, now.day):
            return "noLimit"
    for match in _LOCAL_DATE.finditer(question):
        if (int(match.group(1)), int(match.group(2))) < (now.month, now.day):
            return "noLimit"
    return "oneWeek"


def _query(name, question, as_of):
    """Keep the chosen entity and original subject/event in one bounded query."""
    subject = re.sub(r"\s+", " ", question).strip()
    anchors = list(dict.fromkeys([
        *(match.group() for match in _MODEL.finditer(question)),
        *(term for term in _FOCUSED_EVENTS if term in question),
        *(match.group() for match in _DATE.finditer(question)),
        *(match.group() for match in _LOCAL_DATE.finditer(question)),
    ]))
    focus = " ".join(anchors)[:80]
    date = ""
    if re.search(r"昨天|昨晚", question):
        date = (_as_of(as_of) - timedelta(days=1)).date().isoformat()
    elif re.search(r"今天|今日|近24小时|24小时内", question):
        date = _as_of(as_of).date().isoformat()
    suffix = (" " + date) if date and date not in subject else ""
    prefix = name + (" " + focus if focus else "")
    room = 200 - len(prefix) - 1 - len(suffix)
    return (prefix + " " + subject[:max(0, room)] + suffix).strip()[:200]


def _text_key(value):
    return re.sub(r"\W", "", str(value or "")).casefold()


def _matches_entity(name, item):
    text = _text_key(str(item.get("title") or "") + " " + str(item.get("snippet") or ""))
    key = _text_key(name)
    return bool(len(key) >= 2 and key in text)


def _matches_question(name, question, item):
    if not _matches_entity(name, item):
        return False
    text = _text_key(str(item.get("title") or "") + " " + str(item.get("snippet") or ""))
    models = {_text_key(match.group()) for match in _MODEL.finditer(question)}
    if models and not any(model in text for model in models):
        return False
    events = [term for term in _FOCUSED_EVENTS if term in question]
    if events and not any(_text_key(term) in text for term in events):
        return False
    return True


class RelationLiteRuntime:
    def __init__(self, service, owner, run, control):
        self.service = service
        self.repo = service.repository
        self.owner = owner
        self.run = run
        self.control = control
        try:
            self.skill = service.research_skills.load("explore-relation")
        except ValueError:
            raise GatewayError("发现关联 Skill 无效或未安装。", 503, "explore_skill_unavailable") from None
        self.skill_hash = hashlib.sha256(self.skill["instructions"].encode()).hexdigest()

    def execute(self):
        if self.run.get("module") != "relation" or self.run.get("status") not in ACTIVE:
            raise GatewayError("发现关联阶段已改变。", 409, "explore_stale_relation")
        self._check_selected_files()
        selection = self._select_entities()
        if selection["needs_clarification"]:
            self._await_clarification(selection)
            return
        entities = selection["entities"]
        saved = self.repo.transition(self.owner, self.run["id"], ACTIVE,
            execution_id=self.control["execution_id"], fields={
                "selected_entities": copy.deepcopy(entities), "skill_hash": self.skill_hash,
                "phase": "relation_search", "model_progress": None})
        if saved is None:
            raise GatewayError("本轮已停止。", 409, "explore_cancelled")
        retrieval = {"selection": {"entities": copy.deepcopy(entities),
                                   "selected_count": len(entities),
                                   "truncated_count": selection["truncated_count"]},
                     "searches": [], "news": [], "tool_trace": []}
        sources = self._search_news(entities, retrieval)
        self._read_news(sources, retrieval)
        self._check_selected_files()
        saved = self.repo.save_materials(self.owner, self.run["id"], sources, retrieval,
                                         execution_id=self.control["execution_id"], stage="analyzing")
        if saved is None:
            raise GatewayError("本轮已停止。", 409, "explore_cancelled")
        self._finish_report(entities, sources, retrieval)

    def _on_response(self, response):
        self.control["response"] = response
        if self.control["stop"].is_set() and response is not None:
            response.abort()

    def _context(self, query=""):
        return ToolContext(stopped=self.control["stop"], on_response=self._on_response,
                           emit=lambda _event: None, query=query)

    def _check_selected_files(self):
        for item in self.run.get("context_files") or []:
            file_id = item.get("file_id")
            if file_id:
                self.repo.get_file(self.owner, self.run["session_id"], file_id)

    def _question(self):
        return self.run.get("resolved_question") or self.run["question"]

    def _select_entities(self):
        self.service._stage(self.owner, self.run["id"], "relation_select", self.control)
        question = self._question()
        payload = {"task": "relation_entity_selection", "stage": "relation_select",
                   "module": "relation", "question": question,
                   "as_of": self.run["time_policy"]["as_of"]}
        prompt = (
            "你是金融分析助手。只依据用户本轮问题和一般金融知识，先选最能解释原问题的一个具体实体；"
            "仅在第二个实体提供不同且必要的解释角度时才选两个。用户明确指定的比较对象优先保留。"
            "辨清公司、品牌、产品和发言人；若名称歧义会改变分析对象，先问一个简短澄清问题。"
            "此时没有搜索结果，选择理由不能声称最新合作、供货、控制关系已核实。"
            "只输出 JSON：{needs_clarification:boolean, question:string, "
            "entities:[{name:string,reason:string}]}。无需澄清时 question 为空且 entities 为1或2项；"
            "需要澄清时 question 为一个简短问题且 entities 为空。禁止输出检索计划、工具调用或报告。\n"
            "以下是本轮新版发现关联 Skill：\n" + self.skill["instructions"]
        )
        raw = self.service._invoke_model(payload, prompt, self.control,
            self.service._model_progress(self.owner, self.run["id"], self.control, "relation_select"))
        if not isinstance(raw, dict) or type(raw.get("needs_clarification")) is not bool:
            raise GatewayError("关联实体选择格式无效。", 502, "explore_invalid_intent")
        if raw["needs_clarification"]:
            question_text = str(raw.get("question") or "").strip()[:180]
            if not question_text:
                raise GatewayError("关联实体的澄清问题为空。", 502, "explore_invalid_intent")
            return {"needs_clarification": True, "question": question_text, "entities": [],
                    "truncated_count": 0}
        values = raw.get("entities")
        if not isinstance(values, list) or not values:
            raise GatewayError("未选定关联实体。", 502, "explore_invalid_intent")
        entities, seen = [], set()
        for item in values:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("reason"), str):
                raise GatewayError("关联实体缺少名称或原因。", 502, "explore_invalid_intent")
            name, reason = item["name"].strip()[:120], item["reason"].strip()[:500]
            if not name or not reason:
                raise GatewayError("关联实体缺少名称或原因。", 502, "explore_invalid_intent")
            key = _text_key(name)
            if key and key not in seen:
                entities.append({"name": name, "reason": reason})
                seen.add(key)
        if not entities:
            raise GatewayError("未选定关联实体。", 502, "explore_invalid_intent")
        return {"needs_clarification": False, "question": "", "entities": entities[:2],
                "truncated_count": max(0, len(entities) - 2)}

    def _await_clarification(self, selection):
        question = self._question()
        review = validate_intent({"needs_clarification": True,
            "interpreted_question": question,
            "summary": "关联实体名称存在歧义，请明确本轮要分析的对象。",
            "question": selection["question"], "limitations": [], "options": []}, [])
        revision = int(self.run.get("review_revision") or 0) + 1
        review["revision"] = revision
        saved = self.repo.transition(self.owner, self.run["id"], ACTIVE,
            status="awaiting_clarification", stage="awaiting_clarification",
            execution_id=self.control["execution_id"], fields={
                "phase": "relation_select", "review": review, "review_revision": revision,
                "model_progress": None, "skill_hash": self.skill_hash,
                "model_transport_trace": copy.deepcopy(self.control.get("model_transport_trace", []))})
        if saved is None:
            raise GatewayError("本轮已停止。", 409, "explore_cancelled")

    def _search_news(self, entities, retrieval):
        self.service._stage(self.owner, self.run["id"], "searching_web", self.control)
        spec = self.service.search_tools.get("web_search")
        search_limit = min(2, self.service.research_limits["tool_calls"],
                           spec.max_calls if spec is not None else 0)
        as_of = self.run["time_policy"]["as_of"]
        question = self._question()
        sources, by_url, by_title, by_content = [], {}, {}, {}
        for index, entity in enumerate(entities):
            self.service._check_stop(self.control)
            name = entity["name"]
            if index >= search_limit:
                retrieval["searches"].append({"entity": name, "status": "budget_skipped", "count": 0})
                continue
            query = _query(name, question, as_of)
            args = {"query": query, "count": 3, "freshness": _freshness(question, as_of)}
            started = time.monotonic()
            record = {"name": "web_search", "arguments": args, "started_at": timestamp(),
                      "initiator": "relation_lite", "entity": name}
            matched = []
            try:
                result = self.service.search_tools.execute("web_search", args,
                    self._context(query), call_count=index)
                self.service._check_stop(self.control)
                for item in (result.sources or [])[:3]:
                    url = _safe_source_url(item.get("url"))
                    title = str(item.get("title") or "").strip()[:300]
                    if not url or not title or not _matches_question(name, question, item):
                        continue
                    published = item.get("publishedAt")
                    if published:
                        date = _parse_datetime(published)
                        if date is None:
                            published = None
                        elif date > _as_of(as_of) + timedelta(minutes=5):
                            continue
                    identity = urldefrag(url).url.rstrip("/")
                    title_key = _text_key(title)
                    snippet = str(item.get("snippet") or "")[:1000]
                    content_key = _text_key(snippet[:180]) if len(snippet) >= 80 else ""
                    previous = by_url.get(identity) or by_title.get(title_key) or (
                        by_content.get(content_key) if content_key else None)
                    if previous is not None:
                        if name not in previous["entities"]:
                            previous["entities"].append(name)
                        if previous["id"] not in matched:
                            matched.append(previous["id"])
                        continue
                    source = {"id": f"S{len(sources) + 1}", "kind": "web_search",
                        "source": "web_search", "source_name": "联网搜索摘要",
                        "title": title, "summary": snippet,
                        "text_used": snippet or title, "url": url,
                        "published_at": published,
                        "retrieved_at": result.content.get("searchedAt") or timestamp(),
                        "content_kind": "search_snippet",
                        "provider_id": str(item.get("id") or "")[:100],
                        "search_query": query, "entities": [name]}
                    sources.append(source)
                    by_url[identity] = source
                    by_title[title_key] = source
                    if content_key:
                        by_content[content_key] = source
                    matched.append(source["id"])
                record.update(status="complete" if matched else "empty", result_count=len(matched))
            except GatewayError as exc:
                self.service._check_stop(self.control)
                record.update(status="failed", result_count=0, error=str(exc))
            finally:
                record["duration_seconds"] = round(time.monotonic() - started, 3)
                retrieval["tool_trace"].append(record)
                retrieval["searches"].append({"entity": name, "query": query,
                    "freshness": args["freshness"], "searched_at": record["started_at"],
                    "status": record.get("status", "failed"), "count": len(matched),
                    "source_ids": matched, "error": record.get("error")})
                self.control["response"] = None
        retrieval["selection"]["source_ids_by_entity"] = {
            entity["name"]: next((row.get("source_ids", []) for row in retrieval["searches"]
                                  if row.get("entity") == entity["name"]), []) for entity in entities}
        return sources[:6]

    def _read_news(self, sources, retrieval):
        if not sources:
            return
        reader = build_public_reader_tool(self.service.public_dns_mode)
        searches_used = sum(row["name"] == "web_search" for row in retrieval["tool_trace"])
        read_limit = max(0, min(6, reader.max_calls,
                                self.service.research_limits["tool_calls"] - searches_used))
        question = self._question()
        candidates = [item for item in sources
                      if _PRECISE.search(question) or len(item.get("summary") or "") < 220][:read_limit]
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
            record = {"name": "fetch_public_page", "arguments": args,
                      "started_at": timestamp(), "initiator": "relation_lite", "source_id": source["id"]}
            try:
                result = registry.execute("fetch_public_page", args, self._context(), call_count=index)
                self.service._check_stop(self.control)
                page = result.content
                body = page.get("text") if page.get("status") == "ok" else None
                if isinstance(body, str) and body.strip():
                    source.update(kind="public_page", source="fetch_public_page",
                        text_used=body[:4000], evidence_text=body[:4000],
                        content_kind="extracted_page_text",
                        source_name=page.get("publisher") or "网页原文")
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

    def _report_model(self, entities, sources, retrieval):
        question = self._question()
        payload = {"task": "research_report", "stage": "relation_lite_report", "module": "relation",
            "question": question, "as_of": self.run["time_policy"]["as_of"],
            "selected_entities": entities,
            "sources": [{"id": item["id"], "title": item["title"], "url": item["url"],
                "entities": item["entities"], "published_at": item.get("published_at"),
                "content_kind": item["content_kind"], "text": item["text_used"]} for item in sources],
            "retrieval": retrieval["searches"]}
        prompt = (
            "你是智能金融分析助手。围绕用户完整问题和本轮已选定的最多两个实体，直接写一份中文关联分析。"
            "先回答原问题和每个子问题，再简短说明为何选实体，按“实体发生了什么 → 如何影响问题主体或事件 → "
            "什么条件下成立”解释每条关键关联。只依据 sources 陈述具体新闻事实；选实体理由不是已核实的关系证据。"
            "新闻搜索摘要并非全文，只有 content_kind=extracted_page_text 才表示本轮读到了网页原文。"
            "不要把同期事件写成唯一因果，不要从共同出现推断合作、供货或法律责任。"
            "若只取得部分新闻，明确聚焦范围；某实体无新闻就承认没有该实体的新闻。"
            "全部搜索为空或失败时，简短说明未取得相关新闻，给出有限的一般机制解释，不声称关系已核实。"
            "通常总长600至1000字，材料少则缩短。正文具体事实标注 [S1] 等来源编号。"
            "只输出 JSON 对象：title 短标题，summary 核心结论，key_findings 1至3条短句，"
            "sections 1至3节且每节有 {heading,body,source_refs}。source_refs 只能是输入来源编号；"
            "不要输出关系图谱、研究计划、审稿、返工、推荐问题或其他工具调用。\n"
            "以下是本轮新版发现关联 Skill：\n" + self.skill["instructions"]
        )
        return self.service._invoke_model(payload, prompt, self.control,
            self.service._model_progress(self.owner, self.run["id"], self.control, "relation_lite_report"))

    @staticmethod
    def _fallback_report(sources):
        if sources:
            return {"title": "关联新闻线索", "summary": "模型未完成关联分析；以下仅列出本轮实际取得的新闻线索。",
                "key_findings": ["本轮尚未形成可核实的完整关联结论。"],
                "sections": [{"heading": "已取得的新闻", "body": "\n".join(
                    f"{item['id']} {item['title']}：{item['text_used'][:250]}" for item in sources),
                    "source_refs": [item["id"] for item in sources]}]}
        return {"title": "关联新闻不足", "summary": "本轮未取得相关新闻，无法核实所问的最新关联。",
            "key_findings": ["没有可引用的新闻；需要更多公开材料才能判断具体关系。"],
            "sections": [{"heading": "当前信息边界", "body": "本轮搜索没有取得可用来源，因此只能保留一般机制判断。",
                          "source_refs": []}]}

    def _finish_report(self, entities, sources, retrieval):
        self.service._check_stop(self.control)
        valid = False
        try:
            raw = self._report_model(entities, sources, retrieval)
            if not isinstance(raw, dict):
                raise GatewayError("报告格式无效。", 502, "explore_invalid_model_output")
            # The selected list is authoritative. A prose answer cannot silently
            # expand the graph or attach unsupported structured relation edges.
            raw = {"uncertainties": [], "limitations": [], **raw,
                   "entities": [item["name"] for item in entities],
                   "relationships": [], "recommendations": []}
            data = validate_model_report(raw, "relation", sources, set())
            if sources and not any(section["source_refs"] for section in data["sections"]):
                raise GatewayError("报告未引用本轮新闻。", 502, "explore_invalid_source_ref")
            valid = True
        except GatewayError as exc:
            self.service._check_stop(self.control)
            raw = {"uncertainties": [], "limitations": [], **self._fallback_report(sources),
                   "entities": [item["name"] for item in entities], "relationships": [],
                   "recommendations": []}
            data = validate_model_report(raw, "relation", sources, set())
            retrieval["report_fallback_reason"] = exc.code or "model_error"
        if sources:
            covered = retrieval["selection"].get("source_ids_by_entity") or {}
            missing = [item["name"] for item in entities if not covered.get(item["name"])]
            if missing:
                note = "本轮未取得" + "、".join(missing) + "的相关新闻；相关分析只基于其余已列来源。"
                if note not in data["summary"]:
                    data["summary"] = (data["summary"].rstrip("。") + "。" + note)[:5000]
                retrieval["selection"]["scope_note"] = note
        if retrieval["selection"]["truncated_count"]:
            note = "本轮仅聚焦" + "、".join(item["name"] for item in entities) + "，未覆盖问题中的全部候选对象。"
            if note not in data["summary"]:
                data["summary"] = (data["summary"].rstrip("。") + "。" + note)[:5000]
            retrieval["selection"]["entity_scope_note"] = note
        retrieval["model_trace"] = copy.deepcopy(self.control.get("model_transport_trace", []))
        self.service._stage(self.owner, self.run["id"], "saving", self.control)
        status = "succeeded" if valid and sources else "partial"
        warning = ("本轮未取得相关新闻，具体关联尚未核实。" if not sources else
                   "模型未完成关联分析；仅整理已取得的新闻线索。" if not valid else None)
        report = {"id": uid(), "run_id": self.run["id"], "module": "relation",
            "question": self.run["question"], "resolved_question": self.run.get("resolved_question"),
            "source_report_id": self.run.get("source_report_id"),
            "context_selection": self.run.get("context_selection"),
            "context_files": self.run.get("context_files") or [],
            "context_limitations": self.run.get("context_limitations") or [],
            "created_at": timestamp(), "as_of": self.run["time_policy"]["as_of"],
            "status": status, "mode": "current_research", "sources": sources,
            "selected_entities": copy.deepcopy(entities), "retrieval": retrieval,
            "skills_used": [{"name": "explore-relation", "content_hash": self.skill_hash}],
            "workflow_version": "relation-lite@1", "report_format": "relation_lite@1",
            "answer_status": "answered" if status == "succeeded" else "partial",
            "report_variant": "relation_analysis" if valid and sources else (
                "insufficient_news" if not sources else "generation_fallback"),
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
                execution_id=self.control["execution_id"], required_evidence_ids=required_evidence,
                required_selected_file_ids=required_files):
            raise GatewayError("研究材料已改变，本次报告未保存。", 409, "explore_evidence_changed")
