"""Bounded input and model-output validation for Explore."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from ..workbench.errors import GatewayError


MODULES = {"event", "relation", "history"}
MAX_QUESTION = 12000
MAX_CONTEXT = 4
MAX_SOURCES = 8
PROMPT_VERSIONS = {"event": "event@4", "relation": "relation@4", "history": "history@4"}


def has_final_choice(report):
    """Only reports produced with the explicit final-choice contract opt in."""
    return (report.get("module"), report.get("report_format")) in {
        ("event", "event_lite@2"), ("relation", "relation_lite@2"), ("history", "history_lite@1")}


def nonempty(value, label, maximum=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise GatewayError(f"{label}必须是非空文本，长度不超过 {maximum} 字符。", 400)
    return value.strip()


def optional_text(value, label, maximum=4000):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise GatewayError(f"{label}格式不正确。", 502, "explore_invalid_model_output")
    return value.strip()


def safe_url(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\x00-\x1f\\<>\"'`]", value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username and not parsed.password:
            _ = parsed.port
            return value
    except ValueError:
        pass
    return None


def explicit_judgment(question):
    # This only controls whether to ask for a judgment; it never supplies evidence.
    return bool(re.search(r"是否|能否|会不会|能不能|将会|会导致|将导致|会推动|将推动|利好|利空|上涨|下跌|受益|冲击", question))


def proposed_judgment(question):
    short = re.sub(r"https?://\S+", "", question).strip().rstrip("。？！?！")[:180]
    return f"{short}是否会产生用户关心的后续影响？" if short else "该事件是否会产生后续影响？"


def _no_dupes(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def parse_json_object(content):
    if not isinstance(content, str) or len(content) > 40000:
        raise GatewayError("模型返回长度无效。", 502, "explore_invalid_model_output")
    body = content.strip()
    match = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", body, flags=re.I)
    if match:
        body = match.group(1)
    # Reuse the platform's tested OpenAI-compatible JSON normalization. GLM can
    # emit raw LF/CR/TAB inside JSON strings; this preserves their text meaning.
    from ..agent_platform.planner import _json_object
    try:
        value = _json_object(body)
    except GatewayError as cause:
        # Keep acceptance identical to the shared parser. Persist only bounded
        # metadata, never JSONDecodeError.doc or the model's response text.
        detail = {"stage": "json_parse", "reason": "invalid_json"}
        syntax = cause.__context__
        if isinstance(syntax, json.JSONDecodeError):
            detail.update(reason="syntax", position=syntax.pos, line=syntax.lineno,
                          column=syntax.colno, coordinate_system="normalized_json",
                          decoder_message=syntax.msg, response_chars=len(body))
        else:
            reasons = {
                "模型 JSON 包含重复字段。": "duplicate_key",
                "模型 JSON 包含非有限数值。": "non_finite_number",
                "模型结果必须是 JSON 对象。": "not_object",
                "模型 JSON 字符串包含不支持的控制字符。": "control_character",
            }
            detail["reason"] = reasons.get(str(cause), "invalid_json")
        error = GatewayError("模型未返回有效的研究结构。", 502, "explore_invalid_model_output")
        error.diagnostic = detail
        raise error from None
    if not isinstance(value, dict):
        raise GatewayError("模型未返回研究对象。", 502, "explore_invalid_model_output")
    return value


def _text_list(value, label, maximum=8, length=3000):
    if not isinstance(value, list) or len(value) > maximum:
        raise GatewayError(f"模型的{label}结构不正确。", 502, "explore_invalid_model_output")
    result = []
    for item in value:
        clean = optional_text(item, label, length)
        if clean:
            result.append(clean)
    return result


def _refs(value, allowed, label):
    if not isinstance(value, list) or len(value) > MAX_SOURCES or any(not isinstance(item, str) or item not in allowed for item in value):
        raise GatewayError(f"模型的{label}引用了无效来源。", 502, "explore_invalid_source_ref")
    return list(dict.fromkeys(value))


def _event_overview(value):
    """Preserve one observed GLM object shape without accepting arbitrary objects."""
    if isinstance(value, dict):
        labels = {"event_type": "事件类型", "occurred_at": "发生时间", "key_parties": "相关主体"}
        if set(value) != set(labels):
            raise GatewayError("事件概述结构不正确。", 502, "explore_invalid_model_output")
        pieces = []
        for key, label in labels.items():
            content = optional_text(value[key], label, 1200)
            if not content:
                raise GatewayError("事件概述结构不正确。", 502, "explore_invalid_model_output")
            pieces.append(f"{label}：{content}")
        return optional_text("；".join(pieces), "事件概述", 4000)
    return optional_text(value, "事件概述", 4000)


def validate_intent(raw, materials):
    """Bound and ground the model's pre-report interpretation."""
    if not isinstance(raw, dict) or type(raw.get("needs_clarification")) is not bool:
        raise GatewayError("意图预判结构不正确。", 502, "explore_invalid_intent")
    allowed = {item["id"] for item in materials}
    review = {
        "needs_clarification": raw["needs_clarification"],
        "interpreted_question": optional_text(raw.get("interpreted_question"), "理解后的问题", 1000),
        "summary": optional_text(raw.get("summary"), "材料概览", 2000),
        "question": optional_text(raw.get("question"), "澄清问题", 800),
        "limitations": _text_list(raw.get("limitations", []), "澄清限制", 8, 500),
        "options": [],
    }
    if not review["interpreted_question"] or not review["summary"] or (review["needs_clarification"] and not review["question"]):
        raise GatewayError("意图预判缺少必要内容。", 502, "explore_invalid_intent")
    options = raw.get("options", [])
    if not isinstance(options, list) or len(options) > 3:
        raise GatewayError("澄清选项结构不正确。", 502, "explore_invalid_intent")
    seen = set()
    for item in options:
        if not isinstance(item, dict):
            raise GatewayError("澄清选项结构不正确。", 502, "explore_invalid_intent")
        option_id = optional_text(item.get("id"), "澄清选项标识", 40)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", option_id) or option_id in seen:
            raise GatewayError("澄清选项标识不正确。", 502, "explore_invalid_intent")
        seen.add(option_id)
        option = {"id": option_id, "label": optional_text(item.get("label"), "澄清选项", 180),
                  "question": optional_text(item.get("question"), "选项问题", 1000),
                  "reason": optional_text(item.get("reason"), "选项理由", 500),
                  "source_refs": _refs(item.get("source_refs", []), allowed, "澄清选项")}
        if not option["label"] or not option["question"] or not option["source_refs"]:
            raise GatewayError("澄清选项缺少必要内容。", 502, "explore_invalid_intent")
        review["options"].append(option)
    return review


def validate_model_report(raw, module, sources, historical_source_ids, *, allow_incomplete=False,
                          compact_event=False, compact_history=False):
    """Allow only known source IDs and typed module fields; no repair model calls."""
    allowed = {item["id"] for item in sources}
    result = {
        "title": optional_text(raw.get("title"), "标题", 180),
        "summary": optional_text(raw.get("summary"), "概述", 5000),
        "key_findings": _text_list(raw.get("key_findings"), "核心发现", 6),
        "uncertainties": _text_list(raw.get("uncertainties"), "不确定性", 8),
        "limitations": _text_list(raw.get("limitations"), "信息缺口", 8),
    }
    if not result["title"] or not result["summary"] or not result["key_findings"]:
        raise GatewayError("模型报告缺少标题、概述或核心发现。", 502, "explore_invalid_model_output")
    sections = raw.get("sections")
    if not isinstance(sections, list) or not 1 <= len(sections) <= 10:
        raise GatewayError("模型报告缺少模块分析。", 502, "explore_invalid_model_output")
    result["sections"] = []
    for section in sections:
        if not isinstance(section, dict):
            raise GatewayError("模型的模块分析结构不正确。", 502, "explore_invalid_model_output")
        result["sections"].append({"heading": optional_text(section.get("heading"), "章节标题", 120),
                                   "body": optional_text(section.get("body"), "章节内容", 8000),
                                   "source_refs": _refs(section.get("source_refs", []), allowed, "章节")})
    recs = raw.get("recommendations", [])
    if not isinstance(recs, list) or len(recs) > 3:
        raise GatewayError("模型的下一步问题数量不正确。", 502, "explore_invalid_model_output")
    result["recommendations"] = []
    for item in recs:
        # Suggestions are optional; discard unsupported destinations rather
        # than failing a completed core analysis or showing a dead-end action.
        if not isinstance(item, dict) or not isinstance(item.get("module"), str) or item["module"] not in MODULES:
            continue
        question = optional_text(item.get("question"), "下一步问题", 500)
        if not question:
            continue
        result["recommendations"].append({"question": question,
                                          "module": item["module"],
                                          "reason": optional_text(item.get("reason", ""), "推荐原因", 500)})
    result.update(event_overview="", background="", actors=[], drivers=[], impact_paths=[], timeline=[],
                  entities=[], relations=[], judgment="", conclusion="", cases=[])
    if module == "event":
        # A first-pass event brief has its answer in key_findings/sections.  The
        # legacy full report still requires these fields unless it is incomplete.
        result["event_overview"] = _event_overview(raw.get("event_overview", "") if compact_event
                                                    else raw.get("event_overview"))
        result["background"] = optional_text(raw.get("background", "") if compact_event
                                             else raw.get("background"), "背景", 4000)
        if not (allow_incomplete or compact_event) and (not result["event_overview"] or not result["background"]):
            raise GatewayError("事件报告缺少事件概述或背景。", 502, "explore_invalid_model_output")
        for key, maximum in (("actors", 16), ("drivers", 8), ("impact_paths", 8)):
            result[key] = _text_list(raw.get(key), key, maximum)
        timeline = raw.get("timeline", [])
        if not isinstance(timeline, list) or len(timeline) > 12:
            raise GatewayError("模型时间线结构不正确。", 502, "explore_invalid_model_output")
        for item in timeline:
            if not isinstance(item, dict):
                raise GatewayError("模型时间线结构不正确。", 502, "explore_invalid_model_output")
            result["timeline"].append({"time": optional_text(item.get("time", ""), "时间", 120),
                                       "event": optional_text(item.get("event"), "时间线事件", 1200),
                                       "source_refs": _refs(item.get("source_refs", []), allowed, "时间线")})
    elif module == "relation":
        result["entities"] = _text_list(raw.get("entities"), "主体", 16, 180)
        relationships = raw.get("relationships", [])
        if not isinstance(relationships, list) or len(relationships) > 16:
            raise GatewayError("模型关系路径结构不正确。", 502, "explore_invalid_model_output")
        for item in relationships:
            if not isinstance(item, dict):
                raise GatewayError("模型关系路径结构不正确。", 502, "explore_invalid_model_output")
            relation = {key: optional_text(item.get(key, ""), key, 1600)
                        for key in ("from", "to", "relation_type", "direction", "mechanism", "conditions")}
            if any(not value for value in relation.values()):
                raise GatewayError("关系路径缺少方向、机制或成立条件。", 502, "explore_invalid_model_output")
            relation["source_refs"] = _refs(item.get("source_refs", []), allowed, "关系路径")
            if not relation["source_refs"]:
                raise GatewayError("关系路径缺少可追溯来源。", 502, "explore_invalid_source_ref")
            result["relations"].append(relation)
    elif module == "history" and compact_history:
        # Historical relationship reports use the common sourced analysis
        # sections, not the older hypothesis/case-validation contract.
        result["conclusion"] = result["summary"]
    else:
        result["judgment"] = optional_text(raw.get("judgment"), "待验证判断", 1000)
        result["conclusion"] = optional_text(raw.get("conclusion"), "历史结论", 5000)
        if not result["judgment"] or not result["conclusion"]:
            raise GatewayError("历史报告缺少待验证判断或结论。", 502, "explore_invalid_model_output")
        cases = raw.get("cases", [])
        if not isinstance(cases, list) or len(cases) > 5:
            raise GatewayError("模型历史案例结构不正确。", 502, "explore_invalid_model_output")
        for item in cases:
            if not isinstance(item, dict):
                raise GatewayError("模型历史案例结构不正确。", 502, "explore_invalid_model_output")
            refs = _refs(item.get("source_refs", []), allowed, "历史案例")
            if not refs or any(ref not in historical_source_ids for ref in refs):
                raise GatewayError("当前新闻不能作为历史案例证据。", 502, "explore_invalid_history_ref")
            case = {key: optional_text(item.get(key, ""), key, 2500)
                    for key in ("event", "similarities", "differences", "supporting_conditions", "failure_conditions")}
            if any(not value for value in case.values()):
                raise GatewayError("历史案例缺少对照或条件。", 502, "explore_invalid_model_output")
            result["cases"].append(case | {"source_refs": refs})
    return result


def markdown_text(value):
    # HTML must remain text in Markdown viewers; brackets cannot start injected links.
    clean = html.escape(str(value or ""), quote=False)
    return re.sub(r"([\\\[\]])", r"\\\1", clean)


def _brief_inline(value):
    """Keep untrusted model/source titles on one Markdown line."""
    return markdown_text(re.sub(r"\s+", " ", str(value or "")).strip())


def _brief_gap(value):
    """Preserve the whole verified gap near the finding, not a warning chapter."""
    return str(value or "").strip()


def _same_brief_gap(gap, displayed):
    # Safe textual deduplication only: the *entire* gap must already be present.
    # A similar topic or shorter sentence can have the opposite conclusion.
    def compact(value):
        return re.sub(r"[^\w]", "", str(value or "").lower())

    needle = compact(gap)
    return bool(needle) and any(needle in compact(item) for item in displayed)


def _render_event_brief(report):
    lines = ["# " + _brief_inline(report.get("title") or "事件初步探索"), "",
             "本轮问题：" + _brief_inline(report.get("resolved_question") or report.get("question") or ""), "",
             "信息截止：" + _brief_inline(report.get("as_of") or "未知"), "", "## 核心发现", ""]
    findings = [item for item in report.get("key_findings", []) if isinstance(item, str) and item.strip()]
    lines.extend("- " + markdown_text(item) for item in findings)
    displayed_gaps = list(findings)
    reviewed_ids = report.get("inline_coverage_question_ids")
    reviewed_ids = {item for item in reviewed_ids if isinstance(item, str)} if isinstance(reviewed_ids, list) else set()
    for row in report.get("coverage") or []:
        if not isinstance(row, dict) or row.get("status") not in {"partial", "unanswered"}:
            continue
        question_id = row.get("question_id")
        if isinstance(question_id, str) and question_id in reviewed_ids:
            continue
        gap = _brief_gap(row.get("gap"))
        if gap and not _same_brief_gap(gap, displayed_gaps):
            lines.append("- 待补充：" + _brief_inline(gap))
            displayed_gaps.append(gap)

    # The full structured report is retained for audit.  First-pass Markdown
    # shows only the two sections that help read and verify the direct answer.
    section_count = 0
    for section in report.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "").strip()
        if re.search(r"动因|影响路径|时间线|历史验证|关系路径|重要不确定|信息缺口|限制|检索记录|研究方法", heading):
            continue
        body = section.get("body")
        if not isinstance(body, str) or not body.strip():
            continue
        lines.extend(["", "## " + _brief_inline(heading or "消息核实"), "", markdown_text(body.strip())])
        refs = [ref for ref in section.get("source_refs", []) if isinstance(ref, str)]
        if refs:
            lines.extend(["", "来源：" + "、".join(_brief_inline(ref) for ref in refs)])
        section_count += 1
        if section_count >= 2:
            break

    recommendations = [item for item in report.get("recommendations") or []
                       if isinstance(item, dict) and item.get("question")]
    if recommendations:
        names = {"event": "继续理解事件", "relation": "发现关联", "history": "历史验证"}
        lines.extend(["", "## 下一步研究", ""])
        for item in recommendations[:3]:
            label = names.get(item.get("module"), "继续探索")
            reason = _brief_inline(item.get("reason"))
            lines.append("- **" + label + "**：" + _brief_inline(item["question"]) +
                         ("（" + reason + "）" if reason else ""))

    # Do not put unrelated retrieved material or long raw extracts in the
    # user's first read.  The report JSON still contains the full audit trail.
    cited = {ref for section in report.get("sections") or [] if isinstance(section, dict)
             for ref in section.get("source_refs", []) if isinstance(ref, str)}
    cited.update(ref for claim in report.get("claims") or [] if isinstance(claim, dict)
                 for support in claim.get("supports", []) if isinstance(support, dict)
                 for ref in [support.get("source_ref")] if isinstance(ref, str))
    source_rows = [source for source in report.get("sources") or [] if isinstance(source, dict)
                   and (source.get("evidence_usage") == "cited" or source.get("id") in cited)]
    if source_rows:
        kinds = {"extracted_page_text": "网页原文", "search_snippet": "搜索摘要", "news_excerpt": "快讯摘录",
                 "user_input": "用户提供", "structured_data": "结构化资料"}
        lines.extend(["", "## 来源", ""])
        for source in source_rows:
            title = _brief_inline(source.get("title") or source.get("source_name") or source.get("id") or "来源")
            url = safe_url(source.get("url"))
            destination = quote(url, safe=":/?&=#%+;,@!$*-._~") if url else None
            label = f"[{title}]({destination})" if destination else title
            identity = kinds.get(source.get("content_kind"), source.get("source_name") or source.get("kind") or "资料")
            publisher = source.get("publisher")
            if isinstance(publisher, str) and publisher.strip() and publisher.strip() != identity:
                identity += " · " + publisher.strip()
            lines.append("- " + _brief_inline(source.get("id")) + " " + label + "；" + _brief_inline(identity))
    return "\n".join(lines).strip() + "\n"


def render_markdown(report):
    if has_final_choice(report) or (report.get("module"), report.get("report_format")) == ("relation", "relation_lite@1"):
        default_title = {"relation": "关联分析", "history": "历史关联分析"}.get(report.get("module"), "进一步解释")
        lines = ["# " + markdown_text(report.get("title") or default_title), "",
                 "本轮问题：" + markdown_text(report.get("question")), "",
                 "信息截止：" + markdown_text(report.get("as_of") or "未知"), "",
                 "## 核心结论", "", markdown_text(report.get("summary") or "")]
        if report.get("resolved_question") and report["resolved_question"] != report.get("question"):
            lines[4:4] = ["明确后的问题：" + markdown_text(report["resolved_question"]), ""]
        if report.get("completion_warning"):
            lines.extend(["", markdown_text(report["completion_warning"])])
        for finding in report.get("key_findings") or []:
            lines.append("- " + markdown_text(finding))
        for section in report.get("sections") or []:
            if section.get("body"):
                lines.extend(["", "## " + markdown_text(section.get("heading") or default_title), "",
                              markdown_text(section["body"])])
                if section.get("source_refs"):
                    lines.extend(["", "来源：" + "、".join(section["source_refs"])])
        if report.get("sources"):
            lines.extend(["", "## 新闻来源", ""])
            for source in report["sources"]:
                url = safe_url(source.get("url"))
                label = markdown_text(source.get("title") or "新闻")
                if url:
                    label = "[" + label + "](" + quote(url, safe=":/?&=#%+;,@!$*-._~") + ")"
                lines.append("- " + markdown_text(source.get("id")) + " " + label +
                             "；来源：" + markdown_text(source.get("source_name") or source.get("source") or "未知") +
                             "；发布：" + markdown_text(source.get("published_at") or "未知") +
                             "；材料：" + ("网页原文片段" if source.get("content_kind") == "extracted_page_text" else "搜索摘要"))
        if report.get("recommendations"):
            labels = {"event": "理解事件", "relation": "发现关联", "history": "历史验证"}
            lines.extend(["", "## 可继续探索", ""])
            for option in report["recommendations"]:
                lines.append("- " + labels[option["module"]] + "：" + markdown_text(option["question"]))
        return "\n".join(lines).strip() + "\n"
    if report.get("module") == "event" and report.get("report_format") == "event_brief@1":
        return _render_event_brief(report)
    lines = ["# " + markdown_text(report["title"]), "", "研究问题：" + markdown_text(report["question"]),
             "", "模块：" + markdown_text(report["module"]),
             "", "运行状态：" + markdown_text(report["status"]),
             "", "来源报告：" + markdown_text(report.get("source_report_id") or "无"),
             "", "信息截止：" + markdown_text(report["as_of"]), "",
             "AI 生成的研究分析；结论未经过专业正确性或投资效果验证。", "", "## 核心发现", ""]
    if report.get("answer_status"):
        lines += ["目标完成度：" + markdown_text(report["answer_status"]), ""]
        for item in report.get("coverage", []):
            lines += ["- " + markdown_text(item["question"]) + "：" + markdown_text(item["status"]) +
                      ("；" + markdown_text(item["gap"]) if item.get("gap") else "")]
        lines += ["", "方法说明：" + "；".join(markdown_text(x) for x in report.get("method_notes", [])), ""]
    if report.get("resolved_question"):
        lines[4:4] = ["明确后的问题：" + markdown_text(report["resolved_question"]), ""]
    lines += ["- " + markdown_text(item) for item in report["key_findings"]]
    if report.get("clarification_history"):
        lines += ["", "## 澄清记录", ""]
        for item in report["clarification_history"]:
            lines.append("- " + markdown_text(item.get("question")) + "；时间：" + markdown_text(item.get("at")))
    if report.get("confirmed_sources"):
        lines += ["", "确认使用的材料：" + "、".join(markdown_text(item["id"]) for item in report["confirmed_sources"])]
    selection = report.get("context_selection") or {}
    if selection:
        lines += ["", "## 旧研究文件上下文", "", "选择模式：" +
                  markdown_text("用户自选" if selection.get("mode") == "manual" else "自动选取所选分支")]
        if report.get("context_files"):
            for item in report["context_files"]:
                lines.append("- " + markdown_text(item.get("name")) +
                             "；用途：" + markdown_text(item.get("reason")) +
                             "；证据角色：" + markdown_text(item.get("evidence_role")) +
                             ("；本次截取了正文" if item.get("truncated") else ""))
        else:
            lines.append("- 本次未使用旧研究文件。")
        lines += ["- " + markdown_text(item) for item in report.get("context_limitations") or []]
    lines += ["", "## 概述", "", markdown_text(report["summary"])]
    if report["uncertainties"]:
        lines += ["", "## 重要不确定性", ""]
        lines += ["- " + markdown_text(item) for item in report["uncertainties"]]
    for section in report["sections"]:
        lines += ["", "## " + markdown_text(section["heading"]), "", markdown_text(section["body"]),
                  "", "来源：" + ("、".join(section["source_refs"]) if section["source_refs"] else "无可引用来源")]
    if report["module"] == "event":
        for heading, key in (("事件概述", "event_overview"), ("背景", "background")):
            if report[key]:
                lines += ["", "## " + heading, "", markdown_text(report[key])]
        if report["timeline"]:
            lines += ["", "## 时间线", ""]
            lines += ["- " + markdown_text(item["time"]) + "：" + markdown_text(item["event"]) +
                      "；来源：" + ("、".join(item["source_refs"]) or "无") for item in report["timeline"]]
        for heading, key in (("重要主体", "actors"), ("动因", "drivers"), ("影响路径", "impact_paths")):
            if report[key]:
                lines += ["", "## " + heading, ""]
                lines += ["- " + markdown_text(item) for item in report[key]]
    if report["relations"]:
        lines += ["", "## 关系路径", ""]
        for item in report["relations"]:
            lines += ["- " + markdown_text(item["from"]) + " → " + markdown_text(item["to"]) +
                      "；" + markdown_text(item["relation_type"]) + "；方向：" + markdown_text(item["direction"]) +
                      "；机制：" + markdown_text(item["mechanism"]) +
                      "；条件：" + markdown_text(item["conditions"]) + "；来源：" + "、".join(item["source_refs"])]
    if report["module"] == "relation" and report["entities"]:
        lines += ["", "## 主体", ""]
        lines += ["- " + markdown_text(item) for item in report["entities"]]
    if report["module"] == "history":
        lines += ["", "## 待验证判断", "", markdown_text(report["judgment"]),
                  "", "## 对照结论", "", markdown_text(report["conclusion"])]
    if report["cases"]:
        lines += ["", "## 历史案例", ""]
        for item in report["cases"]:
            lines += ["- " + markdown_text(item["event"]) + "；相似点：" + markdown_text(item["similarities"]) +
                      "；差异：" + markdown_text(item["differences"]) +
                      "；支持条件：" + markdown_text(item["supporting_conditions"]) +
                      "；失效条件：" + markdown_text(item["failure_conditions"]) +
                      "；来源：" + "、".join(item["source_refs"])]
    lines += ["", "## 信息缺口与限制", ""]
    lines += ["- " + markdown_text(item) for item in report["limitations"]]
    retrieval = report.get("retrieval") or {}
    if retrieval.get("searches") or retrieval.get("news"):
        lines += ["", "## 检索记录", ""]
        for search in retrieval.get("searches", []):
            lines.append("- 联网搜索：" + markdown_text(search.get("query")) +
                         "；时间范围：" + markdown_text(search.get("freshness")) +
                         "；状态：" + markdown_text(search.get("status")) +
                         "；结果数：" + markdown_text(search.get("count")) +
                         "；检索时间：" + markdown_text(search.get("searched_at")) +
                         ("；错误：" + markdown_text(search["error"]) if search.get("error") else ""))
        for news in retrieval.get("news", []):
            lines.append("- 快讯窗口：" + markdown_text(news.get("source")) +
                         "；状态：" + markdown_text(news.get("status")) +
                         "；窗口条数：" + markdown_text(news.get("count")) +
                         "；获取时间：" + markdown_text(news.get("fetched_at")) +
                         ("；错误：" + markdown_text(news["error"]) if news.get("error") else ""))
    lines += ["", "## 来源", ""]
    for source in report["sources"]:
        title = markdown_text(source["title"])
        url = safe_url(source.get("url"))
        safe_destination = quote(url, safe=":/?&=#%+;,@!$*-._~") if url else None
        label = f"[{title}]({safe_destination})" if safe_destination else title
        lines.append("- " + source["id"] + " " + label + "；类型：" +
                     markdown_text(source.get("kind") or source.get("source")) + "；发布：" +
                     markdown_text(source.get("published_at") or "未知") + "；检索：" +
                     markdown_text(source.get("retrieved_at") or "未知"))
        if source.get("text_used"):
            label_kind = {"search_snippet": "网页搜索摘要，非全文", "news_excerpt": "快讯摘录，非全文",
                          "user_input": "用户输入，未经独立核实"}.get(source.get("content_kind"), "来源摘录")
            lines.append("  - 实际使用文本（" + label_kind + "）：" +
                         markdown_text(source["text_used"]))
    if report["recommendations"]:
        lines += ["", "## 下一步问题", ""]
        lines += ["- " + markdown_text(item["question"]) for item in report["recommendations"]]
    return "\n".join(lines).strip() + "\n"


def historical_source_ids(sources, as_of, days=90):
    cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) - timedelta(days=days)
    ids = set()
    for source in sources:
        if source["id"].startswith("U") or not source.get("published_at"):
            continue
        try:
            published = datetime.fromisoformat(source["published_at"].replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if published <= cutoff:
                ids.add(source["id"])
        except ValueError:
            continue
    return ids
