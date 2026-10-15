"""Deterministic Explore report checkpoint after a completed claim review.

This path never reads a model draft. The caller remains responsible for checking
run ownership, live evidence authorization and the atomic save transition.
"""

from __future__ import annotations

import re
from copy import deepcopy

from .contracts import MODULES, validate_model_report, render_markdown
from .repository import timestamp, uid
from .research_runtime import PROMPT_VERSION, WORKFLOW_VERSION
from ..workbench.errors import GatewayError


EXPLANATION = "最后报告核对未完成，以下保留已完成逐项审阅的结论与来源。"
_BACKGROUND_ROLES = {"ai_report", "background", "background_only"}
_SUPPORTED_TYPES = {"fact", "sourced_statement", "inference"}


def _source_map(sources):
    """Reject ambiguous display IDs; only this run's S-numbered sources qualify."""
    mapping = {}
    duplicates = set()
    for source in sources if isinstance(sources, list) else []:
        if not isinstance(source, dict):
            continue
        ref = source.get("id")
        if not isinstance(ref, str) or not re.fullmatch(r"S[1-9][0-9]*", ref):
            continue
        if ref in mapping:
            duplicates.add(ref)
        else:
            mapping[ref] = source
    for ref in duplicates:
        mapping.pop(ref, None)
    return mapping


def _review_map(reviews):
    mapping = {}
    duplicates = set()
    for review in reviews if isinstance(reviews, list) else []:
        if not isinstance(review, dict) or not isinstance(review.get("id"), str):
            continue
        claim_id = review["id"]
        if claim_id in mapping:
            duplicates.add(claim_id)
        else:
            mapping[claim_id] = review
    for claim_id in duplicates:
        mapping.pop(claim_id, None)
    return mapping


def _valid_supports(claim, available):
    supports = []
    for support in claim.get("supports", []) if isinstance(claim.get("supports"), list) else []:
        if not isinstance(support, dict) or support.get("background_only") is not False:
            continue
        ref, quote = support.get("source_ref"), support.get("quote")
        if not isinstance(ref, str):
            continue
        source = available.get(ref)
        if (source is None or not isinstance(quote, str) or not 4 <= len(quote) <= 800
                or not quote.strip() or "\x00" in quote):
            continue
        if source.get("kind") in {"user", "report"} or source.get("evidence_role") in _BACKGROUND_ROLES:
            continue
        if source.get("content_kind") == "user_input":
            continue
        source_eid = source.get("evidence_id")
        if (not isinstance(support.get("evidence_id"), str)
                or (source_eid is not None and source_eid != support["evidence_id"])):
            continue
        # validate_brief already checked the exact read span. A caller that
        # provides source text must still contain the alleged literal quote.
        text_used = source.get("text_used")
        if not isinstance(text_used, str) or quote not in text_used:
            continue
        if (source.get("content_hash") and support.get("content_hash")
                and source["content_hash"] != support["content_hash"]):
            continue
        supports.append(deepcopy(support))
    return supports


def _finding(claim, source):
    kind = source.get("content_kind")
    if claim["type"] == "inference":
        prefix = "条件性推断（仍需进一步核实）："
    elif kind == "search_snippet":
        prefix = "检索摘要称："
    elif kind == "news_excerpt":
        prefix = "快讯摘录称："
    elif claim["type"] == "sourced_statement":
        prefix = "来源陈述："
    else:
        prefix = ""
    result = prefix + claim["text"]
    conditions = claim.get("conditions")
    if isinstance(conditions, str) and conditions.strip():
        result += "；适用条件：" + conditions.strip()
    return result + "（来源：" + claim["supports"][0]["source_ref"] + "）"


def build_checkpoint_report(run, research, brief, sources, claim_reviews):
    """Build a partial report solely from reviewed, traceable source excerpts.

    Return ``None`` when there is no approved and grounded non-hypothetical
    claim. This is content construction only; it cannot authorize a late save.
    """
    if not all(isinstance(value, dict) for value in (run, research, brief)):
        return None
    module = run.get("module")
    if module not in MODULES or not isinstance(sources, list):
        return None
    task = research.get("task")
    if not isinstance(task, dict) or not isinstance(brief.get("coverage"), list):
        return None
    available = _source_map(sources)
    decisions = _review_map(claim_reviews)
    scoped_ids = set()
    for row in brief["coverage"]:
        if isinstance(row, dict) and isinstance(row.get("claim_ids"), list):
            scoped_ids.update(claim_id for claim_id in row["claim_ids"]
                              if isinstance(claim_id, str))
    accepted = []
    seen_ids = set()
    for claim in brief.get("claims", []) if isinstance(brief.get("claims"), list) else []:
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("id")
        if not isinstance(claim_id, str):
            continue
        decision = decisions.get(claim_id)
        if (claim_id in seen_ids or claim_id not in scoped_ids
                or claim.get("support_status") != "traceable"
                or claim.get("type") not in _SUPPORTED_TYPES
                or not isinstance(claim.get("text"), str) or not claim["text"].strip()
                or len(claim["text"]) > 1800 or "\x00" in claim["text"]
                or not isinstance(decision, dict)
                or decision.get("decision") not in {"approve", "narrow"}
                or decision.get("type") != claim["type"]
                or type(decision.get("support_count")) is not int
                or decision["support_count"] < 1):
            continue
        seen_ids.add(claim_id)
        supports = _valid_supports(claim, available)
        if not supports:
            continue
        retained = deepcopy(claim)
        retained["supports"] = supports
        accepted.append(retained)
    if not accepted:
        return None

    by_id = {claim["id"]: claim for claim in accepted}
    coverage = []
    for row in brief["coverage"]:
        if not isinstance(row, dict):
            continue
        kept = deepcopy(row)
        original_refs = row.get("claim_ids") if isinstance(row.get("claim_ids"), list) else []
        kept["claim_ids"] = [claim_id for claim_id in original_refs
                             if isinstance(claim_id, str) and claim_id in by_id]
        if kept.get("status") in {"answered", "partial"} and not kept["claim_ids"]:
            kept["status"] = "unanswered"
            kept["gap"] = kept.get("gap") or "该子问题尚无可纳入阶段性简报的已审阅主张。"
        coverage.append(kept)
    if not coverage:
        return None
    status_rank = {"unanswered": 0, "partial": 1, "answered": 2}
    original_status = brief.get("answer_status")
    if original_status not in status_rank:
        return None
    states = [row.get("status") for row in coverage]
    coverage_status = ("answered" if all(state == "answered" for state in states)
                       else "partial" if any(state != "unanswered" for state in states)
                       else "unanswered")
    answer_status = min((original_status, coverage_status), key=status_rank.get)

    # Give each subquestion a chance to appear before filling the six findings.
    chosen_ids = []
    for row in coverage:
        for claim_id in row.get("claim_ids", []):
            if claim_id not in chosen_ids:
                chosen_ids.append(claim_id)
                break
    for claim in accepted:
        if claim["id"] not in chosen_ids:
            chosen_ids.append(claim["id"])
    eligible_findings = [by_id[claim_id] for claim_id in chosen_ids
                         if len(_finding(by_id[claim_id], available[
                             by_id[claim_id]["supports"][0]["source_ref"]])) <= 3000]
    selected = eligible_findings[:6]
    if not selected:
        return None
    primary_refs = list(dict.fromkeys(claim["supports"][0]["source_ref"] for claim in selected))
    key_findings = [_finding(claim, available[claim["supports"][0]["source_ref"]])
                    for claim in selected]
    section_body = EXPLANATION + "\n" + "\n".join(
        ref + " 逐字支持片段：\u201c" + next(
            support["quote"] for claim in selected for support in claim["supports"]
            if support["source_ref"] == ref) + "”"
        for ref in primary_refs
    )
    if not section_body:
        return None

    question = run.get("question") or task.get("original_input")
    resolved = run.get("resolved_question") or task.get("resolved_goal") or question
    as_of = (run.get("time_policy") or {}).get("as_of") or task.get("as_of")
    if not all(isinstance(value, str) and value.strip() for value in (question, resolved, as_of)):
        return None
    recommendations = []
    for row in coverage:
        if row.get("status") not in {"partial", "unanswered"}:
            continue
        question_text = row.get("question")
        reason = row.get("next_action") or row.get("gap") or "继续核对来源"
        if (isinstance(question_text, str) and question_text.strip()
                and len(question_text) <= 500 and isinstance(reason, str)
                and len(reason) <= 500):
            recommendations.append({"question": question_text, "module": module,
                                    "reason": reason})
        if len(recommendations) == 3:
            break
    judgment = run.get("judgment") or (resolved if len(resolved) <= 1000
                                       else "原问题的历史判断仍待核验。")
    if not isinstance(judgment, str) or len(judgment) > 1000:
        return None
    raw = {
        "title": "阶段性研究简报",
        "summary": EXPLANATION,
        "key_findings": key_findings,
        "sections": [{"heading": "已核对的来源信息", "body": section_body,
                      "source_refs": primary_refs}],
        "recommendations": recommendations,
        "uncertainties": [],
        "limitations": [] if module == "event" else [
            row["gap"] for row in coverage if row.get("status") != "answered"
            and isinstance(row.get("gap"), str) and row["gap"].strip()
        ][:8],
        "event_overview": "", "background": "", "actors": [], "drivers": [],
        "impact_paths": [], "timeline": [], "entities": [], "relationships": [],
        "judgment": judgment,
        "conclusion": EXPLANATION, "cases": [],
    }
    cited_ids = {support["source_ref"] for claim in accepted for support in claim["supports"]}
    quote_map = {ref: [] for ref in cited_ids}
    for claim in accepted:
        for support in claim["supports"]:
            quotes = quote_map[support["source_ref"]]
            if support["quote"] not in quotes:
                quotes.append(support["quote"])
    report_sources = []
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("id"), str):
            continue
        item = deepcopy(source)
        quotes = quote_map.get(item["id"], [])
        item["text_used"] = "\n\n".join(quotes)
        if quotes:
            item["summary"] = "\n".join(quotes)
        item["evidence_usage"] = "cited" if quotes else "retrieved_only"
        report_sources.append(item)
    confirmed_sources = [source for source in report_sources if source["evidence_usage"] == "cited"]
    try:
        data = validate_model_report(
            raw, module, confirmed_sources, set(research.get("historical_ids") or []),
            allow_incomplete=True, compact_event=module == "event",
        )
    except GatewayError:
        # This checkpoint is optional. Invalid or oversized data must never
        # replace the ordinary report path with a new failure.
        return None
    kept_ids = set(by_id)
    conflicts = [deepcopy(row) for row in brief.get("conflicts", [])
                 if isinstance(row, dict) and isinstance(row.get("claim_ids"), list)
                 and all(claim_id in kept_ids for claim_id in row["claim_ids"])]
    blocking_gaps = [row["gap"] for row in coverage
                     if row.get("status") != "answered" and isinstance(row.get("gap"), str)
                     and row["gap"].strip()]
    data.update(
        answer_status=answer_status, blocking_gaps=blocking_gaps,
        method_notes=["已完成逐项主张审阅；最终报告核对未完成。",
                      "只展示仍有本轮逐字支持片段的已批准主张；条件性推断保留其类型。"],
        coverage=coverage, claims=accepted, conflicts=conflicts,
        validation_notes=deepcopy(brief.get("validation_notes") or []),
        research_validation_notes=deepcopy((research.get("brief") or {}).get("validation_notes") or []),
        claim_reviews=deepcopy(claim_reviews), claim_review_version="claim-review@1",
        draft_reviews=deepcopy(run.get("draft_reviews") or []),
        report_variant="review_timeout_checkpoint",
    )
    report = {
        "id": uid(), "run_id": run.get("id"), "session_id": run.get("session_id"),
        "module": module, "question": question, "resolved_question": resolved,
        "clarification_history": deepcopy(run.get("clarification_history") or []),
        "confirmed_sources": confirmed_sources,
        "context_selection": deepcopy(run.get("context_selection")),
        "context_files": deepcopy(run.get("context_files") or []),
        "context_limitations": deepcopy(run.get("context_limitations") or []),
        "as_of": as_of, "created_at": timestamp(),
        "source_report_id": run.get("source_report_id"),
        "status": "partial", "mode": "current_research",
        "sources": report_sources, "retrieval": deepcopy(research.get("retrieval") or {}),
        "research_task": deepcopy(task), "skills_used": deepcopy(research.get("skills_used") or run.get("skills_used") or []),
        "workflow_version": run.get("workflow_version") or WORKFLOW_VERSION,
        "tool_schema_hashes": deepcopy(run.get("tool_schema_hashes") or {}),
        "public_dns_mode": run.get("public_dns_mode"),
        "authorized_public_urls": deepcopy(run.get("authorized_public_urls") or []),
        "research_prompt_version": run.get("research_prompt_version") or PROMPT_VERSION,
        "writer_prompt_version": "deterministic-checkpoint@1",
        "report_format": "event_brief@1" if module == "event" else "research",
        "inline_coverage_question_ids": [],
        "research_budget": deepcopy(run.get("research_budget") or research.get("budget") or {}),
        "model_retries": deepcopy(run.get("model_retries") or []),
        "model_transport_trace": deepcopy(run.get("model_transport_trace") or []),
        "research_stop_reason": research.get("stop_reason"),
        "explanation": EXPLANATION,
        "checkpoint_version": "review-timeout-checkpoint@1",
        **data,
    }
    report["markdown"] = render_markdown(report)
    return report
