"""Private data-file authorization through sub-agent and capability runtimes."""

import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app import create_app
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.sub_agent.store import TERMINAL


CODE = '''import csv
def run(payload):
    path = next(iter(payload['files'].values()))
    count = total = 0
    tail = False
    with open(path, newline='', encoding='utf-8') as source:
        for row in csv.DictReader(source):
            count += 1
            total += int(row['amount'])
            tail = tail or row['category'] == 'TAIL'
    return {'rows': count, 'total': total, 'tailSeen': tail}
'''


class DataFileRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cd80-data-runtime-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temporary.name,
                               'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.client = self.app.test_client()
        response = self.client.post('/desktop/api/register', json={
            'email': 'data-runtime@example.invalid', 'password': 'fixture-password'})
        self.assertEqual(response.status_code, 200, response.json)
        self.owner = response.json['user']['id']
        self.workbench = self.app.extensions['workbench_service']
        self.sub_agents = self.app.extensions['sub_agents']
        self.capabilities = self.app.extensions['capability_library']
        self.counter = 0

    def tearDown(self):
        self.workbench.close()
        self.temporary.cleanup()

    def upload(self, raw, name, *, data=True):
        payload = {'file': (io.BytesIO(raw), name)}
        if data:
            payload['purpose'] = 'data'
        response = self.client.post('/desktop/api/files', data=payload,
                                    content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def submit(self, file_id, message='读取已选文件'):
        self.counter += 1
        response = self.client.post('/desktop/api/conversations/new/messages', json={
            'message': message, 'fileIds': [file_id]},
            headers={'Idempotency-Key': f'data-runtime-{self.counter}'})
        self.assertEqual(response.status_code, 202, response.json)
        return response.json['id']

    def wait(self, rid, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            run = self.sub_agents.store.run(self.owner, rid)
            if run['status'] in TERMINAL:
                return run
            time.sleep(.03)
        self.fail('Sub-agent did not finish: ' + json.dumps(run, ensure_ascii=False)[:2000])

    @staticmethod
    def csv_bytes(rows, width=0):
        row = b'A' * width + b',1\n'
        return b'category,amount\n' + row * rows + b'TAIL,7\n'

    def test_small_csv_and_xlsx_read_have_bounded_text_previews(self):
        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = '销售'
        sheet.append(['城市', '金额'])
        sheet.append(['成都', 12])
        excel = io.BytesIO()
        workbook.save(excel)

        for raw, name, expected, truncated in (
            (b'city,amount\nChengdu,12\n', 'small.csv', 'Chengdu', False),
            (excel.getvalue(), 'small.xlsx', '成都', False),
            (b'city,amount\n' + b'Chengdu,12\n' * 35, 'a' * 190 + '.csv', 'Chengdu', True),
        ):
            with self.subTest(name=name):
                file = self.upload(raw, name)
                observed = []

                def model(stage, payload, *_):
                    self.assertEqual(stage, 'plan')
                    if not payload['steps']:
                        artifact = next(a for a in payload['artifacts'] if a.get('inputSource') == 'data_management')
                        self.assertEqual(artifact['artifactRole'], 'input')
                        self.assertLessEqual(len(artifact['preview']), 4000)
                        self.assertIn(expected, artifact['preview'])
                        self.assertNotIn('PK\x03\x04', artifact['preview'])
                        return {'mode': 'direct', 'action': {'type': 'read', 'artifactId': artifact['id']}}
                    observed.append(payload['observations'][-1])
                    return {'mode': 'answer', 'answer': '已读取数据文件的可读预览。'}

                self.sub_agents.model = SimpleNamespace(call=model)
                run = self.wait(self.submit(file['id']))
                self.assertEqual(run['status'], 'succeeded', run.get('answer'))
                self.assertEqual(len(observed), 1)
                self.assertIn(expected, observed[0]['preview'])
                self.assertLessEqual(len(observed[0]['preview']), 8000)
                self.assertNotIn('\ufffd', observed[0]['preview'])
                self.assertEqual(observed[0]['truncated'], truncated)
                artifact = next(a for a in run['artifacts'] if a.get('inputSource') == 'data_management')
                self.assertEqual(Path(artifact['name']).suffix, Path(name).suffix)
                self.assertEqual(self.sub_agents.store.artifact(self.owner, artifact['id'])[1], raw)

        other = self.app.test_client()
        registered = other.post('/desktop/api/register', json={
            'email': 'other-reader@example.invalid', 'password': 'fixture-password'})
        self.assertEqual(registered.status_code, 200, registered.json)
        denied = other.post('/desktop/api/conversations/new/messages', json={
            'message': '读取别人数据', 'fileIds': [file['id']]},
            headers={'Idempotency-Key': 'foreign-data-file'})
        self.assertEqual(denied.status_code, 404, denied.json)
        self.assertEqual(other.get(artifact['downloadUrl']).status_code, 404)

    def test_large_csv_reaches_real_wasi_with_last_row(self):
        if not self.sub_agents.executor.status()['available']:
            self.skipTest('Verified WASI runtime unavailable')
        rows = 70_000
        raw = self.csv_bytes(rows, width=64)
        self.assertGreater(len(raw), 4 * 1024 * 1024)
        self.assertLess(len(raw), 10 * 1024 * 1024)
        file = self.upload(raw, 'large.csv')

        def model(stage, payload, *_):
            if stage == 'plan':
                return {'mode': 'delegate', 'goal': '计算已授权CSV中的实际行数与合计',
                        'deliverables': ['精确行数与合计'], 'acceptanceCriteria': ['尾行参与计算'],
                        'constraints': ['只读用户选定文件'], 'requiredExtensions': [],
                        'allowTemporaryCode': True}
            if stage == 'act':
                if not payload['steps']:
                    artifact = next(a for a in payload['artifacts'] if a.get('inputSource') == 'data_management')
                    return {'type': 'code', 'reason': '读取完整CSV而非预览',
                            'inputRefs': [artifact['id']], 'code': CODE}
                return {'type': 'finish', 'answer': '已读取完整CSV并计算。',
                        'artifactIds': [], 'resultStepId': payload['steps'][0]['id']}
            if stage == 'approve':
                return {'decision': 'approve', 'reason': '只读授权CSV并返回聚合值'}
            if stage == 'review':
                return {'decision': 'accept', 'answer': '已核对完整文件尾行。'}
            raise AssertionError(stage)

        self.sub_agents.model = SimpleNamespace(call=model)
        run = self.wait(self.submit(file['id'], '计算CSV所有行，必须包括尾行'), timeout=120)
        self.assertEqual(run['status'], 'succeeded', run.get('answer'))
        self.assertEqual(len(run['steps']), 1)
        result = run['steps'][0]['result']['content']
        self.assertEqual(result, {'rows': rows + 1, 'total': rows + 7, 'tailSeen': True})
        artifact = next(a for a in run['artifacts'] if a.get('inputSource') == 'data_management')
        self.assertEqual(artifact['size'], len(raw))
        self.assertEqual(self.sub_agents.store.artifact(self.owner, artifact['id'])[1], raw)
        self.assertLessEqual(len(artifact['preview']), 4000)

    def test_file_grants_keep_owner_hash_and_non_data_limit(self):
        raw = self.csv_bytes(38_000, width=80)
        self.assertGreater(len(raw), 2 * 1024 * 1024)
        data_file = self.upload(raw, 'a' * 190 + '.csv')
        ordinary = self.upload(raw, 'ordinary.csv', data=False)
        grant = {'permissions': {'inputFiles': True}}
        granted = self.capabilities.file_grants(self.owner, [data_file['id']], grant)
        self.assertEqual(granted, {data_file['id'] + '.csv': raw})
        with self.assertRaises(GatewayError) as ordinary_error:
            self.capabilities.file_grants(self.owner, [ordinary['id']], grant)
        self.assertEqual(ordinary_error.exception.status, 413)
        with self.assertRaises(GatewayError) as permission_error:
            self.capabilities.file_grants(self.owner, [data_file['id']], {'permissions': {'inputFiles': False}})
        self.assertEqual(permission_error.exception.status, 403)

        other = self.app.test_client()
        response = other.post('/desktop/api/register', json={
            'email': 'other-runtime@example.invalid', 'password': 'fixture-password'})
        other_owner = response.json['user']['id']
        with self.assertRaises(GatewayError) as owner_error:
            self.capabilities.file_grants(other_owner, [data_file['id']], grant)
        self.assertEqual(owner_error.exception.status, 404)

        target = self.workbench.store.directory(self.owner) / 'files' / data_file['id']
        target.write_bytes(b'X' + raw[1:])
        with self.assertRaises(GatewayError) as changed_error:
            self.capabilities.file_grants(self.owner, [data_file['id']], grant)
        self.assertEqual(changed_error.exception.status, 403)
        target.write_bytes(b'X' * (10 * 1024 * 1024 + 1))
        with self.assertRaises(GatewayError) as over_error:
            self.capabilities.file_grants(self.owner, [data_file['id']], grant)
        self.assertEqual(over_error.exception.status, 413)

    def test_changed_data_and_ordinary_large_file_do_not_enter_sub_agent(self):
        def unexpected(*_):
            raise AssertionError('The model must not see a rejected file')
        self.sub_agents.model = SimpleNamespace(call=unexpected)
        raw = self.csv_bytes(60_000, width=70)
        self.assertGreater(len(raw), 4 * 1024 * 1024)
        ordinary = self.upload(raw, 'ordinary-large.csv', data=False)
        rejected = self.wait(self.submit(ordinary['id']))
        self.assertEqual(rejected['status'], 'failed')
        self.assertEqual(rejected['artifacts'], [])

        data_file = self.upload(b'city,amount\nChengdu,12\n', 'changed.csv')
        target = self.workbench.store.directory(self.owner) / 'files' / data_file['id']
        target.write_bytes(b'city,amount\nChengdu,13\n')
        rejected = self.wait(self.submit(data_file['id']))
        self.assertEqual(rejected['status'], 'failed')
        self.assertEqual(rejected['artifacts'], [])

    def test_ten_mib_boundary_and_non_input_artifact_limit(self):
        over = b'category,amount\n' + b'A,1\n' * (10 * 1024 * 1024 // 4 + 1)
        response = self.client.post('/desktop/api/files', data={
            'purpose': 'data', 'file': (io.BytesIO(over), 'too-large.csv')},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 413, response.json)

        rid = 'size-boundary-fixture'
        with self.sub_agents.store.transaction() as db:
            self.sub_agents.store.put(db, self.owner, 'run', rid, {
                'id': rid, 'conversationId': 'fixture', 'revision': 0,
                'status': 'running', 'artifacts': []})
        large = b'x' * (4 * 1024 * 1024 + 1)
        with self.assertRaises(GatewayError) as output_error:
            self.sub_agents._artifact(self.owner, rid, 'output.csv', large, 'code')
        self.assertEqual(output_error.exception.status, 413)
        with self.assertRaises(GatewayError) as role_error:
            self.sub_agents._artifact(self.owner, rid, 'fake.csv', large, 'upload', ['f'],
                                      role='output', input_source='data_management', preview='fake')
        self.assertEqual(role_error.exception.status, 400)
        self.assertEqual(self.sub_agents.store.run(self.owner, rid)['artifacts'], [])


if __name__ == '__main__':
    unittest.main()
