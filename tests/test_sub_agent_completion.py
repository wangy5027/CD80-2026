"""User delivery obligations and explicit conclusion evidence; no real market/model."""
import json
import tempfile
import time
import unittest
from types import SimpleNamespace

from backend.app import create_app
from backend.app.modules.sub_agent.service import visualization_requirement
from backend.app.modules.sub_agent.store import TERMINAL
from backend.app.modules.workbench.tool_registry import ToolResult, ToolSpec
from tests.test_sub_agent_capability_refs import PLOT, VALUES


MESSAGE = '请分析并可视化平安银行2026走势'
EXPLANATION = '仅取得单期报价和两融记录，未取得2026年日线序列，不能计算区间涨跌或生成走势图。'


def delegation(**changes):
    return {'mode': 'delegate', 'goal': '分析已取得的真实记录',
            'deliverables': ['股价走势图（如数据支持）'],
            'acceptanceCriteria': ['若无法取得日线序列须明确说明；如有图表须基于实际数据'],
            'constraints': ['不得捏造数据'], 'requiredExtensions': [],
            'allowTemporaryCode': True, **changes}


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-completion-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                              'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'completion@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.w = self.app.extensions['workbench_service']
        self.s = self.app.extensions['sub_agents']
        self.executions, self.reviews = [], []
        self.count = 0
        samples = {'fixture_quotes': {'synthetic': True, 'records': [{'date': 'sample-1', 'close': 100}]},
                   'fixture_margin': {'synthetic': True, 'records': [{'date': 'sample-1', 'balance': 10}]},
                   'fixture_prices': {'synthetic': True, 'values': VALUES,
                                      'dates': ['sample-1', 'sample-2', 'sample-3', 'sample-4']}}
        for name, content in samples.items():
            def execute(arguments, context, name=name, content=content):
                self.executions.append(name)
                return ToolResult(content)
            self.w.runtime.tools.register(ToolSpec(name, 'Explicit synthetic fixture; no upstream',
                {'type': 'object', 'properties': {}, 'additionalProperties': False}, execute))

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, path, body):
        self.count += 1
        return self.client.post('/desktop/api' + path, json=body, headers={'Idempotency-Key': str(self.count)})

    def submit(self, message=MESSAGE):
        response = self.post('/conversations/new/messages', {'message': message})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['id']

    def wait(self, rid, statuses=TERMINAL):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = self.s.store.run(self.owner, rid)
            if run['status'] in statuses:
                return run
            time.sleep(.02)
        self.fail('Isolated coordinator did not finish: ' + json.dumps(run, ensure_ascii=False))

    def model(self, act, *, plan=None, review=None):
        def call(stage, payload, *_):
            if stage == 'plan':
                return plan(payload) if plan else delegation()
            if stage == 'act':
                return act(payload)
            if stage == 'approve':
                return {'decision': 'approve', 'reason': 'Use only granted synthetic data and isolated output.'}
            if stage == 'review':
                self.reviews.append(payload)
                return review(payload) if review else {'decision': 'accept', 'answer': EXPLANATION}
            raise AssertionError(stage)
        self.s.model = SimpleNamespace(call=call)

    @staticmethod
    def tool(name):
        return {'type': 'tool', 'name': name, 'arguments': {}}

    def evidence_flags(self, run):
        return [self.s.execution_evidence(self.owner, run['id'], step['id']).get('adopted') for step in run['steps']]

    def test_original_request_cannot_be_weakened_by_conditional_plan_or_accept(self):
        def act(payload):
            steps = payload['steps']
            if len(steps) < 2:
                return self.tool(('fixture_quotes', 'fixture_margin')[len(steps)])
            return {'type': 'finish', 'answer': EXPLANATION, 'artifactIds': []}
        self.model(act)
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        run = self.wait(self.submit())
        self.assertEqual(run['status'], 'partial', run.get('answer'))
        self.assertEqual(run['completion']['status'], 'partial')
        self.assertEqual(run['completion']['missingDeliverables'], ['图表'])
        self.assertIn(EXPLANATION, run['answer'])
        self.assertIn('未完成', run['answer'])
        self.assertEqual(run['correctionsUsed'], 0)
        self.assertEqual(self.executions, ['fixture_quotes', 'fixture_margin'])
        self.assertEqual(run['deliveries'], [])
        self.assertEqual(self.evidence_flags(run), [False, False])
        self.assertTrue(self.reviews[0]['deliveryObligations']['visualization'])
        self.assertEqual(run['reviews'][-1]['decision'], 'fail')
        self.assertEqual(run['modelCalls'][-1]['finalDecision'], 'fail')

    def test_actual_wasi_chart_completes_and_only_its_dependency_closure_is_adopted(self):
        def act(payload):
            steps = payload['steps']
            if len(steps) < 2:
                return self.tool(('fixture_quotes', 'fixture_prices')[len(steps)])
            if len(steps) == 2:
                data = next(a for a in payload['artifacts'] if a['stepId'] == steps[1]['id'] and a['artifactRole'] == 'execution_result')
                return {'type': 'code', 'reason': 'Compute and plot actual synthetic sequence', 'inputRefs': [data['id']], 'code': PLOT}
            chart = next(a for a in payload['artifacts'] if a['name'] == 'chart.png')
            return {'type': 'finish', 'answer': 'Four synthetic samples plotted.', 'artifactIds': [chart['id']]}
        self.model(act, review=lambda p: {'decision': 'accept', 'answer': 'Synthetic change 8%, maximum drawdown 25%.'})
        run = self.wait(self.submit())
        self.assertEqual(run['status'], 'succeeded', run.get('answer'))
        self.assertEqual(run['completion'], {'status': 'complete', 'missingDeliverables': [], 'reasons': []})
        self.assertEqual(self.evidence_flags(run), [False, True, True])
        self.assertEqual(self.reviews[0]['platformChecks']['missingDeliverables'], [])
        result = run['steps'][-1]['result']['content']
        self.assertAlmostEqual(result['change'], .08)
        self.assertAlmostEqual(result['drawdown'], .25)
        response = self.client.get(run['deliveries'][0]['downloadUrl'])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_user_can_explicitly_cancel_chart_after_delegation(self):
        def act(payload):
            if not payload['steps']:
                return self.tool('fixture_quotes')
            if not any('userAnswer' in o for o in payload['observations']):
                return {'type': 'ask', 'question': '数据不足，是否取消图表，只说明取得的数据？'}
            return {'type': 'finish', 'answer': EXPLANATION, 'artifactIds': [],
                    'adoptedStepIds': [payload['steps'][0]['id']]}
        self.model(act, plan=lambda p: delegation(requiredExtensions=['.png']))
        pending = self.wait(self.submit(), {'awaiting_user'})
        response = self.post('/sub-agent-runs/' + pending['id'] + '/questions/' + pending['question']['id'] + '/answer',
                             {'revision': pending['revision'], 'answer': '取消图表，只需说明数据是否足够'})
        self.assertEqual(response.status_code, 200, response.json)
        run = self.wait(pending['id'])
        self.assertEqual(run['status'], 'succeeded', run.get('answer'))
        self.assertEqual(run['completion']['status'], 'complete')
        self.assertEqual(run['correctionsUsed'], 0)
        self.assertEqual(self.reviews[0]['deliveryObligations'], {'visualization': False, 'source': 'user.answer'})
        self.assertEqual(self.reviews[0]['platformChecks']['missingExtensions'], [])
        self.assertEqual(self.evidence_flags(run), [True])

    def test_text_conclusion_explicit_step_or_result_selects_only_actual_evidence(self):
        for use_result in (False, True):
            with self.subTest(use_result=use_result):
                def act(payload):
                    if len(payload['steps']) < 2:
                        return self.tool(('fixture_quotes', 'fixture_margin')[len(payload['steps'])])
                    selected = payload['steps'][1]['id']
                    fields = {'adoptedStepIds': [selected]}
                    if use_result:
                        artifact = next(a for a in payload['artifacts'] if a['stepId'] == selected)
                        fields = {'resultArtifactIds': [artifact['id']]}
                    return {'type': 'finish', 'answer': '仅采用合成两融样例。', **fields}
                self.model(act)
                run = self.wait(self.submit('说明合成样例中的两融记录'))
                self.assertEqual(run['status'], 'succeeded', run.get('answer'))
                self.assertEqual(self.evidence_flags(run), [False, True])
                self.assertTrue(run['resultArtifactIds'] if use_result else run['adoptedStepIds'])

    def test_foreign_run_step_and_result_are_rejected_without_adopting_successes(self):
        self.model(lambda p: self.tool('fixture_quotes') if not p['steps'] else
                   {'type': 'finish', 'answer': 'Seed synthetic record'})
        seed = self.wait(self.submit('Read a synthetic record'))
        refs = [('adoptedStepIds', seed['steps'][0]['id']), ('resultArtifactIds', seed['artifacts'][0]['id'])]
        for field, ref in refs:
            with self.subTest(field=field):
                self.model(lambda p: self.tool('fixture_margin') if not p['steps'] else
                           {'type': 'finish', 'answer': 'Invalid cross-run conclusion', field: [ref]})
                run = self.wait(self.submit('说明合成记录'))
                self.assertEqual(run['status'], 'failed', run.get('answer'))
                self.assertEqual(run['correctionsUsed'], 3)
                self.assertNotIn('completion', run)
                self.assertEqual(len(run['steps']), 1)
                self.assertNotEqual(self.evidence_flags(run), [True])

    def test_cross_owner_result_is_not_disclosed_or_adopted(self):
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'other-completion@example.invalid', 'password': 'test-password'})
        owner = other.get('/desktop/api/session').json['user']['id']
        # A foreign artifact key is enough to exercise the real owner lookup.
        with self.s.store.transaction() as db:
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)',
                       (owner, 'foreign-result', 'foreign-run', json.dumps({'private': 'PRIVATE-OTHER-USER'}), b'private'))
        self.assertEqual(self.s.store.artifact(owner, 'foreign-result')[1], b'private')
        self.model(lambda p: {'type': 'finish', 'answer': 'Invalid', 'resultArtifactIds': ['foreign-result']})
        run = self.wait(self.submit('说明已取得的数据'))
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertNotIn('PRIVATE-OTHER-USER', json.dumps(run))
        self.assertFalse(run['steps'])

    def test_failed_step_cannot_be_adopted(self):
        self.model(lambda p: self.tool('fixture_absent') if not p['steps'] else
                   {'type': 'finish', 'answer': 'Invalid', 'adoptedStepIds': [p['steps'][0]['id']]})
        run = self.wait(self.submit('说明已取得的数据'))
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertEqual(run['steps'][0]['status'], 'failed')
        self.assertNotEqual(self.evidence_flags(run), [True])

    def test_direct_answer_does_not_bypass_required_chart(self):
        self.model(lambda p: None, plan=lambda p: {'mode': 'direct', 'action': self.tool('fixture_quotes')} if not p['steps'] else
                   {'mode': 'answer', 'answer': EXPLANATION, 'adoptedStepIds': [p['steps'][0]['id']]})
        run = self.wait(self.submit())
        self.assertEqual(run['status'], 'partial', run.get('answer'))
        self.assertIn(EXPLANATION, run['answer'])
        self.assertEqual(run['completion']['missingDeliverables'], ['图表'])
        self.assertEqual(self.evidence_flags(run), [True])

    def test_declared_missing_data_is_partial_even_without_visualization_requirement(self):
        self.model(lambda p: {'type': 'finish', 'answer': EXPLANATION,
                             'completion': 'partial', 'missingDeliverables': ['历史序列']})
        run = self.wait(self.submit('分析历史收益率'))
        self.assertEqual(run['status'], 'partial')
        self.assertEqual(run['completion']['missingDeliverables'], ['历史序列'])
        self.assertIn(EXPLANATION, run['answer'])
        self.assertEqual(run['correctionsUsed'], 0)

    def test_renamed_non_image_output_cannot_satisfy_chart_requirement(self):
        def act(payload):
            if not payload['steps']:
                return {'type': 'code', 'reason': 'Controlled malformed output regression',
                        'code': "def run(payload):\n    open('/output/chart.png','wb').write(b'not an image')\n    return {}"}
            chart = next(a for a in payload['artifacts'] if a['name'] == 'chart.png')
            return {'type': 'finish', 'answer': 'Output exists but is not a readable chart.', 'artifactIds': [chart['id']]}
        self.model(act)
        run = self.wait(self.submit())
        self.assertEqual(run['steps'][0]['status'], 'succeeded')
        self.assertEqual(run['status'], 'partial', run.get('answer'))
        self.assertEqual(run['completion']['missingDeliverables'], ['图表'])
        self.assertEqual(run['correctionsUsed'], 0)

    def test_reviewer_cannot_replace_verified_selection_with_unknown_step(self):
        self.model(lambda p: self.tool('fixture_quotes') if not p['steps'] else
                   {'type': 'finish', 'answer': 'Observed', 'adoptedStepIds': [p['steps'][0]['id']]},
                   review=lambda p: {'decision': 'accept', 'answer': 'Invalid review selection',
                                     'adoptedStepIds': ['unexecuted-step']})
        run = self.wait(self.submit('说明合成记录'))
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertEqual(self.executions, ['fixture_quotes'])
        self.assertNotEqual(self.evidence_flags(run), [True])


class ExplicitVisualizationTests(unittest.TestCase):
    def test_explicit_action_and_negation_or_knowledge_are_distinct(self):
        for text in (MESSAGE, '请画走势图并解释', '请解释并可视化结果', '为我生成折线图'):
            with self.subTest(text=text):
                self.assertTrue(visualization_requirement({'message': text})['visualization'])
        for text in ('如何画走势图', '画图是什么意思', '请解释可视化的含义', '请问怎么画图',
                     '请告诉我如何画图', '请分析数据，不需要图表', '不要画图'):
            with self.subTest(text=text):
                self.assertFalse(visualization_requirement({'message': text})['visualization'])
        self.assertTrue(visualization_requirement({'message': MESSAGE, 'delegation': delegation(),
                                                  'observations': [{'summary': '无需绘图'}]})['visualization'])
        for answer in ('不要取消图表', '不用解释如何画图', '不需要图表代码'):
            with self.subTest(answer=answer):
                self.assertTrue(visualization_requirement({'message': MESSAGE,
                    'observations': [{'userAnswer': answer}]})['visualization'])

    def test_completion_reference_fields_are_bounded_stage_contracts(self):
        from backend.app.modules.sub_agent.protocol import validate_response
        from backend.app.modules.workbench.errors import GatewayError
        for stage, value in [('plan', {'mode': 'answer', 'answer': 'Observed'}),
                             ('act', {'type': 'finish', 'answer': 'Observed'}),
                             ('review', {'decision': 'accept', 'answer': 'Observed'})]:
            for field, invalid in [('completion', 'maybe'), ('adoptedStepIds', [False]),
                                   ('resultArtifactIds', None), ('missingDeliverables', ['x'] * 33)]:
                with self.subTest(stage=stage, field=field), self.assertRaises(GatewayError) as caught:
                    validate_response(stage, {**value, field: invalid})
                self.assertEqual(caught.exception.code, 'MODEL_SCHEMA_INVALID')


if __name__ == '__main__':
    unittest.main()
