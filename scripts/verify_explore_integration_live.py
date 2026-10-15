"""Finite real-upstream Explore acceptance in an isolated local account/database.

Run from the final project with ``.venv/bin/python scripts/verify_explore_integration_live.py --run``.
This intentionally uses the configured model and web-search service. It never starts a
listener or imports any previous product account/history into the disposable DATA_DIR.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=False)


def response_json(client, method, path, *, payload=None, expected=(200,)):
    response = client.open(path, method=method, json=payload)
    if response.status_code not in expected:
        body = response.get_json(silent=True) or {}
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}, "
                           f"code={body.get('code', '')}, error={body.get('error', '')}")
    return response.get_json()


def run_snapshot(run):
    retrieval = run.get("retrieval") or {}
    report = run.get("report") or {}
    trace = run.get("model_transport_trace") or report.get("model_transport_trace") or []
    searches = retrieval.get("searches") or (report.get("retrieval") or {}).get("searches") or []
    return {
        "runId": run.get("id"), "module": run.get("module"), "status": run.get("status"),
        "stage": run.get("stage"), "phase": run.get("phase"),
        "workflowVersion": run.get("workflow_version"), "selectedModel": run.get("model"),
        "initialAnswerMode": run.get("initial_answer_mode"),
        "initialSearchAttempted": run.get("initial_search_attempted"),
        "initialAnswerChars": len(run.get("initial_answer") or ""),
        "reportId": report.get("id"), "reportStatus": report.get("status"),
        "reportVariant": report.get("report_variant"),
        "reportSourceCount": len(report.get("sources") or []),
        "memoryInputVersion": (run.get("memory_input") or {}).get("version"),
        "memoryOutputVersion": (run.get("memory_output") or {}).get("version"),
        "searches": [{"status": row.get("status"), "count": row.get("count"),
                       "freshness": row.get("freshness"), "errorCode": row.get("error_code")}
                      for row in searches if isinstance(row, dict)],
        "modelTrace": [{key: row.get(key) for key in
                        ("status", "task", "stage", "model", "code", "http_status", "finish_reason")
                        if row.get(key) is not None} for row in trace if isinstance(row, dict)],
        "error": run.get("error"),
    }


def wait_run(client, run_id, *, settled, seconds, progress):
    deadline = time.monotonic() + seconds
    previous = None
    last_print = 0.0
    while time.monotonic() < deadline:
        run = response_json(client, "GET", f"/desktop/api/explore/runs/{run_id}")["run"]
        current = (run.get("status"), run.get("stage"), run.get("phase"))
        now = time.monotonic()
        if current != previous or now - last_print > 15:
            print(f"{progress}: {current[0]} / {current[1]} / {current[2]}", flush=True)
            previous, last_print = current, now
        if run["status"] in settled or run["status"] in {"failed", "cancelled", "interrupted"}:
            return run
        time.sleep(1)
    response_json(client, "POST", f"/desktop/api/explore/runs/{run_id}/cancel")
    raise TimeoutError(f"{progress} exceeded {seconds}s; run cancelled")


def export_checks(client, session_id, run):
    files = response_json(client, "GET", f"/desktop/api/explore/sessions/{session_id}/files")["files"]
    report = run.get("report") or {}
    result = {"fileCount": len(files), "reportFileCount": sum(item.get("kind") == "report" for item in files)}
    if report.get("id"):
        report_id = report["id"]
        sources = response_json(client, "GET", f"/desktop/api/explore/reports/{report_id}/sources")["sources"]
        result["reportSourceExportCount"] = len(sources)
        markdown = client.get(f"/desktop/api/explore/reports/{report_id}/markdown")
        if markdown.status_code != 200:
            raise RuntimeError(f"Report markdown download: HTTP {markdown.status_code}")
        result["reportMarkdownBytes"] = len(markdown.data)
        result["reportMarkdownSha256"] = hashlib.sha256(markdown.data).hexdigest()
    report_file = next((item for item in files if item.get("kind") == "report"), None)
    if report_file:
        downloaded = client.get(f"/desktop/api/explore/sessions/{session_id}/files/{report_file['id']}/download")
        if downloaded.status_code != 200:
            raise RuntimeError(f"Shared report file download: HTTP {downloaded.status_code}")
        result["sharedFileBytes"] = len(downloaded.data)
        result["sharedFileSha256"] = hashlib.sha256(downloaded.data).hexdigest()
    memory = response_json(client, "GET", f"/desktop/api/explore/sessions/{session_id}/memory")
    result["memoryVersions"] = [row.get("version") for row in memory.get("memories") or []]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", required=True,
                        help="Explicitly allow bounded real model/search requests")
    args = parser.parse_args()
    assert args.run
    os.umask(0o077)
    if not os.environ.get("GITEE_AI_API_KEY"):
        raise SystemExit("GITEE_AI_API_KEY unavailable; no upstream request sent")
    from backend.app import create_app

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = ROOT / "data/integration-acceptance/explore-live" / f"{stamp}-{uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, mode=0o700)
    summary_path = run_dir / "summary.json"
    summary = {"startedAt": datetime.now(timezone.utc).isoformat(),
               "kind": "real-model-and-search", "dataDir": str(run_dir / "site-data"),
               "productionDataRead": False, "productionDataWrites": False,
               "modelProvider": "Gitee AI", "searchProvider": "Gitee AI web_search",
               "runs": [], "exports": {}, "firstFailure": None, "passed": False}
    config = {
        "TESTING": True, "DATA_DIR": str(run_dir / "site-data"),
        "IMPORT_LEGACY_HISTORY": False, "CAPABILITY_SKIP_PUBLIC_SEED": True,
        "AGENT_RUNTIME_DIR": str(ROOT / "data/agent-runtime"),
        "EXPLORE_EVENT_LITE_ENABLED": True, "EXPLORE_RELATION_LITE_ENABLED": True,
        "EXPLORE_HISTORY_LITE_ENABLED": True,
        "EXPLORE_RUN_TIMEOUT_SECONDS": 180,
        "EXPLORE_RESEARCH_ROUNDS": 3, "EXPLORE_TOOL_CALLS": 8,
        "CD80_ADMIN_API_KEY": "", "CD80_READ_API_KEY": "",
        "SFC_TOKEN": "", "SFC_KEY_FILE": "", "CAPABILITY_MCP_POLICY_FILE": "",
    }
    app = create_app(config)
    client = app.test_client()
    session_id = None
    active_run_id = None
    try:
        random_id = uuid4().hex
        registered = response_json(client, "POST", "/desktop/api/register", payload={
            "email": f"explore-live-{random_id}@example.test", "password": uuid4().hex + uuid4().hex,
            "name": "Explore 集成验收"})
        summary["unifiedAccountId"] = registered["user"]["id"]
        session = response_json(client, "POST", "/desktop/api/explore/sessions",
                                payload={"title": "华为与赛力斯问界合作研究"}, expected=(201,))["session"]
        session_id = session["id"]
        summary["sessionId"] = session_id

        stages = (
            ("event", "请解释华为与赛力斯在问界业务合作中的分工，以及这种合作模式的形成原因。"),
            ("relation", "请分析华为与赛力斯在问界业务合作中的角色、分工和协作机制，区分已核实事实与推断。"),
            ("history", "请根据公开历史材料，对照华为与赛力斯在问界业务合作中角色分工的演变，并说明哪些变化可核实。"),
        )
        source_report_id = None
        source_run_id = None
        source_report = None
        for module, question in stages:
            if source_report:
                recommended = next((row for row in source_report.get("recommendations") or []
                                    if row.get("module") == module), None)
                if recommended:
                    question = recommended["question"]
            payload = {"module": module, "question": question,
                       "model": "GLM-5.3-Flash", "idempotency_key": str(uuid4())}
            if source_report_id:
                payload["source_report_id"] = source_report_id
                payload["source_run_id"] = source_run_id
            created = response_json(client, "POST", f"/desktop/api/explore/sessions/{session_id}/runs",
                                    payload=payload, expected=(202,))["run"]
            active_run_id = created["id"]
            first_status = {"event": {"awaiting_feedback", "succeeded", "partial"},
                            "relation": {"awaiting_clarification", "awaiting_review", "succeeded", "partial"},
                            "history": {"awaiting_clarification", "awaiting_review", "awaiting_input", "succeeded", "partial"}}[module]
            run = wait_run(client, active_run_id, settled=first_status, seconds=150, progress=module)
            if module == "event" and run["status"] == "awaiting_feedback":
                summary["runs"].append({"step": "event_initial", **run_snapshot(run)})
                run = response_json(client, "POST", f"/desktop/api/explore/runs/{active_run_id}/feedback",
                                    payload={"choice": "explain_more", "idempotency_key": str(uuid4())},
                                    expected=(202,))["run"]
                run = wait_run(client, active_run_id, settled={"succeeded", "partial"},
                               seconds=170, progress="event_deep")
            elif module in {"relation", "history"} and run["status"] == "awaiting_clarification":
                revision = (run.get("review") or {}).get("revision")
                if not isinstance(revision, int):
                    raise RuntimeError(f"{module} clarification has no revision")
                run = response_json(client, "POST", f"/desktop/api/explore/runs/{active_run_id}/clarify",
                                    payload={"revision": revision, "question": question,
                                             "idempotency_key": str(uuid4())}, expected=(202,))["run"]
                run = wait_run(client, active_run_id,
                               settled={"awaiting_review", "awaiting_input", "succeeded", "partial"},
                               seconds=150, progress=f"{module}_clarified")
            if run["status"] == "awaiting_review":
                materials = run.get("materials") or []
                source_ids = [row.get("id") for row in materials if row.get("id")][:4]
                revision = (run.get("review") or {}).get("revision")
                if not source_ids or not isinstance(revision, int):
                    raise RuntimeError(f"{module} review has no selectable materials or revision")
                response_json(client, "POST", f"/desktop/api/explore/runs/{active_run_id}/confirm",
                              payload={"source_ids": source_ids, "revision": revision,
                                       "idempotency_key": str(uuid4())}, expected=(202,))
                run = wait_run(client, active_run_id, settled={"succeeded", "partial"},
                               seconds=150, progress=f"{module}_confirmed")
            if run["status"] == "awaiting_input":
                response_json(client, "POST", f"/desktop/api/explore/runs/{active_run_id}/input",
                              payload={"judgment": "华为与赛力斯在问界合作中的分工逐步变化。"}, expected=(202,))
                run = wait_run(client, active_run_id, settled={"succeeded", "partial"},
                               seconds=150, progress=f"{module}_input")
            summary["runs"].append({"step": "event_deep" if module == "event" else module,
                                    **run_snapshot(run)})
            if run["status"] not in {"succeeded", "partial"} or not (run.get("report") or {}).get("id"):
                raise RuntimeError(f"{module} did not produce a persisted report: "
                                   f"status={run.get('status')}, error={run.get('error')}")
            source_report_id = run["report"]["id"]
            source_report = run["report"]
            source_run_id = active_run_id
            summary["exports"][module] = export_checks(client, session_id, run)
            active_run_id = None

        summary["passed"] = (len(summary["runs"]) == 4 and
                             all(row["workflowVersion"].endswith("lite@1") or
                                 row["workflowVersion"] == "relation-lite@2"
                                 for row in summary["runs"]) and
                             summary["exports"]["history"].get("sharedFileBytes", 0) > 0 and
                             summary["exports"]["history"].get("reportMarkdownBytes", 0) > 0)
        print(f"result: {'passed' if summary['passed'] else 'incomplete'}", flush=True)
    except Exception as exc:
        summary["firstFailure"] = {"type": type(exc).__name__, "message": str(exc)}
        print(f"first failure: {type(exc).__name__}: {exc}", flush=True)
        if active_run_id:
            try:
                latest = response_json(client, "GET", f"/desktop/api/explore/runs/{active_run_id}")["run"]
                summary["runs"].append({"step": "failed_step", **run_snapshot(latest)})
            except Exception:
                pass
    finally:
        try:
            app.extensions["workbench_service"].close()
        finally:
            summary["finishedAt"] = datetime.now(timezone.utc).isoformat()
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(summary_path, flush=True)
    if not summary["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
