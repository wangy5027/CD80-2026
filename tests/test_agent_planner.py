"""Planner protocol tests; only in-memory fixtures and a loopback model server."""

import copy
import json
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.app.modules.agent_platform.planner import Planner, _json_object
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider


def sample_spec():
    return {
        "schemaVersion": "1", "name": "文本长度", "description": "计算用户文本长度",
        "goal": "计算文本长度并解释", "prompt": "说明实际字符数，使用指定解释规范。",
        "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                        "required": ["text"], "additionalProperties": False},
        "outputSchema": {"type": "object", "properties": {"explanation": {"type": "string"}}},
        "tools": [{"id": "length", "version": "1", "code": "def run(payload):\n    return {'length': len(payload['text'])}\n",
                   "entrypoint": "run", "inputSchema": {"type": "object"},
                   "outputSchema": {"type": "object"}, "permissions": {"files": [], "network": False}}],
        "skills": [{"id": "explain-length", "version": "1", "content": "必须区分字符数和字节数。",
                    "requiredTools": ["length"]}],
        "pipeline": [{"id": "count", "kind": "tool", "tool": "length", "inputFrom": "run.input"},
                     {"id": "explain", "kind": "model", "purpose": "解释实际长度", "inputFrom": ["steps.count.output"]}],
        "permissions": {"network": False},
    }


class TextProvider:
    model = "fixture-model"

    def __init__(self, answer, *, stop_after_first=False):
        self.answer = answer
        self.calls = []
        self.closed = False
        self.stop_after_first = stop_after_first

    def tokens(self, messages, model, stopped, on_response):
        self.calls.append(copy.deepcopy(messages))
        try:
            if self.stop_after_first:
                yield self.answer[:1]
                stopped.set()
                yield self.answer[1:]
            else:
                yield self.answer
        finally:
            self.closed = True


class AgentPlannerTests(unittest.TestCase):
    def setUp(self):
        self.stopped = threading.Event()
        self.callback = lambda _response: None

    def classify(self, answer, candidates=None, files=None, history=None):
        provider = TextProvider(json.dumps(answer) if isinstance(answer, dict) else answer)
        result = Planner(provider).classify("请处理", files or [], candidates or [], history or [], self.stopped, self.callback)
        return result, provider

    def test_chat_and_clarification_are_model_decisions(self):
        for decision in ({"intent": "chat"}, {"intent": "clarify", "question": "需要哪个字段？"}):
            result, provider = self.classify(decision)
            self.assertEqual(result["intent"], decision["intent"])
            self.assertEqual(len(provider.calls), 1)

    def test_optional_empty_fields_are_absent_but_branch_requirements_stay_strict(self):
        created, _ = self.classify({"intent": "create", "agentRef": None, "question": None,
                                    "goal": "统计字符", "input": {"text": "hello"}})
        self.assertEqual(created, {"intent": "create", "goal": "统计字符", "input": {"text": "hello"}})
        chat, _ = self.classify({"intent": "chat", "agentRef": None, "question": None, "goal": None})
        self.assertEqual(chat, {"intent": "chat", "input": {}})
        created, _ = self.classify({"intent": "create", "agentRef": "", "question": "  ", "goal": "统计字符"})
        self.assertEqual(created, {"intent": "create", "goal": "统计字符", "input": {}})
        for intent in (None, [], {}, 42):
            with self.subTest(intent=intent), self.assertRaises(GatewayError):
                self.classify({"intent": intent})
        for intent, required in (("invoke", "agentRef"), ("clarify", "question"), ("create", "goal")):
            for value in (None, "", "   ", 42, [], {}):
                with self.subTest(intent=intent, value=value), self.assertRaises(GatewayError):
                    self.classify({"intent": intent, required: value})

    def test_candidate_reference_and_schema_are_enforced(self):
        spec = {**sample_spec(), "id": "known", "version": "1"}
        result, _ = self.classify({"intent": "invoke", "agentRef": "known@1", "input": {"text": "hello"}}, [spec])
        self.assertEqual(result["input"], {"text": "hello"})
        result, provider = self.classify({"intent": "invoke", "agentRef": "known@1", "input": {}}, [spec])
        self.assertEqual(result["intent"], "clarify")
        self.assertIn("text", result["question"])
        self.assertEqual(len(provider.calls), 1)
        with self.assertRaises(GatewayError):
            self.classify({"intent": "invoke", "agentRef": "other@1"}, [spec])
        with self.assertRaises(GatewayError):
            self.classify({"intent": "invoke", "agentRef": "known@1", "input": {"text": 7}}, [spec])

    def test_file_bytes_paths_and_legacy_attachment_text_never_reach_model(self):
        result, provider = self.classify({"intent": "create", "goal": "处理文件"},
            files=[{"id": "f1", "name": "input.csv", "size": 20, "content": "ROW_SECRET", "path": "HOST_SECRET"}],
            history=[{"role": "user", "content": '问题<用户提供的参考文件 name="a">PRIVATE_ROWS</用户提供的参考文件>'}])
        payload = json.loads(provider.calls[0][1]["content"])
        self.assertEqual(payload["files"], [{"id": "f1", "name": "input.csv", "size": 20}])
        text = str(provider.calls)
        for secret in ("ROW_SECRET", "HOST_SECRET", "PRIVATE_ROWS"):
            self.assertNotIn(secret, text)
        self.assertEqual(result["intent"], "create")

    def test_invalid_json_duplicate_keys_constants_and_unknown_fields_fail_once(self):
        for answer in ('说明\n```json\n{"intent":"chat"}\n```', '{"intent":"chat","intent":"create"}',
                       '{"intent":"chat","input":{"number":NaN}}', '[]',
                       '{"intent":"chat","approve":true}', '{"intent":"create"}',
                       '{"intent":"clarify"}', '{"intent":"invoke","agentRef":"missing"}'):
            with self.subTest(answer=answer), self.assertRaises(GatewayError):
                self.classify(answer)

    def test_single_json_fence_accepted_but_mixed_or_multiple_blocks_rejected(self):
        result, _ = self.classify('```json\n{"intent":"chat"}\n```')
        self.assertEqual(result["intent"], "chat")
        for text in ('```json\n{"intent":"chat"}\n```\n后记',
                     '```json\n{"intent":"chat"}\n```\n```json\n{}\n```',
                     '```python\n{"intent":"chat"}\n```'):
            with self.assertRaises(GatewayError):
                self.classify(text)

    def test_raw_code_whitespace_is_transport_escaped_without_source_changes(self):
        source = 'def run(payload):\r\n\treturn {"text": "a\\nb"}\n'
        encoded = json.dumps({"code": source})
        raw = encoded.replace('\\r', '\r').replace('\\n', '\n', 1).replace('\\t', '\t')
        self.assertEqual(_json_object(raw)["code"], source)
        self.assertEqual(_json_object(json.dumps({"code": source}))["code"], source)
        self.assertEqual(_json_object('{"goal":"first\nsecond\tthird\r"}')["goal"], 'first\nsecond\tthird\r')
        for raw_bad in ('{"code":"bad\x00value"}', '{"code":"bad\x08value"}',
                        '{"code":"slash\\\nvalue"}', '{"code":"line\n","code":"duplicate"}',
                        '{"code":"line\n"} trailing', '{"value":NaN}'):
            with self.assertRaises(GatewayError):
                _json_object(raw_bad)

    def test_revision_passes_validated_baseline_only_and_never_executes(self):
        spec = sample_spec()
        provider = TextProvider(json.dumps(spec))
        Planner(provider).generate("改为说明UTF8字节数", {
            "baselineSpec": spec, "unknown": "PRIVATE_EXTRA", "input": {"text": "你好"}
        }, self.stopped, self.callback)
        payload = json.loads(provider.calls[0][1]["content"])
        self.assertEqual(payload["baselineSpec"]["tools"][0]["code"], spec["tools"][0]["code"])
        self.assertIn("待修改", payload["revisionInstruction"])
        self.assertNotIn("PRIVATE_EXTRA", str(provider.calls))
        invalid = {**spec, "unexpected": True}
        with self.assertRaises(GatewayError):
            Planner(provider).generate("修订", {"baselineSpec": invalid}, self.stopped, self.callback)
        self.assertEqual(len(provider.calls), 1)

    def test_generation_prompt_matches_executor_and_result_contract(self):
        provider = TextProvider(json.dumps(sample_spec()))
        Planner(provider).generate("目标", {}, self.stopped, self.callback)
        prompt = provider.calls[0][0]["content"]
        for required in ("WASI", "128MiB", "15秒", "16MiB", "禁止 mkdir", "artifactIds", "stepOutputs", "schemaVersion:'1'"):
            self.assertIn(required, prompt)

    def test_deadline_aborts_blocked_response_without_cancelling_user(self):
        released = threading.Event()

        class Response:
            def abort(self):
                released.set()

        class StalledProvider(TextProvider):
            def tokens(self, messages, model, stopped, on_response):
                self.calls.append(messages)
                try:
                    on_response(Response())
                    if not released.wait(2):
                        raise AssertionError("Deadline failed to abort the response")
                    raise GatewayError("模型连接提前结束", 502)
                    yield ""
                finally:
                    self.closed = True
                    on_response(None)

        provider = StalledProvider("")
        started = time.monotonic()
        with self.assertRaises(GatewayError) as error:
            Planner(provider, deadline_seconds=0.03).generate("目标", {}, self.stopped, self.callback)
        self.assertEqual(error.exception.code, "agent_model_deadline")
        self.assertEqual(error.exception.status, 504)
        self.assertFalse(self.stopped.is_set())
        self.assertTrue(provider.closed)
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(len(provider.calls), 1)

    def test_generate_returns_real_model_tool_code_without_running_it(self):
        spec = sample_spec()
        spec["tools"][0]["code"] = "raise RuntimeError('must never import generated source')\ndef run(payload):\n    return {}\n"
        provider = TextProvider(json.dumps(spec))
        generated = Planner(provider).generate("文本处理", {"input": {"text": "hello"}, "files": []}, self.stopped, self.callback)
        self.assertEqual(generated["tools"][0]["code"], spec["tools"][0]["code"])
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("payload.filePath", provider.calls[0][0]["content"])

    def test_generate_unknown_schema_keys_and_recursive_steps_rejected(self):
        cases = []
        first = sample_spec()
        first["surprise"] = "not allowed"
        cases.append(first)
        second = sample_spec()
        second["pipeline"][0]["kind"] = "agent"
        cases.append(second)
        for spec in cases:
            provider = TextProvider(json.dumps(spec))
            with self.subTest(spec=spec), self.assertRaises(GatewayError):
                Planner(provider).generate("目标", {}, self.stopped, self.callback)
            self.assertEqual(len(provider.calls), 1)

    def test_explanation_uses_real_prompt_skills_and_only_fresh_context(self):
        provider = TextProvider("共 5 个字符。")
        spec = sample_spec()
        result = Planner(provider).explain(spec,
            {"input": {"text": "hello"}, "history": "MAIN_HISTORY_SECRET"},
            {"count": {"length": 5}}, self.stopped, self.callback)
        messages = provider.calls[0]
        self.assertEqual(result, "共 5 个字符。")
        self.assertIn(spec["prompt"], messages[0]["content"])
        self.assertIn(spec["skills"][0]["content"], messages[0]["content"])
        self.assertNotIn("MAIN_HISTORY_SECRET", str(messages))
        self.assertEqual(json.loads(messages[1]["content"])["stepOutputs"], {"count": {"length": 5}})

    def test_output_limit_closes_stream_and_does_not_retry(self):
        provider = TextProvider("x" * 101)
        with self.assertRaises(GatewayError):
            Planner(provider, max_output_chars=100).generate("目标", {}, self.stopped, self.callback)
        self.assertTrue(provider.closed)
        self.assertEqual(len(provider.calls), 1)

    def test_pre_cancel_avoids_model_and_mid_stream_cancel_closes(self):
        provider = TextProvider("answer")
        self.stopped.set()
        with self.assertRaises(GatewayError):
            Planner(provider).generate("目标", {}, self.stopped, self.callback)
        self.assertEqual(provider.calls, [])
        self.stopped.clear()
        provider = TextProvider("answer", stop_after_first=True)
        with self.assertRaises(GatewayError) as caught:
            Planner(provider).generate("目标", {}, self.stopped, self.callback)
        self.assertEqual(caught.exception.status, 409)
        self.assertTrue(provider.closed)

    def test_provider_error_is_propagated_without_repair_call(self):
        class FailedProvider(TextProvider):
            def tokens(self, *args):
                raise GatewayError("模型连接超时。", 504)
        with self.assertRaises(GatewayError) as caught:
            Planner(FailedProvider("")).generate("目标", {}, self.stopped, self.callback)
        self.assertEqual(caught.exception.status, 504)

    def test_real_gitee_transport_against_local_http_fixture(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                answer = json.dumps({"intent": "create", "goal": "统计文本长度", "input": {"text": "hello"}})
                self.wfile.write(("data: " + json.dumps({"choices": [{"delta": {"content": answer}}]}) + "\n\ndata: [DONE]\n\n").encode())

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = GiteeProvider({"TESTING": True, "GITEE_AI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
                                      "GITEE_AI_API_KEY": "fixture-only", "GITEE_AI_MODEL": "test-model"})
            with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}):
                result = Planner(provider).classify("统计 hello 长度", [], [], [], self.stopped, self.callback)
            self.assertEqual(result["input"]["text"], "hello")
            self.assertEqual(requests[0]["model"], "test-model")
            self.assertTrue(requests[0]["stream"])
            self.assertEqual(requests[0]["response_format"], {"type": "json_object"})
            with patch.dict("os.environ", {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}):
                Planner(provider).explain(sample_spec(), {"input": {}, "files": []}, {}, self.stopped, self.callback)
            self.assertNotIn("response_format", requests[1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
