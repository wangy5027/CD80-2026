"""Persistent conversation authoring with isolated candidates and explicit commit."""
import copy
import re
import threading
from contextlib import nullcontext

from ..capability_library.contracts import canonical, digest, fail, now, uid
from ..workbench.errors import GatewayError
from .authoring_budget import BudgetContext, LIMITS, new_round
from .authoring_context import resolve
from .authoring_store import AuthoringStore

WORKING = {'preparing', 'checking', 'validating', 'repairing'}
TECHNICAL_CODES = {'INVALID_DEFINITION', 'INVALID_FILES', 'INVALID_PACKAGE', 'INVALID_SNAPSHOT',
                   'TEST_PLAN_INVALID', 'TEST_FIXTURE_INVALID', 'UNSUPPORTED_SCHEMA', 'INVALID_SCHEMA',
                   'MISSING_IMPLEMENTATION', 'agent_model_output_invalid'}


def draft_only(instruction, kind=None):
    if kind == 'app':
        # APP creation is save-first: "生成后只保存/不要测试" still needs a
        # concrete definition. Only an explicit request to retain the idea
        # without generating an APP should stop before model generation.
        return bool(re.search(
            r'(?:只|仅|先)(?:记录|保存|存下)(?:当前|这个|这段)?(?:想法|创意|需求|描述|草稿)'
            r'|(?:先不|暂不|不要|无需|不用|别)(?:生成|创建|实现)(?:APP|应用)?(?:[，。,.！!]|$)',
            instruction, re.I))
    return bool(re.search(r'只保存|仅保存|先别测试|先不测试|保存当前草稿|不要测试|save\s+(?:only|as draft)', instruction, re.I))


class Authoring:
    def __init__(self, workbench, config, adapters=None):
        self.w = workbench
        self.store = AuthoringStore(workbench.platform.repository)
        if adapters is None:
            from .authoring_adapters import AuthoringAdapters
            adapters = AuthoringAdapters(workbench)
        self.adapters = adapters
        self.lock, self.active, self.closed = threading.RLock(), {}, False
        self.slots = threading.BoundedSemaphore(4)

    def close(self):
        self.closed = True
        with self.lock:
            active = list(self.active.values())
        for control in active:
            control['stop'].set()
            if control.get('budget'):
                control['budget'].set()
        for control in active:
            control['thread'].join(timeout=5)

    @staticmethod
    def _revision(session, body):
        if type(body.get('revision')) is not int or body['revision'] != session['revision']:
            fail('REVISION_CONFLICT', '草稿已更新，请读取最新内容后继续。', 409)

    @staticmethod
    def _instruction(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 100000:
            fail('INVALID_INSTRUCTION', '请输入有效的创作要求，最多100000字符。')
        return value.strip()

    def listing(self, owner, conversation=None, asset_id=None, cursor=None, limit=20):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            fail('INVALID_REQUEST', '分页数量无效。')
        if not 1 <= limit <= 100:
            fail('INVALID_REQUEST', '分页数量应在1到100之间。')
        rows = sorted(self.store.listing('sessions', owner), key=lambda s: (s['createdAt'], s['id']), reverse=True)
        rows = [s for s in rows if (not conversation or s.get('conversationId') == conversation) and
                (not asset_id or (s.get('target') or {}).get('assetId') == asset_id or
                 (s.get('result') or {}).get('assetId') == asset_id)]
        if cursor:
            position = next((i for i, s in enumerate(rows) if s['id'] == cursor), None)
            if position is None:
                fail('INVALID_CURSOR', '分页位置不存在。')
            rows = rows[position + 1:]
        return {'items': [self._public(s) for s in rows[:limit]],
                'nextCursor': rows[limit - 1]['id'] if len(rows) > limit else None}

    def _public(self, session):
        result = {k: copy.deepcopy(v) for k, v in session.items() if not k.startswith('_') and k != 'owner'}
        actions = ['messages', 'draft_only']
        if session['phase'] in WORKING:
            actions.append('cancel')
        if session['phase'] in {'interrupted', 'failed', 'cancelled', 'budget_paused'}:
            actions.append('resume')
        if session['phase'] == 'budget_paused':
            actions.append('extend_budget')
        if session.get('question'):
            actions.append('answers')
        if session['phase'] == 'awaiting_review' and session.get('reviewSnapshotId'):
            actions.append('save_and_enable')
        if session['phase'] == 'committing':
            actions = []
        if session['phase'] == 'completed' and session.get('result'):
            actions = []
        result['allowedActions'] = actions
        result['draftSaved'] = True
        return result

    def detail(self, owner, session_id):
        session = self.store.read('sessions', owner, session_id)
        def optional(kind, key):
            return self.store.read(kind, owner, key) if key else None
        candidate = optional('candidates', session.get('currentCandidateId'))
        if candidate:
            candidate = {k: v for k, v in candidate.items() if k != '_prepared'}
        budget = optional('budget_rounds', session['budgetRoundId'])
        with self.lock:
            control = self.active.get((owner, session_id))
            if control and control.get('budget'):
                budget['consumed']['activeMilliseconds'] = min(LIMITS['activeMilliseconds'],
                    int(LIMITS['activeMilliseconds'] - control['budget'].remaining_seconds() * 1000))
        rounds = self.store.listing('budget_rounds', owner, session_id)
        budget['lifetimeConsumed'] = {key: sum(r['consumed'][key] for r in rounds) for key in LIMITS}
        if session.get('_budgetConfirmationId'):
            budget['confirmationId'] = session['_budgetConfirmationId']
        budget['unknownRequests'] = [{k: call.get(k) for k in ('id', 'model', 'sentAt')}
            for call in self.store.listing('model_requests', owner, session_id)
            if call.get('state') == 'unknown' and not call.get('acknowledgedAt')]
        return {'session': self._public(session), 'candidate': candidate,
                'runtimeBindings': copy.deepcopy(session['_context'].get('runtimeBindings', {})),
                'sources': [{k: v for k, v in s.items() if k != 'data'} for s in self.store.listing('sources', owner, session_id)],
                'testPlan': optional('test_plans', session.get('testPlanId')),
                'evidence': self.store.listing('evidence', owner, session_id),
                'reviewSnapshot': optional('review_snapshots', session.get('reviewSnapshotId')),
                'budget': budget, 'commit': optional('commits', session.get('commitId'))}

    def model_trace(self, owner, session_id, trace_id):
        # Both reads are scoped to the authenticated owner. A trace ID from a
        # different session must not be usable as an alternate read path.
        self.store.read('sessions', owner, session_id)
        trace = self.store.read('model_traces', owner, trace_id)
        if trace['sessionId'] != session_id:
            fail('NOT_FOUND', '模型请求记录不存在或无权访问。', 404)
        return trace

    def create(self, owner, body, key):
        if not isinstance(body, dict):
            fail('INVALID_REQUEST', '创作请求必须为对象。')
        kind, intent, mode = body.get('kind'), body.get('intent', 'create'), body.get('mode', 'prepare')
        if kind not in {'app', 'tool', 'skill'} or intent not in {'create', 'capture', 'edit', 'prepare_import', 'upgrade', 'extract'}:
            fail('INVALID_REQUEST', '资产类型或创作操作无效。')
        if mode not in {'prepare', 'draft_only'}:
            fail('INVALID_REQUEST', '创作模式无效。')
        instruction = self._instruction(body.get('instruction'))
        if mode != 'draft_only' and draft_only(instruction, kind):
            mode = 'draft_only'
        with self.store.transaction() as db:
            old = db.execute('SELECT hash,result FROM authoring_requests WHERE owner=? AND scope=? AND key=?',
                             (owner, 'create', key)).fetchone()
            if old:
                if old['hash'] != digest(body):
                    fail('IDEMPOTENCY_CONFLICT', '同一请求标识不能用于不同内容。', 409)
                import json
                previous_id = json.loads(old['result'])['sessionId']
            else:
                previous_id = None
        if previous_id:
            return self.detail(owner, previous_id)
        session_id = uid('authoring')
        # Resolve outside the transaction: source ownership is checked before a
        # model can see data, and no external source is fetched during a DB lock.
        sources, context, initial = resolve(self.w, owner, session_id, body.get('conversationId'), instruction,
                                            body.get('sourceRefs', []), kind, body.get('target'))
        target = context.get('target')
        if intent == 'edit' and not target:
            fail('INVALID_TARGET', '修改现有资产需要准确目标。')
        with self.lock, self.store.transaction() as db:
            def action():
                budget = new_round(session_id)
                session = {'id': session_id, 'owner': owner, 'conversationId': body.get('conversationId'),
                           'kind': kind, 'intent': intent, 'mode': mode, 'target': target,
                           'revision': 1, 'instruction': instruction, 'sourceIds': [s['id'] for s in sources],
                           'currentCandidateId': None, 'phase': 'preparing' if mode == 'prepare' else 'completed',
                           'budgetRoundId': budget['id'], 'question': None, 'commitId': None,
                           'createdAt': now(), '_context': context, '_initialContent': initial,
                           '_baseContent': copy.deepcopy(context.get('baseContent', initial)), '_generate': True}
                for source in sources:
                    self.store.put(db, 'sources', owner, source)
                self.store.put(db, 'authorizations', owner, {'id': uid('authorization'), 'sessionId': session_id,
                    'revision': 1, 'fileIds': context['fileIds'], 'runtimeBindings': context.get('runtimeBindings', {}),
                    'createdAt': now(), 'basis': 'session_sources'})
                self.store.put(db, 'budget_rounds', owner, budget)
                self.store.put(db, 'sessions', owner, session)
                self.store.put(db, 'messages', owner, {'id': uid('message'), 'sessionId': session_id,
                    'kind': 'instruction', 'instruction': instruction, 'revisionBefore': 0, 'revisionAfter': 1, 'createdAt': now()})
                return {'sessionId': session_id}
            result, fresh = self.store.request(db, owner, 'create', key, body, action)
        if fresh and mode == 'prepare':
            self._launch(owner, result['sessionId'])
        return self.detail(owner, result['sessionId'])

    def command(self, owner, session_id, operation, body, key):
        if operation == 'confirm':
            return self.confirm(owner, session_id, body, key)
        if operation not in {'messages', 'answers', 'continue', 'cancel'} or not isinstance(body, dict):
            fail('INVALID_REQUEST', '不支持的创作操作。')
        launch = False
        with self.lock, self.store.transaction() as db:
            def action():
                nonlocal launch
                session = self.store.get(db, 'sessions', owner, session_id)
                self._revision(session, body)
                if session['phase'] == 'committing' or (session['phase'] == 'completed' and session.get('result')):
                    fail('COMMIT_IN_PROGRESS', '当前提交已固定，请完成后从资产发起下一次修改。', 409)
                if operation in {'messages', 'answers'}:
                    self._supplement(owner, session, body, db)
                if operation == 'messages':
                    if ('instruction' in body) == ('candidateContent' in body):
                        fail('INVALID_REQUEST', '请提交自然语言要求或完整编辑内容中的一种。')
                    message = {'id': uid('message'), 'sessionId': session_id, 'revisionBefore': session['revision'],
                               'revisionAfter': session['revision'] + 1, 'createdAt': now()}
                    if 'instruction' in body:
                        instruction = self._instruction(body['instruction'])
                        if len(session['instruction']) + len(instruction) > 100000:
                            fail('INVALID_INSTRUCTION', '累计需求过长，请精简后再提交。')
                        session['instruction'] += '\n\n用户追加要求：' + instruction
                        message.update(kind='instruction', instruction=instruction)
                        session['_generate'] = True
                    else:
                        if not isinstance(body['candidateContent'], dict):
                            fail('INVALID_CONTENT', '高级编辑内容必须为完整对象。')
                        session['_initialContent'] = copy.deepcopy(body['candidateContent'])
                        session['_generate'] = False
                        message.update(kind='candidate_edit', candidateContent=copy.deepcopy(body['candidateContent']))
                    self.store.put(db, 'messages', owner, message)
                    only_save = 'instruction' in body and draft_only(body['instruction'], session['kind'])
                    session.update(mode='draft_only' if only_save else 'prepare', phase='completed' if only_save else 'preparing',
                                   question=None, reviewSnapshotId=None, commitId=None, error=None)
                    launch = not only_save
                elif operation == 'answers':
                    question = session.get('question')
                    if not question or question['id'] != body.get('questionId'):
                        fail('REVISION_CONFLICT', '问题已更新，请读取当前问题。', 409)
                    answer = self._instruction(body.get('answer'))
                    supplement = ('\n\n针对问题：' + question['prompt'] + '\n用户回答：' + answer
                                  if session['kind'] == 'app' else '\n\n用户回答：' + answer)
                    if len(session['instruction']) + len(supplement) > 100000:
                        fail('INVALID_INSTRUCTION', '累计需求过长，请精简后再提交。')
                    session['instruction'] += supplement
                    session.setdefault('_answers', []).append({'question': question, 'answer': answer})
                    session.update(phase='preparing', question=None, _generate=True, commitId=None, error=None)
                    launch = True
                elif operation == 'continue':
                    if body.get('budgetRoundId') != session['budgetRoundId']:
                        fail('REVISION_CONFLICT', '预算轮次已改变，请读取当前进度。', 409)
                    if session['phase'] not in {'failed', 'cancelled', 'interrupted', 'budget_paused'}:
                        fail('INVALID_STATE', '当前状态无需恢复。', 409)
                    if (owner, session_id) in self.active:
                        fail('OUTCOME_UNKNOWN', '上次处理仍在停止，请等待结果明确后继续。', 409)
                    if body.get('mode') == 'extend_budget':
                        confirmation = session.get('_budgetConfirmationId')
                        if session['phase'] != 'budget_paused' or not confirmation or body.get('confirmationId') != confirmation:
                            fail('BUDGET_CONFIRMATION_REQUIRED', '请明确确认当前展示的追加预算。', 409)
                        previous = self.store.get(db, 'budget_rounds', owner, session['budgetRoundId'])
                        previous.update(status='closed', closedAt=now())
                        budget = new_round(session_id, previous, confirmation)
                        self.store.put(db, 'budget_rounds', owner, previous)
                        self.store.put(db, 'budget_rounds', owner, budget)
                        session['budgetRoundId'] = budget['id']
                        session.pop('_budgetConfirmationId', None)
                    elif body.get('mode') != 'resume':
                        fail('INVALID_REQUEST', '请选择恢复或明确追加预算。')
                    # Unknown in-flight calls require a new user instruction, not replay.
                    import json
                    unknown_calls = [json.loads(r['doc']) for r in db.execute(
                        'SELECT doc FROM authoring_model_requests WHERE owner=? AND session=?', (owner, session_id))]
                    unresolved = [v for v in unknown_calls if v.get('state') == 'unknown' and not v.get('acknowledgedAt')]
                    acknowledgements = body.get('acknowledgeUnknownRequests', [])
                    if not isinstance(acknowledgements, list) or any(not isinstance(v, str) for v in acknowledgements):
                        fail('INVALID_REQUEST', '未完成回复的确认无效。')
                    if unresolved and set(acknowledgements) != {v['id'] for v in unresolved}:
                        fail('OUTCOME_UNKNOWN', '上次模型回复结果未知，请明确放弃未完成回复后继续；旧请求不会重放。', 409,
                             {'requestIds': [v['id'] for v in unresolved]})
                    for call in unresolved:
                        call.update(acknowledgedAt=now(), acknowledgedRevision=session['revision'],
                                    acknowledgement='abandon_unfinished_response')
                        self.store.put(db, 'model_requests', owner, call)
                    session.update(phase='preparing', question=None, error=None)
                    launch = True
                else:
                    session.update(phase='cancelled', question=None, reviewSnapshotId=None)
                session['revision'] += 1
                self.store.put(db, 'sessions', owner, session)
                return {'sessionId': session_id}
            _, fresh = self.store.request(db, owner, session_id + ':' + operation, key, body, action)
        if fresh:
            self._stop(owner, session_id)
            if launch:
                self._launch(owner, session_id)
        return self.detail(owner, session_id)

    def _supplement(self, owner, session, body, db):
        refs = body.get('sourceRefs', [])
        if not isinstance(refs, list) or any(not isinstance(r, dict) or r.get('kind') != 'upload' for r in refs):
            fail('INVALID_SOURCE', '补充输入只接受明确授权的本人文件。')
        sources, context, _ = resolve(self.w, owner, session['id'], None, '', refs, session['kind'])
        for source in sources:
            self.store.put(db, 'sources', owner, source)
            session['sourceIds'].append(source['id'])
        saved = session['_context']
        saved['fileIds'] = list(dict.fromkeys(saved.get('fileIds', []) + context['fileIds']))
        saved.setdefault('sources', []).extend(context['sources'])
        bindings = body.get('runtimeBindings')
        if bindings is not None:
            if not isinstance(bindings, dict) or len(bindings) > 64:
                fail('INVALID_BINDINGS', '请选择当前账号的连接。')
            for identity, connection_id in bindings.items():
                if not isinstance(identity, str) or not isinstance(connection_id, str):
                    fail('INVALID_BINDINGS', '连接绑定无效。')
                connection = self.w.capabilities.store.get(owner, 'connection', connection_id)
                if connection.get('sourceIdentity') != identity:
                    fail('INVALID_BINDINGS', '所选连接与资产需要的来源不一致。')
            saved['runtimeBindings'] = copy.deepcopy(bindings)
        if sources or bindings is not None:
            self.store.put(db, 'authorizations', owner, {'id': uid('authorization'), 'sessionId': session['id'],
                'revision': session['revision'] + 1, 'fileIds': context['fileIds'],
                'runtimeBindings': copy.deepcopy(bindings or {}), 'createdAt': now(), 'basis': 'explicit_user_selection'})

    def _stop(self, owner, session_id):
        with self.lock:
            control = self.active.get((owner, session_id))
            if control:
                control['stop'].set()
                if control.get('budget'):
                    control['budget'].set()

    def _launch(self, owner, session_id):
        with self.lock:
            if self.closed or (owner, session_id) in self.active:
                return
            session = self.store.read('sessions', owner, session_id)
            if session['phase'] != 'preparing':
                return
            control = {'stop': threading.Event(), 'revision': session['revision']}
            thread = threading.Thread(target=self._worker, args=(owner, session_id, control), daemon=True,
                                      name='asset-authoring-' + session_id[-8:])
            control['thread'] = thread
            self.active[owner, session_id] = control
            thread.start()

    def _update(self, owner, session_id, revision, change):
        with self.store.transaction() as db:
            session = self.store.get(db, 'sessions', owner, session_id)
            if session['revision'] != revision or session['phase'] not in WORKING:
                fail('CANDIDATE_SUPERSEDED', '此处理已被新要求替代。', 409)
            change(session, db)
            self.store.put(db, 'sessions', owner, session)
            return session

    def _worker(self, owner, session_id, control):
        revision = control['revision']
        acquired = False
        attempt = {'id': uid('attempt'), 'sessionId': session_id, 'operation': 'prepare', 'status': 'queued', 'createdAt': now()}
        try:
            while not control['stop'].is_set():
                acquired = self.slots.acquire(timeout=.1)
                if acquired:
                    break
            if not acquired:
                return
            session = self.store.read('sessions', owner, session_id)
            if session['revision'] != revision or control['stop'].is_set():
                return
            attempt.update(status='running', budgetRoundId=session['budgetRoundId'], startedAt=now())
            with self.store.transaction() as db:
                self.store.put(db, 'attempts', owner, attempt)
            budget = BudgetContext(self.store, owner, session_id, session['budgetRoundId'], attempt['id'],
                                   control['stop'], revision=revision)
            control['budget'] = budget
            with budget.bind():
                self._prepare(owner, session, budget)
                attempt['status'] = 'succeeded'
        except Exception as error:
            interrupted_evidence = getattr(error, 'authoringEvidence', None)
            if isinstance(interrupted_evidence, dict):
                with self.store.transaction() as db:
                    plan_id = getattr(error, 'authoringPlanId', None)
                    plan_record = self.store.get(db, 'test_plans', owner, plan_id) if plan_id else None
                    self.store.put(db, 'evidence', owner, {**copy.deepcopy(interrupted_evidence), 'id': uid('evidence'),
                        'sessionId': session_id, 'revision': revision, 'candidateId': plan_record.get('candidateId') if plan_record else None,
                        'planId': plan_id, 'status': 'interrupted', 'completedAt': now()})
            attempt.update(status='cancelled' if control['stop'].is_set() else 'failed', error={
                'code': getattr(error, 'code', None) or 'AUTHORING_FAILED',
                'message': str(error) if isinstance(error, GatewayError) else '准备未完成，草稿已保留。'})
            code = attempt['error']['code']
            if control.get('budget') and control['budget'].expired:
                code = 'AUTHORING_DEADLINE'
                attempt['error'] = {'code': code, 'message': '本轮有效执行时间已用完，草稿和结果已保留。'}
            try:
                def failed(session, db):
                    session['error'] = attempt['error']
                    if code in {'BUDGET_EXHAUSTED', 'AUTHORING_DEADLINE'}:
                        session.update(phase='budget_paused', _budgetConfirmationId=uid('confirmation'))
                    elif code in {'AUTHORING_INPUT_REQUIRED', 'FILE_NOT_GRANTED', 'MISSING_DEPENDENCY', 'INVALID_BINDINGS'}:
                        kind = 'authorization' if code == 'FILE_NOT_GRANTED' else 'input'
                        session.update(phase='awaiting_authorization' if kind == 'authorization' else 'awaiting_input',
                            question={'id': uid('question'), 'kind': kind, 'prompt': attempt['error']['message'], 'revision': revision})
                    else:
                        session['phase'] = 'failed'
                self._update(owner, session_id, revision, failed)
            except GatewayError:
                pass
        finally:
            attempt['completedAt'] = now()
            if attempt['status'] != 'queued':
                with self.store.transaction() as db:
                    self.store.put(db, 'attempts', owner, attempt)
            if acquired:
                self.slots.release()
            with self.lock:
                if self.active.get((owner, session_id)) is control:
                    self.active.pop((owner, session_id), None)
            # A newer message may have stopped this worker. It starts only after
            # the old execution has relinquished the shared session allowance.
            self._launch(owner, session_id)

    def _prepare(self, owner, session, budget):
        session_id, revision = session['id'], session['revision']
        context = copy.deepcopy(session['_context'])
        context['target'] = session.get('target')
        content = session.get('_initialContent')
        previous = None
        if session.get('currentCandidateId'):
            old = self.store.read('candidates', owner, session['currentCandidateId'])
            previous = old['_prepared']
            if session.get('_generate', True):
                content = old['content']
        elif session.get('target'):
            previous = {'content': session.get('_baseContent'), 'target': session['target']}
        if session['intent'] == 'prepare_import' and content is None and context.get('importIssues'):
            self._question(owner, session, context['importIssues'], 'input')
            return
        if session.get('_generate', True):
            try:
                content = self.adapters.create(owner, session['kind'], session['instruction'], content=content,
                                               context=context, stopped=budget)
            except GatewayError as error:
                if getattr(error, 'code', None) not in TECHNICAL_CODES:
                    raise
                content = self._repair_content(owner, session, budget, content,
                    {'issues': [{'code': error.code, 'message': str(error)}]}, context)
        plan = None
        while True:
            budget.check()
            self._update(owner, session_id, revision, lambda s, db: s.update(draftContent=copy.deepcopy(content)))
            try:
                prepared = self.adapters.prepare(owner, session['kind'], content, previous=previous,
                                                 bindings=context.get('runtimeBindings', context.get('bindings')))
            except GatewayError as error:
                if getattr(error, 'code', None) not in TECHNICAL_CODES:
                    raise
                content = self._repair_content(owner, session, budget, content,
                    {'issues': [{'code': error.code, 'message': str(error)}]}, context)
                continue
            candidate = {'id': uid('candidate'), 'sessionId': session_id, 'revision': revision,
                         'kind': session['kind'], 'createdAt': now(), '_prepared': prepared,
                         **{key: copy.deepcopy(prepared.get(key)) for key in
                            ('content', 'contentDigest', 'executionDigest', 'runtimeContextDigest', 'changeClass', 'issues')}}
            candidate['sourceIds'] = session['sourceIds']
            def save_candidate(current, db):
                self.store.put(db, 'candidates', owner, candidate)
                current.update(currentCandidateId=candidate['id'], phase='checking', reviewSnapshotId=None,
                               _generate=False, _initialContent=candidate['content'])
            self._update(owner, session_id, revision, save_candidate)
            if session['kind'] == 'app':
                # A usable definition is now frozen. The personal App version is
                # saved before any verification; all later work uses its detail.
                self._save_app_candidate(owner, session_id, revision, candidate)
                return
            blockers = [v for v in prepared.get('issues', []) if not isinstance(v, dict) or v.get('severity', 'blocker') in {'blocker', 'configuration', 'error'}]
            if blockers:
                primary = [v for v in blockers if not isinstance(v, dict) or v.get('code') != 'CAPABILITY_BLOCKED']
                if primary and all(isinstance(v, dict) and (v.get('code') in TECHNICAL_CODES or
                        (str(v.get('path', '')).startswith('nodes.') and
                         str(v['path']).endswith(('.code', '.id', '.checkpoint')))) for v in primary):
                    content = self._repair_content(owner, session, budget, candidate['content'],
                        {'issues': blockers}, context, candidate['id'])
                    previous = prepared
                    continue
                self._question(owner, session, blockers, 'input')
                return
            if plan is None:
                plan_repair_id = None
                while plan is None:
                    try:
                        plan = self.adapters.plan(owner, prepared, instruction=session['instruction'], context=context, stopped=budget)
                    except GatewayError as error:
                        if plan_repair_id:
                            self._finish_repair(owner, plan_repair_id, 'failed')
                        if getattr(error, 'code', None) not in {'TEST_PLAN_INVALID', 'TEST_FIXTURE_INVALID', 'agent_model_output_invalid'}:
                            raise
                        feedback = {'issues': [{'code': error.code, 'message': str(error)}],
                                    'invalidPlan': getattr(error, 'authoringPlan', None)}
                        evidence_id = self._technical_evidence(owner, session, feedback, candidate['id'], 'test_plan_check')
                        plan_repair_id = budget.repair(candidate['id'], [evidence_id])
                        context['planRepairFeedback'] = feedback
                    else:
                        if plan_repair_id:
                            self._finish_repair(owner, plan_repair_id, 'completed')
            if plan.get('issues'):
                self._question(owner, session, plan['issues'], 'input')
                return
            plan['contentDigest'] = prepared['contentDigest']
            plan_record = {**copy.deepcopy(plan), 'id': uid('testplan'), 'sessionId': session_id,
                           'candidateId': candidate['id'], 'createdAt': now(), 'digest': digest(plan)}
            def save_plan(current, db):
                self.store.put(db, 'test_plans', owner, plan_record)
                current.update(testPlanId=plan_record['id'], phase='validating')
            self._update(owner, session_id, revision, save_plan)
            evidence_id = uid('evidence')
            def progress(case):
                budget.check()
                self._update(owner, session_id, revision, lambda s, db: s.update(currentCase=copy.deepcopy(case)))
            try:
                actual = self.adapters.validate(owner, prepared, plan, stopped=budget, on_case=progress, context=context)
                try:
                    budget.check()
                except Exception as error:
                    error.authoringEvidence = actual
                    raise
            except Exception as error:
                error.authoringPlanId = plan_record['id']
                raise
            evidence = {**actual, 'id': evidence_id, 'sessionId': session_id, 'candidateId': candidate['id'],
                        'executionDigest': prepared['executionDigest'], 'planId': plan_record['id'],
                        'planDigest': plan_record['digest'], 'createdAt': now(), 'completedAt': now()}
            with self.store.transaction() as db:
                self.store.put(db, 'evidence', owner, evidence)
            if actual.get('status') in {'passed', 'awaiting_review'}:
                review = {'id': uid('review'), 'sessionId': session_id, 'candidateId': candidate['id'],
                          'contentDigest': prepared['contentDigest'], 'evidenceIds': [evidence_id], 'createdAt': now()}
                review['reviewDigest'] = digest({'contentDigest': prepared['contentDigest'], 'evidence': evidence,
                                                 'planDigest': plan_record['digest']})
                def ready(current, db):
                    self.store.put(db, 'review_snapshots', owner, review)
                    current.update(phase='awaiting_review', reviewSnapshotId=review['id'], currentCase=None, error=None)
                self._update(owner, session_id, revision, ready)
                return
            if actual.get('status') in {'blocked', 'awaiting_input', 'awaiting_authorization', 'unknown', 'interrupted'}:
                issues = actual.get('issues') or [{'message': '验证条件不完整，请补充必要输入或运行条件。'}]
                if actual.get('status') == 'unknown':
                    issues = [{'message': '上次调用结果未知，已停止后续验证。请先核实结果，再明确是否重新尝试；不会自动重放旧调用。'}] + issues
                self._question(owner, session, issues,
                               'authorization' if actual.get('status') == 'awaiting_authorization' else 'input')
                return
            content = self._repair_content(owner, session, budget, candidate['content'], actual,
                                           context, candidate['id'], [evidence_id], frozen_plan=plan)
            previous = prepared

    def _save_app_candidate(self, owner, session_id, revision, candidate):
        from .asset_contracts import definition
        from .asset_creator import has_concrete_app_node
        prepared = candidate['_prepared']
        material = prepared.get('material', {})
        frozen, _ = definition(material.get('definition'))
        if not has_concrete_app_node(frozen):
            fail('AUTHORING_INPUT_REQUIRED', '尚未形成具体应用实现。请补充本应用的输入、处理步骤与期望输出，或说明要复用的Skill或Tool。', 422)
        with self.store.transaction() as db:
            current = self.store.get(db, 'sessions', owner, session_id)
            if (current['revision'] != revision or current['phase'] not in WORKING or
                    current.get('currentCandidateId') != candidate['id']):
                fail('CANDIDATE_SUPERSEDED', '此处理已被新要求替代。', 409)
            target = current.get('target')
            body = {'definition': frozen, 'bindings': copy.deepcopy(material.get('runtimeBindings', {}))}
            if target:
                app = self.w.asset_apps.store.get(db, 'apps', owner, target['assetId'])
                if (app['revision'] != target['baseRevision'] or
                        app['latestVersionId'] != target['baseVersionId']):
                    fail('REVISION_CONFLICT', '正式应用已变化，请从最新版本重新修改。', 409)
                body.update(appId=target['assetId'], revision=target['baseRevision'])
            import json
            model_calls = [json.loads(row['doc']) for row in db.execute(
                'SELECT doc FROM authoring_model_requests WHERE owner=? AND session=?', (owner, session_id))]
            trace_ids = [call['traceId'] for call in model_calls if call.get('traceId') and
                         call.get('revision') == revision]
            provenance = {'sessionId': session_id, 'candidateId': candidate['id'],
                          'candidateContentDigest': candidate['contentDigest'],
                          'sourceIds': list(current.get('sourceIds', [])),
                          'modelTraceIds': list(dict.fromkeys(trace_ids))}
            result, _ = self.w.asset_apps.store.request(db, owner, 'authoring.app.save', candidate['id'],
                {'contentDigest': candidate['contentDigest'], 'target': target, 'definition': frozen},
                lambda: self.w.asset_apps._save_in_transaction(db, owner, body, authoring=provenance))
            receipt = {'id': uid('commit'), 'sessionId': session_id, 'candidateId': candidate['id'],
                       'mode': 'save_only', 'stage': 'completed', 'contentDigest': candidate['contentDigest'],
                       'targetAssetId': result['appId'], 'targetVersionId': result['versionId'],
                       'createdAt': now(), 'completedAt': now()}
            current.update(phase='completed', question=None, error=None, commitId=receipt['id'],
                           result={'assetId': result['appId'], 'versionId': result['versionId'],
                                   'verification': 'unverified', 'enabled': False})
            self.store.put(db, 'commits', owner, receipt)
            self.store.put(db, 'sessions', owner, current)

    def _technical_evidence(self, owner, session, diagnostics, candidate_id, scope):
        evidence = {'id': uid('evidence'), 'sessionId': session['id'], 'candidateId': candidate_id,
                    'revision': session['revision'], 'status': 'failed', 'scope': scope,
                    'issues': copy.deepcopy(diagnostics.get('issues', [])), 'createdAt': now(), 'completedAt': now()}
        if diagnostics.get('invalidPlan') is not None:
            evidence['invalidPlan'] = copy.deepcopy(diagnostics['invalidPlan'])
        with self.store.transaction() as db:
            self.store.put(db, 'evidence', owner, evidence)
        return evidence['id']

    def _finish_repair(self, owner, repair_id, status):
        with self.store.transaction() as db:
            repair = self.store.get(db, 'repair_rounds', owner, repair_id)
            repair.update(status=status, completedAt=now())
            self.store.put(db, 'repair_rounds', owner, repair)

    def _repair_content(self, owner, session, budget, content, diagnostics, context,
                        candidate_id=None, evidence_ids=None, frozen_plan=None):
        while True:
            evidence_ids = evidence_ids or [self._technical_evidence(owner, session, diagnostics, candidate_id, 'static_check')]
            repair_id = budget.repair(candidate_id, evidence_ids)
            self._update(owner, session['id'], session['revision'], lambda s, db: s.update(phase='repairing'))
            compact = {'issues': diagnostics.get('issues', []), 'caseResults': [
                {k: row[k] for k in ('id', 'input', 'result', 'status', 'error', 'checks') if k in row}
                for row in diagnostics.get('caseResults', [])]}
            if frozen_plan:
                compact['frozenExpectations'] = [{'id': case.get('id'), 'checks': case.get('checks')}
                                                  for case in frozen_plan.get('cases', [])]
            explanation = ('冻结的独立预期（只读）：' + canonical(compact.get('frozenExpectations', []))[:3800] +
                           '\n技术问题：' + canonical(compact['issues'])[:1000] + '\n实际检查与输出（有界预览）：' +
                           canonical([{k: canonical(value)[:700] if k in {'result', 'input'} else value
                                       for k, value in row.items()} for row in compact['caseResults']])[:2800])
            feedback = {'diagnostics': explanation, 'planFrozen': frozen_plan is not None,
                        'constraint': '仅修复技术缺陷；不得更改算法、业务口径、交付标准或冻结的测试预期。'}
            repair_context = {**copy.deepcopy(context), 'technicalRepairFeedback': feedback}
            try:
                revised = self.adapters.create(owner, session['kind'], session['instruction'], content=content,
                                               context=repair_context, stopped=budget)
            except Exception as error:
                self._finish_repair(owner, repair_id, 'failed')
                if getattr(error, 'code', None) not in TECHNICAL_CODES:
                    raise
                diagnostics = {'issues': [{'code': error.code, 'message': str(error)}]}
                evidence_ids = None
                continue
            self._finish_repair(owner, repair_id, 'completed')
            return revised

    def _question(self, owner, session, issues, kind):
        prompt = '\n'.join(str(issue.get('message') or issue) if isinstance(issue, dict) else str(issue) for issue in issues)
        def change(current, db):
            current.update(phase='awaiting_authorization' if kind == 'authorization' else 'awaiting_input',
                           question={'id': uid('question'), 'kind': kind, 'prompt': prompt[:6000], 'revision': current['revision']},
                           issues=copy.deepcopy(issues))
        self._update(owner, session['id'], session['revision'], change)

    def confirm(self, owner, session_id, body, key):
        mode = body.get('mode')
        if mode not in {'draft_only', 'save_and_enable'}:
            fail('INVALID_REQUEST', '请选择只存草稿或确认保存并使用。')
        if self.store.read('sessions', owner, session_id)['kind'] == 'app' and mode == 'save_and_enable':
            fail('VERIFICATION_REQUIRED', 'APP 已改为先保存未验证版本；请在已保存应用详情中独立验证并启用。', 409)
        with self.lock, self.store.transaction() as db:
            def reserve():
                session = self.store.get(db, 'sessions', owner, session_id)
                self._revision(session, body)
                if session['phase'] == 'committing':
                    fail('COMMIT_IN_PROGRESS', '正在保存，请等待结果。', 409)
                if session.get('result'):
                    fail('INVALID_STATE', '该候选已提交，请从资产发起新修改。', 409)
                candidate = self.store.get(db, 'candidates', owner, session['currentCandidateId']) if session.get('currentCandidateId') else None
                review = self.store.get(db, 'review_snapshots', owner, session['reviewSnapshotId']) if session.get('reviewSnapshotId') else None
                if mode == 'save_and_enable':
                    if session['phase'] != 'awaiting_review' or not review or not candidate:
                        fail('VERIFICATION_REQUIRED', '请完成当前候选验证后再确认使用。', 409)
                    if (body.get('candidateId') != candidate['id'] or body.get('contentDigest') != candidate['contentDigest'] or
                            body.get('reviewSnapshotId') != review['id'] or body.get('reviewDigest') != review['reviewDigest']):
                        fail('REVIEW_STALE', '审阅对象已改变，请核对最新结果。', 409)
                    if session.get('target') and body.get('baseRevision') != session['target']['baseRevision']:
                        fail('REVISION_CONFLICT', '资产基础版本不一致。', 409)
                commit = {'id': uid('commit'), 'sessionId': session_id, 'candidateId': candidate['id'] if candidate else None,
                          'contentDigest': candidate['contentDigest'] if candidate else None,
                          'reviewDigest': review['reviewDigest'] if review else None, 'mode': mode,
                          'baseTarget': session.get('target'), 'stage': 'reserved', 'createdAt': now()}
                commit.update(reviewSnapshotId=review['id'] if review else None,
                              evidenceIds=review['evidenceIds'] if review else [])
                if mode == 'draft_only':
                    commit['stage'] = 'completed'
                    session.update(phase='completed', mode='draft_only', question=None, reviewSnapshotId=None)
                else:
                    session['phase'] = 'committing'
                session['revision'] += 1
                session['commitId'] = commit['id']
                self.store.put(db, 'commits', owner, commit)
                self.store.put(db, 'sessions', owner, session)
                return {'sessionId': session_id, 'commitId': commit['id']}
            reserved, fresh = self.store.request(db, owner, session_id + ':confirm', key, body, reserve)
        if mode == 'draft_only':
            if fresh:
                self._stop(owner, session_id)
            return self.detail(owner, session_id)
        commit = self.store.read('commits', owner, reserved['commitId'])
        if commit['stage'] == 'completed':
            return self.detail(owner, session_id)
        # Serialize retries; adapter commits use this stable identity in their
        # own store. A lost cross-store response can therefore be reconciled.
        with self.lock:
            session = self.store.read('sessions', owner, session_id)
            if (session.get('commitId') != commit['id'] or session.get('currentCandidateId') != commit['candidateId'] or
                    session.get('reviewSnapshotId') != commit['reviewSnapshotId']):
                fail('REVIEW_STALE', '原提交已被新要求替代，不能用旧确认保存。', 409)
            candidate = self.store.read('candidates', owner, commit['candidateId'])
            review = self.store.read('review_snapshots', owner, commit['reviewSnapshotId'])
            evidence = self.store.read('evidence', owner, commit['evidenceIds'][0])
            try:
                result = self.adapters.commit(owner, candidate['_prepared'], evidence, key=commit['id'],
                                              target=commit.get('baseTarget'), review={'accepted': True, 'reviewDigest': commit['reviewDigest']})
                with self.store.transaction() as db:
                    current = self.store.get(db, 'sessions', owner, session_id)
                    commit.update(stage='completed', lastCompletedStage='enabled', targetAssetId=result['assetId'],
                                  targetVersionId=result['versionId'], completedAt=now(), error=None)
                    current.update(phase='completed', result=result, error=None)
                    self.store.put(db, 'commits', owner, commit)
                    self.store.put(db, 'sessions', owner, current)
            except Exception as error:
                with self.store.transaction() as db:
                    current = self.store.get(db, 'sessions', owner, session_id)
                    commit.update(stage='failed', error={'code': getattr(error, 'code', None) or 'COMMIT_FAILED',
                                  'message': str(error) if isinstance(error, GatewayError) else '保存结果需要核实，请使用原请求重试。'})
                    current.update(phase='failed' if isinstance(error, GatewayError) else 'committing', error=commit['error'])
                    self.store.put(db, 'commits', owner, commit)
                    self.store.put(db, 'sessions', owner, current)
                raise
        return self.detail(owner, session_id)
