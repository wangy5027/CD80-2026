"""Bounds that keep malformed requests from becoming SQLite/server errors."""

import asyncio
import sqlite3

from fastapi.testclient import TestClient

from app.body_limit import JsonBodyLimitMiddleware, MAX_JSON_BODY_BYTES
from app.main import create_app


def _test_client(tmp_path, monkeypatch):
    db_path = tmp_path / "limits.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.executescript(
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
                PRIMARY KEY(table_name, name)
            );
            CREATE TABLE cd80_sample (
                _row_id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT
            );
            INSERT INTO _datasets VALUES
                ('cd80_sample', 'Sample', 0, 0, 'sample.csv', 'hash',
                 'utf-8', '2026-10-13T00:00:00Z');
            INSERT INTO _columns VALUES
                ('cd80_sample', 'code', 0, 'TEXT', 'identifier', '代码');
            """
        )
    monkeypatch.setenv("CD80_DB_PATH", str(db_path))
    monkeypatch.setenv("CD80_ADMIN_API_KEY", "test-key")
    monkeypatch.delenv("CD80_READ_API_KEY", raising=False)
    return TestClient(create_app())


def test_row_id_above_sqlite_signed_int_max_returns_422(tmp_path, monkeypatch):
    path = "/api/v1/tables/cd80_sample/rows/9223372036854775808"
    with _test_client(tmp_path, monkeypatch) as client:
        assert client.get(path).status_code == 422
        assert client.patch(
            path, headers={"X-API-Key": "test-key"},
            json={"data": {"code": "x"}},
        ).status_code == 422
        assert client.delete(path, headers={"X-API-Key": "test-key"}).status_code == 422


def test_content_length_over_2_mib_returns_413(tmp_path, monkeypatch):
    with _test_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/tables/cd80_sample/query",
            content=b"x" * (MAX_JSON_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413


def test_streamed_body_without_content_length_is_limited():
    called = False

    async def endpoint(scope, receive, send):
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = JsonBodyLimitMiddleware(endpoint)
    scope = {
        "type": "http", "method": "POST", "path": "/api/v1/query",
        "headers": [(b"content-type", b"application/json")],
    }
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * MAX_JSON_BODY_BYTES, "more_body": True},
            {"type": "http.request", "body": b"x", "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 413
    assert not called


def test_streamed_body_at_exact_2_mib_is_accepted():
    consumed = bytearray()

    async def endpoint(scope, receive, send):
        while True:
            message = await receive()
            consumed.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = JsonBodyLimitMiddleware(endpoint)
    scope = {"type": "http", "method": "POST", "path": "/", "headers": []}
    messages = iter(
        [
            {"type": "http.request", "body": b"x" * MAX_JSON_BODY_BYTES, "more_body": False},
        ]
    )
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    asyncio.run(middleware(scope, receive, send))
    assert sent[0]["status"] == 200
    assert len(consumed) == MAX_JSON_BODY_BYTES
