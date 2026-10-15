"""Complete, separate execution evidence; display summaries never drive reuse."""
from .diagnostics import redacted
from .store import canonical, digest, now
from ..workbench.errors import GatewayError

MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
_COMPLETION_RESERVE = 64 * 1024


def clean(value):
    # Validate before any execution. Never silently truncate an executable input.
    try:
        original = canonical(value)
        safe = redacted(value)
        encoded = canonical(safe)
    except (TypeError, ValueError, RecursionError):
        raise GatewayError('执行依据必须是有限JSON值。', 400, 'INVALID_EXECUTION_EVIDENCE') from None
    if len(encoded.encode('utf-8')) > MAX_EVIDENCE_BYTES:
        raise GatewayError('完整执行依据超过2MiB，未执行该动作。', 413, 'EXECUTION_EVIDENCE_LIMIT')
    return safe, original == encoded


def begin(service, owner, run, step, action):
    safe, complete = clean(action)
    record = {'schemaVersion': 1, 'id': step['id'], 'runId': run['id'], 'stepId': step['id'],
              'parentRunId': run.get('parentRunId'), 'nodeId': run.get('nodeId'),
              'skillCallId': run.get('skillCallId'), 'action': safe,
              'inputRefs': list(action.get('inputRefs', [])), 'invocationIds': [], 'skillCallIds': [],
              'status': 'running', 'startedAt': now(), 'reconstructable': complete,
              'redactionReason': None if complete else '凭据或不支持值已移除，不能自动重建原输入。'}
    if len(canonical(record).encode('utf-8')) > MAX_EVIDENCE_BYTES - _COMPLETION_RESERVE:
        raise GatewayError('执行依据没有足够空间保存完成状态，未执行该动作。', 413, 'EXECUTION_EVIDENCE_LIMIT')
    with service.store.transaction() as db:
        service.store.put(db, owner, 'execution_evidence', step['id'], record)
    return record


def update(service, owner, step_id, **changes):
    with service.store.transaction() as db:
        record = service.store.get(db, owner, 'execution_evidence', step_id, True)
        safe, complete = clean(changes)
        record.update(safe)
        record['reconstructable'] = record['reconstructable'] and complete
        if not complete:
            record['redactionReason'] = '凭据或不支持值已移除，不能自动重建原输入。'
        # Source and action together also remain bounded. Failure is explicit.
        clean(record)
        if 'executedCode' in changes and len(canonical(record).encode('utf-8')) > MAX_EVIDENCE_BYTES - _COMPLETION_RESERVE:
            raise GatewayError('执行源码与输入依据超过记录容量，未执行该代码。', 413, 'EXECUTION_EVIDENCE_LIMIT')
        service.store.put(db, owner, 'execution_evidence', step_id, record)
        return record


def invocation(service, owner, step_id, invocation_id):
    with service.store.transaction() as db:
        record = service.store.get(db, owner, 'execution_evidence', step_id, True)
        if invocation_id not in record['invocationIds']:
            record['invocationIds'].append(invocation_id)
            service.store.put(db, owner, 'execution_evidence', step_id, record)


def get(service, owner, run_id, step_id):
    service.store.run(owner, run_id)
    with service.store.transaction() as db:
        record = service.store.get(db, owner, 'execution_evidence', step_id, True)
        if record['runId'] != run_id:
            raise GatewayError('执行依据不属于此任务。', 404)
        return record


def skill_call(service, owner, step_id, call_id):
    with service.store.transaction() as db:
        record = service.store.get(db, owner, 'execution_evidence', step_id, True)
        linked = record.setdefault('skillCallIds', [])
        if call_id not in linked:
            linked.append(call_id)
            service.store.put(db, owner, 'execution_evidence', step_id, record)


def completion_refs(service, owner, run, value):
    """Accept explicit conclusion evidence only from successful current-run work."""
    result = {}
    steps = {step['id']: step for step in run['steps']}
    for field in ('adoptedStepIds', 'resultArtifactIds'):
        refs = value.get(field, [])
        if not isinstance(refs, list) or len(refs) > 64 or any(not isinstance(ref, str) for ref in refs):
            raise GatewayError('结论依据引用格式无效。', 422, 'INVALID_COMPLETION_EVIDENCE')
        result[field] = list(dict.fromkeys(refs))
    for key in result['adoptedStepIds']:
        if key not in steps or steps[key]['status'] != 'succeeded':
            raise GatewayError('结论采用的步骤不属于本任务或尚未成功。', 422, 'INVALID_COMPLETION_EVIDENCE')
    for key in result['resultArtifactIds']:
        artifact, data = service.store.artifact(owner, key)
        step = steps.get(artifact.get('stepId'))
        if (artifact.get('runId') != run['id'] or not artifact.get('complete') or not step
                or artifact.get('artifactRole', 'output') not in {'execution_result', 'output'}
                or step['status'] != 'succeeded' or digest(data) != artifact.get('sha256')):
            raise GatewayError('结论采用的结果不属于本任务的成功步骤或内容不完整。', 422, 'INVALID_COMPLETION_EVIDENCE')
    return result


def adopted(service, db, owner, run, deliveries):
    """Mark only an explicit artifact dependency closure, never guess from text."""
    artifacts = {a['id']: a for a in run['artifacts']}
    steps = {s['id']: s for s in run['steps']}
    source = run.get('outputSource') or {}
    pending = [a['id'] for a in deliveries] + run.get('resultArtifactIds', [])
    if source.get('resultArtifactId'):
        pending.append(source['resultArtifactId'])
    selected = set(run.get('adoptedStepIds', []))
    for key in selected:
        pending.extend(steps[key].get('inputRefs', []))
    if source.get('resultStepId') in steps:
        selected.add(source['resultStepId'])
        pending.extend(steps[source['resultStepId']].get('inputRefs', []))
    known = bool(pending or selected) or 'adoptedStepIds' in run or 'resultArtifactIds' in run
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen or key not in artifacts:
            continue
        seen.add(key)
        artifact = artifacts[key]
        pending.extend(artifact.get('sourceRefs', []))
        step = steps.get(artifact['stepId'])
        if step and step['status'] == 'succeeded':
            selected.add(step['id'])
            pending.extend(step.get('inputRefs', []))
    for step in steps.values():
        record = service.store.get(db, owner, 'execution_evidence', step['id'])
        if record:
            record['adopted'] = step['id'] in selected if known else None
            record['adoptionBasis'] = 'verified-conclusion-and-artifact-closure' if known else 'no-explicit-output-dependency'
            service.store.put(db, owner, 'execution_evidence', step['id'], record)
