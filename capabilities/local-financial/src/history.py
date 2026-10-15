"""Unadjusted candles. Calendar period selection follows aggregation."""
import calendar
from datetime import date, timedelta
from decimal import Decimal
from migration_common import bounds, ceiling, code, collect, day, eq, number, result, scan


def period_end(value, period):
    value = date.fromisoformat(value)
    if period == 'week':
        value += timedelta(days=6 - value.weekday())
    elif period == 'month':
        value = value.replace(day=calendar.monthrange(value.year, value.month)[1])
    return value.isoformat()


def candles(rows, period):
    groups, seen = {}, {}
    for raw in rows:
        stamp = day(raw['trade_date'])
        converted = {k: number(raw.get(k)) for k in ('open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount')}
        if stamp in seen:
            if seen[stamp] != converted:
                raise ValueError('DATA_AMBIGUOUS: 同日行情存在冲突版本')
            continue
        seen[stamp] = converted
        groups.setdefault(period_end(stamp, period), []).append({'date': stamp, **converted})
    output, previous = [], None
    for end, members in sorted(groups.items()):
        members.sort(key=lambda r: r['date'])
        first, last = members[0], members[-1]
        row = {'date': end, 'actual_start': first['date'], 'actual_end': last['date'], 'open': first['open'], 'close': last['close']}
        for field, operation in [('high', max), ('low', min), ('vol', sum), ('amount', sum)]:
            values = [m[field] for m in members]
            row[field] = str(operation(Decimal(v) for v in values)) if all(v is not None for v in values) else None
        row['pre_close'] = first['pre_close'] if period == 'day' else previous
        base = row['pre_close']
        row['pct_chg'] = str((Decimal(row['close']) / Decimal(base) - 1) * 100) if row['close'] is not None and base is not None and Decimal(base) != 0 else None
        previous = row['close']
        row['market_completeness'] = 'unknown'
        output.append(row)
    return output


def run(payload):
    args = payload['input']
    symbol = code(args['code'])
    period, asset = args.get('period', 'day'), args.get('asset', 'stock')
    table = {'stock': 'cd80_daily', 'etf': 'cd80_etf_fund_daily', 'index': 'cd80_index_dd'}[asset]
    fields = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'pct_chg', 'vol', 'amount']
    request, datasets = collect([ceiling(table, 'trade_date'), scan(table, fields, [eq('ts_code', symbol)], 'trade_date')], payload['responses'])
    if request:
        return request
    raw = datasets[1]
    dataset_latest = day(datasets[0][0]['trade_date']) if datasets[0] else None
    latest = max((day(r['trade_date']) for r in raw), default=None)
    rows = candles(raw, period)
    start, end = bounds(args)
    rows = [dict(r, code=symbol, period_state='open' if dataset_latest and r['date'] > dataset_latest else 'closed') for r in rows if (not start or r['date'] >= start) and (not end or r['date'] <= end)]
    rows.reverse()
    units = {'prices': '元', 'vol': '手', 'amount': '千元', 'pct_chg': '百分比'} if asset == 'stock' else {'prices': '源表原值', 'vol': '单位待核对', 'amount': '单位待核对', 'pct_chg': '百分比'}
    value = result(table, args, rows, [r['date'] for r in rows], units, ['交易日完整性未核实；不补价格或成交量。'], dataset_latest)
    value['security_as_of'] = latest
    value['sources'][0]['as_of_basis'] = 'dataset_records'
    return {'result': value}
