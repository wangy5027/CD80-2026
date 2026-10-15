"""Business mappings executed in the package, never in the host connector."""
from datetime import date, timedelta
import re
from decimal import Decimal
from migration_common import bounds, ceiling, code, collect, day, eq, number, result, scan


def single(kind, payload):
    args = payload['input']
    symbol = code(args['code'], short=kind in ('margin', 'lhb'))
    specs = {
        'announcement': ('cd80_anns_d', 'ticker', 'announcement_date', ['ticker', 'security_name', 'announcement_date', 'announcement_title', 'url']),
        'margin': ('cd80_margin_data', 'SYMBOL', 'TRADEDATE', ['SYMBOL', 'EXCHANGE', 'TRADEDATE', 'FINBUYAMT', 'FINREPAYAMT', 'FINBALANCE', 'FINMRGHBAL']),
        'lhb': ('cd80_lhb_summary', 'SYMBOL', 'TRADEDATE', ['ID', 'SYMBOL', 'EXCHANGE', 'TRADEDATE', 'BIZSUNITCODE', 'BIZSUNITNAME', 'TRANSDIR', 'BUYAMT', 'SALEAMT', 'AMOUNT', 'CHGTYPE', 'ISVALID'])}
    table, cf, df, columns = specs[kind]
    filters = [eq(cf, symbol)]
    if args.get('market'):
        filters.append(eq('EXCHANGE', args['market']))
    if kind == 'announcement' and args.get('keyword'):
        filters.append({'field': 'announcement_title', 'op': 'contains', 'value': args['keyword']})
    request, data = collect([ceiling(table, df), scan(table, columns, filters, df)], payload['responses'])
    if request:
        return request
    latest = day(data[0][0][df]) if data[0] else None
    start, end = bounds(args)
    if not start and not end:
        end = latest
        start = (date.fromisoformat(latest) - timedelta(days=29)).isoformat() if latest and kind == 'announcement' else latest
    rows, seen = [], {}
    for raw in data[1]:
        stamp = day(raw[df])
        if (start and stamp < start) or (end and stamp > end):
            continue
        if kind == 'announcement':
            row = {'code': symbol, 'date': stamp, 'name': raw['security_name'], 'title': raw['announcement_title'], 'url': raw['url']}
        elif kind == 'margin':
            buy, repay = number(raw['FINBUYAMT']), number(raw['FINREPAYAMT'])
            row = {'code': symbol, 'date': stamp, 'exchange': raw['EXCHANGE'], 'financing_buy': buy, 'financing_repay': repay,
                   'financing_net_buy': str(Decimal(buy) - Decimal(repay)) if buy is not None and repay is not None else None,
                   'financing_balance': number(raw['FINBALANCE']), 'total_margin_balance': number(raw['FINMRGHBAL'])}
            key = (stamp, str(raw['EXCHANGE']))
            if key in seen:
                if seen[key] != row:
                    raise ValueError('DATA_AMBIGUOUS: 同证券日期两融数据有冲突，不能按导入行号选最新')
                continue
            seen[key] = row
        else:
            row = {'code': symbol, 'date': stamp, **raw}
        rows.append(row)
    if kind in ('margin', 'lhb') and not args.get('market') and len({str(r.get('exchange', r.get('EXCHANGE'))) for r in rows}) > 1:
        raise ValueError('DATA_AMBIGUOUS: 同代码涉及多个市场，请明确源表market代码')
    rows.sort(key=lambda r: r['date'], reverse=True)
    value = result(table, args, rows, [r['date'] for r in rows], {'amounts': '源表原值，单位待核对'} if kind != 'announcement' else {}, as_of=latest)
    value['effective_range'] = {'start': start, 'end': end}
    return {'result': value}


SEARCH = {
    'stock': ('cd80_stock_basic', 'ts_code', ['symbol', 'name'], ['ts_code', 'symbol', 'name', 'industry']),
    'etf': ('cd80_etf_basic', 'ts_code', ['csname', 'cname'], ['ts_code', 'csname', 'cname', 'exchange']),
    'fund': ('cd80_fund_basic', 'ts_code', ['name'], ['ts_code', 'name', 'market']),
    'industry': ('cd80_ci_index_member', 'l1_code', ['l1_name', 'l2_name', 'l3_name'], ['l1_code', 'l1_name', 'l2_code', 'l2_name', 'l3_code', 'l3_name'])}


def search(payload):
    args = payload['input']
    keyword = args['keyword'].strip()
    if not keyword:
        raise ValueError('关键词不能为空')
    categories = [args['category']] if 'category' in args else args.get('asset_types', ['stock'])
    plans, labels = [], []
    for category in categories:
        table, cf, names, fields = SEARCH[category]
        for field in [cf, *names]:
            plans.append(scan(table, fields, [{'field': field, 'op': 'contains', 'value': keyword}]))
            labels.append(category)
    request, datasets = collect(plans, payload['responses'])
    if request:
        return request
    rows, seen = [], set()
    for category, data in zip(labels, datasets):
        for raw in data:
            if category == 'industry':
                for level in (1,2,3):
                    if not re.fullmatch(r'CI\d+\.CI',str(raw['l'+str(level)+'_code'])) or re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',str(raw['l'+str(level)+'_name'])):
                        raise ValueError('DATA_AMBIGUOUS: 行业源表字段疑似错位，不能把证券代码或名称当作行业目录')
                candidates = [{'code': raw['l'+str(i)+'_code'], 'name': raw['l'+str(i)+'_name'], 'type': 'industry'} for i in (1, 2, 3) if keyword in str(raw['l'+str(i)+'_name']) or keyword in str(raw['l'+str(i)+'_code'])]
            else:
                candidates = [{'code': raw['ts_code'], 'name': raw.get('name', raw.get('csname')), 'type': category,
                               'market': raw.get('exchange', raw.get('market', str(raw['ts_code']).split('.')[-1])), 'source': SEARCH[category][0]}]
            for row in candidates:
                key = tuple(sorted((k, str(v)) for k, v in row.items()))
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
    return {'result': result('local-directory', args, rows, warnings=['返回候选，不自动确认证券；不支持事件语义搜索。'])}


FINANCE = {
    'indicator': ('cd80_stock_fin_indicator', ['ts_code', 'ann_date', 'end_date', 'eps', 'roe', 'debt_to_assets', 'netprofit_margin']),
    'income': ('cd80_stock_fin_income', ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type', 'comp_type', 'update_flag', 'total_revenue', 'revenue', 'n_income', 'n_income_attr_p']),
    'cashflow': ('cd80_stock_fin_cashflow', ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type', 'comp_type', 'update_flag', 'n_cashflow_act', 'n_cashflow_inv_act', 'n_cash_flows_fnc_act']),
    'balance': ('cd80_stock_fin_balancesheet', ['ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type', 'comp_type', 'update_flag', 'total_assets', 'total_liab', 'total_hldr_eqy_inc_min_int'])}


def finance(payload):
    args = payload['input']
    table, defaults = FINANCE[args.get('kind', 'indicator')]
    fields = args.get('fields', defaults)
    if any(f not in defaults for f in fields):
        raise ValueError('UNSUPPORTED_OPERATION: 字段尚未核实；请选择当前kind已支持字段')
    fields = list(dict.fromkeys([*defaults[:(3 if args.get('kind', 'indicator') == 'indicator' else 7)], 'end_date', *fields]))
    request, datasets = collect([scan(table, fields, [eq('ts_code', code(args['code']))], 'end_date')], payload['responses'])
    if request:
        return request
    rows = []
    for raw in datasets[0]:
        end = day(raw['end_date'])
        if args.get('start_period') and end < day(args['start_period']):
            continue
        if args.get('end_period') and end > day(args['end_period']):
            continue
        disclosure = next((raw.get(k) for k in ('f_ann_date', 'ann_date') if raw.get(k) is not None and str(raw[k]).strip() not in ('', 'NULL', 'null')), None)
        published = day(disclosure) if disclosure and str(disclosure) not in ('NULL', 'null') else None
        if args.get('as_of') and (not published or published > day(args['as_of'])):
            continue
        rows.append({'code': args['code'], 'report_period': end, 'disclosed_at': published, 'source_fields': raw})
    return {'result': result(table, args, rows, [r['report_period'] for r in rows], units={'financial_values':'源表原值，单位待核对'}, warnings=['保留源报表版本和口径，不将累计值称单季；冲突版本需明确选择后比较。', '资产负债表覆盖有限。'] if args.get('kind') == 'balance' else ['保留源报告版本，不自动混合累计与单季。'])}


def valuation(payload):
    args = payload['input']
    symbol = code(args['code'])
    asset = args.get('asset', 'stock')
    price_table = {'stock': 'cd80_daily', 'etf': 'cd80_etf_fund_daily', 'index': 'cd80_index_dd'}[asset]
    plans = [scan(price_table, ['ts_code', 'trade_date', 'close'], [eq('ts_code', symbol)], 'trade_date')]
    if asset == 'stock':
        plans.append(scan('cd80_daily_basic', ['ts_code', 'trade_date', 'pe', 'pe_ttm', 'pb', 'turnover_rate', 'total_mv', 'circ_mv'], [eq('ts_code', symbol)], 'trade_date'))
    request, datasets = collect(plans, payload['responses'])
    if request:
        return request
    rows = []
    for plan, dataset in zip(plans, datasets):
        ordered = sorted(dataset, key=lambda r: day(r['trade_date']), reverse=True)
        matches = [r for r in ordered if not args.get('date') or day(r['trade_date']) == day(args['date'])]
        if matches:
            latest = day(matches[0]['trade_date'])
            same_day = [r for r in matches if day(r['trade_date']) == latest]
            if any(r != same_day[0] for r in same_day[1:]):
                raise ValueError('DATA_AMBIGUOUS: 同证券同日期数据冲突，不能按导入顺序选取')
        for raw in matches[:1]:
            rows.append({'code': symbol, 'date': day(raw['trade_date']), 'source': plan['table'], **{k: number(v) for k, v in raw.items() if k not in ('ts_code', 'trade_date')}})
    value=result(price_table, args, rows, [r['date'] for r in rows], {'values': '源字段口径，单位未核实者不换算'}, ['历史收盘数据，不是实时行情；各来源日期独立。'])
    value['sources']=[{'source':r['source'],'as_of':r['date'],'as_of_basis':'security_records'} for r in rows]
    return {'result':value}


MACRO = {'cpi': ('cd80_cn_cpi', 'month', ['month', 'nt_val', 'nt_yoy', 'nt_mom']),
         'lpr': ('cd80_cn_lpr', 'date', ['date', '1y', '5y']),
         'yield': ('cd80_yc_cb', 'trade_date', ['trade_date', 'curve_code', 'curve_type', 'maturity_years', 'yield_percent'])}


def macro(payload):
    args = payload['input']
    table, df, fields = MACRO[args['indicator']]
    filters = []
    if args['indicator'] == 'yield':
        if not args.get('curve_code') or 'maturity_years' not in args:
            raise ValueError('收益率查询必须提供曲线及期限')
        filters = [eq('curve_code', args['curve_code']), eq('maturity_years', args['maturity_years'])]
        if 'curve_type' in args:
            filters.append(eq('curve_type',args['curve_type']))
    # Require bounded dates; source formats are explicit per dataset.
    for param, op in [('start_date', 'gte'), ('end_date', 'lte')]:
        if args.get(param):
            stamp = day(args[param])
            if args['indicator'] == 'cpi':
                stamp = stamp[:7] + '-01'
            filters.append({'field': df, 'op': 'date_' + op, 'value': stamp})
    request, datasets = collect([scan(table, fields, filters, df)], payload['responses'])
    if request:
        return request
    rows, seen = [], {}
    for raw in datasets[0]:
        stamp = str(raw[df])
        if stamp in seen:
            if seen[stamp] != raw:
                raise ValueError('DATA_AMBIGUOUS: 同期指标存在不同记录，不能猜测版本')
            continue
        seen[stamp] = raw
        normalized=day(stamp[:4]+'-'+stamp[4:6]+'-01') if args['indicator']=='cpi' and len(stamp)==6 else day(stamp)
        row={k:(v if k in (df,'curve_code','curve_type') else number(v)) for k,v in raw.items()}
        rows.append({'date':normalized,'frequency':'monthly' if args['indicator']=='cpi' else 'daily',**row})
    dates=[r['date'] for r in rows]
    return {'result': result(table, args, rows, dates, units={'values':'源表原值，单位待核对'}, warnings=['有限本地指标目录；频率和数值口径按字段保留。'],as_of=None)}


EVENTS = {
    'ipo_listing': ('cd80_ipo', 'LISTDATE', None, ['SESNAME', 'LISTDATE', 'ONLSUBCODE']),
    'dividend_ex': ('cd80_stock_fin_dividend', 'ex_date', 'ts_code', ['ts_code', 'ann_date', 'ex_date', 'div_proc']),
    'dividend_payment': ('cd80_stock_fin_dividend', 'pay_date', 'ts_code', ['ts_code', 'ann_date', 'pay_date', 'div_proc']),
    'bond_redemption': ('cd80_bond_cb_redeem', 'call_date', 'ts_code', ['ts_code', 'ann_date', 'call_date', 'is_call']),
    'conversion_change': ('cd80_cb_price_chg', 'change_date', 'ticker', ['ticker', 'publication_date', 'change_date', 'conversion_price_before', 'conversion_price_after'])}


def events(payload):
    args = payload['input']
    table, df, cf, fields = EVENTS[args['event_type']]
    start, end = day(args['start_date']), day(args['end_date'])
    if start > end:
        raise ValueError('起始日期晚于结束日期')
    filters = []
    if args.get('code'):
        if not cf:
            raise ValueError('UNSUPPORTED_OPERATION: IPO表没有可靠证券代码映射')
        filters.append(eq(cf, args['code']))
    filters.extend([{'field': df, 'op': 'date_gte', 'value': start}, {'field': df, 'op': 'date_lte', 'value': end}])
    # A bounded snapshot is required before normalizing mixed legacy date formats.
    request, datasets = collect([scan(table, fields, filters, df)], payload['responses'])
    if request:
        return request
    rows = []
    for raw in datasets[0]:
        if raw[df] is None or str(raw[df]).strip() in ('', 'NULL', 'null'):
            continue
        stamp = day(raw[df])
        if start <= stamp <= end:
            rows.append({'event_type': args['event_type'], 'date': stamp, 'source_fields': raw})
    rows.sort(key=lambda r: r['date'], reverse=True)
    return {'result': result(table, args, rows, [r['date'] for r in rows], warnings=['仅已有事件记录；申购代码不等于股票代码，不推断未来事件。'])}
