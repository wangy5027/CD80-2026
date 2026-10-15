"""Small data-only assertion language; never evaluates author code."""
import re
from datetime import datetime
from .contracts import canonical


def validate_assertions(rules):
    if not isinstance(rules, list) or not 1 <= len(rules) <= 40:
        raise ValueError('断言数量应为1至40。')
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {'path', 'op', 'value'}:
            raise ValueError('断言需要 path、op、value。')
        if not isinstance(rule['path'], list) or len(rule['path']) > 12 or any(type(x) not in (str, int) or (isinstance(x, int) and x < 0) for x in rule['path']):
            raise ValueError('断言路径无效。')
        op, value = rule['op'], rule['value']
        if not isinstance(op, str) or op not in {'eq', 'type', 'minItems', 'requiredKeys', 'eachType', 'date'}:
            raise ValueError('不支持的断言。')
        if op in {'type', 'eachType'} and value not in ('object', 'array', 'string', 'number', 'integer', 'boolean', 'null'):
            raise ValueError('类型断言无效。')
        if op == 'minItems' and (type(value) is not int or value < 0):
            raise ValueError('数组长度无效。')
        if op == 'requiredKeys' and (not isinstance(value, list) or not value or any(not isinstance(v, str) for v in value)):
            raise ValueError('字段断言无效。')
        if op == 'date' and value not in ('iso-date', 'iso-datetime'):
            raise ValueError('日期断言无效。')


def matches_type(value, kind):
    return {'object': type(value) is dict, 'array': type(value) is list, 'string': type(value) is str,
            'number': type(value) in (int, float), 'integer': type(value) is int,
            'boolean': type(value) is bool, 'null': value is None}.get(kind, False)


def evaluate(content, rules):
    validate_assertions(rules)
    failures = []
    for index, rule in enumerate(rules):
        try:
            value = content
            for part in rule['path']:
                if isinstance(value, dict) and isinstance(part, str): value = value[part]
                elif isinstance(value, list) and type(part) is int: value = value[part]
                else: raise ValueError()
            op, expected = rule['op'], rule['value']
            if op == 'eq': ok = canonical(value) == canonical(expected)
            elif op == 'type': ok = matches_type(value, expected)
            elif op == 'minItems': ok = isinstance(value, list) and len(value) >= expected
            elif op == 'requiredKeys': ok = isinstance(value, dict) and all(k in value for k in expected)
            elif op == 'eachType': ok = isinstance(value, list) and all(matches_type(v, expected) for v in value)
            else:
                ok = isinstance(value, str) and bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) if expected == 'iso-date' else re.match(r'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', value))
                if ok: datetime.fromisoformat(value.replace('Z', '+00:00'))
            if not ok: failures.append(index)
        except (KeyError, IndexError, TypeError, ValueError): failures.append(index)
    return failures


def sample_passed(content, sample):
    if 'expected' not in sample and not sample.get('assertions'): return False
    if 'expected' in sample and canonical(content) != canonical(sample['expected']): return False
    return not sample.get('assertions') or not evaluate(content, sample['assertions'])
