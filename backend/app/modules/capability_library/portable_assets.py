"""Owner-scoped external originals, independent of execution or installed assets."""
import base64
import copy
import io
import zipfile

from .contracts import CapabilityError, digest, fail, now, parse_json, uid
from .packages import MAX_PACKAGE, archive_files, component_export, file_index, inspect, parse_package, safe_path

KINDS = {'app', 'tool', 'skill'}


def _filename(value):
    if not isinstance(value, str) or '/' in value or '\\' in value:
        fail('INVALID_PACKAGE', '原件文件名必须是单个安全文件名。')
    return safe_path(value)


def _read_files(raw, filename):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_PACKAGE:
        fail('LIMIT_EXCEEDED', '原件为空或超过20 MiB。', 413)
    is_zip = filename.lower().endswith('.zip') or raw.startswith((b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08')) or zipfile.is_zipfile(io.BytesIO(raw))
    return (archive_files(raw, component_entry=True), True) if is_zip else ({filename: raw}, False)


def _format(files, is_zip):
    if any(path == 'SKILL.md' or path.endswith('/SKILL.md') for path in files):
        return 'agent_skills'
    for path, raw in files.items():
        if path.lower().endswith('.json'):
            try:
                value = parse_json(raw)
            except CapabilityError:
                continue
            if isinstance(value, dict):
                fmt = value.get('format')
                if isinstance(fmt, str) and fmt in {'cd80.app-package/2', 'cd80.app/2', 'infinity-application', 'cd80.component/1', 'cd80.capability/1'}:
                    return fmt
                if 'input_schema' in value:
                    return 'claude_tool'
                if 'function' in value:
                    return 'openai_chat'
                if value.get('type') == 'function' and 'parameters' in value:
                    return 'openai_responses'
    return 'external'


def import_original(library, owner, raw, filename, kind, key, name=None):
    """Atomically retain exact bytes; never call an adapter, model, or executor."""
    library.workbench.auth.get_user(owner)
    if not isinstance(kind, str) or kind not in KINDS:
        fail('INVALID_REQUEST', '请选择App、Tool或Skill类型。')
    filename = _filename(filename)
    if not isinstance(key, str) or not 1 <= len(key) <= 150:
        fail('INVALID_REQUEST', '请提供有效 Idempotency-Key。')
    if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 200):
        fail('INVALID_REQUEST', '资产名称应为1至200字符。')
    name = name.strip() if name else filename
    files, is_zip = _read_files(raw, filename)
    fingerprint = digest({'kind': kind, 'name': name, 'filename': filename, 'sha256': digest(raw)})
    identity = digest({'operation': 'portable.import', 'key': key})
    with library.store.transaction():
        previous = library.store.get(owner, 'portable_request', identity, {})
        if previous:
            if previous['fingerprint'] != fingerprint:
                fail('IDEMPOTENCY_CONFLICT', '相同请求键对应不同原件或类型。', 409)
            return library.store.get(owner, 'portable_asset', previous['id'])
        asset_id = uid('portable')
        value = {'id': asset_id, 'kind': kind, 'name': name, 'sourceFormat': _format(files, is_zip),
                 'status': 'stored', 'runtimeReadiness': 'unprepared', 'originalName': filename,
                 'sha256': digest(raw), 'size': len(raw), 'files': file_index(files), 'issues': [],
                 'createdAt': now(), 'exportUrl': '/desktop/api/portable-assets/' + asset_id + '/export'}
        library.store.blob(owner, value['sha256'], raw)
        library.store.put(owner, 'portable_asset', asset_id, value)
        library.store.put(owner, 'portable_request', identity, {'fingerprint': fingerprint, 'id': asset_id})
    return value


def listing(library, owner, kind=None):
    if kind is not None and kind not in KINDS:
        fail('INVALID_REQUEST', '资产类型无效。')
    return [item for item in library.store.list(owner, 'portable_asset') if kind is None or item['kind'] == kind]


def detail(library, owner, asset_id):
    return library.store.get(owner, 'portable_asset', asset_id)


def original(library, owner, asset_id):
    value = detail(library, owner, asset_id)
    raw = library.store.blob(owner, value['sha256'])
    if len(raw) != value['size'] or digest(raw) != value['sha256']:
        fail('ORIGINAL_CHANGED', '原件字节或摘要不一致，无法导出。', 409)
    return value, raw


def _component_draft(value, cases=None):
    from .components import validate_snapshot
    from .test_cases import prepare_test_cases
    snapshot = validate_snapshot(value)
    manifest = snapshot['manifest']
    return {'kind': manifest['kind'], 'name': manifest['name'], 'description': manifest['description'],
            'manifest': manifest, 'files': [{'path': path, 'base64': data} for path, data in snapshot['files'].items()],
            'dependencySnapshots': snapshot['dependencies'],
            'testCases': prepare_test_cases(manifest, {}, cases)}


def read_source(library, owner, asset_id):
    """Return normalized source content, never creating runtime/authority records."""
    record, raw = original(library, owner, asset_id)
    files, is_zip = _read_files(raw, record['originalName'])
    result = {key: copy.deepcopy(record[key]) for key in ('kind', 'name', 'sha256', 'sourceFormat', 'files')}
    result.update(content=None, issues=[])
    try:
        if record['kind'] == 'app':
            if is_zip:
                fail('SOURCE_NEEDS_EDIT', '此App原件是外部资源包，需要先明确入口与应用格式。', 422)
            package = parse_json(raw)
            if not isinstance(package, dict):
                fail('SOURCE_NEEDS_EDIT', 'App原件不是当前支持的应用对象。', 422)
            if package.get('format') == 'cd80.app-package/2':
                if package.get('digest') != digest(package.get('definition')):
                    fail('SOURCE_NEEDS_EDIT', 'App包内容摘要不一致；原件已保留。', 422)
                doc = package['definition']
            elif package.get('format') == 'cd80.app/2':
                doc = package
            else:
                fail('SOURCE_NEEDS_EDIT', '此App格式尚无统一创作适配；原件已保留，可原样导出。', 422)
            from ..agent_platform.asset_contracts import definition
            normalized, issues = definition(doc)
            result['content'] = {'definition': normalized}
            result['issues'] = issues
        else:
            component = None
            packaged_cases = None
            if is_zip:
                bundled = component_export(raw)
                if bundled is not None:
                    component, packaged_cases = bundled
            if not is_zip and record['originalName'].lower().endswith('.json'):
                try:
                    value = parse_json(raw)
                    if isinstance(value, dict) and value.get('format') == 'cd80.component/1':
                        component = value
                except CapabilityError:
                    pass
            if component is not None:
                draft = _component_draft(component, packaged_cases)
            else:
                manifest, parsed, metadata = parse_package(
                    raw, 'source.zip' if is_zip else record['originalName'], include_metadata=True)
                from .test_cases import prepare_test_cases
                draft = {'kind': manifest['kind'], 'name': manifest['name'], 'description': manifest['description'],
                         'manifest': manifest, 'files': [{'path': path, 'base64': base64.b64encode(data).decode('ascii')}
                                                        for path, data in sorted(parsed.items())],
                         'testCases': prepare_test_cases(manifest, parsed, metadata.get('testCases'))}
                result['issues'] = inspect(manifest, parsed)
            if draft['kind'] != record['kind']:
                fail('SOURCE_NEEDS_EDIT', '原件内容类型与保存类型不一致，请明确要准备的资产类型。', 422)
            result['content'] = {'draft': draft}
    except CapabilityError as error:
        result.update(content=None, issues=[{'code': 'SOURCE_NEEDS_EDIT', 'message': str(error),
                                            'cause': error.code}])
    except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
        result.update(content=None, issues=[{'code': 'SOURCE_NEEDS_EDIT',
                                            'message': '原件结构暂无法安全适配；完整原件已保留，可原样导出。'}])
    if result['content'] is not None:
        # Imported declarations remain content. No credentials, validation or
        # enabled state are granted by this read-only source conversion.
        result['content']['source'] = {'type': 'imported', 'portableAssetId': asset_id, 'sha256': record['sha256']}
    return result
