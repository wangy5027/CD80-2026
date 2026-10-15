"""The research ledger accepts only this run's read evidence and accumulates tool turns."""

from __future__ import annotations

import unittest
from uuid import uuid4

from backend.app.modules.explore.research_runtime import validate_brief


def visible_source(display_id="S1", text="官方公告确认该项目已经发布。", *, evidence_id=None):
    evidence_id = evidence_id or str(uuid4())
    return evidence_id, {
        "source": {
            "id": display_id,
            "evidence_id": evidence_id,
            "kind": "public_page",
            "content_hash": "example-hash",
        },
        "read_texts": [text],
        "read_spans": [{"start": 100, "end": 100 + len(text), "text": text}],
    }


def claim(claim_id, evidence_id, quote="官方公告确认该项目已经发布。", text=None):
    return {
        "id": claim_id,
        "text": text or "该项目已经发布",
        "type": "fact",
        "supports": [{"evidence_id": evidence_id, "quote": quote}],
    }


def task(questions=1):
    return {
        "subquestions": [
            {"id": f"Q{i}", "question": f"第 {i} 项是否已经发布？"}
            for i in range(1, questions + 1)
        ]
    }


def answered(question_id, claim_id):
    return {"question_id": question_id, "status": "answered", "claim_ids": [claim_id]}


class BriefIntegrityTests(unittest.TestCase):
    def test_unique_display_id_is_normalized_to_opaque_run_evidence(self):
        eid, source = visible_source()
        result = validate_brief(
            {"claims": [claim("C1", "S1")], "coverage": [answered("Q1", "C1")], "done": True},
            task(), {eid: source},
        )

        self.assertEqual(result["answer_status"], "answered")
        self.assertEqual(result["claims"][0]["supports"][0]["evidence_id"], eid)
        self.assertEqual(result["claims"][0]["supports"][0]["source_ref"], "S1")
        self.assertEqual(result["claims"][0]["supports"][0]["locations"],
                         [{"start": 100, "end": 100 + len("官方公告确认该项目已经发布。")}])

    def test_cross_run_opaque_id_and_ambiguous_display_id_are_rejected(self):
        current_id, current = visible_source()
        foreign_id = str(uuid4())
        foreign = validate_brief(
            {"claims": [claim("C1", foreign_id)], "coverage": [answered("Q1", "C1")]},
            task(), {current_id: current},
        )
        self.assertEqual(foreign["answer_status"], "unanswered")
        self.assertEqual(foreign["claims"][0]["supports"], [])
        self.assertTrue(any("证据编号不在本轮" in note for note in foreign["validation_notes"]))

        second_id, second = visible_source("S1")
        ambiguous = validate_brief(
            {"claims": [claim("C1", "S1")], "coverage": [answered("Q1", "C1")]},
            task(), {current_id: current, second_id: second},
        )
        self.assertEqual(ambiguous["answer_status"], "unanswered")
        self.assertTrue(any("来源编号歧义" in note for note in ambiguous["validation_notes"]))

    def test_existing_evidence_with_nonmatching_quote_is_diagnosed_separately(self):
        eid, source = visible_source()
        result = validate_brief(
            {"claims": [claim("C1", "S1", quote="这句话没有在原文中出现")],
             "coverage": [answered("Q1", "C1")]},
            task(), {eid: source},
        )
        self.assertEqual(result["answer_status"], "unanswered")
        self.assertTrue(any("支持片段未在本轮实际读取内容中找到" in note
                            for note in result["validation_notes"]))
        self.assertFalse(any("证据编号不在本轮" in note for note in result["validation_notes"]))

    def test_tool_turn_empty_arrays_preserve_prior_claims_coverage_and_conflicts(self):
        eid, source = visible_source()
        first = validate_brief(
            {"claims": [claim("C1", "S1"), claim("C2", eid, text="项目已公开宣布")],
             "coverage": [answered("Q1", "C1"), answered("Q2", "C2")],
             "conflicts": [{"claim_ids": ["C1", "C2"], "description": "表述有差别"}],
             "done": False},
            task(2), {eid: source},
        )
        patched = validate_brief(
            {"claims": [], "coverage": [], "conflicts": [], "done": False},
            task(2), {eid: source}, first, incremental=True,
        )
        self.assertEqual([x["id"] for x in patched["claims"]], ["C1", "C2"])
        self.assertEqual([x["status"] for x in patched["coverage"]], ["answered", "answered"])
        self.assertEqual(len(patched["conflicts"]), 1)

        omitted = validate_brief({"done": False}, task(2), {eid: source},
                                 patched, incremental=True)
        self.assertEqual(omitted["answer_status"], "answered")
        self.assertEqual(len(omitted["conflicts"]), 1)

    def test_tool_turn_replaces_claim_by_id_and_updates_only_named_question(self):
        eid, source = visible_source()
        first = validate_brief(
            {"claims": [claim("C1", "S1"), claim("C2", eid)],
             "coverage": [answered("Q1", "C1"), answered("Q2", "C2")]},
            task(2), {eid: source},
        )
        patched = validate_brief(
            {"claims": [claim("C2", "S1", text="经核对，项目已正式发布")],
             "coverage": [{"question_id": "Q2", "next_action": "核对时间"}]},
            task(2), {eid: source}, first, incremental=True,
        )
        self.assertEqual([x["id"] for x in patched["claims"]], ["C1", "C2"])
        self.assertEqual(patched["claims"][1]["text"], "经核对，项目已正式发布")
        self.assertEqual(patched["claims"][1]["supports"][0]["evidence_id"], eid)
        self.assertEqual([x["status"] for x in patched["coverage"]], ["answered", "answered"])
        self.assertEqual(patched["coverage"][1]["next_action"], "核对时间")

    def test_invalid_coverage_reference_downgrades_instead_of_claiming_answer(self):
        eid, source = visible_source()
        first = validate_brief(
            {"claims": [claim("C1", eid)], "coverage": [answered("Q1", "C1")]},
            task(), {eid: source},
        )
        patched = validate_brief(
            {"coverage": [answered("Q1", "C999")]}, task(), {eid: source},
            first, incremental=True,
        )
        self.assertEqual(patched["claims"][0]["support_status"], "traceable")
        self.assertEqual(patched["coverage"][0]["claim_ids"], [])
        self.assertEqual(patched["coverage"][0]["status"], "unanswered")

    def test_final_full_ledger_can_explicitly_clear_previous_claims(self):
        eid, source = visible_source()
        first = validate_brief(
            {"claims": [claim("C1", "S1")], "coverage": [answered("Q1", "C1")]},
            task(), {eid: source},
        )
        final = validate_brief(
            {"claims": [], "coverage": [], "conflicts": [], "done": True},
            task(), {eid: source}, first,
        )
        self.assertEqual(final["claims"], [])
        self.assertEqual(final["answer_status"], "unanswered")
        self.assertTrue(final["done"])

    def test_deleted_evidence_cannot_reappear_via_same_display_id(self):
        old_id, old_source = visible_source()
        first = validate_brief(
            {"claims": [claim("C1", "S1")], "coverage": [answered("Q1", "C1")]},
            task(), {old_id: old_source},
        )
        revoked = validate_brief(first, task(), {})
        self.assertEqual(revoked["claims"][0]["supports"], [])
        self.assertEqual(revoked["answer_status"], "unanswered")

        new_id, new_source = visible_source("S1")
        continued = validate_brief(
            {"claims": [], "coverage": []}, task(), {new_id: new_source},
            revoked, incremental=True,
        )
        self.assertEqual(continued["claims"][0]["supports"], [])
        self.assertEqual(continued["answer_status"], "unanswered")


if __name__ == "__main__":
    unittest.main()
