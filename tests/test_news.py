"""News API contracts with a deterministic clock and no upstream network calls."""

from __future__ import annotations

import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.news import provider
from backend.app.modules.news.provider import FetchError, normalize_records
from backend.app.modules.news.service import NewsService
from backend.app.modules.workbench.errors import GatewayError


NEWS = "/desktop/api/news"
CST = timezone(timedelta(hours=8))
START = datetime(2026, 10, 13, 9, 30, tzinfo=CST)


def article(index=1, source="eastmoney", *, title=None, summary=None):
    return {
        "id": f"{source}-{index}",
        "source": source,
        "source_name": "东方财富财经快讯" if source == "eastmoney" else "财联社电报",
        "title": title if title is not None else f"财经标题{index}",
        "summary": summary if summary is not None else f"财经内容{index}",
        "published_at": (START - timedelta(minutes=index)).isoformat(),
        "url": f"https://finance.eastmoney.com/a/{index}.html" if source == "eastmoney" else None,
        "title_generated": False,
    }


class ManualClock:
    def __init__(self):
        self.seconds = 0.0

    def monotonic(self):
        return self.seconds

    def now(self):
        return START + timedelta(seconds=self.seconds)

    def advance(self, seconds):
        self.seconds += seconds


class NewsRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-news-test-")
        self.app = create_app({
            "TESTING": True,
            "DATA_DIR": str(Path(self.temp.name) / "data"),
            "AGENT_RUNTIME_DIR": str(Path(self.temp.name) / "runtime"),
            "GITEE_AI_API_KEY": "",
            "IMPORT_LEGACY_HISTORY": False,
            "NEWS_CACHE_TTL_SECONDS": 60,
            "NEWS_FETCH_TIMEOUT_SECONDS": 15,
            "NEWS_MAX_STALE_SECONDS": 900,
            "NEWS_RETRY_SECONDS": 30,
        })
        self.client = self.app.test_client()
        self.clock = ManualClock()
        self.calls = []

        def fetcher(source, timeout):
            self.calls.append((source, timeout))
            return [article(1, source), article(2, source), article(3, source)]

        self.fetcher = fetcher
        self.service = self.make_service()
        self.app.extensions["news_service"] = self.service

    def tearDown(self):
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def make_service(self, fetcher=None):
        return NewsService(
            self.app.config,
            fetcher=fetcher or self.fetcher,
            clock=self.clock.monotonic,
            now=self.clock.now,
        )

    def login(self):
        response = self.client.post(NEWS.replace("/news", "/register"), json={
            "name": "News test", "email": "news@example.invalid", "password": "test-only-123",
        })
        self.assertEqual(response.status_code, 200, response.json)

    def test_authentication_precedes_sources_and_fetch(self):
        for path in (NEWS, NEWS + "/sources", NEWS + "?limit=bad"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401, (path, response.json))
            self.assertIn("error", response.json)
        self.assertEqual(self.calls, [])

    def test_sources_are_local_and_do_not_fetch(self):
        self.login()
        response = self.client.get(NEWS + "/sources")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertIn("eastmoney", response.get_data(as_text=True))
        self.assertIn("cls", response.get_data(as_text=True))
        self.assertEqual(self.calls, [])

    def test_default_news_response_and_cache_metadata(self):
        self.login()
        response = self.client.get(NEWS)
        self.assertEqual(response.status_code, 200, response.json)
        data = response.json
        self.assertEqual(data["provider"], "akshare")
        self.assertEqual(data["source"], "eastmoney")
        self.assertEqual(data["pagination"]["limit"], 20)
        self.assertEqual(data["pagination"]["offset"], 0)
        self.assertEqual(data["pagination"]["total"], 3)
        self.assertEqual(len(data["items"]), 3)
        self.assertEqual(data["items"][0]["source"], "eastmoney")
        self.assertIn("+08:00", data["items"][0]["published_at"])
        self.assertEqual(data["cache"]["stale"], False)
        self.assertTrue(data["latest_published_at"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.calls, [("eastmoney", 15)])

    def test_invalid_or_duplicate_parameters_never_fetch(self):
        self.login()
        invalid_queries = (
            "source=unknown", "limit=0", "limit=101", "limit=1.5", "limit=abc",
            "offset=-1", "offset=1001", "offset=1.5", "offset=abc",
            "q=" + "中" * 81, "unsupported=1", "limit=1&limit=2", "source=cls&source=eastmoney",
        )
        for query in invalid_queries:
            response = self.client.get(NEWS + "?" + query)
            self.assertEqual(response.status_code, 400, (query, response.json))
            self.assertIn("error", response.json)
        self.assertEqual(self.calls, [])

    def test_pagination_and_search_use_one_cached_snapshot(self):
        self.login()
        self.app.extensions["news_service"] = self.make_service(
            lambda source, timeout: [
                article(1, source, title="股票上涨"),
                article(2, source, title="债券", summary="股票波动"),
                article(3, source, title="其他"),
            ]
        )
        response = self.client.get(NEWS + "?q=股票&limit=1&offset=1")
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([item["title"] for item in response.json["items"]], ["债券"])
        self.assertEqual(response.json["pagination"]["total"], 2)
        self.assertEqual(response.json["pagination"]["has_more"], False)

    def test_source_caches_are_separate(self):
        self.login()
        self.assertEqual(self.client.get(NEWS).status_code, 200)
        self.assertEqual(self.client.get(NEWS + "?source=cls").status_code, 200)
        self.assertEqual(self.client.get(NEWS).status_code, 200)
        self.assertEqual(self.calls, [("eastmoney", 15), ("cls", 15)])


class NewsServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = ManualClock()
        self.config = {
            "NEWS_CACHE_TTL_SECONDS": 60,
            "NEWS_FETCH_TIMEOUT_SECONDS": 15,
            "NEWS_MAX_STALE_SECONDS": 900,
            "NEWS_RETRY_SECONDS": 30,
        }
        self.calls = []
        self.failure = None

        def fetcher(source, timeout):
            self.calls.append((source, timeout))
            if self.failure is not None:
                raise self.failure
            return [article(1, source), article(2, source)]

        self.fetcher = fetcher
        self.service = self.make_service()

    def make_service(self, fetcher=None):
        return NewsService(
            self.config,
            fetcher=fetcher or self.fetcher,
            clock=self.clock.monotonic,
            now=self.clock.now,
        )

    def test_fresh_cache_is_reused_then_refreshed_after_ttl(self):
        first = self.service.get_news()
        self.assertEqual(first["cache"]["hit"], False)
        self.clock.advance(59)
        second = self.service.get_news(limit=1)
        self.assertEqual(second["cache"]["hit"], True)
        self.assertEqual(len(second["items"]), 1)
        self.assertEqual(len(self.calls), 1)
        self.clock.advance(2)
        refreshed = self.service.get_news()
        self.assertEqual(refreshed["cache"]["hit"], False)
        self.assertEqual(len(self.calls), 2)

    def test_failed_refresh_serves_stale_cache_and_obeys_retry_delay(self):
        self.service.get_news()
        self.clock.advance(61)
        self.failure = FetchError("upstream_error", 502, "新闻暂不可用。")
        stale = self.service.get_news()
        self.assertEqual(stale["cache"]["stale"], True)
        self.assertEqual(len(stale["items"]), 2)
        self.assertTrue(stale["warning"])
        self.assertEqual(len(self.calls), 2)
        self.clock.advance(10)
        stale_again = self.service.get_news()
        self.assertEqual(stale_again["cache"]["stale"], True)
        self.assertEqual(len(self.calls), 2)
        self.failure = None
        self.clock.advance(21)
        recovered = self.service.get_news()
        self.assertEqual(recovered["cache"]["stale"], False)
        self.assertEqual(len(self.calls), 3)

    def test_stale_cache_is_rejected_after_maximum_age(self):
        self.service.get_news()
        self.clock.advance(901)
        self.failure = FetchError("upstream_error", 502, "新闻暂不可用。")
        with self.assertRaises(GatewayError) as context:
            self.service.get_news()
        self.assertEqual(context.exception.status, 502)

    def test_empty_or_failed_initial_fetch_is_not_success(self):
        empty = self.make_service(fetcher=lambda _source, _timeout: [])
        with self.assertRaises(GatewayError) as context:
            empty.get_news()
        self.assertEqual(context.exception.status, 502)
        for code, status in (("timeout", 504), ("dependency_missing", 503)):
            failure = self.make_service(
                fetcher=lambda _source, _timeout, c=code, s=status: (_ for _ in ()).throw(FetchError(c, s, "新闻暂不可用。"))
            )
            with self.assertRaises(GatewayError) as raised:
                failure.get_news()
            self.assertEqual(raised.exception.status, status)
            self.assertEqual(raised.exception.code, code)

    def test_concurrent_expired_reads_fetch_once(self):
        entered = threading.Event()
        release = threading.Event()
        calls = []

        def slow_fetch(source, timeout):
            calls.append((source, timeout))
            if len(calls) == 1:
                return [article(1, source)]
            entered.set()
            if not release.wait(timeout=2):
                raise AssertionError("test release was not signalled")
            return [article(1, source)]

        self.service = self.make_service(slow_fetch)
        self.service.get_news()
        self.clock.advance(61)
        ready = threading.Barrier(4)

        def get_news():
            ready.wait(timeout=2)
            return self.service.get_news()

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(get_news) for _ in range(4)]
            self.assertTrue(entered.wait(timeout=2))
            release.set()
            results = [future.result(timeout=2) for future in futures]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(result["items"] for result in results))


class NewsProviderTests(unittest.TestCase):
    def test_eastmoney_normalizes_sorts_deduplicates_and_rejects_unsafe_rows(self):
        records = [
            {"标题": " 较早新闻 ", "摘要": "<b>市场</b>变化", "发布时间": "2026-10-13 09:10:00", "链接": "https://finance.eastmoney.com/a/1.html"},
            {"标题": "最新新闻", "摘要": "最新内容", "发布时间": "2026-10-13 09:20:00", "链接": "javascript:alert(1)"},
            {"标题": "最新新闻", "摘要": "最新内容", "发布时间": "2026-10-13 09:20:00", "链接": "javascript:alert(1)"},
            {"标题": "日期错误", "摘要": "内容", "发布时间": "bad-date", "链接": "https://example.com/invalid"},
            {"标题": "", "摘要": "", "发布时间": "2026-10-13 09:30:00", "链接": "https://example.com/empty"},
        ]
        items = normalize_records("eastmoney", records)
        self.assertEqual(len(items), 2)
        self.assertEqual([item["title"] for item in items], ["最新新闻", "较早新闻"])
        self.assertEqual(items[0]["url"], None)
        self.assertEqual(items[0]["published_at"], "2026-10-13T09:20:00+08:00")
        self.assertNotIn("<", items[1]["summary"])
        self.assertEqual(len({item["id"] for item in items}), 2)

    def test_cls_generates_missing_title_and_combines_date_time(self):
        items = normalize_records("cls", [{
            "标题": "", "内容": "财联社报道，市场波动。",
            "发布日期": "2026-10-13T00:00:00.000", "发布时间": "09:35:29",
        }])
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertTrue(item["title"])
        self.assertTrue(item["title_generated"])
        self.assertEqual(item["published_at"], "2026-10-13T09:35:29+08:00")
        self.assertEqual(item["url"], None)
        self.assertEqual(item["source"], "cls")

    def test_subprocess_timeout_is_bounded_and_does_not_reveal_diagnostics(self):
        with patch.object(provider.subprocess, "run", side_effect=subprocess.TimeoutExpired("secret command", 15)) as run:
            with self.assertRaises(FetchError) as context:
                provider.fetch_source("eastmoney", 15)
        self.assertEqual(context.exception.status, 504)
        self.assertNotIn("secret", str(context.exception))
        self.assertEqual(run.call_count, 1)

    def test_subprocess_dependency_and_failure_do_not_leak_worker_output(self):
        results = (
            (subprocess.CompletedProcess([], 1, '{"error":"dependency_missing"}', "private-path"), 503),
            (subprocess.CompletedProcess([], 1, "unexpected private-path", "private-path"), 502),
        )
        for result, status in results:
            with self.subTest(status=status), patch.object(provider.subprocess, "run", return_value=result):
                with self.assertRaises(FetchError) as context:
                    provider.fetch_source("eastmoney", 15)
                self.assertEqual(context.exception.status, status)
                self.assertNotIn("private-path", str(context.exception))


if __name__ == "__main__":
    unittest.main()
