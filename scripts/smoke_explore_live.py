"""Explicit live Explore smoke test using real configured providers and disposable data.

This command spends the existing model quota (intent checks and three analyses). It does not use
existing user accounts or change the normal website database. Only public news,
generated research reports and non-secret verification metadata are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data/explore-acceptance/live")
    parser.add_argument("--screenshot", action="store_true", help="Capture the actual report UI with local Playwright.")
    parser.add_argument("--capture-model-output", action="store_true", help="Save this public-news smoke's model replies for local diagnostics (no credentials).")
    parser.add_argument("--topic", help="Run from a plain research topic instead of supplying a news article.")
    parser.add_argument("--require-web-results", action="store_true", help="Require saved web-search evidence in every module.")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    import requests
    from werkzeug.serving import WSGIRequestHandler, make_server
    from backend.app import create_app
    from backend.app.config import Config

    args.output.mkdir(parents=True, exist_ok=True)
    result = {"checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
              "real_news": True, "real_model": True, "temporary_accounts_and_database": True,
              "model": Config.GITEE_AI_MODEL, "topic": args.topic, "runs": [], "ok": False}
    if not Config.GITEE_AI_API_KEY:
        result.update(blocked="Existing model configuration is missing; no live model call performed.")
        (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
        return 2

    class QuietHandler(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix="ficagent-explore-live-") as temporary:
        app = create_app({"DATA_DIR": temporary, "IMPORT_LEGACY_HISTORY": False,
                          "AGENT_RUNTIME_DIR": str(Path(temporary) / "no-tool-runtime"),
                          "FRONTEND_DIST": str(ROOT / "frontend/dist")})
        if args.capture_model_output:
            underlying = app.extensions["explore_service"].provider
            class ObservedProvider:
                model = underlying.model
                def json_tokens(self, messages, model, stopped, on_response):
                    parts = []
                    payload = json.loads(messages[1]["content"])
                    module, task = payload["module"], payload.get("task", "report")
                    try:
                        for part in underlying.json_tokens(messages, model, stopped, on_response):
                            parts.append(part)
                            yield part
                    finally:
                        (args.output / f"{module}-{task}-model-output.txt").write_text("".join(parts), encoding="utf-8")
                tokens = json_tokens
            app.extensions["explore_service"].provider = ObservedProvider()
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=QuietHandler)
        app.config["DESKTOP_PORT"] = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        base = origin + "/desktop/api"
        client = requests.Session()
        client.trust_env = False
        def api(method, path, **kwargs):
            response = client.request(method, base + path, timeout=30, **kwargs)
            if not response.ok:
                # Never dump provider bodies, request headers or credential material.
                raise RuntimeError(f"{method} {path}: HTTP {response.status_code}")
            return response.json()
        try:
            anonymous = client.get(base + "/explore/sessions", timeout=5)
            assert anonymous.status_code == 401
            api("POST", "/register", json={"name": "Explore 真实接口验收", "email": "explore-live@example.invalid",
                                           "password": secrets.token_urlsafe(24)})
            news = api("GET", "/news?source=eastmoney&limit=10")
            assert news["items"], "Real news endpoint returned no materials."
            item = next((item for item in news["items"] if item.get("url")), news["items"][0])
            result["news"] = {"source": news["source"], "rows_in_window": news["pagination"]["total"],
                              "fetched_at": news["cache"]["fetched_at"], "title": item["title"],
                              "url": item.get("url"), "published_at": item.get("published_at")}
            session = api("POST", "/explore/sessions", json={"title": "真实新闻三模块烟测"})["session"]
            source = None
            material = f"新闻标题：{item['title']}\n新闻原文摘录：{item['summary']}\n来源：{item.get('url') or '东方财富财经快讯'}"
            questions = [
                ("event", "请理解以下真实新闻事件，说明背景、主体、动因、影响路径和不确定性。\n" + material),
                ("relation", "请发现以下新闻事件涉及的主体与关系，说明方向、成立条件与来源。\n" + material),
                ("history", "待验证判断：这类事件对相关产业的影响能否持续，取决于实际传导条件。请用可取得的历史材料检验；无历史材料须明确不足，不用最新新闻冒充历史证据。\n" + material),
            ]
            if args.topic:
                questions = [
                    ("event", f"请联网搜索最近一周关于{args.topic}的重要新闻，解释事件、背景和可能影响，并给出来源。"),
                    ("relation", f"请搜索最近一周关于{args.topic}的新闻，分析相关主体的关系、影响方向和成立条件。"),
                    ("history", f"待验证判断：{args.topic}的政策支持是否会推动产业发展？请搜索真实历史案例对照，不用最新新闻冒充历史证据。"),
                ]
            for module, question in questions:
                start = time.monotonic()
                payload = {"module": module, "question": question, "source_report_id": source,
                           "idempotency_key": secrets.token_hex(16)}
                path = f"/explore/sessions/{session['id']}/runs"
                run = api("POST", path, json=payload)["run"]
                replay = api("POST", path, json=payload)["run"]
                assert replay["id"] == run["id"], "Duplicate submission created a second run."
                print(json.dumps({"module": module, "run_id": run["id"], "status": run["status"]}), flush=True)
                confirmed = False
                clarification_rounds = 0
                reviewed = False
                while run["status"] in {"queued", "running", "awaiting_input", "awaiting_clarification", "awaiting_review"}:
                    if time.monotonic() - start > 720:
                        api("POST", f"/explore/runs/{run['id']}/cancel", json={})
                        raise RuntimeError(f"{module}: smoke deadline exceeded; cancellation requested.")
                    if run["status"] == "awaiting_input":
                        assert module == "history" and not confirmed
                        run = api("POST", f"/explore/runs/{run['id']}/input",
                                  json={"judgment": "这类事件的产业影响能否持续，取决于需求、供给与政策传导条件。"})["run"]
                        confirmed = True
                    elif run["status"] == "awaiting_clarification":
                        assert clarification_rounds < 2, "Question still unclear after two explicit smoke-test choices."
                        options = run["review"]["options"]
                        assert options, "No grounded direction available; manual clarification is required."
                        run = api("POST", f"/explore/runs/{run['id']}/clarify", json={
                            "idempotency_key": secrets.token_hex(16), "revision": run["review"]["revision"],
                            "option_id": options[0]["id"]})["run"]
                        clarification_rounds += 1
                    elif run["status"] == "awaiting_review":
                        assert not reviewed
                        # Compatibility for records created by the earlier review flow.
                        # New clear requests proceed directly without this state.
                        run = api("POST", f"/explore/runs/{run['id']}/confirm", json={
                            "idempotency_key": secrets.token_hex(16), "revision": run["review"]["revision"],
                            "source_ids": [item["id"] for item in run["materials"]]})["run"]
                        reviewed = True
                    else:
                        time.sleep(1)
                        run = api("GET", f"/explore/runs/{run['id']}")["run"]
                record = {"module": module, "run_id": run["id"], "status": run["status"],
                          "elapsed_seconds": round(time.monotonic() - start, 2),
                          "idempotent_replay": True, "judgment_confirmation_used": confirmed,
                          "material_confirmation_used": reviewed, "clarification_rounds": clarification_rounds,
                          "stage_history": run.get("stage_history", []), "error": run.get("error")}
                result["runs"].append(record)
                assert run["status"] in {"succeeded", "partial"}, f"{module} ended {run['status']}"
                report = run["report"]
                assert report and report["sources"]
                source = report["id"]
                fetched = api("GET", f"/explore/reports/{source}")["report"]
                assert fetched["id"] == source
                download = client.get(base + f"/explore/reports/{source}/markdown", timeout=10)
                assert download.status_code == 200 and download.text.strip()
                record.update(report_id=source, source_count=len(report["sources"]),
                              download_status=download.status_code, limitations=report.get("limitations", []),
                              retrieval=report.get("retrieval"),
                              web_source_count=sum(item.get("kind") == "web_search" for item in report["sources"]))
                if args.require_web_results:
                    assert record["web_source_count"] > 0, f"{module}: no saved real web-search evidence"
                    assert any(item.get("status") == "complete" for item in (report.get("retrieval") or {}).get("searches", [])), f"{module}: no successful recorded search"
                (args.output / f"{module}-report.json").write_text(json.dumps(fetched, ensure_ascii=False, indent=2), encoding="utf-8")
                (args.output / f"{module}-report.md").write_text(download.text, encoding="utf-8")
                if args.screenshot:
                    import subprocess
                    capture = ROOT / "scripts/capture_explore_live.mjs"
                    # An ephemeral test-account cookie is passed only through stdin.
                    subprocess.run(["node", str(capture)], input=json.dumps({"origin": origin,
                        "session_id": session["id"], "run_id": run["id"],
                        "cookies": [{"name": c.name, "value": c.value, "url": origin} for c in client.cookies],
                        "output": str((args.output / f"{module}-desktop.png").resolve())}),
                        text=True, check=True, timeout=45)
                print(json.dumps(record, ensure_ascii=False), flush=True)
            restored = api("GET", f"/explore/sessions/{session['id']}")
            assert len(restored["runs"]) == 3
            result.update(ok=True, restored_runs=3, anonymous_status=401)
        except Exception as error:
            result["failure"] = str(error)
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            app.extensions["workbench_service"].close()
            (args.output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": result["ok"], "runs": len(result["runs"]), "report": str(args.output / "result.json"),
                      "failure": result.get("failure")}, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
