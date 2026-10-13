"""First-pass event Markdown stays readable without weakening source checks."""

import unittest

from backend.app.modules.explore.contracts import render_markdown, validate_model_report
from backend.app.modules.workbench.errors import GatewayError


def event_raw():
    return {
        "title": "华为事件初步探索",
        "summary": "本轮确认了一条公开消息。",
        "key_findings": ["媒体报道了公开回应。"],
        "uncertainties": [],
        "limitations": [],
        "sections": [{"heading": "消息核实", "body": "媒体报道了公开回应。", "source_refs": ["S1"]}],
        "recommendations": [{"question": "合作方如何分工？", "module": "relation", "reason": "追查实际合作安排"}],
        "actors": [], "drivers": [], "impact_paths": [], "timeline": [],
    }


def source(source_id="S1", **overrides):
    return {"id": source_id, "title": "原始资料", "url": "https://example.org/news?id=1",
            "content_kind": "extracted_page_text", "evidence_usage": "cited", **overrides}


class EventBriefContractTest(unittest.TestCase):
    def test_brief_allows_empty_event_overview_and_background_only_for_event(self):
        raw = event_raw()
        with self.assertRaises(GatewayError):
            validate_model_report(raw, "event", [source()], set())
        checked = validate_model_report(raw, "event", [source()], set(), compact_event=True)
        self.assertEqual(checked["event_overview"], "")
        self.assertEqual(checked["background"], "")
        self.assertEqual(checked["sections"][0]["source_refs"], ["S1"])

        explicit_empty = {**raw, "event_overview": "", "background": ""}
        self.assertEqual(validate_model_report(explicit_empty, "event", [source()], set(),
                                               compact_event=True)["background"], "")

    def test_brief_does_not_relax_core_types_or_source_references(self):
        for bad in ({"summary": ""}, {"key_findings": []}, {"sections": []},
                    {"event_overview": None}, {"background": None},
                    {"actors": "not an array"}):
            with self.subTest(bad=bad), self.assertRaises(GatewayError):
                validate_model_report({**event_raw(), **bad}, "event", [source()], set(), compact_event=True)

        invalid_ref = event_raw()
        invalid_ref["sections"][0]["source_refs"] = ["S999"]
        with self.assertRaises(GatewayError):
            validate_model_report(invalid_ref, "event", [source()], set(), compact_event=True)

    def test_brief_keeps_one_real_gap_inline_and_presents_next_step_before_sources(self):
        report = {**validate_model_report(event_raw(), "event", [source()], set(), compact_event=True),
                  "module": "event", "report_format": "event_brief@1", "question": "搜一下华为",
                  "resolved_question": "华为就事件回应了什么？", "as_of": "2026-10-13T12:00:00Z",
                  "key_findings": ["媒体报道了公开回应。", "合作协议原文仍需查证。"],
                  "coverage": [{"question_id": "Q1", "status": "answered", "gap": ""},
                               {"question_id": "Q2", "status": "partial", "gap": "合作协议原文仍需查证。"},
                               {"question_id": "Q3", "status": "unanswered", "gap": "还需核对产品定义的直接材料；再比较合作方分工。"}],
                  "inline_coverage_question_ids": ["Q2"],
                  "uncertainties": ["大量防御性警示"], "limitations": ["冗长限制说明"],
                  "summary": "重复的长篇概述。", "event_overview": "另一个重复概述", "background": "冗长背景",
                  "drivers": ["长篇动因"], "impact_paths": ["长篇影响路径"],
                  "timeline": [{"time": "2026", "event": "冗长时间线", "source_refs": ["S1"]}],
                  "sections": [{"heading": "消息核实", "body": "报道确实有这一句。", "source_refs": ["S1"]},
                               {"heading": "技术背景", "body": "解释术语本身。", "source_refs": ["S1"]},
                               {"heading": "影响路径", "body": "不在首轮展示。", "source_refs": ["S1"]}],
                  "sources": [source(text_used="一千字来源原文"),
                              source("S2", title="无关材料", evidence_usage="retrieved_only")],
                  "retrieval": {"searches": [{"query": "内部检索日志"}]}}
        markdown = render_markdown(report)
        self.assertIn("本轮问题：华为就事件回应了什么？", markdown)
        self.assertIn("## 核心发现", markdown)
        self.assertIn("- 待补充：还需核对产品定义的直接材料", markdown)
        self.assertIn("还需核对产品定义的直接材料；再比较合作方分工。", markdown)
        self.assertEqual(markdown.count("合作协议原文仍需查证"), 1)
        self.assertIn("## 消息核实", markdown)
        self.assertIn("## 技术背景", markdown)
        self.assertIn("**发现关联**：合作方如何分工？（追查实际合作安排）", markdown)
        self.assertLess(markdown.index("## 核心发现"), markdown.index("## 消息核实"))
        self.assertLess(markdown.index("## 技术背景"), markdown.index("## 下一步研究"))
        self.assertLess(markdown.index("## 下一步研究"), markdown.index("## 来源"))
        self.assertIn("[原始资料](https://example.org/news?id=1)；网页原文", markdown)
        for hidden in ("## 重要不确定性", "## 信息缺口与限制", "## 检索记录", "## 时间线",
                       "## 动因", "## 影响路径", "一千字来源原文", "无关材料", "内部检索日志",
                       "重复的长篇概述", "冗长背景", "不在首轮展示"):
            self.assertNotIn(hidden, markdown)

    def test_new_brief_tolerates_optional_missing_fields_and_rejects_unsafe_source_url(self):
        report = {"module": "event", "report_format": "event_brief@1", "title": "简报",
                  "question": "事件如何？", "key_findings": ["目前材料有限。"],
                  "coverage": [{"status": "unanswered", "gap": "尚缺公告原文"}],
                  "sources": [source(title="坏\n标题", url="javascript:alert(1)")]}
        markdown = render_markdown(report)
        self.assertIn("待补充：尚缺公告原文", markdown)
        self.assertIn("坏 标题", markdown)
        self.assertNotIn("javascript:", markdown)
        self.assertNotIn("坏\n标题", markdown)

    def test_opposite_meanings_do_not_deduplicate_a_critical_gap(self):
        report = {"module": "event", "report_format": "event_brief@1", "title": "简报",
                  "question": "模型是否发布？", "key_findings": ["该模型已经核实。"],
                  "coverage": [{"status": "partial", "gap": "该模型尚未核实。"}]}
        markdown = render_markdown(report)
        self.assertIn("该模型已经核实。", markdown)
        self.assertIn("- 待补充：该模型尚未核实。", markdown)

    def test_gap_keeps_later_missing_evidence_after_an_initial_positive_clause(self):
        report = {"module": "event", "report_format": "event_brief@1", "title": "简报",
                  "question": "责任如何划分？", "key_findings": ["已有发布会摘要。"],
                  "coverage": [{"status": "partial",
                                "gap": "已有发布会摘要；合同责任边界尚缺原文。"}]}
        markdown = render_markdown(report)
        self.assertIn("- 待补充：已有发布会摘要；合同责任边界尚缺原文。", markdown)

    def test_reviewed_marker_skips_only_its_question_and_keeps_other_gap(self):
        report = {"module": "event", "report_format": "event_brief@1", "title": "简报",
                  "question": "发布与功能表现？", "key_findings": ["首轮没有读到完整发布会公报。"],
                  "coverage": [{"question_id": "Q1", "status": "partial", "gap": "发布会原文仍需核对。"},
                               {"question_id": "Q2", "status": "unanswered", "gap": "功能表现尚缺实测资料。"}],
                  "inline_coverage_question_ids": ["Q1"]}
        markdown = render_markdown(report)
        self.assertIn("首轮没有读到完整发布会公报。", markdown)
        self.assertNotIn("- 待补充：发布会原文仍需核对。", markdown)
        self.assertIn("- 待补充：功能表现尚缺实测资料。", markdown)

    def test_unmarked_exact_full_gap_is_deduplicated_but_longer_gap_is_kept(self):
        report = {"module": "event", "report_format": "event_brief@1", "title": "简报",
                  "question": "是否有公告？", "key_findings": ["公告原文尚缺。"],
                  "coverage": [{"question_id": "Q1", "status": "partial", "gap": "公告原文尚缺。"},
                               {"question_id": "Q2", "status": "unanswered",
                                "gap": "公告原文尚缺；也缺实际功能的测试结果。"}]}
        markdown = render_markdown(report)
        self.assertEqual(markdown.count("公告原文尚缺。"), 1)
        self.assertNotIn("- 待补充：公告原文尚缺。", markdown)
        self.assertIn("- 待补充：公告原文尚缺；也缺实际功能的测试结果。", markdown)

    def test_relation_report_ignores_event_marker_and_keeps_legacy_rendering(self):
        raw = {**event_raw(), "event_overview": "概述", "background": "背景"}
        data = validate_model_report(raw, "event", [source()], set())
        other = {**data, "module": "relation", "report_format": "event_brief@1",
                 "inline_coverage_question_ids": ["Q1"], "title": "关系报告", "question": "关联如何？",
                 "as_of": "2026-10-13T12:00:00Z", "status": "partial", "sources": [],
                 "coverage": [{"question_id": "Q1", "question": "关联如何？",
                               "status": "partial", "gap": "仍缺公告"}]}
        other["limitations"] = ["旧版限制仍在"]
        markdown = render_markdown(other)
        self.assertIn("模块：relation", markdown)
        self.assertIn("## 信息缺口与限制", markdown)
        self.assertNotIn("本轮问题：", markdown)

    def test_old_event_report_uses_existing_long_rendering(self):
        raw = {**event_raw(), "event_overview": "完整事件概述", "background": "完整背景"}
        data = validate_model_report(raw, "event", [source()], set())
        old = {**data, "module": "event", "question": "原始问题", "as_of": "2026-10-13T12:00:00Z",
               "status": "partial", "sources": [source(text_used="原文摘录")],
               "retrieval": {"searches": [{"query": "原检索", "freshness": "noLimit", "status": "complete",
                                           "count": 1, "searched_at": "2026-10-13T12:00:00Z"}]}}
        old["uncertainties"] = ["仍有不确定性"]
        old["limitations"] = ["旧版保留限制"]
        markdown = render_markdown(old)
        self.assertIn("## 重要不确定性", markdown)
        self.assertIn("## 信息缺口与限制", markdown)
        self.assertIn("## 检索记录", markdown)
        self.assertIn("实际使用文本", markdown)
        self.assertIn("## 事件概述", markdown)


if __name__ == "__main__":
    unittest.main()
