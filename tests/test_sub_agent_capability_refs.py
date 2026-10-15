"""Planner reference repair with real admission, coordinator and WASI; no upstream."""
import json
import struct
import tempfile
import time
import unittest
import zlib
from types import SimpleNamespace

from backend.app import create_app
from backend.app.modules.sub_agent.model import Model
from backend.app.modules.sub_agent.store import TERMINAL
from backend.app.modules.workbench.tool_registry import ToolResult, ToolSpec


REF = {'assetId': 'builtin:fixture_prices', 'versionId': 'latest'}
VALUES = [100, 120, 90, 108]
PLOT = '''import json
from cd80_plot import line_chart
def run(payload):
    data = json.load(open(next(iter(payload['files'].values()))))['content']
    values = data['values']
    peak = values[0]
    drawdown = 0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, 1 - value / peak)
    open('/output/chart.png', 'wb').write(line_chart(data['dates'], values, title='SYNTHETIC SAMPLE', unit='CNY'))
    return {'synthetic': data['synthetic'], 'rows': len(values), 'change': values[-1] / values[0] - 1, 'drawdown': drawdown}
'''


def delegated(refs=None, required=None):
    return {'mode': 'delegate', 'goal': 'Analyze explicitly synthetic samples and plot',
            'deliverables': ['PNG', 'metrics'], 'acceptanceCriteria': ['actual sample-derived plot'],
            'constraints': ['synthetic data only', 'no network'], 'preferredCapabilities': refs or [],
            'requiredCapabilities': required or [], 'allowTemporaryCode': True, 'requiredExtensions': ['.png']}


class PlannerCapabilityReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-planner-refs-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                              'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'refs@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.w = self.app.extensions['workbench_service']
        self.s = self.app.extensions['sub_agents']
        self.executions = []
        self.plans = []
        self.count = 0

        def prices(arguments, context):
            self.executions.append(dict(arguments))
            return ToolResult({'synthetic': True, 'values': VALUES,
                               'dates': ['sample-1', 'sample-2', 'sample-3', 'sample-4']})

        self.w.runtime.tools.register(ToolSpec('fixture_prices', 'Explicit synthetic samples for isolated tests',
            {'type': 'object', 'properties': {}, 'additionalProperties': False}, prices))

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, path, body):
        self.count += 1
        return self.client.post('/desktop/api' + path, json=body, headers={'Idempotency-Key': str(self.count)})

    def submit(self, **body):
        response = self.post('/conversations/new/messages', {'message': 'Analyze and plot synthetic samples', **body})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json

    def wait(self, rid, statuses=TERMINAL):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            run = self.s.store.run(self.owner, rid)
            if run['status'] in statuses:
                return run
            time.sleep(.02)
        self.fail('Coordinator did not finish: ' + json.dumps(run, ensure_ascii=False))

    def model(self, handler):
        def call(stage, payload, *_):
            if stage == 'plan':
                self.plans.append(payload)
            return handler(stage, payload)
        self.s.model = SimpleNamespace(call=call)

    def rejection(self, run):
        return [item for item in run['observations'] if item.get('source') == 'model.preferredCapabilities']

    def test_empty_personal_library_ask_repair_builtin_reference_and_real_wasi_plot(self):
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        self.assertTrue(self.s.executor.status()['available'])

        def reply(stage, payload):
            if stage == 'plan':
                if not payload['answers']:
                    return {'mode': 'ask', 'question': 'Use explicitly synthetic sample data?'}
                return delegated([REF] if len(self.plans) == 2 else [], ['fixture_prices'])
            if stage == 'act':
                if not payload['steps']:
                    return {'type': 'tool', 'name': 'fixture_prices', 'arguments': {}}
                if not any(item['type'] == 'code' for item in payload['steps']):
                    data = next(item for item in payload['artifacts'] if item['name'] == 'tool-result.json')
                    return {'type': 'code', 'reason': 'Compute and plot granted synthetic tool output',
                            'inputRefs': [data['id']], 'code': PLOT}
                chart = next(item for item in payload['artifacts'] if item['name'] == 'chart.png')
                return {'type': 'finish', 'answer': 'Synthetic sample chart', 'artifactIds': [chart['id']]}
            if stage == 'approve':
                return {'decision': 'approve', 'reason': 'Read actual synthetic tool output and write local PNG'}
            if stage == 'review':
                self.assertEqual(payload['platformChecks']['missingCapabilities'], [])
                self.assertEqual(payload['platformChecks']['missingExtensions'], [])
                return {'decision': 'accept', 'answer': 'Four synthetic samples: 8% change and 25% maximum drawdown.'}
            raise AssertionError(stage)

        self.model(reply)
        pending = self.wait(self.submit()['id'], {'awaiting_user'})
        answered = self.post('/sub-agent-runs/' + pending['id'] + '/questions/' + pending['question']['id'] + '/answer',
                             {'revision': pending['revision'], 'answer': 'Yes, use synthetic samples.'})
        self.assertEqual(answered.status_code, 200, answered.json)
        done = self.wait(pending['id'])
        self.assertEqual(done['status'], 'succeeded', done.get('answer'))
        self.assertEqual(done['correctionsUsed'], 1)
        self.assertEqual(len(self.plans), 3)
        self.assertEqual(len(self.executions), 1)
        self.assertEqual([(item['type'], item['status']) for item in done['steps']],
                         [('tool', 'succeeded'), ('code', 'succeeded')])
        self.assertEqual(done['loadedRefs'], [])
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        self.assertEqual(self.rejection(done)[0]['rejectedReference'], REF)
        self.assertEqual(self.rejection(done)[0]['errorCode'], 'NOT_FOUND')
        self.assertEqual(self.rejection(done)[0]['modelCallId'],
                         [item['id'] for item in done['modelCalls'] if item['stage'] == 'plan'][1])
        self.assertTrue(any(item.get('userAnswer') for item in self.plans[-1]['observations']))
        events = self.s.store.events(self.owner, done['id'])
        kinds = [item['type'] for item in events]
        self.assertLess(kinds.index('planning.capability_rejected'), kinds.index('run.delegated'))
        result = done['steps'][1]['result']['content']
        self.assertTrue(result['synthetic'])
        self.assertEqual(result['rows'], 4)
        self.assertAlmostEqual(result['change'], .08)
        self.assertAlmostEqual(result['drawdown'], .25)
        png = self.client.get(done['deliveries'][0]['downloadUrl']).data
        self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
        width, height = struct.unpack('>II', png[16:24])
        cursor, compressed = 8, b''
        while cursor < len(png):
            size = struct.unpack('>I', png[cursor:cursor+4])[0]
            kind, data = png[cursor+4:cursor+8], png[cursor+8:cursor+8+size]
            self.assertEqual(zlib.crc32(kind + data) & 0xffffffff,
                             struct.unpack('>I', png[cursor+8+size:cursor+12+size])[0])
            if kind == b'IDAT':
                compressed += data
            cursor += size + 12
        self.assertEqual(len(zlib.decompress(compressed)), height * (1 + width * 3))

    def test_optional_cross_owner_reference_is_bounded_and_does_not_leak_asset(self):
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'other-refs@example.invalid', 'password': 'test-password'})
        other_owner = other.get('/desktop/api/session').json['user']['id']
        ref = {'assetId': 'foreign-asset', 'versionId': 'foreign-version', 'apiKey': 'do-not-retain-this-value'}
        self.w.capabilities.store.put(other_owner, 'asset', ref['assetId'],
                                      {'assetId': ref['assetId'], 'name': 'private foreign name'})
        self.model(lambda stage, payload: delegated([ref]))
        done = self.wait(self.submit()['id'])
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['correctionsUsed'], 3)
        self.assertEqual(len(self.plans), 4)
        self.assertEqual(len(self.rejection(done)), 4)
        self.assertFalse(done['steps'])
        self.assertFalse(done.get('delegation'))
        self.assertFalse(self.executions)
        safe = json.dumps(done, ensure_ascii=False)
        self.assertNotIn('private foreign name', safe)
        self.assertNotIn('do-not-retain-this-value', safe)
        self.assertTrue(all(item['errorCode'] == 'NOT_FOUND' for item in self.rejection(done)))

    def test_user_selected_missing_reference_stops_before_model_or_substitution(self):
        self.model(lambda stage, payload: {'mode': 'answer', 'answer': 'must not reach'})
        done = self.wait(self.submit(capabilityRefs=[{'assetId': 'missing-selected', 'versionId': 'exact-version'}])['id'])
        self.assertEqual(done['status'], 'failed')
        self.assertFalse(self.plans)
        self.assertFalse(done['steps'])
        self.assertEqual(done['correctionsUsed'], 0)
        self.assertIn('无权访问', done['answer'])

    def test_required_missing_reference_stops_without_substitution(self):
        for ref, required in [({'assetId': 'missing-required', 'versionId': 'exact'}, 'missing-required'),
                              ({'assetId': 'builtin:not_registered', 'versionId': 'latest'}, 'not_registered')]:
            with self.subTest(required=required):
                self.model(lambda stage, payload: delegated([ref], [required]))
                done = self.wait(self.submit()['id'])
                self.assertEqual(done['status'], 'failed')
                self.assertEqual(done['correctionsUsed'], 0)
                self.assertEqual(done['steps'], [])
                self.assertFalse(done.get('delegation'))
                self.assertIn('必须使用的能力', done['answer'])
                self.assertEqual(self.rejection(done)[0]['rejectedReference'], ref)
        self.assertFalse(self.executions)

    def test_direct_plan_repairs_before_executing_once(self):
        def reply(stage, payload):
            if len(self.plans) == 3:
                return {'mode': 'answer', 'answer': 'Synthetic values obtained.'}
            return {'mode': 'direct', 'preferredCapabilities': [REF] if len(self.plans) == 1 else [],
                    'action': {'type': 'tool', 'name': 'fixture_prices', 'arguments': {}}}
        self.model(reply)
        done = self.wait(self.submit()['id'])
        self.assertEqual(done['status'], 'succeeded', done.get('answer'))
        self.assertEqual(done['correctionsUsed'], 1)
        self.assertEqual(len(done['steps']), 1)
        self.assertEqual(len(self.executions), 1)

    def test_invalid_direct_required_capabilities_use_real_model_schema_retry(self):
        for malformed in (None, [{}]):
            with self.subTest(required=malformed):
                requests = []
                before = len(self.executions)

                class Provider:
                    model = 'planner-reference-text-fixture'

                    def tokens(self, *args, **kwargs):
                        raise AssertionError('Expected structured model call')

                    def json_tokens(self, messages, model, stopped, on_response):
                        requests.append(json.loads(messages[-1]['content']))
                        if len(requests) == 3:
                            value = {'mode': 'answer', 'answer': 'Synthetic data read once.'}
                        else:
                            value = {'mode': 'direct', 'requiredCapabilities': malformed if len(requests) == 1 else [],
                                     'action': {'type': 'tool', 'name': 'fixture_prices', 'arguments': {}}}
                        yield json.dumps(value)

                self.s.model = Model(Provider())
                done = self.wait(self.submit()['id'])
                self.assertEqual(done['status'], 'succeeded', done.get('answer'))
                self.assertEqual(done['correctionsUsed'], 1)
                self.assertEqual(len(done['steps']), 1)
                self.assertEqual(len(self.executions) - before, 1)
                self.assertEqual(len(requests), 3)
                self.assertEqual(requests[1]['validationFeedback']['code'], 'MODEL_SCHEMA_INVALID')
                calls = done['modelCalls']
                self.assertEqual(calls[0]['status'], 'failed')
                self.assertEqual(calls[0]['errorCode'], 'MODEL_SCHEMA_INVALID')
                self.assertEqual(calls[1]['retryOf'], calls[0]['id'])
                self.assertFalse(self.rejection(done))

    def test_correction_cannot_remove_previously_required_capability(self):
        self.model(lambda stage, payload: delegated([REF], ['fixture_prices']) if len(self.plans) == 1 else delegated())
        done = self.wait(self.submit()['id'])
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['correctionsUsed'], 3)
        self.assertEqual(done['planningRequiredCapabilities'], ['fixture_prices'])
        self.assertFalse(done['steps'])
        self.assertFalse(self.executions)
        self.assertIn('不能移除', done['answer'])

    def test_planning_answer_can_restate_requirements_without_resetting_correction_budget(self):
        def reply(stage, payload):
            if len(self.plans) == 1:
                return delegated([REF], ['fixture_prices'])
            if len(self.plans) == 2:
                return {'mode': 'ask', 'question': 'Change the request to one synthetic data read?'}
            if len(self.plans) == 3:
                return {'mode': 'direct', 'requiredCapabilities': [],
                        'action': {'type': 'tool', 'name': 'fixture_prices', 'arguments': {}}}
            return {'mode': 'answer', 'answer': 'Synthetic data read as newly requested.'}
        self.model(reply)
        pending = self.wait(self.submit()['id'], {'awaiting_user'})
        self.assertEqual(pending['planningRequiredCapabilities'], ['fixture_prices'])
        response = self.post('/sub-agent-runs/' + pending['id'] + '/questions/' + pending['question']['id'] + '/answer',
                             {'revision': pending['revision'], 'answer': 'Yes, only read the synthetic data now.'})
        self.assertEqual(response.status_code, 200, response.json)
        done = self.wait(pending['id'])
        self.assertEqual(done['status'], 'succeeded', done.get('answer'))
        self.assertEqual(done['correctionsUsed'], 1)
        self.assertNotIn('planningRequiredCapabilities', done)
        self.assertEqual(len(done['steps']), 1)
        self.assertEqual(len(self.executions), 1)
        answered = next(item for item in done['observations'] if 'userAnswer' in item)
        self.assertEqual(answered['previousPlanningRequiredCapabilities'], ['fixture_prices'])


if __name__ == '__main__':
    unittest.main()
