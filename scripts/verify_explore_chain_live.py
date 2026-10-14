"""Run the browser through a real Explore chain with isolated, inspectable data.

Only the configured model and public search are real. The browser registers a
disposable account; no existing user data, cookies or credential files are saved.
The captured request bodies contain only this test's public question and sources.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--question", default="请解释华为与赛力斯在问界业务合作中的分工，以及这种合作模式的形成原因。")
    args = parser.parse_args()
    os.umask(0o077)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from flask import jsonify
    from werkzeug.serving import WSGIRequestHandler, make_server
    from backend.app import create_app
    from backend.app.config import Config
    from backend.app.modules.workbench.graph_runtime import GiteeProvider

    if not Config.GITEE_AI_API_KEY:
        raise RuntimeError("Model configuration unavailable; no live calls made.")
    captures, capture_lock = [], threading.Lock()
    sensitive = [Config.GITEE_AI_API_KEY, Config.SFC_TOKEN]

    def scrub(value):
        if isinstance(value, str):
            for secret in sensitive:
                if secret and len(secret) >= 8:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    class CaptureProvider(GiteeProvider):
        def events(self, messages, model, stopped, on_response, **options):
            first_user = next(item for item in messages if item["role"] == "user")
            payload = json.loads(first_user["content"])
            stage = payload.get("stage") or payload.get("task") or "event_initial"
            record = {"stage": stage, "model": model, "payload": scrub(copy.deepcopy(payload)),
                      "started_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()}
            started = time.monotonic()
            with capture_lock:
                captures.append(record)
            try:
                yield from super().events(messages, model, stopped, on_response, **options)
            finally:
                with capture_lock:
                    record["elapsed_seconds"] = round(time.monotonic() - started, 3)

    class QuietHandler(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    summary = {"real_model": True, "real_news": True, "isolated_data": True,
               "question": args.question, "ok": False, "checks": {}}
    with tempfile.TemporaryDirectory(prefix="cd80-chain-live-") as temporary:
        data = Path(temporary) / "data"
        app = create_app({"DATA_DIR": str(data), "IMPORT_LEGACY_HISTORY": False,
                          "AGENT_RUNTIME_DIR": str(Path(temporary) / "runtime"),
                          "FRONTEND_DIST": str(ROOT / "frontend/dist"),
                          "EXPLORE_EVENT_LITE_ENABLED": True,
                          "EXPLORE_RELATION_LITE_ENABLED": True,
                          "EXPLORE_HISTORY_LITE_ENABLED": True})
        service = app.extensions["explore_service"]
        service.provider = CaptureProvider({**app.config,
            "GITEE_AI_MAX_TOKENS": app.config["EXPLORE_MODEL_MAX_TOKENS"],
            "GITEE_AI_REASONING_EFFORT": app.config["EXPLORE_MODEL_REASONING_EFFORT"]})

        @app.get("/__explore_chain__/audit")
        def audit():
            with capture_lock:
                calls = [{"stage": item["stage"], "model": item["model"],
                          "memoryVersion": (item["payload"].get("research_memory") or {}).get("version"),
                          "contextFileIds": [row["file_id"] for row in item["payload"].get("context_files") or []]}
                         for item in captures]
            return jsonify({"fixture": False, "realModel": True, "realNews": True, "calls": calls})

        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
        app.config["DESKTOP_PORT"] = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {**os.environ, "BROWSER_BASE_URL": f"http://127.0.0.1:{server.server_port}/desktop/",
               "CHAIN_LIVE": "1", "CHAIN_EVENT_QUESTION": args.question,
               "EXPLORE_CHAIN_OUTPUT": str(output / "browser"),
               "PLAYWRIGHT_MODULE_DIR": "/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules"}
        # The browser does not need provider credentials; the server uses them.
        for key in tuple(env):
            if key.startswith("GITEE_AI_") or key in {"SFC_TOKEN", "SFC_KEY_FILE", "CD80_ADMIN_API_KEY", "CD80_READ_API_KEY"}:
                env.pop(key, None)
        try:
            print(json.dumps({"status": "starting_browser", "real_model": True, "real_news": True}), flush=True)
            completed = subprocess.run(["/opt/homebrew/bin/node", str(ROOT / "tests/browser_explore_chain.mjs")],
                                       cwd=ROOT, env=env, timeout=650, check=False)
            summary["browser_exit_code"] = completed.returncode
            with sqlite3.connect(data / "explore.sqlite3") as db:
                rows = db.execute("SELECT owner,id FROM runs ORDER BY rowid").fetchall()
            runs = [service.repository.get_run(owner, run_id) for owner, run_id in rows]
            write_json(output / "runs.json", scrub(runs))
            with capture_lock:
                inputs = copy.deepcopy(captures)
            write_json(output / "model-inputs.json", inputs)
            summary["stages"] = [item["stage"] for item in inputs]
            assert completed.returncode == 0, "Browser chain did not complete; see browser-summary.json"
            assert [run["module"] for run in runs] == ["event", "relation", "history"], "Unexpected chain"
            owner = rows[0][0]
            session_id = runs[0]["session_id"]
            assert all(run["session_id"] == session_id for run in runs)
            memories = service.repository.list_memories(owner, session_id)
            files = service.repository.list_files(owner, session_id)
            write_json(output / "memories.json", scrub(memories))
            write_json(output / "file-pool.json", scrub(files))
            assert [memory["version"] for memory in memories] == [1, 2, 3, 4, 5], "Memory phases missing"
            for previous, current in zip(memories, memories[1:]):
                assert current["parent_id"] == previous["id"], "Memory parent chain changed"
            expected = {"event_initial": None, "event_deep_lite": memories[0],
                        "relation_select": memories[1], "relation_lite_report": memories[1],
                        "history_initial": memories[2], "history_lite_report": memories[3]}
            assert set(summary["stages"]) == set(expected), "Unexpected model phase"
            for call in inputs:
                payload = call["payload"]
                memory = payload.get("research_memory")
                wanted = expected[call["stage"]]
                if wanted is None:
                    assert memory is None
                else:
                    for field in ("id", "version", "content_hash", "content"):
                        assert memory[field] == wanted[field], f"Wrong memory {field} in {call['stage']}"
                    assert hashlib.sha256(memory["content"].encode()).hexdigest() == memory["content_hash"]
                if call["stage"].startswith(("relation_", "history_")):
                    run = runs[1] if call["stage"].startswith("relation_") else runs[2]
                    assert payload["context_files"] == run["context_files"], "Frozen file excerpt changed"
                summary["checks"][call["stage"] + "_memory_exact"] = True
            for parent, child in zip(runs, runs[1:]):
                assert child["source_report_id"] == parent["report"]["id"]
                assert child["memory_input"]["id"] == parent["memory_output"]["id"]
                assert parent["report"]["file_id"] in {item["file_id"] for item in child["context_files"]}
            history_reports = {item.get("source_report_id") for item in runs[2]["context_files"] if item.get("kind") == "report"}
            assert {runs[0]["report"]["id"], runs[1]["report"]["id"]} <= history_reports
            for run in runs:
                report = run["report"]
                assert [item["module"] for item in report["recommendations"]] == ["event", "relation", "history"]
                required = [report["file_id"], *list((run.get("initial_file_ids") or {}).values())]
                if run.get("input_file_id"):
                    required.append(run["input_file_id"])
                for file_id in required:
                    assert service.repository.get_file(owner, session_id, file_id)["content"].strip()
            final_stages = memories[-1]["state"]["stages"]
            assert [(item["module"], item["phase"]) for item in final_stages] == [
                ("event", "initial_explanation"), ("event", "report"), ("relation", "report"),
                ("history", "initial_explanation"), ("history", "report")]
            summary.update(ok=True, file_count=len(files), memory_versions=len(memories),
                modules=[{"module": run["module"], "status": run["status"],
                          "context_files": len(run["context_files"]),
                          "source_report_id": run.get("source_report_id"),
                          "report_id": run["report"]["id"], "news_count": len(run["report"]["sources"]),
                          "initial_answer_mode": run.get("initial_answer_mode"),
                          "next_step_state": run.get("next_step_state")} for run in runs])
            summary["checks"].update(parent_reports_in_child_files=True,
                all_analysis_phases_preserved=True, input_and_reports_readable=True)
        finally:
            with capture_lock:
                write_json(output / "model-inputs.json", copy.deepcopy(captures))
            write_json(output / "summary.json", summary)
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            service.close()
            app.extensions["workbench_service"].close()
            server.shutdown()
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
