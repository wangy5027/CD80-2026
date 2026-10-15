"""Opt-in end-to-end strategy smoke with the site's real configured model.

Run from the final project's root. Account/history live in a self-cleaning
directory under its data tree. The model is used for matching and rule writing;
the real public OHLCV data and WASI runtime are used for validation/backtesting.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
IDEA = (
    "仅使用单只 ETF 的日线价量。每天收盘后，如果当日收盘价严格高于"
    "包含当日在内的最近 20 个交易日收盘价简单移动平均线，就把目标仓位设为 1："
    "下一交易日开盘模拟买入或继续持有。若收盘价小于或等于该 20 日均线，"
    "就把目标仓位设为 0：下一交易日开盘模拟卖出或继续空仓。"
    "只做多、单标的、全仓或空仓、无杠杆；不要加入外部数据、60 日均线或其他信号。"
)
INSTRUCTIONS = (
    "请严格保留原意：当日收盘价 > 含当日的 20 日收盘价简单均线时目标为 1，"
    "当日收盘价 <= 该均线时目标为 0；信号在每日收盘后形成，下一交易日开盘"
    "由统一模拟撮合执行。只用日线 OHLCV，不能沿用基准策略的 60 日均线。"
)
BASE = "/desktop/api/strategy-workshop"


def request_key():
    return uuid4().hex


def api(response, status, stage):
    if response.status_code != status:
        error = response.json.get("error", {}) if response.is_json else {}
        if isinstance(error, dict):
            error = {"code": error.get("code"), "stage": error.get("stage")}
        else:
            error = {"kind": "nonstructured"}
        raise RuntimeError(json.dumps({"stage": stage, "http": response.status_code, "error": error}, ensure_ascii=False))
    return response.json


def check_rule(plan):
    """Reject a fluent rewrite that silently changes the requested condition."""
    field = {"type": "field", "name": "close", "lag": 0}
    sma = {"type": "indicator", "name": "sma", "period": 20, "lag": 0, "input": field}
    expected = lambda op: {"type": "compare", "op": op, "left": field, "right": sma}
    return plan.get("features") == [] and plan.get("entry") == expected("gt") and plan.get("exit") == expected("lte")


def finished(client, run_id):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        value = api(client.get(f"{BASE}/runs/{run_id}"), 200, "poll_run")
        if value["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
            return value
        time.sleep(.2)
    raise RuntimeError("run_timeout")


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    from backend.app import create_app
    from backend.app.modules.strategy_workshop.custom_rules import plan_hash

    market = ROOT / "data" / "strategy-workshop-market"
    runtime = ROOT / "data" / "agent-runtime"
    scratch = ROOT / "data" / "strategy-workshop-validation"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cd80-strategy-real-model-", dir=scratch) as temporary:
        app = create_app({"TESTING": True, "DATA_DIR": temporary, "IMPORT_LEGACY_HISTORY": False,
                          "STRATEGY_WORKSHOP_SOURCE_DB": str(market / "cd80_source_snapshot.sqlite3"),
                          "STRATEGY_WORKSHOP_MARKET_DB": str(market / "derived" / "normalized.sqlite3"),
                          "STRATEGY_WORKSHOP_MAPPING_DIR": str(market / "derived"),
                          "AGENT_RUNTIME_DIR": str(runtime)})
        try:
            if not app.config.get("GITEE_AI_API_KEY"):
                raise RuntimeError("target_env_model_key_missing")
            client = app.test_client()
            registered = api(client.post("/desktop/api/register", json={
                "email": "strategy-real-model@example.test", "password": "temporary-test-password"}), 200, "register")
            owner = registered["user"]["id"]
            session = api(client.post(f"{BASE}/sessions", json={"idea": IDEA,
                "languageStyle": "expert", "idempotencyKey": request_key()}), 201, "real_match")
            candidate = session["candidate"]
            matching = {key: candidate.get(key) for key in ("strategyId", "quality", "intentValidity", "fallbackReason", "explanation")}
            print(json.dumps({"stage": "real_match", "model": app.config["GITEE_AI_MODEL"], "matching": matching}, ensure_ascii=False), flush=True)
            if candidate.get("fallbackReason") or candidate.get("intentValidity") != "meaningful":
                raise RuntimeError("real_model_match_not_verified")

            custom_session = api(client.post(f"{BASE}/sessions/{session['id']}/customization", json={
                "matchRevision": session["matchRevision"], "selectionDigest": candidate["selectionDigest"],
                "instructions": INSTRUCTIONS, "languageStyle": "expert", "idempotencyKey": request_key()}),
                200, "real_rewrite")
            custom = custom_session["customization"]
            rewrite = {key: custom.get(key) for key in ("status", "summary", "assumptions", "questions", "dataGaps",
                                                     "rulePlan", "ruleSummary", "sourceHash", "generationAttempts", "attempts")}
            print(json.dumps({"stage": "real_rewrite", "rewrite": rewrite}, ensure_ascii=False), flush=True)
            if custom["status"] != "rules_ready" or not check_rule(custom["rulePlan"]):
                raise RuntimeError("real_model_rule_not_exact_requested_condition")
            if hashlib.sha256(custom["sourceCode"].encode()).hexdigest() != custom["sourceHash"]:
                raise RuntimeError("rewrite_source_hash_mismatch")

            confirmed = api(client.post(f"{BASE}/sessions/{session['id']}/customization/confirm", json={
                "customizationId": custom["id"], "rulesDigest": custom["rulesDigest"],
                "idempotencyKey": request_key()}), 200, "wasi_validation")
            validation = confirmed["customization"]["validation"]
            print(json.dumps({"stage": "wasi_validation", "status": confirmed["customization"]["status"],
                              "checks": validation.get("checks"), "sourceHash": validation.get("sourceHash"),
                              "ruleHash": validation.get("ruleHash")}, ensure_ascii=False), flush=True)
            if (confirmed["customization"]["status"] != "validated" or validation.get("passed") is not True
                    or validation.get("sourceHash") != custom["sourceHash"]
                    or validation.get("ruleHash") != plan_hash(custom["rulePlan"])):
                raise RuntimeError("wasi_validation_failed")

            selected = api(client.post(f"{BASE}/sessions/{session['id']}/selection", json={
                "matchRevision": confirmed["matchRevision"],
                "selectionDigest": confirmed["candidate"]["selectionDigest"],
                "decision": "accept", "idempotencyKey": request_key()}), 200, "confirm_custom_candidate")
            check = api(client.post(f"{BASE}/sessions/{session['id']}/preflight", json={
                "selectionRevision": selected["selectionRevision"], "symbol": "510300.SH",
                "startDate": "2025-07-11", "endDate": "2026-09-18", "initialCash": 100000,
                "idempotencyKey": request_key()}), 200, "public_market_preflight")
            body = {"sessionId": session["id"], "preflightId": check["id"],
                    "configDigest": check["configDigest"], "mode": "diagnostic",
                    "diagnosticAcknowledged": True, "idempotencyKey": request_key()}
            if check["coverage"]["required"]:
                body["coverageDecision"] = {"decision": "accept_actual_interval",
                                             "coverageDigest": check["coverage"]["digest"]}
            created = api(client.post(f"{BASE}/runs", json=body), 202, "start_diagnostic")
            result = finished(client, created["id"])
            code = api(client.get(f"{BASE}/runs/{created['id']}/code"), 200, "frozen_code")
            events = api(client.get(f"{BASE}/runs/{created['id']}/events"), 200, "run_events")
            fills = (result.get("result") or {}).get("strategy_result", {}).get("fills", [])
            stored = app.extensions["strategy_workshop"].repository.read(owner, "run", result["id"])
            snapshot = app.extensions["strategy_workshop"].repository.load_snapshot(owner, stored["executionSnapshotId"])
            dates = [bar["date"] for bar in snapshot["market"]["bars"]]
            previous_trading_date = {date: dates[index - 1] for index, date in enumerate(dates) if index}
            event_types = [item["type"] for item in events["events"]]
            output = {"stage": "diagnostic", "runStatus": result["status"], "mode": result["mode"],
                      "reportEligible": (result.get("result") or {}).get("report_eligible"),
                      "actual": result.get("actual"),
                      "metrics": (result.get("result") or {}).get("strategy_result", {}).get("metrics"),
                      "sourceHash": code.get("sourceHash"), "strategyKind": code.get("strategyKind"),
                      "frozenCodeMatchesConfirmed": code.get("sourceCode") == custom["sourceCode"],
                      "customResultSourceHash": (result.get("result") or {}).get("custom_strategy", {}).get("source_sha256"),
                      "fillExamples": fills[:5], "eventTypes": event_types,
                      "runId": result["id"]}
            print(json.dumps(output, ensure_ascii=False), flush=True)
            if (result["status"] != "succeeded" or result["mode"] != "diagnostic"
                    or result["result"]["report_eligible"] is not False
                    or code.get("sourceCode") != custom["sourceCode"]
                    or code.get("sourceHash") != custom["sourceHash"]
                    or result["result"]["custom_strategy"]["source_sha256"] != custom["sourceHash"]
                    or not fills or any(fill.get("signal_date") != previous_trading_date.get(fill["date"]) for fill in fills)
                    or event_types != ["run.queued", "run.validating", "run.running", "run.succeeded"]):
                raise RuntimeError("diagnostic_contract_mismatch")
        finally:
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    main()
