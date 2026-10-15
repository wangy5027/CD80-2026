"""Saved strategy applications retain immutable, owner-bound backtest versions."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.strategy_workshop.contracts import canonical
from backend.app.modules.strategy_workshop.repository import Repository
import test_workshop_service as workshop_helpers
import test_workshop_customization as custom_helpers
from test_workshop_service import MarketFixture, matching_fixture, request_key


class PersonalStrategyAppTests(unittest.TestCase):
    setUp = workshop_helpers.WorkshopServiceTests.setUp
    tearDown = workshop_helpers.WorkshopServiceTests.tearDown
    session = workshop_helpers.WorkshopServiceTests.session
    prepared = workshop_helpers.WorkshopServiceTests.prepared
    run_body = workshop_helpers.WorkshopServiceTests.run_body
    error = workshop_helpers.WorkshopServiceTests.error
    wait = workshop_helpers.WorkshopServiceTests.wait

    def completed(self):
        session, preflight = self.prepared()
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, self.run_body(session, preflight))
        return self.complete(run)

    def complete(self, run):
        for status in ("validating", "running"):
            self.service.repository.transition(self.owner, run["id"], status)
        self.service.repository.transition(self.owner, run["id"], "succeeded",
            result={"report_eligible": False, "strategy_result": {"equity_curve": [{"equity": 100000}]}})
        return self.service.run(self.owner, run["id"])

    def test_saving_is_unique_durable_and_does_not_modify_or_execute_run(self):
        run = self.completed()
        frozen_run = self.service.repository.read(self.owner, "run", run["id"])
        body = {"idempotencyKey": request_key()}
        with patch.object(self.service.worker, "start") as start, patch.object(self.service.matching, "match") as match:
            saved = self.service.save_personal_app(self.owner, run["id"], body)
            self.assertEqual(saved, self.service.save_personal_app(self.owner, run["id"], body))
            self.assertEqual(saved, self.service.save_personal_app(self.owner, run["id"], {"idempotencyKey": request_key()}))
            self.assertEqual(start.call_count, 0)
            self.assertEqual(match.call_count, 0)
        app = saved["app"]
        self.assertEqual(app["kind"], "strategy")
        self.assertEqual(app["mode"], "diagnostic")
        self.assertEqual(app["strategy"]["parameters"], run["strategy"]["parameters"])
        self.assertEqual(app["sourceHash"], run["candidate"]["sourceHash"])
        self.assertEqual(self.service.list_personal_apps(self.owner), [app])
        self.assertEqual(self.service.run(self.owner, run["id"])["personalApp"], app)
        self.assertNotIn("executionSnapshotId", app)
        self.assertEqual(self.service.repository.read(self.owner, "run", run["id"]), frozen_run)
        fresh_repository = Repository(self.temp.name)
        self.assertEqual(fresh_repository.personal_apps(self.owner)[0]["id"], app["id"])
        opened = self.service.personal_app(self.owner, app["id"])
        self.assertEqual(opened["run"]["id"], run["id"])
        self.assertEqual(opened["run"]["result"], run["result"])
        with self.service.repository.transaction() as db:
            stored = self.service.repository.get(db, self.owner, "personal_app", app["id"])
            self.error(409, self.service.repository.put, db, self.owner, "personal_app", stored)

    def test_owner_isolation_and_strict_inputs(self):
        run = self.completed()
        app = self.service.save_personal_app(self.owner, run["id"], {"idempotencyKey": request_key()})["app"]
        self.error(404, self.service.save_personal_app, "another-owner", run["id"], {"idempotencyKey": request_key()})
        self.error(404, self.service.personal_app, "another-owner", app["id"])
        self.assertEqual(self.service.list_personal_apps("another-owner"), [])
        for extra in ({"owner": self.owner}, {"sourceCode": "fake"}, {"runId": "other"}):
            self.error(400, self.service.save_personal_app, self.owner, run["id"], {"idempotencyKey": request_key(), **extra})
        self.error(400, self.service.save_personal_app, self.owner, run["id"], {"idempotencyKey": "short"})

    def test_unfinished_failed_and_missing_result_cannot_be_saved(self):
        session, preflight = self.prepared()
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, self.run_body(session, preflight))
        self.error(409, self.service.save_personal_app, self.owner, run["id"], {"idempotencyKey": request_key()})
        self.service.repository.transition(self.owner, run["id"], "failed")
        self.error(409, self.service.save_personal_app, self.owner, run["id"], {"idempotencyKey": request_key()})
        with patch.object(self.service.worker, "start"):
            missing = self.service.create_run(self.owner, self.run_body(session, preflight))
        for status in ("validating", "running", "succeeded"):
            self.service.repository.transition(self.owner, missing["id"], status)
        self.error(409, self.service.save_personal_app, self.owner, missing["id"], {"idempotencyKey": request_key()})
        apps = self.service.list_personal_apps(self.owner)
        self.assertEqual(len(apps), 1)  # Rules remain saved even if verification fails.
        self.assertNotEqual(apps[0]["verification"]["status"], "passed")

    def test_concurrent_requests_create_only_one_personal_application(self):
        run = self.completed()
        def save(_):
            return self.service.save_personal_app(self.owner, run["id"], {"idempotencyKey": request_key()})["app"]["id"]
        with ThreadPoolExecutor(max_workers=4) as executor:
            references = list(executor.map(save, range(8)))
        self.assertEqual(len(set(references)), 1)
        self.assertEqual(len(self.service.list_personal_apps(self.owner)), 1)

    def test_corrupt_snapshot_prevents_saving_and_opening(self):
        run = self.completed()
        app = self.service.save_personal_app(self.owner, run["id"], {"idempotencyKey": request_key()})["app"]
        stored = self.service.repository.read(self.owner, "run", run["id"])
        with self.service.repository.transaction() as db:
            row = db.execute("SELECT doc FROM workshop_snapshots WHERE id=?", (stored["executionSnapshotId"],)).fetchone()
            data = json.loads(row["doc"])
            data["market"]["bars"][0]["close"] += 1
            db.execute("UPDATE workshop_snapshots SET doc=? WHERE id=?", (canonical(data), stored["executionSnapshotId"]))
        self.error(503, self.service.personal_app, self.owner, app["id"])
        self.error(503, self.service.save_personal_app, self.owner, run["id"], {"idempotencyKey": request_key()})
        self.assertEqual(len(self.service.list_personal_apps(self.owner)), 1)

    def test_rematching_does_not_change_saved_strategy(self):
        run = self.completed()
        app = self.service.save_personal_app(self.owner, run["id"], {"idempotencyKey": request_key()})["app"]
        self.service.matching.match = lambda text: {**matching_fixture(text), "strategyId": "rsi_reversion"}
        self.service.rematch(self.owner, run["sessionId"], {"idea": "换个 RSI 策略", "expectedRevision": 1,
            "idempotencyKey": request_key()})
        opened = self.service.personal_app(self.owner, app["id"])
        self.assertEqual(opened["run"]["strategy"], run["strategy"])
        self.assertEqual(opened["app"], app)
        self.assertEqual(self.service.code(self.owner, run["id"])["sourceHash"], app["sourceHash"])

    def saved(self):
        run = self.completed()
        app = self.service.save_personal_app(self.owner, run["id"], {"idempotencyKey": request_key()})["app"]
        return run, app

    def replay_preflight(self, session, **extra):
        return self.service.preflight(self.owner, session["id"], {"selectionRevision": 1,
            "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 200000,
            "idempotencyKey": request_key(), **extra})

    def test_saved_app_reconfigures_and_executes_exact_frozen_code_without_ai(self):
        run, app = self.saved()
        original = self.service.repository.read(self.owner, "run", run["id"])
        archive = self.service.repository.load_snapshot(self.owner, original["executionSnapshotId"])
        self.service.matching.match = lambda text: {**matching_fixture(text), "strategyId": "rsi_reversion"}
        self.service.rematch(self.owner, run["sessionId"], {"idea": "改用 RSI", "expectedRevision": 1,
            "idempotencyKey": request_key()})
        body = {"idempotencyKey": request_key()}
        with patch.object(self.service.matching, "match", side_effect=AssertionError("must not rematch")), \
                patch.object(self.service.rewriting, "prepare", side_effect=AssertionError("must not rewrite")), \
                patch.object(self.service, "_archive_package", side_effect=AssertionError("must reuse saved package")):
            session = self.service.personal_app_session(self.owner, app["id"], body)
            self.assertEqual(session, self.service.personal_app_session(self.owner, app["id"], body))
            self.assertEqual(session["status"], "configuring")
            self.assertEqual(session["sourcePersonalAppId"], app["id"])
            self.assertEqual(session["sourceRunId"], run["id"])
            self.assertEqual(session["candidate"]["strategyId"], "sma_trend")
            self.assertEqual(session["rerunDefaults"], run["requested"])
            self.assertNotEqual(session["candidate"]["selectionDigest"], run["candidate"]["selectionDigest"])
            self.assertEqual(self.service.current(self.owner)["session"]["id"], session["id"])
            self.assertEqual(self.service.session(self.owner, session["id"])["candidate"], session["candidate"])
            preflight = self.replay_preflight(session, startDate=self.service.market.bars[300].date)
            rerun = self.service.create_run(self.owner, self.run_body(session, preflight))
            completed = self.wait(rerun["id"])
        self.assertEqual(completed["status"], "succeeded", completed.get("error"))
        self.assertEqual(completed["requested"]["initialCash"], 200000)
        self.assertEqual(completed["sourcePersonalAppId"], app["id"])
        self.assertEqual(completed["sourceRunId"], run["id"])
        self.assertNotEqual(completed["id"], run["id"])
        self.assertEqual(len(completed["result"]["strategy_result"]["equity_curve"]), 100)
        self.assertEqual(self.service.code(self.owner, rerun["id"]), self.service.code(self.owner, run["id"]))
        stored_rerun = self.service.repository.read(self.owner, "run", rerun["id"])
        rerun_archive = self.service.repository.load_snapshot(self.owner, stored_rerun["executionSnapshotId"])
        self.assertEqual(rerun_archive["package"], archive["package"])
        self.assertNotEqual(rerun_archive["market"]["config"], archive["market"]["config"])
        self.assertEqual(self.service.repository.read(self.owner, "run", run["id"]), original)
        self.assertEqual(self.service.personal_app(self.owner, app["id"])["app"], app)
        conversation = self.service.conversation(self.owner, run_id=rerun["id"])
        self.assertTrue(conversation["complete"])
        self.assertEqual(conversation["entries"][0]["title"], "打开个人策略应用")
        self.assertEqual(conversation["entries"][1]["details"]["sourceRunId"], run["id"])
        new_app = self.service.save_personal_app(self.owner, rerun["id"], {"idempotencyKey": request_key()})["app"]
        self.assertNotEqual(new_app["id"], app["id"])
        self.assertEqual(new_app["sourceHash"], app["sourceHash"])

    def test_replay_owner_strict_inputs_and_idempotency(self):
        _, app = self.saved()
        self.error(404, self.service.personal_app_session, "another-owner", app["id"], {"idempotencyKey": request_key()})
        for extra in ({"sourceRunId": "other"}, {"code": "fake"}, {"initialCash": 1}, {"selectionRevision": 2}):
            self.error(400, self.service.personal_app_session, self.owner, app["id"], {"idempotencyKey": request_key(), **extra})
        self.error(400, self.service.personal_app_session, self.owner, app["id"], {"idempotencyKey": "short"})
        self.error(400, self.service.personal_app_session, self.owner, app["id"], {"idempotencyKey": request_key(), "languageStyle": "other"})
        body = {"idempotencyKey": request_key(), "languageStyle": "expert"}
        doc = self.service.personal_app_session(self.owner, app["id"], body)
        self.assertEqual(doc["languageStyle"], "expert")
        self.assertEqual(self.error(409, self.service.personal_app_session, self.owner, app["id"],
            {**body, "languageStyle": "beginner"}).code, "idempotency_conflict")
        self.assertEqual(Repository(self.temp.name).read(self.owner, "session", doc["id"]), doc)

    def test_current_session_uses_insertion_order_when_timestamps_tie(self):
        _, app = self.saved()
        with patch("backend.app.modules.strategy_workshop.service.now", return_value="2100-01-01T00:00:00.000Z"), \
                patch("backend.app.modules.strategy_workshop.service.identifier", side_effect=["ws_z", "ws_a"]):
            first = self.service.personal_app_session(self.owner, app["id"], {"idempotencyKey": request_key()})
            second = self.service.personal_app_session(self.owner, app["id"], {"idempotencyKey": request_key()})
        self.assertEqual(first["updatedAt"], second["updatedAt"])
        self.assertGreater(first["id"], second["id"])
        self.assertEqual(self.service.current(self.owner)["session"]["id"], second["id"])
        self.assertEqual(Repository(self.temp.name).current(self.owner)["id"], second["id"])

    def test_replay_rechecks_coverage_and_diagnostic_gates_and_invalidates_old_parameters(self):
        _, app = self.saved()
        session = self.service.personal_app_session(self.owner, app["id"], {"idempotencyKey": request_key()})
        self.service.market.coverage = True
        preflight = self.replay_preflight(session, symbol="600000.SH")
        self.assertEqual(preflight["requested"]["symbol"], "600000.SH")
        body = self.run_body(session, preflight)
        without_coverage = {name: value for name, value in body.items() if name != "coverageDecision"}
        self.assertEqual(self.error(409, self.service.create_run, self.owner, without_coverage).code, "coverage_confirmation_required")
        self.assertEqual(self.error(422, self.service.create_run, self.owner, {**body, "mode": "formal"}).code, "formal_data_gate")
        self.assertEqual(self.error(422, self.service.create_run, self.owner,
            {**body, "diagnosticAcknowledged": False}).code, "diagnostic_acknowledgement_required")
        self.replay_preflight(session, initialCash=300000)
        self.assertEqual(self.error(409, self.service.create_run, self.owner, body).code, "stale_config")

    def test_replay_rejects_all_rule_changes(self):
        _, app = self.saved()
        session = self.service.personal_app_session(self.owner, app["id"], {"idempotencyKey": request_key()})
        version = {"matchRevision": 1, "selectionDigest": session["candidate"]["selectionDigest"], "idempotencyKey": request_key()}
        requests = [(self.service.rematch, {"idea": "改用 RSI", "expectedRevision": 1, "idempotencyKey": request_key()}),
                    (self.service.selection, {**version, "decision": "reject"}),
                    (self.service.customization, version), (self.service.customization_reset, version),
                    (self.service.customization_confirm, {"customizationId": "custom_other", "rulesDigest": "0" * 64,
                        "idempotencyKey": request_key()})]
        for method, body in requests:
            self.assertEqual(self.error(409, method, self.owner, session["id"], body).code, "saved_strategy_locked")
        self.assertEqual(self.service.session(self.owner, session["id"])["candidate"], session["candidate"])

    def test_replay_rejects_changed_runtime_before_using_current_market_rules(self):
        _, app = self.saved()
        with patch("backend.app.modules.strategy_workshop.service.catalog.implementation_hash", return_value="0" * 64):
            self.assertEqual(self.error(409, self.service.personal_app_session, self.owner, app["id"],
                {"idempotencyKey": request_key()}).code, "saved_strategy_runtime_changed")
        session = self.service.personal_app_session(self.owner, app["id"], {"idempotencyKey": request_key()})
        with patch("backend.app.modules.strategy_workshop.service.catalog.implementation_hash", return_value="0" * 64), \
                patch.object(self.service.market, "prepare") as prepare:
            self.assertEqual(self.error(409, self.replay_preflight, session).code, "saved_strategy_runtime_changed")
            prepare.assert_not_called()

    def test_custom_strategy_preserves_exact_confirmed_code(self):
        custom = custom_helpers.CustomizationTests()
        custom.setUp()
        try:
            confirmed = custom.validated()
            custom.service.selection(custom.owner, confirmed["id"], {**custom.body(confirmed), "decision": "accept"})
            preflight = custom.service.preflight(custom.owner, confirmed["id"], {"selectionRevision": 1,
                "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()})
            with patch.object(custom.service.worker, "start"):
                run = custom.service.create_run(custom.owner, {"sessionId": confirmed["id"], "preflightId": preflight["id"],
                    "configDigest": preflight["configDigest"], "mode": "diagnostic", "diagnosticAcknowledged": True,
                    "idempotencyKey": request_key()})
            for status in ("validating", "running"):
                custom.service.repository.transition(custom.owner, run["id"], status)
            custom.service.repository.transition(custom.owner, run["id"], "succeeded", result={"report_eligible": False})
            app = custom.service.save_personal_app(custom.owner, run["id"], {"idempotencyKey": request_key()})["app"]
            self.assertEqual(app["strategyKind"], "custom")
            self.assertEqual(app["customizationId"], confirmed["customization"]["id"])
            self.assertEqual(app["sourceHash"], confirmed["candidate"]["sourceHash"])
            opened = custom.service.personal_app(custom.owner, app["id"])
            self.assertEqual(opened["run"]["candidate"]["sourceCode"], confirmed["candidate"]["sourceCode"])
            self.assertEqual(custom.service.code(custom.owner, run["id"])["code"], confirmed["candidate"]["sourceCode"])
            original = custom.service.repository.read(custom.owner, "run", run["id"])
            archive = custom.service.repository.load_snapshot(custom.owner, original["executionSnapshotId"])
            custom.service.rematch(custom.owner, confirmed["id"], {"idea": "另一个想法", "expectedRevision": 1,
                "idempotencyKey": request_key()})
            with patch("backend.app.modules.strategy_workshop.service.custom_runtime_digest", return_value="changed"):
                with self.assertRaises(workshop_helpers.GatewayError) as raised:
                    custom.service.personal_app_session(custom.owner, app["id"], {"idempotencyKey": request_key()})
                self.assertEqual(raised.exception.code, "saved_strategy_runtime_changed")
            with patch.object(custom.service.matching, "match", side_effect=AssertionError("must not rematch")), \
                    patch.object(custom.service.rewriting, "prepare", side_effect=AssertionError("must not rewrite")), \
                    patch.object(custom.service, "_archive_package", side_effect=AssertionError("must preserve package")):
                replay = custom.service.personal_app_session(custom.owner, app["id"], {"idempotencyKey": request_key()})
                preflight = custom.service.preflight(custom.owner, replay["id"], {"selectionRevision": 1,
                    "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 250000,
                    "idempotencyKey": request_key()})
                self.assertEqual(custom.service.market.custom_plan, confirmed["customization"]["rulePlan"])
                with patch.object(custom.service.worker, "start"):
                    rerun = custom.service.create_run(custom.owner, {"sessionId": replay["id"], "preflightId": preflight["id"],
                        "configDigest": preflight["configDigest"], "mode": "diagnostic", "diagnosticAcknowledged": True,
                        "idempotencyKey": request_key()})
            stored = custom.service.repository.read(custom.owner, "run", rerun["id"])
            actual = custom.service.repository.load_snapshot(custom.owner, stored["executionSnapshotId"])
            self.assertEqual(actual["package"], archive["package"])
            self.assertEqual(actual["market"]["config"]["initial_cash"], 250000)
            self.assertEqual(rerun["candidate"]["sourceHash"], confirmed["candidate"]["sourceHash"])
            self.assertEqual(rerun["customizationProvenance"], run["customizationProvenance"])
            self.assertEqual(custom.service.code(custom.owner, rerun["id"]), custom.service.code(custom.owner, run["id"]))
            self.assertEqual(custom.service.repository.read(custom.owner, "run", run["id"]), original)
            # The normal worker also accepts the untouched archived custom package.
            from backend.app.modules.agent_platform.executor import SandboxExecutor
            if SandboxExecutor().status()["available"]:
                custom.service.worker.start(custom.owner, stored)
                result = workshop_helpers.WorkshopServiceTests.wait(custom, rerun["id"], timeout=30)
                self.assertEqual(result["status"], "succeeded", result.get("error"))
                self.assertEqual(result["result"]["custom_strategy"]["source_sha256"], app["sourceHash"])
        finally:
            custom.tearDown()


class PersonalStrategyAppHttpTests(unittest.TestCase):
    def test_authenticated_catalog_save_reopen_and_other_account(self):
        with tempfile.TemporaryDirectory(prefix="strategy-app-http-") as directory:
            app = create_app({"TESTING": True, "DATA_DIR": directory, "GITEE_AI_API_KEY": "test-only", "IMPORT_LEGACY_HISTORY": False})
            try:
                client = app.test_client()
                base = "/desktop/api/strategy-workshop"
                self.assertEqual(client.post(base + "/runs/run_unknown/personal-app", json={"idempotencyKey": request_key()}).status_code, 401)
                owner = client.post("/desktop/api/register", json={"email": "strategy-app@example.test", "password": "long-password"}).json["user"]["id"]
                service = app.extensions["strategy_workshop"]
                service.market = MarketFixture()
                service.matching.match = matching_fixture
                helper = PersonalStrategyAppTests()
                helper.service, helper.owner = service, owner
                run = helper.completed()
                saved = client.post(base + "/runs/" + run["id"] + "/personal-app", json={"idempotencyKey": request_key()})
                self.assertEqual(saved.status_code, 200)
                personal = saved.json["app"]
                catalog = client.get("/desktop/api/agents").json
                self.assertEqual(catalog["strategyApps"], [personal])
                self.assertFalse(any(agent.get("id") == personal["id"] for agent in catalog["agents"]))
                self.assertEqual(client.get(base + "/personal-apps/" + personal["id"]).json["run"]["id"], run["id"])
                replay_path = base + "/personal-apps/" + personal["id"] + "/sessions"
                replay = client.post(replay_path, json={"idempotencyKey": request_key()})
                self.assertEqual(replay.status_code, 201)
                self.assertEqual(replay.json["sourcePersonalAppId"], personal["id"])
                self.assertEqual(replay.json["status"], "configuring")
                self.assertEqual(client.get(base + "/sessions/" + replay.json["id"]).json["sourceRunId"], run["id"])
                other = app.test_client()
                other.post("/desktop/api/register", json={"email": "strategy-app-other@example.test", "password": "long-password"})
                self.assertEqual(other.get("/desktop/api/agents").json["strategyApps"], [])
                self.assertEqual(other.get(base + "/personal-apps/" + personal["id"]).status_code, 404)
                self.assertEqual(other.post(base + "/runs/" + run["id"] + "/personal-app", json={"idempotencyKey": request_key()}).status_code, 404)
                self.assertEqual(other.post(replay_path, json={"idempotencyKey": request_key()}).status_code, 404)
                self.assertEqual(app.test_client().post(replay_path, json={"idempotencyKey": request_key()}).status_code, 401)
            finally:
                app.extensions["workbench_service"].close()


if __name__ == "__main__":
    unittest.main()
