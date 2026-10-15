"""Versioned reusable App definitions and explicit data bindings."""
import ast
import base64
import copy
import csv
import io
import json
import math
import re
import struct
import zipfile
import zlib
from pathlib import PurePosixPath
from xml.etree import ElementTree

from jsonschema import Draft202012Validator

from ..capability_library.contracts import canonical, fail
from ..capability_library.packages import safe_path
from ..sub_agent.media import artifact_media_type

FORMAT = 'cd80.app/2'
MAX_BYTES = 8 * 1024 * 1024
ID = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,63}$')


def schema_check(schema):
    if not isinstance(schema, dict):
        raise ValueError('Schema 必须为对象。')
    def walk(value):
        if isinstance(value, dict):
            if '$ref' in value or '$dynamicRef' in value:
                raise ValueError('Schema 不支持外部或递归引用。')
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(schema)
    Draft202012Validator.check_schema(schema)


def validate_value(value, schema, label='输入'):
    schema_check(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(e.path))
    if errors:
        fail('DELIVERY_INVALID' if label == '交付' else 'INPUT_INVALID',
             label + '不符合约定：' + '; '.join(e.message for e in errors[:8]), 422)


def at(value, path):
    if not path:
        return value
    for part in str(path).split('.'):
        try:
            value = value[int(part)] if isinstance(value, list) else value[part]
        except (KeyError, IndexError, TypeError, ValueError):
            fail('BINDING_MISSING', '找不到实际字段：' + str(path), 422)
    return value


def resource_files(items):
    if not isinstance(items, list) or len(items) > 512:
        fail('INVALID_FILES', '资源必须为最多512项的数组。')
    result = {}
    for item in items:
        if not isinstance(item, dict):
            fail('INVALID_FILES', '资源项必须是对象。')
        name = safe_path(item.get('path', ''))
        if name.casefold() == '__app_entry__.py':
            fail('INVALID_FILES', '__app_entry__.py是平台执行入口，不能作为资源覆盖。')
        if name in result:
            fail('INVALID_FILES', '资源路径重复。')
        try:
            data = item['text'].encode('utf-8') if 'text' in item else base64.b64decode(item['base64'], validate=True)
        except (KeyError, ValueError, TypeError, AttributeError):
            fail('INVALID_FILES', '资源内容编码无效。')
        result[name] = data
    if sum(map(len, result.values())) > MAX_BYTES:
        fail('LIMIT_EXCEEDED', '资源总量超过8MiB。', 413)
    return result


def _schema_input_candidate(schema, depth=0):
    """Offer an editable input from declarations only; never inspect app code."""
    if not isinstance(schema, dict) or depth > 12:
        return None
    for name in ('const', 'default'):
        if name in schema:
            return copy.deepcopy(schema[name])
    examples = schema.get('examples')
    if isinstance(examples, list) and examples:
        return copy.deepcopy(examples[0])
    options = schema.get('enum')
    if isinstance(options, list) and options:
        return copy.deepcopy(options[0])
    for branch in ('oneOf', 'anyOf'):
        choices = schema.get(branch)
        if isinstance(choices, list) and choices:
            return _schema_input_candidate(choices[0], depth + 1)
    kind = schema.get('type')
    if isinstance(kind, list):
        kind = next((value for value in kind if value != 'null'), kind[0] if kind else None)
    if not isinstance(kind, str) and kind is not None:
        return None
    if kind == 'object' or (kind is None and isinstance(schema.get('properties'), dict)):
        properties = schema.get('properties') if isinstance(schema.get('properties'), dict) else {}
        required = schema.get('required') if isinstance(schema.get('required'), list) else []
        return {key: _schema_input_candidate(properties.get(key, {}), depth + 1)
                for key in required[:24] if isinstance(key, str)}
    if kind == 'array':
        minimum = schema.get('minItems', 0)
        length = min(minimum, 10) if type(minimum) is int and minimum > 0 else 0
        item = schema.get('items') or {}
        return [_schema_input_candidate(item, depth + 1) for _ in range(length)]
    if kind == 'string':
        minimum = schema.get('minLength', 0)
        return 'x' * min(minimum, 128) if type(minimum) is int and minimum > 0 else ''
    if kind in {'integer', 'number'}:
        minimum = schema.get('minimum', 0)
        if type(minimum) not in {int, float} or (type(minimum) is float and not math.isfinite(minimum)):
            minimum = 0
        return math.ceil(minimum) if kind == 'integer' else minimum
    if kind == 'boolean':
        return False
    return None


def normalize_test_cases(cases, input_schema):
    """Keep original test metadata distinct from business outputs.

    Missing cases are represented by an editable schema-derived input and a
    null expectation. A result from running the App is never used as an oracle.
    """
    if cases is None or cases == []:
        candidate = _schema_input_candidate(input_schema)
        return [{'label': '原任务用例（待补独立预期）',
                 'input': candidate if isinstance(candidate, dict) else {},
                 'fileBindings': {}, 'expectation': None}]
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        fail('INVALID_CASES', '原任务用例须为1至10组。', 422)
    normalized = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) - {'label', 'input', 'fileBindings', 'expectation'}:
            fail('INVALID_CASES', '用例只能包含名称、输入、文件授权和独立预期。', 422)
        label = case.get('label', '原任务用例' if index == 0 else '补充用例 ' + str(index + 1))
        inputs = case.get('input', {})
        bindings = case.get('fileBindings', {})
        expectation = case.get('expectation')
        if not isinstance(label, str) or not label.strip() or len(label) > 120 or not isinstance(inputs, dict):
            fail('INVALID_CASES', '用例名称和输入格式无效。', 422)
        if not isinstance(bindings, dict) or any(not isinstance(key, str) or not key or
                not isinstance(value, str) for key, value in bindings.items()):
            fail('INVALID_CASES', '用例文件授权须为名称到文件标识的映射。', 422)
        if expectation is not None and (not isinstance(expectation, dict) or
                not isinstance(expectation.get('mode'), str) or
                expectation.get('mode') not in {'json_equals', 'fields'} or
                (expectation.get('mode') == 'json_equals' and set(expectation) != {'mode', 'value'}) or
                (expectation.get('mode') == 'fields' and
                 (set(expectation) != {'mode', 'values'} or
                  not isinstance(expectation.get('values'), dict) or not expectation['values']))):
            fail('INVALID_CASES', '用例独立预期须为明确结果、字段断言或待补状态。', 422)
        normalized.append({'label': label.strip(), 'input': copy.deepcopy(inputs),
                           'fileBindings': copy.deepcopy(bindings),
                           'expectation': copy.deepcopy(expectation)})
    return normalized


def definition(value):
    if not isinstance(value, dict):
        fail('INVALID_DEFINITION', '应用定义必须是对象。')
    try:
        if len(canonical(value).encode('utf-8')) > MAX_BYTES:
            fail('LIMIT_EXCEEDED', '应用定义超过8MiB。', 413)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        fail('INVALID_DEFINITION', '应用定义不是有效 JSON。')
    doc = copy.deepcopy(value)
    if doc.get('format', FORMAT) != FORMAT:
        fail('INVALID_DEFINITION', '不支持此应用格式。')
    doc['format'] = FORMAT
    if not isinstance(doc.get('name'), str) or not 1 <= len(doc['name'].strip()) <= 120:
        fail('INVALID_DEFINITION', '请输入不超过120字的应用名称。')
    doc.setdefault('description', '')
    doc.setdefault('inputSchema', {'type': 'object', 'properties': {}})
    doc.setdefault('nodes', [])
    doc.setdefault('rules', [])
    doc.setdefault('outputs', {})
    doc.setdefault('gaps', [])
    doc.setdefault('parameterization', [])
    doc['testCases'] = normalize_test_cases(doc.get('testCases'), doc['inputSchema'])
    if not isinstance(doc['nodes'], list) or len(doc['nodes']) > 64:
        fail('INVALID_DEFINITION', '应用最多包含64个顺序步骤。')
    for field in ('rules', 'gaps', 'parameterization'):
        if not isinstance(doc[field], list):
            fail('INVALID_DEFINITION', field + '必须为数组。')
    if any(not isinstance(rule, str) or len(rule) > 4000 for rule in doc['rules']):
        fail('INVALID_DEFINITION', '固定规则须为不超过4000字的文本。')
    issues = []
    def issue(path, message):
        issues.append({'path': path, 'message': message, 'severity': 'blocker'})
    allowed = {'format', 'name', 'description', 'inputSchema', 'rules', 'nodes', 'outputs', 'gaps', 'parameterization', 'source', 'testCases'}
    for field in set(doc) - allowed:
        issue(field, '未定义的应用字段，不能假定其控制逻辑会执行。')
    try:
        schema_check(doc['inputSchema'])
        if doc['inputSchema'].get('type') != 'object':
            issue('inputSchema', '应用输入必须为对象。')
    except Exception as error:
        issue('inputSchema', str(error)[:1000])
    if not doc['nodes']:
        issue('nodes', '请添加实际执行步骤。')
    seen = set()
    for index, node in enumerate(doc['nodes']):
        prefix = f'nodes.{index}'
        if not isinstance(node, dict):
            fail('INVALID_DEFINITION', '步骤必须为对象。')
        nid = node.get('id')
        if not isinstance(nid, str) or not ID.fullmatch(nid) or nid in seen:
            issue(prefix + '.id', '步骤标识必须唯一，使用英文字母开头的字母、数字、下划线或短横线。')
        kind = node.get('kind')
        if not isinstance(kind, str) or kind not in {'code', 'tool', 'skill'}:
            issue(prefix + '.kind', '首版仅支持code、tool、skill顺序节点。')
        if ('component' in node and node['component'] is not None and not isinstance(node['component'], dict)) or (
                'componentRef' in node and node['componentRef'] is not None and not isinstance(node['componentRef'], dict)):
            fail('INVALID_DEFINITION', '步骤组件或组件引用必须为对象。', 422)
        reference = node.get('componentRef')
        if isinstance(reference, dict) and reference and (not isinstance(reference.get('assetId'), str) or
                not isinstance(reference.get('versionId'), str) or not reference['assetId'] or not reference['versionId']):
            issue(prefix + '.componentRef', '组件引用需要本人资产ID和精确版本ID。')
        if kind == 'code' and ('component' in node or 'componentRef' in node):
            issue(prefix + '.component', '代码步骤不能包含不会执行的组件。')
        for field in {'condition', 'branches', 'parallel', 'loop', 'next', 'appRef'} & set(node):
            issue(prefix + '.' + field, '首版不支持App级分支、循环、并行或嵌套。')
        node.setdefault('title', str(nid or '未命名步骤'))
        node.setdefault('inputBindings', {})
        node.setdefault('delivery', {'schema': {}, 'files': []})
        node.setdefault('checkpoint', False)
        if not isinstance(node['checkpoint'], bool):
            issue(prefix + '.checkpoint', '检查点必须为布尔值。')
        if kind == 'code':
            code = node.get('code', '')
            try:
                if not isinstance(code, str) or len(code.encode()) > 131072:
                    raise ValueError('代码超过128KiB。')
                tree = ast.parse(code)
                if not any(isinstance(n, ast.FunctionDef) and n.name == 'run' for n in tree.body):
                    raise ValueError('代码须定义run(payload)。')
                resource_files(node.get('files', []))
            except Exception as error:
                issue(prefix + '.code', str(error)[:1000])
        if isinstance(kind, str) and kind in {'tool', 'skill'} and not node.get('component') and not node.get('componentRef'):
            issue(prefix + '.component', '缺少完整组件快照。')
        delivery = node['delivery']
        try:
            if not isinstance(delivery, dict):
                raise ValueError('交付约束必须为对象。')
            schema_check(delivery.get('schema', {}))
            if not isinstance(delivery.get('files', []), list) or not isinstance(delivery.get('evidenceFields', []), list):
                raise ValueError('文件及依据字段须为数组。')
            names = []
            for requirement in delivery.get('files', []):
                name = requirement if isinstance(requirement, str) else requirement.get('name') if isinstance(requirement, dict) else None
                if not isinstance(name, str) or safe_path(name) != name or '/' in name:
                    raise ValueError('交付文件须使用安全平面名称。')
                names.append(name)
                media = requirement.get('mediaType') if isinstance(requirement, dict) else None
                if media is not None and (not isinstance(media, str) or not re.fullmatch(r'[\w.+-]+/[\w.+-]+(?:;\s*charset=[\w-]+)?', media)):
                    raise ValueError('交付文件mediaType无效。')
            if len(set(names)) != len(names):
                raise ValueError('交付文件名不能重复。')
            if any(not isinstance(path, str) or not path for path in delivery.get('evidenceFields', [])):
                raise ValueError('依据字段须为非空字段路径。')
        except Exception as error:
            issue(prefix + '.delivery', str(error)[:1000])
        _bindings(node['inputBindings'], seen, prefix + '.inputBindings', issue)
        seen.add(nid if isinstance(nid, str) else f'invalid_{index}')
    _bindings(doc['outputs'], seen, 'outputs', issue, output=True)
    for gap in doc['gaps']:
        issue('gaps', str(gap.get('message', gap) if isinstance(gap, dict) else gap))
    return doc, issues


def _bindings(bindings, seen, prefix, issue, output=False):
    if not isinstance(bindings, dict):
        issue(prefix, '绑定必须为对象。')
        return
    for key, binding in bindings.items():
        path = prefix + '.' + key
        if not isinstance(binding, dict):
            issue(path, '来源必须显式声明from。')
            continue
        source = binding.get('from')
        if not isinstance(source, str) or source not in {'input', 'literal', 'step', 'file', 'artifact'}:
            issue(path, '不支持的输入来源。')
        elif source in {'step', 'artifact'} and (not isinstance(binding.get('stepId'), str) or binding['stepId'] not in seen):
            issue(path, '仅可引用实际存在的前序步骤。')
        elif output and source in {'input', 'file'}:
            issue(path, '最终结果必须来自已执行步骤或明确常量。')
        elif source == 'literal' and 'value' not in binding:
            issue(path, '缺少常量值。')


def delivery_check(result, files, requirements):
    validate_value(result, requirements.get('schema', {}), '交付')
    if not isinstance(files, dict) or any(not isinstance(name, str) or not isinstance(raw, bytes) for name, raw in files.items()):
        fail('DELIVERY_INVALID', '交付文件必须是名称到实际字节的映射。', 422)
    for required in requirements.get('files', []):
        name = required if isinstance(required, str) else required.get('name')
        if name not in files or not isinstance(files[name], bytes) or not files[name]:
            fail('DELIVERY_INVALID', '交付缺少可读取的文件：' + str(name), 422)
        expected = required.get('mediaType') if isinstance(required, dict) else None
        detected = artifact_media_type(name)
        if expected and expected.lower().split(';', 1)[0] != detected.lower():
            fail('DELIVERY_INVALID', '交付文件类型与声明不一致：' + name, 422)
        _file_content(name, files[name], detected)
    for path in requirements.get('evidenceFields', []):
        value = at(result, path)
        if value is None or value == '' or value == [] or value == {}:
            fail('DELIVERY_INVALID', '交付缺少实际依据：' + str(path), 422)


def _file_content(name, raw, media):
    """Parse common deliverables locally; no optional decoder or network access."""
    try:
        suffix = PurePosixPath(name).suffix.lower()
        if media == 'application/json' or suffix == '.json':
            json.loads(raw.decode('utf-8-sig'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        elif suffix in {'.csv', '.tsv'}:
            rows = list(csv.reader(io.StringIO(raw.decode('utf-8-sig')), delimiter='\t' if suffix == '.tsv' else ',', strict=True))
            if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows) or b'\x00' in raw:
                raise ValueError('invalid tabular content')
        elif suffix in {'.xml', '.svg'}:
            if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                raise ValueError('external entities are unsupported')
            root = ElementTree.fromstring(raw)
            if suffix == '.svg' and root.tag.rsplit('}', 1)[-1] != 'svg':
                raise ValueError('not SVG')
        elif media.startswith('text/'):
            if '\x00' in raw.decode('utf-8-sig'):
                raise ValueError('binary text')
        elif media == 'image/png':
            _png_content(raw)
        elif media == 'image/jpeg':
            if not raw.startswith(b'\xff\xd8\xff') or not raw.rstrip().endswith(b'\xff\xd9'):
                raise ValueError('invalid JPEG envelope')
        elif media == 'image/gif':
            if raw[:6] not in {b'GIF87a', b'GIF89a'} or len(raw) < 14 or raw[-1:] != b';':
                raise ValueError('invalid GIF envelope')
        elif media == 'image/webp':
            if raw[:4] != b'RIFF' or raw[8:12] != b'WEBP' or len(raw) < 20 or int.from_bytes(raw[4:8], 'little') + 8 != len(raw):
                raise ValueError('invalid WebP envelope')
        elif media == 'application/pdf':
            if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-1024:]:
                raise ValueError('invalid PDF envelope')
        elif suffix in {'.zip', '.docx', '.xlsx', '.pptx'}:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 2048 or sum(item.file_size for item in entries) > 64 * 1024 * 1024:
                    raise ValueError('archive expansion limit')
                if archive.testzip() is not None:
                    raise ValueError('archive CRC mismatch')
                part = {'.docx': 'word/document.xml', '.xlsx': 'xl/workbook.xml', '.pptx': 'ppt/presentation.xml'}.get(suffix)
                if part and (part not in archive.namelist() or '[Content_Types].xml' not in archive.namelist()):
                    raise ValueError('invalid office document')
    except (ValueError, UnicodeError, csv.Error, ElementTree.ParseError, zipfile.BadZipFile, RuntimeError, zlib.error, struct.error):
        fail('DELIVERY_INVALID', '交付文件内容不能按其类型读取：' + name, 422)


def _png_content(raw):
    if raw[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('invalid PNG signature')
    cursor, header, compressed, ended, palette = 8, None, bytearray(), False, None
    while cursor + 12 <= len(raw):
        length = int.from_bytes(raw[cursor:cursor + 4], 'big')
        kind, data = raw[cursor + 4:cursor + 8], raw[cursor + 8:cursor + 8 + length]
        end = cursor + length + 12
        if end > len(raw) or zlib.crc32(kind + data) & 0xffffffff != int.from_bytes(raw[end - 4:end], 'big'):
            raise ValueError('PNG chunk or CRC invalid')
        if header is None and kind != b'IHDR':
            raise ValueError('missing PNG header')
        if kind == b'IHDR':
            if header is not None or length != 13:
                raise ValueError('invalid PNG header')
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'PLTE':
            if palette is not None or not 0 < length <= 768 or length % 3:
                raise ValueError('invalid PNG palette')
            palette = length // 3
        elif kind == b'IEND':
            ended = length == 0 and end == len(raw)
            break
        cursor = end
    if not header or not ended or not compressed:
        raise ValueError('incomplete PNG')
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    valid_depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    if not width or not height or not channels or depth not in valid_depths[color] or compression or filtering or interlace not in {0, 1}:
        raise ValueError('unsupported PNG header')
    if color == 3 and (palette is None or palette > 2 ** depth):
        raise ValueError('missing PNG palette')
    passes = [(0, 0, 1, 1)] if interlace == 0 else [(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)]
    rows = []
    for x, y, dx, dy in passes:
        w, h = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if w and h:
            rows.append((((w * channels * depth + 7) // 8) + 1, h))
    expected = sum(length * count for length, count in rows)
    if expected > 64 * 1024 * 1024:
        raise ValueError('PNG expansion limit')
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(bytes(compressed), expected + 1)
    if not decoder.eof or decoder.unused_data or len(pixels) != expected:
        raise ValueError('PNG pixel data invalid')
    cursor = 0
    for length, count in rows:
        for _ in range(count):
            if pixels[cursor] > 4:
                raise ValueError('PNG row filter invalid')
            cursor += length


def diff(before, after, path=''):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        return [item for key in sorted(before.keys() | after.keys())
                for item in diff(before.get(key), after.get(key), (path + '.' + key).strip('.'))]
    return [{'path': path, 'before': before, 'after': after}]


def shareable_definition(doc):
    """Inspect decoded implementation files while preserving their original bytes."""
    from ..capability_library.sharing import assert_shareable, _check_text, _check_values, _TEXT_SUFFIXES
    metadata = copy.deepcopy(doc)
    files = {}
    def component(value, prefix):
        if not isinstance(value, dict):
            fail('INVALID_SNAPSHOT', '组件快照必须为对象。', 422)
        encoded = value.pop('files', {})
        if not isinstance(encoded, dict):
            fail('INVALID_SNAPSHOT', '组件资源集合必须为对象。', 422)
        for path, data in encoded.items():
            try:
                files[prefix + '/' + safe_path(path)] = base64.b64decode(data, validate=True)
            except (TypeError, ValueError):
                fail('INVALID_SNAPSHOT', '组件资源编码无效。', 422)
        dependencies = value.get('dependencies', [])
        if not isinstance(dependencies, list):
            fail('INVALID_SNAPSHOT', '组件依赖集合必须为数组。', 422)
        for index, child in enumerate(dependencies):
            component(child, prefix + '/dependency_' + str(index))
    for index, node in enumerate(metadata.get('nodes', [])):
        prefix = 'node_' + str(index)
        if 'code' in node:
            code = node.pop('code')
            if not isinstance(code, str):
                fail('INVALID_DEFINITION', '步骤代码必须为文本。', 422)
            files[prefix + '/__app_entry__.py'] = code.encode('utf-8')
        for path, raw in resource_files(node.pop('files', [])).items():
            files[prefix + '/resources/' + path] = raw
        if node.get('component'):
            component(node['component'], prefix + '/component')
    _check_values(metadata)
    def private_app_refs(text):
        if '/desktop/api/asset-apps/artifacts/' in text:
            fail('SHARING_PRIVATE_RESOURCE', '资源包包含私人App运行工件，请改为本次输入或包内资源。', 403)
    private_app_refs(canonical(metadata))
    budget = {'bytes': 0, 'files': 0}
    def inspect(path, raw, depth=0):
        safe_path(path)
        budget['bytes'] += len(raw)
        budget['files'] += 1
        if depth > 4 or budget['files'] > 1024 or budget['bytes'] > 16 * 1024 * 1024:
            fail('SHARING_UNREVIEWABLE_FILE', '资源包展开超过检查容量，请拆分资源。', 413)
        parts = [part.casefold() for part in PurePosixPath(path).parts]
        suffix = PurePosixPath(path).suffix.casefold()
        if any(part == '.env' or part.startswith('.env.') or part in {'.aws', '.ssh', 'credentials', 'credentials.json', 'credential.json', 'secrets.json', 'id_rsa', 'id_ed25519'} for part in parts) or suffix in {'.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3'}:
            fail('SHARING_CONTAINS_SECRET', '资源包包含凭据或私人数据库文件。', 403)
        if suffix in _TEXT_SUFFIXES:
            assert_shareable({}, {path: raw})
            private_app_refs(raw.decode('utf-8'))
        else:
            # Portable private export keeps binary resources byte-for-byte. Scan
            # embedded readable metadata without applying community text-only policy.
            _check_text(raw.decode('utf-8', errors='ignore'))
            private_app_refs(raw.decode('utf-8', errors='ignore'))
            if zipfile.is_zipfile(io.BytesIO(raw)):
                try:
                    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                        for item in archive.infolist():
                            safe_path(item.filename.rstrip('/'))
                            if item.is_dir():
                                continue
                            if item.file_size > 16 * 1024 * 1024 - budget['bytes']:
                                fail('SHARING_UNREVIEWABLE_FILE', '压缩资源展开超过检查容量。', 413)
                            inspect(path + '/' + safe_path(item.filename), archive.read(item), depth + 1)
                except (zipfile.BadZipFile, RuntimeError, NotImplementedError):
                    fail('SHARING_UNREVIEWABLE_FILE', '压缩资源损坏、加密或压缩格式不可检查。', 422)
    for path, raw in files.items():
        inspect(path, raw)
