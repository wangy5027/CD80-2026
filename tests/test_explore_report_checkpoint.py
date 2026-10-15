"""The timeout checkpoint contains only approved, literally supported findings."""

import copy
import unittest

from backend.app.modules.explore.report_checkpoint import (
    EXPLANATION,
    build_checkpoint_report,
)


def source(ref, evidence_id, quote, **changes):
    return {
        "id": ref,
        "evidence_id": evidence_id,
        "title": "学校官网公告",
        "url": "https://www.example.edu.cn/news/1",
        "kind": "public_page",
        "content_kind": "extracted_page_text",
        "publisher": "学校官网",
        "summary": "未审核的较长来源概述",
        "text_used": "公告原文：" + quote + "。",
        "content_hash": "hash-" + ref,
        **changes,
    }


def support(ref, evidence_id, quote, *, background=False):
    return {"source_ref": ref, "evidence_id": evidence_id, "quote": quote,
            "content_hash": "hash-" + ref, "background_only": background}


def claim(claim_id, text, type_, support_row):
    return {"id": claim_id, "text": text, "type": type_,
            "support_status": "traceable", "supports": [support_row],
            "conditions": "", "event_at": "", "event_date_quote": ""}


class ReportCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.run = {
            "id": "run-1", "session_id": "session-1", "module": "event",
            "question": "学校学院设立情况如何？",
            "resolved_question": "学校学院设立情况如何？",
            "time_policy": {"as_of": "2026-10-13T20:00:00+08:00"},
            "source_report_id": "parent-report", "context_selection": {"mode": "auto", "file_ids": ["file-1"]},
            "context_files": [{"id": "file-1"}], "context_limitations": [],
            "authorized_public_urls": ["https://www.example.edu.cn/"],
            "model_transport_trace": [{"stage": "research", "status": "complete"}],
            "research_budget": {"tool_calls": 3},
        }
        self.research = {
            "task": {"original_input": self.run["question"],
                     "resolved_goal": self.run["resolved_question"],
                     "as_of": self.run["time_policy"]["as_of"]},
            "brief": {"validation_notes": []}, "historical_ids": [],
            "retrieval": {"tool_trace": [{"name": "fetch_public_page", "status": "complete"}]},
            "skills_used": [{"name": "event-research"}], "stop_reason": "model_final",
            "budget": {"tool_calls": 3},
        }
        self.sources = [
            source("S1", "eid-1", "国际金融学院已设立"),
            source("S2", "eid-2", "银行批准金融学院设立", evidence_role="ai_report"),
        ]
        self.brief = {
            "claims": [
                claim("C1", "学校公告称国际金融学院已设立。", "fact",
                      support("S1", "eid-1", "国际金融学院已设立")),
            ],
            "coverage": [{"question_id": "Q1", "question": self.run["question"],
                          "status": "answered", "claim_ids": ["C1"],
                          "gap": "", "next_action": ""}],
            "answer_status": "answered", "validation_notes": [], "conflicts": [],
        }
        self.reviews = [{"id": "C1", "decision": "approve", "type": "fact",
                         "support_count": 1, "question_ids": ["Q1"]}]

    def build(self):
        return build_checkpoint_report(self.run, self.research, self.brief,
                                       self.sources, self.reviews)

    def test_checkpoint_is_partial_with_source_trace_and_visible_explanation(self):
        report = self.build()
        self.assertIsNotNone(report)
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["report_variant"], "review_timeout_checkpoint")
        self.assertEqual(report["answer_status"], "answered")
        self.assertEqual(report["writer_prompt_version"], "deterministic-checkpoint@1")
        self.assertEqual(report["source_report_id"], "parent-report")
        self.assertEqual(report["context_selection"]["file_ids"], ["file-1"])
        self.assertEqual(report["retrieval"]["tool_trace"][0]["name"], "fetch_public_page")
        self.assertEqual(report["sections"][0]["source_refs"], ["S1"])
        self.assertIn("S1 逐字支持片段", report["sections"][0]["body"])
        self.assertIn(EXPLANATION, report["sections"][0]["body"])
        self.assertIn(EXPLANATION, report["markdown"])
        self.assertEqual([item["id"] for item in report["confirmed_sources"]], ["S1"])
        self.assertEqual(report["confirmed_sources"][0]["summary"], "国际金融学院已设立")
        self.assertEqual(report["confirmed_sources"][0]["text_used"], "国际金融学院已设立")
        self.assertEqual(report["event_overview"], "")
        self.assertEqual(report["timeline"], [])
        self.assertEqual(report["uncertainties"], [])
        self.assertEqual(report["limitations"], [])

    def test_unreviewed_rejected_background_and_hypothesis_are_excluded(self):
        self.brief["claims"].extend([
            claim("C2", "未经审阅的另一说法。", "fact", support("S1", "eid-1", "国际金融学院已设立")),
            claim("C3", "被拒绝的说法。", "fact", support("S1", "eid-1", "国际金融学院已设立")),
            claim("C4", "推测其可能影响就业。", "hypothesis", support("S1", "eid-1", "国际金融学院已设立")),
            claim("C5", "AI 报告认为已获批准。", "fact", support("S2", "eid-2", "银行批准金融学院设立")),
        ])
        self.brief["coverage"][0]["claim_ids"].extend(["C2", "C3", "C4", "C5"])
        self.reviews.extend([
            {"id": "C3", "decision": "reject", "type": "fact", "support_count": 0, "question_ids": []},
            {"id": "C4", "decision": "approve", "type": "hypothesis", "support_count": 1,
             "question_ids": ["Q1"]},
            {"id": "C5", "decision": "approve", "type": "fact", "support_count": 1,
             "question_ids": ["Q1"]},
        ])
        report = self.build()
        self.assertEqual([row["id"] for row in report["claims"]], ["C1"])
        self.assertEqual(report["coverage"][0]["claim_ids"], ["C1"])
        for text in ("未经审阅", "被拒绝", "就业", "AI 报告"):
            self.assertNotIn(text, report["markdown"])

    def test_inference_remains_conditional_and_cannot_cite_absent_quote(self):
        self.brief["claims"].append(
            claim("C2", "若学院实际招生，则人才培养规模可能增加。", "inference",
                  support("S1", "eid-1", "国际金融学院已设立")))
        self.brief["coverage"][0]["claim_ids"].append("C2")
        self.reviews.append({"id": "C2", "decision": "narrow", "type": "inference",
                             "support_count": 1, "question_ids": ["Q1"]})
        report = self.build()
        self.assertEqual(report["claims"][1]["type"], "inference")
        self.assertIn("条件性推断", report["key_findings"][1])
        self.assertIn("仍需进一步核实", report["key_findings"][1])
        self.sources[0]["text_used"] = "这份文本并没有审阅引用的文字。"
        self.assertIsNone(self.build())

    def test_coverage_and_answer_status_only_downgrade_after_filtering(self):
        self.brief["claims"].append(
            claim("C2", "未经审阅的第二问结论。", "fact",
                  support("S1", "eid-1", "国际金融学院已设立")))
        self.brief["coverage"].append(
            {"question_id": "Q2", "question": "第二问", "status": "answered",
             "claim_ids": ["C2"], "gap": "", "next_action": "继续补证"})
        report = self.build()
        self.assertEqual(report["coverage"][0]["status"], "answered")
        self.assertEqual(report["coverage"][1]["status"], "unanswered")
        self.assertEqual(report["answer_status"], "partial")
        self.assertTrue(report["blocking_gaps"])
        self.brief["answer_status"] = "unanswered"
        self.assertEqual(self.build()["answer_status"], "unanswered")

    def test_requires_approved_nonbackground_literal_support(self):
        for mutation in ("reject", "background", "missing", "hash"):
            with self.subTest(mutation=mutation):
                sources = copy.deepcopy(self.sources)
                reviews = copy.deepcopy(self.reviews)
                if mutation == "reject":
                    reviews[0]["decision"] = "reject"
                elif mutation == "background":
                    sources[0]["evidence_role"] = "ai_report"
                elif mutation == "missing":
                    sources[0]["text_used"] = "无关资料"
                else:
                    sources[0]["content_hash"] = "different"
                self.assertIsNone(build_checkpoint_report(
                    self.run, self.research, self.brief, sources, reviews))

    def test_oversized_finding_skips_checkpoint_without_truncating_claim(self):
        self.brief["claims"][0]["text"] = "这是带条件的判断。" + "甲" * 1700
        self.brief["claims"][0]["conditions"] = "乙" * 1700
        self.assertIsNone(self.build())

    def test_other_modules_keep_checkpoint_partial_without_making_cases(self):
        for module in ("relation", "history"):
            with self.subTest(module=module):
                self.run["module"] = module
                report = self.build()
                self.assertIsNotNone(report)
                self.assertEqual(report["status"], "partial")
                self.assertEqual(report["sections"][0]["source_refs"], ["S1"])
                self.assertEqual(report["relations"], [])
                self.assertEqual(report["cases"], [])
                self.assertIn(EXPLANATION, report["markdown"])


if __name__ == "__main__":
    unittest.main()
