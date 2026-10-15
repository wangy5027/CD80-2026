"""Background conversation scheduler with bounded delegated ReAct execution."""
import json
import re
import threading
import time
from pathlib import Path

from .model import Model, validate_response
from .media import artifact_media_type
from .diagnostics import ModelTrace, bounded_record, redacted
from .presentation import safe_details, result_summary, action_label, error_summary
from .store import Store, TERMINAL, canonical, digest, now, uid
from . import evidence
from ..capability_library.chat_bridge import ChatCapabilities
from ..capability_library.contracts import CapabilityError
from ..workbench.errors import GatewayError
from ..workbench.tool_registry import ToolContext


class Cancelled(Exception):
    pass


class Waiting(Exception):
    pass


def visualization_requirement(run):
    """Conservative explicit user requests only; planner text grants no waiver."""
    required, source = False, None
    messages = [('user.message', run.get('message', ''))]
    messages.extend(('user.answer', item['userAnswer']) for item in run.get('observations', []) if 'userAnswer' in item)
    for origin, message in messages:
        for clause in re.split(r'[，,。；;\n！？!?]', message):
            if re.search(r'(是什么意思|是什么含义|的区别)\s*$', clause):
                continue
            if (re.match(r'^\s*(?:请问|请|帮我|为我)?(?:告诉我)?(?:什么是|如何|怎么|怎样|为什么|解释|介绍|说明|what is\b|how to\b|explain\b)', clause, re.I)
                    and not re.search(r'并(?:可视化|绘图|画|生成[^，。；\n]{0,16}图)', clause)):
                continue
            # A waiver needs a direct cancellation, not "do not explain plots"
            # or "do not cancel the chart" embedded in a larger answer.
            negative = re.search(r'(?:不要|不需要|无需|不用|不必|不再需要|不再要)\s*(?:再|继续|任何|新的|额外的)?\s*(?:做|进行|生成|制作|提供|输出|绘制|画)?\s*(?:可视化|绘图|画图|图表|走势图|折线图|图)(?!表?(?:的)?代码|片?段)|(?:取消|去掉)\s*(?:可视化|绘图|画图|图表|走势图|折线图)|不画图|\b(?:no|without|do not|don.t)\s+(?:plot|chart|visualization)', clause, re.I)
            if re.search(r'(?:不要|不必|不用|无需)\s*(?:取消|去掉)', clause):
                negative = None
            if negative:
                required, source = False, origin
                continue
            positive = re.search(r'(?:请|帮我|为我|给我|并|再|需要|分析)(?:做|进行)?(?:可视化|绘图)|^\s*(?:可视化|绘图)|(?:画|绘制|生成|制作|输出|提供|展示|交付)[^，。；\n]{0,32}(?:图表|走势图|折线图|曲线图|柱状图|饼图|热力图|图)|^\s*(?:please\s+)?(?:plot|chart|visualize)\b', clause, re.I)
            if positive:
                required, source = True, origin
    return {'visualization': required, 'source': source}


class SubAgents:
    def __init__(self, workbench, config):
        self.w = workbench
        self.store = Store(config['DATA_DIR'])
        self.model = Model(workbench.platform.planner.provider)
        self.executor = workbench.platform.executor
        self.lock = workbench._lock
        self.local = threading.local()
        self.workers, self.controls = {}, {}
        self.closed = False
        self.slots = threading.BoundedSemaphore(4)
        self.max_seconds = max(10, min(int(config.get('SUB_AGENT_TIMEOUT', 600)), 1800))
        self.max_rounds = 32
        # Test-only synchronization hook is never configurable through HTTP.
        self.before_code = None

    def run_skill(self, owner, snapshot, task, inputs, delivery_requirements, **kwargs):
        from .skill_runtime import run_skill
        return run_skill(self, owner, snapshot, task, inputs, delivery_requirements, **kwargs)

    def execution_evidence(self, owner, run_id, step_id):
        return evidence.get(self, owner, run_id, step_id)

    def _execution_event(self, owner, rid, control, item):
        step_id = control.get('stepId')
        if step_id and item.get('invocationId'):
            evidence.invocation(self, owner, step_id, item['invocationId'])
        if step_id and item.get('skillCallId'):
            evidence.skill_call(self, owner, step_id, item['skillCallId'])
        payload = {k: v for k, v in item.items() if k in {'name', 'assetId', 'versionId', 'invocationId', 'skillCallId', 'message'}}
        if step_id:
            payload['stepId'] = step_id
        self.mutate(owner, rid, lambda r: None, item.get('type', 'tool.status'), payload)

    def task(self, owner, cid):
        return self.w.get_task(owner, cid)

    def queue(self, owner, cid):
        self.task(owner, cid)
        with self.store.transaction() as db:
            q = self.store.get(db, owner, 'queue', cid) or {'id': cid, 'revision': 0, 'paused': False, 'activeRunId': None, 'items': []}
            q['items'] = [self.store.get(db, owner, 'run', key, True) for key in q['items']]
            return q

    def guard_legacy(self, owner, cid):
        if getattr(self.local, 'inside', False) or not cid:
            return
        with self.store.transaction() as db:
            q = self.store.get(db, owner, 'queue', cid)
            if q and (q['activeRunId'] or any(self.store.get(db, owner, 'run', key)['status'] == 'queued' for key in q['items'])):
                raise GatewayError('此对话有后台任务，请通过消息队列继续。', 409, 'CONVERSATION_BUSY')

    def _legacy_busy(self, owner, cid):
        if (owner, cid) in self.w.active:
            return True
        return any(r['status'] not in {'succeeded', 'failed', 'cancelled', 'interrupted'}
                   for r in self.w.platform.repository.list_runs(owner, cid))

    @staticmethod
    def revision(doc, value):
        if type(value) is not int or value != doc['revision']:
            raise GatewayError('状态已更新，请刷新后重试。', 409, 'REVISION_CONFLICT')

    def submit(self, owner, cid, body, key, *, execution_context=None):
        message = body.get('message')
        if not isinstance(message, str) or not message.strip() or len(message) > 100000:
            raise GatewayError('请输入不超过100000字符的消息。', 400)
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            raise GatewayError('需要幂等请求标识。', 400)
        files = body.get('fileIds', [])
        refs = body.get('capabilityRefs', [])
        skills = body.get('skillNames', [])
        if not isinstance(files, list) or len(files) > 8 or any(not isinstance(v, str) for v in files):
            raise GatewayError('附件引用无效。', 400)
        if not isinstance(refs, list) or len(refs) > 8 or any(not isinstance(v, dict) or not isinstance(v.get('assetId'), str) or not isinstance(v.get('versionId'), str) for v in refs):
            raise GatewayError('能力引用无效。', 400)
        if not isinstance(skills, list) or len(skills) > 3 or any(not isinstance(v, str) for v in skills):
            raise GatewayError('Skill引用无效。', 400)
        payload = {k: body[k] for k in ('message', 'fileIds', 'capabilityRefs', 'skillNames', 'agentId', 'modelId', 'projectId', 'searchMode', 'replaceCapabilityLocks', 'taskRevision', 'authoringSessionId', 'authoringRevision') if k in body}
        if payload.get('authoringSessionId'):
            if not isinstance(payload['authoringSessionId'], str):
                raise GatewayError('创作会话身份无效。', 400)
            self.w.authoring.detail(owner, payload['authoringSessionId'])
        identity = {'conversationId': cid, 'payload': payload}
        if execution_context is not None:
            if (not isinstance(execution_context, dict) or set(execution_context) != {'kind', 'recipeId', 'name'}
                    or execution_context.get('kind') != 'recipe'
                    or any(not isinstance(execution_context.get(k), str) or not execution_context[k].strip()
                           or len(execution_context[k]) > maximum for k, maximum in (('recipeId', 100), ('name', 128)))):
                raise GatewayError('内部应用执行上下文无效。', 400)
            # Only internal callers can set this keyword. Request body fields
            # cannot change routing context or confer any execution permission.
            execution_context = dict(execution_context)
            identity['executionContext'] = execution_context
        request_hash = digest(identity)
        with self.lock, self.store.transaction() as db:
            previous = self.store.get(db, owner, 'request', 'submit:' + key)
            if previous:
                if previous['hash'] != request_hash:
                    raise GatewayError('同一请求标识不能用于不同内容。', 409)
                return self.store.get(db, owner, 'run', previous['id'], True)
            state = self.w.store.get(owner)
            if any(f not in {v['id'] for v in state['files']} for f in files):
                raise GatewayError('附件不存在或无权访问。', 404)
            if body.get('projectId') and body['projectId'] not in {p['id'] for p in state['projects']}:
                raise GatewayError('项目不存在。', 404)
            if body.get('modelId') and body['modelId'] != self.w.model_id:
                raise GatewayError('所选模型不可用。', 400)
            if cid == 'new':
                cid = uid()
                task = {'id': cid, 'title': message[:32], 'agentId': body.get('agentId') or 'assistant',
                        'modelId': self.w.model_id, 'projectId': body.get('projectId') or '', 'status': 'idle',
                        'messages': [], 'createdAt': now(), 'updatedAt': now()}
                self.w.store.update(owner, lambda s: s['tasks'].insert(0, task))
            else:
                self.task(owner, cid)
            q = self.store.get(db, owner, 'queue', cid) or {'id': cid, 'revision': 0, 'paused': False, 'activeRunId': None, 'items': []}
            if not q['activeRunId'] and self._legacy_busy(owner, cid):
                raise GatewayError('此对话的原有聊天或应用仍在运行，请先完成或停止。', 409)
            if sum(self.store.get(db, owner, 'run', k)['status'] == 'queued' for k in q['items']) >= 20:
                raise GatewayError('待执行消息最多20条。', 429)
            run = {'id': uid(), 'conversationId': cid, 'logicalTaskId': uid(), 'revision': 0,
                   'status': 'queued', 'kind': 'pending', 'payload': payload, 'message': message.strip(),
                   'correctionsUsed': 0, 'rounds': 0, 'steps': [], 'artifacts': [], 'approvals': [],
                   'reviews': [], 'observations': [], 'loadedRefs': [], 'createdAt': now(), 'updatedAt': now()}
            if execution_context is not None:
                run['executionContext'] = execution_context
            q['items'].append(run['id'])
            q['revision'] += 1
            self.store.put(db, owner, 'queue', cid, q)
            self.store.save_run(db, owner, run, 'run.created')
            self.store.put(db, owner, 'request', 'submit:' + key, {'hash': request_hash, 'id': run['id']})
        self.kick(owner, cid)
        return run

    def mutate(self, owner, rid, fn, event='run.phase_changed', payload=None, allow_terminal=False):
        with self.lock, self.store.transaction() as db:
            run = self.store.get(db, owner, 'run', rid, True)
            if not allow_terminal and (run['status'] in TERMINAL or run['status'] == 'cancelling'):
                raise Cancelled()
            fn(run)
            self.store.save_run(db, owner, run, event, payload)
            return run

    def command(self, owner, cid, rid, action, body, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            raise GatewayError('需要幂等请求标识。', 400)
        scope = action + ':' + key
        hashed = digest({'cid': cid, 'rid': rid, 'body': body})
        with self.lock, self.store.transaction() as db:
            old = self.store.get(db, owner, 'request', scope)
            if old:
                if old['hash'] != hashed:
                    raise GatewayError('幂等请求内容冲突。', 409)
                return old['result']
            q = self.store.get(db, owner, 'queue', cid, True)
            run = self.store.get(db, owner, 'run', rid, True) if rid else None
            if run and run['conversationId'] != cid:
                raise GatewayError('任务不属于此对话。', 404)
            self.revision(run if run else q, body.get('revision'))
            if action == 'resume':
                if not q.get('activeRunId') and self._legacy_busy(owner, cid):
                    raise GatewayError('请先完成当前应用流程。', 409)
                q['paused'] = False
                q.pop('pauseReason', None)
            elif action == 'withdraw':
                if run['status'] != 'queued':
                    raise GatewayError('只能撤回待执行消息。', 409)
                run['status'] = 'withdrawn'
            elif action == 'cancel':
                if run['status'] in TERMINAL:
                    raise GatewayError('任务已经结束。', 409)
                q['paused'] = True
                run['status'] = 'cancelling' if (owner, rid) in self.controls else 'cancelled'
                if run['status'] == 'cancelled':
                    q['activeRunId'] = None
            elif action == 'answer':
                question = run.get('question') or {}
                if run['status'] != 'awaiting_user' or question.get('id') != body.get('questionId'):
                    raise GatewayError('问题已关闭或不属于当前任务。', 409)
                answer = body.get('answer')
                if not isinstance(answer, str) or not answer.strip() or len(answer) > 10000:
                    raise GatewayError('回答无效。', 400)
                observation = {'question': question['prompt'], 'userAnswer': answer}
                if not run.get('delegation') and run.get('planningRequiredCapabilities'):
                    # Freeze requirements across autonomous retries only. A new
                    # user answer may change the request; retain that transition
                    # for audit without changing selected grants or the budget.
                    observation['previousPlanningRequiredCapabilities'] = run.pop('planningRequiredCapabilities')
                run['observations'].append(observation)
                run['question'] = None
                run['status'] = 'running' if run.get('delegation') else 'planning'
            else:
                raise GatewayError('操作不存在。', 404)
            q['revision'] += 1
            self.store.put(db, owner, 'queue', cid, q)
            if run:
                self.store.save_run(db, owner, run, 'question.answered' if action == 'answer' else 'queue.changed')
            result = run or q
            self.store.put(db, owner, 'request', scope, {'hash': hashed, 'result': result})
            if action == 'cancel':
                control = self.controls.get((owner, rid))
                if control:
                    control['stop'].set()
                    response = control.get('response')
                    if response:
                        try:
                            response.abort()
                        except Exception:
                            pass
        if action == 'cancel':
            self.w.stop(owner, cid)
        if action in {'answer', 'resume'}:
            self.kick(owner, cid)
        return result

    def kick(self, owner, cid):
        with self.lock:
            key = (owner, cid)
            if self.closed or key in self.workers:
                return
            thread = threading.Thread(target=self._worker, args=key, daemon=True, name='sub-agent-' + cid[:8])
            self.workers[key] = thread
            thread.start()

    def _worker(self, owner, cid):
        try:
            while not self.closed:
                with self.lock, self.store.transaction() as db:
                    q = self.store.get(db, owner, 'queue', cid, True)
                    if q['paused']:
                        return
                    run = self.store.get(db, owner, 'run', q['activeRunId']) if q['activeRunId'] else None
                    if run and run['status'] == 'awaiting_user':
                        return
                    if run is None or run['status'] in TERMINAL:
                        run = next((r for k in q['items'] if (r := self.store.get(db, owner, 'run', k))['status'] == 'queued'), None)
                    if not run:
                        return
                    q['activeRunId'] = run['id']
                    q['revision'] += 1
                    self.store.put(db, owner, 'queue', cid, q)
                    if run['status'] == 'queued':
                        run.update(status='planning', startedAt=now())
                        self.store.save_run(db, owner, run)
                    control = {'stop': threading.Event(), 'response': None}
                    self.controls[owner, run['id']] = control
                acquired = False
                started = time.monotonic()
                try:
                    while not control['stop'].is_set() and not self.closed:
                        if self.slots.acquire(timeout=.2):
                            acquired = True
                            break
                    if not acquired:
                        raise Cancelled()
                    self._execute(owner, run['id'], control)
                except Waiting:
                    return
                except Cancelled:
                    self._end(owner, run['id'], 'interrupted' if self.closed else 'cancelled', '任务已中断；队列已暂停。')
                except Exception as error:
                    status = 'cancelled' if control['stop'].is_set() else 'failed'
                    timeout = control.get('timeoutFailure', False) or getattr(error, 'status', None) == 504
                    detail = ('任务已达到整体执行时间上限，已停止后续执行。' if getattr(error, 'code', None) == 'sub_agent_deadline'
                              else '模型请求持续超时，已停止后续执行。') if timeout else str(error)[:1000]
                    if getattr(error, 'code', None) == 'MODEL_FORMAT_RETRIES_EXHAUSTED':
                        previous = self.store.run(owner, run['id'])
                        blockers = [str(item['error'])[:250] for item in previous.get('observations', [])
                            if item.get('error') and item.get('errorCode') not in {'MODEL_JSON_INVALID', 'MODEL_SCHEMA_INVALID'}]
                        if blockers:
                            detail += ' 此前已记录的执行障碍：' + '；'.join(dict.fromkeys(blockers[-2:]))
                    self._end(owner, run['id'], status, '任务未完成：' + detail, pause=timeout)
                finally:
                    with self.store.transaction() as db:
                        latest = self.store.get(db, owner, 'run', run['id'], True)
                        latest['activeSeconds'] = latest.get('activeSeconds', 0) + time.monotonic() - started
                        self.store.put(db, owner, 'run', run['id'], latest)
                    if acquired:
                        self.slots.release()
                    with self.lock:
                        self.controls.pop((owner, run['id']), None)
        finally:
            with self.lock:
                self.workers.pop((owner, cid), None)
                # An answer may arrive between Waiting and this cleanup.
                if not self.closed:
                    q = self.queue(owner, cid)
                    current = next((r for r in q['items'] if r['id'] == q['activeRunId']), None)
                    if not q['paused'] and (not current or current['status'] != 'awaiting_user') and any(r['status'] in {'queued', 'planning', 'running'} for r in q['items']):
                        self.kick(owner, cid)

    def _message(self, owner, run, role, text):
        tag = run['id'] + ':' + role
        def save(state):
            task = next(t for t in state['tasks'] if t['id'] == run['conversationId'])
            if not any(m.get('subAgentMessageId') == tag for m in task['messages']):
                task['messages'].append({'role': role, 'content': text, 'at': now(), 'subAgentMessageId': tag,
                    **{k: run[k] for k in ('authoringSessionId', 'assetAppRunId', 'assetAppId', 'assetAppVersionId') if k in run}})
            task.update(updatedAt=now(), status='running' if role == 'user' else 'done')
        self.w.store.update(owner, save)

    def _end(self, owner, rid, status, answer, *, pause=False):
        with self.lock:
            candidate = self.store.run(owner, rid)
            missing = (self._visualization_missing(owner, candidate, candidate.get('deliveries', []))
                       if status == 'succeeded' and not candidate.get('skillCallId')
                       and candidate.get('kind') not in {'application', 'authoring', 'asset_app'} and candidate['status'] not in TERMINAL else [])
            with self.store.transaction() as db:
                run = self.store.get(db, owner, 'run', rid, True)
                if run['status'] in TERMINAL:
                    return
                if run['status'] == 'cancelling':
                    status, answer = 'cancelled', '任务已中断；已有产物仅为未完成结果。'
                if status == 'succeeded' and not run.get('skillCallId') and run.get('kind') not in {'application', 'authoring', 'asset_app'}:
                    completion = run.get('completion') or {'status': 'complete', 'missingDeliverables': [], 'reasons': []}
                    if missing:
                        completion = {'status': 'partial', 'missingDeliverables': list(dict.fromkeys(completion['missingDeliverables'] + missing)),
                                      'reasons': list(dict.fromkeys(completion['reasons'] + ['用户要求的图表尚未交付。']))}
                        status = 'partial'
                    run['completion'] = completion
                if status == 'partial' and run.get('completion', {}).get('reasons'):
                    note = '未完成：' + '；'.join(run['completion']['reasons'])
                    if note not in answer:
                        answer += '\n\n' + note
                run.update(status=status, answer=answer, completedAt=now())
                run.pop('retryNotice', None)
                for item in [*run['steps'], *run.get('modelCalls', [])]:
                    if item['status'] in {'running', 'awaiting_approval'}:
                        item.update(status='cancelled' if status in {'cancelled', 'interrupted'} else 'failed',
                                    completedAt=now(), error='任务已结束，此步骤未完成。')
                for step in run['steps']:
                    record = self.store.get(db, owner, 'execution_evidence', step['id'])
                    if record and record['status'] == 'running':
                        record.update(status=step['status'], completedAt=now())
                        self.store.put(db, owner, 'execution_evidence', step['id'], record)
                # Persist reply before releasing the conversation to the next item.
                if not run.get('legacyPersisted') and not run.get('skillCallId'):
                    self._message(owner, run, 'user', run['message'])
                    self._message(owner, run, 'assistant', answer)
                self.store.save_run(db, owner, run, 'run.completed')
                if run.get('skillCallId'):
                    return
                q = self.store.get(db, owner, 'queue', run['conversationId'], True)
                q['activeRunId'] = None
                q['paused'] = q['paused'] or pause or status in {'cancelled', 'interrupted'}
                if pause:
                    q['pauseReason'] = '任务超时后已终止；排队消息已暂停，请选择是否继续。'
                q['revision'] += 1
                self.store.put(db, owner, 'queue', q['id'], q)

    def _check(self, control, deadline):
        if control['stop'].is_set() or self.closed:
            raise Cancelled()
        if time.monotonic() > deadline:
            raise GatewayError('任务已达到整体执行时间上限，正在结束。', 504, 'sub_agent_deadline')

    def _save_model_trace(self, owner, rid, attempt, trace, error=None):
        # Keep large request/response bodies out of run polling and future model input.
        with self.lock, self.store.transaction() as db:
            run = self.store.get(db, owner, 'run', rid, True)
            record = {'schemaVersion': 1, 'runId': rid, 'callId': attempt['id'],
                'stage': attempt['stage'], 'model': self.model.planner.provider.model if isinstance(self.model, Model) else 'test-double',
                'startedAt': attempt['startedAt'], 'completedAt': attempt.get('completedAt'),
                'status': attempt['status'], 'retryOf': attempt.get('retryOf'),
                'budget': {'correctionsUsed': attempt['correctionIndex'], 'modelCallsUsed': run['rounds'],
                           'maxCorrections': 3, 'maxModelCalls': self.max_rounds},
                'detail': bounded_record(trace.record(), max_chars=1000000)}
            if error is not None:
                record['error'] = redacted({'code': getattr(error, 'code', None), 'message': str(error)[:1000],
                    'details': getattr(error, 'diagnostics', {})})
            self.store.put(db, owner, 'model_trace', attempt['id'], record)
            for item in run.get('modelCalls', []):
                if item['id'] == attempt['id']:
                    item.update(attempt, traceAvailable=True)
                    break
            self.store.save_run(db, owner, run, 'model.trace_updated', {'callId': attempt['id']})

    def _call(self, stage, owner, rid, payload, control, deadline):
        labels = {'plan': '主助手判断任务', 'act': '子任务安排下一步', 'approve': '主助手审批代码', 'review': '主助手检查交付'}
        retry_of, feedback = None, None
        while True:
            self._check(control, deadline)
            run = self.store.run(owner, rid)
            if run['rounds'] >= self.max_rounds:
                raise GatewayError('任务达到32次模型请求上限。', 429)
            attempt = {'id': uid(), 'stage': stage, 'label': labels[stage], 'status': 'running',
                       'startedAt': now(), 'correctionIndex': run['correctionsUsed']}
            attempt.update({k: run[k] for k in ('parentRunId', 'nodeId', 'skillCallId') if k in run})
            if control.get('stepId'):
                attempt['stepId'] = control['stepId']
            attempt['traceUrl'] = '/desktop/api/sub-agent-runs/' + rid + '/model-calls/' + attempt['id']
            if retry_of:
                attempt['retryOf'] = retry_of
            trace = ModelTrace()
            request_payload = dict(payload)
            if 'correctionsUsed' in request_payload:
                request_payload['correctionsUsed'] = run['correctionsUsed']
            if feedback:
                request_payload['validationFeedback'] = feedback
            def begin(r):
                r['rounds'] += 1
                r.setdefault('modelCalls', []).append(attempt)
                r.pop('retryNotice', None)
            self.mutate(owner, rid, begin, 'model.started', {'stage': stage})
            self._save_model_trace(owner, rid, attempt, trace)
            def capture(kind, data):
                trace.receive(kind, data)
                if kind == 'request':
                    self._save_model_trace(owner, rid, attempt, trace)
            def response(value):
                control['response'] = value
                if value and control['stop'].is_set():
                    value.abort()
            try:
                options = {'deadline': max(.01, min(90, deadline - time.monotonic())), 'on_trace': capture} if isinstance(self.model, Model) else {}
                if not isinstance(self.model, Model):
                    capture('request', {'model': 'test-double', 'stage': stage, 'payload': request_payload})
                value = self.model.call(stage, request_payload, control['stop'], response, **options)
                self._check(control, deadline)
                validate_response(stage, value)
                trace.receive('parsed', value)
            except Exception as error:
                timed_out = getattr(error, 'code', None) in {'agent_model_deadline', 'sub_agent_deadline'} or getattr(error, 'status', None) == 504
                malformed = getattr(error, 'code', None) in {'MODEL_JSON_INVALID', 'MODEL_SCHEMA_INVALID'}
                control['timeoutFailure'] = timed_out
                attempt.update(status='cancelled' if control['stop'].is_set() or isinstance(error, Cancelled) else 'failed',
                    completedAt=now(), error='本次模型请求超时。' if timed_out else str(error)[:500],
                    errorCode=getattr(error, 'code', None))
                if malformed:
                    attempt['validationErrors'] = redacted(getattr(error, 'diagnostics', {}))
                self._save_model_trace(owner, rid, attempt, trace, error)
                if not timed_out and not malformed:
                    raise
                self._check(control, deadline)
                # Only this model request is retried. No successful Tool is replayed.
                if malformed and self.store.run(owner, rid)['correctionsUsed'] >= 3:
                    raise GatewayError('模型响应格式校验未通过，已用完本任务的3次纠错机会；已有执行记录已保留。',
                        502, 'MODEL_FORMAT_RETRIES_EXHAUSTED') from None
                reason = '本次模型请求超时，重新请求当前阶段。' if timed_out else '模型响应格式校验未通过，重新请求当前阶段。'
                self._correction(owner, rid, reason, code=getattr(error, 'code', None))
                retry_of = attempt['id']
                feedback = None if timed_out else {'code': error.code,
                    'message': str(error)[:500], 'details': getattr(error, 'diagnostics', {}),
                    'instruction': '仅重新生成当前阶段的合法JSON对象；依据原有执行结果，不能重放已成功工具或改变授权。'}
                latest = self.store.run(owner, rid)
                self.mutate(owner, rid, lambda r: r.update(status=run['status'], retryNotice=reason + '（纠错 %s / 3）' % latest['correctionsUsed']))
                continue
            finally:
                control['response'] = None
            attempt.update(status='succeeded', completedAt=now(), summary=str(value.get('reason') or (value.get('assessment') or {}).get('reason') or {'approve': '已批准', 'reject': '未批准', 'accept': '验收通过', 'revise': '验收未通过，需要补齐交付', 'fail': '验收未通过'}.get(value.get('decision'), '已完成本阶段判断'))[:600])
            if stage == 'review':
                attempt.update(modelDecision=value['decision'], summary='模型已返回验收建议，等待平台核对。')
            self._save_model_trace(owner, rid, attempt, trace)
            self.mutate(owner, rid, lambda r: r.pop('retryNotice', None), 'model.completed', {'stage': stage})
            control['timeoutFailure'] = False
            return value

    def _ask(self, owner, rid, question):
        if not isinstance(question, str) or not question.strip():
            raise GatewayError('模型没有提供有效问题。', 502)
        self.mutate(owner, rid, lambda r: r.update(status='awaiting_user', mainMessageSaved=True, question={'id': uid(), 'prompt': question[:4000]}), 'question.opened')
        raise Waiting()

    def _correction(self, owner, rid, reason, *, code=None):
        def fix(run):
            if run['correctionsUsed'] >= 3:
                raise GatewayError('已用完3次纠错机会。' + reason[:300], 422, 'SUB_AGENT_CORRECTIONS_EXHAUSTED')
            run['correctionsUsed'] += 1
            run['status'] = 'running'
            run['observations'].append({'error': reason[:4000], 'correctionIndex': run['correctionsUsed'],
                                        **({'errorCode': code} if code else {})})
        self.mutate(owner, rid, fix, 'correction.started')

    def _artifact(self, owner, rid, name, data, step, sources=(), *, role='output', input_source=None, preview=None, preview_truncated=False):
        data_input = input_source == 'data_management' and role == 'input' and step == 'upload' and len(sources) == 1
        if input_source is not None and not data_input:
            raise GatewayError('输入文件来源无效。', 400)
        if preview is not None and not data_input:
            raise GatewayError('文件预览来源无效。', 400)
        if data_input and not isinstance(preview, str):
            raise GatewayError('输入文件缺少可读预览。', 400)
        if preview_truncated and not data_input:
            raise GatewayError('文件预览来源无效。', 400)
        limit = (10 if data_input else 4) * 1024 * 1024
        if not isinstance(data, bytes) or len(data) > limit:
            raise GatewayError('输入文件超过10MiB上限。' if data_input else '产物超过4MiB上限。', 413)
        name = Path(name).name
        with self.lock, self.store.transaction() as db:
            run = self.store.get(db, owner, 'run', rid, True)
            if run['status'] in TERMINAL or run['status'] == 'cancelling':
                raise Cancelled()
            used = db.execute('SELECT COALESCE(SUM(length(data)),0),COUNT(*) FROM artifacts WHERE owner=? AND run=?', (owner, rid)).fetchone()
            if used[0] + len(data) > 32 * 1024 * 1024 or used[1] >= 64:
                raise GatewayError('任务产物达到32MiB或64个上限。', 413)
            a = {'id': uid(), 'runId': rid, 'conversationId': run['conversationId'], 'stepId': step, 'name': name,
                 'size': len(data), 'sha256': digest(data), 'mediaType': artifact_media_type(name),
                 'sourceRefs': list(sources), 'artifactRole': role, 'complete': True, 'createdAt': now()}
            a['downloadUrl'] = '/desktop/api/sub-agent-artifacts/' + a['id']
            if data_input:
                a['inputSource'] = 'data_management'
                a['preview'] = preview[:4000]
                a['previewTruncated'] = bool(preview_truncated or len(preview) > 4000)
            elif name.endswith(('.csv', '.json', '.txt', '.py', '.md')):
                a['preview'] = data[:4000].decode('utf-8', errors='replace')
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (owner, a['id'], rid, canonical(a), data))
            run['artifacts'].append(a)
            self.store.save_run(db, owner, run, 'artifact.created', a)
            return a

    def _input(self, owner, run, key):
        a, data = self.store.artifact(owner, key)
        if a['conversationId'] != run['conversationId'] or not a['complete']:
            raise GatewayError('文件未授权给此对话。', 403)
        if a.get('inputSource') == 'data_management' and digest(data) != a['sha256']:
            raise GatewayError('输入文件内容已变化，请重新上传。', 409)
        return a, data

    def _snapshot(self, owner, rid):
        run = self.store.run(owner, rid)
        return {**{k: run.get(k) for k in ('message', 'delegation', 'observations', 'steps', 'artifacts', 'correctionsUsed', 'approvals', 'executionContext')},
                'deliveryObligations': {'visualization': False, 'source': 'skill-node-contract'} if run.get('skillCallId') else visualization_requirement(run)}

    def _visualization_missing(self, owner, run, assets):
        if run.get('skillCallId') or not visualization_requirement(run)['visualization']:
            return []
        from ..agent_platform.asset_contracts import delivery_check
        successful = {step['id'] for step in run['steps'] if step['status'] == 'succeeded'}
        for asset in assets:
            if asset.get('artifactRole', 'output') != 'output' or asset.get('stepId') not in successful or asset.get('runId') != run['id']:
                continue
            media = artifact_media_type(asset['name'])
            if media not in {'image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml', 'application/pdf', 'text/html'}:
                continue
            try:
                stored, data = self.store.artifact(owner, asset['id'])
                if stored['sha256'] != asset['sha256'] or digest(data) != asset['sha256']:
                    continue
                if media == 'text/html' and not re.search(rb'<(?:svg|canvas)\b', data, re.I):
                    continue
                delivery_check({}, {asset['name']: data}, {'files': [{'name': asset['name'], 'mediaType': media}]})
                return []
            except (GatewayError, CapabilityError):
                continue
        return ['图表']

    def _load(self, owner, rid, refs, bridge, writer):
        if not isinstance(refs, list) or any(not isinstance(x, dict) for x in refs):
            raise GatewayError('优先能力格式无效。', 502)
        for ref in refs:
            if ref.get('assetId') in bridge.loaded:
                continue
            started = now()
            result = bridge.load(ref.get('assetId'), ref.get('versionId'), writer)
            version = bridge.loaded[ref['assetId']]
            entry = {'id': uid(), 'status': 'succeeded', 'startedAt': started, 'completedAt': now(),
                     'label': '加载 ' + ('Skill' if version['manifest']['kind'] == 'skill' else 'Tool') + ' · ' + version['manifest']['name'],
                     'summary': '已读取使用方法与约束。' if version['manifest']['kind'] == 'skill' else '已取得调用方式，尚未执行工具。'}
            self.mutate(owner, rid, lambda r: (r['observations'].append(result), r.setdefault('capabilityLoads', []).append(entry)))
        self._remember_loaded(owner, rid, bridge)

    def _remember_loaded(self, owner, rid, bridge):
        refs = [{'assetId': v['assetId'], 'versionId': v['versionId']} for v in bridge.loaded.values()]
        self.mutate(owner, rid, lambda r: r.update(loadedRefs=refs))

    def _load_preferred(self, owner, rid, plan, bridge, writer, control, deadline):
        """Untrusted planner references may be corrected; user grants never change."""
        run = self.store.run(owner, rid)
        required = set(plan.get('requiredCapabilities', []))
        retained = set(run.get('planningRequiredCapabilities', []))
        if not retained <= required:
            self._correction(owner, rid, '规划纠错不能移除先前必须使用的能力，请保留requiredCapabilities；不可用时明确报告或询问用户。',
                             code='REQUIRED_CAPABILITY_CHANGED')
            return False
        refs = plan.get('preferredCapabilities', [])
        rejected = None
        try:
            for ref in refs:
                rejected = ref
                self._load(owner, rid, [ref], bridge, writer)
            return True
        except CapabilityError as error:
            if error.code not in {'NOT_FOUND', 'CAPABILITY_BLOCKED', 'CAPABILITY_DISABLED', 'VERIFICATION_REQUIRED',
                                  'STALE_CONFIGURATION', 'VERSION_LOCK_CONFLICT', 'INVALID_REQUEST', 'LIMIT_EXCEEDED'}:
                raise
            self._check(control, deadline)
            safe, _ = evidence.clean({'references': refs, 'rejectedReference': rejected, 'requiredCapabilities': sorted(required)})
            feedback = {'source': 'model.preferredCapabilities',
                        'modelCallId': next((item['id'] for item in reversed(run.get('modelCalls', []))
                                             if item['stage'] == 'plan' and item['status'] == 'succeeded'), None),
                        **safe, 'errorCode': error.code,
                        'error': '规划推荐的个人能力无法加载：' + str(error),
                        'instruction': '个人引用必须来自当前个人目录的准确ID/版本；内置工具直接使用availableTools名称，不构造builtin:或latest。不替换用户强制能力。'}
            self.mutate(owner, rid, lambda r: (r['observations'].append(feedback),
                r.update(planningRequiredCapabilities=sorted(retained | required), status='planning')),
                'planning.capability_rejected', feedback)
            builtin = {item['name']: item for item in self.w.runtime.tools.catalog()}
            key = rejected.get('assetId', '')
            forced = key in required or 'cap_' + str(rejected.get('versionId')) in required
            name = key.removeprefix('builtin:')
            item = builtin.get(name, {})
            if key.startswith('builtin:') and name in required and (not item.get('available') or
                    (run['payload'].get('searchMode') == 'off' and 'search' in item.get('capabilities', []))):
                forced = True
            if forced:
                raise GatewayError('必须使用的能力不存在、不可用或无权访问，已停止；没有执行替代能力。',
                                   409, 'REQUIRED_CAPABILITY_UNAVAILABLE') from None
            self._correction(owner, rid, feedback['error'] + ' ' + feedback['instruction'], code='PLAN_CAPABILITY_INVALID')
            return False

    def _delegate(self, owner, rid, plan):
        if not isinstance(plan.get('goal'), str):
            raise GatewayError('委派契约无效。', 502)
        if type(plan.get('allowTemporaryCode', False)) is not bool:
            raise GatewayError('临时代码授权字段必须是布尔值。', 502)
        for field in ('deliverables', 'acceptanceCriteria', 'constraints', 'requiredCapabilities', 'requiredExtensions'):
            if not isinstance(plan.get(field, []), list) or any(not isinstance(x, str) for x in plan.get(field, [])):
                raise GatewayError('委派契约字段无效。', 502)
        self.mutate(owner, rid, lambda r: r.update(kind='delegate', delegation=plan, status='running'), 'run.delegated')

    def _execute(self, owner, rid, control):
        run = self.store.run(owner, rid)
        deadline = time.monotonic() + self.max_seconds - run.get('activeSeconds', 0)
        cid = run['conversationId']
        if run['payload'].get('authoringSessionId'):
            from ..agent_platform.authoring_chat import handoff
            handoff(self, owner, run)
            return
        bridge = ChatCapabilities(self.w.capabilities, owner, cid, [], run['payload'].get('fileIds', []))
        blocked = {'search'} if run['payload'].get('searchMode') == 'off' else set()
        def writer(item):
            self._execution_event(owner, rid, control, item)
        self._load(owner, rid, [*run['payload'].get('capabilityRefs', []), *run['loadedRefs']], bridge, writer)
        if not run.get('inputsLoaded'):
            for file_id in run['payload'].get('fileIds', []):
                stream, meta, lock = self.w.open_file(owner, file_id)
                data_input = meta.get('source') == 'data_management'
                limit = (10 if data_input else 4) * 1024 * 1024
                try:
                    data = stream.read(limit + 1)
                finally:
                    stream.close()
                    lock.release()
                if len(data) > limit:
                    raise GatewayError('输入文件超过10MiB上限。' if data_input else '产物超过4MiB上限。', 413)
                if data_input:
                    from ..workbench.data_files import data_context
                    preview = data_context(meta, data, max_chars=4000)
                    input_name = meta['name']
                    suffix = Path(meta.get('originalName') or input_name).suffix.lower()
                    if suffix and Path(input_name).suffix.lower() != suffix:
                        input_name = input_name[:180 - len(suffix)] + suffix
                    self._artifact(owner, rid, input_name, data, 'upload', [file_id],
                                   role='input', input_source='data_management', preview=preview,
                                   preview_truncated=bool(meta.get('dataInfo', {}).get('truncated') or len(preview) >= 4000))
                else:
                    self._artifact(owner, rid, meta['name'], data, 'upload', [file_id])
            for name in run['payload'].get('skillNames', []):
                self.mutate(owner, rid, lambda r: r['observations'].append(self.w.load_skill(name)))
            self.mutate(owner, rid, lambda r: r.update(inputsLoaded=True))
        while True:
            self._check(control, deadline)
            run = self.store.run(owner, rid)
            if not run.get('delegation'):
                previous = self.queue(owner, cid)['items']
                prior = [a for r in previous if r['id'] != rid and r['status'] == 'succeeded' for a in r['artifacts']][-16:]
                task = self.task(owner, cid)
                from ..agent_platform.authoring_chat import app_catalog
                plan = self._call('plan', owner, rid, {**self._snapshot(owner, rid),
                    'availableApps': app_catalog(self.w, owner),
                    'selectedCapabilities': run['payload'].get('capabilityRefs', []),
                    'selectedSkills': run['payload'].get('skillNames', []), 'catalog': bridge.catalog(),
                    'builtinTools': self.w.runtime.tools.catalog(), 'availableTools': bridge.definitions() + self.w.runtime.tools.definitions(blocked_capabilities=blocked),
                    'history': task['messages'][-8:], 'priorArtifacts': prior,
                    'answers': [o for o in run['observations'] if 'userAnswer' in o]}, control, deadline)
                assessment = plan.get('assessment') or {}
                if not isinstance(assessment, dict):
                    raise GatewayError('任务判断格式无效。', 502)
                mode = plan.get('mode')
                if mode not in {'authoring', 'asset_app'} and assessment.get('explicitApplication') is True and not run['steps']:
                    mode = 'application'
                if mode == 'application' and (run.get('executionContext') or {}).get('kind') == 'recipe':
                    self._correction(owner, rid, '当前是已保存研究应用的一次执行，请完成目标；不能再次交给应用创建、保存或运行入口。请依据实际需求返回answer/chat/direct/delegate/ask。')
                    continue
                # Enforce the model's dependency assessment, never finance keywords.
                if assessment.get('dependentProcessing') is True and mode in {'chat', 'answer', 'direct'}:
                    self.mutate(owner, rid, lambda r: r['observations'].append({'routingRequirement': '需要依赖中间结果继续处理，必须返回完整delegate契约。'}))
                    self._correction(owner, rid, '任务判断需要多步处理，但未提供委派契约。')
                    continue
                self.mutate(owner, rid, lambda r: r.update(assessment=assessment))
                if mode in {'authoring', 'asset_app'}:
                    from ..agent_platform.authoring_chat import handoff, invoke_app
                    if (run.get('executionContext') or {}).get('kind') == 'recipe':
                        self._correction(owner, rid, '当前只执行已有研究目标，不能创建或转交其他资产。')
                        continue
                    if mode == 'authoring':
                        handoff(self, owner, run, plan.get('authoring'))
                    else:
                        invoke_app(self, owner, run, plan.get('assetApp') or {})
                    return
                if mode == 'ask':
                    self._message(owner, run, 'user', run['message'])
                    self._ask(owner, rid, plan.get('question'))
                if mode == 'application':
                    if assessment.get('explicitApplication') is not True or run['steps']:
                        raise GatewayError('没有明确应用意图，不能将本次执行自动保存为应用。', 422)
                    self.local.inside = True
                    try:
                        result = self.w.platform._dispatch(owner, {**run['payload'], 'taskId': cid, 'idempotencyKey': rid}, control)
                    finally:
                        self.local.inside = False
                    self._check(control, deadline)
                    if result['intent'] == 'chat':
                        self._ask(owner, rid, '请说明要创建或运行的应用；若只需完成本次任务，请在此说明。')
                    self.mutate(owner, rid, lambda r: r.update(kind='application', application=result, legacyPersisted=True))
                    with self.store.transaction() as db:
                        q = self.store.get(db, owner, 'queue', cid, True)
                        q.update(paused=True, pauseReason='已交给应用流程；请完成应用操作后继续排队消息。')
                        self.store.put(db, owner, 'queue', cid, q)
                    self._end(owner, rid, 'succeeded', '已交给应用流程。')
                    return
                if mode == 'chat' and not run['steps'] and not run.get('mainMessageSaved'):
                    self.mutate(owner, rid, lambda r: r.update(kind='chat', status='running'))
                    self.local.inside = True
                    try:
                        stream, abandon = self.w.start_chat({'user': self.w.auth.get_user(owner)}, {**run['payload'], 'taskId': cid})
                        try:
                            for _ in stream:
                                if control['stop'].is_set():
                                    self.w.stop(owner, cid)
                                self._check(control, deadline)
                        finally:
                            stream.close()
                            abandon()
                    finally:
                        self.local.inside = False
                    self._check(control, deadline)
                    task = self.task(owner, cid)
                    self.mutate(owner, rid, lambda r: r.update(legacyPersisted=True))
                    self._end(owner, rid, 'failed' if task.get('error') else 'succeeded', task['messages'][-1].get('content', ''))
                    return
                self._message(owner, run, 'user', run['message'])
                self.mutate(owner, rid, lambda r: r.update(mainMessageSaved=True))
                if mode == 'answer':
                    if not isinstance(plan.get('answer'), str) or not plan['answer']:
                        raise GatewayError('主agent未返回回答。', 502)
                    try:
                        selected = evidence.completion_refs(self, owner, self.store.run(owner, rid), plan)
                    except (GatewayError, CapabilityError) as error:
                        self._correction(owner, rid, str(error), code='INVALID_COMPLETION_EVIDENCE')
                        continue
                    partial = plan.get('completion') == 'partial' or bool(plan.get('missingDeliverables'))
                    completion = {'status': 'partial' if partial else 'complete', 'missingDeliverables': plan.get('missingDeliverables', []),
                                  'reasons': ['当前任务尚有未完成事项。'] if partial else []}
                    self.mutate(owner, rid, lambda r: r.update(kind='direct' if r['steps'] else 'chat', **selected, completion=completion))
                    with self.store.transaction() as db:
                        evidence.adopted(self, db, owner, self.store.get(db, owner, 'run', rid, True), [])
                    self._end(owner, rid, 'partial' if completion['status'] == 'partial' else 'succeeded', plan['answer'])
                    return
                if mode == 'direct':
                    action = plan.get('action')
                    if not isinstance(action, dict) or action.get('type') not in {'tool', 'read'}:
                        raise GatewayError('主助手直接执行仅支持工具或读取；复杂处理应委派。', 502)
                    if not self._load_preferred(owner, rid, plan, bridge, writer, control, deadline):
                        continue
                    self.mutate(owner, rid, lambda r: r.update(kind='direct', status='running'))
                    self._step(owner, rid, action, bridge, writer, control, deadline)
                    continue
                if mode == 'chat':
                    self._correction(owner, rid, '已有本轮执行或用户回答，请根据观察返回answer/direct/delegate，不重新开始普通聊天。')
                    continue
                if mode != 'delegate':
                    raise GatewayError('委派契约无效。', 502)
                if not self._load_preferred(owner, rid, plan, bridge, writer, control, deadline):
                    continue
                self._delegate(owner, rid, plan)
            context = self._snapshot(owner, rid)
            context['availableTools'] = bridge.definitions() + self.w.runtime.tools.definitions(blocked_capabilities=blocked)
            action = self._call('act', owner, rid, context, control, deadline)
            if action.get('type') == 'ask':
                self._ask(owner, rid, action.get('question'))
            if action.get('type') == 'finish':
                if self._review(owner, rid, action, bridge, control, deadline):
                    return
                continue
            self._step(owner, rid, action, bridge, writer, control, deadline)

    def _step(self, owner, rid, action, bridge, writer, control, deadline):
        run = self.store.run(owner, rid)
        kind = action.get('type')
        step = {'id': uid(), 'type': kind, 'status': 'awaiting_approval' if kind == 'code' else 'running', 'correctionIndex': run['correctionsUsed'], 'inputRefs': action.get('inputRefs', []), 'startedAt': now(),
                'name': action.get('name'), 'label': action_label(action, bridge, self.w.runtime.tools),
                'purpose': str(action.get('purpose') or action.get('reason') or '')[:500],
                'arguments': safe_details(action.get('arguments', {}))}
        step.update({k: run[k] for k in ('parentRunId', 'nodeId', 'skillCallId') if k in run})
        step['evidenceId'] = step['id']
        evidence.begin(self, owner, run, step, action)
        control['stepId'] = step['id']
        self.mutate(owner, rid, lambda r: r['steps'].append(step), 'step.started', {'stepId': step['id'], 'type': kind})
        try:
            if kind == 'code':
                result = self._code(owner, rid, step, action, bridge, control, deadline)
            elif kind == 'read':
                a, data = self._input(owner, run, action.get('artifactId'))
                if a.get('inputSource') == 'data_management':
                    from ..workbench.data_files import data_context
                    preview = data_context({'name': a['name'], 'sha256': a['sha256']}, data, max_chars=8000)
                    result = {'artifactId': a['id'], 'preview': preview,
                              'truncated': bool(a.get('previewTruncated') or len(data) > 8000 or len(preview) >= 8000)}
                else:
                    result = {'artifactId': a['id'], 'preview': data[:8000].decode('utf-8', errors='replace'), 'truncated': len(data) > 8000}
            elif kind == 'tool':
                name, arguments = action.get('name'), action.get('arguments')
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise GatewayError('工具调用格式无效。', 400)
                if bridge.owns(name):
                    file_refs = action.get('inputRefs', [])
                    if not isinstance(file_refs, list) or len(file_refs) > 16 or any(not isinstance(x, str) for x in file_refs):
                        raise GatewayError('工具输入文件引用无效。', 400)
                    granted = {}
                    for key in file_refs:
                        meta, data = self._input(owner, run, key)
                        granted[key + Path(meta['name']).suffix] = data
                    from ..capability_library.read_budget import task_reads
                    def account_reads(reads, rows):
                        def update_budget(doc):
                            used = doc.setdefault('dataReadBudget', {'reads': 0, 'rows': 0})
                            if used['reads'] + reads > 200 or used['rows'] + rows > 20000:
                                raise GatewayError('本任务读取预算已用尽，请缩小范围。', 429, 'READ_BUDGET_EXCEEDED')
                            used['reads'] += reads
                            used['rows'] += rows
                        self.mutate(owner, rid, update_budget)
                    with task_reads(account_reads, scope=rid):
                        result = bridge.execute(name, arguments, writer, control['stop'], task_files=granted)
                else:
                    ctx = ToolContext(control['stop'], lambda response: control.update(response=response), writer, query=run['message'])
                    value = self.w.runtime.tools.execute(name, arguments, ctx, blocked_capabilities={'search'} if run['payload'].get('searchMode') == 'off' else set(), call_count=sum(s.get('name') == name for s in run['steps']))
                    result = {'content': value.content, 'sources': value.sources or []}
                step['name'] = name
                if isinstance(result, dict) and result.get('error'):
                    raise GatewayError(str(result['error']), 422)
                output_files = result.pop('_files', {}) if isinstance(result, dict) else {}
                if output_files and run.get('skillPermissions', {}).get('outputFiles') is False:
                    raise GatewayError('此Skill没有生成文件权限。', 403, 'SKILL_FILE_NOT_GRANTED')
                raw = canonical(result).encode()
                a = self._artifact(owner, rid, 'tool-result.json', raw, step['id'], role='execution_result')
                imported = [a]
                for name, data in output_files.items():
                    imported.append(self._artifact(owner, rid, name, data, step['id'], [a['id']]))
                for item in result.get('artifacts', []) if isinstance(result, dict) else []:
                    record = self.w.capabilities.store.get(owner, 'download', item['id'])
                    data = self.w.capabilities.store.blob(owner, item['id'])
                    imported.append(self._artifact(owner, rid, record['name'], data, step['id'], [a['id']]))
                result = {'summary': result_summary(result), 'resultPreview': canonical(result)[:6000], 'artifacts': imported}
            else:
                raise GatewayError('未知执行动作。', 400)
            self._check(control, deadline)
            self._remember_loaded(owner, rid, bridge)
            step.update(status='succeeded', result=result, summary=result.get('summary') or result_summary(result.get('content', result)), completedAt=now())
            recorded = evidence.update(self, owner, step['id'], status='succeeded', completedAt=step['completedAt'],
                outputRefs=[a['id'] for a in result.get('artifacts', [])],
                resultArtifactId=(next((a for a in result.get('artifacts', []) if a['name'] == 'analysis-result.json'), {})
                                  if kind == 'code' else (result.get('artifacts') or [{}])[0]).get('id'),
                codeArtifactId=result.get('codeArtifactId'))
            step['invocationIds'] = recorded['invocationIds']
            step['skillCallIds'] = recorded.get('skillCallIds', [])
            self.mutate(owner, rid, lambda r: (r['steps'].__setitem__(-1, step), r['observations'].append(result)), 'step.completed', {'stepId': step['id']})
        except (Waiting, Cancelled):
            raise
        except Exception as error:
            if getattr(error, 'invocationId', None):
                evidence.invocation(self, owner, step['id'], error.invocationId)
            self._check(control, deadline)
            detail = (str(error) + '\n' + str(getattr(error, 'logs', '')))[:4000]
            if step['status'] == 'awaiting_approval':
                step['label'] = '准备临时代码（尚未执行）'
            step.update(status='failed', error=detail, errorSummary=error_summary(detail), completedAt=now())
            evidence.update(self, owner, step['id'], status='failed', completedAt=step['completedAt'], error=detail)
            self.mutate(owner, rid, lambda r: r['steps'].__setitem__(-1, step), 'step.failed', {'stepId': step['id'], 'error': detail})
            if getattr(error, 'code', None) in {'SUB_AGENT_CORRECTIONS_EXHAUSTED', 'MODEL_FORMAT_RETRIES_EXHAUSTED'}:
                raise
            self._correction(owner, rid, detail)
        finally:
            control.pop('stepId', None)


    def _code(self, owner, rid, step, action, bridge, control, deadline):
        run = self.store.run(owner, rid)
        refs, code = action.get('inputRefs', []), action.get('code')
        if not isinstance(refs, list) or len(refs) > 16 or any(not isinstance(x, str) for x in refs) or not isinstance(code, str):
            raise GatewayError('代码或输入引用无效。', 400)
        if not run['delegation'].get('allowTemporaryCode'):
            raise GatewayError('主agent未允许临时代码。', 403)
        required = run['delegation'].get('requiredCapabilities', [])
        for version in bridge.loaded.values():
            self.w.capabilities.admit(owner, version['assetId'], version['versionId'], version['configurationId'], historical=True)
        executed = {s.get('name') for s in run['steps'] if s['status'] == 'succeeded'}
        for key in required:
            loaded = bridge.loaded.get(key)
            satisfied = bool(loaded and (loaded['manifest']['kind'] == 'skill' or 'cap_' + loaded['versionId'] in executed)) or key in executed
            if not satisfied:
                raise GatewayError('尚未使用用户强制指定能力，不能改用代码。', 403)
        approved = next((a for a in run['approvals'] if a['decision'] == 'approve' and set(refs) <= set(a['inputRefs'])), None)
        if not approved:
            application = {'id': uid(), 'reason': str(action.get('reason', ''))[:4000], 'inputRefs': refs, 'scope': 'isolated-python-read-input-write-artifacts'}
            self.mutate(owner, rid, lambda r: r.update(status='awaiting_main_approval'), 'approval.requested', application)
            decision = self._call('approve', owner, rid, {**self._snapshot(owner, rid), 'application': application, 'code': code}, control, deadline)
            application.update(decision=decision.get('decision'), reason=decision.get('reason', application['reason']), scopeDigest=digest({'refs': refs, 'delegation': run['delegation']}))
            self.mutate(owner, rid, lambda r: (r['approvals'].append(application), r.update(status='running')), 'approval.decided', application)
            if decision.get('decision') == 'ask':
                self._ask(owner, rid, decision.get('question'))
            if decision.get('decision') != 'approve':
                raise GatewayError('主agent未批准：' + str(decision.get('reason', '')), 403)
        step.update(status='running', startedAt=now())
        self.mutate(owner, rid, lambda r: r['steps'].__setitem__(-1, step), 'step.executing', {'stepId': step['id']})
        inputs, paths = {}, {}
        for key in refs:
            a, data = self._input(owner, run, key)
            name = key + Path(a['name']).suffix
            inputs[name] = data
            paths[key] = '/input/' + name
        if self.before_code:
            self.before_code(owner, rid, control['stop'])
        self._check(control, deadline)
        helper = Path(__file__).with_name('png_plot.py').read_text(encoding='utf-8')
        # Export exactly the executed self-contained source, including plotting support.
        executable = ("import sys as _sys, types as _types\n_cd80_plot = _types.ModuleType('cd80_plot')\n"
                      + 'exec(' + repr(helper) + ", _cd80_plot.__dict__)\n_sys.modules['cd80_plot'] = _cd80_plot\n" + code)
        evidence.update(self, owner, step['id'], executedCode=executable, codeSha256=digest(executable.encode()),
                        resolvedInputPaths=paths, inputDigests={name: digest(data) for name, data in inputs.items()})
        output = self.executor.execute(executable, {'files': paths}, inputs, control['stop'])
        self._check(control, deadline)
        if output['files'] and run.get('skillPermissions', {}).get('outputFiles') is False:
            raise GatewayError('此Skill没有生成文件权限，临时代码不能绕过。', 403, 'SKILL_FILE_NOT_GRANTED')
        files = [self._artifact(owner, rid, name, data, step['id'], refs) for name, data in output['files'].items()]
        source = self._artifact(owner, rid, 'analysis.py', executable.encode(), step['id'], refs, role='executed_source')
        result = self._artifact(owner, rid, 'analysis-result.json', canonical(output['result']).encode(), step['id'], refs, role='execution_result')
        return {'content': output['result'], 'artifacts': files + [source, result], 'codeArtifactId': source['id']}

    def _review(self, owner, rid, action, bridge, control, deadline):
        run = self.mutate(owner, rid, lambda r: r.update(status='reviewing'))
        ids = action.get('artifactIds', [])
        if not isinstance(ids, list) or any(not isinstance(x, str) for x in ids):
            self._correction(owner, rid, '交付引用无效。')
            return False
        assets = []
        try:
            selected = evidence.completion_refs(self, owner, run, action)
            for key in ids:
                a, data = self._input(owner, run, key)
                if digest(data) != a['sha256']:
                    raise GatewayError('产物摘要不一致。', 422)
                assets.append(a)
        except (GatewayError, CapabilityError) as error:
            self._correction(owner, rid, str(error))
            return False
        required = run['delegation'].get('requiredExtensions', [])
        obligation = visualization_requirement(run) if not run.get('skillCallId') else {'visualization': False, 'source': None}
        if obligation['source'] and not obligation['visualization']:
            required = [ext for ext in required if ext.lower() not in {'.png', '.svg', '.jpg', '.jpeg', '.gif', '.webp'}]
        missing = [ext for ext in required if not any(a['name'].endswith(ext) for a in assets)]
        visual_missing = self._visualization_missing(owner, run, assets)
        executed = {s.get('name') for s in run['steps'] if s['status'] == 'succeeded'}
        missing_capabilities = []
        for key in run['delegation'].get('requiredCapabilities', []):
            loaded = bridge.loaded.get(key)
            if not (key in executed or loaded and (loaded['manifest']['kind'] == 'skill' or 'cap_' + loaded['versionId'] in executed)):
                missing_capabilities.append(key)
        decision = self._call('review', owner, rid, {**self._snapshot(owner, rid), 'submission': action,
            'submittedArtifacts': assets, 'platformChecks': {'missingExtensions': missing, 'missingCapabilities': missing_capabilities,
                'missingDeliverables': visual_missing, 'artifactsExist': True}}, control, deadline)
        try:
            if 'adoptedStepIds' in decision or 'resultArtifactIds' in decision:
                selected = evidence.completion_refs(self, owner, run, decision)
        except (GatewayError, CapabilityError) as error:
            self._correction(owner, rid, str(error), code='INVALID_COMPLETION_EVIDENCE')
            return False
        partial = bool(visual_missing or action.get('completion') == 'partial' or decision.get('completion') == 'partial'
                       or action.get('missingDeliverables') or decision.get('missingDeliverables'))
        completion = {'status': 'partial' if partial else 'complete',
                      'missingDeliverables': list(dict.fromkeys(visual_missing + action.get('missingDeliverables', []) + decision.get('missingDeliverables', []))),
                      'reasons': ['用户要求的图表尚未交付。'] if visual_missing else []}
        if partial:
            if not completion['reasons']:
                completion['reasons'] = ['尚未交付：' + '、'.join(completion['missingDeliverables']) if completion['missingDeliverables'] else '当前任务尚有未完成事项。']
            decision = {**decision, 'decision': 'fail', 'completion': 'partial', 'reason': '；'.join(completion['reasons'])}
        elif (missing or missing_capabilities) and decision.get('decision') == 'accept':
            decision = {'decision': 'revise', 'reason': '交付缺少格式或强制能力执行：' + ', '.join(missing + missing_capabilities)}
        with self.lock, self.store.transaction() as db:
            latest = self.store.get(db, owner, 'run', rid, True)
            if latest['status'] in TERMINAL or latest['status'] == 'cancelling':
                raise Cancelled()
            latest['reviews'].append(decision)
            if decision.get('decision') == 'accept' or partial:
                latest.update(**selected, completion=completion, deliveries=assets)
                evidence.adopted(self, db, owner, latest, assets)
            call = next(c for c in reversed(latest['modelCalls']) if c['stage'] == 'review' and c['status'] == 'succeeded')
            call.update(finalDecision=decision['decision'], summary=str(decision.get('reason') or
                {'accept': '验收通过', 'revise': '验收未通过，需要补齐交付', 'fail': '验收未通过'}.get(decision['decision']))[:600])
            record = self.store.get(db, owner, 'model_trace', call['id'])
            if record:
                record['platformDecision'] = redacted({key: value[:4000] if isinstance(value, str) else value
                                                      for key, value in decision.items() if key in {'decision', 'reason', 'answer'}})
                record['platformDecisionTruncated'] = any(isinstance(value, str) and len(value) > 4000 for value in decision.values())
                self.store.put(db, owner, 'model_trace', call['id'], record)
            self.store.save_run(db, owner, latest, 'review.completed', decision)
        if decision.get('decision') == 'revise':
            self._correction(owner, rid, str(decision.get('reason', '验收未通过。')))
            return False
        if partial:
            self._end(owner, rid, 'partial', str(decision.get('answer') or action.get('answer') or decision['reason']))
            return True
        if decision.get('decision') != 'accept':
            self._end(owner, rid, 'partial' if assets else 'failed', str(decision.get('reason', '验收未通过。')))
            return True
        answer = str(decision.get('answer') or action.get('answer') or '任务已完成。')
        self.mutate(owner, rid, lambda r: r.update(deliveries=assets))
        self._end(owner, rid, 'succeeded', answer)
        return True

    def close(self):
        self.closed = True
        with self.lock:
            for control in self.controls.values():
                control['stop'].set()
                if control.get('response'):
                    try:
                        control['response'].abort()
                    except Exception:
                        pass
            workers = list(self.workers.values())
        for thread in workers:
            thread.join(timeout=5)
