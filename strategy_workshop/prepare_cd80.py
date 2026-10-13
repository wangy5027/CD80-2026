"""Prepare two traceable engineering samples from the stable CD80 SQLite copy.

This tool only reads the source snapshot and creates a new output directory.
It does not certify market-calendar, trading-rule or corporate-action coverage.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import re
import sqlite3

from .models import Bar


ETF_SYMBOL = "510300.SH"
STOCK_SYMBOL = "000001.SZ"
QUOTE_FIELDS = {"_row_id", "ts_code", "trade_date", "open", "high", "low", "close", "vol"}
VOLUME_MULTIPLIER = 100.0


def parse_date(value) -> str:
    """Explicitly accept ISO dates, YYYYMMDD and the delivered YYYYMMDD.0."""
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing/invalid date: {value!r}")
    text = str(value).strip()
    if re.fullmatch(r"\d{8}(?:\.0)?", text):
        return datetime.strptime(text[:8], "%Y%m%d").date().isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text).isoformat()
    raise ValueError(f"Unsupported date representation: {value!r}")


def number(value, label: str, *, positive: bool = False, nonnegative: bool = False) -> float:
    if value is None or isinstance(value, bool):
        raise ValueError(f"Missing/invalid {label}: {value!r}")
    result = float(value)
    if not isfinite(result) or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f"Invalid {label}: {value!r}")
    return result


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_rows(connection: sqlite3.Connection, table: str, symbol: str, required: set[str], implemented=False):
    # The four table names are fixed local constants; symbol is always a string
    # bound as a SQL value. Security-code leading zeros are never floatified.
    columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
    if not required.issubset(columns):
        raise ValueError(f"Missing {table} columns: {sorted(required - set(columns))}")
    sql = f'SELECT * FROM "{table}" WHERE ts_code = ?'
    args = [symbol]
    if implemented:
        sql += " AND div_proc = ?"
        args.append("Implemented")
    rows = [dict(row) for row in connection.execute(sql, args)]
    if not rows:
        raise ValueError(f"No sample rows in {table} for {symbol}")
    if any(row["ts_code"] != symbol for row in rows):
        raise ValueError("Source symbol must remain an exact string")
    return rows, columns


def validate_quotes(rows: list[dict], table: str) -> list[dict]:
    result = []
    seen = set()
    for row in rows:
        day = parse_date(row["trade_date"])
        if day in seen:
            raise ValueError(f"Duplicate quote date in {table}: {day}")
        seen.add(day)
        values = {key: number(row[key], f"{table}.{key}", positive=True) for key in ("open", "high", "low", "close")}
        values["volume"] = number(row["vol"], f"{table}.vol", nonnegative=True) * VOLUME_MULTIPLIER
        Bar(day, **values).validate()
        result.append({"symbol": row["ts_code"], "date": day, **values, "source": row, "source_table": table})
    return sorted(result, key=lambda row: row["date"])


def validate_factors(rows: list[dict]) -> dict[str, dict]:
    result = {}
    for row in rows:
        day = parse_date(row["trade_date"])
        if day in result:
            raise ValueError(f"Duplicate adjustment-factor date: {day}")
        factor = None if row["adj_factor"] is None else number(row["adj_factor"], "adj_factor", positive=True)
        result[day] = {"factor": factor, "source": row}
    return result


def validate_actions(rows: list[dict]) -> list[dict]:
    result = []
    seen = set()
    for row in rows:
        day = parse_date(row["ex_date"])
        if day in seen:
            raise ValueError(f"Conflicting/repeated Implemented corporate actions on {day}")
        seen.add(day)
        for key in ("stk_div", "stk_bo_rate", "stk_co_rate"):
            value = 0.0 if row.get(key) is None else number(row[key], key)
            if value != 0:
                raise ValueError(f"Sample rejects share distributions without a listing-date model: {day}, {key}")
        cash = number(row["cash_div_tax"], "cash_div_tax", nonnegative=True)
        paid = parse_date(row["pay_date"]) if row["pay_date"] is not None else None
        if cash > 0 and paid is None:
            raise ValueError(f"Cash dividend requires pay_date: {day}")
        if paid is not None and paid < day:
            raise ValueError(f"pay_date precedes ex_date: {day}")
        result.append({"symbol": row["ts_code"], "date": day, "cash_per_share": cash,
                       "share_ratio": 1.0, "pay_date": paid, "source": row})
    return sorted(result, key=lambda row: row["date"])


def create_normalized(path: Path, etf: list[dict], stock: list[dict], actions: list[dict]) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite: {path}")
    connection = sqlite3.connect(path)
    try:
        connection.executescript("""
            CREATE TABLE etf_quotes (
                symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, source_table TEXT NOT NULL,
                source_row_id INTEGER NOT NULL, source_values_json TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE stock_quotes (
                symbol TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL,
                high REAL NOT NULL, low REAL NOT NULL, close REAL NOT NULL,
                volume REAL NOT NULL, adjustment_factor REAL NOT NULL,
                source_table TEXT NOT NULL, source_row_id INTEGER NOT NULL,
                source_values_json TEXT NOT NULL, factor_source_table TEXT NOT NULL,
                factor_source_row_id INTEGER NOT NULL, factor_source_values_json TEXT NOT NULL,
                PRIMARY KEY (symbol, date)
            );
            CREATE TABLE corporate_actions (
                symbol TEXT NOT NULL, date TEXT NOT NULL, cash_per_share REAL NOT NULL,
                share_ratio REAL NOT NULL, pay_date TEXT,
                source_table TEXT NOT NULL, source_row_id INTEGER NOT NULL,
                source_values_json TEXT NOT NULL, PRIMARY KEY (symbol, date)
            );
            CREATE TABLE lineage (
                target_table TEXT NOT NULL, target_symbol TEXT NOT NULL,
                target_date TEXT NOT NULL, role TEXT NOT NULL,
                source_table TEXT NOT NULL, source_row_id INTEGER NOT NULL,
                source_values_json TEXT NOT NULL,
                PRIMARY KEY (target_table, target_symbol, target_date, role)
            );
        """)

        def lineage(table, row, role, source_table, raw):
            connection.execute("INSERT INTO lineage VALUES (?,?,?,?,?,?,?)", (
                table, row["symbol"], row["date"], role, source_table, raw["_row_id"], json_text(raw)))

        for row in etf:
            connection.execute("INSERT INTO etf_quotes VALUES (?,?,?,?,?,?,?,?,?,?)", (
                row["symbol"], row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"],
                row["source_table"], row["source"]["_row_id"], json_text(row["source"])))
            lineage("etf_quotes", row, "quote", row["source_table"], row["source"])
        for row in stock:
            connection.execute("INSERT INTO stock_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                row["symbol"], row["date"], row["open"], row["high"], row["low"], row["close"], row["volume"],
                row["adjustment_factor"], row["source_table"], row["source"]["_row_id"], json_text(row["source"]),
                "cd80_stock_market_pr_factor", row["factor_source"]["_row_id"], json_text(row["factor_source"])))
            lineage("stock_quotes", row, "quote", row["source_table"], row["source"])
            lineage("stock_quotes", row, "adjustment_factor", "cd80_stock_market_pr_factor", row["factor_source"])
        for row in actions:
            connection.execute("INSERT INTO corporate_actions VALUES (?,?,?,?,?,?,?,?)", (
                row["symbol"], row["date"], row["cash_per_share"], row["share_ratio"], row["pay_date"],
                "cd80_stock_fin_dividend", row["source"]["_row_id"], json_text(row["source"])))
            lineage("corporate_actions", row, "corporate_action", "cd80_stock_fin_dividend", row["source"])
        connection.commit()
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Derived SQLite integrity check failed")
    finally:
        connection.close()


def mapping(table: str, *, is_stock: bool) -> dict:
    columns = {key: key for key in ("symbol", "date", "open", "high", "low", "close", "volume")}
    if is_stock:
        columns["adjustment_factor"] = "adjustment_factor"
    result = {
        "table": table,
        "date_format": "%Y-%m-%d",
        "columns": columns,
        "instrument": {"name": "平安银行" if is_stock else "华泰柏瑞沪深300ETF",
                       "asset_type": "stock" if is_stock else "etf", "lot_size": 100,
                       "quantity_step": 100, "tick_size": 0.01 if is_stock else 0.001,
                       "settlement_days": 1},
        "verified_capabilities": {
            "unadjusted_execution_prices": True,
            "corporate_actions_complete": False,
            "market_rules_verified": False,
            "calendar_complete": False,
            "signal_adjustment_consistent": is_stock,
        },
        "sample_kind": "factor_complete_cash_action_engineering_sample" if is_stock else "unadjusted_price_diagnostic",
        "volume_unit": "shares" if is_stock else "fund_units",
        "source_volume_unit_assumption": "vol is reported in 100-share/unit lots; normalized volume = vol * 100",
        "formal_report_allowed": False,
        "gaps": ["Complete trading calendar not independently verified", "Historical daily price limits and suspension metadata unavailable",
                 "Corporate-action coverage is not independently certified"]
                 + ([] if is_stock else ["ETF adjustment factors unavailable; signal prices use raw OHLC", "ETF distribution/corporate-action records unavailable"]),
    }
    if is_stock:
        result["corporate_actions"] = {
            "table": "corporate_actions", "date_format": "%Y-%m-%d",
            "columns": {key: key for key in ("symbol", "date", "cash_per_share", "share_ratio", "pay_date")},
        }
    return result


def write_new_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def prepare(source: Path, output_dir: Path, source_manifest: Path | None = None) -> dict:
    source, output_dir = source.absolute(), output_dir.absolute()
    if output_dir.exists():
        raise FileExistsError(f"Refusing existing output directory: {output_dir}")
    if not source.is_file():
        raise ValueError(f"Source snapshot is not a file: {source}")
    source_manifest = source_manifest or Path(str(source) + ".manifest.json")
    origin = json.loads(source_manifest.read_text(encoding="utf-8"))
    before = source.stat()
    if before.st_size != origin["bytes"]:
        raise ValueError("Source size does not match stable-copy manifest")
    source_hash = hash_file(source)
    if source_hash != origin["sha256"]:
        raise ValueError("Source SHA-256 does not match stable-copy manifest")
    wal = Path(str(source) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("Immutable stable-copy reader requires no pending WAL content")
    connection = sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    try:
        etf_rows, etf_schema = read_rows(connection, "cd80_etf_fund_daily", ETF_SYMBOL, QUOTE_FIELDS)
        stock_rows, stock_schema = read_rows(connection, "cd80_daily", STOCK_SYMBOL, QUOTE_FIELDS)
        factor_rows, factor_schema = read_rows(connection, "cd80_stock_market_pr_factor", STOCK_SYMBOL, {"_row_id", "ts_code", "trade_date", "adj_factor"})
        action_rows, action_schema = read_rows(connection, "cd80_stock_fin_dividend", STOCK_SYMBOL,
                                             {"_row_id", "ts_code", "div_proc", "ex_date", "pay_date", "cash_div_tax", "stk_div"}, implemented=True)
    finally:
        connection.close()
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ValueError("Source snapshot changed during preparation")
    etf = validate_quotes(etf_rows, "cd80_etf_fund_daily")
    stock_all = validate_quotes(stock_rows, "cd80_daily")
    factors = validate_factors(factor_rows)
    first_gap = next((row["date"] for row in stock_all
                      if row["date"] not in factors or factors[row["date"]]["factor"] is None), None)
    stock = [row for row in stock_all if first_gap is None or row["date"] < first_gap]
    for row in stock:
        row["adjustment_factor"] = factors[row["date"]]["factor"]
        row["factor_source"] = factors[row["date"]]["source"]
    if len(etf) <= 250 or len(stock) <= 250:
        raise ValueError("Engineering samples need more than 250 bars for suggested warm-up")
    actions_all = validate_actions(action_rows)
    actions = [row for row in actions_all if stock[0]["date"] <= row["date"] <= stock[-1]["date"]]
    etf_mapping = mapping("etf_quotes", is_stock=False)
    stock_mapping = mapping("stock_quotes", is_stock=True)
    output_dir.mkdir(parents=True, exist_ok=False)
    database = output_dir / "normalized.sqlite3"
    create_normalized(database, etf, stock, actions)
    etf_path, stock_path = output_dir / "510300.SH.mapping.json", output_dir / "000001.SZ.mapping.json"
    write_new_json(etf_path, etf_mapping)
    write_new_json(stock_path, stock_mapping)

    def sample(rows, mapping_path, kind):
        return {"symbol": rows[0]["symbol"], "sample_kind": kind, "rows": len(rows),
                "available_start": rows[0]["date"], "available_end": rows[-1]["date"],
                "warmup_bars": 250, "suggested_run_start": rows[250]["date"], "suggested_run_end": rows[-1]["date"],
                "run_rows": len(rows) - 250, "mapping_path": str(mapping_path)}

    manifest = {
        "schema_version": "1.0.0", "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Engineering verification only; neither sample qualifies as a formal market-performance report",
        "source_original_path": origin["source"], "source_snapshot_path": str(source),
        "source_sha256": source_hash, "source_bytes": before.st_size,
        "source_copy_manifest_path": str(source_manifest.absolute()),
        "source_open_mode": "mode=ro&immutable=1; PRAGMA query_only=ON; transaction snapshot",
        "source_modified": False, "normalized_database": str(database),
        "normalized_database_sha256": hash_file(database),
        "samples": {
            ETF_SYMBOL: sample(etf, etf_path, etf_mapping["sample_kind"]),
            STOCK_SYMBOL: {**sample(stock, stock_path, stock_mapping["sample_kind"]),
                           "original_quote_rows": len(stock_all), "first_missing_factor_date": first_gap,
                           "excluded_quote_rows": len(stock_all) - len(stock),
                           "truncation_reason": "Stop before the first quote date with missing adjustment factor; no fill-forward and no skipped missing-date rows",
                           "implemented_action_rows_in_source": len(actions_all), "retained_action_rows": len(actions)},
        },
        "source_tables": {"cd80_etf_fund_daily": etf_schema, "cd80_daily": stock_schema,
                          "cd80_stock_market_pr_factor": factor_schema, "cd80_stock_fin_dividend": action_schema},
        "field_and_unit_contract": {
            "security_code": "Exact ts_code string, including leading zeros and exchange suffix; never converted to float",
            "dates": "Explicit YYYYMMDD, YYYYMMDD.0 or ISO parsing; normalized to YYYY-MM-DD",
            "OHLC": "Source raw execution-price fields retained in CNY; no adjusted price substituted for a fill",
            "volume": "Source vol assumed lots of 100 shares/fund units; normalized volume = vol * 100",
            "stock_signal_price": "raw OHLC * same-date cd80_stock_market_pr_factor.adj_factor; native factor scale retained",
            "ETF_signal_price": "No factor available; unadjusted-price diagnostic only, no invented adjustment factor column",
            "cash_dividend": "Implemented rows only; cash_div_tax treated as pre-tax CNY per pre-action share, payment date explicit",
            "share_distribution": "Any nonzero stk_div/stk_bo_rate/stk_co_rate rejected; listing-date model unavailable",
            "lineage": "Original row IDs, table names and complete original field values preserved in quote/action rows and lineage table",
        },
        "gaps": {
            ETF_SYMBOL: etf_mapping["gaps"], STOCK_SYMBOL: stock_mapping["gaps"],
            "common": ["Suggested ranges are available-data engineering ranges, not the product's default three-year range",
                       "No independent full exchange-calendar validation; missing raw quote days have not been filled",
                       "No per-day suspension/price-limit metadata or historical rule verification",
                       "Corporate-action table presence does not independently establish complete coverage",
                       "Reference personal dividend withholding remains an explicit engine assumption"],
        },
        "formal_report_allowed": False,
    }
    write_new_json(output_dir / "manifest.json", manifest)
    return manifest


def main() -> None:
    root = Path(__file__).absolute().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "data/strategy-workshop-market/cd80_source_snapshot.sqlite3")
    parser.add_argument("--output-dir", type=Path, default=root / "data/strategy-workshop-market/derived")
    parser.add_argument("--source-manifest", type=Path)
    args = parser.parse_args()
    result = prepare(args.source, args.output_dir, args.source_manifest)
    print(json.dumps({"normalized_database": result["normalized_database"], "samples": result["samples"],
                      "formal_report_allowed": result["formal_report_allowed"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
