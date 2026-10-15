"""Bounded factual summaries for the conversation timeline (no model narration)."""
import json
import re


def safe_details(value, depth=0):
    if depth > 4:
        return '…'
    if isinstance(value, dict):
        return {str(k): '[已隐藏]' if re.search(r'password|secret|token|credential|authorization|api.?key', str(k), re.I)
                else safe_details(v, depth + 1) for k, v in list(value.items())[:30]}
    if isinstance(value, list):
        return [safe_details(v, depth + 1) for v in value[:10]]
    return value[:500] if isinstance(value, str) else value


def result_summary(value):
    facts = []
    def scan(item, depth=0):
        if depth > 4 or len(facts) >= 6:
            return
        if isinstance(item, dict):
            for key, val in item.items():
                if key in {'rows', 'row_count', 'rowCount', 'count', 'total'} and isinstance(val, (int, float)):
                    facts.append(f'数据条数：{val}' if key in {'rows', 'row_count', 'rowCount', 'count'} else f'总数：{val}')
                elif key in {'source', 'startDate', 'endDate', 'start_date', 'end_date', 'currency', 'price_basis', 'synthetic'} and isinstance(val, (str, bool, int)):
                    label = {'source': '来源', 'synthetic': '合成数据', 'currency': '币种', 'price_basis': '价格口径'}.get(key, key)
                    facts.append(f'{label}：{str(val)[:100]}')
                elif key in {'rows', 'data', 'records'} and isinstance(val, list):
                    facts.append(f'返回 {len(val)} 条记录')
                elif isinstance(val, dict):
                    scan(val, depth + 1)
    scan(value)
    if not facts and isinstance(value, dict):
        keys = [str(k) for k in value if k not in {'artifacts', 'logs', 'sources'}]
        if keys:
            facts.append('返回字段：' + '、'.join(keys[:8]))
    return '；'.join(dict.fromkeys(facts))[:600] or '已获得执行结果，可展开查看。'


def action_label(action, bridge, registry):
    kind, name = action.get('type'), action.get('name')
    if kind == 'code':
        return '执行临时代码'
    if kind == 'read':
        return '读取已有数据'
    if name in bridge.tools:
        return bridge.tools[name][0]['manifest']['name']
    labels = {'library_catalog': '查看可用能力', 'library_load': '加载能力', 'library_resource': '读取能力资料', 'library_script': '执行技能脚本'}
    if name in labels:
        if name in {'library_load', 'library_script'}:
            asset_id = (action.get('arguments') or {}).get('assetId')
            try:
                asset = bridge.lib.asset(bridge.owner, asset_id)
                return labels[name] + ' · ' + asset['name']
            except Exception:
                pass
        return labels[name]
    spec = registry.get(name) if isinstance(name, str) else None
    return getattr(spec, 'title', None) or name or '执行操作'


def error_summary(error):
    text = str(error)
    if 'SyntaxError' in text:
        return '代码存在语法错误，本次执行未成功。'
    if 'KeyError' in text:
        return '代码读取了不存在的字段，需要依据实际数据修正。'
    if '超时' in text or 'deadline' in text.lower():
        return '本次请求超时。'
    return text.splitlines()[0][:180] if text else '本次执行未成功。'
