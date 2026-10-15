"""Offline package parsing. No extraction, imports, subprocesses, or network."""
import ast
import io
import json
import re
import stat
import sys
import unicodedata
import zipfile
import zlib
from pathlib import Path, PurePosixPath

import yaml
from jsonschema import Draft202012Validator

from .contracts import digest, fail, issue, parse_json

MAX_PACKAGE = 20 * 1024 * 1024
MAX_EXPANDED = 50 * 1024 * 1024
MAX_FILE = 1024 * 1024
MAX_FILES = 500
MANIFEST = json.loads(Path(__file__).with_name('capability.schema.json').read_text(encoding='utf-8'))
KEYWORDS = {'type','properties','required','additionalProperties','items','enum','const','minimum','maximum','exclusiveMinimum','exclusiveMaximum','minLength','maxLength','minItems','maxItems','description','title','default'}


def safe_path(name):
    if not isinstance(name, str) or not name or len(name) > 512 or '\\' in name or ':' in name or name.startswith('/'):
        fail('INVALID_PACKAGE', '包内文件路径不安全。')
    parts = name.split('/')
    if len(parts)>12: fail('LIMIT_EXCEEDED','包内路径层级超过 12。',413)
    reserved = {'CON','PRN','AUX','NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}
    if any(part in {'','.', '..'} or part.endswith((' ','.')) or part.split('.')[0].upper() in reserved or any(ord(c)<32 for c in part) for part in parts):
        fail('INVALID_PACKAGE', '包内文件路径不安全。')
    return name


def file_index(files):
    return [{'path':p,'sha256':digest(b),'size':len(b)} for p,b in sorted(files.items())]


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(safe_path(name), content)
    return output.getvalue()


def has_secret(files):
    pattern = re.compile(rb'(?i)(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|access[_-]?token|token|authorization|password|secret)\s*["\x27]?\s*[:=]\s*["\x27]?(?:Bearer\s+)?[A-Za-z0-9_+/=-]{12,})')
    return any(pattern.search(content) for content in files.values())


def original_has_secret(raw,filename):
    # Call only after parse_package has checked member counts and expansion bounds.
    if filename.lower().endswith('.zip'):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return has_secret({i.filename:archive.read(i) for i in archive.infolist() if not i.is_dir()})
    return has_secret({'original':raw})


def archive_files(raw, *, component_entry=False):
    """Read bounded ZIP bytes without extraction or changing paths/content."""
    if not raw or len(raw) > MAX_PACKAGE:
        fail('LIMIT_EXCEEDED', '能力包为空或超过 20 MiB。', 413)
    files, seen, directories, total = {}, set(), set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) > MAX_FILES:
                fail('LIMIT_EXCEEDED', '包内最多允许 500 项。', 413)
            for info in archive.infolist():
                if info.orig_filename != info.filename:
                    fail('INVALID_PACKAGE', '包内文件路径包含无效字符。')
                name = safe_path(info.filename[:-1] if info.is_dir() else info.filename)
                folded = unicodedata.normalize('NFC', name).casefold()
                mode = info.external_attr >> 16
                if folded in seen or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}) or info.flag_bits & 1:
                    fail('INVALID_PACKAGE', '不允许重复路径、链接、特殊文件或加密包。')
                seen.add(folded)
                if info.is_dir():
                    directories.add(folded)
                    continue
                total += info.file_size
                is_component = component_entry and (
                    name == 'component.json' or
                    (name.count('/') == 1 and name.endswith('/component.json')))
                member_limit = MAX_EXPANDED if is_component else MAX_FILE
                if (info.file_size > member_limit or total > MAX_EXPANDED or
                        (not is_component and info.file_size > max(256 * 1024, info.compress_size * 200))):
                    fail('LIMIT_EXCEEDED', '解压大小或压缩比超过限制。', 413)
                with archive.open(info) as handle:
                    content = handle.read(member_limit + 1)
                if len(content) != info.file_size:
                    fail('INVALID_PACKAGE', '包内文件长度不一致。')
                files[name] = content
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, ValueError, EOFError, zlib.error):
        fail('INVALID_PACKAGE', '无法读取 ZIP 能力包。')
    file_names = seen - directories
    for name in seen:
        parts = name.split('/')
        if any('/'.join(parts[:end]) in file_names for end in range(1, len(parts))):
            fail('INVALID_PACKAGE', '包内文件与目录路径冲突。')
    return files


def _unwrapped_files(raw, *, component_entry=False):
    files = archive_files(raw, component_entry=component_entry)
    # A single conventional enclosing directory is accepted without changing bytes.
    roots = {p.split('/')[0] for p in files}
    if len(roots) == 1 and all('/' in p for p in files):
        files = {p.split('/', 1)[1]: data for p, data in files.items()}
    return files


def component_export(raw):
    """Recognize a bounded platform component ZIP; other ZIPs remain native."""
    files = _unwrapped_files(raw, component_entry=True)
    if 'component.json' not in files:
        return None
    try:
        marker = json.loads(files['component.json'])
    except (ValueError, UnicodeError, RecursionError):
        return None
    if not isinstance(marker, dict) or marker.get('format') != 'cd80.component/1':
        return None
    component = parse_json(files['component.json'])
    extras = set(files) - {'component.json', 'test-cases.json'}
    if extras:
        # Standard exports carry human-readable detail and source copies beside
        # the portable component. Their exact accessory bytes must be indexed;
        # arbitrary extra ZIP members still cannot disappear on import.
        index_raw = files.get('standard-export.json')
        if index_raw is None or 'readme.md' not in files:
            fail('INVALID_PACKAGE', '完整组件包包含未声明的附加文件。')
        index = parse_json(index_raw)
        if (not isinstance(index, dict) or set(index) != {'format', 'files'} or
                index.get('format') != 'cd80.standard-export/1' or
                index.get('files') != file_index({k: v for k, v in files.items() if k != 'standard-export.json'})):
            fail('INVALID_PACKAGE', '标准导出包的文件索引与实际内容不一致。')
        allowed = {'readme.md', 'standard-export.json'}
        if any(path not in allowed and not path.startswith(
                ('standard-skill/', 'standard-tool/', 'implementation/source/')) for path in extras):
            fail('INVALID_PACKAGE', '标准导出包包含未支持的附加路径。')
    cases = parse_json(files['test-cases.json']) if 'test-cases.json' in files else None
    return component, cases


def parse_package(raw, filename, *, include_metadata=False):
    if not raw or len(raw)>MAX_PACKAGE:
        fail('LIMIT_EXCEEDED', '能力包为空或超过 20 MiB。', 413)
    if filename.lower().endswith('.zip'):
        files = _unwrapped_files(raw)
    elif filename.lower().endswith(('.json','.md')):
        files = {'SKILL.md' if filename.lower().endswith('.md') else 'tool.json':raw}
    else:
        fail('INVALID_PACKAGE', '请选择 ZIP、JSON 或 SKILL.md 文件。')
    platform_manifest = 'capability.json' in files
    if platform_manifest and 'SKILL.md' in files:
        # A standard skill may legitimately carry an ancillary file with this
        # name. Only an explicit platform format marker makes it our envelope.
        try:
            declared = json.loads(files['capability.json'])
            platform_manifest = isinstance(declared, dict) and declared.get('format') == 'cd80.capability/1'
        except (ValueError, UnicodeError):
            platform_manifest = False
    metadata = {}
    if platform_manifest:
        manifest = parse_json(files.pop('capability.json'))
        files.pop('provenance.json', None)
        cases = files.pop('test-cases.json', None)
        if cases is not None:
            metadata['testCases'] = parse_json(cases)
        try:
            Draft202012Validator(MANIFEST).validate(manifest)
        except Exception:
            fail('INVALID_PACKAGE', 'capability.json 不符合平台清单规范。')
        for entry in manifest['files']:
            safe_path(entry['path'])
        if manifest['files'] != file_index(files):
            # File order is not semantically significant.
            if sorted(manifest['files'], key=lambda x:x['path']) != file_index(files):
                fail('INVALID_PACKAGE', '文件清单、长度或 SHA256 与原件不一致。')
    else:
        manifest, files = native_manifest(files)
    return (manifest, files, metadata) if include_metadata else (manifest, files)


def native_manifest(files):
    base = {'format':'cd80.capability/1', 'dependencies':[], 'permissions':{'network':'none','externalEffects':'none','inputFiles':False,'outputFiles':False}}
    if 'SKILL.md' in files and 'tool.json' in files:
        fail('INVALID_PACKAGE', 'ZIP 同时包含 Skill 与 Tool 入口，请只保留一个。')
    if 'SKILL.md' in files:
        try:
            text = files['SKILL.md'].decode('utf-8-sig')
            front = text.split('---',2)
            if len(front)!=3 or front[0].strip():
                raise ValueError()
            metadata = yaml.safe_load(front[1])
            name, description = metadata['name'], metadata['description']
            if not isinstance(name,str) or not isinstance(description,str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*',name) or len(name)>64 or not description:
                raise ValueError()
        except (ValueError, KeyError, TypeError, UnicodeError, yaml.YAMLError, RecursionError):
            fail('INVALID_PACKAGE', 'SKILL.md 需要有效的 name、description YAML 信息。')
        original = {'original/'+p:b for p,b in files.items()}
        return {**base,'kind':'skill','name':name,'description':description,'source':{'identity':'agent-skills/'+name,'format':'agent_skills'},'files':file_index(original),'skill':{'entry':'original/SKILL.md'}}, original
    if len(files)!=1 and 'tool.json' not in files:
        fail('INVALID_PACKAGE', 'Tool ZIP 需要根目录 tool.json 作为唯一入口。')
    if 'tool.json' in files:
        for path, raw in files.items():
            if path == 'tool.json' or '/' in path or not path.lower().endswith('.json'):
                continue
            try: other=json.loads(raw)
            except (ValueError,UnicodeError): continue
            if isinstance(other,dict) and ('input_schema' in other or 'function' in other or
                                           ('name' in other and 'parameters' in other)):
                fail('INVALID_PACKAGE', 'Tool ZIP 根目录包含多个工具定义，请只保留 tool.json 入口。')
    definition = parse_json(files['tool.json'] if 'tool.json' in files else next(iter(files.values())))
    if not isinstance(definition,dict):
        fail('INVALID_PACKAGE', '工具定义必须是 JSON 对象。')
    if definition.get('format')=='cd80.capability/1':
        # Connection-only manifest is useful as a single JSON file.
        try:
            Draft202012Validator(MANIFEST).validate(definition)
        except Exception:
            fail('INVALID_PACKAGE', '能力清单无效。')
        if definition['files']:
            fail('INVALID_PACKAGE', '包含资源文件的清单请使用 ZIP 上传。')
        return definition, {}
    fmt = 'claude_tool' if 'input_schema' in definition else ('openai_chat' if 'function' in definition else 'openai_responses')
    value = definition.get('function',definition)
    if not isinstance(value,dict) or not isinstance(value.get('name'),str) or not isinstance(value.get('input_schema',value.get('parameters')),dict):
        fail('INVALID_PACKAGE', '无法识别工具定义。')
    original = {'original/'+p:b for p,b in files.items()}
    # A definition alone cannot supply an executable handler. The sentinel is
    # deliberately never registered; inspection reports MISSING_IMPLEMENTATION.
    execution=({'type':'python_wasi','path':'original/tool.py','callable':'run'} if 'tool.py' in files else
               {'type':'builtin','handlerId':'missing-implementation'})
    return {**base,'kind':'tool','name':value['name'],'description':value.get('description') or value['name'],'source':{'identity':fmt+'/'+value['name'],'format':fmt},'files':file_index(original),'tool':{'inputSchema':value.get('input_schema',value.get('parameters')),'execution':execution}}, original


def schema_issues(schema, path='inputSchema'):
    issues = []
    if not isinstance(schema, dict):
        return [issue('UNSUPPORTED_SCHEMA','只支持明确的 JSON Schema 对象。',path=path)]
    try:
        Draft202012Validator.check_schema(schema)
    except Exception:
        return [issue('UNSUPPORTED_SCHEMA','JSON Schema 结构无效。',path=path)]
    def visit(node, loc, depth=0):
        if depth>24 or not isinstance(node,dict):
            issues.append(issue('UNSUPPORTED_SCHEMA','Schema 嵌套过深或形式不受支持。',path=loc))
            return
        for key, value in node.items():
            if key not in KEYWORDS:
                issues.append(issue('UNSUPPORTED_SCHEMA',f'当前不支持 Schema 关键字 {key}，原件保留。',path=loc))
            elif key=='properties':
                for name, child in value.items(): visit(child,loc+'.'+name,depth+1)
            elif key=='items' or (key=='additionalProperties' and isinstance(value,dict)):
                visit(value,loc+'.'+key,depth+1)
    visit(schema,path)
    return issues


def inspect(manifest, files):
    problems = []
    if has_secret(files):
        problems.append(issue('EMBEDDED_SECRET','原件疑似含凭据；请在外部处理后重新导入。'))
    seen_examples = set()
    for example in manifest.get('examples', []):
        if example['id'] in seen_examples:
            problems.append(issue('INVALID_EXAMPLE', '示例标识重复。'))
        seen_examples.add(example['id'])
        if 'assertions' in example:
            from .assertions import validate_assertions
            try: validate_assertions(example['assertions'])
            except ValueError as error: problems.append(issue('INVALID_EXAMPLE', str(error)))
        if has_secret({'example': json.dumps(example).encode()}):
            problems.append(issue('EMBEDDED_SECRET', '示例疑似包含凭据。'))
        for path in example.get('filePaths', []):
            if path not in files: problems.append(issue('INVALID_EXAMPLE', '示例文件不存在。', path=path))
        if manifest['kind'] == 'tool':
            if 'input' not in example or ('expected' not in example and not example.get('assertions')):
                problems.append(issue('INVALID_EXAMPLE', 'Tool 示例必须包含输入和预期结果。'))
            else:
                try:
                    Draft202012Validator(manifest['tool']['inputSchema']).validate(example['input'])
                    if 'outputSchema' in manifest['tool'] and 'expected' in example:
                        Draft202012Validator(manifest['tool']['outputSchema']).validate(example['expected'])
                except Exception:
                    problems.append(issue('INVALID_EXAMPLE', '示例输入或预期结果不符合参数规范。'))
        elif manifest['kind'] == 'skill' and (not example.get('prompt') or not example.get('criteria')):
            problems.append(issue('INVALID_EXAMPLE', 'Skill 示例必须包含问题和验收标准。'))
    permissions = manifest['permissions']
    if permissions['externalEffects']!='none':
        problems.append(issue('PERMISSION_UNKNOWN' if permissions['externalEffects']=='unknown' else 'EXTERNAL_WRITE','外部写入或权限不明确，不允许试运行和调用。'))
    if manifest['kind']=='skill':
        if skill_content_validity(manifest, files) != 'valid':
            problems.append(issue('INVALID_ENTRY', 'Skill标准入口的名称、说明或正文无效；原件保留。'))
        entry = manifest['skill']['entry']
        if entry not in files:
            problems.append(issue('MISSING_ENTRY','缺少 Skill 入口文件。'))
        scripts = manifest['skill'].get('scripts',[])
        declared = {item['path'] for item in scripts}
        for path in files:
            if PurePosixPath(path).suffix.lower() in {'.sh','.bash','.js','.mjs','.cjs','.ps1','.bat','.cmd','.exe','.wasm'}:
                problems.append(issue('UNSUPPORTED_EXECUTION','包包含当前不支持的执行文件，原件保留。',path=path))
            if path.endswith('.py') and path not in declared:
                problems.append(issue('UNDECLARED_SCRIPT','Python 脚本尚未声明受控执行入口。',path=path))
        if entry in files:
            try:
                text = files[entry].decode('utf-8-sig')
                front=text.split('---',2)
                metadata=yaml.safe_load(front[1]) if len(front)==3 and not front[0].strip() else None
                if not isinstance(metadata,dict) or not metadata.get('name') or not metadata.get('description'):
                    problems.append(issue('INVALID_ENTRY','SKILL.md 缺少名称与说明。'))
                elif set(metadata)-{'name','description','license','compatibility','metadata','allowed-tools'}:
                    problems.append(issue('UNSUPPORTED_EXECUTION','Skill 包含当前未支持的宿主扩展字段，不能试运行。'))
                elif metadata.get('allowed-tools') and not manifest['dependencies']:
                    problems.append(issue('MISSING_DEPENDENCY','Skill 声明了工具限制，但缺少明确的工具依赖身份与内容版本；请补齐清单后重新导入。','configuration'))
                if re.search(r'(?im)(?:^|[\s`])(bash|node|npm|pip|powershell|curl|wget|sudo|uvx|npx)(?:\s|`)',text):
                    problems.append(issue('UNSUPPORTED_EXECUTION','说明包含宿主命令或安装要求，当前不可调用。',path=entry))
            except (UnicodeError,yaml.YAMLError,RecursionError):
                problems.append(issue('INVALID_TEXT','Skill 入口需要 UTF-8 文本。'))
        executions = scripts
    elif manifest['kind']=='tool':
        tool = manifest['tool']
        problems.extend(schema_issues(tool['inputSchema']))
        if tool['inputSchema'].get('type')!='object':
            problems.append(issue('UNSUPPORTED_SCHEMA','工具输入必须为 JSON 对象。'))
        if 'outputSchema' in tool: problems.extend(schema_issues(tool['outputSchema'],'outputSchema'))
        fmt=manifest['source']['format']
        if fmt in {'openai_chat','openai_responses','claude_tool'}:
            for path,data in files.items():
                if path=='original/tool.json':
                    try: definition=json.loads(data)
                    except (ValueError,UnicodeError): continue
                    if not isinstance(definition,dict): continue
                    if fmt=='openai_chat' and 'function' in definition:
                        unknown=set(definition)-{'type','function'}
                        definition=definition['function']
                    else: unknown=set()
                    if isinstance(definition,dict) and 'name' in definition:
                        parameters=definition.get('parameters',definition.get('input_schema'))
                        if parameters is not None and parameters!=tool['inputSchema']:
                            problems.append(issue('UNSUPPORTED_SCHEMA','平台输入约束与原生工具定义不一致，请在外部统一后重新导入。',path=path))
                        unknown |= set(definition)-{'type','name','description','parameters','input_schema'}
                        if unknown: problems.append(issue('UNSUPPORTED_SCHEMA','原生工具包含未支持的控制字段：'+', '.join(sorted(unknown))+'；不能静默丢弃。',path=path))
        executions = [tool['execution']]
        if tool['execution']['type'] == 'fixed_pipeline':
            from .fixed_pipeline import issues as pipeline_issues
            problems.extend(pipeline_issues(tool['execution']))
            required = {(d['sourceIdentity'], d['contentDigest']) for d in manifest['dependencies']}
            for step in tool['execution']['steps']:
                if step['kind'] == 'tool' and (step['sourceIdentity'], step['contentDigest']) not in required:
                    problems.append(issue('MISSING_DEPENDENCY', '固定Tool步骤未声明精确依赖。', 'configuration'))
            executions = [{'type': 'python_wasi', 'path': step['path'], 'callable': step['callable']}
                          for step in tool['execution']['steps'] if step['kind'] == 'python_wasi']
    else:
        executions = []
    for execution in executions:
        kind = execution['type']
        if kind=='builtin':
            problems.append(issue('MISSING_IMPLEMENTATION','工具定义不包含可调用实现，请补齐实现后重新导入。','configuration'))
        elif kind in {'python_wasi', 'python_connector'}:
            if kind == 'python_connector' and any(op.endswith('_v2') or op == 'aggregate_v1' for c in execution['connections'] for op in c['operations']) and execution.get('protocolVersion') != '2':
                problems.append(issue('UNSUPPORTED_EXECUTION','新增数据操作需要显式声明连接协议版本2。'))
            fixed = manifest.get('tool', {}).get('execution', {}).get('type') == 'fixed_pipeline'
            allowed_network = {'none', 'connector_only'} if fixed else {('connector_only' if kind == 'python_connector' else 'none')}
            if permissions['network'] not in allowed_network:
                problems.append(issue('UNSUPPORTED_EXECUTION','隔离 Python 不允许网络访问。'))
            try:
                code = files[execution['path']].decode('utf-8')
                tree = ast.parse(code)
                if not any(isinstance(node,ast.FunctionDef) and node.name=='run' for node in tree.body):
                    problems.append(issue('UNSUPPORTED_EXECUTION','执行入口需要 run(payload)，平台不改写原代码。'))
                if len(code.encode())>131072:
                    problems.append(issue('LIMIT_EXCEEDED','Python 入口超过 128 KB。'))
                for node in ast.walk(tree):
                    names = [a.name.split('.')[0] for a in node.names] if isinstance(node,ast.Import) else ([node.module.split('.')[0]] if isinstance(node,ast.ImportFrom) and node.module else [])
                    local_modules={PurePosixPath(p).stem for p in files if p.endswith('.py')} | {part for p in files for part in PurePosixPath(p).parts[:-1]}
                    if any(name not in sys.stdlib_module_names and name not in local_modules for name in names):
                        problems.append(issue('UNSUPPORTED_DEPENDENCY','脚本依赖当前隔离运行器未提供的第三方模块；平台不会安装依赖。',path=execution['path']))
                    if set(names)&{'socket','subprocess','ctypes','requests','urllib','http','multiprocessing','pip'}:
                        problems.append(issue('UNSUPPORTED_EXECUTION','脚本声明了不支持的网络、进程或安装模块。',path=execution['path']))
            except (KeyError,UnicodeError,SyntaxError,RecursionError):
                problems.append(issue('INVALID_ENTRY','Python 文件缺失或语法无效。'))
    return problems


def skill_content_validity(manifest, files):
    """Standard content validity is separate from this host's execution support."""
    if manifest.get('kind') != 'skill':
        return 'valid'
    try:
        entry = manifest['skill']['entry']
        text = files[entry].decode('utf-8-sig')
        front = text.split('---', 2)
        if len(front) != 3 or front[0].strip() or not front[2].strip():
            return 'invalid'
        metadata = yaml.safe_load(front[1])
        if not isinstance(metadata, dict):
            return 'invalid'
        name, description = metadata.get('name'), metadata.get('description')
        if not isinstance(name, str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name) or len(name) > 64:
            return 'invalid'
        if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
            return 'invalid'
        return 'valid'
    except (KeyError, ValueError, UnicodeError, yaml.YAMLError):
        return 'invalid'
