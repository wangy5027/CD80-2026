"""Check real history search/model calls using a disposable account and database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", choices=("GLM-5.3", "GLM-5.3-Flash"))
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from backend.app import create_app
    from backend.app.config import Config
    if not Config.GITEE_AI_API_KEY:
        raise RuntimeError("Model configuration unavailable; no real call made.")
    args.output.mkdir(parents=True, exist_ok=True)
    result = {"real_model": True, "real_search": True, "temporary_database": True, "ok": False}
    with tempfile.TemporaryDirectory(prefix="explore-history-live-") as temporary:
        app = create_app({"DATA_DIR": temporary, "IMPORT_LEGACY_HISTORY": False,
                          "AGENT_RUNTIME_DIR": str(Path(temporary) / "runtime"),
                          "EXPLORE_HISTORY_LITE_ENABLED": True})
        client = app.test_client()
        service = app.extensions["explore_service"]
        try:
            registered = client.post("/desktop/api/register", json={
                "email": "history-live@example.invalid", "password": secrets.token_urlsafe(24)})
            assert registered.status_code == 200, "Disposable registration failed"
            created = client.post("/desktop/api/explore/sessions", json={"title": "历史关联真实接口验收"})
            assert created.status_code == 201, "Disposable session failed"
            session_id = created.json["session"]["id"]
            payload = {"module": "history", "question": "华为与赛力斯的合作关系如何形成，这段历史如何帮助理解问界业务的分工？",
                       "idempotency_key": secrets.token_hex(16)}
            if args.model:
                payload["model"] = args.model
            started = time.monotonic()
            response = client.post(f"/desktop/api/explore/sessions/{session_id}/runs", json=payload)
            assert response.status_code == 202, "History run was not accepted"
            run_id = response.json["run"]["id"]
            result["run_id"] = run_id
            seen = None
            while time.monotonic() - started < 105:
                response = client.get(f"/desktop/api/explore/runs/{run_id}")
                assert response.status_code == 200, "History run unavailable"
                run = response.json["run"]
                if run.get("initial_answer") and "initial_seconds" not in result:
                    result.update(initial_seconds=round(time.monotonic() - started, 2),
                                  initial_status=run["status"], initial_answer_mode=run.get("initial_answer_mode"))
                    (args.output / "initial-answer.txt").write_text(run["initial_answer"], encoding="utf-8")
                    print(json.dumps({key: result[key] for key in ("initial_seconds", "initial_status", "initial_answer_mode")}), flush=True)
                if run["stage"] != seen:
                    seen = run["stage"]
                    print(json.dumps({"stage": seen, "elapsed": round(time.monotonic() - started, 2)}), flush=True)
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(.25)
            report = run.get("report") or {}
            result.update(total_seconds=round(time.monotonic() - started, 2), status=run["status"],
                          workflow_version=run.get("workflow_version"), report_format=report.get("report_format"),
                          news_count=len(report.get("sources") or []), next_step_state=run.get("next_step_state"),
                          recommendations=[row["module"] for row in report.get("recommendations") or []],
                          model_calls=[row.get("stage") for row in run.get("model_memory_trace") or []],
                          stage_history=run.get("stage_history"), error_code=run.get("error_code"))
            (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            assert result.get("initial_seconds") is not None and result["initial_status"] == "running", "No early reply before completion"
            assert run["status"] in {"succeeded", "partial"}, "Run did not complete"
            assert result["report_format"] == "history_lite@1" and result["next_step_state"] == "pending"
            assert result["recommendations"] == ["event", "relation", "history"], "Missing final choices"
            assert 1 <= result["news_count"] <= 5, "Expected real historical news"
            assert result["model_calls"] == ["history_initial", "history_lite_report"], "Unexpected model stages"
            finished = client.post(f"/desktop/api/explore/runs/{run_id}/finish", json={"idempotency_key": secrets.token_hex(16)})
            assert finished.status_code == 200, "Finish failed"
            result["ok"] = True
        finally:
            (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            service.close()
            app.extensions["workbench_service"].close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
