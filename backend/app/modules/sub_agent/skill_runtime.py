"""A persistent, scoped Skill call using the existing real agent action loop."""
import base64
import copy
import json
import threading
import time
from pathlib import PurePosixPath

from jsonschema import Draft202012Validator

from . import evidence
from .store import TERMINAL, canonical, digest, now, uid
from ..workbench.errors import GatewayError


def _invalid(message, code='INVALID_SKILL_CALL', status=400):
    raise GatewayError(message, status, code)


def _path(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        _invalid('Skill资源路径无效。')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) != value:
        _invalid('Skill资源路径越界。')
    return value


def _files(value):
    result = {}
    entries = value.items() if isinstance(value, dict) else ((v.get('path'), v) for v in value) if isinstance(value, list) else None
    if entries is None:
        _invalid('Skill资源清单无效。')
    for path, content in entries:
        path = _path(path)
        if path in result:
            _invalid('Skill资源路径重复。')
        if isinstance(content, dict):
            try:
                content = content['text'] if 'text' in content else base64.b64decode(content['base64'], validate=True)
            except (KeyError, TypeError, ValueError):
                _invalid('Skill资源编码无效。')
        if isinstance(content, str):
            content = content.encode('utf-8')
        if not isinstance(content, bytes):
            _invalid('Skill资源必须是文本或字节。')
        result[path] = content
    if len(result) > 512 or sum(map(len, result.values())) > 4 * 1024 * 1024:
        _invalid('Skill快照资源超过512文件或4MiB。', 'SKILL_SNAPSHOT_LIMIT', 413)
    return result


class SnapshotBridge:
    """No ambient catalog: only this immutable Skill and its supplied tools."""
    def __init__(self, service, owner, snapshot, invoke_tool, bindings, context):
        self.service, self.owner, self.original = service, owner, snapshot
        self.callback, self.bindings, self.context = invoke_tool, bindings, context
        self.material = snapshot
        self.component = 'manifest' in snapshot
        if self.component:
            from ..capability_library.components import skill_material
            self.material = skill_material(snapshot)
        self.files = _files(self.material.get('files', {}))
        self.entry = _path(self.material.get('entry', 'SKILL.md'))
        if self.entry not in self.files:
            _invalid('Skill快照缺少入口文件。')
        try:
            self.instructions = self.files[self.entry].decode('utf-8')
        except UnicodeError:
            _invalid('Skill入口必须是UTF-8文本。')
        if len(self.instructions) > 80000:
            _invalid('Skill正文超过80000字符。', 'SKILL_SNAPSHOT_LIMIT', 413)
        self.name = self.material.get('name') or 'App Skill'
        self.loaded, self.tools, self.targets = {}, {}, {}
        dependencies = self.material.get('dependencies', [])
        definitions = self.material.get('tools', [])
        if not isinstance(dependencies, list) or not isinstance(definitions, list):
            _invalid('Skill工具绑定无效。')
        for index, dep in enumerate(dependencies):
            manifest = dep.get('manifest') or {}
            if manifest.get('kind') != 'tool':
                _invalid('Skill依赖必须是已冻结Tool。')
            alias = 'tool_' + str(index + 1)
            definitions = [*definitions, {'name': alias, 'description': manifest.get('description') or manifest.get('name', alias),
                'inputSchema': manifest.get('tool', {}).get('inputSchema', {'type': 'object'})}]
            self.targets[alias] = dep
        self.defs = []
        for item in definitions:
            name = item.get('name')
            if not isinstance(name, str) or not name or name in self.tools or name in {'skill_resource', 'skill_script'}:
                _invalid('Skill工具名称无效或重复。')
            schema = item.get('inputSchema', item.get('parameters', {'type': 'object'}))
            Draft202012Validator.check_schema(schema)
            self.tools[name] = ({'manifest': {'name': item.get('description') or name}, 'schema': schema}, [])
            self.defs.append({'type': 'function', 'function': {'name': name, 'description': item.get('description') or name,
                              'parameters': schema}})
        self.scripts = {item['path'] if isinstance(item, dict) else item for item in self.material.get('scripts', [])}
        for path in self.scripts:
            if _path(path) not in self.files:
                _invalid('Skill声明脚本不存在。')
        self.calls = 0

    def owns(self, name):
        return True  # Unknown names must fail here, never fall through to ambient tools.

    def definitions(self):
        from ..capability_library.chat_bridge import function
        return self.defs + [
            function('skill_resource', '读取此Skill快照中的UTF-8资源。', {'path': {'type': 'string'}}, ('path',)),
            function('skill_script', '实际执行此Skill已声明的隔离Python脚本。',
                     {'path': {'type': 'string'}, 'input': {'type': 'object'}}, ('path', 'input'))]

    def execute(self, name, arguments, writer, stopped, *, task_files=None):
        self.calls += 1
        if self.calls > 32:
            _invalid('Skill能力操作达到32次上限。', 'SKILL_CALL_LIMIT', 429)
        self.context['stepId'] = self.service.local.skill_control.get('stepId')
        if name == 'skill_resource':
            path = _path(arguments.get('path'))
            data = self.files.get(path)
            if data is None or len(data) > 80000:
                _invalid('Skill资源不存在或超过读取上限。')
            try:
                return {'content': {'path': path, 'text': data.decode('utf-8')}}
            except UnicodeError:
                _invalid('只能将UTF-8资源作为文本读取。')
        if name == 'skill_script':
            path = _path(arguments.get('path'))
            if path not in self.scripts:
                _invalid('脚本未在Skill适配中声明。', 'SKILL_SCOPE_VIOLATION', 403)
            evidence.update(self.service, self.owner, self.context['stepId'],
                executedCode=self.files[path].decode('utf-8'), codeSha256=digest(self.files[path]), sourcePath=path,
                packageFileDigests={name: digest(raw) for name, raw in self.files.items()})
            if self.component:
                from ..capability_library.components import invoke_skill_script
                result = invoke_skill_script(self.service.w.capabilities, self.owner, self.original, path,
                    arguments.get('input', {}), files=task_files, stopped=stopped, context=self.context, bindings=self.bindings)
            else:
                result = self.service.executor.execute(self.files[path].decode('utf-8'), arguments.get('input', {}),
                    task_files or {}, stopped, package_files=self.files, package_entry=path)
        elif name in self.tools:
            errors = list(Draft202012Validator(self.tools[name][0]['schema']).iter_errors(arguments))
            if errors:
                _invalid('Skill工具输入不符合已冻结的接口。')
            if self.callback:
                result = self.callback(name, arguments, task_files or {}, stopped)
            elif name in self.targets:
                from ..capability_library.components import invoke_snapshot
                result = invoke_snapshot(self.service.w.capabilities, self.owner, self.targets[name], arguments,
                    files=task_files, stopped=stopped, context=self.context, bindings=self.bindings)
            else:
                _invalid('Skill工具没有受控执行适配。', 'SKILL_TOOL_UNAVAILABLE', 409)
        else:
            _invalid('该操作不属于此Skill的能力范围。', 'SKILL_SCOPE_VIOLATION', 403)
        if not isinstance(result, dict):
            _invalid('Skill工具返回格式无效。')
        if result.get('invocationId'):
            writer({'type': 'tool_completed' if result.get('status') in {None, 'succeeded'} else 'tool_failed',
                    'name': name, 'invocationId': result['invocationId']})
        if result.get('status') not in {None, 'succeeded'}:
            _invalid('Skill工具未成功完成。', 'SKILL_TOOL_FAILED', 422)
        content = result.get('result', result.get('content', result))
        if isinstance(content, dict) and 'content' in content and ('invocationId' in result or 'sources' in content or 'artifacts' in content):
            content = content['content']
        return {'content': content, '_files': result.get('files', {})}


def _finish_action(run, action):
    """Explicit internal result selection is JSON evidence, never an output-file grant."""
    action = copy.deepcopy(action)
    artifacts = {a['id']: a for a in run['artifacts']}
    succeeded = {s['id'] for s in run['steps'] if s['status'] == 'succeeded'}
    selected = action.get('artifactIds', [])
    results = [key for key in selected if artifacts.get(key, {}).get('artifactRole') == 'execution_result'
               and artifacts[key]['stepId'] in succeeded]
    if not action.get('resultStepId') and not action.get('resultArtifactId') and len(results) == 1:
        action['resultArtifactId'] = results[0]
    if run.get('skillPermissions', {}).get('outputFiles') is False:
        action['artifactIds'] = [key for key in selected if artifacts.get(key, {}).get('artifactRole')
                                 not in {'execution_result', 'executed_source'}]
    return action


def _actual_result(service, owner, run, action):
    if action.get('resultStepId'):
        step = next((s for s in run['steps'] if s['id'] == action['resultStepId'] and s['status'] == 'succeeded'), None)
        if not step:
            _invalid('结构化交付必须引用本Skill成功步骤。', 'SKILL_DELIVERY_INVALID', 422)
        if step['type'] == 'code':
            return step['result']['content']
        record = service.execution_evidence(owner, run['id'], step['id'])
        key = record.get('resultArtifactId')
    else:
        key = action.get('resultArtifactId')
    if key:
        if key not in {a['id'] for a in run['artifacts'] if a['stepId'] != 'skill_input'}:
            _invalid('结构化交付工件不属于本Skill实际输出。', 'SKILL_DELIVERY_INVALID', 422)
        meta, data = service._input(owner, run, key)
        if digest(data) != meta['sha256']:
            _invalid('交付工件摘要不一致。', 'SKILL_DELIVERY_INVALID', 422)
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            _invalid('结构化交付必须是实际JSON工件。', 'SKILL_DELIVERY_INVALID', 422)
        return value.get('content', value) if isinstance(value, dict) and meta['name'] == 'tool-result.json' else value
    return {'answer': action.get('answer', '')}


def _delivery(service, owner, run, action, requirements):
    value = _actual_result(service, owner, run, action)
    if action.get('artifactIds') and run.get('skillPermissions', {}).get('outputFiles') is False:
        _invalid('此Skill没有文件交付权限。结构化结果请用resultStepId或resultArtifactId，并令artifactIds为空。', 'SKILL_FILE_NOT_GRANTED', 403)
    schema = requirements.get('schema', requirements.get('outputSchema'))
    if schema is not None:
        problems = list(Draft202012Validator(schema).iter_errors(value))
        if problems:
            _invalid('实际交付不符合所需字段或类型：' + ', '.join('.'.join(map(str, p.absolute_path)) or '$' for p in problems[:8]),
                     'SKILL_DELIVERY_INVALID', 422)
    for path in requirements.get('evidenceFields', []):
        item = value
        try:
            for part in path.removeprefix('$.').split('.'):
                item = item[int(part)] if isinstance(item, list) else item[part]
            if item is None or item == '':
                raise KeyError(path)
        except (KeyError, IndexError, TypeError, ValueError):
            _invalid('交付缺少实际依据字段：' + path, 'SKILL_DELIVERY_INVALID', 422)
    assets = []
    for key in action.get('artifactIds', []):
        if key not in {a['id'] for a in run['artifacts'] if a['stepId'] != 'skill_input'}:
            _invalid('交付文件必须是本Skill的实际输出。', 'SKILL_DELIVERY_INVALID', 422)
        meta, data = service._input(owner, run, key)
        if digest(data) != meta['sha256']:
            _invalid('交付文件摘要不一致。', 'SKILL_DELIVERY_INVALID', 422)
        assets.append(meta)
    if len({a['name'] for a in assets}) != len(assets):
        _invalid('交付文件名称重复，请明确选择一个成功版本。', 'SKILL_DELIVERY_INVALID', 422)
    for requirement in requirements.get('files', requirements.get('requiredFiles', [])):
        requirement = {'name': requirement} if isinstance(requirement, str) else requirement
        matching = [a for a in assets if a['name'] == requirement['name']]
        if not matching:
            _invalid('缺少所需交付文件：' + requirement['name'], 'SKILL_DELIVERY_INVALID', 422)
        if requirement.get('mediaType') and all(a['mediaType'] != requirement['mediaType'] for a in matching):
            _invalid('交付文件媒体类型不符：' + requirement['name'] + '，要求' + requirement['mediaType']
                     + '，实际' + ', '.join(a['mediaType'] for a in matching), 'SKILL_DELIVERY_INVALID', 422)
    if requirements.get('requireEvidence') and not any(s['status'] == 'succeeded' and s['type'] in {'tool', 'code'} for s in run['steps']):
        _invalid('交付缺少实际执行依据。', 'SKILL_DELIVERY_INVALID', 422)
    return value


def _result(service, owner, run, db=None):
    files = {}
    for meta in run.get('deliveries', []):
        if meta['name'] in files:
            _invalid('交付文件名称重复，不能覆盖。', 'SKILL_DELIVERY_INVALID', 422)
        if db is None:
            record, data = service.store.artifact(owner, meta['id'])
        else:
            row = db.execute('SELECT body,data FROM artifacts WHERE owner=? AND id=?', (owner, meta['id'])).fetchone()
            if row is None:
                _invalid('交付文件不存在。', 'SKILL_DELIVERY_INVALID', 404)
            record, data = json.loads(row['body']), bytes(row['data'])
        if digest(data) != record['sha256']:
            _invalid('交付文件已变化。', 'SKILL_DELIVERY_INVALID', 422)
        files[meta['name']] = data
    question = {**run['question'], 'revision': run['revision']} if run.get('question') else None
    return {'skillCallId': run['id'], 'revision': run['revision'], 'status': run['status'], 'result': run.get('output'), 'files': files,
            'artifacts': run.get('deliveries', []), 'steps': run['steps'], 'question': question,
            'error': run.get('error') or (run.get('answer') if run['status'] != 'succeeded' else None)}


def run_skill(service, owner, snapshot, task, inputs, delivery_requirements, *, parent_run_id, node_id,
              stopped=None, files=None, invoke_tool=None, request_id=None, answer=None, bindings=None):
    from .service import Cancelled, Waiting
    if not all(isinstance(v, str) and v.strip() for v in (owner, parent_run_id, node_id, task)):
        _invalid('Skill调用缺少账号、父运行、节点或任务。')
    if not isinstance(snapshot, dict) or not isinstance(inputs, dict) or not isinstance(delivery_requirements, dict):
        _invalid('Skill快照、输入与交付要求必须是对象。')
    service.w.auth.get_user(owner)
    safe, complete = evidence.clean(inputs)
    if not complete:
        _invalid('凭据请通过连接配置提供，不能作为Skill任务输入。')
    schema = delivery_requirements.get('schema', delivery_requirements.get('outputSchema'))
    if schema is not None:
        Draft202012Validator.check_schema(schema)
    files = _files(files or {})
    if any('/' in name for name in files):
        _invalid('授权输入文件必须使用平面安全名称。')
    stopped = stopped or threading.Event()
    context = {'parentRunId': parent_run_id, 'nodeId': node_id}
    bridge = SnapshotBridge(service, owner, snapshot, invoke_tool, bindings, context)
    if files and bridge.material.get('permissions', {}).get('inputFiles') is False:
        _invalid('此Skill没有输入文件权限。', 'SKILL_FILE_NOT_GRANTED', 403)
    _, complete = evidence.clean({'instructions': bridge.instructions, 'task': task})
    if not complete:
        _invalid('Skill任务或正文含凭据，请改用受保护连接配置。')
    identity = {'parentRunId': parent_run_id, 'nodeId': node_id, 'task': task, 'inputs': inputs,
                'delivery': delivery_requirements, 'files': {n: digest(b) for n, b in files.items()},
                'snapshot': {n: digest(b) for n, b in bridge.files.items()},
                'tools': bridge.definitions(), 'dependencies': bridge.material.get('dependencies', []),
                'constraints': bridge.material.get('constraints', []), 'bindings': bindings or {},
                'skillScope': {'entry': bridge.entry, 'scripts': sorted(bridge.scripts),
                              'permissions': bridge.material.get('permissions', {}),
                              'allowTemporaryCode': bridge.material.get('allowTemporaryCode', True)}}
    fingerprint = digest(identity)
    key = request_id or digest([parent_run_id, node_id])
    if not isinstance(key, str) or not key or len(key) > 200:
        _invalid('Skill幂等标识无效。')
    with service.lock, service.store.transaction() as db:
        old = service.store.get(db, owner, 'request', 'skill:' + key)
        if old:
            if old['hash'] != fingerprint:
                _invalid('Skill调用幂等内容冲突。', 'SKILL_REQUEST_CONFLICT', 409)
            run = service.store.get(db, owner, 'run', old['id'], True)
            if run['status'] in TERMINAL:
                return _result(service, owner, run, db=db)
            if run['status'] != 'awaiting_user' or not answer:
                _invalid('Skill调用仍在执行或等待专门回答。', 'SKILL_CALL_BUSY', 409)
            question = run['question']
            if not isinstance(answer, dict) or answer.get('questionId') != question['id'] or answer.get('revision') != run['revision']:
                _invalid('Skill问题已变化，请刷新后回答。', 'REVISION_CONFLICT', 409)
            if not isinstance(answer.get('answer'), str) or not 1 <= len(answer['answer']) <= 10000:
                _invalid('Skill回答无效。')
            run['observations'].append({'question': question['prompt'], 'userAnswer': answer['answer']})
            run.update(status='running', question=None)
            service.store.save_run(db, owner, run, 'question.answered')
        else:
            rid = uid()
            run = {'id': rid, 'skillCallId': rid, 'parentRunId': parent_run_id, 'nodeId': node_id,
                   'conversationId': 'skill-' + rid, 'logicalTaskId': rid, 'revision': 0, 'status': 'running', 'kind': 'skill',
                   'message': task, 'payload': {}, 'correctionsUsed': 0, 'rounds': 0, 'steps': [], 'artifacts': [],
                   'approvals': [], 'reviews': [], 'observations': [], 'loadedRefs': [], 'createdAt': now(),
                   'executionContext': {'kind': 'skill', **context}, 'deliveryRequirements': copy.deepcopy(delivery_requirements),
                   'skillPermissions': copy.deepcopy(bridge.material.get('permissions', {})),
                   'snapshotDigest': fingerprint, 'delegation': {'goal': task,
                       'constraints': bridge.material.get('constraints', []), 'allowTemporaryCode': bridge.material.get('allowTemporaryCode', True),
                       'requiredCapabilities': delivery_requirements.get('requiredTools', []),
                       'requiredExtensions': delivery_requirements.get('requiredExtensions', []),
                       'deliverables': ['满足本步骤交付契约'], 'acceptanceCriteria': ['仅交付真实执行结果']}}
            if bridge.component:
                run['componentSnapshotId'] = rid
                run['componentSnapshotDigest'] = snapshot['snapshotDigest']
                service.store.put(db, owner, 'skill_snapshot', rid, {'runId': rid, 'snapshot': copy.deepcopy(snapshot)})
            service.store.save_run(db, owner, run, 'skill.started')
            service.store.put(db, owner, 'request', 'skill:' + key, {'hash': fingerprint, 'id': rid})
        context.update(skillCallId=run['id'], runId=run['id'])
        bridge.calls = sum(s['type'] == 'tool' for s in run['steps'])
        control = {'stop': stopped, 'response': None}
        service.controls[owner, run['id']] = control
    rid = run['id']
    acquired = False
    started = time.monotonic()
    service.local.skill_control = control
    try:
        while not stopped.is_set() and not service.closed:
            if service.slots.acquire(timeout=.2):
                acquired = True
                break
        if not acquired:
            raise Cancelled()
        deadline = started + service.max_seconds - run.get('activeSeconds', 0)
        if not run.get('inputsLoaded'):
            inputs_meta = service._artifact(owner, rid, 'skill-input.json', canonical(inputs).encode(), 'skill_input', role='input')
            input_files = [service._artifact(owner, rid, name, data, 'skill_input', role='input') for name, data in files.items()]
            observation = {'skill': bridge.name, 'instructions': bridge.instructions, 'resources': list(bridge.files),
                           'inputs': inputs, 'inputArtifact': inputs_meta, 'inputFiles': input_files,
                           'deliveryRequirements': delivery_requirements, 'permissions': run['skillPermissions'],
                           'scope': '仅在本Skill快照及当前节点内执行。完成时用resultStepId引用实际成功步骤或resultArtifactId引用实际JSON工件，不能编造结构化字段。'}
            service.mutate(owner, rid, lambda r: (r['observations'].append(observation), r.update(inputsLoaded=True)))
        def writer(item):
            service._execution_event(owner, rid, control, item)
        while True:
            service._check(control, deadline)
            payload = service._snapshot(owner, rid)
            payload['availableTools'] = bridge.definitions()
            action = service._call('act', owner, rid, payload, control, deadline)
            if action['type'] == 'ask':
                service._ask(owner, rid, action['question'])
            if action['type'] == 'finish':
                current = service.store.run(owner, rid)
                action = _finish_action(current, action)
                try:
                    output = _delivery(service, owner, current, action, delivery_requirements)
                except GatewayError as error:
                    service._correction(owner, rid, str(error), code=error.code)
                    continue
                service.mutate(owner, rid, lambda r: r.update(output=output, outputSource={key: action[key]
                    for key in ('resultStepId', 'resultArtifactId') if key in action}))
                if service._review(owner, rid, action, bridge, control, deadline):
                    break
            else:
                service._step(owner, rid, action, bridge, writer, control, deadline)
    except Waiting:
        pass
    except Cancelled:
        service._end(owner, rid, 'interrupted' if service.closed else 'cancelled', 'Skill调用已停止。')
    except Exception as error:
        service._end(owner, rid, 'cancelled' if stopped.is_set() else 'failed', 'Skill未完成：' + str(error)[:1000])
        if getattr(error, 'code', None) in {'BUDGET_EXHAUSTED', 'AUTHORING_DEADLINE', 'AUTHORING_CANCELLED'}:
            # These are shared authoring control signals, not candidate defects.
            # Keep the actual Skill record and let the outer coordinator pause.
            error.skillCallId = rid
            raise
    finally:
        with service.lock, service.store.transaction() as db:
            latest = service.store.get(db, owner, 'run', rid, True)
            latest['activeSeconds'] = latest.get('activeSeconds', 0) + time.monotonic() - started
            service.store.put(db, owner, 'run', rid, latest)
            service.controls.pop((owner, rid), None)
        service.local.skill_control = None
        if acquired:
            service.slots.release()
    return _result(service, owner, service.store.run(owner, rid))
