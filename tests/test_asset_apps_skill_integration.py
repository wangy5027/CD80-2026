"""Real App API, frozen Skill scripts and WASI; only model choices are fixtures."""
import copy
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library.components import snapshot_from_package


RULE = '输出金额保持原单位，不转换货币；只计算当前输入序列。'


def pipeline():
    manifest = {'format': 'cd80.capability/1', 'kind': 'skill', 'name': 'file-total',
        'description': 'Read authorized values and deliver their sum.',
        'source': {'identity': 'test/app-file-total', 'format': 'platform'}, 'dependencies': [],
        'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': True, 'outputFiles': True},
        'skill': {'entry': 'SKILL.md', 'scripts': [{'path': 'scripts/total.py', 'type': 'python_wasi', 'callable': 'run'}]}}
    script = ('import json\ndef run(payload):\n'
              '    with open(payload["source"]) as f: values=json.load(f)\n'
              '    total=sum(values)\n'
              '    with open("/output/total.csv","w") as f: f.write("total\\n"+str(total)+"\\n")\n'
              '    return {"total":total,"count":len(values)}\n')
    component = snapshot_from_package(manifest, {'SKILL.md': b'---\nname: file-total\ndescription: Sum the provided values file.\n---\nUse scripts/total.py to read the authorized file and produce total.csv.',
        'scripts/total.py': script.encode()})
    return {'format': 'cd80.app/2', 'name': '真实文件 Skill 链路', 'description': 'Synthetic integration fixture.',
        'inputSchema': {'type': 'object', 'properties': {'values': {'type': 'array', 'items': {'type': 'number'}, 'minItems': 1}},
                        'required': ['values'], 'additionalProperties': False}, 'rules': [RULE], 'gaps': [],
        'nodes': [
            {'id': 'prepare', 'kind': 'code', 'title': '准备文件',
             'code': 'import json\ndef run(payload):\n    with open("/output/values.json","w") as f: json.dump(payload["values"],f)\n    return {"count":len(payload["values"])}\n',
             'inputBindings': {'values': {'from': 'input', 'path': 'values'}},
             'delivery': {'schema': {'type': 'object', 'required': ['count']}, 'files': [{'name': 'values.json'}]}, 'checkpoint': False},
            {'id': 'calculate', 'kind': 'skill', 'title': '计算交付', 'task': '使用实际文件计算合计并交付CSV。',
             'component': component, 'inputBindings': {'source': {'from': 'artifact', 'stepId': 'prepare', 'name': 'values.json'}},
             'delivery': {'schema': {'type': 'object', 'required': ['total', 'count'],
                                     'properties': {'total': {'type': 'number'}, 'count': {'type': 'integer'}}},
                          'files': [{'name': 'total.csv', 'mediaType': 'text/csv'}], 'evidenceFields': ['total', 'count']}, 'checkpoint': False},
            {'id': 'merge', 'kind': 'code', 'title': '核对并合并',
             'code': 'import csv\ndef run(payload):\n    with open(payload["source"]) as f: value=float(next(csv.DictReader(f))["total"])\n    assert value==payload["metrics"]["total"]\n    return {**payload["metrics"],"csvTotal":value}\n',
             'inputBindings': {'source': {'from': 'artifact', 'stepId': 'calculate', 'name': 'total.csv'},
                               'metrics': {'from': 'step', 'stepId': 'calculate', 'path': ''}},
             'delivery': {'schema': {'type': 'object', 'required': ['total', 'count', 'csvTotal']}}, 'checkpoint': False}],
        'outputs': {'analysis': {'from': 'step', 'stepId': 'merge', 'path': ''}}}


class SkillModel:
    def __init__(self):
        self.ask = False
        self.invalid = False
        self.block = False
        self.entered = threading.Event()
        self.tasks = []

    def call(self, stage, payload, stopped, on_response):
        if stage == 'review':
            return {'decision': 'accept', 'answer': '实际脚本交付已核验。'}
        if stage == 'approve':
            return {'decision': 'approve', 'reason': '只使用当前授权文件。'}
        self.tasks.append(payload['message'])
        if self.block:
            self.entered.set()
            stopped.wait(10)
        if self.ask and not any('userAnswer' in o for o in payload['observations']):
            return {'type': 'ask', 'question': '是否按原单位继续？'}
        if self.invalid:
            return {'type': 'finish', 'answer': '自称完成', 'result': {'total': 123, 'count': 1}, 'artifactIds': []}
        completed = [s for s in payload['steps'] if s['status'] == 'succeeded']
        if not completed:
            initial = next(o for o in payload['observations'] if 'inputFiles' in o)
            meta = initial['inputFiles'][0]
            return {'type': 'tool', 'name': 'skill_script', 'inputRefs': [meta['id']],
                'arguments': {'path': 'scripts/total.py', 'input': {'source': '/input/' + meta['id'] + '.json'}}}
        step = completed[-1]
        return {'type': 'finish', 'answer': '交付真实结果。', 'resultStepId': step['id'],
                'artifactIds': [a['id'] for a in step['result']['artifacts'] if a['name'] == 'total.csv']}


class AssetAppsSkillIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='app-skill-integration-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                               'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.service = self.app.extensions['asset_apps']
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'integration@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.model = SkillModel()
        self.w.sub_agents.model = self.model
        self.n = 0

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, path, body):
        self.n += 1
        return self.client.post('/desktop/api/asset-apps' + path, json=body, headers={'Idempotency-Key': str(self.n)})

    def wait(self, rid):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            run = self.service.run(self.owner, rid)
            if run['status'] not in {'queued', 'running'} and rid not in self.service.active:
                return run
            time.sleep(.025)
        self.fail('App did not finish')

    def verified(self):
        response = self.post('', {'definition': pipeline()})
        self.assertEqual(response.status_code, 201, response.json)
        app = response.json
        self.assertFalse(app['issues'], app['issues'])
        cases = [{'label': '原输入', 'input': {'values': [2, 3, 5]},
                  'expectation': {'mode': 'fields', 'values': {'analysis.total': 10, 'analysis.count': 3, 'analysis.csvTotal': 10}}},
                 {'label': '不同输入', 'input': {'values': [7, 11]},
                  'expectation': {'mode': 'fields', 'values': {'analysis.total': 18, 'analysis.count': 2, 'analysis.csvTotal': 18}}}]
        response = self.post('/' + app['id'] + '/verify', {'versionId': app['versionId'], 'cases': cases})
        self.assertEqual(response.status_code, 202, response.json)
        runs = [self.wait(rid) for rid in response.json['verification']['runIds']]
        app = self.service.detail(self.owner, app['id'])
        diagnostics = {'runs': runs, 'skillCalls': []}
        if app['verification']['status'] != 'passed':
            for run in runs:
                for step in run['steps']:
                    call_id = step.get('skillCallId') or (step.get('error') or {}).get('details', {}).get('skillCallId')
                    if call_id:
                        call = self.w.sub_agents.store.run(self.owner, call_id)
                        diagnostics['skillCalls'].append({'id': call_id, 'status': call['status'],
                            'error': call.get('error'), 'steps': call['steps'], 'observations': call['observations']})
        self.assertEqual(app['verification']['status'], 'passed', diagnostics)
        response = self.post('/' + app['id'] + '/enable', {'versionId': app['versionId'], 'enabled': True, 'revision': app['revision']})
        self.assertEqual(response.status_code, 200, response.json)
        return response.json, runs

    def start(self, app):
        response = self.post('/' + app['id'] + '/runs', {'input': {'values': [4, 6, 9]}})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json

    def test_verified_skill_pipeline_actual_files_rules_and_different_input(self):
        app, cases = self.verified()
        run = self.wait(self.start(app)['id'])
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual(run['result'], {'analysis': {'total': 19, 'count': 3, 'csvTotal': 19}})
        self.assertTrue(all(RULE in task for task in self.model.tasks))
        skill = self.w.sub_agents.store.run(self.owner, run['steps'][1]['skillCallId'])
        self.assertEqual(skill['parentRunId'], run['id'])
        self.assertEqual(skill['nodeId'], 'calculate')
        evidence = self.w.sub_agents.execution_evidence(self.owner, skill['id'], skill['steps'][0]['id'])
        self.assertEqual(len(evidence['invocationIds']), 1)
        self.assertTrue(evidence['adopted'])
        file = next(a for a in run['artifacts'] if a['name'] == 'total.csv')
        self.assertEqual(self.client.get(file['downloadUrl']).data, b'total\n19\n')
        self.assertNotEqual(cases[0]['steps'][1]['skillCallId'], cases[1]['steps'][1]['skillCallId'])

    def test_windows_excel_association_cannot_change_csv_delivery_contract(self):
        import mimetypes
        original = mimetypes.guess_type
        def host_type(name, *args, **kwargs):
            return ('application/vnd.ms-excel', None) if str(name).endswith('.csv') else original(name, *args, **kwargs)
        with patch('mimetypes.guess_type', side_effect=host_type):
            self.assertEqual(mimetypes.guess_type('total.csv')[0], 'application/vnd.ms-excel')
            app, _ = self.verified()
            run = self.wait(self.start(app)['id'])
            self.assertEqual(run['status'], 'succeeded', run)
            file = next(a for a in run['artifacts'] if a['name'] == 'total.csv')
            self.assertEqual(file['mediaType'], 'text/csv')
            self.assertEqual(self.client.get(file['downloadUrl']).data, b'total\n19\n')

    def test_skill_question_resume_retains_call_and_previous_artifact(self):
        app, _ = self.verified()
        self.model.ask = True
        run = self.wait(self.start(app)['id'])
        self.assertEqual(run['status'], 'awaiting_input', run)
        previous = copy.deepcopy(run['steps'][0])
        skill_call = run['steps'][1]['skillCallId']
        input_digest = run['inputDigest']
        question = run['waiting']['question']
        answer = {'questionId': question['id'], 'revision': question['revision'], 'answer': '按原单位继续。'}
        response = self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision'], 'skillAnswer': answer})
        self.assertEqual(response.status_code, 202, response.json)
        resumed = self.wait(run['id'])
        self.assertEqual(resumed['status'], 'succeeded', resumed)
        self.assertEqual(resumed['steps'][0], previous)
        self.assertEqual(resumed['steps'][1]['skillCallId'], skill_call)
        self.assertEqual(resumed['inputDigest'], input_digest)
        skill = self.w.sub_agents.store.run(self.owner, skill_call)
        self.assertEqual([o['userAnswer'] for o in skill['observations'] if 'userAnswer' in o], ['按原单位继续。'])

    def test_skill_correction_exhaustion_stops_downstream(self):
        app, _ = self.verified()
        self.model.invalid = True
        run = self.wait(self.start(app)['id'])
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['steps'][0]['status'], 'succeeded')
        self.assertEqual(run['steps'][2]['status'], 'pending')
        self.assertEqual(run['error']['code'], 'SKILL_FAILED')
        skill = self.w.sub_agents.store.run(self.owner, run['error']['details']['skillCallId'])
        self.assertEqual(skill['correctionsUsed'], 3)
        self.assertEqual(skill['status'], 'failed')
        self.assertFalse(skill.get('deliveries'))

    def test_cancel_active_skill_rejects_late_result(self):
        app, _ = self.verified()
        self.model.block = True
        started = self.start(app)
        self.assertTrue(self.model.entered.wait(10))
        run = self.service.run(self.owner, started['id'])
        response = self.post('/runs/' + run['id'] + '/cancel', {'revision': run['revision']})
        self.assertEqual(response.status_code, 202, response.json)
        run = self.wait(run['id'])
        self.assertEqual(run['status'], 'cancelled', run)
        self.assertEqual(run['steps'][2]['status'], 'pending')
        self.assertFalse(any(a['name'] == 'total.csv' for a in run['artifacts']))


if __name__ == '__main__':
    unittest.main()
