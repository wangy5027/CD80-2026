"""HTTP CRUD API for the imported CD80 SQLite database."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import sqlite3
import threading
from typing import Any, Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.body_limit import JsonBodyLimitMiddleware
from app.config import Settings, get_settings
from app.db import connection_scope, initialize_database, quote_identifier
from app.schema import BatchWriteRequest, FilterSpec, QueryRequest, WriteRequest


@dataclass(frozen=True)
class TableInfo:
    name: str
    title: str
    row_count: int
    source_rows: int
    columns: tuple[dict[str, Any], ...]

    @property
    def column_types(self) -> dict[str, str]:
        return {column["name"]: column["data_type"].upper() for column in self.columns}


def _invalid(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _get_table(connection: sqlite3.Connection, name: str) -> TableInfo:
    # Only catalogued source tables may pass through to an SQL identifier.
    dataset = connection.execute(
        "SELECT name,title,row_count,source_rows FROM _datasets WHERE name=?",
        (name,),
    ).fetchone()
    if dataset is None or not name.startswith("cd80_"):
        raise HTTPException(status_code=404, detail="数据表不存在")
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=503, detail="数据表元数据与数据库不一致")
    columns = tuple(
        dict(row)
        for row in connection.execute(
            "SELECT name,ordinal,data_type,logical_type,description "
            "FROM _columns WHERE table_name=? ORDER BY ordinal", (name,)
        ).fetchall()
    )
    actual = {
        row["name"] for row in connection.execute(
            f"PRAGMA table_info({quote_identifier(name)})"
        ).fetchall()
    }
    if "_row_id" not in actual or not columns or not all(
        column["name"] in actual and column["name"] != "_row_id"
        for column in columns
    ):
        raise HTTPException(status_code=503, detail="数据表字段元数据与数据库不一致")
    return TableInfo(
        name=dataset["name"],
        title=dataset["title"],
        row_count=dataset["row_count"],
        source_rows=dataset["source_rows"],
        columns=columns,
    )


def _valid_field(table: TableInfo, name: str) -> str:
    if name == "_row_id":
        return "INTEGER"
    data_type = table.column_types.get(name)
    if data_type is None:
        raise _invalid(f"未知字段：{name}")
    if data_type not in {"TEXT", "INTEGER", "REAL"}:
        raise HTTPException(status_code=503, detail=f"字段 {name} 类型不受支持")
    return data_type


def _typed_value(value: Any, data_type: str, field: str) -> Any:
    if value is None:
        return None
    if data_type == "TEXT":
        if type(value) is not str:
            raise _invalid(f"字段 {field} 需要文本值")
        return value
    if data_type == "INTEGER":
        if type(value) is not int or not -(2**63) <= value < 2**63:
            raise _invalid(f"字段 {field} 需要 64 位整数")
        return value
    if data_type == "REAL":
        if type(value) not in (int, float):
            raise _invalid(f"字段 {field} 需要有限数值")
        try:
            number = float(value)
        except OverflowError as exc:
            raise _invalid(f"字段 {field} 需要有限数值") from exc
        if not math.isfinite(number):
            raise _invalid(f"字段 {field} 需要有限数值")
        return number
    raise HTTPException(status_code=503, detail="字段类型不受支持")


def _selected_columns(table: TableInfo, requested: str | list[str] | None) -> list[str]:
    if requested is None:
        return ["_row_id", *(column["name"] for column in table.columns)]
    if isinstance(requested, str):
        names = [part.strip() for part in requested.split(",")]
    else:
        names = requested
    if not names or any(not isinstance(name, str) or not name for name in names):
        raise _invalid("columns 不能为空")
    if len(names) != len(set(names)):
        raise _invalid("columns 不能有重复字段")
    for name in names:
        _valid_field(table, name)
    return names


def _where_filters(table: TableInfo, filters: list[FilterSpec]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for item in filters:
        data_type = _valid_field(table, item.field)
        field_sql = quote_identifier(item.field)
        operator = item.op
        value = item.value
        if operator == "isnull":
            if value is not None and type(value) is not bool:
                raise _invalid("isnull 的 value 必须是 true 或 false")
            clauses.append(f"{field_sql} IS {'NOT ' if value is False else ''}NULL")
        elif operator == "in":
            if not isinstance(value, list) or not 1 <= len(value) <= 1000:
                raise _invalid("in 的 value 必须是 1 到 1000 项的数组")
            typed = [_typed_value(part, data_type, item.field) for part in value]
            nonnull = [part for part in typed if part is not None]
            include_null = len(nonnull) != len(typed)
            if nonnull:
                placeholders = ",".join("?" for _ in nonnull)
                clause = f"{field_sql} IN ({placeholders})"
                params.extend(nonnull)
                if include_null:
                    clause = f"({clause} OR {field_sql} IS NULL)"
            else:
                clause = f"{field_sql} IS NULL"
            clauses.append(clause)
        elif operator == "contains":
            if data_type != "TEXT" or type(value) is not str:
                raise _invalid("contains 仅接受文本字段和文本值")
            clauses.append(f"instr({field_sql}, ?) > 0")
            params.append(value)
        elif operator in {"eq", "ne"} and value is None:
            clauses.append(f"{field_sql} IS {'NOT ' if operator == 'ne' else ''}NULL")
        else:
            if value is None:
                raise _invalid(f"{operator} 不能比较 NULL")
            typed = _typed_value(value, data_type, item.field)
            sql_op = {
                "eq": "=", "ne": "!=", "gt": ">", "gte": ">=",
                "lt": "<", "lte": "<=",
            }[operator]
            clauses.append(f"{field_sql} {sql_op} ?")
            params.append(typed)
    return " AND ".join(clauses), params


def _query_rows(
    connection: sqlite3.Connection, table: TableInfo, options: QueryRequest
) -> dict[str, Any]:
    if options.after_id is not None and (
        options.sort_by != "_row_id" or options.order != "asc" or options.offset != 0
    ):
        raise _invalid("after_id 只支持 _row_id 升序且 offset=0")
    _valid_field(table, options.sort_by)
    columns = _selected_columns(table, options.columns)
    where, params = _where_filters(table, options.filters)
    count_where = where
    count_params = list(params)
    if options.after_id is not None:
        where = f"({where}) AND _row_id > ?" if where else "_row_id > ?"
        params.append(options.after_id)

    # _row_id is fetched internally even when the client projected it out.
    projection = ["_row_id", *(name for name in columns if name != "_row_id")]
    select_sql = ", ".join(quote_identifier(name) for name in projection)
    order_sql = options.order.upper()
    sort_sql = quote_identifier(options.sort_by)
    tie_breaker = "" if options.sort_by == "_row_id" else f", _row_id {order_sql}"
    sql = (
        f"SELECT {select_sql} FROM {quote_identifier(table.name)}"
        + (f" WHERE {where}" if where else "")
        + f" ORDER BY {sort_sql} {order_sql}{tie_breaker} LIMIT ? OFFSET ?"
    )
    fetched = connection.execute(
        sql, [*params, options.limit + 1, options.offset]
    ).fetchall()
    has_more = len(fetched) > options.limit
    fetched = fetched[: options.limit]
    data = [{name: row[name] for name in columns} for row in fetched]
    total: int | None = None
    if options.include_total:
        if not options.filters:
            total = table.row_count
        else:
            count_sql = f"SELECT COUNT(*) FROM {quote_identifier(table.name)}"
            if count_where:
                count_sql += f" WHERE {count_where}"
            total = connection.execute(count_sql, count_params).fetchone()[0]
    next_after_id = (
        fetched[-1]["_row_id"]
        if has_more and fetched and options.sort_by == "_row_id" and options.order == "asc"
        else None
    )
    return {
        "data": data,
        "pagination": {
            "limit": options.limit,
            "offset": options.offset,
            "has_more": has_more,
            "next_after_id": next_after_id,
            "total": total,
        },
    }


def _validated_data(table: TableInfo, data: dict[str, Any]) -> dict[str, Any]:
    if not data:
        raise _invalid("data 至少需要一个字段")
    result: dict[str, Any] = {}
    for field, value in data.items():
        if field == "_row_id":
            raise _invalid("_row_id 是内部主键，不能写入或修改")
        data_type = _valid_field(table, field)
        result[field] = _typed_value(value, data_type, field)
    return result


def _row_by_id(connection: sqlite3.Connection, table: TableInfo, row_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        f"SELECT * FROM {quote_identifier(table.name)} WHERE _row_id=?", (row_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def _audit(
    connection: sqlite3.Connection, table: TableInfo, row_id: int,
    action: str, before: dict[str, Any] | None, after: dict[str, Any] | None,
) -> None:
    connection.execute(
        "INSERT INTO _audit_log"
        "(occurred_at,table_name,row_id,action,before_json,after_json) "
        "VALUES (?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(), table.name, row_id, action,
            json.dumps(before, ensure_ascii=False, allow_nan=False) if before is not None else None,
            json.dumps(after, ensure_ascii=False, allow_nan=False) if after is not None else None,
        ),
    )


def _insert_row(connection: sqlite3.Connection, table: TableInfo, data: dict[str, Any]) -> dict[str, Any]:
    fields = list(data)
    field_sql = ",".join(quote_identifier(field) for field in fields)
    placeholders = ",".join("?" for _ in fields)
    cursor = connection.execute(
        f"INSERT INTO {quote_identifier(table.name)} ({field_sql}) VALUES ({placeholders})",
        [data[field] for field in fields],
    )
    row_id = cursor.lastrowid
    result = _row_by_id(connection, table, row_id)
    assert result is not None
    _audit(connection, table, row_id, "insert", None, result)
    return result


def _key_matches(provided: str | None, expected: str | None) -> bool:
    if provided is None or expected is None:
        return False
    # Digest comparison avoids exposing key length through compare_digest.
    return hmac.compare_digest(
        hashlib.sha256(provided.encode()).digest(),
        hashlib.sha256(expected.encode()).digest(),
    )


def create_app() -> FastAPI:
    settings: Settings = get_settings()
    init_lock = threading.Lock()
    initialized = False

    def ensure_initialized() -> None:
        nonlocal initialized
        if initialized:
            return
        with init_lock:
            if not initialized:
                initialize_database(settings.db_path, settings.query_timeout_seconds)
                initialized = True

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        ensure_initialized()
        yield

    api = FastAPI(
        title="成都八零授权数据 API",
        version="1.0.0",
        description="以 SQLite 提供比赛授权数据的查询与受保护的增删改接口。",
        lifespan=lifespan,
    )
    api.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key"],
    )
    api.add_middleware(JsonBodyLimitMiddleware)

    def need_read(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if settings.read_api_key is None:
            return
        if _key_matches(x_api_key, settings.read_api_key) or _key_matches(
            x_api_key, settings.admin_api_key
        ):
            return
        raise HTTPException(status_code=401, detail="读取需要有效的 X-API-Key")

    def need_admin(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if settings.admin_api_key is None:
            raise HTTPException(status_code=403, detail="写入未启用：未配置管理密钥")
        if not _key_matches(x_api_key, settings.admin_api_key):
            raise HTTPException(status_code=401, detail="写入需要有效的 X-API-Key")

    def get_connection():
        try:
            ensure_initialized()
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # Each invocation owns a new connection; the context closes it when
        # FastAPI finishes this request, including on endpoint exceptions.
        with connection_scope(settings.db_path, settings.query_timeout_seconds) as connection:
            yield connection

    @api.exception_handler(sqlite3.IntegrityError)
    async def integrity_error_handler(_, __: sqlite3.IntegrityError):
        return JSONResponse(status_code=409, content={"detail": "写入违反数据表约束"})

    @api.exception_handler(sqlite3.OperationalError)
    async def operational_error_handler(_, exc: sqlite3.OperationalError):
        detail = str(exc).lower()
        if "interrupt" in detail:
            return JSONResponse(status_code=504, content={"detail": "数据库查询超时"})
        if "locked" in detail or "busy" in detail:
            return JSONResponse(status_code=503, content={"detail": "数据库暂时繁忙"})
        return JSONResponse(status_code=503, content={"detail": "数据库操作失败"})

    @api.get("/health", tags=["system"])
    def health(connection: sqlite3.Connection = Depends(get_connection)):
        tables = connection.execute("SELECT COUNT(*) FROM _datasets").fetchone()[0]
        return {"status": "ok", "database": "ok", "tables": tables}

    @api.get("/api/v1/tables", dependencies=[Depends(need_read)], tags=["read"])
    def list_tables(connection: sqlite3.Connection = Depends(get_connection)):
        rows = connection.execute(
            "SELECT name,title,row_count,source_rows,source_file,imported_at "
            "FROM _datasets WHERE substr(name,1,5)='cd80_' ORDER BY name"
        ).fetchall()
        data = [dict(row) for row in rows]
        return {"data": data, "total": len(data)}

    @api.get("/api/v1/tables/{table}/schema", dependencies=[Depends(need_read)], tags=["read"])
    def table_schema(table: str, connection: sqlite3.Connection = Depends(get_connection)):
        info = _get_table(connection, table)
        sample_rows = connection.execute(
            f"SELECT * FROM {quote_identifier(table)} ORDER BY _row_id LIMIT 20"
        ).fetchall()
        fields = [
            {
                "name": "_row_id", "type": "INTEGER", "logical_type": "internal",
                "description": "数据库内部行号；源数据中的 id/ID 保留为普通字段",
                "example": sample_rows[0]["_row_id"] if sample_rows else None,
            }
        ]
        for column in info.columns:
            name = column["name"]
            example = next((row[name] for row in sample_rows if row[name] is not None), None)
            fields.append(
                {
                    "name": name,
                    "type": column["data_type"],
                    "logical_type": column["logical_type"],
                    "description": column["description"],
                    "example": example,
                }
            )
        indexes = []
        for index in connection.execute(f"PRAGMA index_list({quote_identifier(table)})"):
            index_name = index["name"]
            index_columns = [
                row["name"]
                for row in connection.execute(f"PRAGMA index_info({quote_identifier(index_name)})")
            ]
            indexes.append(
                {"name": index_name, "unique": bool(index["unique"]), "columns": index_columns}
            )
        return {
            "table": info.name,
            "title": info.title,
            "row_count": info.row_count,
            "source_rows": info.source_rows,
            "fields": fields,
            "indexes": indexes,
        }

    @api.get("/api/v1/tables/{table}/rows", dependencies=[Depends(need_read)], tags=["read"])
    def list_rows(
        table: str,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
        after_id: Annotated[int | None, Query(ge=0, le=9_223_372_036_854_775_807)] = None,
        sort_by: str = "_row_id",
        order: str = "asc",
        columns: str | None = None,
        filters: str | None = None,
        include_total: bool = False,
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        if filters is None:
            parsed_filters = []
        else:
            try:
                parsed_filters = json.loads(filters)
            except json.JSONDecodeError as exc:
                raise _invalid("filters 必须是 JSON 数组") from exc
        try:
            options = QueryRequest.model_validate(
                {
                    "limit": limit, "offset": offset, "after_id": after_id,
                    "sort_by": sort_by, "order": order, "columns": columns,
                    "filters": parsed_filters, "include_total": include_total,
                }
            )
        except ValidationError as exc:
            raise _invalid(f"查询参数无效：{exc.error_count()} 处错误") from exc
        return _query_rows(connection, _get_table(connection, table), options)

    @api.post("/api/v1/tables/{table}/query", dependencies=[Depends(need_read)], tags=["read"])
    def query_rows(
        table: str, body: QueryRequest,
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        return _query_rows(connection, _get_table(connection, table), body)

    @api.get("/api/v1/tables/{table}/rows/{row_id}", dependencies=[Depends(need_read)], tags=["read"])
    def get_row(
        table: str, row_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        info = _get_table(connection, table)
        row = _row_by_id(connection, info, row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"data": row}

    @api.post(
        "/api/v1/tables/{table}/rows", status_code=201,
        dependencies=[Depends(need_admin)], tags=["write"],
    )
    def create_row(
        table: str, body: WriteRequest,
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        info = _get_table(connection, table)
        data = _validated_data(info, body.data)
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = _insert_row(connection, info, data)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return {"data": row}

    @api.post(
        "/api/v1/tables/{table}/rows/batch", status_code=201,
        dependencies=[Depends(need_admin)], tags=["write"],
    )
    def create_rows_batch(
        table: str, body: BatchWriteRequest,
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        info = _get_table(connection, table)
        rows = [_validated_data(info, row) for row in body.rows]
        connection.execute("BEGIN IMMEDIATE")
        try:
            inserted = [_insert_row(connection, info, row) for row in rows]
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return {"data": inserted, "inserted": len(inserted)}

    @api.patch(
        "/api/v1/tables/{table}/rows/{row_id}",
        dependencies=[Depends(need_admin)], tags=["write"],
    )
    def update_row(
        table: str, row_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)], body: WriteRequest,
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        info = _get_table(connection, table)
        data = _validated_data(info, body.data)
        connection.execute("BEGIN IMMEDIATE")
        try:
            before = _row_by_id(connection, info, row_id)
            if before is None:
                raise HTTPException(status_code=404, detail="记录不存在")
            assignments = ",".join(f"{quote_identifier(field)}=?" for field in data)
            connection.execute(
                f"UPDATE {quote_identifier(table)} SET {assignments} WHERE _row_id=?",
                [*data.values(), row_id],
            )
            after = _row_by_id(connection, info, row_id)
            assert after is not None
            _audit(connection, info, row_id, "update", before, after)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return {"data": after}

    @api.delete(
        "/api/v1/tables/{table}/rows/{row_id}", status_code=204,
        dependencies=[Depends(need_admin)], tags=["write"],
    )
    def delete_row(
        table: str, row_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
        connection: sqlite3.Connection = Depends(get_connection),
    ):
        info = _get_table(connection, table)
        connection.execute("BEGIN IMMEDIATE")
        try:
            before = _row_by_id(connection, info, row_id)
            if before is None:
                raise HTTPException(status_code=404, detail="记录不存在")
            connection.execute(
                f"DELETE FROM {quote_identifier(table)} WHERE _row_id=?", (row_id,)
            )
            _audit(connection, info, row_id, "delete", before, None)
            connection.execute("COMMIT")
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        return Response(status_code=204)

    return api


app = create_app()
