"""The model transport emits only bounded, phase-specific diagnostics."""

import json
import threading
import unittest
from unittest.mock import patch

import requests
from urllib3.exceptions import ReadTimeoutError

from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider


class FakeResponse:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}
    raw = None

    def __init__(self, lines=None, error=None):
        self.lines = lines or []
        self.error = error
        self.closed = False

    def iter_lines(self, chunk_size=1):
        assert chunk_size == 1
        yield from self.lines
        if self.error is not None:
            raise self.error

    def close(self):
        self.closed = True


def finished_response():
    return FakeResponse([
        b'data: {"choices":[{"delta":{"content":"ok"}}]}',
        b'',
        b'data: [DONE]',
        b'',
    ])


class ProviderDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.provider = GiteeProvider({
            "GITEE_AI_API_KEY": "private-test-key",
            "GITEE_AI_MODEL": "GLM-5.3",
            "GITEE_AI_CONNECT_TIMEOUT_SECONDS": 2,
            "GITEE_AI_READ_TIMEOUT_SECONDS": 11,
            "GITEE_AI_MAX_TOKENS": 99,
            "GITEE_AI_REASONING_EFFORT": "low",
        })
        self.events = []
        self.provider.on_diagnostic = self.events.append
        self.stopped = threading.Event()
        self.responses = []

    def collect(self, *, messages=None, tools=None):
        return list(self.provider.events(
            messages or [{"role": "user", "content": "secret-prompt"}],
            "GLM-5.3", self.stopped, self.responses.append, tools=tools,
        ))

    def assert_error(self, code, phase, exception_type):
        record = self.events[-1]
        self.assertEqual(record["status"], "error")
        self.assertEqual(record["error_code"], code)
        self.assertEqual(record["phase"], phase)
        self.assertEqual(record["exception_type"], exception_type)

    def test_success_metadata_is_allowlisted_and_copied(self):
        response = finished_response()
        tool = {"type": "function", "function": {"name": "read_evidence", "description": "secret-schema"}}
        original = self.provider.on_diagnostic

        def observer(metadata):
            original(metadata)
            metadata["tool_names"].append("injected")
            metadata["timeout"]["read_seconds"] = 999

        self.provider.on_diagnostic = observer
        with patch("backend.app.modules.workbench.graph_runtime.requests.post", return_value=response) as post:
            events = self.collect(tools=[tool])
        self.assertEqual([item["type"] for item in events], ["token", "turn_end"])
        self.assertEqual([item["status"] for item in self.events], [
            "request_waiting", "headers_received", "first_sse", "completed",
        ])
        self.assertEqual(self.events[0]["request_bytes"], len(json.dumps(post.call_args.kwargs["json"]).encode()))
        self.assertIsNone(self.events[0]["headers_seconds"])
        self.assertIsNotNone(self.events[-1]["first_event_seconds"])
        self.assertTrue(response.closed)
        self.assertEqual(self.responses[-1], None)
        self.assertEqual(post.call_args.kwargs["timeout"], (2, 11))
        self.assertTrue(post.call_args.kwargs["stream"])
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        self.assertEqual(post.call_args.kwargs["json"]["tools"], [tool])
        for record in self.events:
            self.assertEqual(record["tool_names"][0], "read_evidence")
            self.assertEqual(len(record["tool_names"]), 2)
            self.assertEqual(record["timeout"]["connect_seconds"], 2)
            self.assertEqual(set(record), {
                "status", "phase", "model", "request_bytes", "message_count", "tool_names",
                "max_tokens", "reasoning_effort", "timeout", "headers_seconds",
                "first_event_seconds", "elapsed_seconds", "exception_type", "error_code",
            })
        rendered = json.dumps(self.events, ensure_ascii=False)
        for secret in ("secret-prompt", "private-test-key", "secret-schema", "ai.gitee.com", "Bearer"):
            self.assertNotIn(secret, rendered)

    def test_observer_exception_does_not_change_model_result(self):
        self.provider.on_diagnostic = lambda _metadata: (_ for _ in ()).throw(RuntimeError("observer broke"))
        with patch("backend.app.modules.workbench.graph_runtime.requests.post", return_value=finished_response()):
            self.assertEqual([item["text"] for item in self.collect() if item["type"] == "token"], ["ok"])

    def test_connect_timeout_and_header_timeout_have_distinct_codes(self):
        for raised, code, phase, exception_type in (
            (requests.ConnectTimeout("private upstream"), "model_connect_timeout", "connect", "ConnectTimeout"),
            (requests.ReadTimeout("private upstream"), "model_header_timeout", "headers", "ReadTimeout"),
            (requests.ConnectionError(ReadTimeoutError(None, None, "private upstream")), "model_header_timeout", "headers", "ConnectionError"),
        ):
            with self.subTest(code=code):
                self.events.clear()
                with patch("backend.app.modules.workbench.graph_runtime.requests.post", side_effect=raised):
                    with self.assertRaises(GatewayError) as caught:
                        self.collect()
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn("private upstream", str(caught.exception))
                self.assertEqual([item["status"] for item in self.events], ["request_waiting", "error"])
                self.assert_error(code, phase, exception_type)
                self.assertIsNone(self.events[-1]["headers_seconds"])

    def test_stream_read_timeout_records_header_and_first_sse(self):
        response = FakeResponse(
            [b'data: {"choices":[{"delta":{"content":"partial"}}]}', b''],
            requests.ConnectionError(ReadTimeoutError(None, None, "secret upstream stream message")),
        )
        with patch("backend.app.modules.workbench.graph_runtime.requests.post", return_value=response):
            with self.assertRaises(GatewayError) as caught:
                self.collect()
        self.assertEqual(caught.exception.code, "model_stream_timeout")
        self.assertNotIn("secret upstream", str(caught.exception))
        self.assertEqual([item["status"] for item in self.events], [
            "request_waiting", "headers_received", "first_sse", "error",
        ])
        self.assert_error("model_stream_timeout", "stream", "ConnectionError")
        self.assertIsNotNone(self.events[-1]["headers_seconds"])
        self.assertIsNotNone(self.events[-1]["first_event_seconds"])
        self.assertTrue(response.closed)

    def test_invalid_parameters_fail_before_network(self):
        with patch("backend.app.modules.workbench.graph_runtime.requests.post") as post:
            with self.assertRaises(GatewayError) as caught:
                self.collect(messages=[{"role": "user", "content": object()}])
        post.assert_not_called()
        self.assertEqual(caught.exception.code, "model_request_invalid")
        self.assert_error("model_request_invalid", "parameters", "GatewayError")

    def test_existing_token_callers_keep_their_signatures(self):
        self.provider.on_diagnostic = None
        with patch("backend.app.modules.workbench.graph_runtime.requests.post", return_value=finished_response()) as post:
            tokens = list(self.provider.tokens([{"role": "user", "content": "x"}], "GLM-5.3", self.stopped, self.responses.append))
        self.assertEqual(tokens, ["ok"])
        self.assertNotIn("response_format", post.call_args.kwargs["json"])
        with patch("backend.app.modules.workbench.graph_runtime.requests.post", return_value=finished_response()) as post:
            tokens = list(self.provider.json_tokens([{"role": "user", "content": "x"}], "GLM-5.3", self.stopped, self.responses.append))
        self.assertEqual(tokens, ["ok"])
        self.assertEqual(post.call_args.kwargs["json"]["response_format"], {"type": "json_object"})


if __name__ == "__main__":
    unittest.main()
