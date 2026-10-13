"""Tools work without a Skill, and capability policy applies to execution."""

from __future__ import annotations

import threading
import unittest

from backend.app.modules.workbench.builtin_tools import make_search_tool
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolContext, ToolRegistry, ToolResult, ToolSpec


class FakeSearcher:
    available = True

    def __init__(self):
        self.calls = []
        self.error = None

    def search(self, arguments, stopped, on_response):
        self.calls.append(dict(arguments))
        if self.error is not None:
            raise self.error
        return {
            "query": arguments["query"],
            "freshness": arguments.get("freshness", "noLimit"),
            "sources": [{"title": "新闻", "url": "https://example.org/news", "publishedAt": "2026-10-12T10:00:00+08:00"}],
        }


class ToolRegistryTests(unittest.TestCase):
    def setUp(self):
        self.searcher = FakeSearcher()
        self.registry = ToolRegistry((make_search_tool(self.searcher),))
        self.events = []
        self.context = ToolContext(threading.Event(), lambda _response: None, self.events.append, query="用户原问题")

    @staticmethod
    def local_tool():
        return ToolSpec(
            name="local_math",
            description="本地计算两个数之和。",
            parameters={"type": "object", "properties": {"left": {"type": "number"}, "right": {"type": "number"}}},
            handler=lambda args, _context: ToolResult({"sum": args["left"] + args["right"]}),
            capabilities=frozenset({"calculate"}),
        )

    def test_search_executes_without_loading_any_skill(self):
        self.assertTrue(self.registry.has_capability("search"))
        self.assertEqual(self.registry.first_available("search").name, "web_search")
        self.assertEqual([item["function"]["name"] for item in self.registry.definitions()], ["web_search"])
        result = self.registry.execute("web_search", {"query": "最新新闻", "freshness": "oneDay", "count": 2}, self.context)
        self.assertEqual(self.searcher.calls, [{"query": "最新新闻", "freshness": "oneDay", "count": 2}])
        self.assertEqual(result.sources[0]["url"], "https://example.org/news")
        self.assertNotIn("sources", result.content)
        self.assertIn("外部不可信资料", result.content["note"])
        self.assertEqual([event["status"] for event in self.events], ["searching", "complete"])

    def test_unrelated_tool_works_when_search_capability_is_blocked(self):
        self.registry.register(self.local_tool())
        definitions = self.registry.definitions(blocked_capabilities={"search"})
        self.assertEqual([item["function"]["name"] for item in definitions], ["local_math"])
        result = self.registry.execute("local_math", {"left": 3, "right": 4}, self.context, blocked_capabilities={"search"})
        self.assertEqual(result.content, {"sum": 7})
        self.assertIsNone(result.sources)
        with self.assertRaises(GatewayError) as rejected:
            self.registry.execute("web_search", {"query": "should not run"}, self.context, blocked_capabilities={"search"})
        self.assertEqual(rejected.exception.status, 403)
        self.assertEqual(self.searcher.calls, [])

    def test_required_search_repairs_bad_model_arguments_without_skill(self):
        self.context.required = True
        search = self.registry.get("web_search")
        self.assertEqual(search.fallback_arguments("另一个问题"), {"query": "另一个问题", "count": 5, "freshness": "oneDay"})
        self.registry.execute("web_search", {"query": "", "count": True, "freshness": []}, self.context)
        self.assertEqual(self.searcher.calls, [{"query": "用户原问题", "count": 5, "freshness": "oneDay"}])

    def test_registration_unknown_name_and_per_tool_budget(self):
        with self.assertRaises(ValueError):
            self.registry.register(make_search_tool(self.searcher))
        with self.assertRaises(ValueError):
            self.registry.register(ToolSpec("load_skill", "reserved", {}, lambda a, c: ToolResult({})))
        with self.assertRaises(ValueError):
            self.registry.register(ToolSpec("bad name", "invalid", {}, lambda a, c: ToolResult({})))
        with self.assertRaises(GatewayError):
            self.registry.execute("unknown", {}, self.context)
        with self.assertRaises(GatewayError) as exhausted:
            self.registry.execute("web_search", {"query": "not searched"}, self.context, call_count=3)
        self.assertIn("上限", str(exhausted.exception))
        self.assertEqual(self.searcher.calls, [])

    def test_availability_and_external_errors_do_not_leak(self):
        self.searcher.available = False
        self.assertEqual(self.registry.definitions(), [])
        self.assertFalse(self.registry.has_capability("search"))
        with self.assertRaises(GatewayError) as unavailable:
            self.registry.execute("web_search", {"query": "news"}, self.context)
        self.assertEqual(unavailable.exception.status, 503)
        self.searcher.available = True
        self.searcher.error = RuntimeError("secret credential in exception")
        with self.assertRaises(GatewayError) as failed:
            self.registry.execute("web_search", {"query": "news"}, self.context)
        self.assertNotIn("secret credential", str(failed.exception))
        self.assertEqual(self.events[-1]["status"], "error")

    def test_cancellation_preserves_stop_error(self):
        self.searcher.error = GatewayError("已停止生成。", 409)
        with self.assertRaises(GatewayError) as stopped:
            self.registry.execute("web_search", {"query": "news"}, self.context)
        self.assertEqual(stopped.exception.status, 409)
        self.assertEqual([event["status"] for event in self.events], ["searching"])

    def test_public_catalog_and_serializable_result_boundary(self):
        self.registry.register(ToolSpec(
            "unsafe_output", "返回无效对象。", {"type": "object"},
            lambda _args, _context: ToolResult({"opaque": object()}),
        ))
        catalog = self.registry.catalog(include_parameters=True)
        self.assertEqual(catalog[0]["name"], "web_search")
        self.assertEqual(catalog[0]["capabilities"], ["search"])
        self.assertIn("parameters", catalog[0])
        self.assertNotIn("handler", catalog[0])
        with self.assertRaises(GatewayError) as caught:
            self.registry.execute("unsafe_output", {}, self.context)
        self.assertEqual(caught.exception.status, 502)
        self.assertNotIn("object at", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
