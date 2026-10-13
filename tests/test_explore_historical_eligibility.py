"""Historical case source eligibility requires a quoted old event date."""

import unittest

from backend.app.modules.explore.historical_eligibility import eligible_historical_ids


AS_OF = "2026-10-13T12:00:00+08:00"


def claim(event_at="2020-02-29", event_date_quote="2020-02-29 发生了该事件。", *,
          quote=None, source_ref="S1", **overrides):
    item = {"id": "C1", "text": "该事件发生于旧日期", "type": "fact",
            "support_status": "traceable", "event_at": event_at,
            "event_date_quote": event_date_quote,
            "supports": [{"source_ref": source_ref,
                          "quote": quote if quote is not None else event_date_quote,
                          "background_only": False}]}
    item.update(overrides)
    return item


class HistoricalEligibilityTests(unittest.TestCase):
    def test_iso_and_chinese_dates_are_exactly_matched(self):
        english = claim(quote="概述：2020-02-29 发生了该事件。随后另有报道。")
        chinese = claim(event_date_quote="2019 年 6 月 7 日发生的事件", event_at="2019-06-07",
                        source_ref="S2")
        self.assertEqual(eligible_historical_ids([english, chinese], AS_OF), {"S1", "S2"})

    def test_recent_retrospective_qualifies_without_publication_age_rule(self):
        retrospective = claim(event_at="1998-01-02", event_date_quote="1998年1月2日该厂停产",
                              published_at="2026-10-12")
        self.assertEqual(eligible_historical_ids([retrospective], AS_OF), {"S1"})

    def test_publication_date_alone_does_not_qualify(self):
        old_article = claim(event_at="2010-01-01", event_date_quote="",
                            published_at="2010-01-01",
                            supports=[{"source_ref": "S1", "quote": "业务关系概述。",
                                       "published_at": "2010-01-01", "background_only": False}])
        self.assertEqual(eligible_historical_ids([old_article], AS_OF), set())

    def test_rejects_missing_wrong_and_impossible_dates(self):
        for item in (
            claim(event_at="2020-02-30"),
            claim(event_at="2020-02-29", event_date_quote="2020-03-01 发生了事件"),
            claim(event_at="2020-02-29", event_date_quote="2月29日发生了事件"),
            claim(event_at="2020-02-29", event_date_quote="12020-02-29 发生了事件"),
            claim(event_at="2020-02-29T00:00:00"),
            claim(event_at="2020-02-29", event_date_quote="2020年2月30日发生了事件"),
        ):
            with self.subTest(item=item):
                self.assertEqual(eligible_historical_ids([item], AS_OF), set())

    def test_rejects_current_or_future_events_and_bad_as_of(self):
        for event_at in ("2026-10-13", "2026-10-14"):
            with self.subTest(event_at=event_at):
                self.assertEqual(eligible_historical_ids(
                    [claim(event_at=event_at, event_date_quote=event_at + " 发生了事件")], AS_OF), set())
        self.assertEqual(eligible_historical_ids([claim()], "2026-02-30"), set())

    def test_date_must_be_in_same_non_background_support_quote(self):
        item = claim(supports=[
            {"source_ref": "S1", "quote": "这篇文章讨论企业历史。", "background_only": False},
            {"source_ref": "S2", "quote": "2020-02-29 发生了该事件。", "background_only": True},
        ])
        self.assertEqual(eligible_historical_ids([item], AS_OF), set())
        item["supports"][1]["background_only"] = False
        self.assertEqual(eligible_historical_ids([item], AS_OF), {"S2"})

    def test_requires_traceable_non_hypothetical_claim(self):
        self.assertEqual(eligible_historical_ids([claim(support_status="unsupported")], AS_OF), set())
        self.assertEqual(eligible_historical_ids([claim(type="hypothesis")], AS_OF), set())
        self.assertEqual(eligible_historical_ids([claim(supports=[{"source_ref": "S1",
            "quote": "2020-02-29 发生了该事件。"}])], AS_OF), set())

    def test_date_quote_must_be_verbatim_inside_support_quote(self):
        item = claim(event_date_quote="2020-02-29 发生了该事件。",
                     quote="2020-02-29 发生的是另一个事件。")
        self.assertEqual(eligible_historical_ids([item], AS_OF), set())


if __name__ == "__main__":
    unittest.main()
