"""Deterministic HTTP smoke of the real local market and fixed strategy runner.

Creates an isolated temporary DATA_DIR. The matching model is replaced with a
fixed, explicitly labeled response; market loading and backtest execution are real.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4

from backend.app import create_app


ROOT = Path(__file__).resolve().parents[1]
MARKET_ROOT = ROOT / "data" / "strategy-workshop-market"


def key():
    return uuid4().hex


def file_sha256(path):
    hashed = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hashed.update(block)
    return hashed.hexdigest()


def expect(response, status):
    if response.status_code != status:
        raise AssertionError(f"HTTP {response.status_code}, expected {status}: {response.json}")
    return response.json


def finished(client, run_id):
    deadline = time.monotonic() + 55
    while time.monotonic() < deadline:
        value = expect(client.get(f"/desktop/api/strategy-workshop/runs/{run_id}"), 200)
        if value["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return value
        time.sleep(0.1)
    raise AssertionError(f"Backtest {run_id} did not finish in 55 seconds")


def preflight(client, session, *, start, end, cash):
    base = "/desktop/api/strategy-workshop"
    return expect(client.post(f"{base}/sessions/{session['id']}/preflight", json={
        "selectionRevision": session["selectionRevision"], "symbol": "510300.SH",
        "startDate": start, "endDate": end, "initialCash": cash, "idempotencyKey": key(),
    }), 200)


def run(client, session, check):
    body = {"sessionId": session["id"], "preflightId": check["id"],
            "configDigest": check["configDigest"], "mode": "diagnostic",
            "diagnosticAcknowledged": True, "idempotencyKey": key()}
    if check["coverage"]["required"]:
        body["coverageDecision"] = {"decision": "accept_actual_interval",
                                    "coverageDigest": check["coverage"]["digest"]}
    created = expect(client.post("/desktop/api/strategy-workshop/runs", json=body), 202)
    result = finished(client, created["id"])
    if result["status"] != "succeeded":
        raise AssertionError(f"Backtest failed: {result.get('error')}")
    if result["mode"] != "diagnostic" or result["result"]["report_eligible"] is not False:
        raise AssertionError("Real data unexpectedly produced a formal report")
    return result


def brief(value):
    report = value["result"]
    return {
        "runId": value["id"], "status": value["status"], "mode": value["mode"],
        "strategyId": value["strategy"]["strategyId"],
        "requested": value["requested"], "actual": value["actual"],
        "metrics": report["strategy_result"]["metrics"],
        "dataSnapshotSha256": report.get("data_snapshot_sha256"),
        "implementationSha256": report.get("implementation_sha256"),
        "reportEligible": report["report_eligible"],
    }


def main():
    source = MARKET_ROOT / "cd80_source_snapshot.sqlite3"
    normalized = MARKET_ROOT / "derived" / "normalized.sqlite3"
    for path in (source, normalized):
        if not path.is_file():
            raise RuntimeError(f"Public market file is missing: {path}")
    validation_root = ROOT / "data" / "strategy-workshop-validation"
    validation_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cd80-strategy-real-market-", dir=validation_root) as temporary:
        app = create_app({"TESTING": True, "DATA_DIR": temporary, "GITEE_AI_API_KEY": "",
            "IMPORT_LEGACY_HISTORY": False,
            "STRATEGY_WORKSHOP_SOURCE_DB": str(source),
            "STRATEGY_WORKSHOP_MARKET_DB": str(normalized),
            "STRATEGY_WORKSHOP_MAPPING_DIR": str(MARKET_ROOT / "derived")})
        service = app.extensions["strategy_workshop"]
        service.matching.match = lambda idea: {
            "strategyId": "sma_trend", "quality": "partial", "intentValidity": "meaningful",
            "explanation": {"understoodIdea": idea, "implementationApproach": "仅作固定双均线诊断测试。",
                            "whyThisStrategy": "固定模板选择，不调用模型。", "coveredParts": ["趋势"],
                            "uncoveredParts": ["本次未测试真实模型匹配"]}}
        try:
            client = app.test_client()
            expect(client.post("/desktop/api/register", json={
                "email": "strategy-market-smoke@example.test", "password": "temporary-test-password"}), 200)
            capabilities = expect(client.get("/desktop/api/strategy-workshop/capabilities"), 200)
            if not capabilities["available"] or capabilities["strategyCount"] != 10:
                raise AssertionError(f"Public market/catalog unavailable: {capabilities}")

            session = expect(client.post("/desktop/api/strategy-workshop/sessions", json={
                "idea": "用双均线观察趋势", "idempotencyKey": key()}), 201)
            session = expect(client.post(f"/desktop/api/strategy-workshop/sessions/{session['id']}/selection", json={
                "matchRevision": session["matchRevision"],
                "selectionDigest": session["candidate"]["selectionDigest"],
                "decision": "accept", "idempotencyKey": key()}), 200)
            first_check = preflight(client, session, start="2025-07-11", end="2026-09-18", cash=100000)
            first = run(client, session, first_check)
            saved = expect(client.post(f"/desktop/api/strategy-workshop/runs/{first['id']}/personal-app",
                                       json={"idempotencyKey": key()}), 200)["app"]
            catalog = expect(client.get("/desktop/api/agents"), 200)
            if [item["id"] for item in catalog["strategyApps"]] != [saved["id"]]:
                raise AssertionError("Saved strategy did not enter the shared application directory")

            replay = expect(client.post(f"/desktop/api/strategy-workshop/personal-apps/{saved['id']}/sessions",
                                        json={"idempotencyKey": key()}), 201)
            second_check = preflight(client, replay, start="2025-09-01", end="2026-09-18", cash=150000)
            second = run(client, replay, second_check)
            first_after = expect(client.get(f"/desktop/api/strategy-workshop/runs/{first['id']}"), 200)
            first_code = expect(client.get(f"/desktop/api/strategy-workshop/runs/{first['id']}/code"), 200)
            second_code = expect(client.get(f"/desktop/api/strategy-workshop/runs/{second['id']}/code"), 200)
            if first_after["result"] != first["result"] or first_code != second_code:
                raise AssertionError("Reconfiguration changed the saved strategy code or original run")
            if second["sourcePersonalAppId"] != saved["id"] or second["sourceRunId"] != first["id"]:
                raise AssertionError("Rerun source lineage was lost")
            print(json.dumps({"market": {"sourceBytes": source.stat().st_size,
                                       "normalizedBytes": normalized.stat().st_size,
                                       "sourceSha256": file_sha256(source)},
                              "model": "fixed stub, no external call", "appId": saved["id"],
                              "codeIdentical": first_code == second_code,
                              "first": brief(first), "rerun": brief(second)},
                             ensure_ascii=False, sort_keys=True, indent=2))
        finally:
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    main()
