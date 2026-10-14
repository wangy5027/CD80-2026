"""Focused regression checks for the event workflow's one-search intent."""

import unittest

from backend.app.modules.explore.event_search import (
    news_relevance_score, relevant_news, search_intent,
)


AS_OF = "2026-10-13T22:30:00+08:00"


class EventSearchIntentTests(unittest.TestCase):
    def test_spoken_two_day_index_question_uses_short_query_and_local_window(self):
        question = "带我了解一下沪深 300这两天的具体涨跌情况怎么样？"
        intent = search_intent(question, AS_OF)
        self.assertEqual(intent["freshness"], "oneWeek")
        self.assertEqual(intent["window_start"], "2026-10-12T00:00:00+08:00")
        self.assertEqual(intent["focus_question"], "沪深300这两天的具体涨跌情况怎么样")
        self.assertIn("沪深300", intent["query"])
        self.assertIn("2026年10月12日至2026年10月13日", intent["query"])
        self.assertIn("收盘", intent["query"])
        self.assertNotIn("带我了解一下", intent["query"])
        self.assertNotIn("怎么样", intent["query"])
        self.assertLessEqual(len(intent["query"]), 200)

    def test_trading_days_remain_candidate_window_without_guessing_holidays(self):
        intent = search_intent("CSI300近两交易日涨跌情况", AS_OF)
        self.assertEqual(intent["freshness"], "oneWeek")
        self.assertIsNone(intent["window_start"])
        self.assertIn("沪深300", intent["query"])
        self.assertIn("近两交易日", intent["query"])

    def test_calendar_window_uses_shanghai_date_for_utc_as_of(self):
        intent = search_intent("沪深300这两天走势", "2026-10-12T18:30:00Z")
        self.assertEqual(intent["window_start"], "2026-10-12T00:00:00+08:00")
        self.assertIn("2026年10月13日", intent["query"])

    def test_this_week_uses_frozen_shanghai_monday(self):
        intent = search_intent("沪深300本周走势", "2026-10-12T18:30:00Z")
        self.assertEqual(intent["as_of_date"], "2026-10-13")
        self.assertEqual(intent["window_start"], "2026-10-12T00:00:00+08:00")
        self.assertIn("2026年10月12日至2026年10月13日", intent["query"])

    def test_recent_week_remains_rolling_seven_days(self):
        intent = search_intent("A股近期走势", AS_OF)
        self.assertEqual(intent["freshness"], "oneWeek")
        self.assertEqual(intent["window_start"], "2026-10-06T22:30:00+08:00")

    def test_month_takes_precedence_over_recent_and_day_words(self):
        intent = search_intent("沪深300最近一个月涨跌如何？", AS_OF)
        self.assertEqual(intent["freshness"], "oneMonth")
        self.assertIsNone(intent["window_start"])

    def test_index_result_prefers_exact_index_and_rejects_different_index(self):
        question = "沪深300这两天的股票情况怎么样？"
        direct = {"title": "沪深300指数收盘观察", "snippet": "指数出现变化"}
        broad = {"title": "A股市场收盘观察", "snippet": "300只股票上涨"}
        self.assertTrue(relevant_news(question, direct))
        self.assertTrue(relevant_news(question, {
            "title": "CSI 300 Index closes higher", "snippet": "Market news"}))
        self.assertTrue(relevant_news(question, broad))
        self.assertGreater(news_relevance_score(question, direct),
                           news_relevance_score(question, broad))
        self.assertFalse(relevant_news(question, {
            "title": "上证指数收盘观察", "snippet": "300只股票上涨"}))
        self.assertFalse(relevant_news(question, {
            "title": "沪深3000策略报告", "snippet": "没有目标指数"}))

    def test_index_and_named_actor_can_each_supply_background(self):
        question = "华为和沪深300本周走势有什么关系？"
        index_only = {"title": "沪深300指数本周收盘", "snippet": "大盘出现波动"}
        both = {"title": "沪深300指数本周收盘", "snippet": "华为概念股影响市场情绪"}
        actor_only = {"title": "华为股票走势", "snippet": "市场情绪有所变化"}
        self.assertTrue(relevant_news(question, index_only))
        self.assertTrue(relevant_news(question, both))
        self.assertTrue(relevant_news(question, actor_only))
        self.assertGreater(news_relevance_score(question, both),
                           news_relevance_score(question, index_only))

    def test_specific_index_event_ranks_matching_news_ahead_of_background(self):
        question = "带我了解一下沪深300这两天成分股调整了哪些？"
        self.assertIn("成分股调整", search_intent(question, AS_OF)["query"])
        background = {"title": "沪深300指数收盘观察", "snippet": "指数走势与成交量变化"}
        direct = {"title": "沪深300指数样本调整", "snippet": "公布新纳入的公司名单"}
        self.assertTrue(relevant_news(question, background))
        self.assertTrue(relevant_news(question, direct))
        self.assertGreater(news_relevance_score(question, direct),
                           news_relevance_score(question, background))

    def test_single_subject_and_synonym_news_no_longer_require_all_words(self):
        stock_question = "华为概念股票本周怎么样？"
        subject_background = {"title": "华为发布新产品", "snippet": "产业链预期变化"}
        broad_background = {"title": "A股本周收盘观察", "snippet": "股票市场变化"}
        self.assertTrue(relevant_news(stock_question, subject_background))
        self.assertTrue(relevant_news(stock_question, broad_background))
        self.assertGreater(news_relevance_score(stock_question, subject_background),
                           news_relevance_score(stock_question, broad_background))
        self.assertTrue(relevant_news("沪深300本周分红情况如何？", {
            "title": "沪深300成分公司派现进展", "snippet": "多家公司披露现金派发安排"}))
        self.assertTrue(relevant_news("A股红利低波方向资金为何抱团？", {
            "title": "高股息风格获资金关注", "snippet": "银行与公用事业出现防御性配置"}))

    def test_generic_market_news_is_a_lower_ranked_candidate(self):
        item = {"title": "A股指数本周收盘", "snippet": "股票市场成交变化"}
        self.assertTrue(relevant_news("A股这周的股票情况怎么样？", item))
        self.assertTrue(relevant_news("华为概念股票这周怎么样？", item))
        direct = {"title": "华为概念股票本周观察", "snippet": "股票市场变化"}
        self.assertGreater(news_relevance_score("华为概念股票这周怎么样？", direct),
                           news_relevance_score("华为概念股票这周怎么样？", item))


if __name__ == "__main__":
    unittest.main()
