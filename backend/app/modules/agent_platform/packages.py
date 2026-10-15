"""Portable application definitions; runs, credentials and grants never travel."""
from __future__ import annotations

import copy
from uuid import uuid4

from .contracts import canonical, digest, identifier, text, validate_agent_spec
from .library_capabilities import PORTABLE_FIELDS, reference, validate_dependencies
from .shared_capabilities import resolve_references
from ..workbench.errors import GatewayError


def shareable(value):
    from ..capability_library.sharing import assert_shareable
    assert_shareable(value)


def export_application(platform, owner, agent_ref):
    spec = copy.deepcopy(platform.agent(owner, agent_ref))
    shareable(spec)
    provenance = {'name': spec['name'], 'version': spec['version'], 'definitionDigest': digest(spec)}
    spec.pop('id', None)
    spec['origin'] = 'generated'
    dependencies = []
    for group in ('tools', 'skills'):
        for item in spec[group]:
            if 'capabilityRef' in item:
                item['capabilityRef'] = {key: item['capabilityRef'][key] for key in PORTABLE_FIELDS}
                dependencies.append({'bindingId': item['capabilityRef']['kind'] + ':' + item['id'],
                                     'componentId': item['id'], **item['capabilityRef']})
    package = {'format': 'infinity-application', 'formatVersion': '1', 'kind': 'pipeline',
               'definition': spec, 'dependencies': dependencies, 'provenance': provenance}
    shareable(package)
    return package


def import_application(platform, owner, body, *, source=None):
    key = text(body.get('idempotencyKey'), '请求标识', 150)
    # Publication identity comes only from the trusted server caller. A JSON
    # body/provenance field cannot turn an ordinary import into a publication.
    request = {'body': body, 'source': source} if source is not None else body
    previous = platform.repository.request_result(owner, 'application_import', key, request)
    if previous is not None:
        return previous
    package = body.get('package')
    if (not isinstance(package, dict) or set(package) - {'format', 'formatVersion', 'kind', 'definition', 'dependencies', 'provenance'} or
            package.get('format') != 'infinity-application' or package.get('formatVersion') != '1' or package.get('kind') != 'pipeline'):
        raise GatewayError('请选择infinity-application版本1的pipeline应用包。', 400, 'invalid_application_package')
    try:
        if len(canonical(package)) > 240000:
            raise GatewayError('应用包超过240000字符限制。', 413)
    except (TypeError, ValueError, RecursionError):
        raise GatewayError('应用包必须是有限JSON数据。', 400) from None
    shareable(package)
    original = package.get('definition')
    if not isinstance(original, dict):
        raise GatewayError('应用包缺少定义。', 400)
    spec = copy.deepcopy(original)
    bindings = body.get('dependencyBindings', {})
    if not isinstance(bindings, dict):
        raise GatewayError('依赖绑定必须是对象。', 400)
    dependencies = []
    used_bindings = set()
    library = getattr(platform.workbench, 'capabilities', None)
    component_counts = {}
    for group in ('tools', 'skills'):
        if not isinstance(spec.get(group), list):
            raise GatewayError('应用工具与Skill必须是数组。', 400)
        for item in spec[group]:
            if isinstance(item, dict) and 'capabilityRef' in item:
                identifier(item.get('id'), '组件id')
                component_counts[item['id']] = component_counts.get(item['id'], 0) + 1
    for group, kind in (('tools', 'tool'), ('skills', 'skill')):
        for item in spec[group]:
            if not isinstance(item, dict) or 'capabilityRef' not in item:
                continue
            identifier(item.get('id'), '组件id')
            ref = item['capabilityRef']
            if not isinstance(ref, dict) or set(ref) != set(PORTABLE_FIELDS) or ref.get('kind') != kind:
                raise GatewayError('包内能力只允许来源身份、内容摘要和类型，不能携带个人配置。', 400)
            dependencies.append({'componentId': item.get('id'), **ref})
            if library is None:
                raise GatewayError('能力库尚未就绪。', 503)
            binding_key = kind + ':' + item['id']
            if item['id'] in bindings:
                if component_counts[item['id']] != 1:
                    raise GatewayError('同名Tool与Skill需要分别使用tool:id和skill:id绑定。', 409,
                                       'application_dependency_ambiguous_binding')
                if binding_key in bindings:
                    raise GatewayError('同一组件不能同时提供旧id键和类型限定绑定键。', 400)
                binding_key = item['id']
            binding = bindings.get(binding_key)
            if binding is not None:
                used_bindings.add(binding_key)
                if not isinstance(binding, dict) or set(binding) != {'assetId', 'versionId'}:
                    raise GatewayError('依赖绑定需要个人assetId和versionId。', 400)
                identifier(binding['assetId'], '依赖assetId')
                identifier(binding['versionId'], '依赖versionId')
                candidates = [library.version(owner, binding['assetId'], binding['versionId'])]
            else:
                candidates = [v for v in library.store.list(owner, 'version')
                              if v.get('contentDigest') == ref['contentDigest'] and
                              v['manifest']['source']['identity'] == ref['sourceIdentity'] and v['manifest']['kind'] == kind and
                              not library.asset(owner, v['assetId']).get('deletedAt')]
            if len(candidates) != 1:
                raise GatewayError('缺少指定内容的个人能力，或存在多个副本；请添加依赖或明确dependencyBindings。', 409, 'application_dependency_unresolved')
            version = candidates[0]
            actual = reference(version)
            if any(actual[field] != ref[field] for field in PORTABLE_FIELDS):
                raise GatewayError('绑定能力与应用包要求的内容不一致。', 409, 'application_dependency_changed')
            item['capabilityRef'] = actual
    if set(bindings) - used_bindings:
        raise GatewayError('依赖绑定包含未使用的组件。', 400)
    supplied_dependencies = package.get('dependencies', [])
    if not isinstance(supplied_dependencies, list):
        raise GatewayError('应用包依赖清单与定义不一致。', 400, 'invalid_application_package')
    normalized_dependencies = []
    for dependency in supplied_dependencies:
        if not isinstance(dependency, dict):
            raise GatewayError('应用包依赖清单与定义不一致。', 400, 'invalid_application_package')
        normalized = {key: value for key, value in dependency.items() if key != 'bindingId'}
        if 'bindingId' in dependency and dependency['bindingId'] != str(dependency.get('kind')) + ':' + str(dependency.get('componentId')):
            raise GatewayError('应用包依赖绑定标识与组件不一致。', 400, 'invalid_application_package')
        normalized_dependencies.append(normalized)
    if normalized_dependencies != dependencies:
        raise GatewayError('应用包依赖清单与定义不一致。', 400, 'invalid_application_package')
    spec['id'], spec['version'], spec['origin'] = str(uuid4()), '1', 'generated'
    if body.get('name') is not None:
        spec['name'] = text(body['name'], '应用名称', 2000)
    spec = validate_agent_spec(resolve_references(spec, platform.workbench.runtime.tools, platform.workbench.runtime.skills))
    validate_dependencies(spec, library, owner)
    metadata = {'kind': 'import', 'packageDigest': digest(package), 'provenance': package.get('provenance', {})}
    if source is not None:
        metadata.update(copy.deepcopy(source))
    def save_modern(db, imported):
        from .asset_legacy import migrate_pipeline
        doc = migrate_pipeline(platform.workbench.asset_apps, owner, imported)
        provenance = {'kind': 'legacy_pipeline_import', 'packageDigest': digest(package),
                      'source': copy.deepcopy(source) if source is not None else None}
        return platform.workbench.asset_apps._save_in_transaction(db, owner,
            {'definition': doc}, provenance=provenance)
    return platform.repository.copy_definition(owner, spec, key, 'application_import', request, metadata,
                                               save_modern=save_modern)


def fork_application(platform, owner, body):
    key = text(body.get('idempotencyKey'), '请求标识', 150)
    previous = platform.repository.request_result(owner, 'application_fork', key, body)
    if previous is not None:
        return previous
    source_ref = text(body.get('agentRef'), '来源应用', 220)
    spec = copy.deepcopy(platform.agent(owner, source_ref))
    if body.get('name') is not None:
        spec['name'] = text(body['name'], '应用名称', 2000)
    spec.update(id=str(uuid4()), version='1', origin='generated')
    spec = validate_agent_spec(spec)
    def save_modern(db, forked):
        from .asset_legacy import migrate_pipeline
        doc = migrate_pipeline(platform.workbench.asset_apps, owner, forked)
        return platform.workbench.asset_apps._save_in_transaction(db, owner,
            {'definition': doc}, provenance={'kind': 'legacy_pipeline_fork', 'sourceAgentRef': source_ref})
    return platform.repository.copy_definition(owner, spec, key, 'application_fork', body,
                                              {'kind': 'fork', 'sourceAgentRef': source_ref}, save_modern=save_modern)
