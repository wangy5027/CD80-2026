"""Both event model calls receive only the frozen authorized file selection."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.workbench.tool_registry import ToolRegistry
from tests.test_explore_event_lite import ChoiceProvider, NewsTool


BASE = "/desktop/api/explore"


class RecordingProvider(ChoiceProvider):
    def __init__(self):
        super().__init__()
        self.report_payloads = []

    def json_tokens(self, messages, model, stopped, on_response):
        self.report_payloads.append(json.loads(messages[1]["content"]))
        yield from super().json_tokens(messages, model, stopped, on_response)


class EventContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-event-context-")
        root = Path(self.temp.name)
        self.app = create_app({"TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "test-only",
            "IMPORT_LEGACY_HISTORY": False, "EXPLORE_EVENT_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        self.client = self.app.test_client()
        response = self.client.post("/desktop/api/register", json={
            "email": "event-context@example.test", "password": "long-password"})
        self.assertEqual(response.status_code, 200, response.json)
        self.owner = response.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.news = NewsTool()
        self.service.search_tools = ToolRegistry((self.news.search(),))
        self.service.provider = RecordingProvider()
        response = self.client.post(BASE + "/sessions", json={"title": "事件文件选择"})
        self.assertEqual(response.status_code, 201, response.json)
        self.session_id = response.json["session"]["id"]

    def tearDown(self):
        self.service.close()
        self.app.extensions["workbench_service"].close()
        self.temp.cleanup()

    def wait(self, run_id, statuses):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            response = self.client.get(BASE + f"/runs/{run_id}")
            self.assertEqual(response.status_code, 200, response.json)
            run = response.json["run"]
            if run["status"] in statuses:
                return run
            time.sleep(.01)
        self.fail("event run did not settle")

    def submit(self, question, **extra):
        response = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
            "module": "event", "question": question, "idempotency_key": str(uuid4()), **extra})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json["run"]["id"]

    def deepen(self, run_id):
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                   self.news.page):
            response = self.client.post(BASE + f"/runs/{run_id}/feedback", json={
                "choice": "explain_more", "idempotency_key": str(uuid4())})
            self.assertEqual(response.status_code, 202, response.json)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        return done

    def parent(self):
        run_id = self.submit("A股本周的波动与资金预期有什么关系？")
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        return self.deepen(run_id)["report"]

    def test_report_followup_uses_selected_excerpts_in_both_event_calls(self):
        parent = self.parent()
        self.service.provider = RecordingProvider()
        provider = self.service.provider
        selected = parent["recommendations"][0]
        run_id = self.submit(selected["question"], source_report_id=parent["id"])
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        selected_ids = {row["file_id"] for row in waiting["context_files"]}
        self.assertIn(parent["file_id"], selected_ids)
        self.assertIn(parent["sources"][0]["file_id"], selected_ids)
        self.assertEqual(len(provider.initial_payloads), 1)
        initial = provider.initial_payloads[0]
        self.assertEqual(initial["context_files"], waiting["context_files"])
        self.assertEqual(initial["context_limitations"], waiting["context_limitations"])
        self.assertIn(parent["summary"], json.dumps(initial["context_files"], ensure_ascii=False))
        self.assertNotIn("context_snapshot", initial)
        done = self.deepen(run_id)
        self.assertEqual(len(provider.report_payloads), 1)
        report_payload = provider.report_payloads[0]
        self.assertEqual(report_payload["context_files"], done["context_files"])
        self.assertEqual(report_payload["context_limitations"], done["context_limitations"])
        self.assertIn(parent["summary"], json.dumps(report_payload["context_files"], ensure_ascii=False))
        self.assertNotIn("context_snapshot", report_payload)

    def test_manual_empty_selection_is_empty_in_both_calls_while_memory_continues(self):
        parent = self.parent()
        self.service.provider = RecordingProvider()
        provider = self.service.provider
        selected = parent["recommendations"][0]
        run_id = self.submit(selected["question"], source_report_id=parent["id"],
                             context_mode="manual", file_ids=[])
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        self.assertEqual(waiting["context_files"], [])
        self.assertEqual(provider.initial_payloads[0]["context_files"], [])
        self.assertNotIn("context_snapshot", provider.initial_payloads[0])
        self.assertIn(parent["summary"],
                      provider.initial_payloads[0]["research_memory"]["content"])
        done = self.deepen(run_id)
        self.assertEqual(done["context_files"], [])
        self.assertEqual(provider.report_payloads[0]["context_files"], [])
        self.assertNotIn("context_snapshot", provider.report_payloads[0])
        self.assertIn(parent["summary"],
                      provider.report_payloads[0]["research_memory"]["content"])

    def test_deleted_selected_file_blocks_first_event_model(self):
        parent = self.parent()
        self.service.provider = RecordingProvider()
        provider = self.service.provider
        selected = parent["recommendations"][0]
        with patch.object(self.service, "_start"):
            run_id = self.submit(selected["question"], source_report_id=parent["id"])
        frozen = self.service.repository.get_run(self.owner, run_id)
        self.assertIn(parent["file_id"], {row["file_id"] for row in frozen["context_files"]})
        deleted = self.client.delete(BASE + f"/sessions/{self.session_id}/files/{parent['file_id']}")
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.service._start(self.owner, run_id, self.session_id)
        done = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(done["status"], "failed")
        self.assertEqual(provider.initial_payloads, [])
        self.assertIsNone(done.get("initial_answer"))

    def test_deleted_selected_file_blocks_deep_report_publication(self):
        parent = self.parent()
        self.service.provider = RecordingProvider()
        provider = self.service.provider
        provider.report_started = threading.Event()
        provider.report_release = threading.Event()
        selected = parent["recommendations"][0]
        run_id = self.submit(selected["question"], source_report_id=parent["id"])
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        try:
            with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                       self.news.page):
                response = self.client.post(BASE + f"/runs/{run_id}/feedback", json={
                    "choice": "explain_more", "idempotency_key": str(uuid4())})
                self.assertEqual(response.status_code, 202, response.json)
                self.assertTrue(provider.report_started.wait(3))
                self.assertEqual(provider.report_payloads[0]["context_files"],
                                 waiting["context_files"])
                deleted = self.client.delete(
                    BASE + f"/sessions/{self.session_id}/files/{parent['file_id']}")
                self.assertEqual(deleted.status_code, 200, deleted.json)
                provider.report_release.set()
                done = self.wait(run_id, {"succeeded", "partial", "failed"})
        finally:
            provider.report_release.set()
        self.assertEqual(done["status"], "failed")
        self.assertIsNone(done["report"])


if __name__ == "__main__":
    unittest.main()
