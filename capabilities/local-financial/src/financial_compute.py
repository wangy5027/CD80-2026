"""Finite calculations over an explicitly granted, complete local result."""
import json
from pathlib import Path
from decimal import Decimal


def numeric(value):
    if value is None or str(value).strip() in ('', 'NULL', 'null'):
        return None
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError('DATA_AMBIGUOUS: 非有限数值')
    return result


def calculate(args, content):
    if content.get('pagination', {}).get('has_more') or content.get('coverage', {}).get('retrieval_complete') is not True:
        raise ValueError('DATA_INCOMPLETE: 仅计算明确读取完整的授权结果')
    rows = content['records']
    if not isinstance(rows, list) or len(rows) > 10000:
        raise ValueError('LIMIT_EXCEEDED: 输入记录无效或超限')
    source = content['source']
    if args['operation'] == 'financial_compare':
        if source not in ('cd80_stock_fin_indicator', 'cd80_stock_fin_income', 'cd80_stock_fin_cashflow', 'cd80_stock_fin_balancesheet'):
            raise ValueError('UNSUPPORTED_OPERATION: 仅支持本地财务结果')
        pairs = [[r for r in rows if r['report_period'] == args[p]] for p in ('base_period', 'target_period')]
        if any(len(p) != 1 for p in pairs):
            raise ValueError('DATA_AMBIGUOUS: 每期必须恰好一个明确报告版本')
        before, after = pairs[0][0], pairs[1][0]
        if before['code'] != after['code'] or args['base_period'] >= args['target_period']:
            raise ValueError('DATA_AMBIGUOUS: 证券或比较期间不一致')
        # A cumulative flow can be compared only with the same period length.
        if source != 'cd80_stock_fin_balancesheet' and args['base_period'][5:] != args['target_period'][5:]:
            raise ValueError('DATA_AMBIGUOUS: 累计指标只允许相同月日跨年比较')
        left, right = before['source_fields'], after['source_fields']
        if any(left.get(k) != right.get(k) for k in ('report_type', 'comp_type')):
            raise ValueError('DATA_AMBIGUOUS: 报表口径不同')
        field = args['field']
        if field in ('ts_code', 'ann_date', 'f_ann_date', 'end_date', 'report_type', 'comp_type', 'update_flag') or field not in left or field not in right:
            raise ValueError('UNSUPPORTED_OPERATION: 不是已返回的数值指标')
        a, b = numeric(left[field]), numeric(right[field])
        difference = b-a if a is not None and b is not None else None
        pct = difference/abs(a)*100 if difference is not None and a else None
        records = [{'code':before['code'], 'field':field, 'base_period':args['base_period'], 'target_period':args['target_period'],
                    'base':None if a is None else str(a), 'target':None if b is None else str(b),
                    'difference':None if difference is None else str(difference), 'change_percent':None if pct is None else str(pct)}]
    elif args['operation'] == 'lhb_summary':
        if source != 'cd80_lhb_summary':
            raise ValueError('UNSUPPORTED_OPERATION: 仅支持本地龙虎榜明细')
        unique, groups = {}, {}
        for row in rows:
            if str(row.get('ISVALID')) not in ('1', '1.0'):
                raise ValueError('DATA_AMBIGUOUS: 存在无效或未核实有效性的记录')
            identity = row.get('ID')
            if identity is None:
                raise ValueError('DATA_AMBIGUOUS: 缺少源记录ID')
            if identity in unique:
                if unique[identity] != row:
                    raise ValueError('DATA_AMBIGUOUS: 相同ID内容冲突')
                continue
            unique[identity] = row
            key = tuple(row.get(f) for f in ('SYMBOL', 'EXCHANGE', 'date', 'CHGTYPE', 'TRANSDIR'))
            groups.setdefault(key, []).append(row)
        records = []
        for key, members in groups.items():
            out = dict(zip(('code', 'exchange', 'date', 'reason', 'direction'), key))
            out['source_rows'] = len(members)
            for field in ('BUYAMT', 'SALEAMT'):
                values = [numeric(r.get(field)) for r in members]
                out[field] = None if any(v is None for v in values) else str(sum(values))
            records.append(out)
    else:
        raise ValueError('UNSUPPORTED_OPERATION')
    return {'result_schema':'financial-result/1','requested':args,'source':source,'sources':content.get('sources',[{'source':source}]),'as_of':content.get('as_of'),
            'records':records,'units':content.get('units', {}),'pagination':{'has_more':False,'next_cursor':None,'returned':len(records)},
            'returned_range':content.get('returned_range',{'start':None,'end':None}),
            'coverage':{'retrieval_complete':True, 'market_completeness':'unknown'},
            'warnings':[{'code':'INPUT_SCOPE_ONLY','fields':[],'message':'仅汇总授权输入记录；不代表全市场、机构数量或单季财务值；百分比以基期绝对值为分母，零基期返回空。'}]}


def run(args):
    name = args['file']
    if name.startswith('/input/'):
        name=name[len('/input/'):]
    if Path(name).name != name or '/' in name or '\\' in name:
        raise ValueError('只允许授权输入文件名')
    value = json.loads((Path('/input')/name).read_text(encoding='utf-8'))
    return calculate(args, value.get('content', value))
