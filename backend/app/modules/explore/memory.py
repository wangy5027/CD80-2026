"""Deterministic, bounded summaries for a single Explore research branch.

The caller supplies the exact parent snapshot and a saved run/answer/report.
This module makes no model calls and never reads the file pool. ``state`` is
carried in each immutable snapshot so a later phase can merge, not replace,
earlier conclusions. The rendered content is always at most 10,000 characters.
"""

from __future__ import annotations

import copy


MAX_MEMORY_CHARS = 10_000
MAX_STAGES = 5


def _clip(value, limit):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _items(values, count, limit):
    if not isinstance(values, list):
        return []
    return [_clip(value, limit) for value in values[:count] if isinstance(value, str) and value.strip()]


def _sources(run, report, *, initial):
    candidates = (run.get("initial_sources") or []) if initial else (
        report.get("sources") or report.get("confirmed_sources") or [])
    result = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        label = _clip(item.get("id"), 24)
        if not label:
            continue
        title = _clip(item.get("title"), 75)
        url = _clip(item.get("url"), 180)
        result.append({"id": label, "title": title, "url": url})
        if len(result) == 6:
            break
    return result


def _report_sections(report):
    result = []
    for item in (report.get("sections") or [])[:2]:
        if isinstance(item, dict) and item.get("body"):
            result.append({"heading": _clip(item.get("heading"), 80),
                           "body": _clip(item["body"], 320),
                           "source_refs": _items(item.get("source_refs"), 4, 24)})
    return result


def _history_cases(report):
    result = []
    for item in (report.get("cases") or [])[:2]:
        if isinstance(item, dict):
            result.append({key: _clip(item.get(key), limit) for key, limit in (
                ("event", 90), ("similarities", 100), ("differences", 100),
                ("supporting_conditions", 100), ("failure_conditions", 100))} |
                {"source_refs": _items(item.get("source_refs"), 4, 24)})
    return result


def _stage(run, *, report=None, initial_answer=None):
    initial = initial_answer is not None
    phase = "initial_explanation" if initial else "report"
    report = report or {}
    summary = _clip(initial_answer, 1150) if initial else _clip(report.get("summary"), 550)
    result = {
        "run_id": _clip(run.get("id"), 80),
        "module": _clip(run.get("module"), 32),
        "phase": phase,
        "question": _clip(run.get("question"), 260),
        "resolved_question": _clip(run.get("resolved_question"), 260),
        "as_of": _clip(report.get("as_of") or (run.get("time_policy") or {}).get("as_of") or run.get("updated_at"), 80),
        "summary": summary,
        "conclusion": "" if initial else _clip(report.get("conclusion"), 300),
        "findings": [] if initial else _items(report.get("key_findings"), 3, 160),
        "uncertainties": [] if initial else _items(report.get("uncertainties"), 3, 150),
        "limitations": [] if initial else _items(report.get("limitations"), 2, 150),
        "sections": [] if initial else _report_sections(report),
        "cases": [] if initial else _history_cases(report),
        "sources": _sources(run, report, initial=initial),
    }
    if initial:
        result["uncertainties"] = ["第一步解释是未经独立核实的 AI 分析；具体事实须核对原始材料。"]
    return result


def _render(state):
    lines = ["# 连续研究记忆", "",
             "此文件是研究进展摘要；旧 AI 回答只作背景，具体事实须回到原始材料核对。", "",
             "## 研究目标", "", state.get("goal") or "尚未明确", ""]
    if state.get("clarifications"):
        lines += ["## 用户澄清与口径", ""]
        lines += ["- " + item for item in state["clarifications"]]
        lines.append("")
    compacted_count = int(state.get("compacted_count") or 0)
    prior_summaries = state.get("prior_summaries") or []
    if compacted_count or prior_summaries or state.get("prior_digest"):
        lines += ["## 更早进展", ""]
        if compacted_count:
            lines.append(f"已有 {compacted_count} 个更早阶段压缩归档；这里只列最近的简述，完整内容见历史记忆版本。")
        if state.get("prior_digest"):
            lines.append("旧版概述（可能不完整）：" + _clip(state["prior_digest"], 300))
        lines += ["- " + item for item in prior_summaries]
        lines.append("")
    lines += ["## 已完成研究", ""]
    for index, stage in enumerate(state.get("stages") or [], 1):
        label = {"event": "理解事件", "relation": "发现关联", "history": "历史验证"}.get(stage["module"], stage["module"])
        section = [f"### {index}. {label}／{stage['phase']} ({stage['as_of']})",
                   "问题：" + stage["question"]]
        if stage["resolved_question"] and stage["resolved_question"] != stage["question"]:
            section.append("澄清后：" + stage["resolved_question"])
        section.append("阶段结论或回复：" + stage["summary"])
        if stage.get("conclusion"):
            section.append("AI报告结论（待核对）：" + stage["conclusion"])
        for excerpt in stage.get("sections") or []:
            refs = "、".join(excerpt["source_refs"]) or "无"
            section.append("AI报告章节（推断／解释，待核对）「" + excerpt["heading"] +
                           "」：" + excerpt["body"] + "；材料编号：" + refs)
        for case in stage.get("cases") or []:
            refs = "、".join(case["source_refs"]) or "无"
            section.append("AI报告历史案例（待核对）：" + case["event"] +
                           "；相似：" + case["similarities"] + "；差异：" + case["differences"] +
                           "；支持条件：" + case["supporting_conditions"] +
                           "；失效条件：" + case["failure_conditions"] + "；材料编号：" + refs)
        section += ["发现：" + item for item in stage["findings"]]
        section += ["推断／不确定性：" + item for item in stage["uncertainties"]]
        section += ["信息缺口：" + item for item in stage["limitations"]]
        for source in stage["sources"]:
            section.append("本轮材料编号（引用须核对）：" + source["id"] + " " + source["title"] +
                           ((" " + source["url"]) if source["url"] else ""))
        body = "\n".join(section)
        lines += [body if len(body) <= 1900 else body[:1899].rstrip() + "…", ""]
    return "\n".join(lines).strip() + "\n"


def _compact_stages(state, dropped):
    summaries = list(state.get("prior_summaries") or [])
    for item in dropped:
        detail = (item.get("sections") or [{}])[0].get("body") or item.get("conclusion") or ""
        summaries.append(_clip(item.get("question"), 80) + "：" + _clip(item.get("summary"), 90) +
                         (("；章节要点：" + _clip(detail, 110)) if detail else ""))
    state["prior_summaries"] = summaries[-4:]
    state["compacted_count"] = int(state.get("compacted_count") or 0) + len(dropped)


def build_memory_state(parent, run, report=None, initial_answer=None):
    """Return ``{"content": Markdown, "state": structured_summary}``.

    Exactly one of ``report`` and ``initial_answer`` is expected. ``parent``
    is the latest published snapshot in this branch, or None for a new chain.
    A report after an event first explanation uses that first snapshot as its
    parent; a child run uses its frozen ``memory_input`` as parent.
    """
    if (report is None) == (initial_answer is None):
        raise ValueError("exactly one memory phase result is required")
    previous = (parent or {}).get("state")
    if isinstance(previous, dict):
        state = copy.deepcopy(previous)
    else:
        state = {"goal": "", "clarifications": [], "prior_digest": "",
                 "prior_summaries": [], "compacted_count": 0, "stages": []}
        if parent and parent.get("content"):
            state["prior_digest"] = _clip(parent["content"], 700)
    state["goal"] = _clip(state.get("goal") or run.get("question"), 350)
    clarifications = list(state.get("clarifications") or [])
    resolved = _clip(run.get("resolved_question"), 240)
    if resolved and resolved != _clip(run.get("question"), 240) and resolved not in clarifications:
        clarifications.append(resolved)
    for item in run.get("clarification_history") or []:
        if isinstance(item, dict):
            question = _clip(item.get("question"), 240)
            if question and question not in clarifications:
                clarifications.append(question)
    state["clarifications"] = clarifications[-3:]
    stages = list(state.get("stages") or [])
    stages.append(_stage(run, report=report, initial_answer=initial_answer))
    if len(stages) > MAX_STAGES:
        dropped = stages[:-MAX_STAGES]
        _compact_stages(state, dropped)
        stages = stages[-MAX_STAGES:]
    state["stages"] = stages
    content = _render(state)
    # The bounded fields and five-stage window normally fit. Keep a hard
    # budget even for legacy states with unexpected extra text.
    while len(content) > MAX_MEMORY_CHARS and len(state["stages"]) > 1:
        oldest = state["stages"].pop(0)
        _compact_stages(state, [oldest])
        content = _render(state)
    if len(content) > MAX_MEMORY_CHARS:
        raise ValueError("structured memory exceeded fixed budget")
    return {"content": content, "state": state}
