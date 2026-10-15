"""Exercise the real Flask bridge against a disposable live Uvicorn server."""

from __future__ import annotations

import hashlib
import json
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path

import requests
import uvicorn


WEBSITE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WEBSITE_ROOT))

from backend.app import create_app as create_website_app  # noqa: E402
from app.main import create_app as create_data_app  # noqa: E402


PROXY = "/desktop/api/financial-data"
TABLE = "cd80_sample"
ROWS = f"{PROXY}/tables/{TABLE}/rows"
ADMIN_KEY = "integration-admin-only"
READ_KEY = "integration-read-only"


def _make_disposable_db(tmp_path: Path) -> tuple[Path, Path, bytes]:
    source_file = tmp_path / "sample.csv"
    source_bytes = b"code,price,volume\n000001,12.5,100\n000001,12.6,120\n"
    source_file.write_bytes(source_bytes)
    db_path = tmp_path / "integration.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE _datasets (
                name TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                source_rows INTEGER NOT NULL,
                source_file TEXT NOT NULL,
                source_sha256 TEXT NOT NULL,
                encoding TEXT NOT NULL,
                imported_at TEXT NOT NULL
            );
            CREATE TABLE _columns (
                table_name TEXT NOT NULL,
                name TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                data_type TEXT NOT NULL,
                logical_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(table_name,name)
            );
            CREATE TABLE cd80_sample (
                _row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT, price REAL, volume INTEGER
            );
            CREATE INDEX cd80_sample_code_idx ON cd80_sample(code);
            CREATE TRIGGER cd80_sample_insert AFTER INSERT ON cd80_sample
            BEGIN
                UPDATE _datasets SET row_count=row_count+1
                WHERE name='cd80_sample';
            END;
            CREATE TRIGGER cd80_sample_delete AFTER DELETE ON cd80_sample
            BEGIN
                UPDATE _datasets SET row_count=row_count-1
                WHERE name='cd80_sample';
            END;
            INSERT INTO cd80_sample(code,price,volume)
            VALUES ('000001',12.5,100),('000001',12.6,120);
            INSERT INTO _columns VALUES
                ('cd80_sample','code',0,'TEXT','identifier','证券代码'),
                ('cd80_sample','price',1,'REAL','number','价格'),
                ('cd80_sample','volume',2,'INTEGER','number','成交量');
            """
        )
        db.execute(
            "INSERT INTO _datasets VALUES(?,?,?,?,?,?,?,?)",
            (
                TABLE, "联调样本", 2, 2, str(source_file),
                hashlib.sha256(source_bytes).hexdigest(), "utf-8",
                "2026-10-13T00:00:00Z",
            ),
        )
    return db_path, source_file, source_bytes


def test_website_cookie_proxy_and_live_sqlite_crud(tmp_path, monkeypatch):
    db_path, source_file, source_bytes = _make_disposable_db(tmp_path)
    monkeypatch.setenv("CD80_DB_PATH", str(db_path))
    monkeypatch.setenv("CD80_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.setenv("CD80_READ_API_KEY", READ_KEY)

    data_app = create_data_app()
    upstream_calls: list[tuple[str, str | None]] = []

    @data_app.middleware("http")
    async def record_upstream(request, call_next):
        upstream_calls.append((request.url.path, request.headers.get("X-API-Key")))
        return await call_next(request)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(data_app, host="127.0.0.1", port=port,
                       lifespan="on", log_level="critical", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()

    website = None
    try:
        with requests.Session() as health_session:
            health_session.trust_env = False
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    response = health_session.get(
                        f"http://127.0.0.1:{port}/health", timeout=0.3
                    )
                    if response.status_code == 200:
                        break
                except requests.RequestException:
                    time.sleep(0.05)
            else:
                raise AssertionError("临时 Uvicorn 数据服务未在 10 秒内启动")

        website = create_website_app(
            {
                "TESTING": True,
                "DATA_DIR": str(tmp_path / "accounts"),
                "AGENT_RUNTIME_DIR": str(tmp_path / "agent-runtime"),
                "GITEE_AI_API_KEY": "",
                "IMPORT_LEGACY_HISTORY": False,
                "CD80_DATA_API_URL": f"http://127.0.0.1:{port}",
                "CD80_ADMIN_API_KEY": ADMIN_KEY,
                "CD80_READ_API_KEY": READ_KEY,
                "CD80_DATA_ADMIN_USERS": "",
            }
        )
        client = website.test_client()
        anonymous = website.test_client()
        baseline = len(upstream_calls)
        assert anonymous.get(PROXY + "/tables").status_code == 401
        assert len(upstream_calls) == baseline

        registered = client.post(
            "/desktop/api/register",
            json={"email": "integration@example.invalid", "password": "test-only-123",
                  "name": "Integration User"},
        )
        assert registered.status_code == 200, registered.get_data(as_text=True)
        user_id = registered.json["user"]["id"]
        assert client.post("/desktop/api/logout").status_code == 200
        logged_in = client.post(
            "/desktop/api/login",
            json={"email": "integration@example.invalid", "password": "test-only-123"},
        )
        assert logged_in.status_code == 200, logged_in.get_data(as_text=True)
        assert "HttpOnly" in logged_in.headers["Set-Cookie"]
        assert client.get(PROXY + "/capabilities").json == {
            "canRead": True, "canWrite": False,
        }

        catalog = client.get(PROXY + "/tables")
        assert catalog.status_code == 200, catalog.get_data(as_text=True)
        assert catalog.json["data"][0]["name"] == TABLE
        schema = client.get(PROXY + f"/tables/{TABLE}/schema")
        assert schema.status_code == 200
        assert any(field["name"] == "code" for field in schema.json["fields"])

        filters = [{"field": "code", "op": "eq", "value": "000001"}]
        listed = client.get(
            ROWS,
            query_string={"limit": 1, "filters": json.dumps(filters), "include_total": "true"},
            headers={"X-API-Key": "browser-supplied-key-must-be-ignored"},
        )
        assert listed.status_code == 200, listed.get_data(as_text=True)
        assert listed.json["data"][0]["code"] == "000001"
        assert listed.json["pagination"]["total"] == 2
        assert upstream_calls[-1][1] == READ_KEY
        queried = client.post(
            PROXY + f"/tables/{TABLE}/query",
            json={"limit": 2, "filters": filters},
        )
        assert queried.status_code == 200, queried.get_data(as_text=True)
        assert len(queried.json["data"]) == 2
        assert upstream_calls[-1][1] == READ_KEY

        before_denied = len(upstream_calls)
        denied = client.post(ROWS, json={"data": {"code": "000002"}})
        assert denied.status_code == 403
        assert len(upstream_calls) == before_denied

        website.config["CD80_DATA_ADMIN_USERS"] = user_id
        assert client.get(PROXY + "/capabilities").json["canWrite"] is True
        created = client.post(
            ROWS,
            json={"data": {"code": "000002", "price": 15.5, "volume": 1}},
            headers={"X-API-Key": "browser-supplied-key-must-be-ignored"},
        )
        assert created.status_code == 201, created.get_data(as_text=True)
        assert upstream_calls[-1][1] == ADMIN_KEY
        new_id = created.json["data"]["_row_id"]
        updated = client.patch(
            ROWS + f"/{new_id}", json={"data": {"price": 16.0}}
        )
        assert updated.status_code == 200, updated.get_data(as_text=True)
        assert updated.json["data"]["price"] == 16.0
        batch = client.post(
            ROWS + "/batch", json={"rows": [{"code": "000003"}, {"code": "000004"}]}
        )
        assert batch.status_code == 201, batch.get_data(as_text=True)
        assert batch.json["inserted"] == 2
        deleted = client.delete(ROWS + f"/{new_id}")
        assert deleted.status_code == 204
        assert client.get(ROWS + f"/{new_id}").status_code == 404

        before_unknown = len(upstream_calls)
        assert client.get(PROXY + f"/tables/{TABLE}/arbitrary").status_code == 404
        assert len(upstream_calls) == before_unknown

        with sqlite3.connect(db_path) as db:
            actual = db.execute("SELECT COUNT(*) FROM cd80_sample").fetchone()[0]
            row_count, source_rows, source_hash = db.execute(
                "SELECT row_count,source_rows,source_sha256 FROM _datasets WHERE name=?",
                (TABLE,),
            ).fetchone()
            actions = [
                row[0] for row in db.execute("SELECT action FROM _audit_log ORDER BY audit_id")
            ]
        assert (actual, row_count, source_rows) == (4, 4, 2)
        assert actions == ["insert", "update", "insert", "insert", "delete"]
        assert source_file.read_bytes() == source_bytes
        assert source_hash == hashlib.sha256(source_bytes).hexdigest()
    finally:
        if website is not None:
            website.extensions["workbench_service"].close()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive(), "临时 Uvicorn 线程未退出"
