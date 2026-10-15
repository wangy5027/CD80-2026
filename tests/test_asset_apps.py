"""Unified App behavior with isolated accounts/data and the real WASI executor."""
import copy
import base64
import json
import tempfile
import time
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.agent_platform.asset_store import AssetStore
from backend.app.modules.capability_library.contracts import CapabilityError


def sample():
    return {'format': 'cd80.app/2', 'name': '价格研究', 'description': '合成数据计算，不代表实际证券',
        'inputSchema': {'type': 'object', 'properties': {'prices': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2}},
                        'required': ['prices'], 'additionalProperties': False}, 'rules': ['价格变化率及最大回撤均以收盘序列计算'],
        'nodes': [
            {'id': 'prepare', 'kind': 'code', 'title': '准备本次数据',
             'code': 'import json\ndef run(payload):\n    with open("/output/prices.json", "w") as f: json.dump(payload["prices"], f)\n    return {"count":len(payload["prices"])}\n',
             'inputBindings': {'prices': {'from': 'input', 'path': 'prices'}},
             'delivery': {'schema': {'type': 'object', 'required': ['count']}, 'files': [{'name': 'prices.json'}]}, 'checkpoint': False},
            {'id': 'calculate', 'kind': 'code', 'title': '计算价格指标',
             'code': 'import json\ndef run(payload):\n    with open(payload["source"]) as f: prices=json.load(f)\n    peak=prices[0]\n    drawdown=0\n    for price in prices:\n        peak=max(peak,price)\n        drawdown=max(drawdown,1-price/peak)\n    return {"change":round((prices[-1]/prices[0]-1)*100,8),"drawdown":round(drawdown*100,8)}\n',
             'inputBindings': {'source': {'from': 'artifact', 'stepId': 'prepare', 'name': 'prices.json'}},
             'delivery': {'schema': {'type': 'object', 'required': ['change', 'drawdown']}}, 'checkpoint': False},
            {'id': 'merge', 'kind': 'code', 'title': '合并结果',
             'code': 'def run(payload):\n    return {**payload["metrics"],"count":payload["count"]}\n',
             'inputBindings': {'metrics': {'from': 'step', 'stepId': 'calculate', 'path': ''},
                               'count': {'from': 'step', 'stepId': 'prepare', 'path': 'count'}},
             'delivery': {'schema': {'type': 'object', 'required': ['change', 'drawdown', 'count']}}, 'checkpoint': False}],
        'outputs': {'analysis': {'from': 'step', 'stepId': 'merge', 'path': ''}}, 'gaps': []}


class AssetAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-app-assets-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                               'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.service = self.app.extensions['asset_apps']
        self.client = self.app.test_client()
        self.other = self.app.test_client()
        for i, client in enumerate((self.client, self.other)):
            self.assertEqual(client.post('/desktop/api/register', json={'email': f'app{i}@example.invalid', 'password': 'test-password'}).status_code, 200)
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.n = 0

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, path, body, key=None, client=None):
        self.n += 1
        return (client or self.client).post('/desktop/api/asset-apps' + path, json=body,
            headers={'Idempotency-Key': key or str(self.n)})

    def save(self, doc=None):
        response = self.post('', {'definition': doc or sample()})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def wait_run(self, rid):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            run = self.service.run(self.owner, rid)
            if run['status'] not in {'queued', 'running'} and rid not in self.service.active:
                return run
            time.sleep(.025)
        self.fail('App did not finish')

    def verify(self, app, *, bad=False):
        cases = [
            {'label': '原预期', 'input': {'prices': [100, 120, 90, 108]},
             'expectation': {'mode': 'fields', 'values': {'analysis.change': 8, 'analysis.drawdown': 25, 'analysis.count': 4}}},
            {'label': '不同输入', 'input': {'prices': [80, 100, 95, 120]},
             'expectation': {'mode': 'fields', 'values': {'analysis.change': -1 if bad else 50, 'analysis.drawdown': 5}}}]
        response = self.post('/' + app['id'] + '/verify', {'versionId': app['versionId'], 'cases': cases})
        self.assertEqual(response.status_code, 202, response.json)
        for rid in response.json['verification']['runIds']:
            self.wait_run(rid)
        return self.service.detail(self.owner, app['id'])

    def enable(self, app):
        response = self.post('/' + app['id'] + '/enable', {'versionId': app['versionId'], 'enabled': True, 'revision': app['revision']})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def test_full_pipeline_new_input_artifact_and_prior_merge(self):
        app = self.save()
        self.assertEqual(app['issues'], [])
        app = self.verify(app)
        self.assertEqual(app['verification']['status'], 'passed', app['verification'])
        app = self.enable(app)
        runs = []
        for prices, expected in [([100, 120, 90, 108], 8), ([80, 100, 95, 120], 50)]:
            response = self.post('/' + app['id'] + '/runs', {'versionId': app['versionId'], 'input': {'prices': prices}})
            self.assertEqual(response.status_code, 202, response.json)
            run = self.wait_run(response.json['id'])
            self.assertEqual(run['status'], 'succeeded', run.get('error'))
            self.assertEqual(run['result']['analysis']['change'], expected)
            self.assertEqual([s['status'] for s in run['steps']], ['succeeded'] * 3)
            raw = self.client.get(run['artifacts'][0]['downloadUrl'])
            self.assertEqual(json.loads(raw.data), prices)
            self.assertEqual(self.other.get(run['artifacts'][0]['downloadUrl']).status_code, 404)
            runs.append(run)
        self.assertNotEqual(runs[0]['id'], runs[1]['id'])
        self.assertNotEqual(runs[0]['artifacts'][0]['id'], runs[1]['artifacts'][0]['id'])

    def test_draft_version_idempotency_and_owner(self):
        body = {'definition': sample()}
        one = self.post('', body, key='same')
        two = self.post('', body, key='same')
        self.assertEqual(one.json['versionId'], two.json['versionId'])
        altered = copy.deepcopy(body)
        altered['definition']['name'] = '改变'
        self.assertEqual(self.post('', altered, key='same').status_code, 409)
        app = one.json
        self.assertEqual(self.other.get('/desktop/api/asset-apps/' + app['id']).status_code, 404)
        self.assertEqual(self.post('/' + app['id'] + '/runs', {'input': {'prices': [1, 2]}}).status_code, 409)
        changed = self.post('', {**altered, 'appId': app['id'], 'revision': app['revision']})
        self.assertEqual(changed.status_code, 201, changed.json)
        self.assertNotEqual(changed.json['versionId'], app['versionId'])
        old = self.service.detail(self.owner, app['id'], app['versionId'])
        self.assertEqual(old['definition']['name'], '价格研究')
        self.assertEqual(self.post('', {**body, 'appId': app['id'], 'revision': app['revision']}).status_code, 409)

    def test_save_and_step_extraction_persist_without_model_tools_or_verification(self):
        # Saving the user's work is independent of running test cases or models.
        with ExitStack() as stack:
            for target, method in ((self.w.platform.executor, 'execute'),
                                   (self.w.platform.planner, '_call'),
                                   (self.w.runtime.provider, 'events'),
                                   (self.w.runtime.tools, 'execute'),
                                   (self.w.capabilities, 'invoke')):
                stack.enter_context(patch.object(target, method,
                    side_effect=AssertionError('Saving must not execute a model, Tool or code.')))
            saved = self.save()
            self.assertEqual(saved['verification'], {'status': 'unverified', 'cases': [], 'runIds': []})
            self.assertIsNone(saved['enabledVersionId'])
            self.assertEqual(self.client.get('/desktop/api/asset-apps/' + saved['id'] + '/runs').json['items'], [])
            reloaded = self.client.get('/desktop/api/asset-apps/' + saved['id']).json
            self.assertEqual(reloaded['definition'], saved['definition'])
            for kind in ('skill', 'tool'):
                with self.subTest(kind=kind):
                    extracted = self.post('/' + saved['id'] + '/extract', {
                        'versionId': saved['versionId'], 'kind': kind, 'nodeIds': ['prepare']})
                    self.assertEqual(extracted.status_code, 200, extracted.json)
                    draft = self.w.capabilities.draft(self.owner, extracted.json['draft'])
                    result = self.w.capabilities.save_draft(self.owner, draft['draftId'],
                                                          {'draftRevision': draft['revision']})
                    asset = self.w.capabilities.asset(self.owner, result['assetId'])
                    version = self.w.capabilities.version(self.owner, result['assetId'], result['versionId'])
                    self.assertEqual(asset['kind'], kind)
                    self.assertFalse(asset['enabled'])
                    self.assertFalse(version.get('lastVerificationId'))
                    self.assertTrue(self.w.capabilities.files(self.owner, version))
            self.assertEqual(self.w.capabilities.store.list(self.owner, 'verification'), [])
            self.assertEqual(self.w.capabilities.store.list(self.owner, 'invocation'), [])

    def test_original_case_is_saved_and_one_independent_case_can_enable(self):
        original = {'label': '原任务：四日价格', 'input': {'prices': [100, 120, 90, 108]},
                    'fileBindings': {}, 'expectation': {'mode': 'fields',
                        'values': {'analysis.change': 8, 'analysis.drawdown': 25, 'analysis.count': 4}}}
        doc = sample()
        doc['testCases'] = [original]
        saved = self.save(doc)
        self.assertEqual(saved['definition']['testCases'], [original])
        self.assertEqual(saved['testCases'], [original])
        self.assertEqual(saved['verification']['status'], 'unverified')
        response = self.post('/' + saved['id'] + '/verify', {'versionId': saved['versionId'],
                                                            'cases': saved['testCases']})
        self.assertEqual(response.status_code, 202, response.json)
        self.assertEqual(len(response.json['verification']['runIds']), 1)
        self.wait_run(response.json['verification']['runIds'][0])
        checked = self.service.detail(self.owner, saved['id'])
        self.assertEqual(checked['verification']['status'], 'passed')
        self.assertEqual(self.enable(checked)['enabledVersionId'], saved['versionId'])

    def test_missing_or_legacy_original_case_never_uses_execution_result_as_oracle(self):
        saved = self.save()
        candidate = saved['testCases']
        self.assertEqual(len(candidate), 1)
        self.assertEqual(candidate[0]['input'], {'prices': [0, 0]})
        self.assertIsNone(candidate[0]['expectation'])
        self.assertEqual(self.post('/' + saved['id'] + '/verify', {
            'versionId': saved['versionId'], 'cases': candidate}).json['code'], 'INVALID_EXPECTATION')
        version = self.service.store.read('versions', self.owner, saved['versionId'])
        version['definition'].pop('testCases')
        version['verification'] = {'status': 'failed', 'runIds': [], 'cases': [{
            'label': '旧原例', 'input': {'prices': [1, 2]}, 'fileBindings': {},
            'expectation': {'mode': 'fields', 'values': {'analysis.change': 100}},
            'actual': {'analysis': {'change': 999}}, 'status': 'failed'}]}
        with self.service.store.transaction() as db:
            self.service.store.put(db, 'versions', self.owner, version)
        restored = self.service.detail(self.owner, saved['id'])['testCases']
        self.assertEqual(restored, [{'label': '旧原例', 'input': {'prices': [1, 2]},
                                     'fileBindings': {}, 'expectation': {
                                         'mode': 'fields', 'values': {'analysis.change': 100}}}])
        exported = self.client.get('/desktop/api/asset-apps/' + saved['id'] + '/export').json
        self.assertEqual(exported['definition']['testCases'], restored)

    def test_bad_second_case_prevents_enable(self):
        app = self.verify(self.save(), bad=True)
        self.assertEqual(app['verification']['status'], 'failed')
        response = self.post('/' + app['id'] + '/enable', {'versionId': app['versionId'], 'revision': app['revision'], 'enabled': True})
        self.assertEqual(response.status_code, 409)

    def test_checkpoints_resume_cancel_and_stale_revision(self):
        doc = sample()
        doc['nodes'][0]['checkpoint'] = True
        app = self.enable(self.verify(self.save(doc)))
        started = self.post('/' + app['id'] + '/runs', {'input': {'prices': [100, 120, 90, 108]}}).json
        run = self.wait_run(started['id'])
        self.assertEqual(run['status'], 'awaiting_confirmation')
        first_attempt = run['steps'][0]['attemptId']
        response = self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision']})
        self.assertEqual(response.status_code, 202, response.json)
        resumed = self.wait_run(run['id'])
        self.assertEqual(resumed['status'], 'succeeded', resumed.get('error'))
        self.assertEqual(resumed['steps'][0]['attemptId'], first_attempt)
        self.assertEqual(self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision']}).status_code, 409)
        started = self.post('/' + app['id'] + '/runs', {'input': {'prices': [80, 100, 95, 120]}}).json
        run = self.wait_run(started['id'])
        cancelled = self.post('/runs/' + run['id'] + '/cancel', {'revision': run['revision']})
        self.assertEqual(cancelled.json['status'], 'cancelled')
        self.assertEqual(cancelled.json['steps'][1]['status'], 'pending')

    def test_missing_delivery_stops_before_downstream(self):
        doc = sample()
        doc['nodes'][0]['delivery']['files'] = [{'name': 'never-created.csv'}]
        app = self.verify(self.save(doc))
        self.assertEqual(app['verification']['status'], 'failed')
        for rid in app['verification']['runIds']:
            run = self.service.run(self.owner, rid)
            self.assertEqual(run['error']['code'], 'DELIVERY_INVALID')
            self.assertEqual(run['steps'][1]['status'], 'pending')

    def test_forward_reference_draft_cannot_validate(self):
        doc = sample()
        doc['nodes'][0]['inputBindings']['prices'] = {'from': 'step', 'stepId': 'merge', 'path': 'prices'}
        app = self.save(doc)
        self.assertTrue(app['issues'])
        self.assertEqual(app['verification']['status'], 'unverified')

    def test_export_import_preserves_implementation_not_authority(self):
        app = self.save()
        package = self.client.get('/desktop/api/asset-apps/' + app['id'] + '/export').json
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('read/import must not execute')):
            preview = self.post('/import/preview', {'package': package}, client=self.other)
            self.assertEqual(preview.status_code, 200, preview.json)
            imported = self.post('/import', {'package': package}, client=self.other)
            self.assertEqual(imported.status_code, 201, imported.json)
        self.assertEqual(imported.json['definition']['nodes'][1]['code'], app['definition']['nodes'][1]['code'])
        self.assertEqual(imported.json['testCases'], app['testCases'])
        self.assertIsNone(imported.json['enabledVersionId'])
        self.assertEqual(imported.json['verification']['status'], 'unverified')
        package['definition']['name'] = 'tampered'
        self.assertEqual(self.post('/import', {'package': package}).status_code, 400)

    def test_resource_package_above_chat_json_limit_still_roundtrips(self):
        doc = sample()
        raw = b'\x00\x81\xfe\xff' * (600 * 1024)
        doc['nodes'][0]['files'] = [{'path': 'assets/data.bin', 'base64': base64.b64encode(raw).decode()}]
        app = self.save(doc)
        package = self.client.get('/desktop/api/asset-apps/' + app['id'] + '/export').json
        self.assertGreater(len(json.dumps(package)), 2 * 1024 * 1024)
        imported = self.post('/import', {'package': package}, client=self.other)
        self.assertEqual(imported.status_code, 201, imported.json)
        actual = imported.json['definition']['nodes'][0]['files'][0]['base64']
        self.assertEqual(base64.b64decode(actual), raw)
        self.assertEqual(self.post('/preview', {'oversize': 'x' * (9 * 1024 * 1024)}).status_code, 413)

    def test_restart_preserves_completed_result_and_marks_unknown(self):
        app = self.enable(self.verify(self.save()))
        version = self.service.store.read('versions', self.owner, app['versionId'])
        run = self.service._new_run(self.owner, version, {'input': {'prices': [1, 2]}}, 'run')
        run['status'] = 'running'
        run['steps'][0].update(kind='tool', status='running', attemptId='old')
        with self.service.store.transaction() as db:
            self.service.store.put(db, 'runs', self.owner, run)
        AssetStore(self.w.platform.repository)
        recovered = self.service.run(self.owner, run['id'])
        self.assertEqual(recovered['status'], 'interrupted')
        self.assertTrue(recovered['steps'][0]['outcomeUnknown'])
        with self.assertRaises(CapabilityError) as failure:
            self.service.resume(self.owner, run['id'], {'revision': recovered['revision']}, 'resume-unknown')
        self.assertEqual(failure.exception.code, 'OUTCOME_UNKNOWN')


if __name__ == '__main__':
    unittest.main()
