"""Exercise the two current Explore Skills with real model and search services.

The Flask app, account, sessions, and databases are disposable. Only the
public questions, run traces, reports, and verification summary are saved.

    .venv/bin/python scripts/validate_explore_lite_skills_live.py \
        --output /tmp/explore-lite-live
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EVENT_QUESTION = "近期英伟达股价为什么容易受到人工智能算力需求预期影响？"
RELATION_QUESTION = "近期人工智能算力需求变化可能通过什么关键关联影响英伟达？"
ACTIVE = {"queued", "running"}
FINISHED = {"succeeded", "partial"}
FORBIDDEN_REVIEW_STAGES = {
    "checking_intent", "awaiting_review", "preparing_report", "validating",
    "reviewing_claims", "claim_review", "researching", "evidence_review",
}
PRIVATE_KEYS = ("password", "cookie", "token", "secret", "api_key", "authorization", "account", "email")


def _private_values(config):
    return tuple(value for value in (
        config.get("GITEE_AI_API_KEY"), config.get("SFC_TOKEN"),
        config.get("CD80_ADMIN_API_KEY"), config.get("CD80_READ_API_KEY"),
    ) if isinstance(value, str) and value)


def _safe(value, private_values):
    """Keep diagnostic run data while excluding local identity and credentials."""
    if isinstance(value, dict):
        return {key: _safe(item, private_values) for key, item in value.items()
                if not any(part in str(key).lower() for part in PRIVATE_KEYS)}
    if isinstance(value, list):
        return [_safe(item, private_values) for item in value]
    if isinstance(value, str):
        for secret in private_values:
            value = value.replace(secret, "[redacted]")
    return value


def _write(path, value, private_values=()):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.suffix == ".json":
        content = json.dumps(_safe(value, private_values), ensure_ascii=False, indent=2) + "\n"
    else:
        content = _safe(str(value), private_values)
    with path.open("w", encoding="utf-8") as stream:
        stream.write(content)
    os.chmod(path, 0o600)


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _api(client, method, path, *, body=None):
    response = client.open("/desktop/api" + path, method=method, json=body)
    payload = response.get_json(silent=True)
    if not 200 <= response.status_code < 300 or not isinstance(payload, dict):
        code = payload.get("code") if isinstance(payload, dict) else None
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code}, code={code or 'none'}")
    return payload


def _get_run(client, run_id):
    return _api(client, "GET", f"/explore/runs/{run_id}")["run"]


def _wait_run(client, run_id, wanted, deadline, case_name):
    last_state = None
    next_heartbeat = 0.0
    while True:
        run = _get_run(client, run_id)
        now = time.monotonic()
        state = (run.get("status"), run.get("stage"))
        if state != last_state or now >= next_heartbeat:
            print(f"{case_name}: status={state[0]} stage={state[1]}", flush=True)
            last_state, next_heartbeat = state, now + 20
        if run.get("status") in wanted:
            return run
        if run.get("status") not in ACTIVE:
            return run
        remaining = deadline - now
        if remaining <= 0:
            return run
        time.sleep(min(1.0, remaining))


def _skill_hashes(service):
    hashes = {}
    for name in ("explore-event", "explore-relation"):
        body = service.research_skills.load(name)["instructions"]
        hashes[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return hashes


def _assert_initial(run, expected_hash):
    _check(run.get("status") == "awaiting_feedback", "event did not wait for feedback")
    _check(run.get("workflow_version") == "event-lite@1", "event used an unexpected workflow")
    _check(bool(run.get("initial_answer")), "event returned no initial answer")
    _check(run.get("skill_hash") == expected_hash, "event loaded an outdated Skill body")
    _check(run.get("report") is None, "event saved a report before feedback")
    _check(not run.get("materials"), "event collected materials before feedback")
    _check(not (run.get("retrieval") or {}).get("tool_trace"), "event called tools before feedback")
    stages = {item.get("stage") for item in run.get("stage_history") or []}
    _check(not stages & {"searching_web", "reading_pages", "analyzing"},
           "event entered a retrieval stage before feedback")


def _assert_skill_report(report, name, expected_hash, workflow, report_format):
    _check(report.get("workflow_version") == workflow, "report workflow mismatch")
    _check(report.get("report_format") == report_format, "report format mismatch")
    _check(report.get("skills_used") == [{"name": name, "content_hash": expected_hash}],
           "report does not identify the current Skill body")
    _check(bool(report.get("markdown")), "report Markdown is empty")


def _trace(report):
    trace = (report.get("retrieval") or {}).get("tool_trace")
    _check(isinstance(trace, list), "report has no tool trace")
    return trace


def _assert_tools(trace, *, search_max, search_count, fetch_max):
    names = [item.get("name") for item in trace]
    _check(set(names) <= {"web_search", "fetch_public_page"}, "legacy or unexpected tool was used")
    searches = [item for item in trace if item.get("name") == "web_search"]
    fetches = [item for item in trace if item.get("name") == "fetch_public_page"]
    _check(1 <= len(searches) <= search_max, "web search count is outside the lite budget")
    _check(len(fetches) <= fetch_max, "public-page reads exceed the lite budget")
    _check(all(item.get("arguments", {}).get("count") == search_count for item in searches),
           "web search requested the wrong article count")
    return len(searches), len(fetches)


def _case(client, name, module, question, choice, case_dir, timeout, hashes, private_values):
    started = time.monotonic()
    deadline = started + timeout
    case_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    session = _api(client, "POST", "/explore/sessions",
                   body={"title": "轻量 Skill 真实验收"})["session"]
    run_id = None
    latest = None
    try:
        created = _api(client, "POST", f"/explore/sessions/{session['id']}/runs", body={
            "module": module, "question": question,
            "idempotency_key": secrets.token_hex(16)})["run"]
        run_id = created["id"]
        print(f"{name}: created", flush=True)
        latest = _wait_run(client, run_id,
                           {"awaiting_feedback"} if module == "event" else FINISHED,
                           deadline, name)
        if module == "event":
            _write(case_dir / "run_initial.json", latest, private_values)
            _write(case_dir / "initial_answer.md", latest.get("initial_answer") or "", private_values)
            _assert_initial(latest, hashes["explore-event"])
            latest = _api(client, "POST", f"/explore/runs/{run_id}/feedback", body={
                "choice": choice, "idempotency_key": secrets.token_hex(16)})["run"]
            print(f"{name}: feedback={choice}", flush=True)
            latest = _wait_run(client, run_id, FINISHED, deadline, name)
            _check(latest.get("feedback_choice") == choice, "feedback choice was not persisted")
            _check(latest.get("workflow_version") == "event-lite@1", "event workflow changed")
            if choice == "understood":
                _check(latest.get("status") == "succeeded", "understood did not end the run")
                _check(latest.get("report") is None, "understood unexpectedly generated a report")
                _check(not latest.get("materials"), "understood unexpectedly retrieved news")
                _check(not (latest.get("retrieval") or {}).get("tool_trace"),
                       "understood unexpectedly called a tool")
                _check(latest.get("skill_hash") == hashes["explore-event"],
                       "understood lost the current Skill hash")
                result = {"status": "succeeded", "workflow_version": "event-lite@1",
                          "search_calls": 0, "fetch_calls": 0, "news_count": 0,
                          "skill_hash": hashes["explore-event"]}
            else:
                _check(latest.get("status") in FINISHED, "event report did not complete")
                report = latest.get("report")
                _check(isinstance(report, dict), "event report was not saved")
                _assert_skill_report(report, "explore-event", hashes["explore-event"],
                                     "event-lite@1", "event_deep_lite@1")
                trace = _trace(report)
                search_calls, fetch_calls = _assert_tools(trace, search_max=1,
                                                          search_count=5, fetch_max=5)
                news_count = len(report.get("sources") or [])
                _check(news_count <= 5, "event saved more than five news items")
                _write(case_dir / "report.json", report, private_values)
                _write(case_dir / "report.md", report["markdown"], private_values)
                result = {"status": latest["status"], "workflow_version": "event-lite@1",
                          "report_format": report["report_format"], "report_id": report["id"],
                          "search_calls": search_calls, "fetch_calls": fetch_calls,
                          "news_count": news_count, "skill_hash": hashes["explore-event"]}
        else:
            _check(latest.get("status") in FINISHED, "relation run did not complete")
            _check(latest.get("workflow_version") == "relation-lite@1",
                   "relation used an unexpected workflow")
            report = latest.get("report")
            _check(isinstance(report, dict), "relation report was not saved")
            _assert_skill_report(report, "explore-relation", hashes["explore-relation"],
                                 "relation-lite@1", "relation_lite@1")
            trace = _trace(report)
            search_calls, fetch_calls = _assert_tools(trace, search_max=2,
                                                      search_count=3, fetch_max=6)
            selected = report.get("selected_entities") or []
            _check(1 <= len(selected) <= 2, "relation did not select one or two entities")
            _check(search_calls <= len(selected), "relation searched beyond selected entities")
            news_count = len(report.get("sources") or [])
            _check(news_count <= 6, "relation saved more than six news items")
            stages = {item.get("stage") for item in latest.get("stage_history") or []}
            _check(not stages & FORBIDDEN_REVIEW_STAGES,
                   "relation entered an old review or research stage")
            _check(latest.get("review") is None, "relation generated an old review")
            _check(not report.get("claim_reviews") and not report.get("research_budget"),
                   "relation report includes legacy research artifacts")
            _write(case_dir / "report.json", report, private_values)
            _write(case_dir / "report.md", report["markdown"], private_values)
            result = {"status": latest["status"], "workflow_version": "relation-lite@1",
                      "report_format": report["report_format"], "report_id": report["id"],
                      "selected_entities": selected, "search_calls": search_calls,
                      "fetch_calls": fetch_calls, "news_count": news_count,
                      "skill_hash": hashes["explore-relation"]}
        _write(case_dir / "run.json", latest, private_values)
        result["stages"] = [item.get("stage") for item in latest.get("stage_history") or []]
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        result["ok"] = True
        return result
    except Exception:
        # Preserve the latest durable run, including its stage history and
        # retrieval trace, even when an assertion or external service fails.
        if run_id is not None:
            try:
                latest = _get_run(client, run_id)
                _write(case_dir / "run.json", latest, private_values)
                if isinstance(latest.get("report"), dict):
                    _write(case_dir / "report.json", latest["report"], private_values)
                    if latest["report"].get("markdown"):
                        _write(case_dir / "report.md", latest["report"]["markdown"], private_values)
                if latest.get("status") in ACTIVE:
                    # Retain the pre-cancellation trace above, then release the
                    # worker so a later case can run in the same temporary app.
                    cancelled = _api(client, "POST", f"/explore/runs/{run_id}/cancel", body={})["run"]
                    _write(case_dir / "run_cancelled.json", cancelled, private_values)
            except Exception:
                pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True,
                        help="Directory for the summary and isolated run/report artifacts")
    parser.add_argument("--timeout", type=float, default=600,
                        help="Maximum wall time per case, 20 to 600 seconds (default: 600)")
    parser.add_argument("--cases", default="event_understood,event_explain_more,relation",
                        help="Comma-separated cases to run, in the given order (default: all three)")
    parser.add_argument("--event-question", default=EVENT_QUESTION,
                        help="Public question for both event cases")
    parser.add_argument("--relation-question", default=RELATION_QUESTION,
                        help="Public question for the relation case")
    args = parser.parse_args()
    if not 20 <= args.timeout <= 600:
        parser.error("--timeout must be between 20 and 600 seconds")
    case_defs = {
        "event_understood": ("event", args.event_question, "understood"),
        "event_explain_more": ("event", args.event_question, "explain_more"),
        "relation": ("relation", args.relation_question, None),
    }
    requested = [item.strip() for item in args.cases.split(",")]
    if not requested or any(item not in case_defs for item in requested) or len(requested) != len(set(requested)):
        parser.error("--cases must contain unique names from event_understood,event_explain_more,relation")
    if not args.event_question.strip() or not args.relation_question.strip():
        parser.error("case questions must not be empty")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from backend.app import create_app
    from backend.app.config import Config

    config = {key: getattr(Config, key) for key in dir(Config) if key.isupper()}
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.output, 0o700)
    private_values = _private_values(config)
    summary = {"started_at": datetime.now(timezone.utc).isoformat(),
               "model": config.get("GITEE_AI_MODEL"), "real_model": True,
               "real_search": True, "isolated_account_and_data": True,
               "timeout_seconds_per_case": args.timeout,
               "questions": {name: case_defs[name][1] for name in requested},
               "actual_news_report_verified": {}, "cases": {}, "ok": False}
    summary_path = args.output / "summary.json"
    _write(summary_path, summary, private_values)
    if not config.get("GITEE_AI_API_KEY"):
        summary["error"] = "Gitee model/search credential is not configured"
        _write(summary_path, summary, private_values)
        print(summary["error"], file=sys.stderr, flush=True)
        return 2

    with tempfile.TemporaryDirectory(prefix="explore-lite-live-") as temporary:
        app = None
        try:
            app = create_app({"TESTING": True, "DATA_DIR": temporary,
                              "AGENT_RUNTIME_DIR": str(Path(temporary) / "agent-runtime"),
                              "IMPORT_LEGACY_HISTORY": False,
                              "EXPLORE_EVENT_LITE_ENABLED": True,
                              "EXPLORE_RELATION_LITE_ENABLED": True,
                              "EXPLORE_RUN_TIMEOUT_SECONDS": args.timeout})
            service = app.extensions["explore_service"]
            search = service.search_tools.get("web_search")
            _check(search is not None and search.available(), "real web_search is unavailable")
            hashes = _skill_hashes(service)
            client = app.test_client()
            _api(client, "POST", "/register", body={
                "name": "Explore lite acceptance",
                "email": f"explore-lite-{secrets.token_hex(12)}@example.invalid",
                "password": secrets.token_urlsafe(32)})
            for name in requested:
                module, question, choice = case_defs[name]
                try:
                    result = _case(client, name, module, question, choice,
                                   args.output / name, args.timeout, hashes, private_values)
                except Exception as exc:
                    message = _safe(f"{type(exc).__name__}: {exc}", private_values)
                    result = {"ok": False, "error": message}
                    print(f"{name}: FAILED — {message}", file=sys.stderr, flush=True)
                else:
                    print(f"{name}: passed ({result['status']})", flush=True)
                result["question"] = question
                if name != "event_understood":
                    verified = bool(result.get("status") == "succeeded" and result.get("news_count", 0) > 0)
                    result["actual_news_report_verified"] = verified
                    summary["actual_news_report_verified"][name] = verified
                summary["cases"][name] = result
                _write(summary_path, summary, private_values)
            summary["ok"] = all(item.get("ok") for item in summary["cases"].values())
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            _write(summary_path, summary, private_values)
            return 0 if summary["ok"] else 1
        except Exception as exc:
            summary["error"] = _safe(f"{type(exc).__name__}: {exc}", private_values)
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            _write(summary_path, summary, private_values)
            print(f"preflight: FAILED — {summary['error']}", file=sys.stderr, flush=True)
            return 1
        finally:
            if app is not None:
                # Closing Explore first stops background model/tool workers before
                # the temporary SQLite databases are removed.
                app.extensions["explore_service"].close()
                app.extensions["workbench_service"].close()


if __name__ == "__main__":
    raise SystemExit(main())
