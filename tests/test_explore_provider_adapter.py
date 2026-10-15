"""Explore keeps its failure semantics over the platform's model transport."""

import threading
import unittest
from unittest.mock import patch

from backend.app.modules.explore.provider_adapter import ExploreGiteeProvider
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider


class ExploreProviderAdapterTests(unittest.TestCase):
    def setUp(self):
        self.provider = ExploreGiteeProvider({"GITEE_AI_API_KEY": "test-key",
                                               "GITEE_AI_MODEL": "GLM-5.3-Flash"})
        self.messages = [{"role": "user", "content": "test input"}]
        self.stop = threading.Event()

    def test_trace_and_diagnostics_preserve_platform_transport(self):
        calls, diagnostics = [], []
        self.provider.on_diagnostic = diagnostics.append

        def platform_events(_provider, messages, model, stopped, on_response, **kwargs):
            calls.append((messages, model, stopped, kwargs))
            kwargs["on_trace"]("transport", {"httpStatus": 200, "responseId": "r1"})
            yield {"type": "token", "text": "ok"}

        with patch.object(GiteeProvider, "events", platform_events):
            result = list(self.provider.events(self.messages, "GLM-5.3-Flash",
                                               self.stop, lambda _response: None))
        self.assertEqual(result, [{"type": "token", "text": "ok"}])
        self.assertIs(calls[0][0], self.messages)
        self.assertIs(calls[0][2], self.stop)
        self.assertEqual(calls[0][3]["on_trace"].__name__, "trace")
        self.assertEqual(diagnostics[-1]["status"], "completed")
        self.assertTrue(any(item.get("httpStatus") == 200 for item in diagnostics))
        self.assertTrue(all("test input" not in str(item) for item in diagnostics))

    def test_generic_platform_timeout_keeps_explore_retry_code(self):
        def timeout(*_args, **_kwargs):
            raise GatewayError("模型连接或响应超时，请稍后重试。", 504)
            yield

        with patch.object(GiteeProvider, "events", timeout):
            with self.assertRaises(GatewayError) as raised:
                list(self.provider.events(self.messages, "GLM-5.3-Flash",
                                          self.stop, lambda _response: None))
        self.assertEqual(raised.exception.code, "model_request_timeout")
        self.assertEqual(raised.exception.status, 504)

    def test_cancel_status_is_not_reclassified_as_timeout(self):
        def cancelled(*_args, **_kwargs):
            raise GatewayError("已停止生成。", 409)
            yield

        with patch.object(GiteeProvider, "events", cancelled):
            with self.assertRaises(GatewayError) as raised:
                list(self.provider.events(self.messages, "GLM-5.3-Flash",
                                          self.stop, lambda _response: None))
        self.assertEqual(raised.exception.status, 409)
        self.assertIsNone(raised.exception.code)


if __name__ == "__main__":
    unittest.main()
