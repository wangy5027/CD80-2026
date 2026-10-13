"""A semantic reviewer may narrow existing evidence, never author new evidence."""

from __future__ import annotations

import copy
import unittest
from uuid import uuid4

from backend.app.modules.explore.claim_review import apply_claim_review
from backend.app.modules.explore.research_runtime import validate_brief
from backend.app.modules.workbench.errors import GatewayError


QUOTE_1 = "官方于2024年5月1日宣布项目已经发布。"
QUOTE_2 = "第二份公告说明项目仍需进行后续核验。"


def fixture():
    task = {
        "subquestions": [
            {"id": "Q1", "question": "是否已宣布？"},
            {"id": "Q2", "question": "后续核验是否完成？"},
        ]
    }
    first_id, second_id = str(uuid4()), str(uuid4())
    visible = {
        first_id: {
            "source": {"id": "S1", "evidence_id": first_id, "kind": "public_page"},
            "read_texts": [QUOTE_1],
            "read_spans": [{"start": 0, "end": len(QUOTE_1), "text": QUOTE_1}],
        },
        second_id: {
            "source": {"id": "S2", "evidence_id": second_id, "kind": "public_page"},
            "read_texts": [QUOTE_2],
            "read_spans": [{"start": 0, "end": len(QUOTE_2), "text": QUOTE_2}],
        },
    }
    brief = validate_brief(
        {
            "claims": [
                {"id": "C1", "text": "官方已经宣布项目发布", "type": "fact",
                 "supports": [{"evidence_id": first_id, "quote": QUOTE_1}],
                 "conditions": "这是原始推断条件", "event_at": "2024-05-01",
                 "event_date_quote": "2024年5月1日"},
                {"id": "C2", "text": "核验仍未完成", "type": "sourced_statement",
                 "supports": [{"evidence_id": second_id, "quote": QUOTE_2}]},
            ],
            "coverage": [
                {"question_id": "Q1", "status": "answered", "claim_ids": ["C1"]},
                {"question_id": "Q2", "status": "partial", "claim_ids": ["C2"],
                 "gap": "原研究仍缺核验结果。"},
            ],
            "conflicts": [{"claim_ids": ["C1", "C2"], "description": "时间范围不同"}],
            "done": True,
        },
        task,
        visible,
    )
    return task, visible, brief


def review_row(claim_id, *, decision="approve", text=None, claim_type=None,
               support_indices=None, question_ids=None):
    defaults = {
        "C1": ("官方已经宣布项目发布", "fact", ["Q1"]),
        "C2": ("核验仍未完成", "sourced_statement", ["Q2"]),
    }
    original_text, original_type, original_qids = defaults[claim_id]
    return {
        "id": claim_id,
        "decision": decision,
        "text": original_text if text is None else text,
        "type": original_type if claim_type is None else claim_type,
        "support_indices": [0] if support_indices is None else support_indices,
        "question_ids": original_qids if question_ids is None else question_ids,
        "reason": "与现有逐字片段相符",
    }


def audit(*, rows=None, coverage=None):
    return {
        "claim_reviews": rows if rows is not None else [review_row("C1"), review_row("C2")],
        "coverage": coverage if coverage is not None else [
            {"question_id": "Q1", "status": "answered", "gap": "", "next_action": ""},
            {"question_id": "Q2", "status": "answered", "gap": "审阅仍无法确认完结。", "next_action": "核对最终公告"},
        ],
    }


class ClaimReviewTests(unittest.TestCase):
    def test_approved_claims_keep_only_original_opaque_supports_and_live_revalidation(self):
        task, visible, brief = fixture()
        raw, decisions = apply_claim_review(brief, task, audit())
        self.assertEqual(raw["claims"][0]["supports"],
                         [{"evidence_id": brief["claims"][0]["supports"][0]["evidence_id"],
                           "quote": QUOTE_1}])
        self.assertEqual(raw["claims"][0]["conditions"], "")
        self.assertEqual(raw["claims"][0]["event_at"], "2024-05-01")
        self.assertEqual(raw["claims"][0]["event_date_quote"], "2024年5月1日")
        self.assertFalse(any("quote" in decision or "text" in decision for decision in decisions))
        checked = validate_brief(raw, task, visible)
        self.assertEqual(checked["claims"][0]["support_status"], "traceable")
        self.assertEqual([item["status"] for item in checked["coverage"]], ["answered", "partial"])

    def test_injected_source_or_extra_top_level_field_is_rejected(self):
        task, _, brief = fixture()
        injected = audit()
        injected["claim_reviews"][0]["supports"] = [{"evidence_id": str(uuid4()), "quote": "伪造来源"}]
        with self.assertRaises(GatewayError):
            apply_claim_review(brief, task, injected)
        injected = audit()
        injected["sources"] = [{"title": "伪造来源"}]
        with self.assertRaises(GatewayError):
            apply_claim_review(brief, task, injected)

    def test_missing_duplicate_and_unknown_claim_or_question_is_rejected(self):
        task, _, brief = fixture()
        cases = [
            audit(rows=[review_row("C1")]),
            audit(rows=[review_row("C1"), review_row("C1")]),
            audit(rows=[review_row("C1"), {**review_row("C2"), "id": "C99"}]),
            audit(coverage=[{"question_id": "Q1", "status": "answered", "gap": "", "next_action": ""}]),
            audit(coverage=[{"question_id": "Q1", "status": "answered", "gap": "", "next_action": ""}] * 2),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(GatewayError):
                apply_claim_review(brief, task, candidate)

    def test_type_upgrade_and_approve_text_change_are_rejected(self):
        task, _, brief = fixture()
        cases = [
            audit(rows=[review_row("C1"), review_row("C2", claim_type="fact")]),
            audit(rows=[review_row("C1", decision="approve", text="证据没有说过的新结论"), review_row("C2")]),
            audit(rows=[review_row("C1", support_indices=[True]), review_row("C2")]),
            audit(rows=[review_row("C1", support_indices=[1]), review_row("C2")]),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate), self.assertRaises(GatewayError):
                apply_claim_review(brief, task, candidate)

    def test_reject_removes_claim_and_its_conflict_and_forces_unanswered_question(self):
        task, visible, brief = fixture()
        reviewed, decisions = apply_claim_review(
            brief,
            task,
            audit(rows=[review_row("C1"), review_row("C2", decision="reject",
                                   support_indices=[], question_ids=[])]),
        )
        self.assertEqual([item["id"] for item in reviewed["claims"]], ["C1"])
        self.assertEqual(reviewed["conflicts"], [])
        self.assertEqual(reviewed["coverage"][1]["claim_ids"], [])
        self.assertEqual(reviewed["coverage"][1]["status"], "unanswered")
        self.assertEqual(decisions[1]["decision"], "reject")
        self.assertEqual(validate_brief(reviewed, task, visible)["answer_status"], "partial")

    def test_narrow_downgrades_type_and_replaces_unreviewed_gap_for_partial_q2(self):
        task, visible, brief = fixture()
        brief["coverage"][1]["gap"] = "已确认华为和江淮各方回应，但仍缺日期。"
        reviewed, _ = apply_claim_review(
            brief,
            task,
            audit(rows=[review_row("C1"), review_row("C2", decision="narrow",
                                   text="公告仅称后续核验尚未完成", claim_type="inference")]),
        )
        self.assertEqual(reviewed["claims"][1]["text"], "公告仅称后续核验尚未完成")
        self.assertEqual(reviewed["claims"][1]["type"], "inference")
        self.assertEqual(reviewed["coverage"][1]["gap"], "审阅仍无法确认完结。")
        self.assertNotIn("已确认华为", reviewed["coverage"][1]["gap"])
        self.assertEqual(reviewed["coverage"][1]["status"], "partial")
        self.assertEqual(validate_brief(reviewed, task, visible)["coverage"][1]["status"], "partial")

    def test_original_unanswered_can_recover_at_most_partial(self):
        task, visible, brief = fixture()
        brief["coverage"][0]["status"] = "unanswered"
        brief["coverage"][0]["gap"] = "未经支持的旧断言：各方回应已核实。"
        reviewed, _ = apply_claim_review(
            brief, task,
            audit(coverage=[
                {"question_id": "Q1", "status": "answered", "gap": "仍需读取官方回应原文。", "next_action": ""},
                {"question_id": "Q2", "status": "answered", "gap": "核验结果仍不明确。", "next_action": ""},
            ]),
        )
        self.assertEqual(reviewed["coverage"][0]["status"], "partial")
        self.assertEqual(reviewed["coverage"][0]["gap"], "仍需读取官方回应原文。")
        self.assertNotIn("各方回应已核实", reviewed["coverage"][0]["gap"])
        self.assertEqual(validate_brief(reviewed, task, visible)["coverage"][0]["status"], "partial")

    def test_empty_review_gap_uses_conservative_fallback_for_incomplete_question(self):
        task, _, brief = fixture()
        reviewed, _ = apply_claim_review(
            brief, task,
            audit(coverage=[
                {"question_id": "Q1", "status": "answered", "gap": "", "next_action": ""},
                {"question_id": "Q2", "status": "answered", "gap": "", "next_action": ""},
            ]),
        )
        self.assertEqual(reviewed["coverage"][0]["status"], "answered")
        self.assertEqual(reviewed["coverage"][0]["gap"], "")
        self.assertEqual(reviewed["coverage"][1]["status"], "partial")
        self.assertEqual(reviewed["coverage"][1]["gap"], "原子问题仍有待核实的证据缺口。")

    def test_deletion_between_audit_and_live_revalidation_revokes_support(self):
        task, visible, brief = fixture()
        reviewed, _ = apply_claim_review(brief, task, audit())
        live = copy.deepcopy(visible)
        del live[brief["claims"][0]["supports"][0]["evidence_id"]]
        checked = validate_brief(reviewed, task, live)
        self.assertEqual(checked["claims"][0]["supports"], [])
        self.assertEqual(checked["coverage"][0]["status"], "unanswered")


if __name__ == "__main__":
    unittest.main()
