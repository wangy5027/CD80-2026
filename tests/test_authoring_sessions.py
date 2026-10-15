"""Conversation lifecycle tests with deterministic adapters, no external models."""
import copy
import io
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from backend.app import create_app
from backend.app.modules.agent_platform.authoring_budget import current_budget
from backend.app.modules.agent_platform.authoring_store import AuthoringStore
from backend.app.modules.agent_platform.authoring_chat import handoff, invoke_app
from backend.app.modules.capability_library.contracts import CapabilityError
from backend.app.modules.capability_library.contracts import digest, fail, now, uid

BASE = '/desktop/api/authoring-sessions'


class FakeAdapters:
    """Records observable calls; tests control completion and objective outcomes."""
    def __init__(self):
        self.calls, self.statuses, self.commits = [], [], {}
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.gate = None
        self.create_failure = False
        self.commit_failure = None

    def _call(self, stage, data):
        self.calls.append((stage, copy.deepcopy(data)))
        if self.gate == stage:
            self.gate = None
            self.entered.set()
            if not self.release.wait(5):
                raise AssertionError('test failed to release controlled adapter')

    def create(self, owner, kind, instruction, *, content=None, context=None, stopped=None):
        budget = current_budget()
        request_id = budget.begin_request('fixture', 'deterministic') if budget else None
        self._call('create', {'owner': owner, 'kind': kind, 'instruction': instruction, 'context': context})
        if self.create_failure:
            self.create_failure = False
            if budget:
                budget.finish_request(request_id)
            fail('MODEL_UNAVAILABLE', 'Fixture model failed once.', 502)
        if budget:
            budget.finish_request(request_id, succeeded=True)
        return {'name': kind + '-fixture', 'instruction': instruction, 'value': len(self.calls)}

    def prepare(self, owner, kind, content, *, previous=None, bindings=None):
        self._call('prepare', content)
        return {'kind': kind, 'content': copy.deepcopy(content), 'contentDigest': digest(content),
                'executionDigest': digest(content), 'runtimeContextDigest': 'fixture-environment',
                'changeClass': 'behavior', 'issues': []}

    def plan(self, owner, prepared, *, instruction, context, stopped):
        self._call('plan', instruction)
        return {'cases': [{'id': 'original', 'input': {'n': 1}, 'expectation': {'value': 2}},
                          {'id': 'new', 'input': {'n': 4}, 'expectation': {'value': 8}}], 'issues': []}

    def validate(self, owner, prepared, plan, *, stopped, on_case, context):
        self._call('validate', {'content': prepared['content'], 'plan': plan})
        stopped.check()
        for case in plan['cases']:
            on_case({'caseId': case['id'], 'status': 'completed'})
        status = self.statuses.pop(0) if self.statuses else 'passed'
        return {'id': uid('adapter-evidence'), 'status': status, 'contentDigest': prepared['contentDigest'],
                'runtimeContextDigest': prepared['runtimeContextDigest'], 'cases': copy.deepcopy(plan['cases']),
                'issues': [{'message': 'Fixture needs an authorized connection.'}] if status == 'awaiting_authorization' else []}

    def commit(self, owner, prepared, evidence, *, key, target=None, review=None):
        self._call('commit', {'key': key, 'content': prepared['content'], 'evidence': evidence, 'review': review})
        if self.commit_failure:
            code = self.commit_failure
            self.commit_failure = None
            fail(code, 'Fixture commit did not finish.', 409)
        if key not in self.commits:
            self.commits[key] = {'assetId': uid('saved'), 'versionId': uid('version'), 'enabled': True}
        return self.commits[key]

    def count(self, stage):
        return sum(name == stage for name, _ in self.calls)


class AuthoringSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-authoring-sessions-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                               'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.service = self.w.authoring
        self.fake = FakeAdapters()
        self.service.adapters = self.fake
        self.client, self.other = self.app.test_client(), self.app.test_client()
        for number, client in enumerate((self.client, self.other)):
            self.assertEqual(client.post('/desktop/api/register', json={
                'email': f'authoring{number}@example.invalid', 'password': 'test-password'}).status_code, 200)
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.counter = 0

    def tearDown(self):
        self.fake.release.set()
        self.w.close()
        self.temp.cleanup()

    def post(self, path='', body=None, key=None, client=None):
        self.counter += 1
        return (client or self.client).post(BASE + path, json=body or {},
                                           headers={'Idempotency-Key': key or str(self.counter)})

    def create(self, **changes):
        response = self.post(body={'kind': 'tool', 'intent': 'create', 'instruction': 'Double the input value.', **changes})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json['session']['id']

    def wait(self, sid, phase=None):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            result = self.client.get(BASE + '/' + sid)
            self.assertEqual(result.status_code, 200, result.json)
            data = result.json
            with self.service.lock:
                active = (self.owner, sid) in self.service.active
            if not active and (phase is None or data['session']['phase'] == phase):
                return data
            time.sleep(.01)
        self.fail('Authoring did not reach expected state: ' + str(data))

    @staticmethod
    def confirmation(data):
        return {'revision': data['session']['revision'], 'mode': 'save_and_enable',
                'candidateId': data['candidate']['id'], 'contentDigest': data['candidate']['contentDigest'],
                'reviewSnapshotId': data['reviewSnapshot']['id'], 'reviewDigest': data['reviewSnapshot']['reviewDigest']}

    def test_tool_and_skill_prepare_review_once_and_confirm_without_extra_execution(self):
        for kind in ('tool', 'skill'):
            with self.subTest(kind=kind):
                if kind == 'skill':
                    self.fake.statuses = ['awaiting_review']
                sid = self.create(kind=kind)
                data = self.wait(sid, 'awaiting_review')
                self.assertEqual(len(data['testPlan']['cases']), 2)
                self.assertTrue(data['session']['draftSaved'])
                before = (self.fake.count('create'), self.fake.count('validate'))
                payload = self.confirmation(data)
                response = self.post('/' + sid + '/confirm', payload, key=kind + '-confirm')
                self.assertEqual(response.status_code, 200, response.json)
                self.assertEqual(response.json['session']['phase'], 'completed')
                self.assertEqual(before, (self.fake.count('create'), self.fake.count('validate')))
                replay = self.post('/' + sid + '/confirm', payload, key=kind + '-confirm')
                self.assertEqual(replay.status_code, 200, replay.json)
                self.assertEqual(response.json['session']['result'], replay.json['session']['result'])
        self.assertEqual(self.fake.count('commit'), 2)
        self.assertEqual(len(self.fake.commits), 2)

    def test_draft_only_never_calls_adapters_and_reads_never_resume_work(self):
        sid = self.create(mode='draft_only')
        data = self.wait(sid, 'completed')
        self.assertIsNone(data['candidate'])
        self.assertEqual(data['budget']['consumed']['modelRequests'], 0)
        for _ in range(3):
            self.assertEqual(self.client.get(BASE + '/' + sid).status_code, 200)
            self.assertEqual(self.client.get(BASE).status_code, 200)
        self.assertEqual(self.fake.calls, [])
        self.assertIn('Double the input', data['session']['instruction'])
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])

    def test_owner_scope_idempotency_conflict_and_private_context(self):
        body = {'kind': 'tool', 'instruction': 'Only owner context.', 'mode': 'draft_only'}
        first = self.post(body=body, key='create-once')
        sid = first.json['session']['id']
        self.assertEqual(self.post(body=body, key='create-once').json['session']['id'], sid)
        self.assertEqual(self.post(body={**body, 'instruction': 'different'}, key='create-once').status_code, 409)
        other = self.post(body=body, key='create-once', client=self.other)
        self.assertNotEqual(other.json['session']['id'], sid)
        self.assertEqual(self.other.get(BASE + '/' + sid).status_code, 404)
        self.assertEqual(self.post('/' + sid + '/cancel', {'revision': 1}, client=self.other).status_code, 404)
        self.assertNotIn('owner', first.json['session'])
        self.assertFalse(any(key.startswith('_') for key in first.json['session']))
        self.assertEqual(len(self.client.get(BASE).json['items']), 1)
        self.assertEqual(self.app.test_client().get(BASE).status_code, 401)

    def test_wrong_review_or_revision_cannot_enable_candidate(self):
        sid = self.create()
        data = self.wait(sid, 'awaiting_review')
        payload = self.confirmation(data)
        for field in ('candidateId', 'contentDigest', 'reviewSnapshotId', 'reviewDigest'):
            response = self.post('/' + sid + '/confirm', {**payload, field: 'stale'})
            self.assertEqual(response.status_code, 409, response.json)
        response = self.post('/' + sid + '/confirm', {**payload, 'revision': payload['revision'] - 1})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.fake.count('commit'), 0)

    def test_new_requirement_supersedes_late_generation_without_resetting_budget(self):
        self.fake.gate = 'create'
        self.fake.release.clear()
        sid = self.create()
        self.assertTrue(self.fake.entered.wait(2))
        old = self.client.get(BASE + '/' + sid).json
        response = self.post('/' + sid + '/messages', {'revision': old['session']['revision'],
                                                       'instruction': 'Also return the input label.'}, key='append')
        self.assertEqual(response.status_code, 202, response.json)
        self.fake.release.set()
        latest = self.wait(sid, 'awaiting_review')
        self.assertEqual(latest['session']['id'], sid)
        self.assertIn('input label', latest['candidate']['content']['instruction'])
        self.assertEqual(latest['budget']['id'], old['budget']['id'])
        self.assertEqual(latest['budget']['consumed']['modelRequests'], 2)
        self.assertEqual(self.fake.count('validate'), 1)
        self.assertEqual(self.post('/' + sid + '/messages', {'revision': old['session']['revision'],
                              'instruction': 'Also return the input label.'}, key='append').status_code, 202)
        self.assertEqual(self.fake.count('create'), 2)

    def test_cancel_propagates_and_late_validation_cannot_be_reviewed(self):
        self.fake.gate = 'validate'
        self.fake.release.clear()
        sid = self.create()
        self.assertTrue(self.fake.entered.wait(2))
        old = self.client.get(BASE + '/' + sid).json
        self.assertIsNotNone(old['candidate'])
        response = self.post('/' + sid + '/cancel', {'revision': old['session']['revision']})
        self.assertEqual(response.status_code, 202, response.json)
        self.fake.release.set()
        latest = self.wait(sid, 'cancelled')
        self.assertIsNone(latest['reviewSnapshot'])
        self.assertEqual(latest['candidate']['id'], old['candidate']['id'])
        self.assertNotIn('save_and_enable', latest['session']['allowedActions'])
        self.assertEqual(self.fake.count('commit'), 0)

    def test_two_repairs_can_finish_but_third_requires_budget_extension(self):
        self.fake.statuses = ['failed', 'failed', 'passed']
        sid = self.create()
        success = self.wait(sid, 'awaiting_review')
        self.assertEqual(success['budget']['consumed']['repairRounds'], 2)
        self.assertEqual(self.fake.count('create'), 3)
        validated = [item['plan']['cases'] for stage, item in self.fake.calls if stage == 'validate']
        self.assertEqual(validated, [validated[0]] * 3, 'execution failures changed the frozen test expectations')
        self.assertEqual(self.fake.count('plan'), 1)
        generated = [item for stage, item in self.fake.calls if stage == 'create']
        self.assertTrue(all(item['instruction'] == generated[0]['instruction'] for item in generated))
        self.assertTrue(all(item['context']['technicalRepairFeedback']['planFrozen'] for item in generated[1:]))
        self.fake.statuses = ['failed', 'failed', 'failed']
        failed_sid = self.create()
        paused = self.wait(failed_sid, 'budget_paused')
        self.assertEqual(paused['budget']['consumed']['repairRounds'], 2)
        self.assertEqual(len(paused['evidence']), 3)
        self.assertIsNone(paused['reviewSnapshot'])
        self.assertEqual(self.fake.count('create'), 6)
        bad = self.post('/' + failed_sid + '/continue', {'revision': paused['session']['revision'],
            'budgetRoundId': paused['budget']['id'], 'mode': 'extend_budget', 'confirmationId': 'wrong'})
        self.assertEqual(bad.status_code, 409)

    def test_static_and_plan_format_repairs_share_limit_with_execution_repairs(self):
        original_prepare, original_plan = self.fake.prepare, self.fake.plan
        preparation_count, plan_contexts = [0], []

        def prepare(*args, **kwargs):
            prepared = original_prepare(*args, **kwargs)
            preparation_count[0] += 1
            if preparation_count[0] == 1:
                prepared['issues'] = [{'path': 'nodes.0.code', 'message': 'fixture invalid source indentation'}]
            return prepared

        def plan(*args, **kwargs):
            plan_contexts.append(copy.deepcopy(kwargs['context']))
            result = original_plan(*args, **kwargs)
            if len(plan_contexts) == 1:
                error = CapabilityError('TEST_PLAN_INVALID', 'fixture test expectation shape invalid')
                error.authoringPlan = {'cases': 'not an array'}
                raise error
            return result

        self.fake.statuses = ['failed']
        with patch.object(self.fake, 'prepare', side_effect=prepare), patch.object(self.fake, 'plan', side_effect=plan):
            sid = self.create()
            paused = self.wait(sid, 'budget_paused')
        self.assertEqual(paused['budget']['consumed']['repairRounds'], 2)
        self.assertEqual(self.fake.count('create'), 2, 'a third repair bypassed the shared allowance')
        self.assertEqual(self.fake.count('plan'), 2)
        self.assertEqual(self.fake.count('validate'), 1)
        self.assertEqual(len(paused['evidence']), 3)
        self.assertEqual({row.get('scope') for row in paused['evidence']}, {'static_check', 'test_plan_check', None})
        plan_failure = next(row for row in paused['evidence'] if row.get('scope') == 'test_plan_check')
        self.assertEqual(plan_failure['invalidPlan'], {'cases': 'not an array'})
        self.assertEqual(plan_contexts[1]['planRepairFeedback']['invalidPlan'], {'cases': 'not an array'})
        self.assertNotIn('caseResults', plan_contexts[1]['planRepairFeedback'])
        self.assertIsNone(paused['reviewSnapshot'])

    def test_execution_repair_receives_actual_shape_and_frozen_expectations(self):
        original_plan, original_validate = self.fake.plan, self.fake.validate

        def plan(*args, **kwargs):
            value = original_plan(*args, **kwargs)
            for case in value['cases']:
                case['checks'] = [{'type': 'json_equals', 'expected': case['expectation']}]
            return value

        def validate(*args, **kwargs):
            value = original_validate(*args, **kwargs)
            if value['status'] == 'failed':
                value['caseResults'] = [{'id': 'original', 'input': {'n': 1}, 'status': 'failed',
                    'result': {'result': {'value': 2}}, 'checks': [{'passed': False}]},
                    {'id': 'new', 'input': {'large': 'i' * 50000}, 'status': 'failed',
                     'result': {'large': 'r' * 50000}, 'checks': [{'passed': False}]}]
            return value

        self.fake.statuses = ['failed', 'passed']
        with patch.object(self.fake, 'plan', side_effect=plan), patch.object(self.fake, 'validate', side_effect=validate):
            sid = self.create()
            ready = self.wait(sid, 'awaiting_review')
        generated = [item for stage, item in self.fake.calls if stage == 'create']
        self.assertEqual(len(generated), 2)
        self.assertEqual(generated[0]['instruction'], generated[1]['instruction'])
        feedback = generated[1]['context']['technicalRepairFeedback']
        self.assertTrue(feedback['planFrozen'])
        diagnostics = feedback['diagnostics']
        self.assertLess(len(diagnostics), 8000)
        expectations = json.loads(diagnostics.splitlines()[0].split('：', 1)[1])
        actual = json.loads(diagnostics.split('实际检查与输出（有界预览）：', 1)[1])
        self.assertEqual(json.loads(actual[0]['result']), {'result': {'value': 2}})
        self.assertEqual(expectations[0]['checks'],
                         [{'type': 'json_equals', 'expected': {'value': 2}}])
        self.assertEqual(expectations[1]['checks'],
                         [{'type': 'json_equals', 'expected': {'value': 8}}])
        self.assertLessEqual(len(actual[1]['result']), 700)
        failed = next(row for row in ready['evidence'] if row['status'] == 'failed')
        self.assertEqual(len(failed['caseResults'][1]['result']['large']), 50000,
                         'prompt truncation must not discard stored execution evidence')
        verified = [item['plan']['cases'] for stage, item in self.fake.calls if stage == 'validate']
        self.assertEqual(verified, [ready['testPlan']['cases']] * 2)
        self.assertEqual(self.fake.count('plan'), 1)
        self.assertEqual(ready['budget']['consumed']['repairRounds'], 1)

    def test_repeated_generated_format_errors_stop_after_two_repairs_and_keep_facts(self):
        original_create = self.fake.create

        def invalid(*args, **kwargs):
            original_create(*args, **kwargs)
            raise CapabilityError('INVALID_FILES', 'fixture absolute resource path')

        with patch.object(self.fake, 'create', side_effect=invalid):
            sid = self.create()
            paused = self.wait(sid, 'budget_paused')
        self.assertEqual(self.fake.count('create'), 3)
        self.assertEqual(paused['budget']['consumed']['modelRequests'], 3)
        self.assertEqual(paused['budget']['consumed']['repairRounds'], 2)
        self.assertEqual(len(paused['evidence']), 3)
        self.assertTrue(all(row['issues'][0]['code'] == 'INVALID_FILES' for row in paused['evidence']))
        self.assertEqual(self.fake.count('validate'), 0)
        self.assertTrue(paused['session']['draftSaved'])

    def test_business_gaps_and_authorization_issues_are_never_automatically_rewritten(self):
        original_prepare = self.fake.prepare
        for blocker in ({'path': 'gaps', 'message': 'Choose the business accounting rule.'},
                        {'code': 'MISSING_CONNECTION', 'message': 'Select an authorized connection.'},
                        {'code': 'FILE_NOT_GRANTED', 'message': 'Choose the required file.'},
                        {'code': 'CAPABILITY_BLOCKED', 'message': 'Runtime permission has not been granted.'}):
            with self.subTest(blocker=blocker):
                before = (self.fake.count('create'), self.fake.count('plan'), self.fake.count('validate'))

                def prepare(*args, **kwargs):
                    result = original_prepare(*args, **kwargs)
                    result['issues'] = ([blocker] if blocker.get('code') == 'CAPABILITY_BLOCKED' else
                        [{'path': 'nodes.0.code', 'message': 'also needs a technical fix'}, blocker])
                    return result

                with patch.object(self.fake, 'prepare', side_effect=prepare):
                    sid = self.create()
                    waiting = self.wait(sid, 'awaiting_input')
                self.assertEqual(waiting['budget']['consumed']['repairRounds'], 0)
                self.assertEqual(self.fake.count('create'), before[0] + 1)
                self.assertEqual(self.fake.count('plan'), before[1])
                self.assertEqual(self.fake.count('validate'), before[2])
                self.assertIn(blocker['message'], waiting['session']['question']['prompt'])

    def test_cancel_keeps_partial_evidence_bound_to_the_original_candidate(self):
        self.fake.release.clear()

        def partial(owner, prepared, plan, *, stopped, on_case, context):
            self.fake._call('validate', {'content': prepared['content'], 'plan': plan})
            completed = {'id': plan['cases'][0]['id'], 'status': 'passed', 'result': {'value': 2}}
            on_case(completed)
            self.fake.entered.set()
            if not self.fake.release.wait(5):
                raise AssertionError('test did not release partial validation')
            try:
                stopped.check()
            except Exception as error:
                error.authoringEvidence = {'id': 'adapter-partial', 'adapterEvidenceId': 'adapter-partial',
                    'status': 'cancelled', 'cases': [completed], 'caseResults': [completed],
                    'contentDigest': prepared['contentDigest'], 'issues': [{'message': 'Stopped after first case.'}]}
                raise
            raise AssertionError('cancel was not propagated to the candidate')

        with patch.object(self.fake, 'validate', side_effect=partial):
            sid = self.create()
            self.assertTrue(self.fake.entered.wait(2))
            running = self.client.get(BASE + '/' + sid).json
            cancelled = self.post('/' + sid + '/cancel', {'revision': running['session']['revision']})
            self.assertEqual(cancelled.status_code, 202, cancelled.json)
            self.fake.release.set()
            result = self.wait(sid, 'cancelled')
        self.assertEqual(len(result['evidence']), 1)
        evidence = result['evidence'][0]
        self.assertEqual(evidence['status'], 'interrupted')
        self.assertEqual(evidence['candidateId'], running['candidate']['id'])
        self.assertEqual(evidence['planId'], running['testPlan']['id'])
        self.assertEqual(evidence['adapterEvidenceId'], 'adapter-partial')
        self.assertEqual(evidence['cases'], [{'id': 'original', 'status': 'passed', 'result': {'value': 2}}])
        self.assertIsNone(result['reviewSnapshot'])
        self.assertEqual(self.fake.count('commit'), 0)

    def test_failed_work_resume_keeps_allowance_and_confirm_draft_never_executes(self):
        self.fake.create_failure = True
        sid = self.create()
        failed = self.wait(sid, 'failed')
        self.assertTrue(failed['session']['draftSaved'])
        self.assertEqual(failed['budget']['consumed']['modelRequests'], 1)
        response = self.post('/' + sid + '/continue', {'revision': failed['session']['revision'],
            'budgetRoundId': failed['budget']['id'], 'mode': 'resume'})
        self.assertEqual(response.status_code, 202, response.json)
        ready = self.wait(sid, 'awaiting_review')
        self.assertEqual(ready['budget']['id'], failed['budget']['id'])
        self.assertEqual(ready['budget']['consumed']['modelRequests'], 2)
        before = len(self.fake.calls)
        saved = self.post('/' + sid + '/confirm', {'revision': ready['session']['revision'], 'mode': 'draft_only'})
        self.assertEqual(saved.status_code, 200, saved.json)
        self.assertEqual(len(self.fake.calls), before)
        self.assertTrue(saved.json['session']['draftSaved'])

    def test_exhausted_resume_cannot_add_budget_but_confirmed_extension_is_idempotent(self):
        sid = self.create(mode='draft_only')
        draft = self.client.get(BASE + '/' + sid).json
        with self.service.store.transaction() as db:
            budget = self.service.store.get(db, 'budget_rounds', self.owner, draft['budget']['id'])
            budget['consumed']['modelRequests'] = 32
            self.service.store.put(db, 'budget_rounds', self.owner, budget)
        started = self.post('/' + sid + '/messages', {'revision': draft['session']['revision'],
                            'instruction': 'Prepare the saved requirements now.'})
        self.assertEqual(started.status_code, 202)
        paused = self.wait(sid, 'budget_paused')
        resumed = self.post('/' + sid + '/continue', {'revision': paused['session']['revision'],
            'budgetRoundId': paused['budget']['id'], 'mode': 'resume'})
        self.assertEqual(resumed.status_code, 202)
        paused_again = self.wait(sid, 'budget_paused')
        self.assertEqual(paused_again['budget']['id'], paused['budget']['id'])
        self.assertEqual(paused_again['budget']['consumed']['modelRequests'], 32)
        payload = {'revision': paused_again['session']['revision'], 'budgetRoundId': paused_again['budget']['id'],
                   'mode': 'extend_budget', 'confirmationId': paused_again['budget']['confirmationId']}
        extended = self.post('/' + sid + '/continue', payload, key='explicit-extension')
        self.assertEqual(extended.status_code, 202, extended.json)
        ready = self.wait(sid, 'awaiting_review')
        self.assertNotEqual(ready['budget']['id'], paused['budget']['id'])
        self.assertEqual(ready['budget']['number'], 2)
        self.assertEqual(ready['budget']['consumed']['modelRequests'], 1)
        self.assertEqual(ready['budget']['lifetimeConsumed']['modelRequests'], 33)
        self.assertEqual(self.post('/' + sid + '/continue', payload, key='explicit-extension').status_code, 202)
        self.assertEqual(len(self.service.store.listing('budget_rounds', self.owner, sid)), 2)
        self.assertEqual(self.fake.count('create'), 1)

    def test_authorization_question_binds_revision_and_does_not_add_budget(self):
        self.fake.statuses = ['awaiting_authorization']
        sid = self.create()
        waiting = self.wait(sid, 'awaiting_authorization')
        question = waiting['session']['question']
        self.assertIsNotNone(question)
        self.assertEqual(self.post('/' + sid + '/answers', {'revision': waiting['session']['revision'],
                          'questionId': 'stale', 'answer': 'Use my connection.'}).status_code, 409)
        answer = self.post('/' + sid + '/answers', {'revision': waiting['session']['revision'],
                          'questionId': question['id'], 'answer': 'Use my existing connection.'})
        self.assertEqual(answer.status_code, 202, answer.json)
        ready = self.wait(sid, 'awaiting_review')
        self.assertEqual(ready['budget']['id'], waiting['budget']['id'])
        self.assertEqual(ready['budget']['consumed']['modelRequests'], 2)

    def test_unknown_requests_require_exact_acknowledgment_and_keep_consumption(self):
        sid = self.create(mode='draft_only')
        with self.service.store.transaction() as db:
            session = self.service.store.get(db, 'sessions', self.owner, sid)
            session.update(phase='budget_paused', _budgetConfirmationId='current-budget-confirmation')
            self.service.store.put(db, 'sessions', self.owner, session)
            budget = self.service.store.get(db, 'budget_rounds', self.owner, session['budgetRoundId'])
            budget['consumed']['modelRequests'] = 32
            self.service.store.put(db, 'budget_rounds', self.owner, budget)
            self.service.store.put(db, 'model_requests', self.owner, {'id': 'unfinished-model-request',
                'sessionId': sid, 'budgetRoundId': budget['id'], 'state': 'unknown', 'sentAt': now()})
        paused = self.client.get(BASE + '/' + sid).json
        self.assertEqual([r['id'] for r in paused['budget']['unknownRequests']], ['unfinished-model-request'])
        payload = {'revision': paused['session']['revision'], 'budgetRoundId': paused['budget']['id'],
                   'mode': 'extend_budget', 'confirmationId': paused['budget']['confirmationId']}
        for wrong in ([], ['unrelated-request'], ['unfinished-model-request', 'unrelated-request']):
            refused = self.post('/' + sid + '/continue', {**payload, 'acknowledgeUnknownRequests': wrong})
            self.assertEqual(refused.status_code, 409, refused.json)
            self.assertEqual(len(self.service.store.listing('budget_rounds', self.owner, sid)), 1)
        self.assertEqual(self.fake.calls, [])
        self.fake.create_failure = True
        payload['acknowledgeUnknownRequests'] = ['unfinished-model-request']
        started = self.post('/' + sid + '/continue', payload, key='acknowledge-and-extend')
        self.assertEqual(started.status_code, 202, started.json)
        failed = self.wait(sid, 'failed')
        self.assertEqual(failed['budget']['number'], 2)
        self.assertEqual(failed['budget']['lifetimeConsumed']['modelRequests'], 33)
        self.assertEqual(failed['budget']['unknownRequests'], [])
        uncertain = self.service.store.read('model_requests', self.owner, 'unfinished-model-request')
        self.assertEqual(uncertain['state'], 'unknown', 'acknowledgment fabricated a model outcome')
        self.assertTrue(uncertain.get('acknowledgedAt'))
        resumed = self.post('/' + sid + '/continue', {'revision': failed['session']['revision'],
            'budgetRoundId': failed['budget']['id'], 'mode': 'resume'})
        self.assertEqual(resumed.status_code, 202, resumed.json)
        ready = self.wait(sid, 'awaiting_review')
        self.assertEqual(ready['budget']['id'], failed['budget']['id'])
        self.assertEqual(ready['budget']['lifetimeConsumed']['modelRequests'], 34)
        before = self.fake.count('create')
        self.assertEqual(self.post('/' + sid + '/continue', payload, key='acknowledge-and-extend').status_code, 202)
        self.assertEqual(self.fake.count('create'), before)
        self.assertEqual(len(self.service.store.listing('budget_rounds', self.owner, sid)), 2)

    def test_old_failed_confirmation_cannot_commit_after_new_candidate(self):
        sid = self.create()
        old = self.wait(sid, 'awaiting_review')
        payload = self.confirmation(old)
        self.fake.commit_failure = 'STALE_CONFIGURATION'
        self.assertEqual(self.post('/' + sid + '/confirm', payload, key='old-review').status_code, 409)
        failed = self.client.get(BASE + '/' + sid).json
        changed = self.post('/' + sid + '/messages', {'revision': failed['session']['revision'],
                             'instruction': 'Instead calculate a cumulative sum.'})
        self.assertEqual(changed.status_code, 202, changed.json)
        latest = self.wait(sid, 'awaiting_review')
        before = self.fake.count('commit')
        old_retry = self.post('/' + sid + '/confirm', payload, key='old-review')
        self.assertEqual(old_retry.status_code, 409, old_retry.json)
        self.assertEqual(self.fake.count('commit'), before)
        result = self.post('/' + sid + '/confirm', self.confirmation(latest), key='current-review')
        self.assertEqual(result.status_code, 200, result.json)

    def test_restart_keeps_unknown_commit_locked_until_exact_request_recovers(self):
        sid = self.create()
        ready = self.wait(sid, 'awaiting_review')
        payload = self.confirmation(ready)
        original_commit = self.fake.commit

        def save_then_lose_response(*args, **kwargs):
            original_commit(*args, **kwargs)
            raise RuntimeError('fixture lost coordinator response after atomic asset save')

        with patch.object(self.fake, 'commit', side_effect=save_then_lose_response):
            with self.assertLogs(self.app.logger, level='ERROR'):
                lost = self.post('/' + sid + '/confirm', payload, key='lost-save-response')
            self.assertEqual(lost.status_code, 500)
        self.assertEqual(len(self.fake.commits), 1)
        AuthoringStore(self.w.platform.repository)
        recovered = self.client.get(BASE + '/' + sid).json
        before = len(self.fake.calls)
        for operation, body in (
            ('messages', {'instruction': 'Create a different asset instead.'}),
            ('cancel', {}),
            ('continue', {'mode': 'resume', 'budgetRoundId': recovered['budget']['id']}),
        ):
            response = self.post('/' + sid + '/' + operation,
                                 {'revision': recovered['session']['revision'], **body})
            self.assertEqual(response.status_code, 409, (operation, response.json))
        self.assertEqual(len(self.fake.calls), before, 'restart replayed or changed the unresolved commit')
        result = self.post('/' + sid + '/confirm', payload, key='lost-save-response')
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['session']['phase'], 'completed')
        self.assertEqual(len(self.fake.commits), 1)
        self.assertEqual(result.json['session']['result'], next(iter(self.fake.commits.values())))

    def test_restart_preserves_candidates_and_budget_without_dispatch(self):
        sid = self.create()
        data = self.wait(sid, 'awaiting_review')
        with self.service.store.transaction() as db:
            session = self.service.store.get(db, 'sessions', self.owner, sid)
            session['phase'] = 'validating'
            self.service.store.put(db, 'sessions', self.owner, session)
            budget = self.service.store.get(db, 'budget_rounds', self.owner, session['budgetRoundId'])
            budget['activeSince'] = now()
            self.service.store.put(db, 'budget_rounds', self.owner, budget)
            self.service.store.put(db, 'model_requests', self.owner, {'id': 'request-before-crash',
                'sessionId': sid, 'budgetRoundId': budget['id'], 'state': 'sent'})
        before = len(self.fake.calls)
        AuthoringStore(self.w.platform.repository)
        recovered = self.client.get(BASE + '/' + sid).json
        self.assertEqual(recovered['session']['phase'], 'interrupted')
        self.assertEqual(recovered['candidate']['id'], data['candidate']['id'])
        self.assertEqual(recovered['budget']['consumed']['modelRequests'], data['budget']['consumed']['modelRequests'])
        self.assertEqual(recovered['budget']['consumed']['activeMilliseconds'], 600000)
        self.assertEqual(len(self.fake.calls), before)
        response = self.post('/' + sid + '/continue', {'revision': recovered['session']['revision'],
            'budgetRoundId': recovered['budget']['id'], 'mode': 'resume'})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'OUTCOME_UNKNOWN')
        acknowledged = self.post('/' + sid + '/continue', {'revision': recovered['session']['revision'],
            'budgetRoundId': recovered['budget']['id'], 'mode': 'resume',
            'acknowledgeUnknownRequests': ['request-before-crash']})
        self.assertEqual(acknowledged.status_code, 202, acknowledged.json)
        paused = self.wait(sid, 'budget_paused')
        self.assertEqual(paused['budget']['id'], recovered['budget']['id'])
        self.assertEqual(paused['budget']['consumed']['activeMilliseconds'], 600000)
        self.assertEqual(len(self.fake.calls), before, 'restart acknowledgment minted execution time')
        extended = self.post('/' + sid + '/continue', {'revision': paused['session']['revision'],
            'budgetRoundId': paused['budget']['id'], 'mode': 'extend_budget',
            'confirmationId': paused['budget']['confirmationId']})
        self.assertEqual(extended.status_code, 202, extended.json)
        ready = self.wait(sid, 'awaiting_review')
        self.assertEqual(ready['budget']['number'], 2)
        self.assertEqual(ready['budget']['lifetimeConsumed']['activeMilliseconds'] >= 600000, True)

    def test_unknown_external_original_stays_saved_and_foreign_source_is_rejected(self):
        imported = self.client.post('/desktop/api/portable-assets/imports', data={
            'file': (io.BytesIO(b'unknown provider archive'), 'foreign.bin'), 'kind': 'app'},
            headers={'Idempotency-Key': 'original'}).json
        refs = [{'kind': 'import_draft', 'reference': {'portableAssetId': imported['id']}}]
        sid = self.create(kind='app', intent='prepare_import', sourceRefs=refs)
        result = self.wait(sid, 'awaiting_input')
        self.assertIsNotNone(result['session']['question'])
        self.assertEqual(self.fake.calls, [])
        self.assertEqual(self.client.get(imported['exportUrl']).data, b'unknown provider archive')
        foreign = self.post(body={'kind': 'app', 'intent': 'prepare_import', 'instruction': 'Prepare this import.',
                                   'sourceRefs': refs}, client=self.other)
        self.assertEqual(foreign.status_code, 404, foreign.json)

    def test_file_and_connection_supplements_are_explicit_owned_and_atomic(self):
        uploaded = self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'fixture input'), 'fixture.txt')})
        foreign_upload = self.other.post('/desktop/api/files', data={'file': (io.BytesIO(b'foreign input'), 'foreign.txt')})
        self.assertEqual(uploaded.status_code, 201)
        self.assertEqual(foreign_upload.status_code, 201)
        file_id, foreign_file = uploaded.json['id'], foreign_upload.json['id']
        other_owner = self.other.get('/desktop/api/session').json['user']['id']
        self.w.capabilities.store.put(self.owner, 'connection', 'mine', {'id': 'mine', 'sourceIdentity': 'fixture/source'})
        self.w.capabilities.store.put(other_owner, 'connection', 'theirs', {'id': 'theirs', 'sourceIdentity': 'fixture/source'})
        self.fake.statuses = ['awaiting_authorization']
        sid = self.create()
        waiting = self.wait(sid, 'awaiting_authorization')
        original_sources = waiting['session']['sourceIds']
        refs = [{'kind': 'upload', 'reference': {'fileId': file_id}}]
        body = {'revision': waiting['session']['revision'], 'questionId': waiting['session']['question']['id'],
                'answer': 'Use the selected file and connection.', 'sourceRefs': refs,
                'runtimeBindings': {'fixture/source': 'mine'}}
        invalid = [
            {**body, 'sourceRefs': [{'kind': 'upload', 'reference': {'fileId': foreign_file}}]},
            {**body, 'runtimeBindings': {'fixture/source': 'theirs'}},
            {**body, 'runtimeBindings': {'wrong/source': 'mine'}},
            {**body, 'sourceRefs': [{'kind': 'asset_version', 'reference': {'assetId': 'arbitrary'}}]},
            {**body, 'questionId': 'old-question'},
        ]
        before = len(self.fake.calls)
        for bad in invalid:
            response = self.post('/' + sid + '/answers', bad)
            self.assertIn(response.status_code, (400, 403, 404, 409), response.json)
            state = self.client.get(BASE + '/' + sid).json
            self.assertEqual(state['session']['revision'], waiting['session']['revision'])
            self.assertEqual(state['session']['sourceIds'], original_sources)
            self.assertEqual(state['runtimeBindings'], {})
            self.assertEqual(len(self.service.store.listing('authorizations', self.owner, sid)), 1)
        self.assertEqual(len(self.fake.calls), before)
        accepted = self.post('/' + sid + '/answers', body, key='selected-inputs')
        self.assertEqual(accepted.status_code, 202, accepted.json)
        ready = self.wait(sid, 'awaiting_review')
        self.assertEqual(ready['runtimeBindings'], {'fixture/source': 'mine'})
        create_context = [call['context'] for stage, call in self.fake.calls if stage == 'create'][-1]
        self.assertEqual(create_context['fileIds'], [file_id])
        self.assertEqual(create_context['runtimeBindings'], {'fixture/source': 'mine'})
        authorizations = self.service.store.listing('authorizations', self.owner, sid)
        self.assertEqual(len(authorizations), 2)
        self.assertEqual(authorizations[-1]['basis'], 'explicit_user_selection')
        self.assertEqual(authorizations[-1]['fileIds'], [file_id])
        self.assertEqual(self.post('/' + sid + '/answers', body, key='selected-inputs').status_code, 202)
        self.assertEqual(len(self.service.store.listing('authorizations', self.owner, sid)), 2)

    def test_chat_followup_passes_newly_selected_files_to_same_session(self):
        sid = self.create(mode='draft_only')
        uploaded = self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'chat attachment'), 'chat.txt')}).json
        run = {'id': 'fixture-chat-run', 'conversationId': 'fixture-conversation', 'message': 'Use this new file.',
               'payload': {'authoringSessionId': sid, 'fileIds': [uploaded['id']]}}
        sub_agents = SimpleNamespace(w=self.w, mutate=lambda owner, rid, fn: fn(run), _end=lambda *_: None)
        handoff(sub_agents, self.owner, run)
        ready = self.wait(sid, 'awaiting_review')
        self.assertEqual(run['authoringSessionId'], sid)
        self.assertEqual(ready['session']['revision'], 2)
        context = [call['context'] for stage, call in self.fake.calls if stage == 'create'][-1]
        self.assertEqual(context['fileIds'], [uploaded['id']])
        self.assertEqual(len(self.service.store.listing('sessions', self.owner)), 1)

    def test_ordinary_app_invocation_cannot_use_files_absent_from_current_message(self):
        run = {'id': 'fixture-chat-run', 'conversationId': 'fixture-conversation', 'payload': {'fileIds': ['selected-file']}}
        catalog = [{'assetId': 'app', 'versionId': 'version', 'name': 'Fixture'}]
        sub_agents = SimpleNamespace(w=self.w, mutate=lambda owner, rid, fn: fn(run), _end=lambda *_: None)
        with patch('backend.app.modules.agent_platform.authoring_chat.app_catalog', return_value=catalog), patch.object(
                self.w.asset_apps, 'create_run', return_value={'id': 'app-run'}) as create_run:
            for ungranted in ('other-owned-file', 'foreign-file', '/input/private.txt'):
                with self.assertRaises(CapabilityError) as caught:
                    invoke_app(sub_agents, self.owner, run, {'assetId': 'app', 'versionId': 'version',
                                                           'fileBindings': {'source': ungranted}})
                self.assertEqual(caught.exception.code, 'FILE_NOT_GRANTED')
            create_run.assert_not_called()
            invoke_app(sub_agents, self.owner, run, {'assetId': 'app', 'versionId': 'version',
                                                   'fileBindings': {'source': 'selected-file'}})
            self.assertEqual(create_run.call_count, 1)
            self.assertEqual(create_run.call_args.args[2]['fileBindings'], {'source': 'selected-file'})


if __name__ == '__main__':
    unittest.main()
