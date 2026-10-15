"""Verify the real HTTP/news path using disposable accounts, never real users."""

import argparse
import errno
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import sys
import tempfile
import threading

SITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE_ROOT))

import requests
from waitress import create_server
from backend.app import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save public metadata only; no cookies or credentials.")
    args = parser.parse_args()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "site": str(SITE_ROOT), "checks": []}
    with tempfile.TemporaryDirectory(prefix="cd80-news-live-") as temporary:
        app = create_app({"DATA_DIR": temporary, "GITEE_AI_API_KEY": "", "IMPORT_LEGACY_HISTORY": False,
                          "AGENT_RUNTIME_DIR": str(Path(temporary) / "runtime"),
                          "NEWS_CACHE_TTL_SECONDS": 60, "NEWS_FETCH_TIMEOUT_SECONDS": 15,
                          "NEWS_MAX_STALE_SECONDS": 900, "NEWS_RETRY_SECONDS": 30})
        server = create_server(app, host="127.0.0.1", port=0, threads=4)
        app.config["DESKTOP_PORT"] = server.effective_port
        closing = threading.Event()

        def serve():
            try:
                server.run()
            except OSError as error:
                if not (closing.is_set() and error.errno == errno.EBADF):
                    raise

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.effective_port}/desktop/api"
        session = requests.Session()
        session.trust_env = False
        try:
            assert session.get(base + "/news", timeout=5).status_code == 401
            response = session.post(base + "/register", json={"name": "News validation",
                                    "email": "news-live@example.invalid", "password": secrets.token_urlsafe(24)}, timeout=5)
            assert response.status_code == 200, "Disposable login failed"
            sources = session.get(base + "/news/sources", timeout=5)
            assert sources.status_code == 200 and len(sources.json()["sources"]) == 2
            for source in ("eastmoney", "cls"):
                response = session.get(base + "/news", params={"source": source, "limit": 3}, timeout=25)
                body = response.json()
                if response.status_code != 200:
                    raise RuntimeError(f"{source}: HTTP {response.status_code}, {body.get('code', 'unknown')}")
                assert len(body["items"]) == 3 and body["pagination"]["total"] >= 3
                assert body["cache"]["hit"] is False and body["cache"]["stale"] is False
                assert body["latest_published_at"].endswith("+08:00")
                again = session.get(base + "/news", params={"source": source, "limit": 1, "offset": 1}, timeout=5).json()
                assert again["cache"]["hit"] is True
                assert again["cache"]["fetched_at"] == body["cache"]["fetched_at"]
                assert again["items"][0]["id"] == body["items"][1]["id"]
                search = session.get(base + "/news", params={"source": source, "q": body["items"][0]["title"][:10]}, timeout=5).json()
                assert search["pagination"]["total"] >= 1 and search["cache"]["hit"] is True
                report["checks"].append({"source": source, "status": response.status_code,
                                          "rows_in_window": body["pagination"]["total"],
                                          "latest_published_at": body["latest_published_at"],
                                          "fetched_at": body["cache"]["fetched_at"],
                                          "cache_reused": True, "pagination_and_search": True})
            assert session.get(base + "/news?limit=101", timeout=5).status_code == 400
            assert session.post(base + "/news", timeout=5).status_code == 405
            report.update(ok=True, anonymous_status=401, invalid_limit_status=400, write_status=405)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            session.close()
            closing.set()
            server.close()
            thread.join(timeout=3)
            server.task_dispatcher.shutdown()
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    main()
