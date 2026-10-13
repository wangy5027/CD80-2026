"""Regression for FastAPI moving a request's SQLite connection across workers."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

from fastapi.testclient import TestClient

from app.db import open_connection
from app.main import create_app


def _make_db(tmp_path):
    db_path = tmp_path / "threads.sqlite3"
    with sqlite3.connect(db_path) as db:
        db.executescript(
            """
            CREATE TABLE _datasets (
                name TEXT PRIMARY KEY, title TEXT NOT NULL,
                row_count INTEGER NOT NULL, source_rows INTEGER NOT NULL,
                source_file TEXT NOT NULL, source_sha256 TEXT NOT NULL,
                encoding TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE _columns (
                table_name TEXT NOT NULL, name TEXT NOT NULL,
                ordinal INTEGER NOT NULL, data_type TEXT NOT NULL,
                logical_type TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(table_name,name)
            );
            CREATE TABLE cd80_sample (
                _row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL
            );
            INSERT INTO cd80_sample(code) VALUES('000001');
            INSERT INTO _datasets VALUES
                ('cd80_sample','Thread sample',1,1,'sample.csv','hash','utf-8','now');
            INSERT INTO _columns VALUES
                ('cd80_sample','code',0,'TEXT','identifier','证券代码');
            """
        )
    return db_path


def test_connection_can_be_created_used_and_closed_on_distinct_threads(tmp_path):
    db_path = _make_db(tmp_path)

    # Separate one-worker pools keep all three distinct worker threads alive,
    # making this deterministic rather than relying on a scheduler race.
    with (
        ThreadPoolExecutor(max_workers=1) as creator,
        ThreadPoolExecutor(max_workers=1) as user,
        ThreadPoolExecutor(max_workers=1) as closer,
    ):
        creator_id, connection = creator.submit(
            lambda: (threading.get_ident(), open_connection(db_path, 5))
        ).result()
        try:
            user_id, value = user.submit(
                lambda: (
                    threading.get_ident(),
                    connection.execute("SELECT code FROM cd80_sample").fetchone()[0],
                )
            ).result()
        finally:
            closer_id = closer.submit(
                lambda: (connection.close(), threading.get_ident())[1]
            ).result()

    assert len({creator_id, user_id, closer_id}) == 3
    assert value == "000001"


def test_rapid_concurrent_schema_and_row_requests(tmp_path, monkeypatch):
    db_path = _make_db(tmp_path)
    monkeypatch.setenv("CD80_DB_PATH", str(db_path))
    monkeypatch.delenv("CD80_READ_API_KEY", raising=False)
    monkeypatch.delenv("CD80_ADMIN_API_KEY", raising=False)
    app = create_app()
    paths = (
        "/api/v1/tables",
        "/api/v1/tables/cd80_sample/schema",
        "/api/v1/tables/cd80_sample/rows?limit=1",
        "/api/v1/tables/cd80_sample/rows/1",
    )

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(client.get, paths * 20))

    assert len(responses) == 80
    assert all(response.status_code == 200 for response in responses), [
        (response.status_code, response.text) for response in responses
        if response.status_code != 200
    ]
    assert responses[1].json()["fields"][1]["name"] == "code"
    assert responses[2].json()["data"][0]["code"] == "000001"
