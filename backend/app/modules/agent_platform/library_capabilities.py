"""Account-local capability snapshots and admission for saved applications."""
from __future__ import annotations

import copy
import hashlib
from pathlib import PurePosixPath

from ..workbench.errors import GatewayError


PORTABLE_FIELDS = ('sourceIdentity', 'contentDigest', 'kind')
LOCAL_FIELDS = ('assetId', 'versionId', 'configurationId')
MAX_SKILL_RESOURCES = 16
MAX_RESOURCE_CHARS = 12000
MAX_SKILL_CONTEXT_CHARS = 36000
MAX_APPLICATION_SKILL_CHARS = 60000


def reference(version):
    manifest = version['manifest']
    return {**{key: version[key] for key in LOCAL_FIELDS},
            'sourceIdentity': manifest['source']['identity'],
            'contentDigest': version['contentDigest'], 'kind': manifest['kind']}


def admitted(library, owner, ref, kind):
    if library is None:
        raise GatewayError('能力库尚未就绪。', 503, 'capability_library_unavailable')
    if not isinstance(ref, dict) or any(not isinstance(ref.get(key), str) or not ref[key] for key in LOCAL_FIELDS):
        raise GatewayError('应用能力引用缺少精确版本或配置，请重新绑定。', 400, 'application_dependency_invalid')
    version = library.admit(owner, ref['assetId'], ref['versionId'], ref['configurationId'])
    actual = reference(version)
    if actual['kind'] != kind or any(ref.get(key) != actual[key] for key in PORTABLE_FIELDS):
        raise GatewayError('应用能力内容与引用不一致，请重新绑定。', 409, 'application_dependency_changed')
    return version


def skill_snapshot(library, owner, version):
    """Read bounded method references only from the locked, owner-local package."""
    manifest = version['manifest']
    if manifest['skill'].get('scripts'):
        raise GatewayError('此Skill包含脚本，固定流程尚不支持Skill脚本节点；请在研究应用或主对话中使用。',
                           409, 'application_skill_unsupported')
    entry = manifest['skill']['entry']
    prefix = str(PurePosixPath(entry).parent / 'references') + '/'
    index = {item['path']: item for item in manifest['files']}
    paths = sorted(path for path in index if path.startswith(prefix))
    if len(paths) > MAX_SKILL_RESOURCES:
        raise GatewayError('此Skill参考资料超过16个文件，不能完整用于固定流程。', 409, 'application_skill_unsupported')
    files = library.files(owner, version)
    def read(path):
        data, expected = files.get(path), index.get(path)
        if (not isinstance(data, bytes) or expected is None or len(data) != expected['size'] or
                hashlib.sha256(data).hexdigest() != expected['sha256']):
            raise GatewayError('Skill入口或参考资料与锁定的内容摘要不一致，请重新导入并验证。',
                               409, 'application_dependency_changed')
        try:
            content = data.decode('utf-8')
        except UnicodeError:
            raise GatewayError('Skill参考资料必须是UTF-8文本，当前文件不能用于固定流程：' + path,
                               409, 'application_skill_unsupported') from None
        if '\x00' in content or len(content) > MAX_RESOURCE_CHARS:
            raise GatewayError('Skill入口或参考资料不是可读文本或超过单文件12000字符限制：' + path,
                               409, 'application_skill_unsupported')
        return {**expected, 'content': content}
    content = read(entry)['content']
    resources = [read(path) for path in paths]
    if len(content) + sum(len(item['content']) for item in resources) > MAX_SKILL_CONTEXT_CHARS:
        raise GatewayError('此Skill入口与参考资料合计超过36000字符，不能完整用于固定流程。',
                           409, 'application_skill_unsupported')
    return {'content': content, 'resources': resources}


def catalog(library, owner, selected=None, *, snapshots=False):
    if library is None:
        return []
    if selected is not None and (not isinstance(selected, list) or len(selected) > 8 or
            any(not isinstance(item, dict) or not isinstance(item.get('assetId'), str) or not isinstance(item.get('versionId'), str) for item in selected)):
        raise GatewayError('最多选择8项个人能力的精确版本。', 400)
    wanted = {item['assetId']: item['versionId'] for item in selected or []}
    if any(wanted[item['assetId']] != item['versionId'] for item in selected or []):
        raise GatewayError('同一能力不能同时选择不同版本。', 400)
    assets = [a for a in library.store.list(owner, 'asset') if not a.get('deletedAt')]
    if wanted and set(wanted) - {a['assetId'] for a in assets}:
        raise GatewayError('所选能力不存在或无权访问。', 404)
    for asset_id, version_id in list(wanted.items()):
        version = library.version(owner, asset_id, version_id)
        if version.get('embeddedSnapshot'):
            # Owned components are not ambient personal-library selections.
            continue
        for dependency in version.get('dependencyLocks', []):
            if dependency['assetId'] in wanted and wanted[dependency['assetId']] != dependency['versionId']:
                raise GatewayError('所选工具版本与Skill锁定的依赖冲突。', 409)
            wanted[dependency['assetId']] = dependency['versionId']
    result = []
    for asset in sorted(assets, key=lambda a: a['assetId']):
        if wanted and asset['assetId'] not in wanted:
            continue
        version_id = wanted.get(asset['assetId']) or asset.get('activeVersionId') or asset.get('latestVersionId')
        version = library.version(owner, asset['assetId'], version_id)
        manifest = version['manifest']
        item = {'id': asset['assetId'], 'assetId': asset['assetId'], 'versionId': version_id,
                'name': asset['name'], 'description': asset['description'], 'kind': asset['kind'],
                'capabilityRef': reference(version), 'available': True, 'reason': ''}
        try:
            admitted(library, owner, item['capabilityRef'], item['kind'])
            if item['kind'] == 'tool':
                from .contracts import check_schema
                check_schema(manifest['tool']['inputSchema'])
                check_schema(manifest['tool'].get('outputSchema', {'type': 'object'}))
                item.update(inputSchema=manifest['tool']['inputSchema'], outputSchema=manifest['tool'].get('outputSchema', {'type': 'object'}),
                            inputFiles=manifest['permissions']['inputFiles'])
            else:
                if version.get('embeddedSnapshot'):
                    from ..capability_library.components import skill_material, build_snapshot
                    component = build_snapshot(library, owner, asset['assetId'], version_id)
                    material = skill_material(component)
                    item.update(skillRunRequired=True,
                        dependencies=[{'sourceIdentity': d['manifest']['source']['identity'], 'contentDigest': d['contentDigest'],
                                       'kind': 'tool', 'owned': True} for d in component['dependencies']],
                        resources=[dict(f) for f in component['manifest']['files']],
                        entry=material['entry'])
                    if snapshots:
                        item.update(content=material['instructions'], componentSnapshot=component)
                    result.append(item)
                    continue
                item['dependencies'] = [reference(library.version(owner, lock['assetId'], lock['versionId']))
                                        for lock in version.get('dependencyLocks', [])]
                snapshot = skill_snapshot(library, owner, version)
                item['resources'] = [{key: value for key, value in resource.items() if snapshots or key != 'content'}
                                     for resource in snapshot['resources']]
                if snapshots:
                    item['content'] = snapshot['content']
        except GatewayError as error:
            item.update(available=False, reason=str(error))
        if wanted and not item['available']:
            raise GatewayError(item['name'] + '不可用于应用：' + item['reason'], 409, 'application_dependency_unavailable')
        result.append(item)
    return result


def resolve_snapshots(value, components):
    """The planner can reference only the exact account catalog it was given."""
    spec = copy.deepcopy(value)
    by_ref = {(c['assetId'], c['versionId']): c for c in components if c['available']}
    for collection, kind in (('tools', 'tool'), ('skills', 'skill')):
        if not isinstance(spec.get(collection, []), list):
            continue
        for item in spec.get(collection, []):
            if not isinstance(item, dict) or 'capabilityRef' not in item:
                continue
            ref = item['capabilityRef']
            candidate = by_ref.get((ref.get('assetId'), ref.get('versionId'))) if isinstance(ref, dict) else None
            if candidate is None or candidate['kind'] != kind:
                raise GatewayError('生成结果引用了未提供的个人能力。', 400, 'application_dependency_invalid')
            item['capabilityRef'] = copy.deepcopy(candidate['capabilityRef'])
            if kind == 'tool':
                if 'code' in item or 'registeredTool' in item:
                    raise GatewayError('个人工具引用不能同时包含其他实现。', 400)
                item['inputSchema'] = copy.deepcopy(candidate['inputSchema'])
                item['outputSchema'] = copy.deepcopy(candidate['outputSchema'])
            else:
                if 'registeredSkill' in item:
                    raise GatewayError('个人Skill引用不能同时引用内置Skill。', 400)
                item['content'] = candidate['content']
    return spec


def validate_dependencies(spec, library, owner, *, with_context=False):
    if with_context:
        # Runtime resources are transient context. Portable application definitions
        # retain their exact capability content identity, not duplicate file bytes.
        spec = copy.deepcopy(spec)
    tools = {item['id']: item for item in spec.get('tools', [])}
    executed_tools = {step['tool'] for step in spec.get('pipeline', []) if step.get('kind') == 'tool'}
    skill_chars = sum(len(item.get('content', '')) for item in spec.get('skills', []))
    for collection, kind in (('tools', 'tool'), ('skills', 'skill')):
        for item in spec.get(collection, []):
            if 'capabilityRef' not in item:
                continue
            version = admitted(library, owner, item['capabilityRef'], kind)
            if kind == 'tool':
                tool = version['manifest']['tool']
                if item['inputSchema'] != tool['inputSchema'] or item['outputSchema'] != tool.get('outputSchema', {'type': 'object'}):
                    raise GatewayError('个人工具接口与应用快照不一致。', 409, 'application_dependency_changed')
            else:
                if version.get('embeddedSnapshot'):
                    raise GatewayError('自足Skill需要独立Skill运行节点，请通过统一App保留完整方法与依赖。',
                                       409, 'application_skill_requires_unified_app')
                snapshot = skill_snapshot(library, owner, version)
                if snapshot['content'] != item['content']:
                    raise GatewayError('个人Skill正文与应用快照不一致。', 409, 'application_dependency_changed')
                skill_chars += sum(len(resource['content']) for resource in snapshot['resources'])
                if with_context:
                    item['resources'] = snapshot['resources']
                linked = [tools[tool_id]['capabilityRef'] for tool_id in item.get('requiredTools', [])
                          if tool_id in executed_tools and 'capabilityRef' in tools.get(tool_id, {})]
                for lock in version.get('dependencyLocks', []):
                    if not any(all(ref[key] == lock[key] for key in LOCAL_FIELDS) for ref in linked):
                        raise GatewayError('应用未执行此Skill锁定的工具依赖，请重新绑定requiredTools并生成流程。',
                                           409, 'application_dependency_changed')
    if skill_chars > MAX_APPLICATION_SKILL_CHARS:
        raise GatewayError('本应用Skill正文与参考资料合计超过60000字符，请拆分流程或减少方法。',
                           409, 'application_skill_unsupported')
    return spec
