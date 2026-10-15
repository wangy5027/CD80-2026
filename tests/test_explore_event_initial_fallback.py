import unittest

from backend.app.modules.explore.event_initial_fallback import (
    build_initial_fallback, present_initial_answer)


class InitialFallbackTests(unittest.TestCase):
    def test_partial_keeps_complete_qualified_sentence_not_unfinished_tail(self):
        answer, mode = build_initial_fallback("本周行情", "如果资金流入持续，反弹可能更有支撑。接下来分析", [])
        self.assertEqual(answer, "如果资金流入持续，反弹可能更有支撑。")
        self.assertEqual(mode, "model_partial")

    def test_does_not_turn_uncertain_sentence_into_firm_claim(self):
        text = "我无法联网，因此不能确认当前排名。现有材料尚不能证明甲公司是第一。"
        answer, mode = build_initial_fallback("排名", text, [])
        self.assertEqual(answer, "现有材料尚不能证明甲公司是第一。")
        self.assertEqual(mode, "model_partial")

    def test_no_text_uses_existing_source_as_plain_excerpt(self):
        answer, mode = build_initial_fallback("市场", "只有没写完的开头", [
            {"id": "S1", "text_used": "<p>公司拟增加研发投入。</p>更多内容尚未"}])
        self.assertEqual(answer, "公司拟增加研发投入。")
        self.assertEqual(mode, "source_excerpt")

    def test_unreviewed_zero_match_context_is_not_direct_excerpt(self):
        sources = [
            {"id": "S1", "text_used": "其他领域出现重大进展。", "relevance_score": 0},
            {"id": "S2", "text_used": "市场成交量有所变化。", "relevance_score": 4},
        ]
        answer, mode = build_initial_fallback("本周市场", "", sources)
        self.assertEqual((answer, mode), ("市场成交量有所变化。", "source_excerpt"))
        answer, mode = build_initial_fallback("本周市场", "", sources[:1])
        self.assertEqual(mode, "knowledge_fallback")
        self.assertNotIn("其他领域", answer)

    def test_legacy_source_without_score_still_supports_excerpt(self):
        answer, mode = build_initial_fallback("本周市场", "", [
            {"id": "S1", "text_used": "市场成交量有所变化。"}])
        self.assertEqual((answer, mode), ("市场成交量有所变化。", "source_excerpt"))

    def test_zero_text_and_sources_never_invents_a_current_ranking(self):
        answer, mode = build_initial_fallback("今天券商排名前三是谁？", "", [])
        self.assertEqual(mode, "knowledge_fallback")
        self.assertIn("口径", answer)
        self.assertNotIn("第一名", answer)
        self.assertNotIn("联网", answer)
        self.assertNotIn("重试", answer)

    def test_only_service_notice_falls_back_without_second_request(self):
        answer, mode = build_initial_fallback("这周股市", "本轮联网搜索失败，请稍后重试。", [])
        self.assertEqual(mode, "knowledge_fallback")
        self.assertIn("成交量", answer)
        self.assertNotIn("失败", answer)

    def test_decimal_inside_sentence_is_not_treated_as_full_stop(self):
        answer, mode = build_initial_fallback("收入", "材料显示收入增长3.5%。[S1] 后续", [
            {"id": "S1", "text_used": "材料显示收入增长3.5%。"}])
        self.assertEqual(answer, "材料显示收入增长3.5%。[S1]")
        self.assertEqual(mode, "model_partial")

    def test_unfinished_qualification_does_not_publish_unqualified_ranking(self):
        answer, mode = build_initial_fallback("本周排名", "甲公司本周排名第一。这个说法尚未", [])
        self.assertEqual(mode, "knowledge_fallback")
        self.assertNotIn("甲公司", answer)

    def test_unreferenced_current_number_is_not_promoted_from_partial_stream(self):
        answer, mode = build_initial_fallback("本周行情", "本周上涨12%。接下来", [])
        self.assertEqual(mode, "knowledge_fallback")
        self.assertNotIn("12%", answer)

    def test_general_ranking_method_does_not_require_news_evidence(self):
        text = "比较券商排名，先统一规模、盈利能力或具体业务等比较口径。"
        answer, mode = build_initial_fallback("券商排名", text + "接下来", [])
        self.assertEqual(mode, "model_partial")
        self.assertEqual(answer, text)

    def test_invalid_partial_reference_uses_only_actual_excerpt(self):
        answer, mode = build_initial_fallback("研发投入", "公司拟增加研发投入。[S99] 后续", [
            {"id": "S2", "text_used": "公司拟增加研发投入。"}])
        self.assertEqual(mode, "source_excerpt")
        self.assertEqual(answer, "公司拟增加研发投入。")
        self.assertNotIn("S99", answer)

    def test_citation_after_sentence_is_kept_with_completed_claim(self):
        answer, mode = build_initial_fallback("收入", "材料显示收入增长3.5%。[S1] 后续", [
            {"id": "S1", "text_used": "材料显示收入增长3.5%。"}])
        self.assertEqual(answer, "材料显示收入增长3.5%。[S1]")
        self.assertEqual(mode, "model_partial")

    def test_presentation_removes_temporary_citations_and_links_after_validation(self):
        text = "材料显示收入增长3.5%。[S1] 来源：https://news.example/a。另见 S3 新闻链接：https://news.example/b"
        displayed = present_initial_answer(text, [{"id": "S1"}], "收入情况怎么样？")
        self.assertIn("3.5%", displayed)
        self.assertNotIn("S1", displayed)
        self.assertNotIn("S3", displayed)
        self.assertNotIn("https://news.example", displayed)

    def test_presentation_preserves_a_user_supplied_url_and_entity_number(self):
        question = "Galaxy S1 的公告 https://user.example/news 说了什么？"
        displayed = present_initial_answer(
            "Galaxy S1 的公告见 https://user.example/news；临时来源[S2]见https://source.example/a。",
            [{"id": "S2"}], question)
        self.assertIn("Galaxy S1", displayed)
        self.assertIn("https://user.example/news", displayed)
        self.assertNotIn("[S2]", displayed)
        self.assertNotIn("https://source.example/a", displayed)


if __name__ == "__main__":
    unittest.main()
