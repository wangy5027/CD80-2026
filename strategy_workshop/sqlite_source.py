"""Read-only, explicit-schema SQLite adapter; never auto-guess a price basis."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3

from .models import Bar, CorporateAction, Instrument, validate_bars


def _connect(path: str | Path) -> sqlite3.Connection:
    # absolute() avoids Windows sandbox failures while resolving temporary-file
    # reparse points. SQLite still opens an existing file strictly read-only.
    file = Path(path).absolute()
    if not file.is_file():
        raise ValueError("Database path must be a file")
    connection = sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    connection.execute("BEGIN")
    return connection


def inspect_database(path: str | Path) -> dict[str, list[dict]]:
    """Schema only: does not inspect account or other record contents."""
    connection = _connect(path)
    try:
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: [{"name": row[1], "type": row[2]} for row in connection.execute(f"PRAGMA table_info({_identifier(name)})")] for name in names}
    finally:
        connection.close()


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError("Invalid SQLite identifier")
    return '"' + value.replace('"', '""') + '"'


def _rows(connection: sqlite3.Connection, table: str, columns: dict, symbol: str, end_date: str, date_format: str) -> list[dict]:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if table not in tables:
        raise ValueError(f"Mapped table does not exist: {table}")
    available = {row[1] for row in connection.execute(f"PRAGMA table_info({_identifier(table)})")}
    if not set(columns.values()).issubset(available):
        raise ValueError("Mapped columns do not exist")
    if not {"symbol", "date"}.issubset(columns):
        raise ValueError("symbol and date column mappings are required")
    if date_format not in ("%Y-%m-%d", "%Y%m%d"):
        raise ValueError("Supported date formats: %Y-%m-%d, %Y%m%d")
    end_encoded = datetime.strptime(end_date, "%Y-%m-%d").strftime(date_format)
    names = list(columns)
    projection = ", ".join(_identifier(columns[key]) for key in names)
    date_column = _identifier(columns["date"])
    sql = (f"SELECT {projection} FROM {_identifier(table)} WHERE {_identifier(columns['symbol'])} = ? "
           f"AND CAST({date_column} AS TEXT) <= ? ORDER BY {date_column} ASC")
    result = []
    for values in connection.execute(sql, (symbol, end_encoded)):
        row = dict(zip(names, values))
        row["date"] = datetime.strptime(str(row["date"]), date_format).date().isoformat()
        result.append(row)
    return result


def load_snapshot(path: str | Path, mapping: dict, symbol: str, start_date: str, end_date: str,
                  warmup_bars: int = 250) -> tuple[list[Bar], list[CorporateAction], Instrument, dict]:
    """Mapping/provenance is an operator-reviewed contract, not model output."""
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    columns = mapping["columns"]
    if not required.issubset(columns):
        raise ValueError(f"Missing OHLCV mapping: {sorted(required - set(columns))}")
    if warmup_bars < 0:
        raise ValueError("warmup_bars cannot be negative")
    connection = _connect(path)
    try:
        rows = _rows(connection, mapping["table"], columns, symbol, end_date, mapping.get("date_format", "%Y-%m-%d"))
        history = [row for row in rows if row["date"] < start_date][-warmup_bars:] if warmup_bars else []
        selected = history + [row for row in rows if row["date"] >= start_date]
        bars = []
        for row in selected:
            values = {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}
            optional = {}
            if "adjustment_factor" in row:
                optional["adjustment_factor"] = float(row["adjustment_factor"])
            if "suspended" in row:
                if row["suspended"] not in (0, 1, False, True):
                    raise ValueError("suspended must be an explicit 0/1 field")
                optional["suspended"] = bool(row["suspended"])
            for key in ("limit_up", "limit_down"):
                if key in row and row[key] is not None:
                    optional[key] = float(row[key])
            bars.append(Bar(date=row["date"], **values, **optional))
        validate_bars(bars)
        actions = []
        if mapping.get("corporate_actions"):
            action_mapping = mapping["corporate_actions"]
            action_rows = _rows(connection, action_mapping["table"], action_mapping["columns"], symbol,
                                end_date, action_mapping.get("date_format", mapping.get("date_format", "%Y-%m-%d")))
            action_format = action_mapping.get("date_format", mapping.get("date_format", "%Y-%m-%d"))
            actions = [CorporateAction(row["date"], float(row.get("cash_per_share", 0)), float(row.get("share_ratio", 1)),
                                       datetime.strptime(str(row["pay_date"]), action_format).date().isoformat() if row.get("pay_date") is not None else None)
                       for row in action_rows if row["date"] >= bars[0].date]
        instrument_data = dict(mapping.get("instrument", {}))
        if not {"asset_type", "lot_size", "quantity_step", "tick_size", "settlement_days"}.issubset(instrument_data):
            raise ValueError("Explicit instrument metadata is required; do not infer stock/ETF trading rules from price data")
        instrument_data["code"] = symbol
        instrument_data.setdefault("name", symbol)
        instrument = Instrument(**instrument_data)
        provenance = dict(mapping.get("verified_capabilities", {}))
        provenance.update({"dataset_kind": "real_sqlite", "source_file": str(Path(path).absolute()),
                           "table": mapping["table"], "mapping": mapping, "read_only": True})
        return bars, actions, instrument, provenance
    finally:
        connection.close()

