"""Calendar-day relation searches use local fixtures, never external services."""

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from backend.app.modules.explore.relation_lite_runtime import (
    RelationLiteRuntime, _freshness, _query, _yesterday_window_start,
)
from backend.app.modules.workbench.tool_registry import ToolResult


AS_OF = "2026-10-14T06:52:50.401Z"  # 14:52 on October 14 in Shanghai.


def article(ident, published_at):
    return {"id": ident, "title": f"A股科技股上涨报道 {ident}",
            "url": f"https://example.org/news/{ident}",
            "snippet": "报道讨论A股科技板块表现，具体原因仍需核对。",
            "publishedAt": published_at}


class SearchFixture:
    def __init__(self, rows, metadata=None):
        self.rows = rows
        self.metadata = metadata or {}
        self.calls = []

    def get(self, name):
        assert name == "web_search"
        return SimpleNamespace(max_calls=2)

    def execute(self, name, arguments, context, *, call_count):
        assert name == "web_search"
        self.calls.append((dict(arguments), call_count))
        return ToolResult(content={"searchedAt": AS_OF, **self.metadata},
                          sources=self.rows)


def search_once(question, rows, metadata=None, *, as_of=AS_OF):
    fixture = SearchFixture(rows, metadata)
    service = SimpleNamespace(search_tools=fixture, research_limits={"tool_calls": 2},
                              _stage=Mock(), _check_stop=Mock())
    runtime = RelationLiteRuntime.__new__(RelationLiteRuntime)
    runtime.service = service
    runtime.owner = "fixture-owner"
    runtime.run = {"id": "fixture-run", "question": question,
                   "time_policy": {"as_of": as_of}}
    runtime.control = {"stop": threading.Event(), "response": None}
    retrieval = {"selection": {}, "searches": [], "news": [], "tool_trace": []}
    sources = runtime._search_news([{"name": "A股科技股"}], retrieval)
    return sources, retrieval, fixture


class RelationCalendarSearchTests(unittest.TestCase):
    def test_yesterday_variants_use_frozen_shanghai_calendar_and_one_search(self):
        for wording in ("昨天", "昨日", "昨晚", "昨天上午"):
            with self.subTest(wording=wording):
                question = f"{wording}A股科技股为什么涨"
                self.assertEqual(_freshness(question, AS_OF), "oneWeek")
                self.assertEqual(_yesterday_window_start(question, AS_OF).isoformat(),
                                 "2026-10-13T00:00:00+08:00")
                self.assertIn("2026-10-13", _query("A股科技股", question, AS_OF))
                sources, retrieval, fixture = search_once(question, [
                    article("yesterday-morning", "2026-10-13T09:30:00+08:00"),
                    article("today-retrospective", "2026-10-14T08:30:00+08:00"),
                    article("day-before", "2026-10-12T23:59:59+08:00"),
                ])
                self.assertEqual(len(fixture.calls), 1)
                self.assertEqual(fixture.calls[0][0]["count"], 3)
                self.assertEqual(fixture.calls[0][0]["freshness"], "oneWeek")
                self.assertEqual([s["provider_id"] for s in sources],
                                 ["yesterday-morning", "today-retrospective"])
                trace = retrieval["tool_trace"][0]
                self.assertEqual(trace["window_start"], "2026-10-13T00:00:00+08:00")
                self.assertEqual(trace["filter_reasons"], {"outside_requested_window": 1})

    def test_missing_or_future_publication_is_not_yesterday_evidence(self):
        _, retrieval, _ = search_once("昨天A股科技股为什么涨", [
            article("missing", None),
            article("future", "2026-10-14T15:00:00+08:00"),
            article("right", "2026-10-13T15:00:00+08:00"),
        ])
        trace = retrieval["tool_trace"][0]
        self.assertEqual(trace["filter_reasons"],
                         {"missing_date": 1, "future_date": 1})
        self.assertEqual(retrieval["searches"][0]["count"], 1)

    def test_explicit_rolling_day_and_today_keep_existing_one_day_scope(self):
        for question in ("近24小时A股科技股为什么涨", "24小时内A股科技股为什么涨",
                         "今天A股科技股为什么涨", "昨天近24小时A股科技股为什么涨"):
            with self.subTest(question=question):
                self.assertEqual(_freshness(question, AS_OF), "oneDay")
                self.assertIsNone(_yesterday_window_start(question, AS_OF))
        for question in ("近24小时A股科技股为什么涨", "24小时内A股科技股为什么涨",
                         "昨天近24小时A股科技股为什么涨", "今天24小时内A股科技股为什么涨"):
            with self.subTest(rolling_query=question):
                query = _query("A股科技股", question, AS_OF)
                self.assertIn(question, query)
                self.assertNotIn("2026-10-13", query)
                self.assertNotIn("2026-10-14", query)

    def test_today_adds_its_calendar_date_and_explicit_date_is_preserved(self):
        self.assertIn("2026-10-14", _query("A股科技股", "今天A股科技股为什么涨", AS_OF))
        dated = "2026-10-13 A股科技股上涨原因"
        query = _query("A股科技股", dated, AS_OF)
        self.assertIn(dated, query)
        self.assertEqual(query, "A股科技股 2026-10-13 " + dated)

    def test_frozen_window_crosses_year_month_and_leap_day(self):
        cases = (
            ("2026-01-01T00:30:00+08:00", "2025-12-31T00:00:00+08:00", "2025-12-31"),
            ("2026-03-01T00:30:00+08:00", "2026-02-28T00:00:00+08:00", "2026-02-28"),
            ("2024-03-01T00:30:00+08:00", "2024-02-29T00:00:00+08:00", "2024-02-29"),
        )
        for as_of, start, date in cases:
            with self.subTest(as_of=as_of):
                self.assertEqual(_yesterday_window_start("昨日行情", as_of).isoformat(), start)
                self.assertIn(date, _query("A股科技股", "昨日行情", as_of))

    def test_provider_raw_empty_and_provider_filtered_are_distinct(self):
        cases = (
            ({"rawCount": 0, "excludedCount": 0, "excludedReasons": {}},
             "provider_empty"),
            ({"rawCount": 3, "excludedCount": 3, "excludedReasons": {"stale_date": 3}},
             "provider_filtered_out"),
            ({}, "provider_empty"),
        )
        for metadata, reason in cases:
            with self.subTest(metadata=metadata):
                sources, retrieval, fixture = search_once("昨天A股科技股为什么涨", [], metadata)
                self.assertEqual(sources, [])
                self.assertEqual(len(fixture.calls), 1)
                trace = retrieval["tool_trace"][0]
                brief = retrieval["searches"][0]
                self.assertEqual(trace["empty_reason"], reason)
                self.assertEqual(brief["empty_reason"], reason)
                self.assertEqual(trace["provider_raw_count"], metadata.get("rawCount"))
                self.assertEqual(brief["provider_excluded_count"], metadata.get("excludedCount"))
                self.assertEqual(brief["provider_filter_reasons"],
                                 metadata.get("excludedReasons", {}))
                self.assertEqual(brief["window_start"], "2026-10-13T00:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
