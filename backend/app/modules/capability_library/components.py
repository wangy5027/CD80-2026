"""Portable immutable components; execution authority always belongs to the caller.

These are internal APIs. The application service enforces its own current-version
validation and enablement before calling them. Source asset switches are not
consulted after capture; current account/connection/platform restrictions are.
"""
import base64
import copy
import threading
import unicodedata
from pathlib import PurePosixPath

from jsonschema import Draft202012Validator

from .contracts import canonical, digest, fail, now, uid
from .packages import MANIFEST, MAX_FILE, MAX_EXPANDED, file_index, inspect, safe_path, has_secret, zip_bytes

FORMAT = 'cd80.component/1'
_FIELDS = {'format', 'manifest', 'files', 'dependencies', 'provenance', 'contentDigest', 'snapshotDigest'}
_ORIGIN_FIELDS = {'assetId', 'versionId', 'contentDigest', 'configurationId', 'runId', 'stepId', 'source'}


def _content(manifest, files):
    index = file_index(files)
    return digest({'manifest': {**manifest, 'files': index}, 'files': index})


def snapshot_from_package(manifest, files, dependencies=(), provenance=None):
    """Capture bytes without importing, enabling, verifying, or executing them."""
    manifest = copy.deepcopy(manifest)
    manifest['files'] = file_index(files)
    value = {'format': FORMAT, 'manifest': manifest,
             'files': {path: base64.b64encode(data).decode('ascii') for path, data in sorted(files.items())},
             'dependencies': copy.deepcopy(list(dependencies)),
             'provenance': {key: str(value) for key, value in (provenance or {}).items() if key in _ORIGIN_FIELDS},
             'contentDigest': _content(manifest, files)}
    value['snapshotDigest'] = digest(value)
    return validate_snapshot(value)


def validate_snapshot(snapshot):
    """Validate the entire closed graph, including every byte, without I/O."""
    count = [0, 0, 0]
    active = set()

    def visit(value, depth):
        if depth > 8 or id(value) in active:
            fail('INVALID_SNAPSHOT', '组件依赖过深或存在循环。', 422)
        if not isinstance(value, dict) or set(value) != _FIELDS or value.get('format') != FORMAT:
            fail('INVALID_SNAPSHOT', '组件快照格式无效。', 422)
        count[0] += 1
        if count[0] > 16:
            fail('LIMIT_EXCEEDED', '组件闭包超过16个包。', 413)
        active.add(id(value))
        manifest = value['manifest']
        try:
            Draft202012Validator(MANIFEST).validate(manifest)
        except Exception:
            fail('INVALID_SNAPSHOT', '组件清单无效。', 422)
        if manifest['kind'] not in {'tool', 'skill'}:
            fail('INVALID_SNAPSHOT', '连接配置不能作为组件内容。', 422)
        encoded = value['files']
        if not isinstance(encoded, dict) or len(encoded) > 500:
            fail('INVALID_SNAPSHOT', '组件文件集合无效。', 422)
        files, names = {}, set()
        for path, data in encoded.items():
            safe_path(path)
            folded = unicodedata.normalize('NFC', path).casefold()
            if folded in names:
                fail('INVALID_SNAPSHOT', '组件包含大小写冲突路径。', 422)
            names.add(folded)
            if not isinstance(data, str) or len(data) > (MAX_FILE + 2) // 3 * 4:
                fail('LIMIT_EXCEEDED', '组件文件超过1 MiB。', 413)
            try:
                raw = base64.b64decode(data, validate=True)
            except (ValueError, TypeError):
                fail('INVALID_SNAPSHOT', '组件文件不是有效base64。', 422)
            if len(raw) > MAX_FILE:
                fail('LIMIT_EXCEEDED', '组件文件超过1 MiB。', 413)
            files[path] = raw
            count[1] += 1
            count[2] += len(raw)
        if count[1] > 500 or count[2] > MAX_EXPANDED:
            fail('LIMIT_EXCEEDED', '组件闭包超过文件数或总字节限制。', 413)
        if sorted(manifest['files'], key=lambda f: f['path']) != file_index(files):
            fail('INVALID_SNAPSHOT', '组件文件与清单摘要不一致。', 422)
        if _content(manifest, files) != value['contentDigest']:
            fail('INVALID_SNAPSHOT', '组件内容摘要不一致。', 422)
        origin = value['provenance']
        if not isinstance(origin, dict) or set(origin) - _ORIGIN_FIELDS or any(
                not isinstance(v, str) or len(v) > 512 for v in origin.values()):
            fail('INVALID_SNAPSHOT', '组件来源只能包含有限的追溯标识。', 422)
        if not isinstance(value['dependencies'], list):
            fail('INVALID_SNAPSHOT', '组件依赖集合无效。', 422)
        expected = [(d['sourceIdentity'], d['contentDigest']) for d in manifest['dependencies']]
        actual = []
        for dependency in value['dependencies']:
            visit(dependency, depth + 1)
            dm = dependency['manifest']
            if dm['kind'] != 'tool':
                fail('INVALID_SNAPSHOT', '组件依赖必须是明确Tool。', 422)
            actual.append((dm['source']['identity'], dependency['contentDigest']))
        if sorted(expected) != sorted(actual) or len(actual) != len(set(actual)):
            fail('INVALID_SNAPSHOT', '组件依赖缺失、重复或与清单不一致。', 422)
        if value['snapshotDigest'] != digest({k: v for k, v in value.items() if k != 'snapshotDigest'}):
            fail('INVALID_SNAPSHOT', '组件快照摘要不一致。', 422)
        active.remove(id(value))

    visit(snapshot, 0)
    return copy.deepcopy(snapshot)


def build_snapshot(library, owner, asset_id, version_id):
    library.workbench.auth.get_user(owner)
    seen = set()

    def capture(aid, vid):
        if vid in seen or len(seen) >= 8:
            fail('INVALID_SNAPSHOT', '组件依赖存在循环或层级过深。', 422)
        asset = library.asset(owner, aid)
        if asset.get('deletedAt'):
            fail('CAPABILITY_BLOCKED', '不能从已删除组件建立新快照。', 409)
        version = library.version(owner, aid, vid)
        if version.get('containsSecret'):
            fail('EMBEDDED_SECRET', '来源包疑似含秘密，不能复制到App。', 403)
        if version.get('embeddedSnapshot'):
            return validate_snapshot(version['embeddedSnapshot'])
        seen.add(vid)
        deps = [capture(d['assetId'], d['versionId']) for d in version.get('dependencyLocks', [])]
        seen.remove(vid)
        return snapshot_from_package(version['manifest'], library.files(owner, version), deps,
            {key: version[key] for key in ('assetId', 'versionId', 'contentDigest', 'configurationId')})

    return capture(asset_id, version_id)


def _files(snapshot):
    return {path: base64.b64decode(data, validate=True) for path, data in snapshot['files'].items()}


def standard_skill_files(manifest, files):
    """Pure standard layout; never silently omit resources outside its root."""
    if manifest.get('kind') != 'skill':
        fail('UNSUPPORTED_EXPORT', '只有Skill可导出Agent Skills标准包。', 422)
    entry = PurePosixPath(manifest['skill']['entry'])
    if entry.name != 'SKILL.md':
        fail('UNSUPPORTED_EXPORT', '标准入口必须命名为SKILL.md，请先整理入口。', 422)
    prefix = '' if str(entry.parent) == '.' else str(entry.parent) + '/'
    if any(prefix and not path.startswith(prefix) for path in files):
        fail('UNSUPPORTED_EXPORT', '存在标准Skill目录外资源，无法无损转换；请先整理相对引用。', 422)
    result = {path[len(prefix):]: raw for path, raw in files.items()}
    if 'SKILL.md' not in result:
        fail('INVALID_SNAPSHOT', 'Skill入口缺失。', 422)
    return result


def export_snapshot(snapshot):
    """Portable contents only; recipient bindings and author authority never travel."""
    value = validate_snapshot(snapshot)
    if has_secret({**_files(value), '__manifest__': canonical(value['manifest']).encode()}):
        fail('EXPORT_CONTAINS_SECRET', '组件内容疑似含秘密，不能导出。', 403)
    value['dependencies'] = [export_snapshot(d) for d in value['dependencies']]
    value['provenance'] = {'contentDigest': value['contentDigest']}
    value['snapshotDigest'] = digest({k: v for k, v in value.items() if k != 'snapshotDigest'})
    return value


def skill_material(snapshot):
    value = validate_snapshot(snapshot)
    manifest, files = value['manifest'], _files(value)
    if manifest['kind'] != 'skill':
        fail('INVALID_SNAPSHOT', '所选组件不是Skill。', 422)
    entry = manifest['skill']['entry']
    try:
        instructions = files[entry].decode('utf-8-sig')
    except (KeyError, UnicodeError):
        fail('INVALID_SNAPSHOT', 'Skill入口缺失或不是UTF-8。', 422)
    return {'name': manifest['name'], 'description': manifest['description'], 'entry': entry,
            'instructions': instructions, 'files': files, 'scripts': manifest['skill'].get('scripts', []),
            'dependencies': value['dependencies'], 'permissions': manifest['permissions'],
            'contentDigest': value['contentDigest'], 'snapshotDigest': value['snapshotDigest']}


class _Runtime:
    """Read-only snapshot overlay; durable invocations remain in the account store."""
    def __init__(self, library, owner, snapshot, bindings=None):
        self.library, self.owner = library, owner
        self.registered_counts = {}
        self.bindings = bindings or {}
        if not isinstance(self.bindings, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in self.bindings.items()):
            fail('INVALID_REQUEST', '连接绑定必须是来源身份到当前账号连接ID的映射。')
        self.snapshots, self.versions = {}, {}
        self.root = self._add(validate_snapshot(snapshot))
        for version in self.versions.values():
            version['configuration'] = self.configuration(owner, version)
            version['configurationId'] = version['configuration']['configurationId']

    def __getattr__(self, name):
        return getattr(self.library, name)

    def _add(self, snapshot):
        key = 'snapshot_' + snapshot['snapshotDigest']
        if key in self.versions:
            return self.versions[key]
        deps = [self._add(d) for d in snapshot['dependencies']]
        version = {'assetId': key, 'versionId': key, 'manifest': snapshot['manifest'],
                   'contentDigest': snapshot['contentDigest'], 'snapshotDigest': snapshot['snapshotDigest'],
                   'readiness': 'unverified', 'issues': [],
                   'dependencyLocks': [{'assetId': d['assetId'], 'versionId': d['versionId']} for d in deps]}
        self.snapshots[key], self.versions[key] = snapshot, version
        return version

    def files(self, owner, version):
        if owner != self.owner:
            fail('NOT_FOUND', '组件不属于当前调用。', 404)
        return _files(self.snapshots[version['versionId']])

    def version(self, owner, asset_id, version_id):
        if owner != self.owner or asset_id != version_id or version_id not in self.versions:
            fail('NOT_FOUND', '组件不属于当前调用。', 404)
        return self.versions[version_id]

    def configuration(self, owner, version, dependencies=None):
        config = self.library.configuration(owner, version, dependencies)
        execution = version['manifest'].get('tool', {}).get('execution', {})
        if execution.get('type') == 'mcp_http' and execution['connectionIdentity'] in self.bindings:
            connection = self.store.get(owner, 'connection', self.bindings[execution['connectionIdentity']])
            if connection['sourceIdentity'] != execution['connectionIdentity']:
                fail('INVALID_REQUEST', '连接来源与组件需求不一致。', 409)
            config['connection'] = {k: connection.get(k) for k in ('connectionId', 'revision', 'credentialRevision', 'endpoint')}
        config['configurationId'] = 'config_' + digest({k: v for k, v in config.items() if k != 'configurationId'})
        return config

    def admit(self, owner, asset_id, version_id, configuration_id=None, trial=False, historical=False, _seen=None):
        self.workbench.auth.get_user(owner)
        version = self.version(owner, asset_id, version_id)
        manifest = version['manifest']
        problems = inspect(manifest, self.files(owner, version))
        if has_secret({'manifest': canonical(manifest).encode()}):
            fail('EMBEDDED_SECRET', '组件清单疑似包含私密凭据。', 403)
        execution = manifest.get('tool', {}).get('execution', {})
        from ..workbench.retired_capabilities import check
        check(execution.get('handlerId'))
        if execution.get('type') == 'builtin':
            spec = self.builtins.get(execution.get('handlerId'))
            if spec and spec.available() and spec.parameters == manifest['tool']['inputSchema']:
                problems = [p for p in problems if p['code'] != 'MISSING_IMPLEMENTATION']
                if manifest['permissions']['network'] != 'connector_only':
                    fail('CAPABILITY_BLOCKED', '内置远端查询需要受控连接声明。', 403)
        if problems:
            fail('CAPABILITY_BLOCKED', '组件在当前运行环境不可执行。', 409, {'issues': problems})
        config = self.configuration(owner, version)
        if execution.get('type') == 'python_connector':
            if self.data_connections is None:
                fail('MISSING_CONNECTION', '平台数据连接不可用。', 503)
            self.data_connections.check(execution['connections'])
        if execution.get('type') == 'mcp_http':
            ref = config.get('connection')
            if not ref:
                fail('MISSING_CONNECTION', '接收账号须配置唯一的对应MCP连接。', 409)
            connection = self.store.get(owner, 'connection', ref['connectionId'])
            if not connection.get('configured') or connection.get('enabled') is False or not self.trusted(connection, execution):
                fail('CAPABILITY_BLOCKED', '当前MCP连接未配置、已停用或只读准入已撤销。', 403)
        if configuration_id and config['configurationId'] != configuration_id:
            fail('STALE_CONFIGURATION', '组件执行期间运行配置变化，须重新验证。', 409)
        for dep in version['dependencyLocks']:
            self.admit(owner, dep['assetId'], dep['versionId'], _seen=_seen)
        return version

    def invoke(self, *args, **kwargs):
        from .service import CapabilityLibrary
        return CapabilityLibrary.invoke(self, *args, **kwargs)


def runtime_digest(library, owner, snapshot, bindings=None, *, check=True):
    runtime = _Runtime(library, owner, snapshot, bindings)
    records = []
    for version in runtime.versions.values():
        if check:
            runtime.admit(owner, version['assetId'], version['versionId'])
        records.append({'snapshotDigest': version['snapshotDigest'],
                        'configurationId': runtime.configuration(owner, version)['configurationId']})
    return digest(sorted(records, key=lambda r: r['snapshotDigest']))


def bind_snapshot(library, owner, snapshot, bindings=None):
    """Check recipient-owned connections; caller stores bindings in private config."""
    value = export_snapshot(snapshot)
    runtime_digest(library, owner, value, bindings)
    return value


def install_snapshot(library, owner, snapshot, *, target=None, bindings=None, authoring_provenance=None, test_cases=None):
    """Save one owned closed component; no source switches or authority travel."""
    value = export_snapshot(snapshot)
    if authoring_provenance is not None and (not isinstance(authoring_provenance, dict) or len(canonical(authoring_provenance).encode()) > 32768):
        fail('INVALID_REQUEST', '来源声明必须是最多32 KiB的对象。')
    library.workbench.auth.get_user(owner)
    manifest, files = value['manifest'], _files(value)
    from .test_cases import prepare_test_cases
    prepared_cases = prepare_test_cases(manifest, files, test_cases)
    if test_cases is not None:
        for case in prepared_cases:
            if case['fileIds']:
                library.file_grants(owner, case['fileIds'], manifest)
    with library.store.transaction():
        if target:
            asset = library.asset(owner, target)
            if asset.get('deletedAt') or asset['kind'] != manifest['kind'] or asset['sourceIdentity'] != manifest['source']['identity']:
                fail('INVALID_PACKAGE', '目标资产类型、来源不一致或已删除。', 409)
        else:
            asset = {'assetId': uid('asset'), 'name': manifest['name'], 'description': manifest['description'],
                     'kind': manifest['kind'], 'sourceIdentity': manifest['source']['identity'], 'scope': 'personal',
                     'lifecycle': 'active', 'enabled': False, 'activeVersionId': None, 'revision': 1, 'createdAt': now()}
            library.store.put(owner, 'asset', asset['assetId'], asset)
        prior = [v for v in library.store.list(owner, 'version') if v['assetId'] == asset['assetId']]
        raw = canonical(value).encode()
        library.store.blob(owner, digest(raw), raw)
        for data in files.values():
            library.store.blob(owner, digest(data), data)
        version = {'versionId': uid('ver'), 'assetId': asset['assetId'], 'number': len(prior) + 1,
                   'contentDigest': value['contentDigest'], 'manifest': manifest, 'originalId': digest(raw),
                   'originalName': 'component.json', 'createdAt': now(), 'containsSecret': False,
                   'embeddedSnapshot': value, 'validationPolicy': 'original_and_new',
                   'componentBindings': copy.deepcopy(bindings or {}), 'testCases': prepared_cases}
        if authoring_provenance is not None:
            version['authoringProvenance'] = {'claims': copy.deepcopy(authoring_provenance), 'historicalEvidenceVerified': False}
        version = library.check(owner, version)
        asset.update(latestVersionId=version['versionId'], revision=asset['revision'] + 1)
        if not asset.get('activeVersionId'):
            asset.update(readiness=version['readiness'], issues=version['issues'])
        library.store.put(owner, 'asset', asset['assetId'], asset)
        return {'status': 'completed', 'assetId': asset['assetId'], 'versionId': version['versionId'],
                'readiness': version['readiness'], 'runtimeReadiness': version['readiness'],
                'contentValidity': version['contentValidity'], 'issues': version['issues'], 'deduplicated': False}


def tool_draft_from_nodes(definition, nodes):
    """Extract explicit fixed nodes without inventing missing upstream evidence."""
    if not nodes or len(nodes) > 16 or any(n.get('kind') not in {'code', 'tool'} for n in nodes):
        fail('SOURCE_NEEDS_EDIT', '固定Tool需要1至16个代码或Tool步骤；动态Skill应保留为Skill。', 422)
    selected = {n['id'] for n in nodes}
    ordered = [n for n in definition['nodes'] if n['id'] in selected]
    if len(ordered) != len(nodes):
        fail('INVALID_SELECTION', '所选步骤重复或不属于此App。', 422)
    ids = {n['id']: 'step_' + str(i + 1) for i, n in enumerate(ordered)}
    dependencies, files, steps, seen = {}, [], [], set()
    input_schema = copy.deepcopy(definition.get('inputSchema', {'type': 'object'}))
    permissions = {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False}
    for node in ordered:
        sid = ids[node['id']]
        step = {'id': sid, 'inputBindings': {}, 'outputSchema': copy.deepcopy(node.get('delivery', {}).get('schema', {})),
                'delivery': copy.deepcopy(node.get('delivery', {}))}
        if 'files' in step['delivery']:
            step['delivery']['files'] = [{'name': item} if isinstance(item, str) else item
                                         for item in step['delivery']['files']]
        for key, binding in node.get('inputBindings', {}).items():
            source = binding.get('from')
            if source in {'step', 'artifact'} and binding.get('stepId') not in seen:
                fail('SOURCE_NEEDS_EDIT', '所选步骤依赖未选入的前序结果，请加入前序步骤或改成明确输入。', 422,
                     {'gaps': [{'nodeId': node['id'], 'field': key, 'stepId': binding.get('stepId')}]})
            if source == 'literal':
                converted = {'value': copy.deepcopy(binding.get('value'))}
            elif source in {'input', 'step'}:
                path = binding.get('path', '')
                parts = [int(p) if p.isdigit() else p for p in path.split('.')] if path else []
                converted = {'source': 'input' if source == 'input' else 'steps.' + ids[binding['stepId']], 'path': parts}
            elif source == 'file':
                alias = binding['input']
                permissions['inputFiles'] = True
                input_schema.setdefault('properties', {})['file_' + alias] = {
                    'type': 'string', 'description': '本次明确授权文件的 /input/ 路径：' + alias}
                required = input_schema.setdefault('required', [])
                if 'file_' + alias not in required:
                    required.append('file_' + alias)
                converted = {'inputFile': 'file_' + alias}
            elif source == 'artifact':
                converted = {'artifact': {'stepId': ids[binding['stepId']], 'name': binding['name']}}
            else:
                fail('SOURCE_NEEDS_EDIT', '所选步骤存在无法转换的输入绑定。', 422, {'nodeId': node['id'], 'field': key})
            step['inputBindings'][key] = converted
        if node['kind'] == 'code':
            directory = 'original/steps/' + sid + '/'
            # Preserve all relative resource paths and use a reserved entry filename.
            entry = '__cd80_entry__.py'
            if any(r['path'] == entry for r in node.get('files', [])):
                fail('SOURCE_NEEDS_EDIT', '资源名称与保存入口冲突。', 422)
            files.append({'path': directory + entry, 'text': node['code']})
            files.extend({**r, 'path': directory + safe_path(r['path'])} for r in node.get('files', []))
            step.update(kind='python_wasi', path=directory + entry, callable='run', fileAliases=True)
            permissions['outputFiles'] = True
        else:
            dependency = validate_snapshot(node['component'])
            dm = dependency['manifest']
            if dm['kind'] != 'tool':
                fail('SOURCE_NEEDS_EDIT', '动态Skill不能转换为固定Tool步骤。', 422)
            identity = dm['source']['identity']
            key = (identity, dependency['contentDigest'])
            if any(k[0] == identity and k != key for k in dependencies):
                fail('DEPENDENCY_CONFLICT', '同一来源选择了不同工具版本，请先明确绑定。', 422)
            dependencies[key] = dependency
            step.update(kind='tool', sourceIdentity=identity, contentDigest=dependency['contentDigest'])
            for flag in ('inputFiles', 'outputFiles'):
                permissions[flag] |= dm['permissions'][flag]
            if dm['permissions']['network'] != 'none':
                permissions['network'] = 'connector_only'
            if dm['permissions']['externalEffects'] != 'none':
                permissions['externalEffects'] = dm['permissions']['externalEffects']
        steps.append(step)
        seen.add(node['id'])
    name = (definition.get('name') or '提取') + '工具'
    description = definition.get('description') or name
    manifest = {'format': 'cd80.capability/1', 'kind': 'tool', 'name': name, 'description': description,
                'source': {'identity': 'user/' + uid('fixed-tool'), 'format': 'platform'}, 'files': [],
                'permissions': permissions, 'dependencies': [{'sourceIdentity': k[0], 'contentDigest': k[1],
                    'required': True, 'reason': '固定步骤使用的完整工具版本。'} for k in dependencies],
                'tool': {'inputSchema': input_schema, 'outputSchema': steps[-1]['outputSchema'],
                         'execution': {'type': 'fixed_pipeline', 'protocolVersion': '1', 'steps': steps}}}
    return {'kind': 'tool', 'name': name, 'description': description, 'manifest': manifest,
            'files': files, 'dependencySnapshots': list(dependencies.values()),
            'authoringProvenance': {'source': 'selected_app_nodes', 'nodes': [
                {'nodeId': n['id'], 'sourceClaim': copy.deepcopy(n.get('provenance', {})),
                 **({'codeSha256': digest(n['code'].encode('utf-8'))} if n['kind'] == 'code' else
                    {'contentDigest': n['component']['contentDigest']})} for n in ordered]}}


def invoke_snapshot(library, owner, snapshot, arguments, *, files=None, stopped=None, context=None, bindings=None):
    runtime = _Runtime(library, owner, snapshot, bindings)
    version = runtime.root
    if version['manifest']['kind'] != 'tool':
        fail('INVALID_REQUEST', 'Skill须由独立Skill运行器调用。')
    record = runtime.invoke(owner, {'assetId': version['assetId'], 'versionId': version['versionId'],
        'configurationId': version['configurationId'], 'input': arguments, 'fileIds': [],
        '_componentContext': copy.deepcopy(context or {}),
        'taskId': (context or {}).get('parentRunId') or (context or {}).get('runId')},
        stopped=stopped, trial=True, task_files=files)
    record.update(componentContext=copy.deepcopy(context or {}), snapshotDigest=snapshot['snapshotDigest'])
    library.store.put(owner, 'invocation', record['invocationId'], record)
    return {**record, 'files': {item['name']: library.store.blob(owner, item['id']) for item in record.get('artifacts', [])}}


def invoke_skill_script(library, owner, snapshot, path, arguments, *, files=None, stopped=None, context=None, bindings=None):
    runtime = _Runtime(library, owner, snapshot, bindings)
    version = runtime.root
    runtime.admit(owner, version['assetId'], version['versionId'])
    material = skill_material(snapshot)
    if path not in {s['path'] for s in material['scripts']}:
        fail('CAPABILITY_BLOCKED', '脚本不在此Skill快照的声明内。', 403)
    if files and not material['permissions']['inputFiles']:
        fail('FILE_NOT_GRANTED', 'Skill没有声明输入文件权限。', 403)
    stopped = stopped or threading.Event()
    record = {'invocationId': uid('inv'), 'assetId': version['assetId'], 'versionId': version['versionId'],
              'configurationId': version['configurationId'], 'snapshotDigest': snapshot['snapshotDigest'],
              'scriptPath': path, 'input': arguments, 'componentContext': copy.deepcopy(context or {}),
              'status': 'running', 'startedAt': now()}
    library.store.put(owner, 'invocation', record['invocationId'], record)
    try:
        output = library.executor.execute(material['files'][path].decode('utf-8'), arguments, files or {},
                    stopped, package_files=material['files'], package_entry=path)
        if stopped.is_set():
            fail('CANCELLED', 'Skill脚本已取消。', 409)
        runtime.admit(owner, version['assetId'], version['versionId'], version['configurationId'])
        if output['files'] and not material['permissions']['outputFiles']:
            fail('CAPABILITY_BLOCKED', 'Skill未声明输出文件权限。', 403)
        artifacts = [library.download(owner, name, data) for name, data in output['files'].items()]
        record.update(status='succeeded', completedAt=now(), logs=output['logs'], artifacts=artifacts,
                      result={'content': output['result'], 'sources': [], 'artifacts': artifacts})
        return {**record, 'files': output['files']}
    except Exception as error:
        error.invocationId = record['invocationId']
        if hasattr(error, 'details'):
            error.details = {**error.details, 'invocationId': record['invocationId']}
        record.update(status='cancelled' if stopped.is_set() else 'failed', completedAt=now(),
                      error={'code': getattr(error, 'code', 'EXECUTION_FAILED'), 'error': str(error)})
        raise
    finally:
        library.store.put(owner, 'invocation', record['invocationId'], record)
