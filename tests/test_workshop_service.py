"""Workflow, snapshot integrity, ownership and real process lifecycle tests."""
import copy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from backend.app import create_app
from backend.app.modules.strategy_workshop.contracts import canonical
from backend.app.modules.strategy_workshop.service import WorkshopService
from backend.app.modules.workbench.errors import GatewayError
from strategy_workshop.fixtures import synthetic_bars
from strategy_workshop.models import BacktestConfig, Instrument


def request_key():
    return uuid4().hex


class MarketFixture:
    def __init__(self):
        self.bars = synthetic_bars("regimes")[:400]
        self.coverage = False

    def capabilities(self):
        return {"available": True, "defaults": {"symbol": "510300.SH", "startDate": self.bars[250].date,
                                                  "endDate": self.bars[-1].date, "initialCash": 100000}}

    def symbols(self, query=""):
        return [{"symbol": "510300.SH", "name": "fixture", "supported": True}]

    def prepare(self, symbol, start, end, strategy_id, cash):
        start, end = start or self.bars[250].date, end or self.bars[-1].date
        config, instrument = BacktestConfig(start, end, initial_cash=cash), Instrument()
        requested = {"symbol": symbol, "startDate": start, "endDate": end, "initialCash": cash}
        return {"requested": requested, "actual": {"startDate": start, "endDate": end, "bars": 150},
                "instrument": asdict(instrument), "asof": self.bars[-1].date,
                "coverage": {"required": self.coverage, "reasons": ["fixture short interval"] if self.coverage else []},
                # Even a falsely asserted declaration cannot open formal reports.
                "gate": {"eligible": True, "checks": {"calendar_complete": True}},
                "snapshot": {"bars": [asdict(bar) for bar in self.bars], "actions": [],
                             "config": asdict(config), "instrument": asdict(instrument),
                             "provenance": {"dataset_kind": "synthetic_fixture", "source_file": "E:/private/source.sqlite3"}}}


def matching_fixture(text):
    return {"strategyId": "sma_trend", "quality": "partial", "intentValidity": "meaningful",
            "explanation": {"understoodIdea": text, "implementationApproach": "可以把趋势判断表达为均线比较规则，再用日线检验。", "whyThisStrategy": "双均线检验价格趋势。",
                            "coveredParts": ["趋势"], "uncoveredParts": ["参数固定"]}}


class WorkshopServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cd80-workshop-")
        self.config = {"DATA_DIR": self.temp.name, "TESTING": True, "STRATEGY_WORKSHOP_RUN_TIMEOUT": 10}
        self.service = WorkshopService(object(), self.config)
        self.service.market = MarketFixture()
        self.service.matching.match = matching_fixture
        self.owner = "owner-a"

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def error(self, status, fn, *args):
        with self.assertRaises(GatewayError) as raised:
            fn(*args)
        self.assertEqual(raised.exception.status, status)
        return raised.exception

    def session(self):
        return self.service.create_session(self.owner, {"idea": "上涨趋势时买入", "idempotencyKey": request_key()})

    def prepared(self):
        session = self.session()
        self.service.selection(self.owner, session["id"], {"matchRevision": session["matchRevision"],
            "selectionDigest": session["candidate"]["selectionDigest"], "decision": "accept", "idempotencyKey": request_key()})
        preflight = self.service.preflight(self.owner, session["id"], {"selectionRevision": session["matchRevision"],
            "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()})
        return session, preflight

    def run_body(self, session, preflight):
        value = {"sessionId": session["id"], "preflightId": preflight["id"], "configDigest": preflight["configDigest"],
                 "mode": "diagnostic", "diagnosticAcknowledged": True, "idempotencyKey": request_key()}
        if preflight["coverage"]["required"]:
            value["coverageDecision"] = {"decision": "accept_actual_interval", "coverageDigest": preflight["coverage"]["digest"]}
        return value

    def wait(self, run_id, statuses=("succeeded", "failed", "cancelled", "interrupted"), timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.service.run(self.owner, run_id)
            if value["status"] in statuses:
                return value
            time.sleep(.02)
        self.fail("Run did not reach expected state: " + repr(value))

    def test_match_idempotency_and_conflicting_payload(self):
        body = {"idea": "趋势上涨时买入", "idempotencyKey": request_key()}
        calls = []
        self.service.matching.match = lambda text: calls.append(text) or matching_fixture(text)
        first = self.service.create_session(self.owner, body)
        self.assertEqual(first, self.service.create_session(self.owner, body))
        self.assertEqual(len(calls), 1)
        self.error(409, self.service.create_session, self.owner, {**body, "idea": "跌了就买"})
        self.assertEqual(first["id"], self.service.current(self.owner)["session"]["id"])
        self.assertIn("rules", first["candidate"])

    def test_selection_is_mandatory_and_rematch_invalidates_confirmation(self):
        session = self.session()
        body = {"selectionRevision": 1, "symbol": "510300.SH", "startDate": "", "endDate": "", "initialCash": 100000, "idempotencyKey": request_key()}
        self.error(409, self.service.preflight, self.owner, session["id"], body)
        newer = self.service.rematch(self.owner, session["id"], {"idea": "我想均线趋势", "expectedRevision": 1, "idempotencyKey": request_key()})
        self.assertEqual(newer["matchRevision"], 2)
        self.error(409, self.service.selection, self.owner, session["id"], {"matchRevision": 1,
            "selectionDigest": session["candidate"]["selectionDigest"], "decision": "accept", "idempotencyKey": request_key()})

    def test_reject_returns_to_editing_without_auto_rematch(self):
        session = self.session()
        rejected = self.service.selection(self.owner, session["id"], {"matchRevision": 1,
            "selectionDigest": session["candidate"]["selectionDigest"], "decision": "reject", "idempotencyKey": request_key()})
        self.assertEqual(rejected["status"], "idea_editing")
        self.assertIsNone(rejected["candidate"])

    def test_formal_gate_cannot_be_bypassed_by_mapping_declarations(self):
        session, preflight = self.prepared()
        self.assertFalse(preflight["gate"]["eligible"])
        body = self.run_body(session, preflight)
        body["mode"] = "formal"
        self.error(422, self.service.create_run, self.owner, body)
        body["mode"] = "diagnostic"
        body["diagnosticAcknowledged"] = False
        self.error(422, self.service.create_run, self.owner, body)

    def test_actual_interval_requires_bound_confirmation(self):
        self.service.market.coverage = True
        session, preflight = self.prepared()
        body = self.run_body(session, preflight)
        del body["coverageDecision"]
        self.error(409, self.service.create_run, self.owner, body)
        body["coverageDecision"] = {"decision": "accept_actual_interval", "coverageDigest": "old-digest"}
        self.error(409, self.service.create_run, self.owner, body)

    def test_new_preflight_invalidates_old_config(self):
        session, old = self.prepared()
        self.service.preflight(self.owner, session["id"], {"selectionRevision": 1, "symbol": "510300.SH",
            "startDate": "", "endDate": "", "initialCash": 200000, "idempotencyKey": request_key()})
        self.error(409, self.service.create_run, self.owner, self.run_body(session, old))

    def test_actual_process_and_frozen_source_data_round_trip(self):
        session, preflight = self.prepared()
        body = self.run_body(session, preflight)
        run = self.service.create_run(self.owner, body)
        self.assertEqual(run, self.service.create_run(self.owner, body))
        completed = self.wait(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertFalse(completed["result"]["report_eligible"])
        self.assertEqual(len(completed["result"]["strategy_result"]["equity_curve"]), 150)
        self.assertNotIn("E:/private", json.dumps(completed))
        code = self.service.code(self.owner, run["id"])
        self.assertEqual(hashlib.sha256(code["code"].encode()).hexdigest(), code["sourceHash"])
        self.assertEqual(code["sourceHash"], session["candidate"]["sourceHash"])
        stored = self.service.repository.read(self.owner, "run", run["id"])
        snapshot = self.service.repository.load_snapshot(self.owner, stored["executionSnapshotId"])
        self.assertEqual(snapshot["market"]["bars"], [asdict(bar) for bar in self.service.market.bars])
        self.assertIn("engine.py", snapshot["package"]["files"])
        self.assertEqual(len(self.service.history(self.owner)["items"]), 1)
        self.assertGreater(len(self.service.events(self.owner, run["id"], 0)["events"]), 2)
        before = copy.deepcopy(completed)
        self.service.cancel(self.owner, run["id"], {"idempotencyKey": request_key()})
        self.assertEqual(self.service.run(self.owner, run["id"]), before)

    def test_corrupt_frozen_snapshot_is_not_rehashed_as_trusted(self):
        session, preflight = self.prepared()
        frozen = self.service.repository.read(self.owner, "preflight", preflight["id"])
        with self.service.repository.transaction() as db:
            row = db.execute("SELECT doc FROM workshop_snapshots WHERE id=?", (frozen["marketSnapshotId"],)).fetchone()
            data = json.loads(row["doc"])
            data["bars"][0]["close"] += .001
            db.execute("UPDATE workshop_snapshots SET doc=? WHERE id=?", (canonical(data), frozen["marketSnapshotId"]))
        error = self.error(503, self.service.create_run, self.owner, self.run_body(session, preflight))
        self.assertEqual(error.code, "snapshot_corrupt")

    def test_owner_isolation_includes_code_cancel_and_snapshots(self):
        session, preflight = self.prepared()
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, self.run_body(session, preflight))
        self.error(404, self.service.session, "owner-b", session["id"])
        self.error(404, self.service.run, "owner-b", run["id"])
        self.error(404, self.service.code, "owner-b", run["id"])
        self.error(404, self.service.cancel, "owner-b", run["id"], {"idempotencyKey": request_key()})
        self.assertEqual(self.service.history("owner-b")["items"], [])

    def test_two_process_limit_and_actual_cancel_stop(self):
        session, preflight = self.prepared()
        with patch("backend.app.modules.strategy_workshop.worker.CHILD_SCRIPT", "import time; time.sleep(30)"):
            first = self.service.create_run(self.owner, self.run_body(session, preflight))
            second = self.service.create_run(self.owner, self.run_body(session, preflight))
            self.wait(first["id"], ("running",))
            self.wait(second["id"], ("running",))
            self.error(429, self.service.create_run, self.owner, self.run_body(session, preflight))
            process = self.service.worker.jobs[first["id"]]["process"]
            response = self.service.cancel(self.owner, first["id"], {"idempotencyKey": request_key()})
            self.assertEqual(response["status"], "cancelling")
            self.assertEqual(self.wait(first["id"])["status"], "cancelled")
            self.assertIsNotNone(process.poll())
            self.service.cancel(self.owner, second["id"], {"idempotencyKey": request_key()})
            self.assertEqual(self.wait(second["id"])["status"], "cancelled")

    def test_cancel_at_result_publication_cannot_stick_in_cancelling(self):
        session, preflight = self.prepared()
        publishing, release = threading.Event(), threading.Event()
        original = self.service.repository.transition
        def delayed(owner, run_id, status, **updates):
            if status == "succeeded":
                publishing.set()
                if not release.wait(5):
                    raise RuntimeError("test publication latch timed out")
            return original(owner, run_id, status, **updates)
        with patch.object(self.service.repository, "transition", delayed):
            run = self.service.create_run(self.owner, self.run_body(session, preflight))
            self.assertTrue(publishing.wait(5))
            self.service.cancel(self.owner, run["id"], {"idempotencyKey": request_key()})
            release.set()
            done = self.wait(run["id"])
        self.assertEqual(done["status"], "cancelled")
        self.assertIsNone(done["result"])

    def test_timeout_stops_actual_process(self):
        self.service.worker.timeout = .15
        session, preflight = self.prepared()
        with patch("backend.app.modules.strategy_workshop.worker.CHILD_SCRIPT", "import time; time.sleep(30)"):
            run = self.service.create_run(self.owner, self.run_body(session, preflight))
            done = self.wait(run["id"])
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["error"]["code"], "execution_timeout")
        self.assertFalse(self.service.worker.jobs)

    def test_restart_marks_orphan_interrupted_and_never_reexecutes(self):
        session, preflight = self.prepared()
        with patch.object(self.service.worker, "start"):
            run = self.service.create_run(self.owner, self.run_body(session, preflight))
        self.service.close()
        replacement = WorkshopService(object(), self.config)
        try:
            self.assertEqual(replacement.run(self.owner, run["id"])["status"], "interrupted")
            self.assertFalse(replacement.worker.jobs)
        finally:
            replacement.close()

    def test_phase_one_does_not_accept_code_or_rule_overrides(self):
        self.error(400, self.service.create_session, self.owner,
                   {"idea": "趋势", "code": "print(1)", "idempotencyKey": request_key()})
        session, preflight = self.prepared()
        body = self.run_body(session, preflight)
        body["parameters"] = {"fast_period": 1}
        self.error(400, self.service.create_run, self.owner, body)


class WorkshopRoutesTests(unittest.TestCase):
    def test_cookie_authentication_and_route_error_contract(self):
        with tempfile.TemporaryDirectory(prefix="cd80-workshop-http-") as directory:
            app = create_app({"TESTING": True, "DATA_DIR": directory, "IMPORT_LEGACY_HISTORY": False})
            service = app.extensions["strategy_workshop"]
            service.market, service.matching.match = MarketFixture(), matching_fixture
            client = app.test_client()
            try:
                response = client.get("/desktop/api/strategy-workshop/capabilities")
                self.assertEqual(response.status_code, 401)
                response = client.post("/desktop/api/register", json={"email": "workshop@example.test", "password": "long-password"})
                self.assertEqual(response.status_code, 200)
                response = client.post("/desktop/api/strategy-workshop/sessions", json={"idea": "趋势上涨时买入", "idempotencyKey": request_key()})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json["status"], "awaiting_selection")
                response = client.post("/desktop/api/strategy-workshop/sessions", json={"idea": "趋势", "parameters": {}, "idempotencyKey": request_key()})
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json["error"]["code"], "invalid_request")
            finally:
                app.extensions["workbench_service"].close()


if __name__ == "__main__":
    unittest.main()
