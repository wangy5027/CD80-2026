"""Read-only catalog of the currently connected official SQLite tables."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from pathlib import Path, PureWindowsPath
import re
import sqlite3
from threading import Lock
from time import monotonic
from typing import Any, Callable, Literal

from fastapi import HTTPException
from pydantic import BaseModel

from app.db import quote_identifier


# A business date is not interchangeable with an import timestamp or a field
# merely tagged as "date" in source metadata. These choices were checked
# against the 37 official local tables; absent fields are reported honestly.
BUSINESS_DATES: dict[str, tuple[str, str, str]] = {
    "cd80_anns_d": ("announcement_date", "公告日", "day"),
    "cd80_bond_cb_basic": ("list_date", "上市日", "day"),
    "cd80_bond_cb_dd": ("trade_date", "交易日", "day"),
    "cd80_bond_cb_redeem": ("ann_date", "公告日", "day"),
    "cd80_cb_price_chg": ("change_date", "转股价生效日", "day"),
    "cd80_ci_index_member": ("in_date", "成分纳入日", "day"),
    "cd80_cn_cpi": ("month", "月度日期", "month"),
    "cd80_cn_lpr": ("date", "发布日期", "day"),
    "cd80_daily": ("trade_date", "交易日", "day"),
    "cd80_daily_basic": ("trade_date", "交易日", "day"),
    "cd80_etf_basic": ("setup_date", "成立日", "day"),
    "cd80_etf_fund_daily": ("trade_date", "交易日", "day"),
    "cd80_etf_index": ("pub_date", "指数发布日期", "day"),
    "cd80_etf_share_size": ("trade_date", "交易日", "day"),
    "cd80_fund_basic": ("found_date", "成立日", "day"),
    "cd80_fund_nav": ("nav_date", "净值日期", "day"),
    "cd80_fund_portfolio": ("report_end_date", "持仓报告期", "day"),
    "cd80_index_dd": ("trade_date", "交易日", "day"),
    "cd80_ipo": ("ISSPUBDATE", "发行公告日", "day"),
    "cd80_lhb_summary": ("TRADEDATE", "交易日", "day"),
    "cd80_limit_down": ("trade_date", "交易日", "day"),
    "cd80_limit_surge": ("trade_date", "交易日", "day"),
    "cd80_margin_data": ("TRADEDATE", "交易日", "day"),
    "cd80_spots_sh_gold_dd": ("trade_date", "交易日", "day"),
    "cd80_stock_basic": ("list_date", "上市日", "day"),
    "cd80_stock_basic_calendar": ("cal_date", "日历日期（含休市日）", "day"),
    "cd80_stock_basic_managers": ("ann_date", "公告日", "day"),
    "cd80_stock_fin_balancesheet": ("end_date", "报告期末日", "day"),
    "cd80_stock_fin_cashflow": ("end_date", "报告期末日", "day"),
    "cd80_stock_fin_dividend": ("ann_date", "公告日", "day"),
    "cd80_stock_fin_income": ("end_date", "报告期末日", "day"),
    "cd80_stock_fin_indicator": ("end_date", "报告期末日", "day"),
    "cd80_stock_market_pr_factor": ("trade_date", "交易日", "day"),
    "cd80_stock_refer_holders_top10": ("end_date", "报告期末日", "day"),
    "cd80_stock_refer_pledge_stat": ("end_date", "统计期末日", "day"),
    "cd80_yc_cb": ("trade_date", "观察日期", "day"),
}

# The imported title for this one table incorrectly names a different index
# family. Preserve the imported metadata and source rows; correct only display.
SUMMARY_OVERRIDES = {
    "cd80_ci_index_member": "中信行业分级及成分股，包含纳入和剔除日期。",
}

MAX_DISTINCT_DATE_VALUES = 100_000
_DAY_COMPACT = re.compile(r"^[0-9]{8}$")
_DAY_DASHED = re.compile(r"^[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}$")
_MONTH_COMPACT = re.compile(r"^[0-9]{6}$")
_MONTH_DASHED = re.compile(r"^[0-9]{4}-[0-9]{2}$")


class PrimaryColumn(BaseModel):
    name: str
    description: str
    data_type: str
    logical_type: str


class DateRange(BaseModel):
    field: str | None
    label: str | None
    start: str | None
    end: str | None
    granularity: Literal["day", "month"] | None
    status: Literal["available", "no_date_field", "empty", "no_valid_date", "too_many_values"]


class CatalogEntry(BaseModel):
    name: str
    title: str
    row_count: int
    source_rows: int
    source_file: str
    imported_at: str
    provider: Literal["Tushare"]
    storage: Literal["local_sqlite"]
    summary: str
    primaryColumns: list[PrimaryColumn]
    dateRange: DateRange
    sourceNote: str


class CatalogResponse(BaseModel):
    data: list[CatalogEntry]
    total: int


def _normal_date(value: Any, granularity: str) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    raw = str(value).strip()
    if not raw or raw.upper() in {"NULL", "NONE", "NAN"}:
        return None
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    try:
        if granularity == "month":
            if _MONTH_COMPACT.fullmatch(raw):
                year, month = int(raw[:4]), int(raw[4:6])
            elif _MONTH_DASHED.fullmatch(raw):
                year, month = int(raw[:4]), int(raw[5:7])
            else:
                return None
            date(year, month, 1)
            return f"{year:04d}-{month:02d}"
        if _DAY_COMPACT.fullmatch(raw):
            parsed = date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        elif _DAY_DASHED.fullmatch(raw):
            parsed = date.fromisoformat(raw.replace("/", "-"))
        else:
            return None
        return parsed.isoformat()
    except ValueError:
        return None


def _date_range(connection: sqlite3.Connection, table: str, columns: set[str]) -> dict[str, Any]:
    spec = BUSINESS_DATES.get(table)
    if spec is None or spec[0] not in columns:
        return {
            "field": None, "label": None, "start": None, "end": None,
            "granularity": None, "status": "no_date_field",
        }
    field, label, granularity = spec
    result: dict[str, Any] = {
        "field": field, "label": label, "start": None, "end": None,
        "granularity": granularity, "status": "no_valid_date",
    }
    # The column and table are selected only from verified SQLite metadata.
    # DISTINCT avoids transferring millions of repeated trading-date values.
    rows = connection.execute(
        f"SELECT DISTINCT {quote_identifier(field)} AS value "
        f"FROM {quote_identifier(table)} WHERE {quote_identifier(field)} IS NOT NULL "
        "LIMIT ?",
        (MAX_DISTINCT_DATE_VALUES + 1,),
    ).fetchall()
    if len(rows) > MAX_DISTINCT_DATE_VALUES:
        result["status"] = "too_many_values"
        return result
    values = sorted(
        normalized for row in rows
        if (normalized := _normal_date(row["value"], granularity)) is not None
    )
    if values:
        result.update(start=values[0], end=values[-1], status="available")
    elif connection.execute(f"SELECT 1 FROM {quote_identifier(table)} LIMIT 1").fetchone() is None:
        result["status"] = "empty"
    return result


def _primary_columns(columns: tuple[dict[str, Any], ...], date_field: str | None) -> list[dict[str, Any]]:
    selected = list(columns[:6])
    if date_field and not any(column["name"] == date_field for column in selected):
        selected = [*selected[:5], next(column for column in columns if column["name"] == date_field)]
    return [
        {key: column[key] for key in ("name", "description", "data_type", "logical_type")}
        for column in selected
    ]


def build_catalog(connection: sqlite3.Connection, get_table: Callable[..., Any]) -> dict[str, Any]:
    rows = connection.execute(
        "SELECT name,title,row_count,source_rows,source_file,imported_at "
        "FROM _datasets WHERE substr(name,1,5)='cd80_' ORDER BY name"
    ).fetchall()
    data: list[dict[str, Any]] = []
    for row in rows:
        try:
            info = get_table(connection, row["name"])
        except HTTPException as exc:
            # A metadata inconsistency is a single unavailable table, not a
            # reason to expose a stale entry. Unexpected failures still fail.
            if exc.status_code != 503:
                raise
            continue
        columns = {column["name"] for column in info.columns}
        date_range = _date_range(connection, info.name, columns)
        source_file = PureWindowsPath(str(row["source_file"]).replace("/", "\\")).name
        data.append({
            **{key: row[key] for key in (
                "name", "title", "row_count", "source_rows", "imported_at"
            )},
            "source_file": source_file,
            "provider": "Tushare",
            "storage": "local_sqlite",
            "summary": SUMMARY_OVERRIDES.get(info.name, row["title"]),
            "primaryColumns": _primary_columns(info.columns, date_range["field"]),
            "dateRange": date_range,
            "sourceNote": f"原始导入文件：{source_file}；本地 SQLite 历史数据，非实时同步。",
        })
    return {"data": data, "total": len(data)}


def _stat_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


def _wal_identity(db_path: Path) -> tuple[int, int, int, int] | None:
    identity = _stat_identity(Path(str(db_path) + "-wal"))
    # Closing the last request connection removes an empty WAL; opening the
    # next one recreates it with a new inode/mtime despite zero data changes.
    # A WAL header alone (32 bytes) also contains no committed frames.
    return identity if identity is not None and identity[2] > 32 else None


class CatalogCache:
    """Short process cache, invalidated by DB/WAL changes and API audit writes."""

    def __init__(self, db_path: Path, ttl_seconds: float = 30.0) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self.lock = Lock()
        self.key: tuple[Any, ...] | None = None
        self.expires = 0.0
        self.value: dict[str, Any] | None = None

    def _identity(self, connection: sqlite3.Connection) -> tuple[Any, ...]:
        audit = connection.execute("SELECT MAX(audit_id) FROM _audit_log").fetchone()[0]
        return (
            _stat_identity(self.db_path),
            _wal_identity(self.db_path),
            audit,
        )

    def get(self, connection: sqlite3.Connection, get_table: Callable[..., Any]) -> dict[str, Any]:
        with self.lock:
            before = self._identity(connection)
            if self.value is not None and self.key == before and monotonic() < self.expires:
                return deepcopy(self.value)
            connection.execute("BEGIN")
            try:
                value = build_catalog(connection, get_table)
            finally:
                connection.execute("ROLLBACK")
            after = self._identity(connection)
            if before == after:
                self.key, self.value, self.expires = after, value, monotonic() + self.ttl_seconds
            else:
                self.key, self.value, self.expires = None, None, 0.0
            return deepcopy(value)
