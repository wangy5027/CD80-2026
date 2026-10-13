"""Compare first, middle, and last source CSV records with SQLite values."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
csv.field_size_limit(sys.maxsize)


def quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def expected_value(value: str, declared_type: str):
    if value == "":
        return None
    if declared_type == "INTEGER":
        return int(value)
    if declared_type == "REAL":
        return float(value)
    if declared_type == "TEXT":
        return value
    raise ValueError(f"未知字段类型：{declared_type}")


def short_repr(value) -> str:
    result = repr(value)
    return result if len(result) <= 160 else result[:157] + "..."


def verify_table(conn: sqlite3.Connection, source: Path, entry: dict) -> dict:
    name = entry["name"]
    source_file = source / entry["source_file"]
    columns = list(entry["column_types"])
    types = entry["column_types"]
    source_count = entry["rows"]
    target_ids = sorted({1, (source_count + 1) // 2, source_count}) if source_count else []
    select = (
        f"SELECT _row_id, {', '.join(quoted(column) for column in columns)} "
        f"FROM {quoted(name)} WHERE _row_id IN ({', '.join('?' for _ in target_ids)})"
    ) if target_ids else None
    sqlite_rows = {
        row[0]: row[1:]
        for row in conn.execute(select, target_ids)
    } if select else {}

    problems = []
    for row_id in target_ids:
        if row_id not in sqlite_rows:
            problems.append({"row_id": row_id, "problem": "SQLite 行不存在"})

    sampled_rows = 0
    compared_fields = 0
    scanned_rows = 0
    with source_file.open("r", encoding=entry["encoding"], errors="strict", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        header = next(reader)
        if header != columns:
            problems.append({"problem": "CSV 字段名或顺序与导入清单不一致"})
        target_set = set(target_ids)
        for row in reader:
            scanned_rows += 1
            if len(row) != len(columns):
                problems.append({"row_id": scanned_rows, "problem": f"CSV 字段数 {len(row)}，预期 {len(columns)}"})
                continue
            if scanned_rows not in target_set or scanned_rows not in sqlite_rows:
                continue
            sampled_rows += 1
            actual = sqlite_rows[scanned_rows]
            for index, field in enumerate(columns):
                expected = expected_value(row[index], types[field])
                got = actual[index]
                compared_fields += 1
                if type(expected) is not type(got) or expected != got:
                    problems.append(
                        {
                            "row_id": scanned_rows,
                            "field": field,
                            "declared_type": types[field],
                            "expected": short_repr(expected),
                            "actual": short_repr(got),
                        }
                    )
    if scanned_rows != source_count:
        problems.append({"problem": f"CSV 实际 {scanned_rows} 行，清单 {source_count} 行"})
    return {
        "name": name,
        "source_rows_scanned": scanned_rows,
        "sample_row_ids": target_ids,
        "compared_rows": sampled_rows,
        "compared_fields": compared_fields,
        "error_count": len(problems),
        "errors": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "import_manifest.json")
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "cd80.sqlite3")
    parser.add_argument("--source", type=Path, help="默认使用清单中的 actual_source")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "value_verification.json")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    source = (args.source or Path(manifest["actual_source"])).expanduser().resolve()
    database = args.database.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not source.is_dir() or not database.is_file():
        parser.error(f"源目录或数据库不存在：{source}；{database}")

    report = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "database": str(database),
        "method": "各源 CSV 的第 1、(N+1)//2、N 条逻辑记录与对应 SQLite _row_id 全字段比较；空串按 NULL，INTEGER/REAL 按导入规则转换；TEXT 精确比较。",
        "database_sha256_note": "导入清单中的整库 SHA256 仅代表初始导入快照，本核验不要求当前库 SHA256 相同。",
        "tables": [],
    }
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only=ON")
        for position, entry in enumerate(manifest["tables"], start=1):
            result = verify_table(conn, source, entry)
            report["tables"].append(result)
            print(
                f"[{position:02d}/{len(manifest['tables'])}] {entry['name']}: "
                f"{result['compared_rows']} 行/{result['compared_fields']} 字段，{result['error_count']} 错误",
                flush=True,
            )
    finally:
        conn.close()
    report["table_count"] = len(report["tables"])
    report["compared_rows"] = sum(item["compared_rows"] for item in report["tables"])
    report["compared_fields"] = sum(item["compared_fields"] for item in report["tables"])
    report["error_count"] = sum(item["error_count"] for item in report["tables"])
    report["ok"] = report["error_count"] == 0
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".building")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, output)
    print(
        f"总计：{report['table_count']} 表、{report['compared_rows']} 行、"
        f"{report['compared_fields']} 字段、{report['error_count']} 错误；报告 {output}",
        flush=True,
    )
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
