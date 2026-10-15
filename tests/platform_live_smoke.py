"""Small real-dependency check of the integrated platform; run with --live.

Uses the final project's .env, a disposable account/database, one ordinary chat,
read-only calls to the running local data service, and at most two news fetches.
The saved summary contains no credentials, cookies, full answers or news text.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DESTINATION = ROOT / "data/integration-acceptance/platform-live"
TERMINAL = {"succeeded", "partial", "failed", "cancelled", "interrupted"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def result_code(response):
    body = response.get_json(silent=True)
    return {"http": response.status_code, "code": body.get("code") if isinstance(body, dict) else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--chat-timeout", type=int, default=150)
    parser.add_argument("--direct-chat-diagnostic", action="store_true",
                        help="Only probe the older /chat route with the same configured model")
    parser.add_argument("--chat-only-diagnostic", action="store_true",
                        help="Only probe the current message-queue route")
    parser.add_argument("--agent-max-tokens", type=int,
                        help="Diagnostic override for structured agent calls; requires --chat-only-diagnostic")
    args = parser.parse_args()
    if args.chat_timeout < 30 or args.chat_timeout > 300:
        parser.error("--chat-timeout must be between 30 and 300 seconds")
    if args.agent_max_tokens is not None and (not args.chat_only_diagnostic or
                                               not 256 <= args.agent_max_tokens <= 2048):
        parser.error("--agent-max-tokens requires --chat-only-diagnostic and 256..2048")
    if args.direct_chat_diagnostic and args.chat_only_diagnostic:
        parser.error("choose only one diagnostic mode")

    from dotenv import load_dotenv
    import requests

    load_dotenv(ROOT / ".env", override=True)
    from backend.app import create_app

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_dir = DESTINATION / run_id
    data_dir = run_dir / "site-data"
    data_dir.mkdir(parents=True)
    report_path = run_dir / "summary.json"
    report = {
        "startedAt": timestamp(), "runId": run_id, "project": "最终版",
        "isolatedAccountAndHistory": True, "productionFinancialWrites": False,
        "checks": {}, "passed": False,
        "diagnosticOnly": args.direct_chat_diagnostic or args.chat_only_diagnostic,
        "sourceSha256": {
            path: digest((ROOT / path).read_bytes()) for path in (
                "backend/app/modules/workbench/service.py",
                "backend/app/modules/sub_agent/service.py",
                "backend/app/modules/financial_data/routes.py",
                "backend/app/modules/news/service.py",
                "tests/platform_live_smoke.py",
            )
        },
    }

    def save(stage):
        report["updatedAt"] = timestamp()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        item = report["checks"].get(stage, {})
        print(json.dumps({"stage": stage, "passed": item.get("passed"),
                          "status": item.get("status")}, ensure_ascii=False), flush=True)

    app = None
    try:
        overrides = {"DATA_DIR": str(data_dir), "IMPORT_LEGACY_HISTORY": False,
                     "CAPABILITY_SKIP_PUBLIC_SEED": True,
                     "AGENT_RUNTIME_DIR": str(ROOT / "data/agent-runtime"),
                     "CD80_DATA_ADMIN_USERS": "", "SUB_AGENT_TIMEOUT": args.chat_timeout}
        if args.agent_max_tokens is not None:
            overrides["AGENT_MODEL_MAX_TOKENS"] = args.agent_max_tokens
            report["diagnosticParameters"] = {"agentMaxTokens": args.agent_max_tokens,
                                               "agentReasoningEffort": "low"}
        app = create_app(overrides)
        service = app.extensions["workbench_service"]
        client = app.test_client()
        report["model"] = service.model_id
        report["dataServiceUrl"] = app.config["CD80_DATA_API_URL"]
        registered = client.post("/desktop/api/register", json={
            "email": f"platform-live-{uuid4().hex[:12]}@example.invalid",
            "name": "Platform live validation", "password": uuid4().hex + uuid4().hex,
        })
        if registered.status_code != 200 or not (registered.get_json(silent=True) or {}).get("user", {}).get("id"):
            raise RuntimeError("disposable-account-registration-failed")
        owner = registered.get_json()["user"]["id"]

        if args.direct_chat_diagnostic:
            direct = {"passed": False, "status": "not_started"}
            report["checks"]["directChatDiagnostic"] = direct
            started_at = time.monotonic()
            try:
                response = client.post("/desktop/api/chat", buffered=True, json={
                    "message": "请只用一个阿拉伯数字回答：2+3等于多少？不要搜索，也不要调用工具。",
                    "agentId": "assistant", "modelId": service.model_id, "searchMode": "off"})
                stream = response.get_data(as_text=True)
                tasks = service.store.get(owner).get("tasks") or []
                task = tasks[0] if tasks else {}
                answer = next((str(item.get("content") or "") for item in reversed(task.get("messages") or [])
                               if item.get("role") == "assistant"), "")
                direct.update(http=response.status_code, taskStatus=task.get("status"),
                              errorEvent="event: error" in stream, doneEvent="event: done" in stream,
                              answerLength=len(answer), answerSha256=digest(answer.encode()) if answer else None,
                              expectedFivePresent=bool(re.search(r"(?<!\d)5(?!\d)", answer)),
                              timeoutReported="超时" in str(task.get("error") or ""))
                direct["passed"] = (direct["http"] == 200 and direct["taskStatus"] == "done"
                                    and not direct["errorEvent"] and direct["doneEvent"]
                                    and direct["expectedFivePresent"])
                direct["status"] = "passed" if direct["passed"] else "route_or_model_failed"
            except Exception as error:
                direct["status"] = "check_exception"
                direct["exceptionType"] = type(error).__name__
            direct["elapsedSeconds"] = round(time.monotonic() - started_at, 2)
            save("directChatDiagnostic")
            return 0 if direct["passed"] else 1

        # Follow the present UI path: with sub agents available, the composer
        # posts to /conversations/new/messages rather than the older /chat SSE.
        chat = {"passed": False, "status": "not_started"}
        report["checks"]["ordinaryChat"] = chat
        try:
            if not service.runtime.provider.key:
                chat["status"] = "model_key_missing"
            else:
                prompt = "请只用一个阿拉伯数字回答：2+3等于多少？不要搜索，也不要调用工具。"
                response = client.post("/desktop/api/conversations/new/messages",
                    headers={"Idempotency-Key": "platform-live-" + uuid4().hex},
                    json={"message": prompt, "agentId": "assistant", "modelId": service.model_id,
                          "searchMode": "off", "fileIds": [], "skillNames": [], "capabilityRefs": []})
                chat["submitHttp"] = response.status_code
                if response.status_code != 202:
                    chat.update(status="submission_failed", **result_code(response))
                else:
                    started = response.get_json()
                    run_id_value = started["id"]
                    deadline = time.monotonic() + args.chat_timeout + 10
                    last = started
                    while last.get("status") not in TERMINAL and time.monotonic() < deadline:
                        time.sleep(.5)
                        observed = client.get("/desktop/api/sub-agent-runs/" + run_id_value)
                        if observed.status_code != 200:
                            raise RuntimeError("run-observation-failed")
                        last = observed.get_json()
                    chat["status"] = last.get("status", "unknown")
                    chat["modelCalls"] = [{"stage": call.get("stage"), "status": call.get("status"),
                                           "errorCode": call.get("errorCode")}
                                          for call in last.get("modelCalls", [])]
                    successful = next((call for call in last.get("modelCalls", [])
                                       if call.get("status") == "succeeded"), None)
                    if successful:
                        trace_response = client.get("/desktop/api/sub-agent-runs/" + run_id_value +
                                                    "/model-calls/" + successful["id"])
                        trace = trace_response.get_json(silent=True) or {}
                        trace_detail = trace.get("detail") or {}
                        transport = (trace_detail.get("value") or {}).get("transport") or {}
                        chat["upstream"] = {"provider": transport.get("provider"),
                                            "model": trace.get("model"),
                                            "httpStatus": transport.get("httpStatus"),
                                            "finishReason": transport.get("finishReason")}
                    answer = str(last.get("answer") or "")
                    chat["answerLength"] = len(answer)
                    chat["answerSha256"] = digest(answer.encode("utf-8")) if answer else None
                    chat["expectedFivePresent"] = bool(re.search(r"(?<!\d)5(?!\d)", answer))
                    chat["passed"] = (last.get("status") == "succeeded" and bool(answer)
                                      and chat["expectedFivePresent"] and bool(successful)
                                      and chat.get("upstream", {}).get("httpStatus") == 200
                                      and chat.get("upstream", {}).get("model") == service.model_id)
                    if not chat["passed"] and last.get("status") not in TERMINAL:
                        chat["status"] = "observation_timeout"
        except Exception as error:
            chat["status"] = "check_exception"
            chat["exceptionType"] = type(error).__name__
        save("ordinaryChat")
        if args.chat_only_diagnostic:
            return 0 if chat["passed"] else 1

        financial = {"passed": False, "status": "not_started"}
        report["checks"]["officialLocalFinancialData"] = financial
        try:
            parsed = urlsplit(str(app.config["CD80_DATA_API_URL"]))
            if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port != 8897:
                financial["status"] = "configured_origin_not_local_8897"
            else:
                with requests.Session() as session:
                    session.trust_env = False
                    health = session.get("http://127.0.0.1:8897/health", timeout=5)
                financial["healthHttp"] = health.status_code
                body = health.json() if health.status_code == 200 else {}
                financial["healthTableCount"] = body.get("tables")
                permissions = client.get("/desktop/api/financial-data/capabilities")
                financial["accountCanWrite"] = (permissions.get_json(silent=True) or {}).get("canWrite")
                tables_response = client.get("/desktop/api/financial-data/tables")
                financial["tablesHttp"] = tables_response.status_code
                if tables_response.status_code != 200:
                    financial["errorCode"] = result_code(tables_response)["code"]
                    financial["status"] = "proxy_read_failed"
                else:
                    listing = tables_response.get_json()
                    tables = listing.get("data") or []
                    financial["listedTables"] = len(tables)
                    selected = next((row for row in tables if row.get("name") == "cd80_daily" and row.get("row_count", 0) > 0), None)
                    selected = selected or next((row for row in tables if row.get("row_count", 0) > 0), None)
                    if not selected:
                        financial["status"] = "no_nonempty_table"
                    else:
                        table = selected["name"]
                        financial["sampleTable"] = table
                        schema = client.get(f"/desktop/api/financial-data/tables/{table}/schema")
                        rows = client.get(f"/desktop/api/financial-data/tables/{table}/rows?limit=1&columns=_row_id")
                        financial["schemaHttp"] = schema.status_code
                        financial["rowsHttp"] = rows.status_code
                        row_data = (rows.get_json(silent=True) or {}).get("data") or []
                        financial["sampleRows"] = len(row_data)
                        financial["passed"] = (health.status_code == 200 and body.get("status") == "ok"
                                               and body.get("database") == "ok" and body.get("tables") == 37
                                               and permissions.status_code == 200 and financial["accountCanWrite"] is False
                                               and listing.get("total") == 37 and len(tables) == 37
                                               and schema.status_code == 200 and rows.status_code == 200
                                               and len(row_data) == 1 and type(row_data[0].get("_row_id")) is int)
                        financial["status"] = "passed" if financial["passed"] else "contract_mismatch"
        except Exception as error:
            financial["status"] = "check_exception"
            financial["exceptionType"] = type(error).__name__
        save("officialLocalFinancialData")

        news = {"passed": False, "status": "not_started", "attempts": []}
        report["checks"]["newsUpstream"] = news
        for source in ("eastmoney", "cls"):
            try:
                response = client.get(f"/desktop/api/news?source={source}&limit=1")
                body = response.get_json(silent=True) or {}
                attempt = {"source": source, "http": response.status_code,
                           "code": body.get("code") if response.status_code != 200 else None}
                if response.status_code == 200:
                    items = body.get("items") or []
                    cache = body.get("cache") or {}
                    attempt.update(items=len(items), total=body.get("pagination", {}).get("total"),
                                   cacheHit=cache.get("hit"), stale=cache.get("stale"),
                                   fetchedAt=cache.get("fetched_at"))
                    attempt["passed"] = bool(items and cache.get("hit") is False and cache.get("stale") is False)
                else:
                    attempt["passed"] = False
                news["attempts"].append(attempt)
                if attempt["passed"]:
                    news.update(passed=True, status="passed", successfulSource=source)
                    break
            except Exception as error:
                news["attempts"].append({"source": source, "passed": False,
                                         "exceptionType": type(error).__name__})
        if not news["passed"]:
            news["status"] = "all_real_upstreams_failed"
        save("newsUpstream")
    except Exception as error:
        report["fatal"] = {"type": type(error).__name__, "stage": "setup"}
    finally:
        if app is not None:
            app.extensions["workbench_service"].close()
        report["finishedAt"] = timestamp()
        required = (("directChatDiagnostic",) if args.direct_chat_diagnostic else
                    ("ordinaryChat",) if args.chat_only_diagnostic else
                    ("ordinaryChat", "officialLocalFinancialData", "newsUpstream"))
        report["passed"] = all(report["checks"].get(name, {}).get("passed") for name in required)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"report": str(report_path), "passed": report["passed"],
                          "checks": {name: value.get("status") for name, value in report["checks"].items()}},
                         ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
