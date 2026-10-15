"""Relation final-choice transaction and file-pool lifecycle regression tests."""

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

import test_explore_relation_lite as fixtures
from backend.app.modules.explore.contracts import has_final_choice
from backend.app.modules.workbench.errors import GatewayError


class RelationChoiceTests(unittest.TestCase):
    setUp = fixtures.RelationLiteTests.setUp
    tearDown = fixtures.RelationLiteTests.tearDown
    submit = fixtures.RelationLiteTests.submit
    wait = fixtures.RelationLiteTests.wait
    run_with_old_path_blocked = fixtures.RelationLiteTests.run_with_old_path_blocked

    def test_each_angle_consumes_parent_once_with_idempotent_replay(self):
        for module in ("event", "relation", "history"):
            with self.subTest(module=module):
                done = self.run_with_old_path_blocked()
                report = done["report"]
                selected = next(row for row in report["recommendations"] if row["module"] == module)
                payload = {"module": module, "question": selected["question"],
                           "source_report_id": report["id"], "idempotency_key": str(uuid4())}
                endpoint = f"/desktop/api/explore/sessions/{self.session_id}/runs"
                before = len(self.service.repository.list_files(self.owner, self.session_id))
                invalid = self.client.post(endpoint, json={**payload, "question": "未经推荐的问题"})
                self.assertEqual(invalid.status_code, 400, invalid.json)
                with patch.object(self.service, "_start"):
                    child = self.client.post(endpoint, json=payload)
                    duplicate = self.client.post(endpoint, json=payload)
                self.assertEqual(child.status_code, 202, child.json)
                self.assertEqual(duplicate.json["run"]["id"], child.json["run"]["id"])
                self.assertEqual(child.json["run"]["module"], module)
                self.assertEqual(child.json["run"]["source_report_id"], report["id"])
                self.assertEqual(len(self.service.repository.list_files(self.owner, self.session_id)), before)
                parent = self.service.repository.get_run(self.owner, done["id"])
                self.assertEqual(parent["next_step_state"], "continued")
                self.assertEqual(parent["continuation_run_id"], child.json["run"]["id"])
                self.assertEqual(self.client.post(endpoint, json={**payload, "idempotency_key": str(uuid4())}).status_code, 409)
                self.assertEqual(self.client.post(f"/desktop/api/explore/runs/{done['id']}/finish",
                    json={"idempotency_key": str(uuid4())}).status_code, 409)
                self.service.cancel(self.owner, child.json["run"]["id"])

    def test_ok_closes_session_keeps_files_readable_and_replays_without_more_work(self):
        done = self.run_with_old_path_blocked()
        files = self.service.repository.list_files(self.owner, self.session_id)
        model_calls, searches = len(self.provider.payloads), len(self.news.calls)
        with patch.object(self.service, "_start"):
            sibling = self.submit("华为和江淮的合作有什么关联？")
        endpoint = f"/desktop/api/explore/runs/{done['id']}/finish"
        key = str(uuid4())
        result = self.client.post(endpoint, json={"idempotency_key": key})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertTrue(result.json["session"]["closed_at"])
        self.assertEqual(result.json["run"]["next_step_state"], "finished")
        self.assertEqual(self.service.repository.get_run(self.owner, sibling)["status"], "cancelled")
        self.assertEqual(self.client.post(endpoint, json={"idempotency_key": key}).status_code, 200)
        self.assertEqual(self.client.post(endpoint, json={"idempotency_key": str(uuid4())}).status_code, 409)
        session_path = f"/desktop/api/explore/sessions/{self.session_id}"
        self.assertEqual(self.client.post(session_path + "/runs", json={"module": "relation",
            "question": "新问题", "idempotency_key": str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.post(f"/desktop/api/explore/runs/{done['id']}/retry",
            json={"idempotency_key": str(uuid4())}).status_code, 409)
        self.assertEqual(self.client.patch(session_path, json={"title": "修改"}).status_code, 409)
        self.assertEqual(self.client.delete(session_path).status_code, 409)
        for file in files:
            self.assertEqual(self.client.get(session_path + f"/files/{file['id']}/download").status_code, 200)
            self.assertEqual(self.client.delete(session_path + f"/files/{file['id']}").status_code, 409)
        self.assertEqual((len(self.provider.payloads), len(self.news.calls)), (model_calls, searches))

    def test_finish_and_followup_race_only_one_wins(self):
        done = self.run_with_old_path_blocked()
        selected = done["report"]["recommendations"][0]
        gate = threading.Barrier(2)

        def finish():
            gate.wait(timeout=2)
            try:
                self.service.finish(self.owner, done["id"], {"idempotency_key": str(uuid4())})
                return "finished"
            except GatewayError as exc:
                return exc.status

        def followup():
            gate.wait(timeout=2)
            try:
                self.service.create_run(self.owner, self.session_id, {
                    "module": selected["module"], "question": selected["question"],
                    "source_report_id": done["report"]["id"], "idempotency_key": str(uuid4())})
                return "continued"
            except GatewayError as exc:
                return exc.status

        with patch.object(self.service, "_start"), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda fn: fn(), (finish, followup)))
        self.assertEqual(sum(value != 409 for value in outcomes), 1, outcomes)
        parent = self.service.repository.get_run(self.owner, done["id"])
        self.assertIn(parent["next_step_state"], {"finished", "continued"})
        if parent["next_step_state"] == "continued":
            self.service.cancel(self.owner, parent["continuation_run_id"])

    def test_partial_and_original_relation_formats_cannot_opt_into_new_finish(self):
        self.assertTrue(has_final_choice({"module": "relation", "report_format": "relation_lite@2"}))
        for module, format in (("relation", "relation_lite@1"), ("relation", "event_lite@2"),
                               ("event", "relation_lite@2"), ("history", "relation_lite@2")):
            self.assertFalse(has_final_choice({"module": module, "report_format": format}))
        done = self.run_with_old_path_blocked()
        repository = self.service.repository
        # Re-present a stored legacy report: it remains readable, but cannot
        # acquire a whole-session close action solely by having pending state.
        with repository.transaction() as db:
            from backend.app.modules.explore.repository import encoded
            report = {**done["report"], "report_format": "relation_lite@1"}
            db.execute("UPDATE reports SET doc=? WHERE id=?", (encoded(report), report["id"]))
        denied = self.client.post(f"/desktop/api/explore/runs/{done['id']}/finish",
                                 json={"idempotency_key": str(uuid4())})
        self.assertEqual(denied.status_code, 409, denied.json)
        self.assertEqual(self.client.get(f"/desktop/api/explore/runs/{done['id']}").status_code, 200)


if __name__ == "__main__":
    unittest.main()
