"""Bounded Explore orchestration over existing news and model adapters."""

from __future__ import annotations

import json
import copy
import logging
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..workbench.errors import GatewayError
from ..workbench.graph_runtime import GiteeProvider
from ..workbench.tool_registry import ToolContext
from ..workbench.skills import SkillRegistry
from .research_task import (build_research_task, search_queries, keywords, relevance,
                            qualified_source, search_result_acceptable, search_result_score)
from .contracts import (MAX_QUESTION, MAX_SOURCES, MODULES, PROMPT_VERSIONS,
                        explicit_judgment, historical_source_ids, nonempty,
                        parse_json_object, proposed_judgment, render_markdown,
                        safe_url, validate_intent, validate_model_report)
from .repository import ACTIVE, TERMINAL, ExploreRepository, MAX_CONTEXT_FILES, timestamp, uid
from .public_entries import parse_entry_catalog, entries_for_question
from .memory_context import MEMORY_INSTRUCTION, inject_memory_payload, memory_trace
from .event_report_json import parse_event_report_json


LOG = logging.getLogger(__name__)
EVENT_INITIAL_TIMEOUT_SECONDS = 23.0
RELATION_RUN_TIMEOUT_SECONDS = 90.0
# Socket/header idle waits; the run watchdog also bounds continuously streaming replies.
RELATION_MODEL_WAIT_SECONDS = {
    "relation_select": 20.0,
    "relation_lite_report": 45.0,
}
HISTORY_RUN_TIMEOUT_SECONDS = 90.0
HISTORY_MODEL_WAIT_SECONDS = {"history_initial": 20.0, "history_lite_report": 45.0}
EXPLORE_MODEL_CHOICES = frozenset({"GLM-5.3-Flash", "GLM-5.3"})


def event_brief_issues(raw):
    """Require the first-step report to move material caveats into its answers."""
    issues = []
    expanded = [key for key in ('event_overview', 'background', 'actors', 'drivers',
        'impact_paths', 'timeline', 'uncertainties', 'limitations') if raw.get(key)]
    if expanded:
        issues.append('首轮简报不展开这些字段：' + '、'.join(expanded) +
                      '。将影响答案的关键条件用一句话保留在核心发现或对应条目，再清空这些字段；不遗漏实质缺口。')
    if isinstance(raw.get('sections'), list) and len(raw['sections']) > 2:
        issues.append('首轮只保留1至2组直接相关的来源核实或必要解释；将进一步分析改为具体下一步问题。')
    sections = raw.get('sections') if isinstance(raw.get('sections'), list) else []
    if any(isinstance(section, dict) and section.get('heading') in
           {'重要不确定性', '信息缺口与限制', '事件背景与影响路径', '动因分析', '完整时间线'}
           for section in sections):
        issues.append('不要把已省略的独立防御性章节或完整分析重新放入sections；关键缺口内联到相应答案。')
    return issues
_URL = re.compile(r"https?://[^\s<>\"']+", re.I)
_WORDS = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}")
_COMMON = {"分析", "理解", "研究", "事件", "影响", "判断", "最新", "新闻", "历史", "公司", "企业",
           "近期", "发现", "关联", "什么", "是否", "如何", "今日", "这个", "可能", "为何", "验证",
           "问题", "情况", "相关", "后续", "主要", "哪些", "当前", "时间", "材料", "报告", "市场",
           "最近", "发生", "有什么", "消息", "快讯"}


def _keywords(question):
    return keywords(question)


def _relevance(item, words):
    return relevance(item, words)


def _judgment(question):
    match = re.search(r"(?m)^\s*(?:待验证判断|判断|假设|我认为)[:：]\s*([^\n]+)", question)
    if match and len(match.group(1).strip()) <= 1000 and explicit_judgment(match.group(1)):
        return match.group(1).strip()
    first_line = question.splitlines()[0].strip() if question.splitlines() else ""
    if re.match(r"^(?:新闻标题|新闻原文|原文摘录|来源)[:：]", first_line):
        first_line = ""
    if len(first_line) <= 1000 and explicit_judgment(first_line):
        return first_line
    if len(question) <= 1000 and explicit_judgment(question):
        return question
    return None


def _dt(value):
    try:
        date = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return date if date.tzinfo else date.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _search_query(question, context):
    # Compatibility helper: all subquestions live in ResearchTask/search_queries.
    queries = search_queries({"question": question, "context_snapshot": context, "module": "event"})
    return queries[0] if queries else ""


def _freshness(question, module):
    if module == "history":
        return "noLimit"
    if re.search(r"今天|今日|当天|近24小时|24小时内", question):
        return "oneDay"
    if re.search(r"本月|近一个月|最近一个月|一个月内|近30天|最近30天", question):
        return "oneMonth"
    if re.search(r"最新|最近|近期|本周|近一周|一周内", question):
        return "oneWeek"
    return "noLimit"


def _actionable_option(question, module):
    """Turn a model's 'shall we study X?' option into the request it offers."""
    match = re.fullmatch(r"\s*(?:是否)?以\s*(.+?)\s*为(?:本次)?研究对象[？?。.]?\s*", question, re.S)
    if not match:
        return question
    topic = match.group(1).strip(" ，,。？? ")
    if module == "event":
        return f"请分析{topic}，梳理事件概述、时间线、主体及影响路径。"[:1000]
    if module == "relation":
        return f"请分析{topic}涉及的主体关系、方向、机制及成立条件。"[:1000]
    return f"待验证判断：{topic}\n请根据真实历史材料作定性对照。"[:1000]


def _user_material_text(run):
    original = run["question"]
    current = run.get("resolved_question")
    if not current or current == original:
        return original[:4000]
    text = f"本轮最新研究请求（用户已选择或编辑；以此判断当前意图）：\n{current}"
    label = "\n\n初次提供的原始输入或新闻材料（未经独立核实；仅作背景，不代表用户仍未确定研究对象）：\n"
    text += label + original[:max(0, 4000 - len(text) - len(label))]
    return text[:4000]


class ExploreService:
    def __init__(self, workbench, news_service, config, provider=None):
        self.repository = ExploreRepository(config["DATA_DIR"])
        self.news_service = news_service
        self.search_tools = workbench.runtime.tools
        self.provider = provider or GiteeProvider({**config,
            "GITEE_AI_MAX_TOKENS": config.get("EXPLORE_MODEL_MAX_TOKENS", 8192),
            "GITEE_AI_REASONING_EFFORT": config.get("EXPLORE_MODEL_REASONING_EFFORT", "low")})
        self.relation_model = str(config.get("EXPLORE_RELATION_MODEL") or "GLM-5.3-Flash").strip() or "GLM-5.3-Flash"
        self.testing = bool(config.get("TESTING"))
        # Existing test fixtures exercise the original workflow unless they
        # explicitly opt into the two-step event path.
        self.event_lite_enabled = bool(config.get("EXPLORE_EVENT_LITE_ENABLED", not self.testing))
        self.relation_lite_enabled = bool(config.get("EXPLORE_RELATION_LITE_ENABLED", not self.testing))
        self.history_lite_enabled = bool(config.get("EXPLORE_HISTORY_LITE_ENABLED", not self.testing))
        if not self.legacy_fixture and not callable(getattr(self.provider, "events", None)):
            raise ValueError("Explore 需要支持 events/tool calls 的 Provider。")
        self.public_dns_mode = config.get("EXPLORE_PUBLIC_DNS_MODE", "system")
        if self.public_dns_mode not in {"system", "cloudflare_doh"}:
            raise ValueError("Explore 公网 DNS 模式无效。")
        self.public_entry_catalog = parse_entry_catalog(config.get('EXPLORE_PUBLIC_ENTRY_CATALOG', '[]'))
        self.research_skills = SkillRegistry(Path(__file__).with_name("skills"))
        self.data_skills = workbench.runtime.skills
        self.research_limits = {
            "model_rounds": min(8, max(2, int(config.get("EXPLORE_RESEARCH_ROUNDS", 5)))),
            "tool_calls": min(24, max(3, int(config.get("EXPLORE_TOOL_CALLS", 12)))),
            "context_chars": 100000, "write_reserve_seconds": 40,
        }
        self.timeout = float(config.get("EXPLORE_RUN_TIMEOUT_SECONDS", 600))
        self.research_limits["write_reserve_seconds"] = min(200, max(5, self.timeout / 3))
        self.max_concurrent = int(config.get("EXPLORE_MAX_CONCURRENT_RUNS", 2))
        self.news_limit = int(config.get("EXPLORE_NEWS_WINDOW", 100))
        if not 20 <= self.timeout <= 600 or not 1 <= self.max_concurrent <= 8 or not 1 <= self.news_limit <= 100:
            raise ValueError("Explore 运行上限配置无效。")
        self._lock = threading.RLock()
        self._active = {}
        self._closed = False
        prompt_dir = Path(__file__).with_name("prompts")
        self.prompts = {module: (prompt_dir / f"{module}_v4.txt").read_text(encoding="utf-8")
                        for module in MODULES}
        self.research_prompts = {module: (prompt_dir / ("event_v6.txt" if module == "event" else f"{module}_v5.txt")).read_text(encoding="utf-8") for module in MODULES}
        self.intent_prompt = (prompt_dir / "clarify_v3.txt").read_text(encoding="utf-8")

    @property
    def legacy_fixture(self):
        # Old token-only test doubles exercise the original lifecycle, not the research loop.
        return self.testing and not callable(getattr(self.provider, "events", None))

    def create_session(self, owner, body):
        title = body.get("title", "新探索")
        return self.repository.create_session(owner, nonempty(title, "探索标题", 120))

    def set_context(self, owner, session_id, body):
        if not isinstance(body, dict) or set(body) != {"module", "mode", "file_ids"}:
            raise GatewayError("上下文设置格式不正确。", 400)
        module = body["module"]
        if not isinstance(module, str) or module not in MODULES:
            raise GatewayError("研究模块不正确。", 400)
        return self.repository.set_context(owner, session_id, module, body["mode"], body["file_ids"])

    def rename_file(self, owner, session_id, file_id, body):
        if not isinstance(body, dict) or set(body) != {"name"}:
            raise GatewayError("文件名称格式不正确。", 400)
        name = nonempty(body["name"], "文件名称", 120)
        if name in {".", ".."} or re.search(r"[\\/\x00-\x1f<>:\"|?*]", name):
            raise GatewayError("文件名称包含不支持的字符。", 400)
        return self.repository.rename_file(owner, session_id, file_id, name)

    def rename_session(self, owner, session_id, body):
        return self.repository.rename_session(owner, session_id, nonempty(body.get("title"), "探索标题", 120))

    def delete_session(self, owner, session_id):
        # Remove the durable resources first. Late worker commits then fail their CAS.
        self.repository.delete_session(owner, session_id)
        with self._lock:
            controls = [control for (run_owner, _), control in self._active.items()
                        if run_owner == owner and control["session_id"] == session_id]
        for control in controls:
            self._stop(control)

    @staticmethod
    def _validate_key(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,150}", value):
            raise GatewayError("请为本次提交提供唯一请求标识。", 400)
        return value

    def create_run(self, owner, session_id, body, *, retry_of=None, previous=None):
        if not isinstance(body, dict):
            raise GatewayError("请求格式不正确。", 400)
        if set(body) - {"module", "question", "source_report_id", "source_run_id", "idempotency_key", "context_mode", "file_ids", "model"}:
            raise GatewayError("本轮只支持当前研究时点；请求包含不支持的参数。", 400, "explore_unsupported_parameter")
        if "model" in body and (not isinstance(body["model"], str) or body["model"] not in EXPLORE_MODEL_CHOICES):
            raise GatewayError("请选择支持的探索模型。", 400, "explore_invalid_model")
        module = body.get("module")
        if not isinstance(module, str) or module not in MODULES:
            raise GatewayError("请选择理解事件、发现关联或历史验证。", 400)
        question = nonempty(body.get("question"), "研究问题", MAX_QUESTION)
        if re.search(r"(?:javascript|data|file):", question, re.I):
            raise GatewayError("输入包含不安全链接；请粘贴新闻原文或使用普通 HTTP(S) 链接。", 400)
        source = body.get("source_report_id")
        if source is not None and not isinstance(source, str):
            raise GatewayError("来源报告标识不正确。", 400)
        source = source or None
        source_run = body.get("source_run_id")
        if source_run is not None and (not isinstance(source_run, str) or not source_run):
            raise GatewayError("来源研究记录标识不正确。", 400)
        key = self._validate_key(body.get("idempotency_key"))
        selection_override = None
        if "context_mode" in body or "file_ids" in body:
            if "context_mode" not in body or "file_ids" not in body:
                raise GatewayError("请同时提交上下文模式和文件选择。", 400)
            mode, file_ids = body["context_mode"], body["file_ids"]
            if not isinstance(mode, str) or mode not in {"auto", "manual"} or not isinstance(file_ids, list) or \
                    len(file_ids) > MAX_CONTEXT_FILES or any(not isinstance(item, str) for item in file_ids) or \
                    len(file_ids) != len(set(file_ids)) or (mode == "auto" and file_ids):
                raise GatewayError("研究文件选择不正确。", 400)
            selection_override = {"mode": mode, "file_ids": file_ids}
        now = timestamp()
        resolved = previous.get("resolved_question") if previous else None
        judgment = _judgment(resolved or question) if module == "history" else None
        stage = "queued"
        request = {"session_id": session_id, "module": module, "question": question,
                   "source_report_id": source, "source_run_id": source_run,
                   "retry_of": retry_of, "idempotency_key": key,
                   "context_override": selection_override}
        run = {"id": uid(), "session_id": session_id, "module": module, "question": question,
               "source_report_id": source, "source_run_id": source_run,
               "retry_of": retry_of, "status": stage, "stage": stage,
               "authorized_public_urls": entries_for_question(self.public_entry_catalog, resolved or question),
               "stage_history": [{"stage": stage, "at": now}], "created_at": now, "updated_at": now,
               "time_policy": {"mode": "current_research", "as_of": now,
                               "historical_search_scope": "bounded_web_search",
                               "market_data_available": False},
               "context_snapshot": [], "context_selection": None, "context_files": [], "context_limitations": [],
               "prompt_version": PROMPT_VERSIONS[module] if self.legacy_fixture else f"{module}@5", "intent_prompt_version": "clarify@3",
               "judgment": judgment, "proposed_judgment": None,
               "resolved_question": resolved, "clarification_history": [],
               "materials": [], "retrieval": {}, "review": None, "review_revision": 0,
               "confirmed_sources": [], "phase": "prepare", "skip_intent": False,
               "pre_report_limitations": [], "model_progress": None, "execution_id": uid(), "error": None}
        if "model" in body:
            # Keep legacy requests unchanged; only an explicit selection enters
            # the idempotency signature and this run's frozen model choice.
            request["model"] = body["model"]
            run["model"] = body["model"]
        if module == "event" and getattr(self, "event_lite_enabled", False) and not self.legacy_fixture:
            run.update(workflow_version="event-lite@1", prompt_version="event-lite@1",
                       phase="initial_explanation", initial_answer=None, feedback_choice=None)
        elif module == "relation" and self.relation_lite_enabled and not self.legacy_fixture:
            run.update(workflow_version="relation-lite@2", prompt_version="relation-lite@2",
                       phase="relation_select")
        elif module == "history" and self.history_lite_enabled and not self.legacy_fixture:
            run.update(workflow_version="history-lite@1", prompt_version="history-lite@1",
                       phase="history_initial", initial_answer=None)
        saved, created = self.repository.create_run(owner, request, run, self.max_concurrent,
                                                     selection_override=selection_override)
        if created:
            self._start(owner, saved["id"], session_id)
        return saved

    def feedback(self, owner, run_id, body):
        if not isinstance(body, dict) or set(body) != {"choice", "idempotency_key"}:
            raise GatewayError("请选择一个后续操作。", 400)
        choice = body.get("choice")
        if not isinstance(choice, str) or choice not in {"understood", "explain_more"}:
            raise GatewayError("反馈选项无效。", 400)
        key = self._validate_key(body.get("idempotency_key"))

        def mutate(run):
            if (run.get("module") != "event" or run.get("workflow_version") != "event-lite@1"
                    or run.get("status") != "awaiting_feedback" or not run.get("initial_answer")):
                raise GatewayError("本轮已处理该选择，请刷新查看结果。", 409, "explore_stale_feedback")
            run["feedback_choice"] = choice
            if choice == "understood":
                run.update(status="succeeded", stage="completed", phase="done", model_progress=None,
                           next_step_state="finished")
            else:
                run.update(status="queued", stage="queued", phase="event_deep_report",
                           execution_id=uid(), model_progress=None, error=None)

        saved, created = self.repository.apply_review_action(
            owner, run_id, "event_feedback", key, {"choice": choice}, mutate, self.max_concurrent)
        if created and choice == "explain_more":
            self._start(owner, run_id, saved["session_id"])
        return self.repository.get_run(owner, run_id)

    def finish(self, owner, run_id, body):
        if not isinstance(body, dict) or set(body) != {"idempotency_key"}:
            raise GatewayError("结束请求格式不正确。", 400)
        key = self._validate_key(body.get("idempotency_key"))
        run, session = self.repository.finish_event(owner, run_id, key)
        with self._lock:
            controls = [control for (run_owner, _), control in self._active.items()
                        if run_owner == owner and control["session_id"] == run["session_id"]]
        for control in controls:
            self._stop(control)
        return run, session

    def clarify(self, owner, run_id, body):
        if not isinstance(body, dict) or set(body) != {"idempotency_key", "revision", "question"} and set(body) != {"idempotency_key", "revision", "option_id"}:
            raise GatewayError("请填写澄清问题或选择一个方向。", 400)
        key = self._validate_key(body.get("idempotency_key"))
        revision = body.get("revision")
        if type(revision) is not int or revision < 1:
            raise GatewayError("澄清版本无效，请刷新后重试。", 400)
        if "question" in body:
            nonempty(body["question"], "澄清问题", 1000)
        else:
            nonempty(body["option_id"], "澄清选项", 40)

        def mutate(run):
            review = run.get("review") or {}
            if run["status"] not in {"awaiting_clarification", "awaiting_review"} or review.get("revision") != revision:
                raise GatewayError("澄清内容已更新，请刷新后重试。", 409, "explore_stale_review")
            if "option_id" in body:
                option = next((item for item in review["options"] if item["id"] == body["option_id"]), None)
                if option is None:
                    raise GatewayError("澄清选项不存在。", 400)
                resolved = _actionable_option(option["question"], run["module"])
                direct = True
            else:
                resolved = body["question"].strip()
                direct = False
            run["clarification_history"] = [*run.get("clarification_history", []),
                                            {"revision": revision, "question": resolved,
                                             "option_id": body.get("option_id"), "at": timestamp()}]
            run.update(status="queued", stage="queued", phase="prepare", execution_id=uid(),
                       resolved_question=resolved, judgment=_judgment(resolved) if run["module"] == "history" else None,
                       authorized_public_urls=entries_for_question(self.public_entry_catalog, resolved),
                       review=None, confirmed_sources=[], skip_intent=direct, model_progress=None,
                       pre_report_limitations=[], error=None)
            if run.get("workflow_version") == "relation-lite@2":
                run.update(prompt_version="relation-lite@2", phase="relation_select")

        saved, created = self.repository.apply_review_action(owner, run_id, "clarify", key,
                                                                {name: value for name, value in body.items() if name != "idempotency_key"},
                                                                mutate, self.max_concurrent)
        if created:
            self._start(owner, run_id, saved["session_id"])
        return self.repository.get_run(owner, run_id)

    def confirm(self, owner, run_id, body):
        if not isinstance(body, dict) or set(body) != {"idempotency_key", "revision", "source_ids"}:
            raise GatewayError("请确认研究材料。", 400)
        key = self._validate_key(body.get("idempotency_key"))
        revision, ids = body.get("revision"), body.get("source_ids")
        if type(revision) is not int or revision < 1:
            raise GatewayError("材料版本无效，请刷新后重试。", 400)
        if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_SOURCES or any(not isinstance(item, str) for item in ids) or len(ids) != len(set(ids)):
            raise GatewayError("请选择至少一条有效材料。", 400)

        def mutate(run):
            review = run.get("review") or {}
            if run["status"] != "awaiting_review" or review.get("revision") != revision:
                raise GatewayError("材料确认内容已更新，请刷新后重试。", 409, "explore_stale_review")
            allowed = {item["id"] for item in run.get("materials", [])}
            if not set(ids) <= allowed:
                raise GatewayError("所选材料已失效，请刷新后重试。", 400)
            resolved = review["interpreted_question"]
            if run["module"] == "history" and not _judgment(resolved) and self.legacy_fixture:
                raise GatewayError("请先明确待验证判断。", 409)
            run.update(status="queued", stage="queued", phase="report", execution_id=uid(),
                       prompt_version=PROMPT_VERSIONS[run["module"]],
                       resolved_question=resolved, model_progress=None,
                       authorized_public_urls=entries_for_question(self.public_entry_catalog, resolved),
                       judgment=_judgment(resolved) if run["module"] == "history" else None,
                       confirmed_sources=[item for item in run["materials"] if item["id"] in ids], error=None)
            if run.get("workflow_version") == "relation-lite@2":
                run.update(prompt_version="relation-lite@2", phase="relation_select",
                           confirmed_sources=[])

        saved, created = self.repository.apply_review_action(owner, run_id, "confirm", key,
                                                                {"revision": revision, "source_ids": ids},
                                                                mutate, self.max_concurrent)
        if created:
            self._start(owner, run_id, saved["session_id"])
        return self.repository.get_run(owner, run_id)

    def supply_input(self, owner, run_id, body):
        judgment = nonempty(body.get("judgment"), "待验证判断", 1000)
        run = self.repository.get_run(owner, run_id)
        if run["module"] != "history" or run["status"] != "awaiting_input":
            raise GatewayError("该分析记录不需要补充判断。", 409)
        resolved = f"待验证判断：{judgment}\n请根据真实历史材料作定性对照。"
        updated = self.repository.transition(owner, run_id, {"awaiting_input"}, status="queued", stage="queued",
                                             judgment=judgment, max_concurrent=self.max_concurrent,
                                             fields={"resolved_question": resolved, "phase": "prepare",
                                                     "authorized_public_urls": entries_for_question(self.public_entry_catalog, resolved),
                                                     "execution_id": uid(), "review": None, "model_progress": None,
                                                     "confirmed_sources": []})
        if updated is None:
            raise GatewayError("分析记录状态已改变，请刷新后重试。", 409)
        self._start(owner, run_id, run["session_id"])
        return self.repository.get_run(owner, run_id)

    def cancel(self, owner, run_id):
        existing = self.repository.get_run(owner, run_id)
        if existing["status"] in TERMINAL:
            return existing
        updated = self.repository.transition(owner, run_id, {"awaiting_input", "awaiting_clarification", "awaiting_review", "awaiting_feedback", "queued", "running"},
                                             status="cancelled", stage="cancelled",
                                             error="已停止本次分析；迟到的结果不会保存。",
                                             fields={"materials": [], "retrieval": {}, "review": None,
                                                     "confirmed_sources": []})
        with self._lock:
            control = self._active.get((owner, run_id))
        if control:
            self._stop(control)
        return {**updated, "report": None} if updated else self.repository.get_run(owner, run_id)

    def retry(self, owner, run_id, body):
        old = self.repository.get_run(owner, run_id)
        if old["status"] not in TERMINAL:
            raise GatewayError("当前分析尚未结束，不能重试。", 409)
        if not isinstance(body, dict) or set(body) - {"idempotency_key", "context_mode", "file_ids"}:
            raise GatewayError("重试请求格式不正确。", 400)
        request = {
            "module": old["module"], "question": old["question"],
            "source_report_id": old.get("source_report_id"),
            "source_run_id": old.get("source_run_id"),
            "idempotency_key": body.get("idempotency_key")}
        if "model" in old:
            request["model"] = old["model"]
        for key in ("context_mode", "file_ids"):
            if key in body:
                request[key] = body[key]
        return self.create_run(owner, old["session_id"], request, retry_of=run_id, previous=old)

    def _start(self, owner, run_id, session_id):
        run = self.repository.get_run(owner, run_id)
        control = {"stop": threading.Event(), "timed_out": threading.Event(), "response": None,
                   "session_id": session_id, "execution_id": run.get("execution_id"),
                   "owner": owner, "run_id": run_id, "model": run.get("model"),
                   "model_transport_trace": [],
                   "memory_input": copy.deepcopy(run.get("memory_input")),
                   "model_memory_trace": copy.deepcopy(run.get("model_memory_trace") or [])}
        with self._lock:
            if self._closed:
                self.repository.transition(owner, run_id, ACTIVE, status="interrupted", stage="interrupted",
                                           execution_id=control["execution_id"],
                                           error="服务关闭，本次分析没有自动重新执行。")
                return
            self._active[(owner, run_id)] = control
            thread = threading.Thread(target=self._execute, args=(owner, run_id, control), daemon=True,
                                      name=f"explore-{run_id[:8]}")
            control["thread"] = thread
            thread.start()

    @staticmethod
    def _stop(control):
        control["stop"].set()
        response = control.get("response")
        if response is not None:
            try:
                response.abort()
            except Exception:
                pass

    def _check_stop(self, control):
        if control["timed_out"].is_set():
            raise GatewayError("本次研究超过执行时间上限。", 504, "explore_timeout")
        if control["stop"].is_set():
            raise GatewayError("已取消本次分析。", 409, "explore_cancelled")

    def _stage(self, owner, run_id, stage, control):
        self._check_stop(control)
        updated = self.repository.transition(owner, run_id, ACTIVE, status="running", stage=stage,
                                             execution_id=control["execution_id"])
        if updated is None:
            raise GatewayError("本次分析已停止。", 409, "explore_cancelled")

    def _web_search(self, run, control):
        active_question = run.get("resolved_question") or run["question"]
        query = _search_query(active_question, run["context_snapshot"])
        if not query:
            return [], [], ["未能从输入中提取可用搜索词。"]
        primary = {"query": query, "freshness": _freshness(query, run["module"]), "count": 5}
        requests = [{"query": item, "freshness": _freshness(item, run["module"]), "count": 5}
                    for item in search_queries(run)[:3]] or [primary]
        records, sources, limitations, seen = [], [], [], set()
        index = 0
        while index < len(requests):
            self._check_stop(control)
            arguments = requests[index]
            record = {"query": arguments["query"], "freshness": arguments["freshness"],
                      "searched_at": timestamp(), "count": 0, "status": "empty"}

            def on_response(response):
                control["response"] = response
                if control["stop"].is_set() and response is not None:
                    response.abort()

            try:
                result = self.search_tools.execute(
                    "web_search", arguments,
                    ToolContext(control["stop"], on_response, lambda _event: None,
                                query=arguments["query"]), call_count=index)
                self._check_stop(control)
                content = result.content
                record["searched_at"] = content.get("searchedAt") or record["searched_at"]
                raw_sources = result.sources or []
                if len(raw_sources) > 5:
                    raw_sources = raw_sources[:5]
                # A targeted search result remains a lead when its short
                # snippet omits query words. Rank such leads behind stronger
                # matches, and reject only an explicit identity conflict.
                raw_sources = sorted(raw_sources, key=lambda item: search_result_score(
                    arguments["query"], item), reverse=True)
                usable, local_seen = 0, set()
                for item in raw_sources:
                    if not isinstance(item, dict):
                        continue
                    url = safe_url(item.get("url"))
                    title = str(item.get("title") or "").strip()[:300]
                    if not url or not title:
                        continue
                    published = item.get("publishedAt")
                    date = _dt(published) if published else None
                    if published and (date is None or date > _dt(run["time_policy"]["as_of"])):
                        continue
                    if not search_result_acceptable(arguments["query"], item):
                        continue
                    identity = url.split("#", 1)[0]
                    if identity in local_seen:
                        continue
                    local_seen.add(identity)
                    usable += 1
                    if identity in seen:
                        continue
                    seen.add(identity)
                    sources.append({"kind": "web_search", "title": title,
                                    "summary": str(item.get("snippet") or "")[:1000],
                                    "source": "web_search", "source_name": "联网搜索摘要",
                                    "published_at": published if date else None,
                                    "retrieved_at": record["searched_at"], "url": url,
                                    "provider_id": str(item.get("id") or "")[:100],
                                    "search_query": arguments["query"]})
                record["count"] = usable
                record["status"] = "complete" if usable else "empty"
                if not usable:
                    limitations.append(f"联网搜索“{arguments['query']}”未返回可用来源。")
            except GatewayError as exc:
                if control["stop"].is_set() or control["timed_out"].is_set():
                    raise
                record["status"] = "failed"
                record["error"] = str(exc)
                limitations.append(f"联网搜索“{arguments['query']}”失败：{str(exc)}")
            except Exception:
                if control["stop"].is_set() or control["timed_out"].is_set():
                    raise
                LOG.exception("Explore web search failed")
                record["status"] = "failed"
                record["error"] = "联网搜索暂时不可用。"
                limitations.append(f"联网搜索“{arguments['query']}”失败。")
            finally:
                control["response"] = None
            records.append(record)
            # The provider's recent filter excludes undated pages. A single
            # bounded noLimit retry can preserve them as undated leads.
            if (run["module"] != "history" and index == 0 and record["status"] == "empty"
                    and arguments["freshness"] != "noLimit"):
                requests.append({"query": query, "freshness": "noLimit", "count": 5})
            index += 1
        if any(item["published_at"] is None for item in sources):
            limitations.append("部分网页没有可核实发布日期，只能作为线索；不能据此确认最新性或充作历史案例。")
        if run["module"] == "history":
            cutoff = _dt(run["time_policy"]["as_of"]).timestamp() - 90 * 86400
            historical = [item for item in sources if item["published_at"] and _dt(item["published_at"]).timestamp() <= cutoff]
            if historical:
                historical_urls = {item["url"] for item in historical[:3]}
                sources = historical[:3] + [item for item in sources if item["url"] not in historical_urls]
        return sources, records, limitations

    def _news(self, run, control):
        as_of = _dt(run["time_policy"]["as_of"])
        active_question = run.get("resolved_question") or run["question"]
        title_match = re.search(r"(?m)^\s*新闻标题[:：]\s*([^\n]+)", active_question)
        topic = _search_query(active_question, run["context_snapshot"])
        words = _keywords(topic)
        urls = {match.rstrip(".,;，。；）)") for match in _URL.findall(active_question)}
        candidates, limitations, records, succeeded = [], [], [], 0
        for source in ("eastmoney", "cls"):
            self._check_stop(control)
            try:
                page = self.news_service.get_news(source=source, limit=self.news_limit, offset=0, q="")
                succeeded += 1
                record = {"source": source, "status": "complete", "fetched_at": page.get("cache", {}).get("fetched_at") or timestamp(),
                          "count": len(page.get("items", []))}
                records.append(record)
                if page.get("cache", {}).get("stale"):
                    limitations.append(f"{page.get('source_name') or source}使用较早缓存，检索时间以来源记录为准。")
                retrieved = page.get("cache", {}).get("fetched_at") or timestamp()
                for item in page.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    published = _dt(item.get("published_at"))
                    if not published or published > as_of:
                        continue
                    url = safe_url(item.get("url"))
                    score = _relevance(item, words)
                    if url in urls:
                        score += 100
                    if url not in urls and not qualified_source(active_question, item):
                        continue
                    candidates.append((score, item.get("published_at", ""), {
                        "kind": "news",
                        "title": str(item.get("title") or "未知标题")[:300],
                        "summary": str(item.get("summary") or "")[:1500],
                        "source": str(item.get("source") or source),
                        "source_name": str(item.get("source_name") or page.get("source_name") or source)[:100],
                        "published_at": item.get("published_at"), "retrieved_at": retrieved,
                        "url": url, "provider_id": str(item.get("id") or "")[:100]}))
            except GatewayError as exc:
                records.append({"source": source, "status": "failed", "fetched_at": timestamp(),
                                "count": 0, "error": str(exc)})
                limitations.append(f"{source} 新闻接口暂不可用：{str(exc)}")
            except Exception:
                LOG.exception("Explore news fetch failed for %s", source)
                records.append({"source": source, "status": "failed", "fetched_at": timestamp(),
                                "count": 0, "error": "新闻接口暂不可用。"})
                limitations.append(f"{source} 新闻接口暂不可用。")
        if urls and any(item[2]["url"] in urls for item in candidates):
            candidates = [item for item in candidates if item[2]["url"] in urls]
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        seen, sources = set(), []
        for _, _, item in candidates:
            identity = item["source"] + "|" + (item["provider_id"] or item["title"] + item["published_at"])
            if identity in seen:
                continue
            seen.add(identity)
            sources.append(item)
            if len(sources) >= (5 if not words else MAX_SOURCES):
                break
        if not sources:
            limitations.append("当前两个快讯窗口未找到与问题直接匹配的新闻；快讯窗口不等于完整新闻或历史库。")
        return sources, records, limitations, succeeded

    def _assemble_sources(self, run, web_sources, news_sources):
        by_url = {}
        for item in [*news_sources, *web_sources]:
            identity = item.get("url", "").split("#", 1)[0] if item.get("url") else (
                item["source"] + "|" + item["title"] + "|" + str(item.get("published_at")))
            if identity not in by_url:
                by_url[identity] = item
        external = list(by_url.values())
        requested_urls = {match.rstrip(".,;，。；）)") for match in _URL.findall(run["question"])}
        requested = [item for item in external if item.get("url") in requested_urls]
        web = [item for item in external if item["kind"] == "web_search"]
        news = [item for item in external if item["kind"] == "news"]
        priority = requested + web[:3] + news[:2]
        if run["module"] == "history":
            as_of = _dt(run["time_policy"]["as_of"])
            historical = [item for item in external if item.get("published_at") and
                          _dt(item["published_at"]) and (as_of - _dt(item["published_at"])).days >= 90]
            web_historical = [item for item in historical if item["kind"] == "web_search"]
            priority = requested + web_historical[:3] + priority
        chosen, chosen_ids = [], set()
        for item in [*priority, *external]:
            if id(item) not in chosen_ids:
                chosen.append(item)
                chosen_ids.add(id(item))
        external = chosen
        external = external[:MAX_SOURCES - 1]
        sources = [{"id": f"S{index + 1}", **item,
                    "content_kind": "search_snippet" if item["kind"] == "web_search" else "news_excerpt",
                    "text_used": item["summary"]} for index, item in enumerate(external)]
        user_text = _user_material_text(run)
        sources.append({"id": "U1", "kind": "user", "title": "用户提供的输入（未经独立核实）",
                        "summary": user_text, "source": "user", "source_name": "用户输入",
                        "published_at": None, "retrieved_at": run["created_at"], "url": None,
                        "provider_id": "", "content_kind": "user_input",
                        "text_used": user_text})
        return sources

    @staticmethod
    def _model_sources(sources):
        # The user-visible text_used is the exact excerpt passed to the model.
        return [{key: value for key, value in item.items() if key != "summary"} for item in sources]

    @staticmethod
    def _prompt_context(run):
        # Lineage metadata only guides auto file selection and short follow-up
        # searches. The model's entire prior research context is context_files.
        return []

    def _model_progress(self, owner, run_id, control, task):
        def update(length):
            self._check_stop(control)
            saved = self.repository.transition(
                owner, run_id, ACTIVE, execution_id=control["execution_id"],
                fields={"model_progress": {"task": task, "output_chars": length,
                                           "updated_at": timestamp()}})
            if saved is None:
                self._stop(control)
                raise GatewayError("本次分析已停止。", 409)
        return update

    def _invoke_model(self, payload, prompt, control, on_progress=None):
        while True:
            self._check_stop(control)
            try:
                return self._invoke_model_once(payload, prompt, control, on_progress)
            except GatewayError as exc:
                if not self._reserve_model_retry(control, exc, control.get('deadline'),
                                                 payload.get('stage') or payload.get('task') or 'model'):
                    raise

    def _reserve_model_retry(self, control, error, deadline, stage):
        """One transport retry for the whole run; never extend its deadline."""
        self._check_stop(control)
        if stage in RELATION_MODEL_WAIT_SECONDS or stage in HISTORY_MODEL_WAIT_SECONDS:
            return False
        transient = {'model_connect_timeout', 'model_header_timeout', 'model_request_timeout',
                     'model_stream_timeout', 'model_stream_interrupted', 'model_connection_failed'}
        if (error.code not in transient or control.get('model_retries')
                or deadline is None or deadline - time.monotonic() < 30):
            return False
        if callable(control.get('evidence_guard')):
            control['evidence_guard']()
        retries = [{'stage': stage, 'error_code': error.code, 'at': timestamp(),
                    'remaining_seconds': round(deadline - time.monotonic(), 3)}]
        if control.get('owner') and control.get('run_id'):
            saved = self.repository.transition(control['owner'], control['run_id'], ACTIVE,
                execution_id=control['execution_id'], fields={'model_retries': retries})
            if saved is None:
                self._stop(control)
                raise GatewayError('本次分析已停止。', 409, 'explore_cancelled')
        control['model_retries'] = retries
        return True

    def _memory_for_model(self, control, stage):
        """Resolve the frozen chain memory before every request, including retries."""
        self._check_stop(control)
        snapshot = control.get("memory_input")
        if control.get("owner") and control.get("run_id"):
            snapshot = self.repository.validate_memory(
                control["owner"], control["run_id"], execution_id=control.get("execution_id"))
        control["memory_input"] = copy.deepcopy(snapshot)
        try:
            metadata = memory_trace(snapshot)
        except (ValueError, TypeError):
            raise GatewayError("研究记忆无效，请重新读取研究记录。", 409,
                               "explore_memory_invalid") from None
        record = {"stage": stage, "at": timestamp(), **metadata}
        records = control.setdefault("model_memory_trace", [])
        records.append(record)
        if control.get("owner") and control.get("run_id"):
            saved = self.repository.transition(
                control["owner"], control["run_id"], ACTIVE,
                execution_id=control.get("execution_id"),
                fields={"model_memory_trace": copy.deepcopy(records)})
            if saved is None:
                self._stop(control)
                raise GatewayError("本次分析已停止。", 409, "explore_cancelled")
        return snapshot

    def _invoke_model_once(self, payload, prompt, control, on_progress=None):
        snapshot = self._memory_for_model(control, payload.get("stage") or payload.get("task") or "model")
        payload = inject_memory_payload(payload, snapshot)
        prompt = prompt + "\n" + MEMORY_INSTRUCTION
        if payload.get("task") == "research_report" and callable(control.get("evidence_guard")):
            control["evidence_guard"]()
        control["writer_model_calls"] = control.get("writer_model_calls", 0) + int(payload.get("task") == "research_report")
        content = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        if len(content) > (90000 if payload.get("task") == "research_report" else 30000):
            raise GatewayError("研究上下文过长，请缩短输入。", 400, "explore_context_too_long")
        parts, length = [], 0
        last_progress_at, saved_length = 0, 0

        def on_response(response):
            control["response"] = response
            if control["stop"].is_set() and response is not None:
                response.abort()

        provider = self._provider_for_run(control.get('deadline'), control,
                                          payload.get('stage') or payload.get('task'))
        model_wait = {**RELATION_MODEL_WAIT_SECONDS, **HISTORY_MODEL_WAIT_SECONDS}.get(payload.get('stage'))
        if isinstance(provider, GiteeProvider) and model_wait is not None:
            # _provider_for_deadline returns a copy, leaving other workflows unchanged.
            provider.connect_timeout = min(provider.connect_timeout, 3.0)
            provider.read_timeout = min(provider.read_timeout, model_wait)
        if payload.get('stage') == 'claim_review' and isinstance(provider, GiteeProvider):
            provider.request_options = {**provider.request_options,
                'max_tokens': min(int(provider.request_options.get('max_tokens', 4096)), 4096)}
        if isinstance(provider, GiteeProvider):
            provider.on_diagnostic = self._model_transport_observer(control, payload.get('stage') or payload.get('task') or 'model')
        stream = None
        try:
            if on_progress:
                on_progress(0)
            token_stream = getattr(provider, "json_tokens", None) or provider.tokens
            stream = token_stream([{"role": "system", "content": prompt},
                                   {"role": "user", "content": content}], provider.model,
                                  control["stop"], on_response)
            for token in stream:
                self._check_stop(control)
                if not isinstance(token, str):
                    raise GatewayError("模型返回了非文本内容。", 502, "explore_invalid_model_output")
                length += len(token)
                if length > (18000 if payload.get('stage') == 'claim_review' else 40000):
                    raise GatewayError("模型报告超过长度上限。", 502, "explore_invalid_model_output")
                parts.append(token)
                # Only public response-content counts leave the worker. Never
                # persist unvalidated JSON fragments or model reasoning. Bound
                # writes to the first chunk, once per second, and the final count.
                now = time.monotonic()
                if on_progress and length and (not saved_length or now - last_progress_at >= 1):
                    on_progress(length)
                    last_progress_at, saved_length = now, length
            self._check_stop(control)
            if on_progress and length != saved_length:
                on_progress(length)
        finally:
            if stream is not None and callable(getattr(stream, "close", None)):
                stream.close()
            control["response"] = None
        if payload.get("stage") in {"event_deep_lite", "history_lite_report"}:
            control.pop("report_json_repairs", None)
            result, repairs = parse_event_report_json("".join(parts))
            if repairs:
                control["report_json_repairs"] = repairs
            return result
        return parse_json_object("".join(parts))

    def _model_transport_observer(self, control, stage):
        records = control.setdefault('model_transport_trace', [])
        record = {'request': len(records) + 1, 'stage': stage}
        if control.get("model_memory_trace"):
            record["memory"] = {key: value for key, value in control["model_memory_trace"][-1].items()
                                if key.startswith("memory_")}
        records.append(record)
        def update(metadata):
            record.update(metadata)
            if control.get('owner') and control.get('run_id'):
                self.repository.transition(control['owner'], control['run_id'], ACTIVE,
                    execution_id=control['execution_id'], fields={'model_transport_trace': records})
        return update

    def _provider_for_deadline(self, deadline):
        # Keep the shared adapter/config immutable. A request waiting for HTTP headers
        # has no abortable response yet, so it also needs a bounded socket timeout.
        provider = self.provider
        if isinstance(provider, GiteeProvider):
            provider = copy.copy(provider)
            if deadline is not None:
                remaining = max(0.1, deadline - time.monotonic())
                provider.connect_timeout = min(provider.connect_timeout, remaining / 4)
                provider.read_timeout = min(provider.read_timeout, remaining - provider.connect_timeout)
        return provider

    def _provider_for_run(self, deadline, control, stage):
        """Apply a frozen run choice only to this request's Gitee adapter copy."""
        provider = self._provider_for_deadline(deadline)
        if isinstance(provider, GiteeProvider):
            selected = control.get("model")
            if selected is not None:
                if not isinstance(selected, str) or selected not in EXPLORE_MODEL_CHOICES:
                    raise GatewayError("探索模型选择无效。", 409, "explore_invalid_model")
                provider.model = selected
            elif stage in RELATION_MODEL_WAIT_SECONDS:
                provider.model = getattr(self, "relation_model", "GLM-5.3-Flash")
            if stage in HISTORY_MODEL_WAIT_SECONDS:
                provider.connect_timeout = min(provider.connect_timeout, 3.0)
                provider.read_timeout = min(provider.read_timeout, HISTORY_MODEL_WAIT_SECONDS[stage])
                provider.request_options = {**provider.request_options, "reasoning_effort": "low",
                    "max_tokens": min(int(provider.request_options.get("max_tokens", 8192)),
                                      600 if stage == "history_initial" else 3000)}
        return provider

    def _model(self, run, sources, historical_ids, retrieval, control, on_progress=None):
        payload = {"task": "report", "module": run["module"],
                   "question": run.get("resolved_question") or run["question"],
                   "judgment": run.get("judgment"), "as_of": run["time_policy"]["as_of"],
                   "context": self._prompt_context(run), "context_files": run.get("context_files") or [],
                   "sources": self._model_sources(sources),
                   "historical_source_ids": sorted(historical_ids), "retrieval": retrieval,
                   "news_capability": "bounded_web_search_plus_recent_eastmoney_cls_windows",
                   "market_data_available": False}
        return self._invoke_model(payload, self.prompts[run["module"]], control, on_progress)

    @staticmethod
    def _suggestions(materials, module):
        external = [item for item in materials if item["kind"] != "user"]
        if not external:
            return []
        angles = {
            "event": ("事件经过、时间线与主体", "可能动因及证据缺口", "影响路径与成立条件"),
            "relation": ("主体与关系方向", "传导机制与证据", "成立条件及反证"),
            "history": ("历史相似案例", "判断的支持条件", "判断的失效条件"),
        }[module]
        options = []
        for index, angle in enumerate(angles, 1):
            item = external[min(index - 1, len(external) - 1)]
            title = item["title"][:120]
            if module == "history":
                question = f"待验证判断：{title}是否会产生后续影响？请用真实历史材料对照{angle}。"
            else:
                question = f"请以“{title}”为具体研究对象，分析{angle}。"
            options.append({"id": f"source_{index}", "label": f"{title[:70]} · {angle}",
                            "question": question[:1000],
                            "reason": "从已取得材料的标题和摘录提出方向，具体事实仍需核对。",
                            "source_refs": [item["id"]]})
        return options

    def _fallback_review(self, run, materials, limitations):
        external = [item for item in materials if item["kind"] != "user"]
        return {"needs_clarification": True,
                "interpreted_question": (run.get("resolved_question") or run["question"])[:1000],
                "summary": (f"已取得 {len(external)} 条外部材料，请先核对研究对象和材料。" if external else
                            "本次没有取得可核对的外部材料；可粘贴原文并明确要研究的事件。"),
                "question": "您希望研究哪一件具体事件或判断？请补充或选择已检索的材料。",
                "options": self._suggestions(materials, run["module"]),
                "limitations": limitations[:8]}

    def _intent(self, run, materials, retrieval, limitations, control, on_progress=None):
        question = run.get("resolved_question") or run["question"]
        external = [item for item in materials if item["kind"] != "user"]
        link_only = bool(_URL.fullmatch(question.strip()))
        unmatched_link = link_only and all(item.get("url") != question.strip() for item in external)
        topic_query = _search_query(question, run["context_snapshot"])
        forced = (unmatched_link or (run["module"] == "history" and not _judgment(question)) or
                  bool(re.fullmatch(r"(?:[\u4e00-\u9fffA-Za-z0-9]{2,20}发生了什么|"
                                    r"(?:最近|最新)?有什么(?:最新|最近)?(?:新闻|消息|事件|快讯)|"
                                    r"(?:最近|最新)(?:的)?(?:新闻|消息|事件|快讯))", topic_query)) or
                  bool(re.search(r"^(?:这件事|这个|上述|它)(?:怎么样|如何|呢|？|\?)?$", question.strip())))
        payload = {"task": "clarify", "module": run["module"], "question": question,
                   "question_role": "latest_user_request",
                   "clarification_history": [{"revision": item["revision"], "question": item["question"],
                                              "option_id": item.get("option_id")}
                                             for item in run.get("clarification_history", [])[-4:]],
                   "as_of": run["time_policy"]["as_of"], "context": self._prompt_context(run),
                   "context_files": run.get("context_files") or [],
                   "sources": self._model_sources(materials), "retrieval": retrieval,
                   "explicit_judgment": bool(_judgment(question)) if run["module"] == "history" else None}
        try:
            review = validate_intent(self._invoke_model(payload, self.intent_prompt, control, on_progress), materials)
            for option in review["options"]:
                option["question"] = _actionable_option(option["question"], run["module"])
                if run["module"] == "history" and not _judgment(option["question"]):
                    option["question"] = (f"待验证判断：{option['label'][:140]}是否会产生后续影响？"
                                          "请根据真实历史材料作定性对照。")
        except GatewayError as exc:
            self._check_stop(control)
            review = self._fallback_review(run, materials, [*limitations, "意图预判暂不可用，请明确研究问题。"])
            review["limitations"] = [*review["limitations"][:7], str(exc)[:200]][:8]
        if forced:
            review["needs_clarification"] = True
            if not review["question"]:
                review["question"] = "请明确您想研究的具体事件或待验证判断。"
            if not review["options"]:
                review["options"] = self._suggestions(materials, run["module"])
        if review["needs_clarification"] and external and len(review["options"]) < 3:
            taken = {item["question"] for item in review["options"]}
            for suggestion in self._suggestions(materials, run["module"]):
                if suggestion["question"] not in taken and len(review["options"]) < 3:
                    suggestion["id"] = f"fallback_{len(review['options']) + 1}"
                    review["options"].append(suggestion)
                    taken.add(suggestion["question"])
        review["limitations"] = list(dict.fromkeys([*review["limitations"], *limitations]))[:8]
        return review

    def _prepare(self, owner, run_id, control):
        if not self.legacy_fixture:
            return self._prepare_research(owner, run_id, control)
        self._stage(owner, run_id, "searching_web", control)
        run = self.repository.get_run(owner, run_id)
        web_sources, searches, search_limitations = self._web_search(run, control)
        self._stage(owner, run_id, "retrieving_news", control)
        news_sources, news_records, news_limitations, _ = self._news(run, control)
        materials = self._assemble_sources(run, web_sources, news_sources)
        retrieval = {"searches": searches, "news": news_records}
        limitations = search_limitations + news_limitations
        active_question = run.get("resolved_question") or run["question"]
        urls = {match.rstrip(".,;，。；）)") for match in _URL.findall(active_question)}
        matched_urls = {item.get("url") for item in materials if item.get("url")}
        if urls - matched_urls:
            limitations.append("输入链接未在可信搜索或快讯结果中匹配；没有抓取链接正文。请粘贴原文。")
        self._check_stop(control)
        saved = self.repository.save_materials(owner, run_id, materials, retrieval,
                                               execution_id=control["execution_id"],
                                               stage="preparing_report" if run.get("skip_intent") else "checking_intent")
        if saved is None:
            raise GatewayError("本次材料核对已停止。", 409)
        if run.get("skip_intent"):
            updated = self.repository.transition(owner, run_id, ACTIVE, status="running", stage="preparing_report",
                                                 execution_id=control["execution_id"],
                                                 fields={"confirmed_sources": materials, "phase": "report",
                                                         "pre_report_limitations": limitations})
            if updated is None:
                raise GatewayError("本次材料核对已停止。", 409)
            self._report(owner, run_id, control)
            return
        review = self._intent(run, materials, retrieval, limitations, control,
                              self._model_progress(owner, run_id, control, "clarify"))
        revision = int(run.get("review_revision") or 0) + 1
        review["revision"] = revision
        if review["needs_clarification"]:
            self._check_stop(control)
            updated = self.repository.transition(owner, run_id, ACTIVE, status="awaiting_clarification",
                                                 stage="awaiting_clarification",
                                                 execution_id=control["execution_id"],
                                                 fields={"review": review, "review_revision": revision})
            if updated is None:
                raise GatewayError("本次材料核对已停止。", 409)
            return
        self._check_stop(control)
        updated = self.repository.transition(owner, run_id, ACTIVE, status="running", stage="preparing_report",
                                             execution_id=control["execution_id"],
                                             fields={"review": review, "review_revision": revision,
                                                     "resolved_question": active_question,
                                                     "confirmed_sources": materials, "phase": "report",
                                                     "pre_report_limitations": review["limitations"],
                                                     "judgment": _judgment(active_question) if run["module"] == "history" else None})
        if updated is None:
            raise GatewayError("本次分析已停止。", 409)
        self._report(owner, run_id, control)

    def _report(self, owner, run_id, control):
        if not self.legacy_fixture:
            return self._report_research(owner, run_id, control)
        self._stage(owner, run_id, "analyzing", control)
        run = self.repository.get_run(owner, run_id)
        sources = run["confirmed_sources"]
        limitations = list(dict.fromkeys([*(run.get("pre_report_limitations") or []),
                                          *((run.get("review") or {}).get("limitations") or []),
                                          *(run.get("context_limitations") or [])]))
        retrieval = run.get("retrieval") or {}
        historical_ids = historical_source_ids(sources, run["time_policy"]["as_of"])
        raw = self._model(run, sources, historical_ids, retrieval, control,
                          self._model_progress(owner, run_id, control, "report"))
        self._stage(owner, run_id, "validating", control)
        if run["module"] == "history":
            raw["judgment"] = run["judgment"]
        data = validate_model_report(raw, run["module"], sources, historical_ids)
        if run["module"] == "history":
            if not historical_ids:
                limitations.append("确认使用的材料中没有足够的有日期历史材料，不能据此验证历史判断。")
            limitations.append("定性对照，未进行行情验证；未完成严格历史时点验证。")
        if not any(item["source_refs"] for item in data["sections"]):
            limitations.append("报告章节没有可引用的确认材料，结论只能作为待核实分析。")
        if run["context_snapshot"] and run["context_snapshot"][-1].get("ancestors_truncated"):
            limitations.append("来源报告祖先超过 4 层；本次只纳入最近 4 份，较早上下文未用于分析。")
        if not any(item["kind"] != "user" for item in sources):
            limitations.append("本次只有用户输入，缺少独立新闻来源；内容未经独立核实。")
        data["limitations"] = list(dict.fromkeys(data["limitations"] + limitations))
        status = "partial" if (data["limitations"] or (run["module"] == "history" and not data["cases"])) else "succeeded"
        self._stage(owner, run_id, "saving", control)
        report = {"id": uid(), "run_id": run_id, "module": run["module"], "question": run["question"],
                  "resolved_question": run.get("resolved_question"),
                  "clarification_history": run.get("clarification_history", []),
                  "confirmed_sources": sources,
                  "context_selection": run.get("context_selection"),
                  "context_files": run.get("context_files") or [],
                  "context_limitations": run.get("context_limitations") or [],
                  "as_of": run["time_policy"]["as_of"], "created_at": timestamp(),
                  "source_report_id": run.get("source_report_id"), "status": status,
                  "mode": "qualitative_no_market_validation" if run["module"] == "history" else "research",
                  "sources": sources, "retrieval": retrieval, **data}
        report["markdown"] = render_markdown(report)
        self._check_stop(control)
        self.repository.complete(owner, run_id, report, status, execution_id=control["execution_id"])

    def _prepare_research(self, owner, run_id, control):
        """Keep clarification lifecycle only when the research object is ambiguous."""
        run = self.repository.get_run(owner, run_id)
        task = build_research_task(run)
        goal = task['resolved_goal'].strip()
        vague = bool(re.fullmatch(r'(?:[\u4e00-\u9fffA-Za-z0-9]{2,20}发生了什么|(?:最近|最新)?有什么(?:新闻|消息)|(?:这件事|这个|它)(?:怎么样|如何|呢)?)[？?。]?', goal))
        if vague and not run.get('skip_intent'):
            self._stage(owner, run_id, 'searching_web', control)
            materials, searches, limitations = self._web_search(run, control)
            materials = self._assemble_sources(run, materials, [])
            retrieval = {'searches': searches, 'news': []}
            self.repository.save_materials(owner, run_id, materials, retrieval, execution_id=control['execution_id'])
            review = self._intent(run, materials, retrieval, limitations, control)
            review['needs_clarification'] = True
            review['question'] = review.get('question') or '请明确要研究的具体事件。'
            revision = int(run.get('review_revision') or 0) + 1
            review['revision'] = revision
            self.repository.transition(owner, run_id, ACTIVE, status='awaiting_clarification', stage='awaiting_clarification',
                execution_id=control['execution_id'], fields={'review': review, 'review_revision': revision, 'research_task': task})
            return
        saved = self.repository.transition(owner, run_id, ACTIVE, status='running', stage='preparing_report',
            execution_id=control['execution_id'], fields={'phase': 'report', 'research_task': task,
            'resolved_question': goal, 'judgment': (_judgment(goal) or goal[:1000]) if run['module'] == 'history' else None})
        if saved is None:
            raise GatewayError('本次研究已停止。', 409)
        self._report_research(owner, run_id, control)

    def _report_research(self, owner, run_id, control):
        from .research_runtime import ResearchRuntime, WORKFLOW_VERSION, PROMPT_VERSION, validate_brief
        from .claim_review import apply_claim_review
        from .historical_eligibility import eligible_historical_ids
        from .evidence import EvidenceSession
        from .report_checkpoint import build_checkpoint_report
        self._stage(owner, run_id, 'analyzing', control)
        run = self.repository.get_run(owner, run_id)
        runtime = ResearchRuntime(self, owner, run, control)
        research = runtime.run_research()
        research_actions = []
        for trace in research['retrieval'].get('tool_trace', []):
            result = trace.get('result') or {}
            outcome = result.get('result') or {}
            research_actions.append({'tool': trace.get('name'), 'arguments': trace.get('arguments'),
                'status': trace.get('status'), 'evidence_count': len(result.get('evidence') or []),
                'outcome': {k: outcome[k] for k in ('status', 'reason', 'body_not_extracted', 'coverage') if k in outcome}})
        brief, sources = research['brief'], research['sources']
        supported_claims = [claim for claim in brief['claims'] if claim['support_status'] == 'traceable']
        # Authorization is checked before every model call, including semantic review.
        guarded_ids = {ref['evidence_id'] for claim in supported_claims for ref in claim['supports']}
        def guard_evidence():
            selected_ids = {x['evidence_id'] for x in EvidenceSession(self.repository, owner, run,
                execution_id=control['execution_id']).selected_catalog()}
            if set(research['selected_evidence_ids']) - selected_ids:
                raise GatewayError('所选研究文件已移除，请重新运行以更新授权上下文。', 409)
            live_ids = {x['evidence_id'] for x in self.repository.active_evidence_sources(
                owner, run_id, execution_id=control['execution_id'])}
            if guarded_ids - live_ids:
                raise GatewayError('研究材料已移除，本次不再向模型发送缓存证据。请重新运行。', 409)
        control['evidence_guard'] = guard_evidence
        claim_reviews = []
        if supported_claims and brief['answer_status'] != 'unanswered':
            audit = self._invoke_model({'task': 'research_report', 'stage': 'claim_review',
                'research_task': research['task'], 'research_brief': {**brief, 'claims': supported_claims, 'validation_notes': []},
                'sources': [{k: source.get(k) for k in ('id', 'title', 'url', 'content_kind', 'kind')}
                            for source in sources if source['evidence_id'] in guarded_ids]},
                '''你是写作前的主张证据审阅器。每个主张逐个分句与supports.quote逐字证据对照，不能把traceable当作语义成立。
不要使用记忆或来源标题补事实。只允许原主张id和原支持序号；删除无依据角色、数字、日期、因果、责任归属。
披露风险不能推出承担责任；产品定义涉及某主体不能变成该主体主导；媒体转述合同不能变成读过合同原文。
搜索摘要须保持“媒体摘要称”；公司披露须保持“公司披露”。来源类型以sources.content_kind为准。
本轮没找到某材料只是检索范围的局限，不能从一篇评论推断该材料不存在。不能用conditions或不确定性夹带未支持的新断言。
approve仅保留原text；narrow将其收窄为证据足以支持的独立短句，最多300字；reject拒绝。type只能保持或降低断言强度，不能从sourced_statement/inference/hypothesis提升为fact。
为每个原traceable主张给出一条审阅结果；为每个子问题给出coverage。question_ids关联被保留主张真正回答的子问题。
旧缺口仍须保留；媒体摘要不足以回答合同真实条款和个案责任时至多partial。无支持则unanswered。
只输出JSON且不增加字段：{"claim_reviews":[{"id":"C1","decision":"approve|narrow|reject","text":"保留或收窄的主张","type":"fact|sourced_statement|inference|hypothesis","support_indices":[0],"question_ids":["Q1"],"reason":"简短原因"}],"coverage":[{"question_id":"Q1","status":"answered|partial|unanswered","gap":"尚缺什么","next_action":"如何补足"}]}。
reject也给全字段，text/type可沿用原值，support_indices/question_ids可空。支持序号从0开始，仅引用原supports。''',
                control, self._model_progress(owner, run_id, control, 'claim_review'))
            guard_evidence()
            reviewed_raw, claim_reviews = apply_claim_review(brief, research['task'], audit)
            brief = validate_brief(reviewed_raw, research['task'], runtime.visible)
            supported_claims = [claim for claim in brief['claims'] if claim['support_status'] == 'traceable']
            guarded_ids = {ref['evidence_id'] for claim in supported_claims for ref in claim['supports']}
            research['historical_ids'] = eligible_historical_ids(supported_claims, research['task']['as_of'])
            self.repository.transition(owner, run_id, ACTIVE, execution_id=control['execution_id'],
                                       fields={'claim_reviews': claim_reviews, 'research_brief': brief})
        support_texts = {}
        for claim in supported_claims:
            for support in claim['supports']:
                if not support['background_only']:
                    support_texts.setdefault(support['source_ref'], []).append(support['quote'])
        # Writer receives only ledger-supported excerpts; collecting a source is not permission
        # to turn every unrelated sentence in it into a new report assertion.
        writer_sources = [{**source, 'summary': '\n'.join(dict.fromkeys(support_texts[source['id']])),
                           'text_used': '\n'.join(dict.fromkeys(support_texts[source['id']]))}
                          for source in sources if source['id'] in support_texts]
        frozen_brief = {**brief, 'claims': supported_claims,
                        'unsupported_claim_ids': [c['id'] for c in brief['claims'] if c['support_status'] != 'traceable']}
        # A deterministic, reviewed ledger is safe to retain even if later prose
        # writing or draft review exhausts the external model's time budget.
        try:
            checkpoint = build_checkpoint_report(run, research, brief, writer_sources, claim_reviews)
        except GatewayError:
            # An optional short checkpoint must not reject an otherwise valid
            # full report (for example when its six findings exceed the limit).
            checkpoint = None
        if checkpoint:
            control['checkpoint_evidence_ids'] = set(guarded_ids)
            control['checkpoint_file_ids'] = set((run.get('context_selection') or {}).get('file_ids', []))
            control['report_checkpoint'] = checkpoint
        sources = [{**source, 'text_used': '\n\n'.join(dict.fromkeys(support_texts.get(source['id'], []))),
                    'evidence_usage': 'cited' if source['id'] in support_texts else 'retrieved_only'}
                   for source in sources]
        draft_reviews = []
        inline_coverage_question_ids = []
        # Unread old AI reports never enter the writer's evidence set.
        methods = ['引用与片段已做结构校验；其语义支持强度仍需审阅。',
                   '搜索摘要、正文片段、结构化数据分别标记；相同内容哈希已标记同源组；独立性仍需按原始出处审阅。']
        if claim_reviews:
            methods.append('写作前已对主张逐项进行模型语义审阅并锁定原始支持片段；此审阅不等同于独立事实核验。')
        if run['module'] == 'history':
            methods.append('本轮是当前时点的历史资料研究；未执行严格当时信息实验或市场因果回测。')
        if brief['answer_status'] == 'unanswered':
            gaps = brief['blocking_gaps'] or ['本轮没有取得足以回答核心问题的可追溯材料。']
            raw = {'title': '研究未完成：' + research['task']['resolved_goal'][:120],
                   'summary': '本次研究尚不能回答核心问题。' + '；'.join(gaps)[:2800],
                   'key_findings': ['已执行 %s 次工具请求，未将缺失证据写成已核实结论。' % research['budget']['tool_calls']],
                   'uncertainties': [], 'limitations': gaps[:8], 'recommendations': [],
                   'sections': [{'heading': '尚待回答的问题', 'body': '\n'.join(x['question'] + '：' + x['gap'] for x in brief['coverage'])[:8000], 'source_refs': []}],
                   'event_overview': '', 'background': '', 'actors': [], 'drivers': [], 'impact_paths': [], 'timeline': [],
                   'entities': [], 'relationships': [], 'judgment': run.get('judgment') or research['task']['resolved_goal'][:1000],
                   'conclusion': '未取得可追溯的历史对照，无法完成本次验证。', 'cases': []}
            if run['module'] == 'event':
                raw.update(key_findings=['本轮尚未取得足以回答当前问题的可引用材料。'],
                    limitations=[], uncertainties=[],
                    sections=[{'heading': '消息核实与来源',
                        'body': '本轮已检索相关线索，仍需补齐能够直接回答问题的原始材料。', 'source_refs': []}],
                    recommendations=[{'question': row['question'], 'module': 'event',
                        'reason': row.get('next_action') or '补齐直接回答这个问题的原始材料。'}
                        for row in brief['coverage'][:2]])
            if research['stop_reason'] == 'ledger_timeout':
                # Retrieval succeeded, but no complete, validated claim ledger was
                # returned. Keep that distinction without treating snippets as facts.
                raw.update(
                    summary='已保留本轮检索材料；证据归纳阶段超时，尚未形成可核验的答案。',
                    key_findings=['已保留本轮检索材料；证据归纳阶段超时，尚未形成可核验的答案。'],
                    sections=[{'heading': '本轮进展',
                        'body': '已完成的检索与授权材料读取记录均已保留，可沿当前问题继续研究。',
                        'source_refs': []}])
        else:
            # Freeze the evidence/claim ledger before writing. Writer has no tool definitions.
            payload = {'task': 'research_report', 'module': run['module'], 'research_task': research['task'],
                       'question': research['task']['resolved_goal'], 'as_of': run['time_policy']['as_of'],
                       'judgment': run.get('judgment') or research['task']['resolved_goal'][:1000],
                       'context': [], 'context_files': [],
                       'sources': [{k: value for k, value in source.items() if k not in {'paragraphs', 'text', 'locations'}}
                                   for source in writer_sources],
                       'research_brief': frozen_brief, 'historical_source_ids': sorted(research['historical_ids']),
                       'method_notes': methods, 'stop_reason': research['stop_reason'],
                       'research_actions': research_actions, 'retrieved_source_count': len(sources),
                       'cited_source_count': len(writer_sources),
                       'report_format': 'event_brief@1' if run['module'] == 'event' else 'research'}
            prompt = self.research_prompts[run['module']] + '\n' + self.research_skills.load('explore-' + run['module'])['instructions']
            prompt += ('\n首轮理解事件写成紧凑简报，通常300至600字，简单问题更短；只用1至2组必要解释，不凑字数。'
                       '核心发现后直接引出具体下一步；关键未知用一句话贴着对应答案，不独立铺防御性章节。'
                       if run['module'] == 'event' else '\n正文聚焦600至1000字、1至3节；缺证据时更短。')
            prompt += '''\n输出JSON总长不超过5000字符。避免在概述、章节与背景重复同一内容。
本轮使用 research_brief 冻结账本写报告。来源层级以 content_kind 为准，已取得正文的来源不能再称仅摘要。
只能将 traceable 主张按其 fact/sourced_statement/inference/hypothesis 身份使用；unsupported不能变成确定结论。
摘要不能强于主张；相互冲突的主张保留条件与双方出处。来源/quote哈希校验只保证可追溯，不能据此声称事实已独立核实。
逐句内容必须同时符合主张及supports.quote；不得从背景或不确定性字段夹带未获支持的主体、角色、数字、日期或责任归属。
检索过程以research_actions为准。区分已查过但没有相关条款、完全未查、仅导航页metadata_only；区分检索到的材料数和进入引用的材料数，不能说只有一份材料就否认其他检索。
按全部子问题先直接回答，再解释证据和缺口。来源引用用本轮S编号。文档与长期主题可省无意义的时间线/动因（数组为空）。
输出只包含原模块规定字段；不输出研究中间过程。报告内容必须与answer_status和blocking_gaps一致。'''
            raw = self._invoke_model(payload, prompt, control, self._model_progress(owner, run_id, control, 'report'))
            # One repair shared by schema and semantic review; no unbounded repair loop.
            for attempt in range(2):
                issues = []
                try:
                    validate_model_report(raw, run['module'], writer_sources, research['historical_ids'],
                                          compact_event=run['module'] == 'event')
                    if run['module'] == 'event':
                        issues.extend(event_brief_issues(raw))
                except GatewayError as exc:
                    issues.append(str(exc))
                if not issues:
                    audit = self._invoke_model({'task': 'research_report', 'stage': 'review', 'module': run['module'], 'draft': raw,
                        'research_task': research['task'], 'research_brief': frozen_brief,
                        'research_actions': research_actions, 'retrieved_source_count': len(sources),
                        'cited_source_count': len(writer_sources)},
                        '你是研究稿审阅器。仅依据冻结主张及其逐字支持片段检查稿件。不要使用记忆补事实。'
                        '检查摘要比证据更强、来源说法变事实、业务分工推责任、无依据数字/日期、遗漏核心子问题、把未读原文说成已读。'
                        '逐句核对原quote，不把claim.text本身当证据；披露主体不等于责任承担主体，涉及产品定义不等于主导。背景、局限、不确定性中的断言也须有quote支持。'
                        '检索过程描述对照research_actions；已经查过公告但未找到条款，不能写没查公告；引用只有一份不等于只检索到一份。'
                        '未能回答而如实写缺口不是错误。只返回JSON {"issues":["具体有实质问题的字段及原因"]}，最多6条；'
                        '无实质问题返回空数组。不要因措辞风格要求改稿，不输出思维过程。'
                        '对于event模块，再返回inline_coverage_question_ids字符串数组：逐项核对research_brief.coverage中partial/unanswered的gap，'
                        '只有key_findings或sections已完整交代该子问题的全部实质缺口时才列出该question_id；'
                        '仅提及问题、只交代部分缺口、表达相反含义均不能列入。不确定则不列入。此标记仅用于避免在正文再次重复相同缺口。', control,
                        self._model_progress(owner, run_id, control, 'review'))
                    if not isinstance(audit.get('issues'), list) or len(audit['issues']) > 6 or any(not isinstance(x, str) or len(x)>1200 for x in audit['issues']):
                        raise GatewayError('报告复核返回格式无效。', 502)
                    issues = audit['issues']
                    if run['module'] == 'event':
                        covered = audit.get('inline_coverage_question_ids', [])
                        pending_ids = {row['question_id'] for row in brief['coverage']
                                       if row['status'] in {'partial', 'unanswered'}}
                        if (not isinstance(covered, list) or len(covered) > len(pending_ids)
                                or any(not isinstance(x, str) or x not in pending_ids for x in covered)
                                or len(set(covered)) != len(covered)):
                            raise GatewayError('报告复核返回的缺口覆盖标记无效。', 502)
                        # Never trust a marker from a draft that still requires repair.
                        inline_coverage_question_ids = covered if not issues else []
                draft_reviews.append({'attempt': attempt + 1, 'issues': issues})
                self.repository.transition(owner, run_id, ACTIVE, execution_id=control['execution_id'],
                                           fields={'draft_reviews': draft_reviews})
                if not issues:
                    break
                if attempt == 1:
                    raise GatewayError('报告复核仍发现无依据或引用问题，本次未保存报告。', 502, 'explore_review_failed')
                payload['repair'] = {'issues': issues, 'draft': raw}
                raw = self._invoke_model(payload, prompt + '\n按复核问题修正草稿；不能补造事实。删掉无法支持的扩写，明确保留缺口。', control,
                                         self._model_progress(owner, run_id, control, 'repair'))
        self._stage(owner, run_id, 'validating', control)
        if run['module'] == 'history':
            raw['judgment'] = run.get('judgment') or research['task']['resolved_goal'][:1000]
        data = validate_model_report(raw, run['module'], writer_sources, research['historical_ids'],
                                     allow_incomplete=brief['answer_status'] == 'unanswered',
                                     compact_event=run['module'] == 'event')
        status = 'succeeded' if brief['answer_status'] == 'answered' else 'partial'
        data.update(answer_status=brief['answer_status'], blocking_gaps=brief['blocking_gaps'],
                    method_notes=methods, coverage=brief['coverage'], claims=brief['claims'], conflicts=brief['conflicts'],
                    validation_notes=brief['validation_notes'], research_validation_notes=research['brief']['validation_notes'], claim_reviews=claim_reviews,
                    claim_review_version='claim-review@1', draft_reviews=draft_reviews, report_variant='insufficient_evidence' if brief['answer_status'] == 'unanswered' else 'research')
        self._stage(owner, run_id, 'saving', control)
        live = self.repository.get_run(owner, run_id)
        report = {'id': uid(), 'run_id': run_id, 'module': run['module'], 'question': run['question'],
                  'resolved_question': run.get('resolved_question'), 'clarification_history': run.get('clarification_history', []),
                  'confirmed_sources': writer_sources, 'context_selection': run.get('context_selection'),
                  'context_files': run.get('context_files') or [], 'context_limitations': run.get('context_limitations') or [],
                  'as_of': run['time_policy']['as_of'], 'created_at': timestamp(), 'source_report_id': run.get('source_report_id'),
                  'status': status, 'mode': 'current_research', 'sources': sources, 'retrieval': research['retrieval'],
                  'research_task': research['task'], 'skills_used': research['skills_used'], 'workflow_version': WORKFLOW_VERSION, 'tool_schema_hashes': live.get('tool_schema_hashes'),
                  'public_dns_mode': self.public_dns_mode,
                  'authorized_public_urls': run.get('authorized_public_urls', []),
                  'research_prompt_version': PROMPT_VERSION, 'writer_prompt_version': 'event@8' if run['module'] == 'event' else run['module']+'@6',
                  'report_format': 'event_brief@1' if run['module'] == 'event' else 'research',
                  'inline_coverage_question_ids': inline_coverage_question_ids,
                  'research_budget': {**(live.get('research_budget') or {}), 'writer_model_calls': control.get('writer_model_calls', 0),
                                      'model_retries': len(control.get('model_retries', [])), 'token_usage': None},
                  'model_retries': live.get('model_retries', []),
                  'model_transport_trace': live.get('model_transport_trace', []),
                  'research_stop_reason': research['stop_reason'], **data}
        report['markdown'] = render_markdown(report)
        self._check_stop(control)
        self.repository.complete(owner, run_id, report, status, execution_id=control['execution_id'])

    def _complete_checkpoint(self, owner, run_id, control, reason):
        checkpoint = control.get('report_checkpoint')
        if not checkpoint or control['stop'].is_set():
            return False
        try:
            live = self.repository.get_run(owner, run_id)
            if live['status'] not in ACTIVE or live.get('execution_id') != control['execution_id']:
                return False
            if callable(control.get('evidence_guard')):
                control['evidence_guard']()
            report = copy.deepcopy(checkpoint)
            report.update(completion_warning=reason, created_at=timestamp(),
                public_dns_mode=live.get('public_dns_mode', getattr(self, 'public_dns_mode', 'system')),
                tool_schema_hashes=copy.deepcopy(live.get('tool_schema_hashes') or {}),
                draft_reviews=copy.deepcopy(live.get('draft_reviews') or []),
                model_transport_trace=copy.deepcopy(control.get('model_transport_trace', [])),
                model_retries=copy.deepcopy(control.get('model_retries', [])),
                research_budget={**(live.get('research_budget') or {}),
                    'writer_model_calls': control.get('writer_model_calls', 0),
                    'model_retries': len(control.get('model_retries', []))})
            report['markdown'] = render_markdown(report)
            return self.repository.complete(owner, run_id, report, 'partial',
                execution_id=control['execution_id'],
                required_evidence_ids=control['checkpoint_evidence_ids'],
                required_selected_file_ids=control['checkpoint_file_ids'])
        except GatewayError:
            # Cancellation, deletion and changed authorization take precedence.
            return False
        except Exception:
            LOG.exception('Explore reviewed checkpoint could not be saved: %s', run_id)
            return False

    def _execute(self, owner, run_id, control):
        completion_lock = threading.RLock()
        timer = None
        timeout_message = "本次研究超过执行时间上限。"
        try:
            run = self.repository.get_run(owner, run_id)
            event_initial = (run.get("workflow_version") == "event-lite@1" and
                             run.get("phase") == "initial_explanation")
            run_timeout = min(self.timeout, EVENT_INITIAL_TIMEOUT_SECONDS) if event_initial else self.timeout
            if run.get("workflow_version") == "relation-lite@2":
                run_timeout = min(run_timeout, RELATION_RUN_TIMEOUT_SECONDS)
            elif run.get("workflow_version") == "history-lite@1":
                run_timeout = min(run_timeout, HISTORY_RUN_TIMEOUT_SECONDS)
            if event_initial:
                timeout_message = "首次解释未能保存，请重试。"
            control["run_timeout_seconds"] = run_timeout
            control["deadline"] = time.monotonic() + run_timeout

            def expire():
                with completion_lock:
                    if event_initial:
                        # Both the full answer and a fallback use the same CAS publish.
                        try:
                            live = self.repository.get_run(owner, run_id)
                        except GatewayError as exc:
                            if exc.status == 404:
                                return
                            raise
                        if live["status"] not in ACTIVE or live.get("execution_id") != control["execution_id"]:
                            return
                        fallback = control.get("event_initial_timeout")
                        if callable(fallback):
                            try:
                                if fallback():
                                    control["timed_out"].set()
                                    self._stop(control)
                                    return
                            except Exception:
                                LOG.exception("Explore first explanation fallback failed: %s", run_id)
                        failed = self.repository.transition(owner, run_id, ACTIVE,
                            status="failed", stage="failed", execution_id=control["execution_id"],
                            error=timeout_message, fields={"error_code": "explore_run_timeout"})
                        if failed is None:
                            return
                        control["timed_out"].set()
                    else:
                        control["timed_out"].set()
                        if not self._complete_checkpoint(owner, run_id, control, 'explore_run_timeout'):
                            self.repository.transition(owner, run_id, ACTIVE, status="failed", stage="failed",
                                                       execution_id=control["execution_id"],
                                                       error=timeout_message,
                                                       fields={"error_code": "explore_run_timeout"})
                    self._stop(control)

            timer = threading.Timer(run_timeout, expire)
            timer.daemon = True
            timer.start()
            if run.get("workflow_version") == "event-lite@1":
                from .event_lite_runtime import EventLiteRuntime
                EventLiteRuntime(self, owner, run, control).execute()
            elif run.get("workflow_version") == "relation-lite@2":
                from .relation_lite_runtime import RelationLiteRuntime
                RelationLiteRuntime(self, owner, run, control).execute()
            elif run.get("workflow_version") == "history-lite@1":
                from .history_lite_runtime import HistoryLiteRuntime
                HistoryLiteRuntime(self, owner, run, control).execute()
            elif run.get("phase") == "report":
                self._report(owner, run_id, control)
            else:
                self._prepare(owner, run_id, control)
        except GatewayError as exc:
            with completion_lock:
                if exc.code in {'model_connect_timeout', 'model_header_timeout', 'model_request_timeout',
                                'model_stream_timeout', 'model_stream_interrupted', 'model_connection_failed',
                                'explore_invalid_model_output', 'explore_review_failed'}:
                    if self._complete_checkpoint(owner, run_id, control, exc.code):
                        return
                if control["timed_out"].is_set():
                    message = timeout_message
                else:
                    message = str(exc)
                self.repository.transition(owner, run_id, ACTIVE, status="failed", stage="failed", error=message,
                                           execution_id=control["execution_id"],
                                           fields={"error_code": "explore_run_timeout" if control["timed_out"].is_set() else exc.code})
        except Exception:
            LOG.exception("Explore run failed: %s", run_id)
            self.repository.transition(owner, run_id, ACTIVE, status="failed", stage="failed",
                                       execution_id=control["execution_id"],
                                       error="研究执行失败，请重试；本次未保存报告。")
        finally:
            if timer is not None:
                timer.cancel()
            with self._lock:
                if self._active.get((owner, run_id)) is control:
                    self._active.pop((owner, run_id), None)

    def close(self):
        with self._lock:
            self._closed = True
            controls = list(self._active.items())
        for (owner, run_id), control in controls:
            self.repository.transition(owner, run_id, ACTIVE, status="interrupted", stage="interrupted",
                                       execution_id=control["execution_id"],
                                       error="服务关闭，本次分析没有自动重新执行。")
            self._stop(control)
        for _, control in controls:
            control["thread"].join(timeout=3)
