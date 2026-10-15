"""Bounded immutable read snapshots; no database migration or caller SQL.

Snapshots own their bytes, expire after two minutes and are bound to the caller
scope and exact query. A later database write cannot change an existing page.
"""
import hashlib
import json
import secrets
import sqlite3
import threading
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import quote_identifier
from .schema import FilterSpec


class Order(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str
    direction: Literal['asc', 'desc'] = 'asc'


class ScanFilter(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str
    op: Literal['eq', 'gte', 'lte', 'contains', 'date_gte', 'date_lte']
    value: str | int | float


class Scan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    columns: list[str] = Field(min_length=1, max_length=40)
    filters: list[ScanFilter] = Field(default_factory=list, max_length=12)
    order_by: list[Order] = Field(default_factory=list, max_length=2)
    page_size: int = Field(default=100, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=256)


class Metric(BaseModel):
    model_config = ConfigDict(extra='forbid')
    field: str
    op: Literal['count', 'sum', 'min', 'max']
    alias: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')


class Aggregate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    filters: list[FilterSpec] = Field(default_factory=list, max_length=12)
    group_by: list[str] = Field(default_factory=list, max_length=3)
    metrics: list[Metric] = Field(min_length=1, max_length=8)
    limit: int = Field(default=100, ge=1, le=100)
    order_by: list[Order] = Field(default_factory=list, max_length=2)


def fail(code, message, status=422):
    raise HTTPException(status_code=status, detail={'code': code, 'message': message})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class Snapshots:
    def __init__(self):
        self.lock = threading.RLock()
        self.entries = {}

    def page(self, scope, query, rows=None, cursor=None):
        fingerprint = hashlib.sha256(encoded([scope, query])).hexdigest()
        with self.lock:
            self.entries = {k: v for k, v in self.entries.items() if v['expires'] > time.monotonic()}
            if cursor:
                value = self.entries.get(cursor)
                if not value or value['fingerprint'] != fingerprint:
                    fail('CURSOR_EXPIRED', '游标已失效或不属于本次查询，请重新开始。', 409)
                snapshot, offset = value['snapshot'], value['offset']
            else:
                raw = encoded(rows)
                if len(raw) > 4 * 1024 * 1024:
                    fail('LIMIT_EXCEEDED', '查询快照超过4MiB，请缩小范围。', 413)
                snapshots = {id(v['snapshot']): v['snapshot'] for v in self.entries.values()}
                if sum(v['size'] for v in snapshots.values()) + len(raw) > 32 * 1024 * 1024 or len(self.entries) >= 6400:
                    fail('READ_BUDGET_EXCEEDED', '查询快照容量已满，请稍后重试。', 429)
                snapshot = {'rows': rows, 'size': len(raw), 'digest': hashlib.sha256(raw).hexdigest(), 'expires': time.monotonic() + 120}
                offset = 0
            end = offset + query['page_size']
            result = snapshot['rows'][offset:end]
            next_cursor = None
            if end < len(snapshot['rows']):
                # Stable replay of a page must not allocate unlimited cursor entries.
                next_cursor = value.get('next') if cursor else None
                if not next_cursor:
                    if len(self.entries) >= 6400:
                        fail('READ_BUDGET_EXCEEDED', '查询游标容量已满，请稍后重试。', 429)
                    next_cursor = secrets.token_urlsafe(32)
                    self.entries[next_cursor] = {'snapshot': snapshot, 'offset': end, 'fingerprint': fingerprint, 'expires': snapshot['expires']}
                    if cursor:
                        value['next'] = next_cursor
            response = {'data': result, 'pagination': {'has_more': next_cursor is not None, 'next_cursor': next_cursor, 'returned': len(result)},
                        'snapshot': {'sha256': snapshot['digest'], 'rows': len(snapshot['rows']), 'immutable': True},
                        'coverage': {'retrieval_complete': next_cursor is None, 'market_completeness': 'unknown'}}
            if len(encoded(response)) > 512 * 1024:
                fail('LIMIT_EXCEEDED', '查询页超过512KiB，请减少字段或条数。', 413)
            return response


def register(api, need_read, get_connection, get_table, valid_field, where_filters):
    snapshots = Snapshots()

    def read(connection, table_name, columns, filters, order):
        table = get_table(connection, table_name)
        for field in [*columns, *(o.field for o in order)]:
            valid_field(table, field)
        regular = [FilterSpec(**f.model_dump()) for f in filters if not f.op.startswith('date_')]
        where, params = where_filters(table, regular)
        def normalized_day(value):
            if value is None or str(value).strip() in ('', 'NULL', 'null'):
                return None
            text = str(value).strip().removesuffix('.0')
            if len(text) == 6 and text.isdigit():
                text += '01'
            if len(text) == 8 and text.isdigit():
                text = text[:4] + '-' + text[4:6] + '-' + text[6:]
            # Malformed stored dates fail the query; do not silently drop them.
            return date.fromisoformat(text).isoformat()
        connection.create_function('cd80_day', 1, normalized_day, deterministic=True)
        clauses = [where] if where else []
        for item in filters:
            if item.op.startswith('date_'):
                valid_field(table, item.field)
                try:
                    stamp = date.fromisoformat(str(item.value)).isoformat()
                except ValueError:
                    fail('INVALID_INPUT', '日期筛选需要YYYY-MM-DD。')
                clauses.append('cd80_day(' + quote_identifier(item.field) + ') ' + ('>=' if item.op == 'date_gte' else '<=') + ' ?')
                params.append(stamp)
        where = ' AND '.join(clauses)
        ordering = [quote_identifier(o.field) + ' ' + o.direction.upper() for o in order]
        ordering.append('"_row_id" ASC')
        sql = 'SELECT ' + ','.join(quote_identifier(c) for c in columns) + ' FROM ' + quote_identifier(table_name)
        if where:
            sql += ' WHERE ' + where
        sql += ' ORDER BY ' + ','.join(ordering) + ' LIMIT 10001'
        try:
            rows = [dict(row) for row in connection.execute(sql, params).fetchall()]
        except sqlite3.OperationalError as error:
            if 'user-defined function' in str(error):
                fail('DATA_AMBIGUOUS', '源日期含无法规范化的值，请先核实数据。')
            raise
        if len(rows) > 10000:
            fail('READ_BUDGET_EXCEEDED', '匹配记录超过10000条，请缩小日期或证券范围；未返回截断计算。', 429)
        return rows

    @api.post('/api/v1/tables/{table}/scan', dependencies=[Depends(need_read)], tags=['read'])
    def scan(table: str, body: Scan, connection=Depends(get_connection), x_cd80_scope: str = Header(default='direct')):
        query = {'table': table, **body.model_dump(exclude={'cursor'})}
        rows = None if body.cursor else read(connection, table, body.columns, body.filters, body.order_by)
        return snapshots.page(x_cd80_scope, query, rows, body.cursor)

    @api.post('/api/v1/tables/{table}/aggregate', dependencies=[Depends(need_read)], tags=['read'])
    def aggregate(table: str, body: Aggregate, connection=Depends(get_connection)):
        aliases = [m.alias for m in body.metrics]
        if any(o.field not in [*aliases,*body.group_by] for o in body.order_by):
            fail('INVALID_INPUT', '排序字段必须是分组键或聚合别名。')
        if len(set(aliases)) != len(aliases) or set(aliases) & set(body.group_by):
            fail('INVALID_INPUT', '聚合字段名称冲突。')
        columns = list(dict.fromkeys([*body.group_by, *(m.field for m in body.metrics)]))
        rows = read(connection, table, columns, body.filters, [])
        groups = {}
        for row in rows:
            key = tuple(row[f] for f in body.group_by)
            groups.setdefault(key, []).append(row)
        if not rows and not body.group_by:
            groups[()] = []
        if len(groups) > body.limit:
            fail('READ_BUDGET_EXCEEDED', '聚合组数超过限制，请缩小范围，未返回部分排名。', 429)
        result = []
        for key, members in groups.items():
            item = dict(zip(body.group_by, key))
            for metric in body.metrics:
                raw = [r[metric.field] for r in members]
                valid = [v for v in raw if v is not None and str(v).strip() not in ('', 'NULL', 'null')]
                if metric.op == 'count':
                    item[metric.alias] = len(valid)
                    continue
                try:
                    values = [Decimal(str(v)) for v in valid]
                    if not all(v.is_finite() for v in values):
                        raise InvalidOperation()
                except (InvalidOperation, ValueError):
                    fail('DATA_AMBIGUOUS', '聚合字段包含非有限数值或文本，不能猜测转换。')
                # Missing values propagate, rather than silently becoming zeros.
                item[metric.alias] = None if len(valid) != len(raw) or not values else str({'sum': sum, 'min': min, 'max': max}[metric.op](values))
            result.append(item)
        # Stable ties follow sorted group keys; nulls stay last in both directions.
        result.sort(key=lambda r: encoded([r[k] for k in body.group_by]))
        for order in reversed(body.order_by):
            present = [r for r in result if r[order.field] is not None]
            missing = [r for r in result if r[order.field] is None]
            present.sort(key=lambda r: Decimal(str(r[order.field])) if order.field in aliases else str(r[order.field]), reverse=order.direction=='desc')
            result = present+missing
        response = {'data': result, 'matched_rows': len(rows), 'processed_rows': len(rows), 'coverage': {'retrieval_complete': True, 'market_completeness': 'unknown'}}
        if len(encoded(response)) > 512 * 1024:
            fail('LIMIT_EXCEEDED', '聚合结果超过512KiB。', 413)
        return response
