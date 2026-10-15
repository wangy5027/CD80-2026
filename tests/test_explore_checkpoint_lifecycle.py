"""A reviewed Explore checkpoint survives transient failure without reviving deleted work."""

import tempfile
import threading
import time
import unittest

from backend.app.modules.explore.evidence import EvidenceSession
from backend.app.modules.explore.report_checkpoint import build_checkpoint_report
from backend.app.modules.explore.repository import ExploreRepository, file_id_for, timestamp, uid
from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError


class CheckpointLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="explore-checkpoint-")
        self.addCleanup(self.temp.cleanup)
        self.repo = ExploreRepository(self.temp.name)
        self.owner = "checkpoint-owner"
        self.session = self.repo.create_session(self.owner, "证据审阅")
        self.service = ExploreService.__new__(ExploreService)
        self.service.repository = self.repo
        self.service.timeout = 1
        self.service._lock = threading.RLock()
        self.service._active = {}

    def _selected_file(self):
        file_id = file_id_for("checkpoint-selected:" + uid())
        with self.repo.transaction() as db:
            self.repo._insert_file(
                db, file_id=file_id, owner=self.owner, session_id=self.session["id"],
                origin_key="checkpoint-selected:" + file_id,
                kind="material", origin="web_search", name="学校公告.md",
                content="学校官网公告原文", metadata={"content_kind": "html_fulltext",
                    "context_text": "学校官网公告原文"}, created_at=timestamp())
        return file_id

    def _run_with_checkpoint(self, *, select_file=False):
        selected_id = self._selected_file() if select_file else None
        now = timestamp()
        run = {
            "id": uid(), "session_id": self.session["id"], "module": "event",
            "question": "学校官网如何表述学院设立？", "status": "queued", "stage": "queued",
            "stage_history": [{"stage": "queued", "at": now}],
            "created_at": now, "updated_at": now, "execution_id": uid(),
            "materials": [], "retrieval": {}, "source_report_id": None,
            "time_policy": {"as_of": now},
        }
        selection = {"mode": "manual", "file_ids": [selected_id] if selected_id else []}
        run, _ = self.repo.create_run(self.owner, {"idempotency_key": uid()}, run, 8, selection)
        evidence = EvidenceSession(self.repo, self.owner, run)
        source = evidence.add_source({
            "kind": "public_page", "title": "学校公告", "url": "https://example.org/notice",
            "evidence_text": "学校公告称国际金融学院于本年设立。",
            "text_used": "学校公告称国际金融学院于本年设立。",
            "content_kind": "extracted_page_text", "publisher": "学校官网",
        })
        claim = {"id": "C1", "text": "官网公告称国际金融学院已设立。",
                 "type": "sourced_statement", "support_status": "traceable",
                 "supports": [{"source_ref": source["id"], "evidence_id": source["evidence_id"],
                               "quote": "学校公告称国际金融学院于本年设立。",
                               "background_only": False}]}
        coverage = {"question_id": "Q1", "question": run["question"],
                    "status": "partial", "claim_ids": ["C1"],
                    "gap": "仍需核查教育部表述。", "next_action": "检索教育部网站"}
        brief = {"claims": [claim], "coverage": [coverage], "answer_status": "partial",
                 "blocking_gaps": [coverage["gap"]], "validation_notes": []}
        reviews = [{"id": "C1", "decision": "approve", "type": "sourced_statement",
                    "support_count": 1, "question_ids": ["Q1"]}]
        research = {"task": {"resolved_goal": run["question"], "original_input": run["question"],
                             "as_of": now}, "brief": brief, "historical_ids": set(),
                    "retrieval": {"tool_trace": [{"name": "fetch_public_page", "status": "complete"}]},
                    "budget": {"tool_calls": 1}, "stop_reason": "done"}
        checkpoint = build_checkpoint_report(run, research, brief, [source], reviews)
        self.assertIsNotNone(checkpoint, "reviewed source must produce a checkpoint")
        control = {
            "stop": threading.Event(), "timed_out": threading.Event(),
            "response": None, "execution_id": run["execution_id"],
            "report_checkpoint": checkpoint,
            "checkpoint_evidence_ids": {source["evidence_id"]},
            "checkpoint_file_ids": {selected_id} if selected_id else set(),
            "model_transport_trace": [], "model_retries": [],
        }
        return run, source, selected_id, control

    def _saved(self, run):
        return self.repo.get_run(self.owner, run["id"])

    def test_global_timer_saves_reviewed_partial_and_late_result_cannot_overwrite(self):
        run, source, _, control = self._run_with_checkpoint()
        self.service.timeout = 0.08
        entered = threading.Event()
        release = threading.Event()
        late_complete = []
        # LIFO cleanup: release the fake writer before waiting for its thread.

        def delayed_writer(owner, run_id, worker_control):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test writer was not released")
            late = {**worker_control["report_checkpoint"], "id": uid(),
                    "key_findings": ["迟到稿件不得覆盖已审阅结论。"], "markdown": "# late"}
            late_complete.append(self.repo.complete(owner, run_id, late, "succeeded",
                execution_id=worker_control["execution_id"]))

        self.service._prepare = delayed_writer
        worker = threading.Thread(target=self.service._execute,
            args=(self.owner, run["id"], control), daemon=True)
        worker.start()
        self.addCleanup(worker.join, 3)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2), "writer did not enter its blocked section")
        deadline = time.monotonic() + 3
        saved = self._saved(run)
        while saved["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            saved = self._saved(run)
        self.assertEqual(saved["status"], "partial")
        self.assertTrue(control["timed_out"].is_set())
        self.assertTrue(control["stop"].is_set())
        self.assertEqual(saved["report"]["completion_warning"], "explore_run_timeout")
        self.assertEqual(saved["report"]["claim_reviews"][0]["decision"], "approve")
        self.assertIn(source["id"], saved["report"]["markdown"])
        report_id = saved["report"]["id"]
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(late_complete, [False])
        self.assertEqual(self._saved(run)["report"]["id"], report_id)

    def test_exhausted_transport_retry_saves_existing_approved_checkpoint(self):
        run, _, _, control = self._run_with_checkpoint()
        control["model_retries"] = [{"stage": "report", "error_code": "model_header_timeout"}]
        def exhausted_writer(_owner, _run_id, _control):
            raise GatewayError("等待模型响应头超时", 504, "model_header_timeout")
        self.service._prepare = exhausted_writer
        self.service._execute(self.owner, run["id"], control)
        saved = self._saved(run)
        self.assertEqual(saved["status"], "partial")
        self.assertEqual(saved["report"]["completion_warning"], "model_header_timeout")
        self.assertEqual(saved["report"]["research_budget"]["model_retries"], 1)
        self.assertEqual(saved["report"]["claims"][0]["support_status"], "traceable")
        self.assertIsNone(saved.get("error_code"))

    def test_cancel_and_delete_session_cannot_save_checkpoint(self):
        run, _, _, control = self._run_with_checkpoint()
        control["session_id"] = self.session["id"]
        self.service._active[(self.owner, run["id"])] = control
        self.service.cancel(self.owner, run["id"])
        self.assertTrue(control["stop"].is_set())
        self.assertFalse(self.service._complete_checkpoint(self.owner, run["id"], control, "model_header_timeout"))
        self.assertEqual(self._saved(run)["status"], "cancelled")
        self.assertIsNone(self._saved(run)["report"])

        # A fresh session makes the deletion case independent of cancellation.
        self.session = self.repo.create_session(self.owner, "待删除探索")
        deleted, _, _, deleted_control = self._run_with_checkpoint()
        deleted_control["session_id"] = self.session["id"]
        self.service._active[(self.owner, deleted["id"])] = deleted_control
        self.service.delete_session(self.owner, self.session["id"])
        self.assertTrue(deleted_control["stop"].is_set())
        self.assertFalse(self.service._complete_checkpoint(self.owner, deleted["id"],
                                                           deleted_control, "model_header_timeout"))
        with self.repo.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM reports WHERE run_id=?",
                                        (deleted["id"],)).fetchone()[0], 0)

    def test_deleted_selected_file_or_cited_evidence_cannot_enter_checkpoint(self):
        selected_run, _, selected_id, selected_control = self._run_with_checkpoint(select_file=True)
        self.repo.delete_file(self.owner, self.session["id"], selected_id)
        self.assertFalse(self.service._complete_checkpoint(self.owner, selected_run["id"],
                                                           selected_control, "model_header_timeout"))
        self.assertIsNone(self._saved(selected_run)["report"])

        # The repository also checks the cited evidence's archived file tombstone.
        self.repo.transition(self.owner, selected_run["id"], {"queued"}, status="failed")
        evidence_run, source, _, evidence_control = self._run_with_checkpoint()
        self.repo.delete_file(self.owner, self.session["id"], source["file_id"])
        self.assertFalse(self.service._complete_checkpoint(self.owner, evidence_run["id"],
                                                           evidence_control, "model_header_timeout"))
        self.assertIsNone(self._saved(evidence_run)["report"])

    def test_changed_execution_id_cannot_save_checkpoint(self):
        run, _, _, control = self._run_with_checkpoint()
        self.repo.transition(self.owner, run["id"], {"queued"}, fields={"execution_id": uid()})
        self.assertFalse(self.service._complete_checkpoint(self.owner, run["id"],
                                                           control, "model_header_timeout"))
        self.assertIsNone(self._saved(run)["report"])

    def test_complete_rechecks_tombstone_after_evidence_guard(self):
        run, source, _, control = self._run_with_checkpoint()
        passed_guard = threading.Event()
        def evidence_guard():
            live = self.repo.active_evidence_sources(self.owner, run["id"],
                                                      execution_id=run["execution_id"])
            self.assertIn(source["evidence_id"], {item["evidence_id"] for item in live})
            passed_guard.set()
        control["evidence_guard"] = evidence_guard
        real_complete = self.repo.complete
        def tombstone_before_commit(*args, **kwargs):
            self.assertTrue(passed_guard.is_set())
            self.repo.delete_file(self.owner, self.session["id"], source["file_id"])
            return real_complete(*args, **kwargs)
        self.repo.complete = tombstone_before_commit
        self.assertFalse(self.service._complete_checkpoint(self.owner, run["id"],
                                                           control, "model_header_timeout"))
        self.assertIsNone(self._saved(run)["report"])


if __name__ == "__main__":
    unittest.main()
