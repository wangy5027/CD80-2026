"""Static, owner-independent version test cases. Never run a new asset here."""
import copy
import math

from .assertions import validate_assertions
from .contracts import CapabilityError, canonical, fail
from .packages import has_secret

MAX_CASES = 10
MAX_BYTES = 128 * 1024


def _sample(schema, depth=0):
    """Suggest bounded JSON input from declarations, never from code output."""
    if not isinstance(schema, dict) or depth > 8:
        return None
    for key in ('const', 'default'):
        if key in schema:
            return copy.deepcopy(schema[key])
    choices = schema.get('enum')
    if isinstance(choices, list) and choices:
        return copy.deepcopy(choices[0])
    kind = schema.get('type')
    if isinstance(kind, list):
        kind = next((item for item in kind if item != 'null'), kind[0] if kind else None)
    if kind == 'object' or isinstance(schema.get('properties'), dict):
        properties = schema.get('properties', {})
        required = schema.get('required', [])
        if not isinstance(required, list):
            return {}
        return {key: _sample(properties.get(key), depth + 1) for key in required[:24]
                if isinstance(key, str) and key in properties}
    if kind == 'array':
        count = schema.get('minItems', 0)
        count = min(count, 4) if type(count) is int and count >= 0 else 0
        return [_sample(schema.get('items'), depth + 1) for _ in range(count)]
    if kind == 'string':
        length = schema.get('minLength', 0)
        length = min(length, 64) if type(length) is int and length >= 0 else 0
        return '示' * max(1, length)
    if kind in {'integer', 'number'}:
        minimum = schema.get('minimum', 0)
        if type(minimum) not in {int, float} or not math.isfinite(minimum):
            minimum = 0
        if kind == 'integer':
            return math.ceil(minimum)
        return minimum
    if kind == 'boolean':
        return False
    return None


def _suggested_input(manifest):
    if manifest.get('kind') != 'tool':
        return {}
    candidate = _sample(manifest.get('tool', {}).get('inputSchema'), 0)
    return candidate if isinstance(candidate, dict) else {}


def _skill_prompt(manifest):
    name = str(manifest.get('name') or '此 Skill').strip()[:128]
    description = str(manifest.get('description') or name).strip()[:1000]
    return f'请按照「{name}」的方法处理以下示例任务：{description}。说明结果、依据和无法确定的部分。'


def _candidate_expectation(manifest, example):
    if manifest['kind'] == 'skill':
        prompt = example.get('prompt') or _skill_prompt(manifest)
        return {'mode': 'manual_review', 'prompt': prompt,
                'criteria': example.get('criteria') or ''}
    if 'expected' in example:
        return {'mode': 'json_equals', 'value': copy.deepcopy(example['expected'])}
    if example.get('assertions'):
        try:
            validate_assertions(example['assertions'])
        except ValueError:
            return None
        return {'mode': 'assertions', 'assertions': copy.deepcopy(example['assertions'])}
    return None


def _from_examples(manifest):
    output = []
    for example in manifest.get('examples', [])[:MAX_CASES]:
        if not isinstance(example, dict):
            continue
        has_files = bool(example.get('filePaths'))
        expectation = _candidate_expectation(manifest, example)
        if has_files:
            # Package paths cannot stand in for the recipient's file grants.
            expectation = ({'mode': 'manual_review', 'prompt': expectation['prompt'], 'criteria': ''}
                           if manifest['kind'] == 'skill' and isinstance(expectation, dict) else None)
        value = example.get('input')
        output.append({'label': str(example.get('title') or example.get('id') or '原用例')[:200],
                       'input': copy.deepcopy(value) if isinstance(value, dict) else _suggested_input(manifest),
                       'fileIds': [], 'expectation': expectation})
    return output


def _normalize(manifest, cases):
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        fail('INVALID_REQUEST', '请提供1至10组测试用例。')
    output = []
    for case in cases:
        if (not isinstance(case, dict) or set(case) - {'label', 'input', 'fileIds', 'expectation'} or
                not isinstance(case.get('label'), str) or not 1 <= len(case['label'].strip()) <= 200 or
                not isinstance(case.get('input'), dict)):
            fail('INVALID_REQUEST', '用例需要名称和输入对象。')
        file_ids = case.get('fileIds', [])
        if (not isinstance(file_ids, list) or len(file_ids) > 8 or
                any(not isinstance(key, str) or not key for key in file_ids) or
                len(set(file_ids)) != len(file_ids)):
            fail('INVALID_REQUEST', '用例文件授权列表无效。')
        expectation = case.get('expectation')
        if expectation is not None:
            if not isinstance(expectation, dict):
                fail('INVALID_REQUEST', '用例预期格式无效。')
            mode = expectation.get('mode')
            if manifest['kind'] == 'skill':
                if (mode != 'manual_review' or set(expectation) - {'mode', 'prompt', 'criteria'} or
                        not isinstance(expectation.get('prompt'), str) or not expectation['prompt'].strip() or
                        not isinstance(expectation.get('criteria'), str)):
                    fail('INVALID_REQUEST', 'Skill用例需要测试问题及验收标准文本。')
            elif mode == 'json_equals':
                if set(expectation) - {'mode', 'value'} or 'value' not in expectation:
                    fail('INVALID_REQUEST', 'Tool用例需要明确预期JSON。')
            elif mode == 'assertions':
                if set(expectation) - {'mode', 'assertions'}:
                    fail('INVALID_REQUEST', 'Tool用例断言格式无效。')
                try:
                    validate_assertions(expectation.get('assertions'))
                except ValueError as error:
                    fail('INVALID_REQUEST', str(error))
            else:
                fail('INVALID_REQUEST', 'Tool用例需要JSON预期或结果断言。')
        output.append({'label': case['label'].strip(), 'input': copy.deepcopy(case['input']),
                       'fileIds': list(file_ids), 'expectation': copy.deepcopy(expectation)})
    try:
        encoded = canonical(output).encode('utf-8')
    except (TypeError, ValueError, RecursionError):
        fail('INVALID_REQUEST', '用例必须是可保存的有限JSON。')
    if len(encoded) > MAX_BYTES:
        fail('LIMIT_EXCEEDED', '版本测试用例超过128 KiB。', 413)
    if has_secret({'testCases': encoded}):
        fail('EMBEDDED_SECRET', '用例疑似包含凭据，请移除后再保存。')
    return output


def prepare_test_cases(manifest, files, supplied=None):
    """Freeze explicit cases or derive the original without execution.

    files is accepted for the same package/snapshot call shape; no file is read
    to infer an expected output. Package example paths remain in the manifest.
    """
    if supplied is not None:
        return _normalize(manifest, supplied)
    examples = _from_examples(manifest)
    if examples:
        try:
            return _normalize(manifest, examples)
        except CapabilityError:
            # A malformed or private package example blocks neither retention
            # nor the ability to supply a clean case later.
            pass
    fallback = [{'label': '原用例', 'input': _suggested_input(manifest), 'fileIds': [],
                 'expectation': ({'mode': 'manual_review', 'prompt': _skill_prompt(manifest),
                                  'criteria': ''} if manifest.get('kind') == 'skill' else None)}]
    try:
        return _normalize(manifest, fallback)
    except CapabilityError:
        return _normalize(manifest, [{'label': '原用例', 'input': {}, 'fileIds': [],
                                      'expectation': ({'mode': 'manual_review',
                                                       'prompt': '请按照此 Skill 处理一项用户明确提供的任务。',
                                                       'criteria': ''} if manifest.get('kind') == 'skill' else None)}])


def version_test_cases(library, owner, version):
    """Read legacy submitted expectations, never observed outputs or status."""
    if version.get('testCases'):
        return copy.deepcopy(version['testCases'])
    records = sorted(
        (record for record in library.store.list(owner, 'verification')
         if record.get('origin') == 'validation_suite' and
         record.get('assetId') == version['assetId'] and
         record.get('versionId') == version['versionId'] and
         isinstance(record.get('cases'), list) and record['cases']),
        key=lambda record: (record.get('createdAt') or '', record.get('verificationId') or ''),
        reverse=True)
    for record in records:
        original = record['cases'][0]
        if not isinstance(original, dict):
            continue
        candidate = {key: copy.deepcopy(original.get(key)) for key in
                     ('label', 'input', 'fileIds', 'expectation')}
        try:
            return prepare_test_cases(version['manifest'], {}, [candidate])
        except CapabilityError:
            continue
    return prepare_test_cases(version['manifest'], {})
