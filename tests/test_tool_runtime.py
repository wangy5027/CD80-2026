"""Local HTTP simulations of the bounded LangGraph Skill/tool round trip."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GraphRuntime
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


class ToolProvider(BaseHTTPRequestHandler):
    requests = []
    lock = threading.Lock()

    def log_message(self, *_args):
        pass

    def _send_event(self, delta=None, finish_reason=None):
        body = {"choices": [{"delta": delta or {}, "finish_reason": finish_reason}]}
        self.wfile.write(("data: " + json.dumps(body, ensure_ascii=False) + "\n\n").encode())
        self.wfile.flush()

    def _tool(self, call_id, name, arguments, index=0):
        self._send_event({"tool_calls": [{"index": index, "id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}]})
        self._send_event({}, "tool_calls")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        with type(self).lock:
            type(self).requests.append((self.path, body))
        if self.path == "/v1/web-search":
            now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            query = body["query"]
            payload = {"code": 200, "data": {"webPages": {"value": [{"name": f"{query} 最新报道", "url": "https://example.org/news/" + str(len(type(self).requests)), "datePublished": now, "snippet": "验证用新闻摘要"}]}}}
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        query = next(item["content"] for item in reversed(body["messages"]) if item["role"] == "user")
        tool_messages = [item for item in body["messages"] if item["role"] == "tool"]
        if query == "direct-search" and not tool_messages:
            self._tool("direct-search", "web_search", '{"query":"独立工具新闻","freshness":"oneDay"}')
        elif query == "renamed-skill" and not tool_messages:
            self._tool("renamed-load", "load_skill", '{"name":"custom-news"}')
        elif query == "renamed-skill" and len(tool_messages) == 1:
            self._tool("renamed-search", "web_search", '{"query":"改名后新闻","freshness":"oneDay"}')
        elif query == "custom-local" and not tool_messages:
            self._tool("local-call", "local_echo", '{"text":"独立扩展"}')
        elif query == "custom-search" and not tool_messages:
            self._tool("custom-search-call", "alternate_lookup", '{"query":"替换搜索实现"}')
        elif query == "auto-flow" and not tool_messages:
            self._tool("load-a", "load_skill", '{"name":"news-search"}')
        elif query == "auto-flow" and len(tool_messages) == 1:
            # Both calls report index 0. IDs, rather than index, must separate them.
            self._send_event({"tool_calls": [{"index": 0, "id": "search-a", "function": {"name": "web_search", "arguments": '{"query":"甲'}}]})
            self._send_event({"tool_calls": [{"index": 0, "id": "search-b", "function": {"name": "web_search", "arguments": '{"query":"乙","freshness":"oneDay"}'}}]})
            self._send_event({"tool_calls": [{"index": 0, "id": "search-a", "function": {"arguments": '新闻","freshness":"oneDay"}'}}]})
            self._send_event({}, "tool_calls")
        elif query == "on-force" and not tool_messages:
            self._send_event({"content": "我来查询。"})
            self._send_event({}, "stop")
        elif query == "on-malformed" and not tool_messages:
            self._tool("bad-search", "web_search", "{")
        elif query == "on-twelve-calls" and not tool_messages:
            for index in range(12):
                self._send_event({"tool_calls": [{"index": index, "id": f"load-{index}", "function": {
                    "name": "load_skill", "arguments": '{"name":"news-search"}'}}]})
            self._send_event({}, "tool_calls")
        elif query == "off-fake" and not tool_messages:
            self._tool("fake-search", "web_search", '{"query":"隐式搜索"}')
        elif query == "budget":
            self._tool(f"load-{len(tool_messages)}", "load_skill", '{"name":"news-search"}')
        elif query == "empty-final" and not tool_messages:
            self._send_event({"content": "我先查询。"})
            self._tool("load-empty", "load_skill", '{"name":"news-search"}')
        elif query == "empty-final":
            self._send_event({}, "stop")
        elif query == "length":
            self._send_event({"content": "部分文本"})
            self._send_event({}, "length")
        elif query == "oversized-args":
            self._tool("oversized", "load_skill", "x" * 10_001)
        else:
            self._send_event({"content": "已根据工具结果回答。"})
            self._send_event({}, "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class ToolRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ToolProvider)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ficagent-tool-test-")
        skill_dir = Path(self.temp.name) / "skills" / "news-search"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: news-search\ndescription: 查询近期新闻。\n---\n先联网搜索，再引用真实来源。\n", encoding="utf-8")
        self.config = {
            "DATA_DIR": str(Path(self.temp.name) / "data"),
            "SKILLS_DIR": str(skill_dir.parent),
            "TESTING": True,
            "GITEE_AI_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1",
            "GITEE_AI_API_KEY": "test-only-key",
            "GITEE_AI_MODEL": "mock",
            "GITEE_AI_CONNECT_TIMEOUT_SECONDS": 1,
            "GITEE_AI_READ_TIMEOUT_SECONDS": 2,
        }
        with ToolProvider.lock:
            ToolProvider.requests.clear()

    def tearDown(self):
        self.temp.cleanup()

    def collect(self, runtime, query, *, mode="auto", task="task"):
        return list(runtime.stream("user", task, [], query, "原系统提示", "mock", threading.Event(), lambda _response: None, search_mode=mode))

    def requests(self, suffix):
        with ToolProvider.lock:
            return [body for path, body in ToolProvider.requests if path.endswith(suffix)]

    def test_auto_load_then_two_searches_then_answer_and_reset_next_turn(self):
        runtime = GraphRuntime(self.config)
        try:
            events = self.collect(runtime, "auto-flow")
            self.assertEqual([item["name"] for item in events if item["type"] == "skill_loaded"], ["news-search"])
            self.assertEqual(len(self.requests("/web-search")), 2)
            self.assertEqual([item["query"] for item in self.requests("/web-search")], ["甲新闻", "乙"])
            message = events[-1]["message"]
            self.assertEqual(message["skillsUsed"], ["news-search"])
            self.assertEqual(message["toolsUsed"], ["web_search"])
            self.assertEqual(len(message["sources"]), 2)
            self.assertEqual([item["id"] for item in message["sources"]], ["S1", "S2"])
            self.assertEqual(message["content"], "已根据工具结果回答。")
            final_request = self.requests("/chat/completions")[-1]
            self.assertEqual([item["tool_call_id"] for item in final_request["messages"] if item["role"] == "tool"][-2:], ["search-a", "search-b"])
            self.assertIn("先联网搜索", final_request["messages"][0]["content"])
            self.assertEqual(len([item for item in final_request["messages"] if item["role"] == "system"]), 1)
            next_turn = self.collect(runtime, "normal", mode="off")
            self.assertEqual(next_turn[-1]["message"]["sources"], [])
            self.assertEqual(next_turn[-1]["message"]["skillsUsed"], [])
            self.assertEqual(next_turn[-1]["message"]["toolsUsed"], [])
        finally:
            runtime.close()

    def test_on_forces_search_even_if_model_ignores_tool_choice(self):
        runtime = GraphRuntime(self.config)
        try:
            events = self.collect(runtime, "on-force", mode="on")
            malformed = self.collect(runtime, "on-malformed", mode="on", task="malformed")
            self.assertEqual(len(self.requests("/web-search")), 2)
            first = self.requests("/chat/completions")[0]
            self.assertEqual(first["tool_choice"], "auto")
            self.assertEqual(events[-1]["message"]["skillsUsed"], [])
            self.assertEqual(len(events[-1]["message"]["sources"]), 1)
            self.assertEqual([item["text"] for item in events if item["type"] == "token"], ["已根据工具结果回答。"])
            self.assertEqual(events[-1]["message"]["content"], "已根据工具结果回答。")
            self.assertEqual(len(malformed[-1]["message"]["sources"]), 1)
            self.assertEqual(self.requests("/web-search")[-1]["query"], "on-malformed")
        finally:
            runtime.close()

    def test_off_rejects_unadvertised_search_call_without_http_request(self):
        runtime = GraphRuntime(self.config)
        try:
            events = self.collect(runtime, "off-fake", mode="off")
            self.assertEqual(len(self.requests("/web-search")), 0)
            self.assertEqual(events[-1]["message"]["sources"], [])
            first = self.requests("/chat/completions")[0]
            self.assertNotIn("web_search", [item["function"]["name"] for item in first.get("tools", [])])
            self.assertIn("关闭联网搜索", first["messages"][0]["content"])
            self.assertIn("本轮不可用", self.requests("/chat/completions")[-1]["messages"][-1]["content"])
        finally:
            runtime.close()

    def test_search_runs_directly_with_no_skill_directory_in_auto_and_on(self):
        self.config["SKILLS_DIR"] = str(Path(self.temp.name) / "no-skills")
        runtime = GraphRuntime(self.config)
        try:
            for mode, query in [("auto", "direct-search"), ("on", "on-force")]:
                events = self.collect(runtime, query, mode=mode, task=mode)
                self.assertEqual(events[-1]["message"]["skillsUsed"], [])
                self.assertEqual(len(events[-1]["message"]["sources"]), 1)
                self.assertNotIn("skill_loaded", [item["type"] for item in events])
            self.assertEqual(len(self.requests("/web-search")), 2)
            for request in self.requests("/chat/completions"):
                offered = [item["function"]["name"] for item in request.get("tools", [])]
                self.assertIn("web_search", offered)
                self.assertNotIn("load_skill", offered)
        finally:
            runtime.close()

    def test_renaming_a_skill_does_not_affect_the_search_tool(self):
        original = Path(self.config["SKILLS_DIR"]) / "news-search"
        renamed = original.with_name("custom-news")
        original.rename(renamed)
        path = renamed / "SKILL.md"
        path.write_text(path.read_text(encoding="utf-8").replace("name: news-search", "name: custom-news"), encoding="utf-8")
        runtime = GraphRuntime(self.config)
        try:
            events = self.collect(runtime, "renamed-skill")
            self.assertEqual(events[-1]["message"]["skillsUsed"], ["custom-news"])
            self.assertEqual(len(events[-1]["message"]["sources"]), 1)
            self.assertEqual(len(self.requests("/web-search")), 1)
        finally:
            runtime.close()

    def test_new_tool_and_alternative_search_run_without_engine_changes(self):
        executed = []
        def local(arguments, context):
            executed.append(("local", arguments))
            return ToolResult({"text": arguments["text"]})
        def alternate(arguments, context):
            executed.append(("alternate", arguments))
            return ToolResult({"query": arguments["query"]}, sources=[{"url": "https://example.org/alternate", "title": "替代来源"}])
        registry = ToolRegistry([
            ToolSpec("local_echo", "本地工具", {"type": "object"}, local),
            ToolSpec("alternate_lookup", "替代搜索", {"type": "object"}, alternate,
                     capabilities=frozenset({"search"}), fallback_arguments=lambda query: {"query": query}),
        ])
        self.config["SKILLS_DIR"] = str(Path(self.temp.name) / "no-skills")
        runtime = GraphRuntime(self.config, tool_registry=registry)
        try:
            result = self.collect(runtime, "custom-local", mode="off")
            self.assertEqual(result[-1]["message"]["sources"], [])
            self.assertEqual(result[-1]["message"]["toolsUsed"], ["local_echo"])
            result = self.collect(runtime, "custom-search", mode="on", task="custom-search")
            self.assertEqual(result[-1]["message"]["sources"][0]["id"], "S1")
            self.assertEqual(result[-1]["message"]["skillsUsed"], [])
            self.assertEqual(result[-1]["message"]["toolsUsed"], ["alternate_lookup"])
            self.assertEqual([name for name, _ in executed], ["local", "alternate"])
            self.assertEqual(self.requests("/web-search"), [])
            self.assertEqual([item["function"]["name"] for item in self.requests("/chat/completions")[0]["tools"]], ["local_echo"])
        finally:
            runtime.close()

    def test_tool_budget_and_length_reason_are_errors(self):
        runtime = GraphRuntime(self.config)
        try:
            for query, expected in [("budget", "上限"), ("length", "令牌上限"),
                                    ("empty-final", "没有返回文本"), ("oversized-args", "参数过长")]:
                before = len(self.requests("/chat/completions"))
                with self.assertRaises(GatewayError) as caught:
                    self.collect(runtime, query, task=query)
                self.assertIn(expected, str(caught.exception))
                if query == "budget":
                    self.assertLessEqual(len(self.requests("/chat/completions")) - before, GraphRuntime.MAX_MODEL_ROUNDS)
        finally:
            runtime.close()

    def test_forced_search_cannot_exceed_twelve_tool_calls(self):
        runtime = GraphRuntime(self.config)
        try:
            with self.assertRaises(GatewayError) as caught:
                self.collect(runtime, "on-twelve-calls", mode="on")
            self.assertIn("过多工具调用", str(caught.exception))
            self.assertEqual(len(self.requests("/chat/completions")), 1)
            self.assertEqual(self.requests("/web-search"), [])
        finally:
            runtime.close()


if __name__ == "__main__":
    unittest.main()
