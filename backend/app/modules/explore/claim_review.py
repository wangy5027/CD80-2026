"""Apply a bounded semantic claim audit without trusting it to invent evidence.

This is a structural gate. The caller must pass its result through
``validate_brief(..., visible=live_evidence)`` before using it in a report.
"""

from __future__ import annotations

from copy import deepcopy

from ..workbench.errors import GatewayError


_REVIEW_KEYS = frozenset(
    {"id", "decision", "text", "type", "support_indices", "question_ids", "reason"}
)
_COVERAGE_KEYS = frozenset({"question_id", "status", "gap", "next_action"})
_TYPES = {"hypothesis": 0, "inference": 1, "sourced_statement": 2, "fact": 3}
_STATUSES = {"answered", "partial", "unanswered"}


def _invalid(detail: str) -> GatewayError:
    return GatewayError("主张审阅结果无效：" + detail, 502)


def _exact_keys(value, keys: frozenset[str], detail: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise _invalid(detail)


def _text(value, maximum: int, detail: str, *, nonempty=False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (nonempty and not value.strip()):
        raise _invalid(detail)
    return value


def apply_claim_review(brief: dict, task: dict, audit: dict) -> tuple[dict, list[dict]]:
    """Return a reviewed raw ledger and compact decisions for later live recheck.

    The audit can retain or narrow already traceable claims, select only their
    original supports, and downgrade coverage. It cannot add claims or sources.
    """
    if not isinstance(brief, dict) or not isinstance(task, dict):
        raise _invalid("原研究账本或任务缺失")
    original_claims = brief.get("claims")
    questions = task.get("subquestions")
    prior_coverage = brief.get("coverage")
    if not isinstance(original_claims, list) or not isinstance(questions, list) or not isinstance(prior_coverage, list):
        raise _invalid("原研究账本结构不完整")
    _exact_keys(audit, frozenset({"claim_reviews", "coverage"}), "顶层字段不匹配")
    claim_reviews = audit["claim_reviews"]
    coverage_reviews = audit["coverage"]
    if not isinstance(claim_reviews, list) or not isinstance(coverage_reviews, list):
        raise _invalid("审阅列表格式不正确")

    question_ids = []
    for question in questions:
        qid = question.get("id") if isinstance(question, dict) else None
        if not isinstance(qid, str) or not qid or qid in question_ids:
            raise _invalid("研究子问题编号无效")
        question_ids.append(qid)
    valid_qids = set(question_ids)
    original_by_id = {}
    for claim in original_claims:
        if not isinstance(claim, dict) or not isinstance(claim.get("id"), str):
            raise _invalid("原主张编号无效")
        if claim.get("support_status") != "traceable":
            continue
        claim_id = claim["id"]
        if (claim_id in original_by_id or not isinstance(claim.get("type"), str)
                or claim["type"] not in _TYPES or not isinstance(claim.get("text"), str)
                or not isinstance(claim.get("supports"), list)
                or any(not isinstance(support, dict) or not isinstance(support.get("evidence_id"), str)
                       or not isinstance(support.get("quote"), str)
                       for support in claim["supports"])):
            raise _invalid("原可追溯主张结构无效")
        original_by_id[claim_id] = claim

    reviews_by_id = {}
    for row in claim_reviews:
        _exact_keys(row, _REVIEW_KEYS, "主张审阅字段不匹配")
        claim_id = row["id"]
        if not isinstance(claim_id, str) or claim_id not in original_by_id or claim_id in reviews_by_id:
            raise _invalid("新增、遗漏或重复主张编号")
        if not isinstance(row["decision"], str) or row["decision"] not in {"approve", "narrow", "reject"}:
            raise _invalid("审阅决定无效")
        original = original_by_id[claim_id]
        _text(row["text"], 1800, "主张文本无效", nonempty=True)
        if row["decision"] == "approve" and row["text"] != original["text"]:
            raise _invalid("直接批准的主张文本发生变化")
        if not isinstance(row["type"], str) or row["type"] not in _TYPES or _TYPES[row["type"]] > _TYPES[original["type"]]:
            raise _invalid("主张类型被升格")
        _text(row["reason"], 500, "审阅理由无效")
        indices = row["support_indices"]
        if (
            not isinstance(indices, list)
            or len(indices) > len(original["supports"])
            or any(type(index) is not int or index < 0 or index >= len(original["supports"]) for index in indices)
            or len(set(indices)) != len(indices)
        ):
            raise _invalid("支持材料序号越界或重复")
        qids = row["question_ids"]
        if (
            not isinstance(qids, list)
            or len(qids) > len(question_ids)
            or any(not isinstance(qid, str) or qid not in valid_qids for qid in qids)
            or len(set(qids)) != len(qids)
        ):
            raise _invalid("子问题编号无效或重复")
        if row["decision"] != "reject":
            if not indices or not qids:
                raise _invalid("保留的主张缺少原支持材料或对应子问题")
            if not any(original["supports"][index].get("background_only") is False for index in indices):
                raise _invalid("保留的主张只有背景材料")
        reviews_by_id[claim_id] = row
    if set(reviews_by_id) != set(original_by_id):
        raise _invalid("原可追溯主张未逐项审阅")

    coverage_by_q = {}
    for row in coverage_reviews:
        _exact_keys(row, _COVERAGE_KEYS, "子问题审阅字段不匹配")
        qid = row["question_id"]
        if not isinstance(qid, str) or qid not in valid_qids or qid in coverage_by_q:
            raise _invalid("子问题新增、遗漏或重复")
        if not isinstance(row["status"], str) or row["status"] not in _STATUSES:
            raise _invalid("子问题状态无效")
        _text(row["gap"], 800, "子问题缺口无效")
        _text(row["next_action"], 500, "后续动作无效")
        coverage_by_q[qid] = row
    if set(coverage_by_q) != valid_qids:
        raise _invalid("子问题未逐项审阅")

    reviewed = deepcopy(brief)
    kept_claims = []
    decisions = []
    refs_by_q = {qid: [] for qid in question_ids}
    for claim_id, original in original_by_id.items():
        row = reviews_by_id[claim_id]
        decision = row["decision"]
        decisions.append(
            {
                "id": claim_id,
                "decision": decision,
                "type": row["type"],
                "support_count": len(row["support_indices"]) if decision != "reject" else 0,
                "question_ids": row["question_ids"] if decision != "reject" else [],
            }
        )
        if decision == "reject":
            continue
        supports = [
            {"evidence_id": original["supports"][index]["evidence_id"],
             "quote": original["supports"][index]["quote"]}
            for index in row["support_indices"]
        ]
        kept_claims.append(
            {
                "id": claim_id,
                "text": row["text"],
                "type": row["type"],
                "supports": supports,
                "conditions": "",
                "event_at": original.get("event_at", ""),
                "event_date_quote": original.get("event_date_quote", ""),
            }
        )
        if row["type"] != "hypothesis":
            for qid in row["question_ids"]:
                refs_by_q[qid].append(claim_id)

    old_coverage_by_q = {
        row["question_id"]: row
        for row in prior_coverage
        if isinstance(row, dict) and isinstance(row.get("question_id"), str)
    }
    reviewed_coverage = []
    for qid in question_ids:
        old = old_coverage_by_q.get(qid, {})
        audit_row = coverage_by_q[qid]
        old_status = old.get("status", "unanswered")
        if old_status not in _STATUSES:
            old_status = "unanswered"
        requested = audit_row["status"]
        if not refs_by_q[qid] or requested == "unanswered":
            status = "unanswered"
        elif old_status == "answered" and requested == "answered":
            status = "answered"
        else:
            status = "partial"
        # The original gap may itself contain an unsupported assertion. Only
        # the reviewed gap can enter the writer's final evidence ledger.
        gap = "" if status == "answered" else (
            audit_row["gap"].strip() or "原子问题仍有待核实的证据缺口。"
        )
        reviewed_coverage.append(
            {
                "question_id": qid,
                "status": status,
                "claim_ids": refs_by_q[qid],
                "gap": gap,
                "next_action": audit_row["next_action"] or str(old.get("next_action") or "")[:500],
            }
        )
    kept_ids = {item["id"] for item in kept_claims}
    reviewed["claims"] = kept_claims
    reviewed["coverage"] = reviewed_coverage
    reviewed["conflicts"] = [
        item for item in brief.get("conflicts", [])
        if isinstance(item, dict)
        and isinstance(item.get("claim_ids"), list)
        and all(isinstance(claim_id, str) and claim_id in kept_ids for claim_id in item["claim_ids"])
    ]
    reviewed["done"] = True
    return reviewed, decisions
