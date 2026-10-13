"""API contract tests using a small, disposable SQLite database only."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


TABLE = "cd80_sample"
BASE = f"/api/v1/tables/{TABLE}"
ADMIN_KEY = "test-admin-key"
READ_KEY = "test-read-key"


@pytest.fixture
def sample_db(tmp_path: Path) -> Path:
    path = tmp_path / "sample.sqlite3"
    with sqlite3.connect(path) as db:
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
                PRIMARY KEY (table_name, name)
            );
            CREATE TABLE cd80_sample (
                _row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT,
                trade_date TEXT,
                price REAL,
                volume INTEGER CHECK (volume >= 0),
                note TEXT,
                business_key TEXT
            );
            CREATE INDEX idx_sample_code_date ON cd80_sample(code, trade_date);
            CREATE TRIGGER sample_insert AFTER INSERT ON cd80_sample
            BEGIN
                UPDATE _datasets SET row_count = row_count + 1 WHERE name = 'cd80_sample';
            END;
            CREATE TRIGGER sample_delete AFTER DELETE ON cd80_sample
            BEGIN
                UPDATE _datasets SET row_count = row_count - 1 WHERE name = 'cd80_sample';
            END;
            INSERT INTO _datasets VALUES
                ('cd80_sample', 'Sample', 0, 4, 'sample.csv', 'test-sha256',
                 'utf-8', '2026-10-13T00:00:00Z');
            """
        )
        db.executemany(
            "INSERT INTO _columns VALUES (?, ?, ?, ?, ?, ?)",
            [
                (TABLE, "code", 0, "TEXT", "identifier", "证券代码"),
                (TABLE, "trade_date", 1, "TEXT", "date", "交易日期"),
                (TABLE, "price", 2, "REAL", "number", "收盘价"),
                (TABLE, "volume", 3, "INTEGER", "number", "成交量"),
                (TABLE, "note", 4, "TEXT", "text", "备注"),
                (TABLE, "business_key", 5, "TEXT", "identifier", "业务键"),
            ],
        )
        db.executemany(
            """INSERT INTO cd80_sample
               (code, trade_date, price, volume, note, business_key)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                ("000001", "2026-09-30", 12.5, 100, "alpha", "dup"),
                ("000001", "2026-09-30", 12.6, 120, "beta", "dup"),
                ("300750", "2026-09-29", 20.0, None, None, "single"),
                ("000002", "2026/10/01", -1.0, 0, "gamma", "other"),
            ],
        )
    return path


@pytest.fixture
def make_client(sample_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CD80_DB_PATH", str(sample_db))
    monkeypatch.setenv("CD80_ADMIN_API_KEY", ADMIN_KEY)
    monkeypatch.delenv("CD80_READ_API_KEY", raising=False)
    monkeypatch.setenv("CD80_CORS_ORIGINS", "http://localhost:5173")

    def factory(*, admin_key: str | None = ADMIN_KEY,
                read_key: str | None = None) -> TestClient:
        if admin_key is None:
            monkeypatch.delenv("CD80_ADMIN_API_KEY", raising=False)
        else:
            monkeypatch.setenv("CD80_ADMIN_API_KEY", admin_key)
        if read_key is None:
            monkeypatch.delenv("CD80_READ_API_KEY", raising=False)
        else:
            monkeypatch.setenv("CD80_READ_API_KEY", read_key)
        from app.main import create_app

        return TestClient(create_app())

    return factory


def count_rows(path: Path) -> tuple[int, int, int]:
    with sqlite3.connect(path) as db:
        actual = db.execute("SELECT COUNT(*) FROM cd80_sample").fetchone()[0]
        registered, source = db.execute(
            "SELECT row_count, source_rows FROM _datasets WHERE name = ?", (TABLE,)
        ).fetchone()
    return actual, registered, source


def fail_4xx(response):
    assert 400 <= response.status_code < 500, response.text


def test_health_catalog_schema_and_preserved_source_values(make_client):
    with make_client() as client:
        assert client.get("/health").status_code == 200
        catalog = client.get("/api/v1/tables")
        assert catalog.status_code == 200, catalog.text
        assert TABLE in catalog.text
        assert "4" in catalog.text
        schema = client.get(f"{BASE}/schema")
        assert schema.status_code == 200, schema.text
        for field in ("code", "trade_date", "price", "volume", "note"):
            assert field in schema.text
        assert "idx_sample_code_date" in schema.text
        row = client.get(f"{BASE}/rows/1")
        assert row.status_code == 200, row.text
        assert row.json()["data"]["code"] == "000001"
        assert row.json()["data"]["trade_date"] == "2026-09-30"
        assert row.json()["data"]["_row_id"] == 1
        assert client.get(f"{BASE}/rows/2").json()["data"]["business_key"] == "dup"


def test_pagination_cursor_total_projection_filter_sort_and_post_query(make_client):
    with make_client() as client:
        first = client.get(f"{BASE}/rows", params={"limit": 2})
        assert first.status_code == 200, first.text
        assert [r["_row_id"] for r in first.json()["data"]] == [1, 2]
        assert first.json()["pagination"]["has_more"] is True
        assert first.json()["pagination"]["next_after_id"] == 2
        assert first.json()["pagination"]["total"] is None
        second = client.get(f"{BASE}/rows", params={"limit": 2, "offset": 2})
        assert [r["_row_id"] for r in second.json()["data"]] == [3, 4]
        assert second.json()["pagination"]["has_more"] is False
        cursor = client.get(f"{BASE}/rows", params={"limit": 2, "after_id": 2})
        assert cursor.status_code == 200, cursor.text
        assert [r["_row_id"] for r in cursor.json()["data"]] == [3, 4]
        assert cursor.json()["pagination"]["next_after_id"] is None
        total = client.get(f"{BASE}/rows", params={"include_total": "true"})
        assert total.json()["pagination"]["total"] == 4

        query = {
            "limit": 2,
            "sort_by": "price",
            "order": "desc",
            "columns": "code,price",
            "filters": [{"field": "code", "op": "eq", "value": "000001"}],
            "include_total": True,
        }
        get_result = client.get(
            f"{BASE}/rows",
            params={**{k: v for k, v in query.items() if k != "filters"},
                    "filters": json.dumps(query["filters"])},
        )
        post_result = client.post(f"{BASE}/query", json=query)
        assert get_result.status_code == 200, get_result.text
        assert post_result.status_code == 200, post_result.text
        for result in (get_result, post_result):
            assert [r["price"] for r in result.json()["data"]] == [12.6, 12.5]
            assert result.json()["pagination"]["total"] == 2
            assert all(set(r) <= {"_row_id", "code", "price"} for r in result.json()["data"])


@pytest.mark.parametrize(
    ("filter_expr", "expected"),
    [
        ({"field": "price", "op": "gt", "value": 12.5}, [2, 3]),
        ({"field": "price", "op": "gte", "value": 20}, [3]),
        ({"field": "price", "op": "lt", "value": 0}, [4]),
        ({"field": "volume", "op": "lte", "value": 100}, [1, 4]),
        ({"field": "code", "op": "ne", "value": "000001"}, [3, 4]),
        ({"field": "code", "op": "in", "value": ["000001", "300750"]}, [1, 2, 3]),
        ({"field": "note", "op": "contains", "value": "a"}, [1, 2, 4]),
        ({"field": "note", "op": "isnull", "value": True}, [3]),
    ],
)
def test_filter_operations(make_client, filter_expr, expected):
    with make_client() as client:
        response = client.get(
            f"{BASE}/rows", params={"filters": json.dumps([filter_expr])}
        )
        assert response.status_code == 200, response.text
        assert [r["_row_id"] for r in response.json()["data"]] == expected


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 1001},
        {"offset": -1},
        {"sort_by": "nonexistent"},
        {"sort_by": "price; DROP TABLE cd80_sample"},
        {"columns": "code,not_a_column"},
        {"columns": "code,(SELECT 1)"},
        {"order": "asc; DROP TABLE cd80_sample"},
        {"after_id": 1, "offset": 1},
        {"after_id": 1, "sort_by": "price"},
        {"after_id": 1, "order": "desc"},
        {"filters": json.dumps([{"field": "unknown", "op": "eq", "value": 1}])},
        {"filters": json.dumps([{"field": "code", "op": "unsafe", "value": 1}])},
        {"filters": "not-json"},
    ],
)
def test_bad_query_rejected_without_sql_execution(make_client, sample_db, params):
    with make_client() as client:
        fail_4xx(client.get(f"{BASE}/rows", params=params))
    assert count_rows(sample_db) == (4, 4, 4)


def test_filter_value_is_parameterized(make_client, sample_db):
    with make_client() as client:
        payload = [{"field": "code", "op": "eq", "value": "x' OR 1=1 --"}]
        response = client.get(f"{BASE}/rows", params={"filters": json.dumps(payload)})
        assert response.status_code == 200, response.text
        assert response.json()["data"] == []
    assert count_rows(sample_db) == (4, 4, 4)


def test_unknown_and_internal_tables_and_rows_return_404(make_client):
    with make_client() as client:
        for table in ("unknown", "_datasets", "cd80_sample;DROP TABLE cd80_sample"):
            fail_4xx(client.get(f"/api/v1/tables/{table}/schema"))
            fail_4xx(client.get(f"/api/v1/tables/{table}/rows"))
        assert client.get(f"{BASE}/rows/99999").status_code == 404
        assert client.patch(
            f"{BASE}/rows/99999",
            json={"data": {"note": "x"}},
            headers={"X-API-Key": ADMIN_KEY},
        ).status_code == 404
        assert client.delete(
            f"{BASE}/rows/99999", headers={"X-API-Key": ADMIN_KEY}
        ).status_code == 404


def test_write_auth_disabled_wrong_key_and_read_key(make_client, sample_db):
    payload = {"data": {"code": "000003"}}
    with make_client(admin_key=None) as client:
        fail_4xx(client.post(f"{BASE}/rows", json=payload))
        fail_4xx(client.post(
            f"{BASE}/rows", json=payload, headers={"X-API-Key": ADMIN_KEY}
        ))
    with make_client(admin_key=ADMIN_KEY) as client:
        fail_4xx(client.post(f"{BASE}/rows", json=payload))
        fail_4xx(client.post(
            f"{BASE}/rows", json=payload, headers={"X-API-Key": "wrong"}
        ))
    assert count_rows(sample_db) == (4, 4, 4)

    with make_client(admin_key=ADMIN_KEY, read_key=READ_KEY) as client:
        for path in ("/api/v1/tables", f"{BASE}/schema", f"{BASE}/rows",
                     f"{BASE}/rows/1"):
            fail_4xx(client.get(path))
            assert client.get(path, headers={"X-API-Key": READ_KEY}).status_code == 200
            assert client.get(path, headers={"X-API-Key": ADMIN_KEY}).status_code == 200
        fail_4xx(client.post(f"{BASE}/query", json={}))
        assert client.post(
            f"{BASE}/query", json={}, headers={"X-API-Key": READ_KEY}
        ).status_code == 200
        fail_4xx(client.post(
            f"{BASE}/rows", json=payload, headers={"X-API-Key": READ_KEY}
        ))
    assert count_rows(sample_db) == (4, 4, 4)


def test_crud_persists_and_row_count_tracks_changes(make_client, sample_db):
    key = {"X-API-Key": ADMIN_KEY}
    with make_client() as client:
        created = client.post(
            f"{BASE}/rows",
            json={"data": {"code": "000009", "trade_date": "2026/10/13",
                           "price": 1.25, "volume": 0, "note": "new",
                           "business_key": "dup"}},
            headers=key,
        )
        assert created.status_code == 201, created.text
        data = created.json()["data"]
        new_id = data["_row_id"]
        assert data["code"] == "000009"
        assert data["trade_date"] == "2026/10/13"
        assert count_rows(sample_db) == (5, 5, 4)
        updated = client.patch(
            f"{BASE}/rows/{new_id}",
            json={"data": {"note": "changed", "volume": 7}},
            headers=key,
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["note"] == "changed"
        assert count_rows(sample_db) == (5, 5, 4)
        assert client.get(f"{BASE}/rows/{new_id}").json()["data"]["volume"] == 7
        deleted = client.delete(f"{BASE}/rows/{new_id}", headers=key)
        assert deleted.status_code == 204, deleted.text
        assert client.get(f"{BASE}/rows/{new_id}").status_code == 404
        assert count_rows(sample_db) == (4, 4, 4)

    # A fresh app and a separate connection both observe the committed result.
    with make_client() as client:
        assert client.get(f"{BASE}/rows", params={"include_total": "true"}).json()[
            "pagination"
        ]["total"] == 4


def test_batch_is_atomic_and_rejects_bad_rows(make_client, sample_db):
    key = {"X-API-Key": ADMIN_KEY}
    with make_client() as client:
        bad_batch = client.post(
            f"{BASE}/rows/batch",
            json={"rows": [{"code": "000010"}, {"not_a_column": "bad"}]},
            headers=key,
        )
        fail_4xx(bad_batch)
        assert count_rows(sample_db) == (4, 4, 4)
        constraint_batch = client.post(
            f"{BASE}/rows/batch",
            json={"rows": [{"code": "000010", "volume": 2},
                           {"code": "000011", "volume": -1}]},
            headers=key,
        )
        fail_4xx(constraint_batch)
        assert count_rows(sample_db) == (4, 4, 4)
        good_batch = client.post(
            f"{BASE}/rows/batch",
            json={"rows": [{"code": "000010", "price": 1.0},
                           {"code": "000011", "price": 2.0}]},
            headers=key,
        )
        assert good_batch.status_code == 201, good_batch.text
        assert good_batch.json()["inserted"] == 2
        assert [r["code"] for r in good_batch.json()["data"]] == ["000010", "000011"]
        assert count_rows(sample_db) == (6, 6, 4)
        too_many = client.post(
            f"{BASE}/rows/batch", json={"rows": [{}] * 1001}, headers=key
        )
        fail_4xx(too_many)
        assert count_rows(sample_db) == (6, 6, 4)


@pytest.mark.parametrize(
    "endpoint,payload",
    [
        ("rows", {"data": {"_row_id": 900}}),
        ("rows", {"data": {"not_a_column": "x"}}),
        ("rows", {"data": {"price": "NaN"}}),
        ("rows", {"data": {"price": "Infinity"}}),
        ("rows", {"data": {"volume": "1.5"}}),
        ("rows/batch", {"rows": [{"_row_id": 900}]}),
    ],
)
def test_invalid_write_values_are_rejected(make_client, sample_db, endpoint, payload):
    with make_client() as client:
        fail_4xx(client.post(
            f"{BASE}/{endpoint}", json=payload, headers={"X-API-Key": ADMIN_KEY}
        ))
    assert count_rows(sample_db) == (4, 4, 4)


def test_row_id_is_read_only_on_patch(make_client, sample_db):
    with make_client() as client:
        fail_4xx(client.patch(
            f"{BASE}/rows/1", json={"data": {"_row_id": 900}},
            headers={"X-API-Key": ADMIN_KEY},
        ))
        assert client.get(f"{BASE}/rows/1").json()["data"]["_row_id"] == 1
    assert count_rows(sample_db) == (4, 4, 4)


def test_cors_allows_configured_origin_only(make_client):
    with make_client() as client:
        allowed = client.options(
            f"{BASE}/rows",
            headers={"Origin": "http://localhost:5173",
                     "Access-Control-Request-Method": "GET"},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
        other = client.options(
            f"{BASE}/rows",
            headers={"Origin": "https://untrusted.example",
                     "Access-Control-Request-Method": "GET"},
        )
        assert other.headers.get("access-control-allow-origin") != "https://untrusted.example"
