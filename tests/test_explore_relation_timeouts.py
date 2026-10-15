"""Relation-lite deadlines and one-attempt model calls, with local fixtures only."""

from __future__ import annotations

import json
import threading
import time
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from backend.app.modules.explore.service import ExploreService
from backend.app.modules.explore.relation_lite_runtime import RelationLiteRuntime
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider
from tests.test_explore_model_retry import RetryProvider
from tests.test_explore_relation_lite import RelationProvider, RelationReader
import tests.test_explore_relation_lite as relation_fixtures


BASE = "/desktop/api/explore"


def bare_service(provider):
    service = ExploreService.__new__(ExploreService)
    service.provider = provider
    service.repository = Mock()
    return service


def bare_control(deadline=None):
    return {"stop": threading.Event(), "timed_out": threading.Event(),
            "deadline": deadline, "evidence_guard": Mock(), "model": "GLM-5.3-Flash"}


class CaptureGiteeProvider(GiteeProvider):
    """A Gitee instance whose stream never opens a socket."""

    def __init__(self, connect=9.0, read=120.0):
        self.model = "timeout-fixture"
        self.connect_timeout = connect
        self.read_timeout = read
        self.request_options = {}
        self.seen = []

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        self.seen.append((payload["stage"], self.connect_timeout, self.read_timeout))
        yield '{"ok":true}'


class RelationModelTimeoutUnitTests(unittest.TestCase):
    def test_relation_connect_header_and_stream_errors_never_retry(self):
        for stage in ("relation_select", "relation_lite_report"):
            for code in ("model_connect_timeout", "model_header_timeout",
                         "model_stream_timeout"):
                with self.subTest(stage=stage, code=code):
                    first = (["{\"partial\":", GatewayError("fixture timeout", 504, code)]
                             if code == "model_stream_timeout" else
                             [GatewayError("fixture timeout", 504, code)])
                    provider = RetryProvider([first,
                                              ['{"unexpected":true}']])
                    service = bare_service(provider)
                    control = bare_control(time.monotonic() + 120)
                    task = "relation_entity_selection" if stage == "relation_select" else "research_report"
                    with self.assertRaises(GatewayError) as raised:
                        service._invoke_model({"task": task, "stage": stage}, "fixture", control)
                    self.assertEqual(raised.exception.code, code)
                    self.assertEqual(provider.calls, 1)
                    self.assertEqual(provider.closed, 1)
                    self.assertEqual(control.get("model_retries") or [], [])

    def test_non_relation_model_retains_existing_one_transport_retry(self):
        provider = RetryProvider([[GatewayError("timeout", 504, "model_stream_timeout")],
                                  ['{"ok":true}']])
        service = bare_service(provider)
        control = bare_control(time.monotonic() + 120)
        result = service._invoke_model({"task": "research_report", "stage": "event_deep_lite"},
                                       "fixture", control)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(control["model_retries"]), 1)

    def test_relation_stage_caps_copied_gitee_provider_only(self):
        provider = CaptureGiteeProvider()
        service = bare_service(provider)
        for stage in ("relation_select", "relation_lite_report", "event_deep_lite",
                      "history_report"):
            task = "relation_entity_selection" if stage == "relation_select" else "research_report"
            result = service._invoke_model_once({"task": task, "stage": stage},
                                                "fixture", bare_control())
            self.assertEqual(result, {"ok": True})
        by_stage = {stage: (connect, read) for stage, connect, read in provider.seen}
        self.assertLessEqual(by_stage["relation_select"][0], 3.0)
        self.assertLessEqual(by_stage["relation_select"][1], 20.0)
        self.assertLessEqual(by_stage["relation_lite_report"][0], 3.0)
        self.assertLessEqual(by_stage["relation_lite_report"][1], 45.0)
        self.assertEqual(by_stage["event_deep_lite"], (9.0, 120.0))
        self.assertEqual(by_stage["history_report"], (9.0, 120.0))
        self.assertEqual((provider.connect_timeout, provider.read_timeout), (9.0, 120.0))

    def test_shorter_provider_configuration_and_run_remainder_are_not_extended(self):
        short = CaptureGiteeProvider(connect=1.5, read=7.0)
        service = bare_service(short)
        for stage in ("relation_select", "relation_lite_report"):
            service._invoke_model_once({"task": "research_report", "stage": stage},
                                       "fixture", bare_control())
        self.assertEqual([(connect, read) for _, connect, read in short.seen],
                         [(1.5, 7.0), (1.5, 7.0)])
        self.assertEqual((short.connect_timeout, short.read_timeout), (1.5, 7.0))

        long = CaptureGiteeProvider()
        service = bare_service(long)
        start = time.monotonic()
        service._invoke_model_once({"task": "research_report", "stage": "relation_lite_report"},
                                   "fixture", bare_control(start + 8))
        _, connect, read = long.seen[0]
        self.assertLessEqual(connect, 3.0)
        self.assertLessEqual(connect + read, 8.0)
        self.assertEqual((long.connect_timeout, long.read_timeout), (9.0, 120.0))

    def test_page_read_budget_uses_effective_ninety_second_relation_run(self):
        # Simulate 20 seconds used out of the actual 90-second relation budget.
        # A stale generic 600-second setting would reserve 60+15 seconds and
        # skip this otherwise useful page with 70 seconds still available.
        def exercise(run_timeout_seconds):
            reader = RelationReader()
            runtime = RelationLiteRuntime.__new__(RelationLiteRuntime)
            runtime.service = Mock()
            runtime.service.timeout = 600
            runtime.service.public_dns_mode = "fixture"
            runtime.service.research_limits = {"tool_calls": 8}
            runtime.owner = "local-owner"
            runtime.run = {"id": "local-run", "question": "尊界V800事件细节有哪些？"}
            runtime.control = {"stop": threading.Event(),
                               "deadline": time.monotonic() + 70}
            if run_timeout_seconds is not None:
                runtime.control["run_timeout_seconds"] = run_timeout_seconds
            source = {"id": "S1", "url": "https://example.org/v800/page",
                      "title": "公开回应", "summary": "需要核实原文细节。",
                      "text_used": "需要核实原文细节。", "content_kind": "search_snippet"}
            retrieval = {"tool_trace": [{"name": "web_search"}]}
            with patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool",
                       reader.tool):
                runtime._read_news([source], retrieval)
            return reader.calls, source, retrieval

        calls, source, retrieval = exercise(90.0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(source["content_kind"], "extracted_page_text")
        self.assertNotIn("read_stop_reason", retrieval)
        self.assertEqual(retrieval["tool_trace"][-1]["name"], "fetch_public_page")

        old_calls, old_source, old_retrieval = exercise(None)
        self.assertEqual(old_calls, [])
        self.assertEqual(old_source["content_kind"], "search_snippet")
        self.assertEqual(old_retrieval["read_stop_reason"], "write_time_reserved")


class SelectionTimeoutProvider(RelationProvider):
    def __init__(self, *, block=False, fail_code=None):
        super().__init__()
        self.block = block
        self.fail_code = fail_code
        self.started = threading.Event()
        self.release = threading.Event()

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        if payload["stage"] == "relation_select":
            self.started.set()
            if self.block:
                self.release.wait(3)
            if self.fail_code:
                self.payloads.append(payload)
                raise GatewayError("fixture timeout", 504, self.fail_code)
        yield from super().json_tokens(messages, model, stopped, on_response)


class RelationTimeoutIntegrationTests(unittest.TestCase):
    setUp = relation_fixtures.RelationLiteTests.setUp
    tearDown = relation_fixtures.RelationLiteTests.tearDown
    submit = relation_fixtures.RelationLiteTests.submit
    wait = relation_fixtures.RelationLiteTests.wait
    run_with_old_path_blocked = relation_fixtures.RelationLiteTests.run_with_old_path_blocked

    def test_selection_timeout_fails_without_news_pool_or_retry(self):
        self.provider = SelectionTimeoutProvider(fail_code="model_header_timeout")
        self.service.provider = self.provider
        self.service.timeout = 120
        done = self.wait(self.submit())
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["error_code"], "model_header_timeout")
        self.assertEqual([payload["stage"] for payload in self.provider.payloads],
                         ["relation_select"])
        self.assertEqual(done.get("model_retries") or [], [])
        self.assertIsNone(done["report"])
        self.assertEqual(self.news.calls, [])
        self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])

    def test_report_timeout_keeps_news_input_finite_report_memory_and_ok(self):
        self.provider.report_error = GatewayError("fixture timeout", 504,
                                                  "model_header_timeout")
        self.service.timeout = 120
        done = self.run_with_old_path_blocked()
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual([payload["stage"] for payload in self.provider.payloads],
                         ["relation_select", "relation_lite_report"])
        self.assertEqual(done.get("model_retries") or [], [])
        report = done["report"]
        self.assertEqual(report["retrieval"]["report_fallback_reason"],
                         "model_header_timeout")
        self.assertTrue(report["sources"])
        self.assertIn("本轮尚未形成可核实的完整关联结论", report["key_findings"][0])
        self.assertEqual(report["recommendations"], [])
        self.assertEqual(done["next_step_state"], "pending")
        self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)),
                         len(report["sources"]) + 2)
        self.assertTrue(report["input_file_id"])
        self.assertTrue(report["file_id"])
        self.assertEqual(report["memory_output"]["state"]["stages"][-1]["phase"], "report")
        closed = self.client.post(BASE + f"/runs/{done['id']}/finish", json={
            "idempotency_key": str(uuid4())})
        self.assertEqual(closed.status_code, 200, closed.json)
        self.assertEqual(closed.json["run"]["next_step_state"], "finished")
        self.assertTrue(closed.json["session"]["closed_at"])

    def _blocked_run(self, *, relation_cap, global_timeout):
        self.provider = SelectionTimeoutProvider(block=True)
        self.service.provider = self.provider
        self.service.timeout = global_timeout
        with patch("backend.app.modules.explore.service.RELATION_RUN_TIMEOUT_SECONDS",
                   relation_cap):
            run_id = self.submit()
            try:
                self.assertTrue(self.provider.started.wait(2))
                done = self.wait(run_id, {"failed", "partial", "succeeded"})
            finally:
                self.provider.release.set()
        return run_id, done

    def test_relation_run_cap_stops_blocked_selection_without_late_publication(self):
        run_id, done = self._blocked_run(relation_cap=.15, global_timeout=30)
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["error_code"], "explore_run_timeout")
        time.sleep(.05)
        live = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(live["status"], "failed")
        self.assertIsNone(live["report"])
        self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])

    def test_shorter_global_run_timeout_still_wins(self):
        run_id, done = self._blocked_run(relation_cap=90.0, global_timeout=.12)
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["error_code"], "explore_run_timeout")
        time.sleep(.05)
        self.assertIsNone(self.service.repository.get_run(self.owner, run_id)["report"])

    def test_cancelled_selection_cannot_resume_after_late_model_response(self):
        self.provider = SelectionTimeoutProvider(block=True)
        self.service.provider = self.provider
        run_id = self.submit()
        try:
            self.assertTrue(self.provider.started.wait(2))
            cancelled = self.client.post(BASE + f"/runs/{run_id}/cancel")
            self.assertEqual(cancelled.status_code, 200, cancelled.json)
        finally:
            self.provider.release.set()
        done = self.wait(run_id, {"cancelled", "failed", "succeeded", "partial"})
        self.assertEqual(done["status"], "cancelled")
        time.sleep(.05)
        live = self.service.repository.get_run(self.owner, run_id)
        self.assertEqual(live["status"], "cancelled")
        self.assertIsNone(live["report"])
        self.assertEqual(self.service.repository.list_files(self.owner, self.session_id), [])


if __name__ == "__main__":
    unittest.main()
