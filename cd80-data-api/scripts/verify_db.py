"""Check CD80 SQLite integrity, metadata, source provenance, and row counts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def quoted(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(database: Path, manifest: dict, source: Path | None, pristine: bool) -> dict:
    problems: list[str] = []
    if not database.is_file():
        raise FileNotFoundError(database)
    conn = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        conn.execute("PRAGMA query_only=ON")
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            problems.append(f"SQLite quick_check: {integrity}")
        expected_tables = {entry["name"]: entry for entry in manifest["tables"]}
        actual_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cd80_%'"
            )
        }
        if actual_tables != set(expected_tables):
            problems.append(f"表集合不符：缺少 {sorted(set(expected_tables) - actual_tables)}，多出 {sorted(actual_tables - set(expected_tables))}")
        actual_total = 0
        source_total = 0
        actual_fields = 0
        for name, entry in expected_tables.items():
            if name not in actual_tables:
                continue
            count = conn.execute(f"SELECT COUNT(*) FROM {quoted(name)}").fetchone()[0]
            actual_total += count
            source_total += entry["rows"]
            actual_fields += entry["columns"]
            metadata = conn.execute(
                "SELECT row_count,source_rows,source_file,source_sha256,encoding FROM _datasets WHERE name=?",
                (name,),
            ).fetchone()
            if metadata is None:
                problems.append(f"{name}: _datasets 缺失")
            else:
                row_count, source_rows, source_file, source_hash, encoding = metadata
                if row_count != count:
                    problems.append(f"{name}: 实际 {count} 行，_datasets.row_count={row_count}")
                if source_rows != entry["rows"]:
                    problems.append(f"{name}: source_rows={source_rows}，清单为 {entry['rows']}")
                if (source_file, source_hash, encoding) != (
                    entry["source_file"], entry["source_sha256"], entry["encoding"]
                ):
                    problems.append(f"{name}: 文件名、SHA256 或编码与清单不符")
            if pristine and count != entry["rows"]:
                problems.append(f"{name}: 初始行数 {entry['rows']}，现为 {count}")

            columns = conn.execute(f"PRAGMA table_info({quoted(name)})").fetchall()
            expected_columns = ["_row_id", *entry["column_types"]]
            if [item[1] for item in columns] != expected_columns:
                problems.append(f"{name}: 表字段顺序或名称不符")
            if not columns or columns[0][2] != "INTEGER" or columns[0][5] != 1:
                problems.append(f"{name}: _row_id 不是 INTEGER PRIMARY KEY")
            table_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)
            ).fetchone()[0]
            if "AUTOINCREMENT" not in table_sql.upper():
                problems.append(f"{name}: _row_id 未声明 AUTOINCREMENT")
            actual_types = {item[1]: item[2] for item in columns[1:]}
            if actual_types != entry["column_types"]:
                problems.append(f"{name}: 字段类型不符")
            registered = conn.execute(
                "SELECT name, data_type FROM _columns WHERE table_name=? ORDER BY ordinal", (name,)
            ).fetchall()
            if registered != list(entry["column_types"].items()):
                problems.append(f"{name}: _columns 与源字段不符")
            for operation in ("insert", "delete"):
                trigger = f"trg_{name}_{operation}_count"
                if not conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=?", (trigger,)
                ).fetchone():
                    problems.append(f"{name}: 缺少行数触发器 {trigger}")
            index_names = {row[1] for row in conn.execute(f"PRAGMA index_list({quoted(name)})")}
            for index in entry["indexes"]:
                if index["name"] not in index_names:
                    problems.append(f"{name}: 缺少索引 {index['name']}")

            if source is not None:
                source_file = source / entry["source_file"]
                if not source_file.is_file():
                    problems.append(f"源文件不存在：{source_file}")
                elif source_file.stat().st_size != entry["source_bytes"] or sha256_file(source_file) != entry["source_sha256"]:
                    problems.append(f"源文件字节数或 SHA256 变化：{source_file}")

        metadata_count = conn.execute("SELECT COUNT(*) FROM _datasets").fetchone()[0]
        if metadata_count != len(expected_tables):
            problems.append(f"_datasets 共 {metadata_count} 表，清单共 {len(expected_tables)} 表")
        if actual_fields != manifest["source_columns"]:
            problems.append(f"源字段位置总数 {actual_fields}，清单为 {manifest['source_columns']}")
        if source_total != manifest["source_rows"]:
            problems.append(f"源总行数 {source_total}，清单为 {manifest['source_rows']}")
    finally:
        conn.close()

    if pristine:
        if database.stat().st_size != manifest["database_bytes"]:
            problems.append("SQLite 文件字节数与初始清单不符")
        if sha256_file(database) != manifest["database_sha256"]:
            problems.append("SQLite 文件 SHA256 与初始清单不符")
    return {
        "ok": not problems,
        "quick_check": integrity,
        "tables": len(expected_tables),
        "current_rows": actual_total,
        "source_rows": source_total,
        "source_columns": actual_fields,
        "source_sha256_verified": source is not None,
        "pristine_verified": pristine,
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=PROJECT_ROOT / "data" / "cd80.sqlite3")
    parser.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data" / "import_manifest.json")
    parser.add_argument("--source", type=Path, help="如需核对 CSV 当前 SHA256，指定源目录")
    parser.add_argument("--pristine", action="store_true", help="核对初始库行数和整个 SQLite 文件 SHA256；发生增删改后不再适用")
    args = parser.parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        result = check(args.database.resolve(), manifest, args.source.resolve() if args.source else None, args.pristine)
    except (OSError, sqlite3.Error, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
