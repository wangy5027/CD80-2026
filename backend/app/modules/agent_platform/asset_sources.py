"""Recover only concrete adopted executions, never infer use from loading."""
import copy
import json

from ..capability_library.components import build_snapshot, snapshot_from_package, validate_snapshot
from ..capability_library.contracts import digest, fail


def selection(run, evidence, step_ids=None, builtin_names=()):
    """Keep successful candidates distinct from an explicit adopted method."""
    steps = {step['id']: step for step in run.get('steps', [])}
    artifacts = {item['id']: item for item in run.get('artifacts', [])}
    candidates = [s for s in steps.values() if s.get('status') == 'succeeded' and s.get('type') in {'code', 'tool'}]
    candidate_ids = {s['id'] for s in candidates}
    if step_ids is not None and (not isinstance(step_ids, list) or not step_ids or
            any(not isinstance(key, str) or key not in candidate_ids for key in step_ids) or len(set(step_ids)) != len(step_ids)):
        fail('INVALID_SELECTION', '请选择本任务中真实成功的代码或工具步骤；失败步骤不能作为已执行实现。', 422)

    def closure(step_seeds=(), artifact_seeds=()):
        picked, visited = set(), set()
        def follow_artifact(key):
            if key in visited:
                return
            visited.add(key)
            item = artifacts.get(key, {})
            follow_step(item.get('stepId'))
            for previous in item.get('sourceRefs', []):
                follow_artifact(previous)
        def follow_step(key):
            if key in picked or key not in steps or steps[key].get('status') != 'succeeded':
                return
            picked.add(key)
            for previous in steps[key].get('inputRefs', []):
                follow_artifact(previous)
        for key in step_seeds:
            follow_step(key)
        for key in artifact_seeds:
            follow_artifact(key)
        return picked & candidate_ids

    if step_ids is not None:
        needed = closure(step_ids)
    else:
        adopted = [key for key, record in evidence.items() if record.get('adopted') is True]
        adopted += [key for key in run.get('adoptedStepIds', []) if key in candidate_ids]
        delivered = [item['id'] if isinstance(item, dict) else item for item in run.get('deliveries', [])]
        delivered += run.get('resultArtifactIds', [])
        needed = closure(adopted, delivered)
    rows = []
    for step in candidates:
        record = evidence.get(step['id']) or {}
        reasons = []
        if step['type'] == 'code':
            recoverable = bool((step.get('result') or {}).get('codeArtifactId'))
            if not recoverable:
                reasons.append('没有保存实际执行源码，需补充实现。')
        else:
            recoverable = bool(record.get('status') == 'succeeded' and record.get('reconstructable') and
                                 isinstance(record.get('action', {}).get('arguments'), dict))
            if not recoverable:
                reasons.append('缺少可恢复的完整成功调用参数，展示摘要不能替代原输入。')
            name = record.get('action', {}).get('name') or step.get('name', '')
            if not (name in builtin_names or name.startswith('cap_') or name in {'library_script', 'library_skill'}):
                recoverable = False
                reasons.append('当前不能恢复操作 ' + str(name) + ' 的实现；可通过Creator明确补充。')
        rows.append({'stepId': step['id'], 'type': step['type'], 'title': step.get('purpose') or step.get('label') or step.get('name') or step['id'],
                     'status': 'succeeded', 'reconstructable': recoverable, 'issues': reasons,
                     'inputStepIds': [s['id'] for s in candidates if s['id'] in closure([step['id']]) - {step['id']}],
                     'adopted': record.get('adopted')})
    return needed, rows


def recipe_run(record):
    source = record.get('source') or {}
    if source:
        return source.get('runId') if source.get('kind') == 'sub_agent' else None
    return record.get('sourceRunId')


def records(service, owner, run, evidence):
    result = []
    for key in evidence.get('invocationIds', []):
        record = service.w.capabilities.store.get(owner, 'invocation', key)
        if record is None or record.get('status') != 'succeeded':
            fail('SOURCE_NEEDS_EDIT', '历史调用记录缺失或未成功，不能重建为已采用实现。', 422)
        result.append(record)
    return result


def reference(run, asset_id, version_id=None):
    refs = run.get('loadedRefs') or run.get('payload', {}).get('capabilityRefs', [])
    matches = [r for r in refs if r.get('assetId') == asset_id and (not version_id or r.get('versionId') == version_id)]
    if len(matches) != 1:
        fail('SOURCE_NEEDS_EDIT', '实际来源没有唯一的本任务精确版本锁，不能按最新版本补找。', 422)
    return matches[0]


def skill_call(service, owner, run, step, evidence):
    identities = list(evidence.get('skillCallIds', []))
    artifact_id = evidence.get('resultArtifactId')
    if artifact_id:
        meta, raw = service.w.sub_agents.store.artifact(owner, artifact_id)
        if meta.get('runId') != run['id'] or meta.get('stepId') != step['id'] or meta.get('sha256') != digest(raw):
            fail('SOURCE_NEEDS_EDIT', 'Skill实际返回记录不属于此步骤或摘要已变化。', 422)
        actual = json.loads(raw)
        if actual.get('skillCallId'):
            identities.append(actual['skillCallId'])
    identities = list(dict.fromkeys(identities))
    if len(identities) != 1:
        fail('SOURCE_NEEDS_EDIT', '缺少唯一的实际独立Skill调用，不能把加载当作执行。', 422)
    called = service.w.sub_agents.store.run(owner, identities[0])
    if called.get('kind') != 'skill' or called.get('status') != 'succeeded':
        fail('SOURCE_NEEDS_EDIT', '实际独立Skill尚未成功交付。', 422)
    if called.get('parentRunId') not in {run['id'], run.get('conversationId'), run.get('logicalTaskId')}:
        fail('SOURCE_NEEDS_EDIT', '独立Skill不属于此来源任务。', 422)
    return called


def recover_tool(service, owner, run, step, evidence):
    """Return kind, complete snapshot, actual business arguments and task."""
    action = evidence.get('action', {})
    name = action.get('name') or step.get('name', '')
    arguments = action.get('arguments', {})
    if not isinstance(arguments, dict):
        fail('SOURCE_NEEDS_EDIT', '历史完整参数不是对象。', 422)
    calls = records(service, owner, run, evidence)
    task = None
    if name in service.w.capabilities.builtins:
        # Builtins record their complete action and actual result in SubAgents,
        # not a personal-library invocation. Validate those original bytes first.
        artifact_id = evidence.get('resultArtifactId')
        if evidence.get('status') != 'succeeded' or not evidence.get('reconstructable') or not artifact_id:
            fail('SOURCE_NEEDS_EDIT', '内置工具缺少完整成功执行依据，不能从展示摘要构造实现。', 422)
        meta, raw = service.w.sub_agents.store.artifact(owner, artifact_id)
        if meta.get('runId') != run['id'] or meta.get('stepId') != step['id'] or meta.get('sha256') != digest(raw):
            fail('SOURCE_NEEDS_EDIT', '内置工具结果不属于此步骤或摘要不符。', 422)
        from .asset_legacy import _tool_component
        spec = service.w.capabilities.builtins[name]
        from jsonschema import Draft202012Validator
        try:
            Draft202012Validator(spec.parameters).validate(arguments)
        except Exception:
            fail('SOURCE_NEEDS_EDIT', '历史参数不符合当前内置工具接口，需明确适配后重新验证。', 422)
        wrapped = _tool_component(service, owner, {'sourceRunId': run['id'], 'stepId': step['id'], 'handlerId': name},
            {'id': 'builtin_call'}, {'registeredTool': name, 'inputSchema': copy.deepcopy(spec.parameters), 'outputSchema': {}},
            ['run.input'], {'source_1': {'from': 'input', 'path': ''}}, [], {})
        # Reuse its checked registered-Tool child directly: expose the actual
        # argument fields, without introducing a redundant argument wrapper.
        manifest = copy.deepcopy(wrapped['dependencies'][0]['manifest'])
        manifest.update(name=(step.get('label') or name)[:120],
                        description='由实际成功调用适配到当前平台内置工具；历史宿主实现未冻结，须重新验证。')
        snapshot = snapshot_from_package(manifest, {}, provenance={'runId': run['id'], 'stepId': step['id'],
            'source': '当前受控内置工具适配；历史宿主实现未冻结，重新验证后使用。'})
        return 'tool', snapshot, copy.deepcopy(arguments), task
    if name == 'library_skill':
        called = skill_call(service, owner, run, step, evidence)
        ref = reference(run, arguments.get('assetId'))
        frozen = service.w.sub_agents.store.skill_snapshot(owner, called['id']) if called.get('componentSnapshotId') else called.get('componentSnapshot')
        snapshot = validate_snapshot(frozen) if frozen else build_snapshot(service.w.capabilities, owner, ref['assetId'], ref['versionId'])
        if snapshot['manifest']['kind'] != 'skill':
            fail('SOURCE_NEEDS_EDIT', '实际来源不是Skill。', 422)
        return 'skill', snapshot, arguments.get('input', {}), arguments.get('task')
    if name == 'library_script':
        ref = reference(run, arguments.get('assetId'))
        matched = [r for r in calls if r.get('assetId') == ref['assetId'] and r.get('versionId') == ref['versionId']
                   and r.get('scriptPath') == arguments.get('path') and r.get('input') == arguments.get('input', {})]
        if not matched:
            fail('SOURCE_NEEDS_EDIT', '标准脚本缺少匹配路径/版本/完整参数的实际成功调用。', 422)
        original = build_snapshot(service.w.capabilities, owner, ref['assetId'], ref['versionId'])
        import base64
        path = arguments.get('path')
        if path not in {s['path'] for s in original['manifest']['skill'].get('scripts', [])}:
            fail('SOURCE_NEEDS_EDIT', '实际脚本不在原Skill声明中。', 422)
        manifest = copy.deepcopy(original['manifest'])
        manifest.update(kind='tool', name=(manifest['name'] + '脚本')[:120])
        manifest['source']['identity'] += '/script/' + digest(path)[:16]
        manifest['permissions']['network'] = 'none'
        manifest.pop('skill')
        manifest['tool'] = {'inputSchema': {'type': 'object'}, 'outputSchema': {},
                            'execution': {'type': 'python_wasi', 'path': path, 'callable': 'run'}}
        snapshot = snapshot_from_package(manifest, {p: base64.b64decode(b) for p, b in original['files'].items()},
                                         original['dependencies'], original.get('provenance'))
        return 'tool', snapshot, arguments.get('input', {}), task
    if name.startswith('cap_'):
        version_id = name[4:]
        refs = run.get('loadedRefs') or run.get('payload', {}).get('capabilityRefs', [])
        direct = [r for r in refs if r.get('versionId') == version_id]
        actual = [r for r in calls if r.get('versionId') == version_id and r.get('input') == arguments]
        if actual:
            ref = {'assetId': actual[0]['assetId'], 'versionId': version_id}
            # A Tool loaded through a Skill may be absent from loadedRefs itself;
            # its actual invocation and the source Skill's exact lock provide identity.
            if not direct:
                for source in actual[0].get('sourceSkillRefs', []):
                    reference(run, source['assetId'], source['versionId'])
                if not actual[0].get('sourceSkillRefs'):
                    fail('SOURCE_NEEDS_EDIT', '实际Tool缺少本任务的精确来源绑定。', 422)
        elif len(direct) == 1:
            ref = direct[0]
        else:
            fail('SOURCE_NEEDS_EDIT', '实际Tool版本无法从调用证据恢复。', 422)
        return 'tool', build_snapshot(service.w.capabilities, owner, ref['assetId'], ref['versionId']), arguments, task
    fail('SOURCE_NEEDS_EDIT', '无法恢复实际操作 ' + str(name) + ' 的完整实现；需要明确补齐或改造。', 422)


def used_skills(service, owner, run, step_ids=None):
    used = {}
    for step in run['steps']:
        if step.get('status') != 'succeeded' or not step.get('evidenceId') or (step_ids is not None and step['id'] not in step_ids):
            continue
        evidence = service.w.sub_agents.execution_evidence(owner, run['id'], step['id'])
        if (step_ids is None and evidence.get('adopted') is not True) or evidence.get('status') != 'succeeded' or not evidence.get('reconstructable'):
            continue
        for call in records(service, owner, run, evidence):
            for source in call.get('sourceSkillRefs', []):
                ref = reference(run, source['assetId'], source['versionId'])
                used[(ref['assetId'], ref['versionId'])] = ref
        action = evidence.get('action', {})
        if action.get('name') in {'library_script', 'library_resource', 'library_skill'}:
            ref = reference(run, action.get('arguments', {}).get('assetId'))
            used[(ref['assetId'], ref['versionId'])] = ref
    return list(used.values())
