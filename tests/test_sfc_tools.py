"""Contract tests for independent Southern Finance tool adapters."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

import requests

from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.sfc_client import SFCClient
from backend.app.modules.workbench.sfc_tools import build_sfc_tools
from backend.app.modules.workbench.tool_registry import ToolContext, ToolRegistry


class FakeClient:
    available = True

    def __init__(self):
        self.calls = []
        self.answers = {}

    def _token(self):
        return "fixture-key"

    def request(self, method, path, context, **kwargs):
        self.calls.append((method, path, kwargs))
        value = self.answers.get(path, {"code": 0, "data": [{"title": "样本", "date": "2026-10-12"}]})
        if isinstance(value, list) and value and isinstance(value[0], dict) and "__page__" in value[0]:
            return value.pop(0)["__page__"]
        if isinstance(value, Exception):
            raise value
        return value


class FakeResponse:
    def __init__(self, data, status=200):
        self.status_code = status
        self.data = json.dumps(data, ensure_ascii=False).encode()
        self.closed = False

    def iter_content(self, chunk_size=32768):
        yield self.data

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class SFCToolsTest(unittest.TestCase):
    def setUp(self):
        self.client = FakeClient()
        self.registry = ToolRegistry(build_sfc_tools({}, client=self.client))
        self.context = ToolContext(stopped=Event(), on_response=lambda _: None, emit=lambda _: None)

    def run_tool(self, name, args):
        return self.registry.execute(name, args, self.context)

    def test_thirteen_independent_tools_and_capability(self):
        names = [definition["function"]["name"] for definition in self.registry.definitions()]
        self.assertEqual(len(names), 13)
        self.assertEqual(names, ["sfc_news", "sfc_timeline", "sfc_search", "sfc_research",
                                 "sfc_announcements", "sfc_quotes", "sfc_history", "sfc_finance",
                                 "sfc_macro", "sfc_margin", "sfc_lhb", "sfc_calendar", "sfc_themes"])
        self.assertTrue(all("financial_data" in self.registry.get(name).capabilities for name in names))
        self.assertFalse(any("search" in self.registry.get(name).capabilities for name in names))
        self.assertFalse(any("news-search" in str(definition) for definition in self.registry.definitions()))

    def test_smoke_each_tool_with_fake_client(self):
        cases = {
            "sfc_news": {"action": "recent"},
            "sfc_timeline": {"hours": 1},
            "sfc_search": {"keyword": "新能源", "category": 4},
            "sfc_research": {"title": "银行"},
            "sfc_announcements": {"code": "600519"},
            "sfc_quotes": {"code": "600519"},
            "sfc_history": {"code": "600519", "period": "dayk"},
            "sfc_finance": {"code": "600519", "kind": "cashflow"},
            "sfc_macro": {"action": "categories", "scope": "china"},
            "sfc_margin": {"code": "600519"},
            "sfc_lhb": {"date": "2026-10-12"},
            "sfc_calendar": {"action": "highlights"},
            "sfc_themes": {"action": "list"},
        }
        self.client.answers["/terminal/quotes/api/dayk"] = {"OHLCItems": [{"date": "2026-10-12"}]}
        for name, arguments in cases.items():
            with self.subTest(name=name):
                result = self.run_tool(name, arguments)
                self.assertEqual(result.content["tool"], name)
                self.assertEqual(result.content["provider"], "南方财经")
                self.assertIn("retrievedAt", result.content)

    def test_strict_validation_no_http_for_bad_args(self):
        for name, args in (("sfc_news", {"action": "unknown"}),
                           ("sfc_news", {"action": []}),
                           ("sfc_news", {"action": "recent", "code": "600519"}),
                           ("sfc_quotes", {"code": "https://bad.example"}),
                           ("sfc_research", {"title": "x", "url": "https://bad.example"}),
                           ("sfc_history", {"code": "600519", "startDate": "2026-99-01"}),
                           ("sfc_calendar", {"importantOnly": "true"}),
                           ("sfc_themes", {"action": "article"}),
                           ("sfc_search", {"keyword": "ok", "limit": 21}),
                           ("sfc_search", {"keyword": "ok", "category": {}})):
            with self.subTest(name=name, args=args), self.assertRaises(GatewayError) as raised:
                self.run_tool(name, args)
            self.assertEqual(raised.exception.status, 400)
        self.assertEqual(self.client.calls, [])

    def test_finance_cashflow_sorts_by_enddate(self):
        self.run_tool("sfc_finance", {"code": "600519", "kind": "cashflow"})
        method, path, kwargs = self.client.calls[-1]
        self.assertEqual(path, "/terminal/zxt-web/mongo/query")
        self.assertEqual(kwargs["body"]["collectionName"], "NC_TQ_FIN_PRGCFSTATEMENTNEW_2")
        self.assertEqual(kwargs["body"]["sortFields"], [{"field": "ENDDATE", "direction": "DESC"}])

    def test_margin_date_uses_collection_format(self):
        result = self.run_tool("sfc_margin", {"date": "2026-10-09", "limit": 2})
        body = self.client.calls[-1][2]["body"]
        self.assertEqual(body["filters"]["trade_date"]["value"], "20261009")
        self.assertFalse(result.content["dateIsLatestVerified"])

    def test_history_filters_start_and_marks_limited_coverage(self):
        self.client.answers["/terminal/quotes/api/dayk"] = {
            "OHLCItems": [{"date": "2026-10-12", "closePrice": 10},
                          {"date": "2026-10-11", "closePrice": 9},
                          {"date": "2026-10-10", "closePrice": 8}]}
        result = self.run_tool("sfc_history", {"code": "600519", "startDate": "2026-10-11"})
        self.assertEqual([row["date"] for row in result.content["items"]], ["2026-10-12", "2026-10-11"])
        self.assertEqual(result.content["coverage"]["oldestFetched"], "2026-10-10")
        self.assertFalse(result.content["coverage"]["completeForRequestedRange"])
        self.assertEqual(self.client.calls[0][2]["body"]["pageSize"], 100)
        self.assertEqual(self.client.calls[0][2]["body"]["datetime"], "20261011")

    def test_macro_search_is_explicitly_top_level(self):
        self.client.answers["/terminal/zxt-web/mongo/query"] = {
            "data": [{"INDIC_NAME": "国际金融"}, {"INDIC_NAME": "通货膨胀"}]}
        result = self.run_tool("sfc_macro", {"action": "category_search", "keyword": "金融"})
        self.assertEqual(result.content["count"], 1)
        self.assertEqual(result.content["coverage"]["level"], "top-level categories only")

    def test_calendar_eco_fin_are_distinct(self):
        self.client.answers["/terminal/zxt-web/investCalendar/list"] = {
            "code": 200, "data": [{"nowDate": "2026-10-13", "ecoData": [{"ecoNameCn": "GDP"}],
                                  "finEvents": [{"event": "会议"}]}]}
        eco = self.run_tool("sfc_calendar", {"action": "eco", "date": "2026-10-13"}).content
        fin = self.run_tool("sfc_calendar", {"action": "fin", "date": "2026-10-13"}).content
        self.assertIn("ecoNameCn", eco["items"][0])
        self.assertNotIn("finEvents", eco["items"][0])
        self.assertIn("event", fin["items"][0])
        self.assertNotIn("ecoData", fin["items"][0])

    def test_timeline_partial_error_and_bounded_pages(self):
        self.client.answers["/terminal/rest/article/flash"] = GatewayError("HTTP500", 502)
        self.client.answers["/console/newsflash/list"] = {"code": 0, "data": [
            {"id": 1, "title": "新闻", "createTime": datetime.now().isoformat()}]}
        result = self.run_tool("sfc_timeline", {"hours": 2})
        self.assertTrue(result.content["partial"])
        self.assertEqual(result.content["count"], 1)
        self.assertLessEqual(sum(path == "/console/newsflash/list" for _, path, _ in self.client.calls), 3)
        self.client.answers["/console/newsflash/list"] = GatewayError("HTTP500", 502)
        with self.assertRaises(GatewayError):
            self.run_tool("sfc_timeline", {})

    def test_timeline_drops_unknown_timestamps(self):
        self.client.answers["/terminal/rest/article/flash"] = {"code": 0, "data": [
            {"title": "没有时间", "content": "无法判断是否在最近一小时"},
            {"title": "有时间", "publishTime": int(datetime.now().timestamp() * 1000)}]}
        self.client.answers["/console/newsflash/list"] = {"code": 0, "data": []}
        result = self.run_tool("sfc_timeline", {"hours": 1})
        self.assertEqual(result.content["count"], 1)
        self.assertEqual(result.content["coverage"]["unknownTimeCount"], 1)

    def test_pagination_coverage_and_wide_financial_rows(self):
        self.client.answers["/terminal/rest/esSearch/searchkey/page"] = {
            "code": 0, "total": 100, "data": [{"title": "甲"}, {"title": "乙"}]}
        page = self.run_tool("sfc_search", {"keyword": "测试", "page": 2, "limit": 2}).content
        self.assertFalse(page["localTruncated"])
        self.assertIsNone(page["coverage"]["complete"])
        self.assertEqual(page["coverage"]["page"], 2)
        self.assertEqual(page["coverage"]["upstreamReportedTotal"], 100)
        wide = {f"FIELD_{index}": "金" * 120 for index in range(300)}
        wide.update({"SYMBOL": "600519", "ENDDATE": "2026-06-30", "report_date": "2026-08-30",
                     "notes": "正文" * 2000})
        self.client.answers["/terminal/zxt-web/mongo/query"] = {"code": 200, "data": [wide] * 20}
        result = self.run_tool("sfc_finance", {"code": "600519", "limit": 20}).content
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), 440 * 1024)
        self.assertTrue(result["fieldsTruncated"])
        self.assertGreater(result["omittedFieldCount"], 0)
        self.assertGreater(result["stringsTruncated"], 0)
        self.assertEqual(result["dataDate"], "2026-06-30")
        self.assertIn("SYMBOL", result["items"][0])
        self.assertIn("ENDDATE", result["items"][0])
        self.assertIn("report_date", result["items"][0])
        self.assertTrue(any("[截断]" in str(value) for value in result["items"][0].values()))

    def test_invalid_provider_date_is_not_reported_as_data_date(self):
        self.client.answers["/terminal/rest/article/recent"] = {"code": 0, "data": [
            {"title": "无效日期", "report_date": "2026-99-99", "date_updated": "garbled"}]}
        result = self.run_tool("sfc_news", {"action": "recent"})
        self.assertIsNone(result.content["dataDate"])
        self.client.answers["/terminal/rest/article/recent"] = {"code": 0, "data": [
            {"title": "有效日期", "report_date": "2026-10-11 12:30:00"}]}
        result = self.run_tool("sfc_news", {"action": "recent"})
        self.assertEqual(result.content["dataDate"], "2026-10-11")

    def test_sources_only_use_real_row_urls_and_redact_token(self):
        self.client.answers["/terminal/rest/article/recent"] = {"code": 0, "data": [
            {"title": "真新闻", "url": "https://www.sfccn.com/a/123", "content": "fixture-key"},
            {"title": "无网址", "url": "javascript:alert(1)"},
            {"title": "本机", "url": "http://127.0.0.1/private"},
            {"title": "含密钥", "url": "https://www.sfccn.com/a?token=fixture-key"}]}
        result = self.run_tool("sfc_news", {"action": "recent"})
        self.assertEqual([s["url"] for s in result.sources], ["https://www.sfccn.com/a/123"])
        self.assertEqual(result.content["items"][0]["content"], "[redacted]")


class SFCClientTest(unittest.TestCase):
    def setUp(self):
        self.context = ToolContext(stopped=Event(), on_response=lambda _: None, emit=lambda _: None)

    def test_missing_key_and_explicit_file_only(self):
        client = SFCClient({})
        self.assertFalse(client.available)
        with self.assertRaises(GatewayError):
            client.request("GET", "/terminal/rest/article/recent", self.context)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.json"
            path.write_text('{"api_key":"fixture-key"}')
            client = SFCClient({"SFC_KEY_FILE": str(path)})
            self.assertTrue(client.available)
            self.assertEqual(client._token(), "fixture-key")
            client2 = SFCClient({"SFC_TOKEN": "config-key", "SFC_KEY_FILE": str(path)})
            self.assertEqual(client2._token(), "config-key")

    def test_http_status_business_error_fixed_host_and_bearer(self):
        for response in (FakeResponse({}, 500), FakeResponse({"code": 500, "data": []})):
            transport = FakeTransport(response)
            client = SFCClient({"SFC_TOKEN": "fixture-key"}, transport=transport)
            with self.assertRaises(GatewayError):
                client.request("GET", "/terminal/rest/article/recent", self.context)
            self.assertTrue(response.closed)
        transport = FakeTransport(FakeResponse({"code": 0, "data": []}))
        client = SFCClient({"SFC_TOKEN": "fixture-key"}, transport=transport)
        with self.assertRaises(GatewayError):
            client.request("GET", "https://evil.example/steal", self.context)
        self.assertFalse(transport.calls)
        client.request("GET", "/terminal/rest/article/recent", self.context, auth="bearer")
        args, kwargs = transport.calls[0]
        self.assertEqual(args[1], "https://terminal.sfccn.com/terminal/rest/article/recent")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer fixture-key")
        self.assertFalse(kwargs["allow_redirects"])
        client.request("GET", "/console/newsflash/list", self.context, host="zxtop", auth="none")
        args, kwargs = transport.calls[-1]
        self.assertEqual(kwargs["headers"]["User-Agent"], "wxwork")
        self.assertNotIn("token", kwargs["headers"])
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_cancel_before_and_while_reading(self):
        self.context.stopped.set()
        client = SFCClient({"SFC_TOKEN": "fixture-key"}, transport=FakeTransport(FakeResponse({"code": 0})))
        with self.assertRaises(GatewayError) as raised:
            client.request("GET", "/terminal/rest/article/recent", self.context)
        self.assertEqual(raised.exception.status, 409)
        self.context.stopped.clear()

        class StopResponse(FakeResponse):
            def iter_content(inner, chunk_size=32768):
                self.context.stopped.set()
                yield inner.data

        response = StopResponse({"code": 0, "data": []})
        client = SFCClient({"SFC_TOKEN": "fixture-key"}, transport=FakeTransport(response))
        with self.assertRaises(GatewayError) as raised:
            client.request("GET", "/terminal/rest/article/recent", self.context)
        self.assertEqual(raised.exception.status, 409)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
