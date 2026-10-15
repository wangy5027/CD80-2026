"""Relation follow-ups use only authorized, frozen file-pool context.

These API tests use local model and search doubles. No network service is called.
"""

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
from tests.test_explore_relation_lite import RelationNews, RelationProvider


BASE = "/desktop/api/explore"
FOLLOWUP = "行情页提示的资金抱团防御题材，具体指向哪些板块和个股？"
PARENT_PHRASE = "资金抱团防御题材"


class ParentProvider(ChoiceProvider):
    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        assert payload["stage"] == "event_deep_lite"
        self.report_calls += 1
        refs = [source["id"] for source in payload["sources"]]
        yield json.dumps({
            "title": "本周 A 股行情提示",
            "summary": "行情页提示指数分化、资金抱团防御题材；具体板块和个股尚未核实。",
            "key_findings": ["资金抱团防御题材只是行情页的简要提示。"],
            "sections": [{"heading": "提示的边界",
                          "body": "该提示来自单个行情页，不能据此确定具体板块和个股。",
                          "source_refs": refs[:1]}],
            "recommendations": [
                {"module": "event", "question": "本周行情有哪些可靠数据？", "reason": "核对市场事实"},
                {"module": "relation", "question": FOLLOWUP, "reason": "核对提示所指对象"},
                {"module": "history", "question": "类似行情提示以往指向什么？", "reason": "对比历史"},
            ],
        }, ensure_ascii=False)


class RelationContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-relation-context-")
        root = Path(self.temp.name)
        self.app = create_app({
            "TESTING": True, "DATA_DIR": str(root / "data"),
            "AGENT_RUNTIME_DIR": str(root / "runtime"),
            "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False,
            "EXPLORE_EVENT_LITE_ENABLED": True,
            "EXPLORE_RELATION_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 30,
        })
        self.client = self.app.test_client()
        registered = self.client.post("/desktop/api/register", json={
            "email": "relation-context@example.test", "password": "long-password"})
        self.assertEqual(registered.status_code, 200, registered.json)
        self.owner = registered.json["user"]["id"]
        self.service = self.app.extensions["explore_service"]
        self.parent_provider = ParentProvider()
        self.parent_news = NewsTool()
        self.service.provider = self.parent_provider
        self.service.search_tools = ToolRegistry((self.parent_news.search(),))
        created = self.client.post(BASE + "/sessions", json={"title": "上下文边界测试"})
        self.assertEqual(created.status_code, 201, created.json)
        self.session_id = created.json["session"]["id"]

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
        self.fail("Explore run did not reach the expected state")

    def parent_report(self):
        created = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
            "module": "event", "question": "A股这周的行情怎么样？",
            "idempotency_key": str(uuid4())})
        self.assertEqual(created.status_code, 202, created.json)
        run_id = created.json["run"]["id"]
        waiting = self.wait(run_id, {"awaiting_feedback", "failed"})
        self.assertEqual(waiting["status"], "awaiting_feedback", waiting.get("error"))
        with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool",
                   self.parent_news.page):
            feedback = self.client.post(BASE + f"/runs/{run_id}/feedback", json={
                "choice": "explain_more", "idempotency_key": str(uuid4())})
            self.assertEqual(feedback.status_code, 202, feedback.json)
            done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "succeeded", done.get("error"))
        self.assertEqual(done["next_step_state"], "pending")
        self.assertIn(PARENT_PHRASE, done["report"]["summary"])
        return done["report"]

    def followup(self, report, *, delayed=False):
        self.relation_provider = RelationProvider(entities=[
            {"name": "A股防御题材", "reason": "核对行情提示对应的板块和个股"}])
        self.service.provider = self.relation_provider
        self.service.search_tools = ToolRegistry((RelationNews(mode="empty").tool(),))
        payload = {"module": "relation", "question": FOLLOWUP,
                   "source_report_id": report["id"], "idempotency_key": str(uuid4())}
        endpoint = BASE + f"/sessions/{self.session_id}/runs"
        if delayed:
            with patch.object(self.service, "_start"):
                created = self.client.post(endpoint, json=payload)
        else:
            created = self.client.post(endpoint, json=payload)
        self.assertEqual(created.status_code, 202, created.json)
        return created.json["run"]

    def test_recommended_followup_passes_only_parent_lineage_to_both_model_steps(self):
        unrelated = self.parent_report()
        parent = self.parent_report()
        created = self.followup(parent)
        done = self.wait(created["id"], {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual(done["source_report_id"], parent["id"])
        self.assertEqual(done["context_selection"]["mode"], "auto")
        selected_ids = {item["file_id"] for item in done["context_files"]}
        self.assertIn(parent["file_id"], selected_ids)
        self.assertIn(parent["sources"][0]["file_id"], selected_ids)
        self.assertNotIn(unrelated["file_id"], selected_ids)
        self.assertLessEqual(len(selected_ids), 8)
        self.assertEqual([row["stage"] for row in self.relation_provider.payloads],
                         ["relation_select", "relation_lite_report"])
        for payload in self.relation_provider.payloads:
            self.assertEqual(payload["context_files"], done["context_files"])
            self.assertEqual(payload["context_limitations"], done["context_limitations"])
            self.assertNotIn("context_snapshot", payload)
            self.assertIn(PARENT_PHRASE, json.dumps(payload["context_files"], ensure_ascii=False))
            self.assertNotIn(unrelated["id"], json.dumps(payload, ensure_ascii=False))
        self.assertEqual(self.relation_provider.payloads[1]["sources"], [])

    def test_manual_empty_files_preserve_mandatory_chain_memory(self):
        parent = self.parent_report()
        selection = {"module": "relation", "mode": "manual", "file_ids": []}
        updated = self.client.put(BASE + f"/sessions/{self.session_id}/context", json=selection)
        self.assertEqual(updated.status_code, 200, updated.json)
        created = self.followup(parent)
        done = self.wait(created["id"], {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "partial", done.get("error"))
        self.assertEqual(done["context_selection"], selection)
        self.assertEqual(done["context_snapshot"], [])
        self.assertEqual(done["context_files"], [])
        for payload in self.relation_provider.payloads:
            self.assertEqual(payload["context_files"], [])
            self.assertEqual(payload["context_limitations"], [])
            self.assertNotIn("context_snapshot", payload)
            self.assertIn(parent["summary"], payload["research_memory"]["content"])

    def test_deleted_selected_file_blocks_selection_model(self):
        parent = self.parent_report()
        created = self.followup(parent, delayed=True)
        self.assertIn(parent["file_id"], {row["file_id"] for row in created["context_files"]})
        deleted = self.client.delete(BASE + f"/sessions/{self.session_id}/files/{parent['file_id']}")
        self.assertEqual(deleted.status_code, 200, deleted.json)
        self.service._start(self.owner, created["id"], self.session_id)
        done = self.wait(created["id"], {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "failed")
        self.assertEqual(self.relation_provider.payloads, [])
        self.assertIsNone(done["report"])

    def test_deleted_selected_file_blocks_report_publication(self):
        parent = self.parent_report()
        self.relation_provider = RelationProvider(entities=[
            {"name": "A股防御题材", "reason": "核对行情提示对应的板块和个股"}])
        self.relation_provider.report_started = threading.Event()
        self.relation_provider.report_release = threading.Event()
        self.service.provider = self.relation_provider
        self.service.search_tools = ToolRegistry((RelationNews(mode="empty").tool(),))
        created = self.client.post(BASE + f"/sessions/{self.session_id}/runs", json={
            "module": "relation", "question": FOLLOWUP, "source_report_id": parent["id"],
            "idempotency_key": str(uuid4())})
        self.assertEqual(created.status_code, 202, created.json)
        run_id = created.json["run"]["id"]
        try:
            self.assertTrue(self.relation_provider.report_started.wait(3))
            self.assertEqual([row["stage"] for row in self.relation_provider.payloads],
                             ["relation_select", "relation_lite_report"])
            deleted = self.client.delete(BASE + f"/sessions/{self.session_id}/files/{parent['file_id']}")
            self.assertEqual(deleted.status_code, 200, deleted.json)
        finally:
            self.relation_provider.report_release.set()
        done = self.wait(run_id, {"succeeded", "partial", "failed"})
        self.assertEqual(done["status"], "failed")
        self.assertIsNone(done["report"])


if __name__ == "__main__":
    unittest.main()
