"""Isolated local site for real-model home-composer App creation acceptance.

No formal account or financial database is writable here. Run this fixture only
when an operator explicitly requests real-model acceptance; it spends upstream
model requests and stores a disposable account database under the supplied dir.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values
from flask import jsonify, request
from werkzeug.serving import make_server

from backend.app import create_app


PORT = 49025
EMAILS = (
    "home-creation-holdings@example.invalid",
    "home-creation-orders@example.invalid",
)
PASSWORD = "home-creation-isolated-fixture"


def main() -> None:
    target = os.environ.get("HOME_CREATION_ACCEPTANCE_DIR")
    if not target:
        raise SystemExit("HOME_CREATION_ACCEPTANCE_DIR is required")
    folder = Path(target).expanduser().resolve()
    parent = (ROOT / "data" / "integration-acceptance").resolve()
    if folder == parent or parent not in folder.parents:
        raise SystemExit("Fixture data must be inside data/integration-acceptance")
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    data_dir = folder / "isolated-data"
    data_dir.mkdir(mode=0o700)

    configured = dotenv_values(ROOT / ".env")
    key = os.environ.get("GITEE_AI_API_KEY") or configured.get("GITEE_AI_API_KEY")
    if not key:
        raise SystemExit("Real GLM credential unavailable")
    runtime = ROOT / "data" / "agent-runtime"
    if not runtime.is_dir():
        raise SystemExit("Read-only AGENT_RUNTIME_DIR unavailable")
    base_url = os.environ.get("GITEE_AI_BASE_URL") or configured.get("GITEE_AI_BASE_URL") or "https://ai.gitee.com/v1"

    app = create_app({
        "TESTING": True,
        "DATA_DIR": str(data_dir),
        "DESKTOP_PORT": PORT,
        "FRONTEND_DIST": str(ROOT / "frontend" / "dist"),
        "IMPORT_LEGACY_HISTORY": False,
        "CAPABILITY_SKIP_PUBLIC_SEED": True,
        "AGENT_RUNTIME_DIR": str(runtime),
        "GITEE_AI_API_KEY": key,
        "GITEE_AI_BASE_URL": base_url,
        "GITEE_AI_MODEL": "GLM-5.3-Flash",
        "AGENT_MODEL_MAX_TOKENS": 12288,
        "GITEE_AI_READ_TIMEOUT_SECONDS": 180,
        "SUB_AGENT_TIMEOUT": 420,
        "CD80_READ_API_KEY": "",
        "CD80_ADMIN_API_KEY": "",
        "SFC_TOKEN": "",
        "SFC_KEY_FILE": "",
        "CAPABILITY_MCP_POLICY_FILE": "",
    })
    workbench = app.extensions["workbench_service"]
    for email in EMAILS:
        workbench.auth.register(email, PASSWORD, "首页创建模式验收")
    writes: list[dict] = []

    @app.after_request
    def trace_writes(response):
        if request.method != "GET" and request.path.startswith("/desktop/api/"):
            writes.append({"method": request.method, "path": request.path, "status": response.status_code})
        return response

    @app.get("/__home_creation_fixture__/state")
    def fixture_state():
        return jsonify({"realModel": True, "model": "GLM-5.3-Flash", "accounts": EMAILS,
                        "fixturePassword": PASSWORD, "writes": writes})

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = make_server("127.0.0.1", PORT, app, threaded=True)
    print(json.dumps({"ready": True, "port": PORT, "realModel": True, "isolatedDir": str(folder)}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workbench.close()


if __name__ == "__main__":
    main()
