"""Small SQLite access layer; every API request owns its connection."""

from collections.abc import Iterator
from contextlib import contextmanager
import sqlite3
import time
from pathlib import Path
from urllib.parse import quote


AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS _audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_id INTEGER NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    before_json TEXT,
    after_json TEXT
)
"""


def quote_identifier(identifier: str) -> str:
    """Quote an identifier selected from the database metadata whitelist."""
    return '"' + identifier.replace('"', '""') + '"'


def open_connection(db_path: Path, timeout_seconds: float) -> sqlite3.Connection:
    # mode=rw guarantees that a misspelled or absent path never creates a DB.
    uri = "file:" + quote(str(db_path), safe="/") + "?mode=rw"
    # FastAPI may run a sync generator dependency, its route, and dependency
    # cleanup on different worker threads. The connection stays private to one
    # request and these stages run sequentially; disable only SQLite's thread
    # identity guard so the route can use it and cleanup can close it safely.
    connection = sqlite3.connect(
        uri, uri=True, timeout=5.0, isolation_level=None, check_same_thread=False
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA foreign_keys=ON")
    deadline = time.monotonic() + timeout_seconds
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    return connection


def initialize_database(db_path: Path, timeout_seconds: float) -> None:
    if not db_path.is_file():
        raise RuntimeError(
            f"未找到 SQLite 数据库：{db_path}。请先运行 CSV 导入脚本。"
        )
    with connection_scope(db_path, timeout_seconds) as connection:
        required = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('_datasets', '_columns')"
        ).fetchall()
        if {row["name"] for row in required} != {"_datasets", "_columns"}:
            raise RuntimeError("数据库缺少 _datasets/_columns 元数据，请先运行 CSV 导入脚本。")
        # WAL allows concurrent website reads while an authorized write commits.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(AUDIT_DDL)
        connection.execute(
            "CREATE INDEX IF NOT EXISTS _audit_log_table_row_idx "
            "ON _audit_log(table_name, row_id, audit_id)"
        )


@contextmanager
def connection_scope(db_path: Path, timeout_seconds: float) -> Iterator[sqlite3.Connection]:
    connection = open_connection(db_path, timeout_seconds)
    try:
        yield connection
    finally:
        connection.close()
