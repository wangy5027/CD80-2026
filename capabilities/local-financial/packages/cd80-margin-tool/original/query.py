"""Package-owned financial logic. Executed only inside WASI."""
from datetime import date
from decimal import Decimal, InvalidOperation


def day(value):
    text = str(value)
    if text.endswith('.0'): text = text[:-2]
    if len(text) == 8 and text.isdigit(): text = text[:4]+'-'+text[4:6]+'-'+text[6:]
    return date.fromisoformat(text).isoformat()


def number(value):
    if value is None or str(value).strip() in ('', 'NULL', 'null'): return None
    result = Decimal(str(value))
    if not result.is_finite(): raise ValueError('非有限数值')
    return str(result)


def execute(kind, payload):
    args, responses = payload['input'], payload['responses']
    if kind == 'news':
        if not responses:
            return {'request': {'connection': 'cd80.news', 'operation': 'latest', 'arguments': {
                'source': args.get('source', 'eastmoney'), 'q': args.get('keyword', ''),
                'limit': args.get('limit', 5), 'offset': args.get('offset', 0)}}}
        data = responses[0]
        return {'result': {'source': 'akshare', 'provider_source': data['source'], 'records': data['items'],
            'as_of': data.get('latest_published_at'), 'cache': data['cache'], 'warning': data.get('warning'),
            'pagination': data['pagination'], 'coverage': '当前来源缓存范围，不是全量历史新闻；无自动换源。'}}
    exact = args.get('date')
    start, end = args.get('start_date'), args.get('end_date')
    if exact and (start or end): raise ValueError('指定日期与日期区间不能同时使用')
    for value in (exact, start, end):
        if value: day(value)
    if start and end and day(start) > day(end): raise ValueError('开始日期晚于结束日期')
    if kind == 'history':
        asset = args.get('asset', 'stock')
        table = {'stock': 'cd80_daily', 'etf': 'cd80_etf_fund_daily', 'index': 'cd80_index_dd'}[asset]
        date_field, code_field = 'trade_date', 'ts_code'
        columns = ['ts_code','trade_date','open','high','low','close','pre_close','pct_chg','vol','amount']
    elif kind == 'announcement':
        table, date_field, code_field = 'cd80_anns_d', 'announcement_date', 'ticker'
        columns = ['ticker','security_name','announcement_date','announcement_title','url']
    else:
        table, date_field, code_field = 'cd80_margin_data', 'TRADEDATE', 'SYMBOL'
        columns = ['SYMBOL','EXCHANGE','TRADEDATE','FINBUYAMT','FINREPAYAMT','FINBALANCE','FINMRGHBAL']
    code = args['code'].upper()
    if kind == 'margin':
        if len(code) != 6 or not code.isdigit(): raise ValueError('两融证券代码必须为6位数字')
    elif '.' not in code or code.split('.')[-1] not in ('SH','SZ','BJ') or len(code.split('.')[0]) != 6 or not code.split('.')[0].isdigit():
        raise ValueError('证券代码应含明确交易所，例如600000.SH')
    filters = []
    def request_query(limit, fields, selected, offset=0):
        return {'request': {'connection': 'cd80.local-data', 'operation': 'query', 'arguments': {
            'table': table, 'limit': limit, 'offset': offset, 'columns': fields,
            'sort_by': date_field, 'order': 'desc', 'filters': selected}}}
    # Query dataset ceiling separately; this is not a per-security continuity claim.
    if not responses: return request_query(1, [date_field], [])
    latest = responses[0]['data']
    as_of = day(latest[0][date_field]) if latest else None
    if not as_of:
        return {'result': {'source': table, 'as_of': None, 'records': [], 'coverage': '数据集为空'}}
    def encode(value):
        normalized = day(value)
        if kind == 'announcement': return normalized
        raw = normalized.replace('-', '')
        return raw + '.0' if kind == 'history' and args.get('asset') == 'index' else raw
    filters = [{'field': code_field, 'op': 'eq', 'value': code}]
    if exact or not (start or end): filters.append({'field': date_field, 'op': 'eq', 'value': encode(exact or as_of)})
    else:
        if start: filters.append({'field': date_field, 'op': 'gte', 'value': encode(start)})
        if end: filters.append({'field': date_field, 'op': 'lte', 'value': encode(end)})
    if kind == 'announcement' and args.get('keyword'):
        filters.append({'field':'announcement_title','op':'contains','value':args['keyword']})
    if len(responses) == 1: return request_query(args.get('limit',20), columns, filters, args.get('offset',0))
    data = responses[1]
    records = []
    for row in data['data']:
        if kind == 'history':
            item = {'code': row['ts_code'], 'date': day(row[date_field])}
            for field in ('open','high','low','close','pre_close','pct_chg','vol','amount'): item[field] = number(row[field])
        elif kind == 'announcement':
            item = {'code':row['ticker'],'date':day(row[date_field]),'name':row['security_name'],
                    'title':row['announcement_title'],'url':row['url']}
        else:
            buy, repay = number(row['FINBUYAMT']), number(row['FINREPAYAMT'])
            item = {'code':row['SYMBOL'],'exchange':row['EXCHANGE'],'date':day(row[date_field]),
                    'financing_buy':buy,'financing_repay':repay,
                    'financing_net_buy':str(Decimal(buy)-Decimal(repay)) if buy is not None and repay is not None else None,
                    'financing_balance':number(row['FINBALANCE']),'total_margin_balance':number(row['FINMRGHBAL'])}
        records.append(item)
    units = ({'prices':'元','vol':'手','amount':'千元','pct_chg':'百分比'} if kind == 'history' else
             {'amounts':'源表金额原值，展示前核对单位'} if kind == 'margin' else {})
    if kind == 'history' and args.get('asset','stock') != 'stock':
        units = {'prices':'源表原值','vol':'源表原值，单位待核对','amount':'源表原值，单位待核对','pct_chg':'百分比'}
    dates = [r['date'] for r in records]
    return {'result': {'source':table,'as_of':as_of,'requested':args,'records':records,'units':units,
        'returned_range':{'start':min(dates) if dates else None,'end':max(dates) if dates else None},
        'pagination':data.get('pagination',{}),
        'coverage':'仅返回本地已存在记录；不承诺逐日连续或全量覆盖。分页未结束时不得作全量汇总。',
        'warning':'未找到指定条件记录，不自动替换日期。' if not records else None}}
