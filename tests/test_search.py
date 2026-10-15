"""Local HTTP tests for Gitee search; no real key or provider calls."""

import json
import html
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.search import GiteeSearch


class MockSearch(BaseHTTPRequestHandler):
    calls = []
    lock = threading.Lock()
    hold_started = threading.Event()
    hold_release = threading.Event()

    def log_message(self, _format, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        with type(self).lock:
            type(self).calls.append({"path": self.path, "body": body, "authorization": self.headers.get("Authorization")})
        if body["query"] == "redirect":
            self.send_response(302)
            self.send_header("Location", "https://example.com/steal")
            self.end_headers()
            return
        if body["query"] == "huge":
            response = b'{' + b'"padding":"' + (b"x" * (1024 * 1024 + 1)) + b'"}'
        elif body["query"] == "provider-error":
            response = json.dumps({"code": 500, "message": "mock-secret"}).encode()
        else:
            now = datetime.now(ZoneInfo("Asia/Shanghai"))
            recent = (now - timedelta(hours=2)).isoformat()
            stale = (now - timedelta(days=4)).isoformat()
            pages = [
                {"name": "新闻 A", "url": "https://example.com/news/1", "datePublished": recent, "summary": "已核实的摘要。"},
                {"name": "旧新闻", "url": "https://example.com/news/2", "datePublished": stale, "snippet": "过去的消息"},
                {"name": "没有日期", "url": "https://example.com/news/3", "summary": "日期未知"},
                {"name": "私网地址", "url": "http://127.0.0.1/private", "datePublished": recent},
                {"name": "重复新闻", "url": "https://example.com/news/1", "datePublished": recent},
                {"name": "危险协议", "url": "javascript:alert(1)", "datePublished": recent},
                {"name": "新闻 B", "url": "https://example.org/news/2", "datePublished": recent, "snippet": "后一条摘要。"},
            ]
            if body["query"] == "all-stale":
                pages = [{"name": f"旧报道 {index}",
                          "url": f"https://example.com/old/{index}",
                          "datePublished": (now - timedelta(hours=30 + index)).isoformat(),
                          "summary": "搜索上游确实返回了报道。"}
                         for index in range(3)]
            elif body["query"] == "provider-empty":
                pages = []
            elif body["query"] == "reason-buckets":
                pages = [
                    {"name": "有效新闻", "url": "https://example.com/accepted",
                     "datePublished": recent},
                    {"name": "同网址重复", "url": "https://example.com/accepted#section",
                     "datePublished": recent},
                    "not-a-row",
                    {"name": "", "url": "https://example.com/no-title", "datePublished": recent},
                    {"name": "未来报道", "url": "https://example.com/future",
                     "datePublished": (now + timedelta(hours=1)).isoformat()},
                    {"name": "未知日期", "url": "https://example.com/no-date"},
                    {"name": "过期报道", "url": "https://example.com/stale",
                     "datePublished": stale},
                ] + [{"name": f"有效候选 {index}",
                      "url": f"https://example.com/extra/{index}",
                      "datePublished": recent} for index in range(24)]
            if body["query"] == "html":
                table = ("<table><tr><th>指标</th><th>变化</th></tr>"
                         "<tr><td>&nbsp;</td><td> </td></tr>"
                         "<tr><td>--</td><td>—</td><td>-</td></tr>"
                         "<tr><td>利润</td><td>增长 &lt; 5%</td></tr>"
                         "<tr><td>收益</td><td>0</td><td>-5</td></tr>"
                         "<tr><td>--</td><td>真实业务</td><td>—</td></tr></table>"
                         "<script>不可见的脚本内容</script><style>.secret{display:none}</style>")
                pages = [
                    {"name": '<span data-long="' + "x" * 260 + '">某公司 &amp; 利润</span>'
                             "<script>机密标题</script>",
                     "url": "https://example.com/news/html", "datePublished": recent,
                     "summary": html.escape(html.escape(table))},
                    {"name": "&lt;b&gt;普通比较&lt;/b&gt;", "url": "https://example.org/news/math",
                     "datePublished": recent, "summary": "增长 < 5%，AT&amp;T 同期发布消息。"},
                ]
            response = json.dumps({"code": 200, "data": {"webPages": {"value": pages}}}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        if body["query"] == "hold":
            self.wfile.write(b'{"code":200,"data":')
            self.wfile.flush()
            type(self).hold_started.set()
            type(self).hold_release.wait(timeout=8)
            try:
                self.wfile.write(b'{}')
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.wfile.write(response)
        self.wfile.flush()


class GiteeSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), MockSearch)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-search-")
        self.config = {
            "TESTING": True,
            "GITEE_AI_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "GITEE_AI_API_KEY": "test-only-key",
            "GITEE_AI_SEARCH_CONNECT_TIMEOUT_SECONDS": 1,
            "GITEE_AI_SEARCH_READ_TIMEOUT_SECONDS": 2,
        }
        self.searcher = GiteeSearch(self.config)
        with MockSearch.lock:
            MockSearch.calls.clear()
        MockSearch.hold_started.clear()
        MockSearch.hold_release.clear()

    def tearDown(self):
        MockSearch.hold_release.set()
        self.temp.cleanup()

    def search(self, query="news", freshness="oneDay", count=5):
        return self.searcher.search({"query": query, "freshness": freshness, "count": count}, threading.Event(), lambda _response: None)

    def test_strict_date_filter_source_safety_payload_and_ids(self):
        result = self.search()
        self.assertEqual(result["query"], "news")
        self.assertEqual(result["freshness"], "oneDay")
        self.assertEqual([item["title"] for item in result["sources"]], ["新闻 A", "新闻 B"])
        self.assertEqual(result["excludedCount"], 5)
        self.assertEqual(result["rawCount"], 7)
        self.assertEqual(result["excludedReasons"], {
            "candidate_limit": 0, "invalid_row": 0, "invalid_source": 2,
            "duplicate": 1, "future_published_at": 0,
            "missing_published_at": 1, "outside_freshness": 1,
            "result_limit": 0})
        self.assertTrue(result["sources"][0]["publishedAt"].endswith("+08:00"))
        self.assertEqual(result["sources"][0]["snippet"], "已核实的摘要。")
        self.assertEqual(result["sources"][0]["id"], self.search()["sources"][0]["id"])
        with MockSearch.lock:
            sent = MockSearch.calls[0]
        self.assertEqual(sent["path"], "/v1/web-search")
        self.assertEqual(sent["authorization"], "Bearer test-only-key")
        self.assertEqual(sent["body"], {"query": "news", "count": 5, "summary": True, "freshness": "oneDay"})

    def test_no_limit_can_keep_undated_and_old_but_never_private_urls(self):
        result = self.search(freshness="noLimit")
        self.assertEqual([item["title"] for item in result["sources"]], ["新闻 A", "旧新闻", "没有日期", "新闻 B"])
        self.assertIsNone(result["sources"][2]["publishedAt"])
        self.assertEqual(result["rawCount"], 7)
        self.assertEqual(result["excludedCount"], 3)
        self.assertEqual(result["excludedReasons"]["missing_published_at"], 0)
        self.assertEqual(result["excludedReasons"]["outside_freshness"], 0)
        self.assertEqual(result["excludedReasons"]["invalid_source"], 2)

    def test_all_three_raw_results_outside_one_day_are_not_provider_empty(self):
        result = self.search(query="all-stale")
        self.assertEqual(result["rawCount"], 3)
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["excludedCount"], 3)
        self.assertEqual(result["excludedReasons"]["outside_freshness"], 3)
        self.assertEqual(sum(result["excludedReasons"].values()), result["excludedCount"])
        self.assertIn("时间范围", result["warning"])

        empty = self.search(query="provider-empty")
        self.assertEqual(empty["rawCount"], 0)
        self.assertEqual(empty["sources"], [])
        self.assertEqual(empty["excludedCount"], 0)
        self.assertTrue(all(value == 0 for value in empty["excludedReasons"].values()))

    def test_each_exclusion_reason_preserves_candidate_and_result_limits(self):
        result = self.search(query="reason-buckets", count=1)
        self.assertEqual(result["rawCount"], 31)
        self.assertEqual([item["title"] for item in result["sources"]], ["有效新闻"])
        self.assertEqual(result["excludedCount"], 30)
        self.assertEqual(result["excludedReasons"], {
            "candidate_limit": 1, "invalid_row": 1, "invalid_source": 1,
            "duplicate": 1, "future_published_at": 1,
            "missing_published_at": 1, "outside_freshness": 1,
            "result_limit": 23})
        with MockSearch.lock:
            self.assertEqual(MockSearch.calls[-1]["body"],
                             {"query": "reason-buckets", "count": 1,
                              "summary": True, "freshness": "oneDay"})

    def test_provider_html_is_plain_text_before_truncation_without_losing_math(self):
        result = self.search(query="html")
        self.assertEqual(len(result["sources"]), 2)
        first, second = result["sources"]
        self.assertEqual(first["title"], "某公司 & 利润")
        self.assertEqual(first["snippet"],
                         "指标 | 变化 ; 利润 | 增长 < 5% ; 收益 | 0 | -5 ; -- | 真实业务 | —")
        self.assertNotIn("不可见", first["snippet"])
        self.assertNotIn("机密", first["title"])
        self.assertEqual(second["title"], "普通比较")
        self.assertEqual(second["snippet"], "增长 < 5%，AT&T 同期发布消息。")
        self.assertEqual(first["url"], "https://example.com/news/html")

    def test_bounded_count_and_invalid_arguments_do_not_call_provider(self):
        result = self.search(count=50)
        self.assertEqual(len(result["sources"]), 2)
        with MockSearch.lock:
            self.assertEqual(MockSearch.calls[-1]["body"]["count"], 5)
            prior = len(MockSearch.calls)
        for arguments in ({"query": ""}, {"query": "x" * 201}, {"query": "x", "freshness": []}, {"query": "x", "count": True}):
            with self.assertRaises(GatewayError):
                self.searcher.search(arguments, threading.Event(), lambda _response: None)
        with MockSearch.lock:
            self.assertEqual(len(MockSearch.calls), prior)

    def test_missing_key_redirect_error_and_large_body_are_safe(self):
        no_key = GiteeSearch({**self.config, "GITEE_AI_API_KEY": ""})
        self.assertFalse(no_key.available)
        with self.assertRaises(GatewayError):
            no_key.search({"query": "news"}, threading.Event(), lambda _response: None)
        for query in ("redirect", "provider-error", "huge"):
            with self.assertRaises(GatewayError) as caught:
                self.search(query=query)
            self.assertNotIn("test-only-key", str(caught.exception))
            self.assertNotIn("mock-secret", str(caught.exception))
        with MockSearch.lock:
            self.assertEqual(len(MockSearch.calls), 3)

    def test_cancel_blocked_search_response(self):
        stopped = threading.Event()
        active = []
        errors = []
        response_ready = threading.Event()

        def capture(response):
            if response is not None:
                active.append(response)
                response_ready.set()

        def run():
            try:
                self.searcher.search({"query": "hold", "freshness": "noLimit"}, stopped, capture)
            except GatewayError as error:
                errors.append(error)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(MockSearch.hold_started.wait(timeout=2))
        self.assertTrue(response_ready.wait(timeout=2))
        stopped.set()
        active[0].abort()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors[0].status, 409)

    def test_production_credentials_cannot_target_other_hosts(self):
        with self.assertRaises(ValueError):
            GiteeSearch({"GITEE_AI_BASE_URL": "https://elsewhere.example/v1", "GITEE_AI_API_KEY": "dummy"})
        with self.assertRaises(ValueError):
            GiteeSearch({"GITEE_AI_BASE_URL": "http://127.0.0.1:10001/v1", "GITEE_AI_API_KEY": "dummy"})


if __name__ == "__main__":
    unittest.main()
