"""Pure conversion and connector protocol helpers shared by migration packages."""
from datetime import date
from decimal import Decimal


def day(value):
    text = str(value).strip()
    if text.endswith('.0'):
        text = text[:-2]
    if len(text) == 8 and text.isdigit():
        text = text[:4] + '-' + text[4:6] + '-' + text[6:]
    return date.fromisoformat(text).isoformat()


def number(value):
    if value is None or str(value).strip() in ('', 'NULL', 'null'):
        return None
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('DATA_AMBIGUOUS: 非有限数值')
    return str(value)


def code(value, short=False):
    value = value.upper()
    if short:
        if len(value) != 6 or not value.isdigit():
            raise ValueError('请输入6位证券代码')
    elif len(value.split('.')[0]) != 6 or not value.split('.')[0].isdigit() or value.split('.')[-1] not in ('SH', 'SZ', 'BJ', 'OF'):
        raise ValueError('证券代码必须含明确市场后缀')
    return value


def scan(table, columns, filters=(), sort=None):
    return {'table': table, 'columns': list(columns), 'filters': list(filters),
            'order_by': [] if not sort else [{'field': sort, 'direction': 'desc'}], 'page_size': 100}


def collect(plans, responses):
    """Replay a finite multi-query plan, continuing each immutable snapshot."""
    position, datasets = 0, []
    for plan in plans:
        rows, cursor = [], None
        while True:
            if position == len(responses):
                args = dict(plan)
                operation = args.pop('_operation', 'query_v2')
                if cursor:
                    args['cursor'] = cursor
                return {'request': {'connection': 'cd80.local-data', 'operation': operation, 'arguments': args}}, None
            response = responses[position]
            position += 1
            rows.extend(response['data'])
            cursor = response.get('pagination', {}).get('next_cursor')
            if not cursor:
                break
        datasets.append(rows)
    return None, datasets


def eq(field, value):
    return {'field': field, 'op': 'eq', 'value': value}


def ceiling(table, field):
    return {'_operation': 'lookup_v2', 'table': table, 'columns': [field], 'filters': [], 'sort_by': field, 'order': 'desc', 'limit': 1}


def bounds(args):
    exact, start, end = args.get('date'), args.get('start_date'), args.get('end_date')
    if exact and (start or end):
        raise ValueError('指定日期和日期区间不能同时使用')
    start, end = (day(exact), day(exact)) if exact else (day(start) if start else None, day(end) if end else None)
    if start and end and start > end:
        raise ValueError('起始日期晚于结束日期')
    return start, end


def result(source, args, records, dates=(), units=None, warnings=(), as_of=None):
    offset, limit = args.get('offset', 0), args.get('limit', 20)
    selected = records[offset:offset + limit]
    all_dates = list(dates)
    dates = all_dates[offset:offset + limit]
    return {'_page_records':records, '_page_dates':all_dates, 'result_schema': 'financial-result/1', 'source': source, 'sources': [{'source': source, 'as_of': as_of}],
            'requested': args, 'records': selected, 'as_of': as_of,
            'returned_range': {'start': min(dates) if dates else None, 'end': max(dates) if dates else None},
            'units': {k:{'unit':v,'status':'source_raw' if '源' in v or '待核对' in v else 'verified'} for k,v in (units or {}).items()},
            'warnings': [{'code':'COVERAGE_NOTICE','message':w,'fields':[]} if isinstance(w,str) else w for w in warnings],
            'coverage': {'status': 'unknown', 'basis':'bounded_source_records', 'period_state':'not_applicable', 'retrieval_complete': True, 'market_completeness': 'unknown'},
            'pagination': {'returned': len(selected), 'has_more': offset + limit < len(records), 'next_offset': offset + limit if offset + limit < len(records) else None}}
