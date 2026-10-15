"""Import all CD80 CSV files into a lossless, API-ready SQLite database.

The CSV files are always read, never modified. The database is built at a
temporary path and becomes the requested database only after verification.
"""

from __future__ import annotations

import argparse
import codecs
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Iterator

import csv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = Path("/Volumes/Jaylen_980/2026CD80/02-授权数据-Data/Datasets")
DEFAULT_DATABASE = PROJECT_ROOT / "data" / "cd80.sqlite3"
EXPECTED_SOURCE_ROWS = 19_274_362
EXPECTED_SOURCE_BYTES = 2_305_643_404
EXPECTED_SOURCE_COLUMNS = 856
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
INTEGER_PATTERN = re.compile(r"[+-]?(?:0|[1-9][0-9]*)\Z")
NUMBER_PATTERN = re.compile(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
LEADING_ZERO_PATTERN = re.compile(r"[+-]?0\d+\Z")
csv.field_size_limit(sys.maxsize)


def quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def decoded_lines(path: Path, encoding: str, digest: hashlib._Hash) -> Iterator[str]:
    """Hash the original bytes while supplying strictly decoded CSV lines."""
    decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    with path.open("rb") as stream:
        for raw_line in stream:
            digest.update(raw_line)
            line = decoder.decode(raw_line, final=False)
            if line:
                yield line
        tail = decoder.decode(b"", final=True)
        if tail:
            yield tail


def rows_with_hash(path: Path, encoding: str, digest: hashlib._Hash):
    return csv.reader(decoded_lines(path, encoding, digest), strict=True)


def read_dictionary(path: Path) -> dict[str, dict]:
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("字段字典需要 openpyxl；请使用项目 .venv/bin/python") from exc

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    result: dict[str, dict] = {}
    for sheet in workbook:
        if not sheet.title.startswith("cd80_"):
            continue
        title = ""
        descriptions: dict[str, str] = {}
        field_header_seen = False
        for row in sheet.iter_rows(values_only=True):
            first = str(row[0]).strip() if row and row[0] is not None else ""
            second = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
            third = str(row[2]).strip() if len(row) > 2 and row[2] is not None else ""
            if first == "Table Description":
                title = second
            elif first == "No." and second == "Field Name":
                field_header_seen = True
            elif field_header_seen and second:
                descriptions[second] = third
        result[sheet.title] = {"title": title or sheet.title, "columns": descriptions}
    workbook.close()
    return result


def logical_type(name: str, data_type: str, description: str = "") -> str:
    key = name.casefold()
    detail = description.casefold()
    if (
        "date" in key
        or "time" in key
        or key in {"month", "year", "period", "calendarmonth", "birthday", "tmstamp"}
        or ("date" in detail and data_type == "TEXT")
    ):
        return "date"
    if "code" in key or key in {"symbol", "ticker", "exchange", "isin"}:
        return "code"
    if key == "id" or key.endswith("_id") or key.endswith("id"):
        return "identifier"
    if data_type in {"INTEGER", "REAL"}:
        return "number"
    return "text"


def next_numeric_type(current: str, value: str) -> str:
    if current == "TEXT":
        return current
    if LEADING_ZERO_PATTERN.fullmatch(value):
        return "TEXT"
    if INTEGER_PATTERN.fullmatch(value):
        integer = int(value)
        if not INT64_MIN <= integer <= INT64_MAX:
            return "TEXT"
        return current
    if NUMBER_PATTERN.fullmatch(value):
        number = float(value)
        if math.isfinite(number):
            return "REAL"
    return "TEXT"


def scan_csv(path: Path, encoding: str, descriptions: dict[str, str]) -> dict:
    digest = hashlib.sha256()
    reader = rows_with_hash(path, encoding, digest)
    try:
        header = next(reader)
    except StopIteration as exc:
        raise ValueError(f"空 CSV：{path}") from exc
    if not header or any(not field for field in header):
        raise ValueError(f"字段名为空：{path}")
    if len(set(header)) != len(header) or "_row_id" in header:
        raise ValueError(f"重复字段名或与 _row_id 冲突：{path}")

    logical = [logical_type(field, "TEXT", descriptions.get(field, "")) for field in header]
    states = ["TEXT" if kind in {"date", "code", "identifier"} else "INTEGER" for kind in logical]
    seen_nonempty = [False] * len(header)
    count = 0
    for row in reader:
        if len(row) != len(header):
            raise ValueError(f"{path}:{reader.line_num} 字段数 {len(row)}，预期 {len(header)}")
        count += 1
        for index, value in enumerate(row):
            if value:
                seen_nonempty[index] = True
                if states[index] != "TEXT":
                    states[index] = next_numeric_type(states[index], value)
    # A field may be numeric but entirely empty. Keeping TEXT prevents writing
    # an unsupported numerical meaning into metadata for an unknown field.
    states = [state if seen_nonempty[index] else "TEXT" for index, state in enumerate(states)]
    return {
        "header": header,
        "types": states,
        "logical_types": [logical_type(field, states[i], descriptions.get(field, "")) for i, field in enumerate(header)],
        "rows": count,
        "sha256": digest.hexdigest(),
        "bytes": path.stat().st_size,
    }


def sql_value(value: str, data_type: str):
    if value == "":
        return None
    if data_type == "INTEGER":
        return int(value)
    if data_type == "REAL":
        return float(value)
    return value


def create_metadata(conn: sqlite3.Connection) -> None:
    conn.executescript(
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
        CREATE INDEX idx__columns_table_ordinal ON _columns(table_name, ordinal);
        """
    )


def create_dataset_table(conn: sqlite3.Connection, name: str, header: list[str], types: list[str]) -> None:
    fields = [f"{quoted(field)} {data_type}" for field, data_type in zip(header, types)]
    conn.execute(
        f"CREATE TABLE {quoted(name)} (_row_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        + ", ".join(fields)
        + ")"
    )


def import_rows(
    conn: sqlite3.Connection,
    path: Path,
    encoding: str,
    name: str,
    header: list[str],
    types: list[str],
    expected_rows: int,
    expected_hash: str,
    batch_size: int,
) -> None:
    digest = hashlib.sha256()
    reader = rows_with_hash(path, encoding, digest)
    actual_header = next(reader)
    if actual_header != header:
        raise ValueError(f"导入时字段名发生变化：{path}")
    placeholders = ", ".join("?" for _ in header)
    insert_sql = f"INSERT INTO {quoted(name)} (" + ", ".join(quoted(column) for column in header) + f") VALUES ({placeholders})"
    batch = []
    total = 0
    conn.execute("BEGIN")
    try:
        for row in reader:
            if len(row) != len(header):
                raise ValueError(f"{path}:{reader.line_num} 字段数 {len(row)}，预期 {len(header)}")
            batch.append(tuple(sql_value(value, types[i]) for i, value in enumerate(row)))
            if len(batch) >= batch_size:
                conn.executemany(insert_sql, batch)
                total += len(batch)
                batch.clear()
        if batch:
            conn.executemany(insert_sql, batch)
            total += len(batch)
        if total != expected_rows or digest.hexdigest() != expected_hash:
            raise ValueError(f"导入时源数据发生变化：{path}；原 {expected_rows} 行/sha256={expected_hash}，现 {total} 行/sha256={digest.hexdigest()}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def choose_index_fields(header: list[str]) -> tuple[str | None, str | None]:
    lookup = {field.casefold(): field for field in header}
    code_names = [
        "ts_code", "ticker", "security_code", "stk_code", "fund_code", "symbol",
        "secode", "code", "cb_code", "curve_code", "indx_code", "bond_code",
    ]
    date_names = [
        "trade_date", "announcement_date", "nav_date", "ann_date", "date",
        "cal_date", "publication_date", "end_date", "report_end_date", "month",
        "list_date", "pub_date", "call_date", "change_date", "timestamp",
    ]
    code = next((lookup[field] for field in code_names if field in lookup), None)
    date = next((lookup[field] for field in date_names if field in lookup), None)
    return code, date


def create_indexes(conn: sqlite3.Connection, table: str, header: list[str]) -> list[dict]:
    code, date = choose_index_fields(header)
    planned: list[tuple[str, tuple[str, ...]]] = []
    if code and date:
        planned.extend([("code_date", (code, date)), ("date", (date,))])
    elif code:
        planned.append(("code", (code,)))
    elif date:
        planned.append(("date", (date,)))
    created = []
    for suffix, fields in planned:
        name = f"idx_{table}_{suffix}"
        conn.execute(
            f"CREATE INDEX {quoted(name)} ON {quoted(table)} ("
            + ", ".join(quoted(field) for field in fields)
            + ")"
        )
        created.append({"name": name, "columns": list(fields)})
    return created


def create_count_triggers(conn: sqlite3.Connection, table: str) -> None:
    table_literal = table.replace("'", "''")
    for operation, delta in (("INSERT", "+ 1"), ("DELETE", "- 1")):
        name = f"trg_{table}_{operation.casefold()}_count"
        conn.execute(
            f"CREATE TRIGGER {quoted(name)} AFTER {operation} ON {quoted(table)} BEGIN "
            f"UPDATE _datasets SET row_count = row_count {delta} WHERE name = '{table_literal}'; END"
        )


def verify_build(conn: sqlite3.Connection, tables: list[dict], expected_rows: int | None) -> dict:
    result = conn.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise RuntimeError(f"SQLite quick_check 失败：{result}")
    actual_total = 0
    for item in tables:
        table = item["name"]
        count = conn.execute(f"SELECT COUNT(*) FROM {quoted(table)}").fetchone()[0]
        metadata = conn.execute("SELECT row_count, source_rows FROM _datasets WHERE name=?", (table,)).fetchone()
        column_count = conn.execute("SELECT COUNT(*) FROM _columns WHERE table_name=?", (table,)).fetchone()[0]
        if count != item["rows"] or metadata != (count, count) or column_count != item["columns"]:
            raise RuntimeError(f"数量校验失败：{table} count={count}, metadata={metadata}, fields={column_count}")
        actual_total += count
    if expected_rows is not None and actual_total != expected_rows:
        raise RuntimeError(f"总行数 {actual_total} 不等于预期 {expected_rows}")
    return {"quick_check": result, "total_rows": actual_total}


def fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="包含 cd80_*.csv 的目录")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE, help="最终 SQLite 数据库路径")
    parser.add_argument("--dictionary", type=Path, help="Dataset Overview.xlsx；默认取源目录上一级")
    parser.add_argument("--manifest", type=Path, help="导入清单 JSON；默认 data/import_manifest.json")
    parser.add_argument("--batch-size", type=int, default=20_000)
    parser.add_argument("--expected-tables", type=int, default=None)
    parser.add_argument("--expected-rows", type=int, default=None)
    parser.add_argument("--expected-bytes", type=int, default=None)
    parser.add_argument("--expected-columns", type=int, default=None)
    parser.add_argument("--requested-source", type=Path, help="记录原计划使用、但当前不可用的源路径")
    parser.add_argument("--replace", action="store_true", help="仅在显式指定时原子替换现有最终数据库")
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size 必须大于 0")

    source = args.source.expanduser().resolve()
    database = args.database.expanduser().resolve()
    dictionary_path = (args.dictionary or source.parent / "Dataset Overview.xlsx").expanduser().resolve()
    manifest_path = (args.manifest or database.parent / "import_manifest.json").expanduser().resolve()
    building = database.with_name(database.name + ".building")
    if not source.is_dir():
        parser.error(f"源目录不存在：{source}")
    if not dictionary_path.is_file():
        parser.error(f"字段字典不存在：{dictionary_path}")
    if database.exists() and not args.replace:
        parser.error(f"目标数据库已存在，默认拒绝覆盖：{database}")
    if building.exists():
        parser.error(f"发现未完成的构建文件，请先检查后手动移走：{building}")
    files = sorted(source.glob("cd80_*.csv"))
    if not files:
        parser.error(f"未找到 cd80_*.csv：{source}")
    if args.expected_tables is not None and len(files) != args.expected_tables:
        parser.error(f"找到 {len(files)} 张 CSV，预期 {args.expected_tables}")
    source_bytes = sum(path.stat().st_size for path in files)
    if args.expected_bytes is not None and source_bytes != args.expected_bytes:
        parser.error(f"源文件共 {source_bytes} 字节，预期 {args.expected_bytes}")
    database.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    dictionary = read_dictionary(dictionary_path)
    if any(path.stem not in dictionary for path in files):
        missing = [path.stem for path in files if path.stem not in dictionary]
        parser.error(f"字典缺少工作表：{missing}")

    manifest = {
        "format_version": 1,
        "requested_source": str(args.requested_source) if args.requested_source else str(source),
        "actual_source": str(source),
        "requested_source_sha256_comparison": "not_performed" if args.requested_source and args.requested_source != source else "same_source",
        "dictionary": str(dictionary_path),
        "dictionary_sha256": sha256_file(dictionary_path),
        "database": str(database),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "source_files": len(files),
        "source_bytes": source_bytes,
        "tables": [],
    }
    print(f"开始导入 {len(files)} 张 CSV，源文件 {source_bytes:,} 字节；构建路径 {building}", flush=True)
    conn = sqlite3.connect(building, timeout=120, isolation_level=None)
    try:
        conn.execute("PRAGMA page_size=8192")
        conn.execute("PRAGMA journal_mode=OFF")
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")
        conn.execute("PRAGMA cache_size=-131072")
        conn.execute("PRAGMA temp_store=MEMORY")
        create_metadata(conn)
        for number, path in enumerate(files, start=1):
            table = path.stem
            details = dictionary[table]
            encoding = "gb18030" if table == "cd80_bond_cb_basic" else "utf-8-sig"
            print(f"[{number:02d}/{len(files)}] 扫描 {table} ...", flush=True)
            profile = scan_csv(path, encoding, details["columns"])
            create_dataset_table(conn, table, profile["header"], profile["types"])
            print(f"[{number:02d}/{len(files)}] 写入 {profile['rows']:,} 行 ...", flush=True)
            import_rows(
                conn, path, encoding, table, profile["header"], profile["types"],
                profile["rows"], profile["sha256"], args.batch_size,
            )
            imported_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO _datasets (name,title,row_count,source_rows,source_file,source_sha256,encoding,imported_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (table, details["title"], profile["rows"], profile["rows"], path.name, profile["sha256"], encoding, imported_at),
            )
            conn.executemany(
                "INSERT INTO _columns (table_name,name,ordinal,data_type,logical_type,description) VALUES (?,?,?,?,?,?)",
                (
                    (table, field, ordinal, profile["types"][ordinal - 1], profile["logical_types"][ordinal - 1], details["columns"].get(field, ""))
                    for ordinal, field in enumerate(profile["header"], start=1)
                ),
            )
            indexes = create_indexes(conn, table, profile["header"])
            create_count_triggers(conn, table)
            entry = {
                "name": table,
                "source_file": path.name,
                "source_bytes": profile["bytes"],
                "source_sha256": profile["sha256"],
                "encoding": encoding,
                "rows": profile["rows"],
                "columns": len(profile["header"]),
                "column_types": dict(zip(profile["header"], profile["types"])),
                "missing_dictionary_descriptions": [field for field in profile["header"] if not details["columns"].get(field)],
                "indexes": indexes,
            }
            manifest["tables"].append(entry)
            print(f"[{number:02d}/{len(files)}] 完成 {table}: {profile['rows']:,} 行，{len(profile['header'])} 列，{len(indexes)} 个索引", flush=True)

        source_rows = sum(item["rows"] for item in manifest["tables"])
        source_columns = sum(item["columns"] for item in manifest["tables"])
        if args.expected_rows is not None and source_rows != args.expected_rows:
            raise RuntimeError(f"源总行数 {source_rows} 不等于预期 {args.expected_rows}")
        if args.expected_columns is not None and source_columns != args.expected_columns:
            raise RuntimeError(f"源字段位置总数 {source_columns} 不等于预期 {args.expected_columns}")
        verification = verify_build(conn, manifest["tables"], args.expected_rows)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.close()
        fsync_file(building)
        # This replacement is deliberate only if --replace was supplied.
        if database.exists() and not args.replace:
            raise RuntimeError(f"最终路径在构建期间出现，拒绝覆盖：{database}")
        os.replace(building, database)
        fsync_file(database)
        manifest.update(
            {
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "source_rows": source_rows,
                "source_columns": source_columns,
                "verification": verification,
                "database_bytes": database.stat().st_size,
                "database_sha256": sha256_file(database),
            }
        )
        manifest_temp = manifest_path.with_name(manifest_path.name + ".building")
        with manifest_temp.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(manifest_temp, manifest_path)
        print(f"完成：{source_rows:,} 行，{source_columns} 源字段，SQLite {database.stat().st_size:,} 字节", flush=True)
        print(f"数据库：{database}\n清单：{manifest_path}", flush=True)
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
