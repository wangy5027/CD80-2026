"""Read complete portable App definitions without extracting or executing files."""
import ast
import base64
import copy
import io
import json
import math
import stat
import unicodedata
import zipfile
import zlib

from ..capability_library.contracts import digest, fail, parse_json
from ..capability_library.packages import safe_path

MAX_UPLOAD = 9 * 1024 * 1024
MAX_ENTRIES = 64


def _zip_definition(raw):
    files, seen, directories, total = {}, set(), set(), 0
    ignored = False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > MAX_ENTRIES:
                fail('LIMIT_EXCEEDED', 'APP 压缩包最多允许 64 项。', 413)
            for info in archive.infolist():
                if info.orig_filename != info.filename:
                    fail('INVALID_PACKAGE', '压缩包路径包含无效字符。')
                name = safe_path(info.filename[:-1] if info.is_dir() else info.filename)
                folded = unicodedata.normalize('NFC', name).casefold()
                mode = info.external_attr >> 16
                if folded in seen or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
                    fail('INVALID_PACKAGE', '不支持重复路径、链接、特殊文件或加密压缩包。')
                seen.add(folded)
                if info.is_dir():
                    directories.add(folded)
                    continue
                total += info.file_size
                if info.file_size > MAX_UPLOAD or total > MAX_UPLOAD or info.file_size > max(256 * 1024, info.compress_size * 200):
                    fail('LIMIT_EXCEEDED', 'APP 展开大小或压缩比超过限制。', 413)
                with archive.open(info) as stream:
                    content = stream.read(MAX_UPLOAD + 1)
                if len(content) != info.file_size:
                    fail('INVALID_PACKAGE', '压缩包文件长度不一致。')
                # Finder metadata carries no App implementation. Explicitly report
                # its omission while refusing every other unrepresented resource.
                if name.split('/')[0] == '__MACOSX' or name.split('/')[-1] == '.DS_Store':
                    ignored = True
                else:
                    files[name] = content
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, ValueError, EOFError, zlib.error):
        fail('INVALID_PACKAGE', '无法读取 APP ZIP 压缩包。')
    for name in seen:
        parts = name.split('/')
        if any('/'.join(parts[:i]) in seen - directories for i in range(1, len(parts))):
            fail('INVALID_PACKAGE', '压缩包文件与目录路径冲突。')
    entries = [name for name in files if len(name.split('/')) <= 2 and name.split('/')[-1] in {'app.json', 'application.json'}]
    if len(entries) != 1:
        fail('APP_PACKAGE_LAYOUT', '入口须为 app.json 或 application.json，可放在一层文件夹中。', 422)
    entry = entries[0]
    prefix = entry.rsplit('/', 1)[0] + '/' if '/' in entry else ''
    if any(not name.startswith(prefix) for name in files):
        fail('APP_PACKAGE_LAYOUT', '源码和资源必须与应用入口位于同一目录内。', 422)
    return files.pop(entry), {name[len(prefix):]: content for name, content in files.items()}, ignored


def _source_path(value, files, consumed):
    name = safe_path(value)
    if name not in files:
        fail('APP_SOURCE_MISSING', '源码包缺少声明文件：' + name, 422)
    if name in consumed:
        fail('APP_SOURCE_DUPLICATE', '源码包文件被重复引用：' + name, 422)
    consumed.add(name)
    return files[name]


def _normalize_source_zip(value, files, *, allow_companion_readme=False):
    """Resolve ZIP transport references to the existing code/files runtime format."""
    if value.get('format') == 'cd80.app-package/2':
        if value.get('digest') != digest(value.get('definition')):
            fail('INVALID_PACKAGE', '应用包格式或内容摘要不一致。')
        doc = value.get('definition')
    elif value.get('format') == 'cd80.app/2':
        doc = value
    else:
        fail('INVALID_PACKAGE', '当前支持 cd80.app/2 定义或 cd80.app-package/2 完整应用包。')
    if not isinstance(doc, dict):
        fail('INVALID_PACKAGE', 'APP 定义必须为对象。')
    doc = copy.deepcopy(doc)
    consumed = set()
    for node in doc.get('nodes', []) if isinstance(doc.get('nodes'), list) else []:
        if not isinstance(node, dict):
            continue  # The canonical definition checker reports malformed nodes.
        has_sources = 'codeFile' in node or 'resourceFiles' in node
        if not has_sources:
            continue
        if node.get('kind') != 'code':
            fail('INVALID_PACKAGE', 'codeFile/resourceFiles 仅能用于 code 步骤。', 422)
        if 'codeFile' in node:
            if 'code' in node:
                fail('INVALID_PACKAGE', 'code 与 codeFile 只能选择一种。', 422)
            data = _source_path(node.pop('codeFile'), files, consumed)
            try:
                node['code'] = data.decode('utf-8')
            except UnicodeError:
                fail('INVALID_PACKAGE', 'Python 源码必须是 UTF-8 文本。', 422)
        references = node.pop('resourceFiles', [])
        if not isinstance(references, list):
            fail('INVALID_PACKAGE', 'resourceFiles 必须为相对路径数组。', 422)
        embedded = node.get('files', [])
        if not isinstance(embedded, list):
            fail('INVALID_PACKAGE', 'files 必须为资源数组。', 422)
        seen_names = set()
        for item in embedded:
            if not isinstance(item, dict):
                fail('INVALID_PACKAGE', 'files 资源项必须为对象。', 422)
            name = safe_path(item.get('path'))
            folded = unicodedata.normalize('NFC', name).casefold()
            if folded in seen_names:
                fail('INVALID_PACKAGE', '内嵌资源路径重复。', 422)
            seen_names.add(folded)
        resolved = []
        for reference in references:
            content = _source_path(reference, files, consumed)
            folded = unicodedata.normalize('NFC', reference).casefold()
            if folded in seen_names or folded == '__app_entry__.py':
                fail('INVALID_PACKAGE', '源码资源与已有资源或平台入口路径冲突。', 422)
            seen_names.add(folded)
            resolved.append({'path': reference, 'base64': base64.b64encode(content).decode('ascii')})
        node['files'] = embedded + resolved
    leftovers = sorted(set(files) - consumed)
    companion_readme = False
    if allow_companion_readme and 'readme.md' in leftovers:
        # Standard exports carry a human-readable root companion. A README
        # explicitly declared as a code resource has already been consumed.
        # An embedded same-name resource is a separate JSON namespace and is
        # retained byte-for-byte; never replace it with this ZIP companion.
        try:
            files['readme.md'].decode('utf-8')
        except UnicodeError:
            fail('INVALID_PACKAGE', '根目录 readme.md 必须为 UTF-8 文本。', 422)
        leftovers.remove('readme.md')
        companion_readme = True
    if leftovers:
        fail('APP_SOURCE_UNDECLARED', '源码包有未声明文件：' + ', '.join(leftovers[:4]), 422)
    return doc, companion_readme


def _python_definition(raw):
    """Read a literal declaration only; never import or evaluate uploaded code."""
    if len(raw) > 131072:
        fail('LIMIT_EXCEEDED', '单文件 Python 源码不能超过128KiB。', 413)
    try:
        source = raw.decode('utf-8')
        tree = ast.parse(source)
    except (UnicodeError, SyntaxError, ValueError, RecursionError):
        fail('INVALID_PACKAGE', 'Python 源码须为有效的 UTF-8 语法。', 422)
    declarations = []
    for item in tree.body:
        if isinstance(item, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'APP_INTERFACE' for target in item.targets):
            if len(item.targets) != 1 or not isinstance(item.targets[0], ast.Name):
                fail('INVALID_PACKAGE', 'APP_INTERFACE 须为单独的静态字面量赋值。', 422)
            declarations.append(item.value)
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name) and item.target.id == 'APP_INTERFACE':
            declarations.append(item.value)
        elif isinstance(item, ast.AugAssign) and isinstance(item.target, ast.Name) and item.target.id == 'APP_INTERFACE':
            fail('INVALID_PACKAGE', 'APP_INTERFACE 不支持动态修改。', 422)
    if len(declarations) != 1:
        fail('INVALID_PACKAGE', 'Python 文件须包含唯一的 APP_INTERFACE 静态声明。', 422)
    try:
        interface = ast.literal_eval(declarations[0])
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        fail('INVALID_PACKAGE', 'APP_INTERFACE 只允许静态 JSON 字面量，不执行变量、函数或表达式。', 422)
    def json_literal(value, depth=0):
        if depth > 64:
            return False
        if isinstance(value, dict):
            return all(isinstance(key, str) and json_literal(child, depth + 1) for key, child in value.items())
        if isinstance(value, list):
            return all(json_literal(child, depth + 1) for child in value)
        if isinstance(value, float):
            return math.isfinite(value)
        return value is None or isinstance(value, (str, bool, int))
    if not json_literal(interface):
        fail('INVALID_PACKAGE', 'APP_INTERFACE 只能使用 JSON 可表达的静态对象、数组和标量，不能用元组或非字符串键。', 422)
    required = {'name', 'description', 'inputSchema', 'outputSchema', 'rules'}
    if not isinstance(interface, dict) or not required <= set(interface) or set(interface) - required - {'testCases'}:
        fail('INVALID_PACKAGE', 'APP_INTERFACE 须包含 name、description、inputSchema、outputSchema、rules，可另含 testCases。', 422)
    if not isinstance(interface['name'], str) or not isinstance(interface['description'], str) or not isinstance(interface['rules'], list) or any(not isinstance(rule, str) for rule in interface['rules']):
        fail('INVALID_PACKAGE', 'APP_INTERFACE 名称、用途和规则格式无效。', 422)
    input_schema, output_schema = interface['inputSchema'], interface['outputSchema']
    if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
        fail('INVALID_PACKAGE', '输入与输出 Schema 必须为 JSON 对象。', 422)
    properties = input_schema.get('properties')
    required_inputs = input_schema.get('required')
    if (input_schema.get('type') != 'object' or not isinstance(properties, dict)
            or any(not isinstance(name, str) or not name or '.' in name for name in properties)
            or not isinstance(required_inputs, list) or len(required_inputs) != len(properties)
            or any(not isinstance(name, str) for name in required_inputs)
            or set(required_inputs) != set(properties)
            or input_schema.get('patternProperties')
            or input_schema.get('additionalProperties') is not False):
        fail('INVALID_PACKAGE', '单文件输入 Schema 须将每个无点号的 properties 字段列为 required，且 additionalProperties 为 false；复杂输入请用完整 APP JSON。', 422)
    runs = [item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == 'run']
    if len(runs) != 1 or isinstance(runs[0], ast.AsyncFunctionDef) or len(runs[0].args.args) != 1 or runs[0].args.args[0].arg != 'payload' or runs[0].args.posonlyargs or runs[0].args.kwonlyargs or runs[0].args.vararg or runs[0].args.kwarg:
        fail('INVALID_PACKAGE', 'Python 文件须定义顶层 def run(payload)。', 422)
    # Ensure literal_eval did not admit Python-only values such as tuples/bytes.
    try:
        json.dumps(interface, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        fail('INVALID_PACKAGE', 'APP_INTERFACE 须为可表示的 JSON 字面量。', 422)
    return {'format': 'cd80.app/2', 'name': interface['name'], 'description': interface['description'],
            'inputSchema': input_schema, 'rules': interface['rules'], 'gaps': [],
            **({'testCases': interface['testCases']} if 'testCases' in interface else {}),
            'nodes': [{'id': 'main', 'kind': 'code', 'title': interface['name'], 'code': source,
                       'inputBindings': {name: {'from': 'input', 'path': name} for name in properties},
                       'delivery': {'schema': output_schema, 'files': []}, 'checkpoint': False, 'files': []}],
            'outputs': {'result': {'from': 'step', 'stepId': 'main', 'path': ''}}}


def preview_file(service, owner, raw, filename):
    if not raw or len(raw) > MAX_UPLOAD:
        fail('LIMIT_EXCEEDED', '请选择非空且不超过 9 MiB 的 APP JSON、Python 或 ZIP。', 413)
    filename = filename or ''
    ignored = False
    companion_readme = False
    if filename.lower().endswith('.zip'):
        entry, files, ignored = _zip_definition(raw)
        value = parse_json(entry)
        if not isinstance(value, dict):
            fail('INVALID_PACKAGE', 'APP 内容须为完整 JSON 对象。')
        value, companion_readme = _normalize_source_zip(value, files, allow_companion_readme=True)
        package = {'format': 'cd80.app-package/2', 'definition': value, 'digest': digest(value)}
    elif filename.lower().endswith('.py'):
        value = _python_definition(raw)
        package = {'format': 'cd80.app-package/2', 'definition': value, 'digest': digest(value)}
    elif filename.lower().endswith('.json'):
        value = parse_json(raw)
        if not isinstance(value, dict):
            fail('INVALID_PACKAGE', 'APP 内容须为完整 JSON 对象。')
        value, _ = _normalize_source_zip(value, {})
        package = {'format': 'cd80.app-package/2', 'definition': value, 'digest': digest(value)}
    else:
        fail('INVALID_PACKAGE', '请选择 APP JSON、Python 或 ZIP 文件。')
    result = service.import_preview(owner, {'package': package})
    if ignored:
        result['warnings'].append('已忽略 macOS 的 .DS_Store / __MACOSX 元数据。')
    if companion_readme:
        result['warnings'].append('ZIP 根目录 readme.md 是导出说明，不属于 APP 运行资源；需要保留时请另行保存。')
    return {**result, 'package': package}


def template_definition():
    return {
        'format': 'cd80.app/2', 'name': '两数求和示例',
        'description': '完整 APP 格式示例：接收两个数，输出它们的和。',
        'inputSchema': {'type': 'object', 'properties': {'a': {'type': 'number'}, 'b': {'type': 'number'}},
                        'required': ['a', 'b'], 'additionalProperties': False},
        'testCases': [{'label': '原任务用例：1 + 2', 'input': {'a': 1, 'b': 2},
                       'fileBindings': {}, 'expectation': {'mode': 'json_equals', 'value': {'result': {'sum': 3}}}}],
        'rules': ['返回 a 与 b 的和。'], 'gaps': [],
        'nodes': [{'id': 'sum', 'kind': 'code', 'title': '计算两数之和',
                   'code': 'def run(payload):\n    return {"sum": payload["a"] + payload["b"]}\n',
                   'inputBindings': {'a': {'from': 'input', 'path': 'a'}, 'b': {'from': 'input', 'path': 'b'}},
                   'delivery': {'schema': {'type': 'object', 'properties': {'sum': {'type': 'number'}}, 'required': ['sum']}},
                   'checkpoint': False, 'files': []}],
        'outputs': {'result': {'from': 'step', 'stepId': 'sum', 'path': ''}},
    }


def template_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        doc = template_definition()
        node = doc['nodes'][0]
        code = node.pop('code')
        node['codeFile'] = 'src/main.py'
        node['resourceFiles'] = ['assets/notes.txt']
        archive.writestr('app.json', json.dumps(doc, ensure_ascii=False, indent=2))
        archive.writestr('src/main.py', code)
        archive.writestr('assets/notes.txt', '示例资源：导入后冻结到 code 步骤的 files。\n')
    return output.getvalue()
