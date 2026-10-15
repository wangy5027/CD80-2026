"""Conversation replay survives rematches, retries, failures and old databases."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.strategy_workshop.contracts import canonical
from backend.app.modules.workbench.errors import GatewayError
import test_workshop_customization as customization_fixtures
from test_workshop_customization import ready_reply, request_key
from test_workshop_service import MarketFixture, matching_fixture


class ConversationTests(unittest.TestCase):
    setUp = customization_fixtures.CustomizationTests.setUp
    tearDown = customization_fixtures.CustomizationTests.tearDown
    body = customization_fixtures.CustomizationTests.body
    confirm_body = customization_fixtures.CustomizationTests.confirm_body
    validation = staticmethod(customization_fixtures.CustomizationTests.validation)

    def conversation(self, **kwargs):
        return self.service.conversation(self.owner, **(kwargs or {"session_id": self.session["id"]}))

    def prepared_run(self):
        self.service.selection(self.owner, self.session["id"], {**self.body(), "decision": "accept"})
        preflight = self.service.preflight(self.owner, self.session["id"], {"selectionRevision": 1,
            "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()})
        body = {"sessionId": self.session["id"], "preflightId": preflight["id"], "configDigest": preflight["configDigest"],
                "mode": "diagnostic", "diagnosticAcknowledged": True, "idempotencyKey": request_key()}
        with patch.object(self.service.worker, "start"):
            return self.service.create_run(self.owner, body)

    def test_all_versions_questions_validation_and_reset_are_retained(self):
        self.service.rewriting.prepare = lambda *args: {**ready_reply(), "status": "needs_clarification", "plan": None,
            "summary": "请明确均线周期。", "questions": ["短期和长期均线分别采用多少日？"]}
        question = self.service.customization(self.owner, self.session["id"], self.body())
        self.service.rewriting.prepare = lambda *args, **kwargs: ready_reply()
        body = self.body(question, instructions="10/30日，其他不变")
        ready = self.service.customization(self.owner, self.session["id"], body)
        before = self.conversation()
        self.service.customization(self.owner, self.session["id"], body)
        self.assertEqual(before, self.conversation())
        with patch("backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy", self.validation):
            confirmed = self.service.customization_confirm(self.owner, self.session["id"], self.confirm_body(ready))
        self.service.customization_reset(self.owner, self.session["id"], self.body(confirmed))
        conversation = self.conversation()
        self.assertTrue(conversation["complete"])
        versions = [entry["details"]["customization"] for entry in conversation["entries"] if "customization" in entry["details"]]
        self.assertEqual([item["status"] for item in versions], ["needs_clarification", "rules_ready", "validated"])
        self.assertEqual(versions[0]["questions"], ["短期和长期均线分别采用多少日？"])
        self.assertIsNone(versions[0]["sourceCode"])
        self.assertIn("def build_targets", versions[1]["sourceCode"])
        self.assertEqual(conversation["entries"][-2]["details"]["decision"], "reset")
        self.assertTrue(any(entry["text"] == "10/30日，其他不变" and entry["role"] == "user" for entry in conversation["entries"]))

    def test_run_cutoff_survives_same_timestamp_rematch_and_joins_terminal_status(self):
        run = self.prepared_run()
        frozen = self.conversation(run_id=run["id"])
        initial = copy.deepcopy(frozen["entries"])
        with patch("backend.app.modules.strategy_workshop.service.now", return_value=run["createdAt"]):
            self.service.rematch(self.owner, self.session["id"], {"idea": "后来改成RSI，不能进入先前回测的记录", "expectedRevision": 1, "idempotencyKey": request_key()})
        self.assertEqual(initial, self.conversation(run_id=run["id"])["entries"])
        self.assertIn("后来改成RSI", json.dumps(self.conversation(), ensure_ascii=False))
        self.service.repository.transition(self.owner, run["id"], "validating")
        self.service.repository.transition(self.owner, run["id"], "running")
        self.service.repository.transition(self.owner, run["id"], "succeeded", result={"report_eligible": False})
        final = self.conversation(run_id=run["id"])
        self.assertEqual(final["entries"][:len(initial)], initial)
        self.assertEqual(final["entries"][-1]["details"]["status"], "succeeded")
        self.assertNotIn("后来改成RSI", json.dumps(final, ensure_ascii=False))
        self.assertEqual(final["runs"][0]["status"], "succeeded")

    def test_configuration_original_empty_dates_and_explicit_coverage_are_recorded(self):
        self.service.market.coverage = True
        self.service.selection(self.owner, self.session["id"], {**self.body(), "decision": "accept"})
        preflight = self.service.preflight(self.owner, self.session["id"], {"selectionRevision": 1,
            "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 123456, "idempotencyKey": request_key()})
        approval = {"decision": "accept_actual_interval", "coverageDigest": preflight["coverage"]["digest"]}
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, {"sessionId": self.session["id"], "preflightId": preflight["id"],
                "configDigest": preflight["configDigest"], "mode": "diagnostic", "diagnosticAcknowledged": True,
                "coverageDecision": approval, "idempotencyKey": request_key()})
        entries = self.conversation(run_id=run["id"])["entries"]
        configuration = next(item["details"]["configuration"] for item in entries if item["title"] == "设置回测")
        self.assertEqual(configuration["startDate"], "")
        self.assertEqual(configuration["initialCash"], 123456)
        confirmation = next(item["details"]["configuration"] for item in entries if item["title"] == "确认并开始回测")
        self.assertEqual(confirmation["coverageDecision"], approval)

    def test_unfinished_sessions_paginate_and_owner_isolation_applies(self):
        second = self.service.create_session(self.owner, {"idea": "不完成回测也要保存的会话", "idempotencyKey": request_key()})
        listing = self.service.sessions(self.owner, limit=1)
        self.assertEqual(listing["sessions"][0]["id"], second["id"])
        self.assertEqual(listing["sessions"][0]["runCount"], 0)
        self.assertEqual(self.service.sessions(self.owner, cursor=listing["nextCursor"])["sessions"][0]["id"], self.session["id"])
        self.assertEqual(self.service.sessions("other-owner")["sessions"], [])
        run = self.prepared_run()
        for kwargs in ({"session_id": self.session["id"]}, {"run_id": run["id"]}):
            with self.assertRaises(GatewayError) as error:
                self.service.conversation("other-owner", **kwargs)
            self.assertEqual(error.exception.code, "not_found")
        with self.assertRaises(GatewayError):
            self.service.sessions("other-owner", cursor=second["id"])

    def test_legacy_recovery_is_read_only_partial_and_uses_exact_request_cutoff(self):
        run = self.prepared_run()
        with self.service.repository.transaction() as db:
            stored = self.service.repository.get(db, self.owner, "run", run["id"])
            stored.pop("conversationSnapshotId")
            db.execute("UPDATE workshop_objects SET doc=? WHERE id=?", (canonical(stored), run["id"]))
            db.execute("DELETE FROM workshop_conversation")
        self.service.rematch(self.owner, self.session["id"], {"idea": "后来不再使用均线", "expectedRevision": 1, "idempotencyKey": request_key()})
        self.service.repository.transition(self.owner, run["id"], "failed", error={"code": "old_failure", "message": "E:/private/runtime-secret"})
        with self.service.repository.transaction() as db:
            before = db.execute("SELECT count(*) FROM workshop_conversation").fetchone()[0]
        conversation = self.conversation(run_id=run["id"])
        self.assertFalse(conversation["complete"])
        self.assertIn("未保存", conversation["notice"])
        self.assertNotIn("后来不再", json.dumps(conversation, ensure_ascii=False))
        self.assertNotIn("runtime-secret", json.dumps(conversation))
        self.assertEqual(conversation["entries"][-1]["details"]["status"], "failed")
        self.assertFalse(self.conversation()["complete"])
        with self.service.repository.transaction() as db:
            self.assertEqual(before, db.execute("SELECT count(*) FROM workshop_conversation").fetchone()[0])

    def test_legacy_without_requests_uses_immutable_run_instead_of_current_session(self):
        run = self.prepared_run()
        with self.service.repository.transaction() as db:
            stored = self.service.repository.get(db, self.owner, "run", run["id"])
            stored.pop("conversationSnapshotId")
            db.execute("UPDATE workshop_objects SET doc=? WHERE id=?", (canonical(stored), run["id"]))
            db.execute("DELETE FROM workshop_requests")
        self.service.rematch(self.owner, self.session["id"], {"idea": "新的思路", "expectedRevision": 1, "idempotencyKey": request_key()})
        conversation = self.conversation(run_id=run["id"])
        self.assertFalse(conversation["complete"])
        self.assertEqual(conversation["entries"][0]["text"], self.session["idea"])

    def test_success_and_history_commit_together(self):
        body = self.body(instructions="本次写入应当一起回滚")
        with patch("backend.app.modules.strategy_workshop.conversation.append_action", side_effect=RuntimeError("disk")):
            with self.assertRaises(RuntimeError):
                self.service.customization(self.owner, self.session["id"], body)
        self.assertIsNone(self.service.session(self.owner, self.session["id"])["customization"])
        self.assertEqual(len(self.conversation()["entries"]), 2)

    def test_failure_and_external_data_responses_are_retained_without_provider_secrets(self):
        error = GatewayError("secret provider message", 503, "rewrite_unavailable")
        body = self.body(instructions="使用我明确的规则")
        self.service.record_conversation_failure(self.owner, self.session["id"], "customization", body, error)
        before = self.conversation()
        self.service.record_conversation_failure(self.owner, self.session["id"], "customization", body, error)
        self.assertEqual(before, self.conversation())
        self.assertNotIn("secret provider", json.dumps(before))
        self.assertIn("使用我明确的规则", json.dumps(before, ensure_ascii=False))
        self.service.rewriting.prepare = lambda *args: {**ready_reply(), "status": "unsupported_data", "plan": None,
            "summary": "需要微博热度。", "dataGaps": ["微博热度"]}
        self.service.customization(self.owner, self.session["id"], self.body())
        result = self.conversation()
        self.assertIn("本平台暂不支持外源数据", result["entries"][-1]["text"])
        self.assertIn("尚未开放", result["entries"][-1]["text"])
        self.assertEqual(self.service.sessions(self.owner)["sessions"][0]["status"], "unsupported_data")


class ConversationRoutesTests(unittest.TestCase):
    def test_authentication_and_public_failure_replay(self):
        with tempfile.TemporaryDirectory(prefix="cd80-conversation-http-") as directory:
            app = create_app({"TESTING": True, "DATA_DIR": directory, "IMPORT_LEGACY_HISTORY": False})
            service = app.extensions["strategy_workshop"]
            service.market, service.matching.match = MarketFixture(), matching_fixture
            client = app.test_client()
            prefix = "/desktop/api/strategy-workshop"
            try:
                self.assertEqual(client.get(prefix + "/sessions").status_code, 401)
                self.assertEqual(client.post("/desktop/api/register", json={"email": "history@example.test", "password": "long-password"}).status_code, 200)
                session = client.post(prefix + "/sessions", json={"idea": "均线上涨买入", "idempotencyKey": request_key()}).json
                self.assertEqual(client.get(prefix + "/sessions?limit=1").json["sessions"][0]["id"], session["id"])
                service.rewriting.prepare = lambda *args: (_ for _ in ()).throw(GatewayError("RAW-PROVIDER-SECRET", 503, "rewrite_unavailable"))
                response = client.post(prefix + "/sessions/" + session["id"] + "/customization", json={"matchRevision": 1,
                    "selectionDigest": session["candidate"]["selectionDigest"], "instructions": "用10/30日", "idempotencyKey": request_key()})
                self.assertEqual(response.status_code, 503)
                conversation = client.get(prefix + "/sessions/" + session["id"] + "/conversation").json
                self.assertEqual(conversation["entries"][-1]["details"]["error"]["code"], "rewrite_unavailable")
                self.assertNotIn("RAW-PROVIDER-SECRET", json.dumps(conversation))
                self.assertEqual(client.get(prefix + "/sessions?limit=0").status_code, 400)
            finally:
                app.extensions["workbench_service"].close()


if __name__ == "__main__":
    unittest.main()
