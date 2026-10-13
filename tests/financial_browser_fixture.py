"""Loopback-only browser fixture for the financial data management page.

From the desktop website directory, run::

    cd80-data-api/.venv/bin/python tests/financial_browser_fixture.py

The fixture reads the real database through SQLite's read-only URI, copies at
most 65 rows from each of its 37 catalogued tables into a temporary database,
and starts the real FastAPI and Flask applications against that copy. Test
accounts are created in another temporary directory. Nothing loads a .env file.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import quote

import requests
import uvicorn
from flask import jsonify
from werkzeug.serving import make_server


WEBSITE_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = WEBSITE_ROOT / "cd80-data-api"
for _root in (str(WEBSITE_ROOT), str(API_ROOT)):
    if _root not in sys.path:
        sys.path.insert(0, _root)

# This application is always offline for models, including when the launcher
# inherits provider configuration from a developer's shell.
for _name in tuple(os.environ):
    if _name.startswith("GITEE_AI_"):
        os.environ.pop(_name, None)

from app.db import quote_identifier  # noqa: E402
from app.main import create_app as create_data_app  # noqa: E402
from backend.app import create_app as create_website_app  # noqa: E402


ADMIN_EMAIL = "admin@fixture.test"
READER_EMAIL = "reader@fixture.test"
PASSWORD = "fixture-password-123"
ADMIN_KEY = "fixture-local-admin-key"
READ_KEY = "fixture-local-read-key"
TABLE_PATTERN = re.compile(r"cd80_[a-z0-9_]+\Z")


def copy_sample_database(source_path: Path, target_path: Path, rows_per_table: int) -> dict[str, int]:
    """Clone the catalogued schema and bounded rows without writing to source."""
    if rows_per_table < 1 or rows_per_table > 1000:
        raise ValueError("rows-per-table 必须在 1 到 1000 之间")
    if not source_path.is_file():
        raise FileNotFoundError(f"未找到比赛 SQLite 数据库：{source_path}")
    source_uri = "file:" + quote(str(source_path.resolve()), safe="/") + "?mode=ro"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source_uri, uri=True)) as source, closing(
        sqlite3.connect(target_path)
    ) as target:
        source.row_factory = sqlite3.Row
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        catalog = source.execute("SELECT * FROM _datasets ORDER BY name").fetchall()
        names = [row["name"] for row in catalog]
        if not names or any(not TABLE_PATTERN.fullmatch(name) for name in names):
            raise ValueError("源数据库的比赛数据目录不完整")
        objects = ["_datasets", "_columns", *names]
        for name in objects:
            ddl = source.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()
            if ddl is None or not ddl[0]:
                raise ValueError(f"源数据库缺少数据表：{name}")
            target.execute(ddl[0])

        fields = source.execute("SELECT * FROM _columns ORDER BY table_name, ordinal").fetchall()
        target.executemany(
            "INSERT INTO _columns(table_name,name,ordinal,data_type,logical_type,description) "
            "VALUES (?,?,?,?,?,?)",
            [tuple(row) for row in fields],
        )
        counts: dict[str, int] = {}
        sampled_at = datetime.now(timezone.utc).isoformat()
        for dataset in catalog:
            name = dataset["name"]
            rows = source.execute(
                f"SELECT * FROM {quote_identifier(name)} ORDER BY _row_id LIMIT ?",
                (rows_per_table,),
            ).fetchall()
            names_in_row = [part["name"] for part in source.execute(
                f"PRAGMA table_info({quote_identifier(name)})"
            )]
            if not names_in_row or names_in_row[0] != "_row_id":
                raise ValueError(f"源数据表缺少内部主键：{name}")
            if rows:
                columns = ",".join(quote_identifier(column) for column in names_in_row)
                params = ",".join("?" for _ in names_in_row)
                target.executemany(
                    f"INSERT INTO {quote_identifier(name)} ({columns}) VALUES ({params})",
                    [tuple(row) for row in rows],
                )
            count = len(rows)
            counts[name] = count
            sample_hash = hashlib.sha256(
                json.dumps([tuple(row) for row in rows], ensure_ascii=False,
                           separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            target.execute(
                "INSERT INTO _datasets(name,title,row_count,source_rows,source_file,"
                "source_sha256,encoding,imported_at) VALUES (?,?,?,?,?,?,?,?)",
                (name, dataset["title"], count, count,
                 f"fixture://sample/{name}", sample_hash, "fixture", sampled_at),
            )

        # Recreate normal lookup indexes and row-count triggers after loading.
        # This keeps fixture row_count synchronized during UI insert/delete.
        marks = ",".join("?" for _ in objects)
        for item in source.execute(
            f"SELECT sql FROM sqlite_master WHERE type IN ('index','trigger') "
            f"AND tbl_name IN ({marks}) AND sql IS NOT NULL ORDER BY type,name",
            objects,
        ):
            target.execute(item["sql"])
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("临时 SQLite 数据库完整性检查失败")
        target.commit()
    return counts


def _start_data_server(db_path: Path, port: int):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
        with patch.dict(os.environ, {
            "CD80_DB_PATH": str(db_path),
            "CD80_ADMIN_API_KEY": ADMIN_KEY,
            "CD80_READ_API_KEY": READ_KEY,
        }):
            app = create_data_app()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="on",
                           log_level="warning", access_log=False)
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        with requests.Session() as session:
            session.trust_env = False
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if not thread.is_alive():
                    break
                try:
                    response = session.get(f"http://127.0.0.1:{port}/health", timeout=.3)
                    if response.status_code == 200:
                        return listener, server, thread
                except requests.RequestException:
                    pass
                time.sleep(.05)
        raise RuntimeError("临时金融数据服务未在 15 秒内启动")
    except BaseException:
        if "server" in locals():
            server.should_exit = True
        if "thread" in locals():
            thread.join(timeout=5)
        listener.close()
        raise


def _make_website(temporary_root: Path, db_path: Path, api_port: int,
                  website_port: int, frontend_dist: Path):
    app = create_website_app({
        "TESTING": True,
        "DESKTOP_PORT": website_port,
        "DATA_DIR": str(temporary_root / "accounts"),
        "FRONTEND_DIST": str(frontend_dist),
        "AGENT_RUNTIME_DIR": str(temporary_root / "agent-runtime"),
        "GITEE_AI_API_KEY": "",
        "GITEE_AI_BASE_URL": "http://127.0.0.1:1/v1",
        "IMPORT_LEGACY_HISTORY": False,
        "CD80_DATA_API_URL": f"http://127.0.0.1:{api_port}",
        "CD80_ADMIN_API_KEY": ADMIN_KEY,
        "CD80_READ_API_KEY": READ_KEY,
        "CD80_DATA_ADMIN_USERS": "",
    })
    @app.get("/__financial_fixture__/stats")
    def fixture_stats():
        with closing(sqlite3.connect(db_path)) as db:
            actual = {
                name: db.execute(f"SELECT COUNT(*) FROM {quote_identifier(name)}").fetchone()[0]
                for (name,) in db.execute("SELECT name FROM _datasets ORDER BY name")
            }
            metadata = dict(db.execute("SELECT name,row_count FROM _datasets"))
            audit = [
                {"table": name, "rowId": row_id, "action": action}
                for name, row_id, action in db.execute(
                    "SELECT table_name,row_id,action FROM _audit_log ORDER BY audit_id"
                )
            ]
        response = jsonify({
            "fixture": True,
            "temporaryData": True,
            "realModel": False,
            "rowCounts": actual,
            "metadataRowCounts": metadata,
            "auditActions": audit,
        })
        response.headers["Cache-Control"] = "no-store"
        return response

    for email, name in ((ADMIN_EMAIL, "Fixture Administrator"),
                        (READER_EMAIL, "Fixture Reader")):
        client = app.test_client()
        response = client.post(
            "/desktop/api/register",
            json={"email": email, "password": PASSWORD, "name": name},
        )
        if response.status_code != 200:
            raise RuntimeError(f"测试账号注册失败：{email} ({response.status_code})")
        if email == ADMIN_EMAIL:
            app.config["CD80_DATA_ADMIN_USERS"] = response.json["user"]["id"]

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", type=Path,
                        default=API_ROOT / "data" / "cd80.sqlite3")
    parser.add_argument("--rows-per-table", type=int, default=65)
    parser.add_argument("--api-port", type=int, default=48995)
    parser.add_argument("--website-port", type=int, default=48996)
    parser.add_argument("--frontend-dist", type=Path,
                        default=WEBSITE_ROOT / "frontend" / "dist")
    args = parser.parse_args()
    if args.api_port == args.website_port or not all(
        1 <= port <= 65535 for port in (args.api_port, args.website_port)
    ):
        parser.error("两个端口必须不同，且均在 1 到 65535 范围内")
    if not (args.frontend_dist / "index.html").is_file():
        parser.error("请先构建前端：npm --prefix frontend run build")

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with tempfile.TemporaryDirectory(prefix="cd80-financial-browser-") as tmp:
        root = Path(tmp)
        db_path = root / "sample.sqlite3"
        counts = copy_sample_database(args.source_db, db_path, args.rows_per_table)
        listener = api_server = api_thread = website_server = website = None
        try:
            listener, api_server, api_thread = _start_data_server(db_path, args.api_port)
            website = _make_website(root, db_path, args.api_port,
                                    args.website_port, args.frontend_dist)
            website_server = make_server("127.0.0.1", args.website_port, website, threaded=True)
            print(json.dumps({
                "ready": True,
                "url": f"http://127.0.0.1:{args.website_port}/desktop/",
                "stats": f"http://127.0.0.1:{args.website_port}/__financial_fixture__/stats",
                "apiHealth": f"http://127.0.0.1:{args.api_port}/health",
                "fixture": True,
                "temporaryData": True,
                "realModel": False,
                "tableCount": len(counts),
                "sampleRows": sum(counts.values()),
                "adminEmail": ADMIN_EMAIL,
                "readerEmail": READER_EMAIL,
                "password": PASSWORD,
            }, ensure_ascii=False), flush=True)
            website_server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if website_server is not None:
                website_server.server_close()
            if website is not None:
                website.extensions["workbench_service"].close()
            if api_server is not None:
                api_server.should_exit = True
            if api_thread is not None:
                api_thread.join(timeout=10)
            if listener is not None:
                listener.close()


if __name__ == "__main__":
    main()
