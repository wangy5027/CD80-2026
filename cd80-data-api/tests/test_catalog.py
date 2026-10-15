"""Official catalog behavior against disposable local SQLite fixtures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.catalog import BUSINESS_DATES, _normal_date


READ_KEY = "catalog-read-fixture"
ADMIN_KEY = "catalog-admin-fixture"


@pytest.fixture
def catalog_db(tmp_path: Path) -> Path:
    path = tmp_path / "catalog.sqlite3"
    with sqlite3.connect(path) as db:
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
                PRIMARY KEY (table_name, name)
            );
            CREATE TABLE _unlisted_internal (value TEXT);
            """
        )
        definitions = [
            ("cd80_daily", "日线行情", "trade_date", "交易日期", [
                "20240701", "2026-09-30", "2026/09/29", "NULL", "20261345"
            ]),
            ("cd80_ipo", "新股发行", "ISSPUBDATE", "发行公告日", [
                "NULL", "19841126", "20260930", None
            ]),
            ("cd80_cn_cpi", "CPI 月度", "month", "统计月度", [
                "195112", "202608", "2026-13", "2026-07"
            ]),
            ("cd80_etf_basic", "ETF 基础", "setup_date", "成立日", [
                "20240705.0", "20260827.0"
            ]),
            ("cd80_factor_list", "因子列表", "update_flag", "更新标志", ["20260503"]),
            ("cd80_ci_index_member", "Shenwan industry members", "in_date", "纳入日", [
                "20200101", "20260601"
            ]),
            ("cd80_stock_fin_income", "利润表", "end_date", "报告期末", []),
            ("cd80_fund_nav", "基金净值", "nav_date", "净值日期", [None, "NULL", "20261501"]),
        ]
        for table, title, date_column, label, values in definitions:
            db.execute(
                f'CREATE TABLE "{table}" ('
                '_row_id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, '
                f'"{date_column}" TEXT, DATA_DOWNLOAD_TIME TEXT)'
            )
            db.execute(
                "INSERT INTO _datasets VALUES (?,?,?,?,?,?,?,?)",
                (table, title, len(values), len(values),
                 "C:\\private\\downloads\\" + table + ".csv", "fixture", "utf-8",
                 "2026-10-15T09:00:00Z"),
            )
            db.executemany(
                "INSERT INTO _columns VALUES (?,?,?,?,?,?)",
                [
                    (table, "code", 0, "TEXT", "identifier", "代码"),
                    (table, date_column, 1, "TEXT", "date", label),
                    (table, "DATA_DOWNLOAD_TIME", 2, "TEXT", "date", "下载时间"),
                ],
            )
            db.executemany(
                f'INSERT INTO "{table}" (code,"{date_column}",DATA_DOWNLOAD_TIME) '
                "VALUES (?,?,?)",
                [(f"code-{index}", value, "2099-12-31") for index, value in enumerate(values)],
            )
        # A registered table without a real table is never offered to the UI.
        db.execute(
            "INSERT INTO _datasets VALUES (?,?,?,?,?,?,?,?)",
            ("cd80_daily_basic", "stale", 1, 1, "stale.csv", "fixture", "utf-8", "now"),
        )
        db.execute('CREATE TABLE cd80_bond_cb_dd (_row_id INTEGER PRIMARY KEY, code TEXT)')
        db.execute(
            "INSERT INTO _datasets VALUES (?,?,?,?,?,?,?,?)",
            ("cd80_bond_cb_dd", "bad columns", 1, 1, "bad.csv", "fixture", "utf-8", "now"),
        )
        db.execute(
            "INSERT INTO _columns VALUES (?,?,?,?,?,?)",
            ("cd80_bond_cb_dd", "trade_date", 0, "TEXT", "date", "交易日期"),
        )
    return path


@pytest.fixture
def client(catalog_db: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CD80_DB_PATH", str(catalog_db))
    monkeypatch.setenv("CD80_READ_API_KEY", READ_KEY)
    monkeypatch.setenv("CD80_ADMIN_API_KEY", ADMIN_KEY)
    from app.main import create_app

    with TestClient(create_app()) as app_client:
        yield app_client


def _get(client: TestClient):
    response = client.get("/api/v1/catalog", headers={"X-API-Key": READ_KEY})
    assert response.status_code == 200, response.text
    return {item["name"]: item for item in response.json()["data"]}


def test_business_dates_source_metadata_and_real_column_intersection(client: TestClient):
    items = _get(client)
    assert len(items) == 8
    assert "cd80_daily_basic" not in items
    assert "cd80_bond_cb_dd" not in items
    assert "_unlisted_internal" not in items
    daily = items["cd80_daily"]
    assert daily["provider"] == "Tushare"
    assert daily["storage"] == "local_sqlite"
    assert daily["summary"] == "日线行情"
    assert daily["source_file"] == "cd80_daily.csv"
    assert "private" not in daily["sourceNote"]
    assert daily["dateRange"] == {
        "field": "trade_date", "label": "交易日", "start": "2024-07-01",
        "end": "2026-09-30", "granularity": "day", "status": "available",
    }
    assert {column["name"] for column in daily["primaryColumns"]} == {
        "code", "trade_date", "DATA_DOWNLOAD_TIME"
    }
    assert daily["primaryColumns"][1]["description"] == "交易日期"
    assert items["cd80_ipo"]["dateRange"]["start"] == "1984-11-26"
    assert items["cd80_ipo"]["dateRange"]["end"] == "2026-09-30"
    assert items["cd80_etf_basic"]["dateRange"]["start"] == "2024-07-05"
    assert items["cd80_etf_basic"]["dateRange"]["end"] == "2026-08-27"
    assert items["cd80_cn_cpi"]["dateRange"] == {
        "field": "month", "label": "月度日期", "start": "1951-12",
        "end": "2026-08", "granularity": "month", "status": "available",
    }
    assert items["cd80_factor_list"]["dateRange"]["status"] == "no_date_field"
    assert items["cd80_ci_index_member"]["title"] == "Shenwan industry members"
    assert items["cd80_ci_index_member"]["summary"] == "中信行业分级及成分股，包含纳入和剔除日期。"
    assert items["cd80_ci_index_member"]["dateRange"]["label"] == "成分纳入日"
    assert items["cd80_stock_fin_income"]["dateRange"]["status"] == "empty"
    assert items["cd80_fund_nav"]["dateRange"]["status"] == "no_valid_date"


def test_catalog_read_key_and_original_tables_contract(client: TestClient):
    assert client.get("/api/v1/catalog").status_code == 401
    assert client.get("/api/v1/catalog", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/api/v1/catalog", headers={"X-API-Key": ADMIN_KEY}).status_code == 200
    response = client.get("/api/v1/tables", headers={"X-API-Key": READ_KEY})
    assert response.status_code == 200
    assert response.json()["total"] == 10  # Lightweight legacy list is unchanged.
    assert "dateRange" not in response.json()["data"][0]
    schema = client.get("/openapi.json").json()
    response_schema = schema["paths"]["/api/v1/catalog"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]
    assert response_schema["$ref"] == "#/components/schemas/CatalogResponse"
    assert "dateRange" in schema["components"]["schemas"]["CatalogEntry"]["properties"]


def test_cached_catalog_invalidates_after_same_count_authorized_update(client: TestClient):
    assert _get(client)["cd80_daily"]["dateRange"]["end"] == "2026-09-30"
    response = client.patch(
        "/api/v1/tables/cd80_daily/rows/1",
        headers={"X-API-Key": ADMIN_KEY},
        json={"data": {"trade_date": "20271001"}},
    )
    assert response.status_code == 200, response.text
    assert _get(client)["cd80_daily"]["dateRange"]["end"] == "2027-10-01"


def test_cached_catalog_invalidates_after_external_sqlite_update(
    client: TestClient, catalog_db: Path
):
    assert _get(client)["cd80_daily"]["dateRange"]["end"] == "2026-09-30"
    with sqlite3.connect(catalog_db) as db:
        db.execute("UPDATE cd80_daily SET trade_date='20280101' WHERE _row_id=1")
    assert _get(client)["cd80_daily"]["dateRange"]["end"] == "2028-01-01"


def test_repeated_requests_reuse_catalog_across_closed_request_connections(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from app import catalog

    original = catalog.build_catalog
    builds = 0

    def counted_build(connection, get_table):
        nonlocal builds
        builds += 1
        return original(connection, get_table)

    monkeypatch.setattr(catalog, "build_catalog", counted_build)
    assert _get(client)["cd80_daily"]["dateRange"]["status"] == "available"
    assert _get(client)["cd80_daily"]["dateRange"]["status"] == "available"
    assert builds == 1


def test_commit_during_catalog_snapshot_does_not_cache_stale_range(
    client: TestClient, catalog_db: Path, monkeypatch: pytest.MonkeyPatch
):
    from app import catalog

    original = catalog.build_catalog
    read_done = Event()
    writer_done = Event()

    def pause_after_read(connection, get_table):
        value = original(connection, get_table)
        read_done.set()
        assert writer_done.wait(5)
        return value

    monkeypatch.setattr(catalog, "build_catalog", pause_after_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first_future = pool.submit(_get, client)
        assert read_done.wait(5)
        with sqlite3.connect(catalog_db, timeout=5) as db:
            db.execute("UPDATE cd80_daily SET trade_date='20290101' WHERE _row_id=1")
        writer_done.set()
        first = first_future.result(timeout=5)
    assert first["cd80_daily"]["dateRange"]["end"] == "2026-09-30"
    monkeypatch.setattr(catalog, "build_catalog", original)
    assert _get(client)["cd80_daily"]["dateRange"]["end"] == "2029-01-01"


def test_distinct_value_bound_does_not_claim_partial_range(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("app.catalog.MAX_DISTINCT_DATE_VALUES", 2)
    result = _get(client)["cd80_daily"]["dateRange"]
    assert result["status"] == "too_many_values"
    assert result["start"] is None and result["end"] is None


@pytest.mark.parametrize(
    ("value", "granularity", "expected"),
    [
        ("20260229", "day", None),
        ("20240229", "day", "2024-02-29"),
        ("2026/09/30", "day", "2026-09-30"),
        ("20260930.0", "day", "2026-09-30"),
        ("202608", "month", "2026-08"),
        ("2026-08", "month", "2026-08"),
        ("2026-08", "day", None),
        ("NULL", "day", None),
    ],
)
def test_strict_date_normalization(value: str, granularity: str, expected: str | None):
    assert _normal_date(value, granularity) == expected


def test_date_whitelist_covers_all_official_business_tables():
    assert len(BUSINESS_DATES) == 36
    assert "cd80_factor_list" not in BUSINESS_DATES
    assert BUSINESS_DATES["cd80_stock_basic_calendar"][1] == "日历日期（含休市日）"
