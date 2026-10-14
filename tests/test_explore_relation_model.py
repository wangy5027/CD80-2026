"""Relation model routing uses local HTTP fixtures and never contacts Gitee."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider


class LocalStreamResponse:
    status_code = 200
    headers = {"Content-Type": "text/event-stream"}

    def __init__(self):
        self.closed = False

    def iter_lines(self, chunk_size=1):
        assert chunk_size == 1
        chunk = {"choices": [{"delta": {"content": '{"ok":true}'},
                              "finish_reason": "stop"}]}
        yield ("data: " + json.dumps(chunk)).encode("utf-8")
        yield b""

    def close(self):
        self.closed = True


def local_provider(*, connect=9.0, read=120.0):
    return GiteeProvider({"TESTING": True, "GITEE_AI_API_KEY": "test-only",
                          "GITEE_AI_MODEL": "GLM-5.3",
                          "GITEE_AI_CONNECT_TIMEOUT_SECONDS": connect,
                          "GITEE_AI_READ_TIMEOUT_SECONDS": read,
                          "GITEE_AI_MAX_TOKENS": 8192,
                          "GITEE_AI_REASONING_EFFORT": "low"})


def bare_service(relation_model="GLM-5.3-Flash"):
    service = ExploreService.__new__(ExploreService)
    service.provider = local_provider()
    service.repository = Mock()
    service.relation_model = relation_model
    return service


def control(*, deadline=None):
    return {"stop": threading.Event(), "timed_out": threading.Event(),
            "deadline": deadline, "evidence_guard": Mock()}


class RelationModelRoutingTests(unittest.TestCase):
    def test_config_default_blank_fallback_and_explicit_full_model(self):
        workbench = Mock()
        for setting, expected in ((None, "GLM-5.3-Flash"),
                                  ("   ", "GLM-5.3-Flash"),
                                  ("GLM-5.3", "GLM-5.3")):
            with self.subTest(configured=setting), tempfile.TemporaryDirectory() as folder:
                config = {"TESTING": True, "DATA_DIR": folder,
                          "GITEE_AI_API_KEY": "test-only"}
                if setting is not None:
                    config["EXPLORE_RELATION_MODEL"] = setting
                service = ExploreService(workbench, Mock(), config)
                try:
                    self.assertEqual(service.relation_model, expected)
                    self.assertEqual(service.provider.model, "GLM-5.3")
                    with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                               return_value=LocalStreamResponse()) as post:
                        self.assertEqual(service._invoke_model_once(
                            {"task": "relation_entity_selection", "stage": "relation_select"},
                            "fixture", control()), {"ok": True})
                    self.assertEqual(post.call_args.kwargs["json"]["model"], expected)
                    self.assertEqual(service.provider.model, "GLM-5.3")
                finally:
                    service.close()

    def test_only_relation_stages_send_flash_and_diagnostics_record_it(self):
        service = bare_service()
        requests_sent = []
        responses = []

        def fake_post(_url, **kwargs):
            requests_sent.append(kwargs)
            response = LocalStreamResponse()
            responses.append(response)
            return response

        stages = ("relation_select", "relation_lite_report",
                  "event_deep_lite", "history_report")
        with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                   side_effect=fake_post) as post:
            for stage in stages:
                with self.subTest(stage=stage):
                    state = control()
                    result = service._invoke_model_once(
                        {"task": "relation_entity_selection" if stage == "relation_select"
                         else "research_report", "stage": stage}, "fixture", state)
                    self.assertEqual(result, {"ok": True})
                    trace = state["model_transport_trace"]
                    self.assertEqual(len(trace), 1)
                    self.assertEqual(trace[0]["stage"], stage)
                    self.assertEqual(trace[0]["status"], "completed")
                    expected = ("GLM-5.3-Flash" if stage in stages[:2]
                                else "GLM-5.3")
                    self.assertEqual(trace[0]["model"], expected)
                    self.assertEqual(requests_sent[-1]["json"]["model"], expected)
            self.assertEqual(post.call_count, 4)

        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(service.provider.model, "GLM-5.3")
        self.assertIsNone(service.provider.on_diagnostic)
        self.assertEqual([sent["timeout"] for sent in requests_sent],
                         [(3.0, 20.0), (3.0, 45.0), (9.0, 120.0), (9.0, 120.0)])
        self.assertTrue(all(sent["json"]["response_format"] == {"type": "json_object"}
                            for sent in requests_sent))

    def test_explicit_full_model_override_still_uses_relation_stage_limits(self):
        service = bare_service(relation_model="GLM-5.3")
        sent = []

        def fake_post(_url, **kwargs):
            sent.append(kwargs)
            return LocalStreamResponse()

        with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                   side_effect=fake_post):
            for stage in ("relation_select", "relation_lite_report"):
                state = control()
                self.assertEqual(service._invoke_model_once(
                    {"task": "report", "stage": stage}, "fixture", state), {"ok": True})
                self.assertEqual(state["model_transport_trace"][0]["model"], "GLM-5.3")

        self.assertEqual([row["json"]["model"] for row in sent],
                         ["GLM-5.3", "GLM-5.3"])
        self.assertEqual([row["timeout"] for row in sent],
                         [(3.0, 20.0), (3.0, 45.0)])
        self.assertEqual(service.provider.model, "GLM-5.3")

    def test_relation_header_timeout_keeps_flash_diagnostic_and_never_retries(self):
        for stage, read_cap in (("relation_select", 20.0),
                                ("relation_lite_report", 45.0)):
            with self.subTest(stage=stage):
                service = bare_service()
                state = control(deadline=time.monotonic() + 120)
                with patch("backend.app.modules.workbench.graph_runtime.requests.post",
                           side_effect=requests.ReadTimeout("local fixture")) as post:
                    with self.assertRaises(GatewayError) as raised:
                        service._invoke_model({"task": "relation_entity_selection"
                                               if stage == "relation_select" else "research_report",
                                               "stage": stage}, "fixture", state)

                self.assertEqual(raised.exception.code, "model_header_timeout")
                self.assertEqual(post.call_count, 1)
                self.assertEqual(state.get("model_retries") or [], [])
                trace = state["model_transport_trace"]
                self.assertEqual(len(trace), 1)
                self.assertEqual(trace[0]["model"], "GLM-5.3-Flash")
                self.assertEqual(trace[0]["error_code"], "model_header_timeout")
                self.assertLessEqual(trace[0]["timeout"]["connect_seconds"], 3.0)
                self.assertLessEqual(trace[0]["timeout"]["read_seconds"], read_cap)
                self.assertEqual(service.provider.model, "GLM-5.3")


if __name__ == "__main__":
    unittest.main()
