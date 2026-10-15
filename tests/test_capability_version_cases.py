"""Saved Skill/Tool originals and one-case verification, using isolated data."""
import io
import base64
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library import portable_assets
from backend.app.modules.capability_library.components import install_snapshot, snapshot_from_package
from backend.app.modules.capability_library.contracts import canonical
from backend.app.modules.capability_library.packages import MAX_FILE, component_export, parse_package, zip_bytes
from backend.app.modules.capability_library.service import PUBLIC

ROOT = Path(__file__).resolve().parents[1]
BASE = '/desktop/api/capabilities'
COMMUNITY = '/desktop/api/community/capabilities'


class CapabilityVersionCasesTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / 'data' / 'capability-validation'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-version-cases-', dir=root)
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'GITEE_AI_API_KEY': 'test-only',
                               'AGENT_RUNTIME_DIR': str(ROOT / 'data' / 'agent-runtime')})
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register',
                         json={'email': 'cases-owner@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.lib = self.app.extensions['capability_library']
        self.counter = 0

    def tearDown(self):
        self.app.extensions['workbench_service'].close()
        self.temp.cleanup()

    def post(self, path, body, *, client=None, base=BASE):
        self.counter += 1
        return (client or self.client).post(base + path, json=body,
                                           headers={'Idempotency-Key': str(self.counter)})

    def wait(self, response, *, client=None):
        self.assertIn(response.status_code, (200, 201, 202), response.json)
        job = response.json
        if 'jobId' not in job:
            return job
        end = time.monotonic() + 40
        while job['status'] in {'queued', 'running', 'cancelling'} and time.monotonic() < end:
            time.sleep(.02)
            job = (client or self.client).get(BASE + '/jobs/' + job['jobId']).json
        self.assertEqual(job['status'], 'completed', job)
        return job['result']

    def upload(self, raw, filename='source.zip', cases=None, *, client=None):
        self.counter += 1
        body = {'file': (io.BytesIO(raw), filename)}
        if cases is not None:
            body['testCases'] = canonical(cases)
        return (client or self.client).post(BASE + '/imports', data=body,
                                            headers={'Idempotency-Key': str(self.counter)})

    def sample(self):
        folder = ROOT / 'docs' / 'capability-library' / 'examples' / 'decimal-total'
        return zip_bytes({str(p.relative_to(folder)): p.read_bytes()
                          for p in folder.rglob('*') if p.is_file()})

    def version(self, saved, *, client=None):
        return (client or self.client).get(
            f"{BASE}/assets/{saved['assetId']}/versions/{saved['versionId']}").json

    def test_preview_and_import_freeze_explicit_original_without_running(self):
        folder = ROOT / 'docs' / 'capability-library' / 'examples' / 'decimal-total' / 'original'
        raw = zip_bytes({'tool.json': (folder / 'openai-tool.json').read_bytes(),
                         'tool.py': (folder / 'tool.py').read_bytes()})
        preview = self.client.post(BASE + '/imports/preview',
                                   data={'file': (io.BytesIO(raw), 'native.zip'), 'kind': 'tool'})
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['testCases'][0]['fileIds'], [])
        self.assertIsNone(preview.json['testCases'][0]['expectation'])
        cases = [{'label': '原输入', 'input': {'values': ['0.10', '0.20']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '0.30'}}}]
        saved = self.wait(self.upload(raw, 'native.zip', cases))
        version = self.version(saved)
        self.assertEqual(version['testCases'], cases)
        self.assertEqual(version['readiness'], 'unverified')
        self.assertFalse(self.client.get(BASE + '/assets/' + saved['assetId']).json['enabled'])
        self.assertEqual(self.lib.store.blob(self.owner, version['originalId']), raw)
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])

        duplicate = self.wait(self.upload(raw, 'native.zip', cases))
        self.assertTrue(duplicate['deduplicated'])
        self.assertEqual(duplicate['versionId'], saved['versionId'])
        changed = self.wait(self.upload(raw, 'native.zip',
            [{'label': '另一输入', 'input': {'values': ['1', '2']}, 'fileIds': [], 'expectation': None}]))
        self.assertFalse(changed['deduplicated'])
        self.assertNotEqual(changed['versionId'], saved['versionId'])
        self.assertEqual(self.version(changed)['contentDigest'], version['contentDigest'])
        self.assertIsNone(self.version(changed)['testCases'][0]['expectation'])
        self.assertEqual(self.version(saved)['testCases'], cases)

    def test_one_original_case_real_wasi_passes_enable_and_publish(self):
        saved = self.wait(self.upload(self.sample()))
        version = self.version(saved)
        self.assertEqual(version['testCases'][0]['expectation'],
                         {'mode': 'json_equals', 'value': {'total': '0.30'}})
        self.assertEqual(self.post('/validation-suites', {
            'assetId': saved['assetId'], 'versionId': saved['versionId'],
            'configurationId': version['configurationId'],
            'cases': [{**version['testCases'][0], 'expectation': None}],
        }).status_code, 400)
        checked = self.wait(self.post('/validation-suites', {
            'assetId': saved['assetId'], 'versionId': saved['versionId'],
            'configurationId': version['configurationId'], 'cases': version['testCases'][:1],
        }))
        self.assertEqual(checked['status'], 'passed')
        edited = self.wait(self.upload(self.sample(), 'source.zip', [
            {'label': '后来编辑的原例', 'input': {'values': ['1', '3']}, 'fileIds': [],
             'expectation': {'mode': 'json_equals', 'value': {'total': '4.00'}}}]))
        self.assertNotEqual(edited['versionId'], saved['versionId'])
        self.assertEqual(self.version(edited)['contentDigest'], version['contentDigest'])
        self.assertEqual(self.version(edited)['readiness'], 'unverified')
        self.assertIsNone(self.lib.evidence(self.owner,
            self.lib.version(self.owner, edited['assetId'], edited['versionId'])))
        asset_response = self.client.get(BASE + '/assets/' + saved['assetId'])
        enabled = self.client.patch(BASE + '/assets/' + saved['assetId'],
                                    json={'enabled': True, 'activeVersionId': saved['versionId']},
                                    headers={'If-Match': asset_response.headers['ETag']})
        self.assertEqual(enabled.status_code, 200, enabled.json)
        preview = self.post('/preview', {'assetId': saved['assetId'], 'versionId': saved['versionId']},
                            base=COMMUNITY)
        self.assertEqual(preview.status_code, 200, preview.json)
        release = self.post('', {'assetId': saved['assetId'], 'versionId': saved['versionId'],
                                 'snapshotDigest': preview.json['snapshotDigest'],
                                 'confirmSharing': True}, base=COMMUNITY)
        self.assertEqual(release.status_code, 201, release.json)

        reader = self.app.test_client()
        reader.post('/desktop/api/register',
                    json={'email': 'cases-reader@example.invalid', 'password': 'test-password'})
        copied = self.post('/' + release.json['releaseId'] + '/copy',
                           {'ensureOwned': True}, client=reader, base=COMMUNITY)
        self.assertEqual(copied.status_code, 201, copied.json)
        self.assertEqual(self.version(copied.json, client=reader)['testCases'], version['testCases'])
        self.assertFalse(reader.get(BASE + '/assets/' + copied.json['assetId']).json['enabled'])
        self.assertEqual(reader.get(
            f"{BASE}/assets/{saved['assetId']}/versions/{saved['versionId']}").status_code, 404)
        reader_owner = reader.get('/desktop/api/session').json['user']['id']
        private_edit = self.lib.import_package(reader_owner, self.sample(), 'source.zip',
            target=copied.json['assetId'], test_cases=[{
                'label': '私人修改的原例', 'input': {'values': ['2', '2']}, 'fileIds': [],
                'expectation': {'mode': 'json_equals', 'value': {'total': '4.00'}}}])
        self.assertNotEqual(private_edit['versionId'], copied.json['versionId'])
        self.assertTrue(self.version(private_edit, client=reader)['admission']['validationRequired'])
        self.assertEqual(self.version(private_edit, client=reader)['readiness'], 'unverified')

    def test_skill_without_example_gets_static_prompt_and_unfilled_criteria(self):
        raw = ('---\nname: synthetic-summary\ndescription: 归纳给定合成事实\n---\n'
               '只归纳用户明确给出的事实，指出未提供的信息。').encode()
        saved = self.wait(self.upload(raw, 'SKILL.md'))
        cases = self.version(saved)['testCases']
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0]['input'], {})
        self.assertIn('归纳给定合成事实', cases[0]['expectation']['prompt'])
        self.assertEqual(cases[0]['expectation']['criteria'], '')
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        version = self.version(saved)
        rejected = self.post('/validation-suites', {
            'assetId': saved['assetId'], 'versionId': saved['versionId'],
            'configurationId': version['configurationId'], 'cases': cases,
        })
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

        completed = [{**cases[0], 'expectation': {**cases[0]['expectation'],
                                                  'criteria': '仅包含用户给出的事实，不编造。'}}]
        with patch('backend.app.modules.capability_library.skill_validation.collect_skill',
                   return_value={'content': {'summary': '仅记录已知事实'}, 'sources': [], 'artifacts': []}):
            checked = self.wait(self.post('/validation-suites', {
                'assetId': saved['assetId'], 'versionId': saved['versionId'],
                'configurationId': version['configurationId'], 'cases': completed,
            }))
        self.assertEqual(checked['status'], 'awaiting_review')
        reviewed = self.post('/verifications/' + checked['verificationId'] + '/review',
                             {'decision': 'accept'})
        self.assertEqual(reviewed.status_code, 200, reviewed.json)
        self.assertEqual(reviewed.json['status'], 'passed')
        asset_response = self.client.get(BASE + '/assets/' + saved['assetId'])
        enabled = self.client.patch(BASE + '/assets/' + saved['assetId'],
                                    json={'enabled': True, 'activeVersionId': saved['versionId']},
                                    headers={'If-Match': asset_response.headers['ETag']})
        self.assertEqual(enabled.status_code, 200, enabled.json)
        self.assertEqual(self.version(saved)['testCases'], cases)

    def test_official_copy_keeps_exact_declared_original_without_execution(self):
        public = self.client.get(BASE + '/assets?scope=public&kind=tool&limit=100')
        self.assertEqual(public.status_code, 200, public.json)
        self.assertTrue(public.json['items'])
        asset = public.json['items'][0]
        source = self.client.get(
            f"{BASE}/assets/{asset['assetId']}/versions/{asset['latestVersionId']}?scope=public")
        self.assertEqual(source.status_code, 200, source.json)
        copied = self.wait(self.post('/official-preparations', {
            'assetId': asset['assetId'], 'versionId': asset['latestVersionId'],
            'ensureOwned': True,
        }))
        version = self.version(copied)
        self.assertEqual(version['testCases'], source.json['testCases'])
        self.assertEqual(version['admission']['validationRequired'], False)
        self.assertFalse(self.client.get(BASE + '/assets/' + copied['assetId']).json['enabled'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])
        public_version = self.lib.version(PUBLIC, asset['assetId'], asset['latestVersionId'])
        raw = zip_bytes({**self.lib.files(PUBLIC, public_version),
                         'capability.json': canonical(public_version['manifest']).encode()})
        changed = self.lib.import_package(self.owner, raw, 'official-source.zip',
            target=copied['assetId'], test_cases=[{
                'label': '私人修改的原例', 'input': {}, 'fileIds': [], 'expectation': None}])
        self.assertNotEqual(changed['versionId'], copied['versionId'])
        self.assertTrue(self.version(changed)['admission']['validationRequired'])
        self.assertEqual(self.version(changed)['readiness'], 'unverified')

    def test_official_install_does_not_grant_a_private_case_version(self):
        public = self.client.get(BASE + '/assets?scope=public&kind=tool&limit=100').json['items'][0]
        source = self.lib.version(PUBLIC, public['assetId'], public['latestVersionId'])
        raw = zip_bytes({**self.lib.files(PUBLIC, source),
                         'capability.json': canonical(source['manifest']).encode()})
        private = self.lib.import_package(self.owner, raw, 'same-source.zip', test_cases=[{
            'label': '私人原例', 'input': {}, 'fileIds': [], 'expectation': None}])
        self.assertTrue(self.version(private)['admission']['validationRequired'])
        copied = self.wait(self.post('/official-preparations', {
            'assetId': source['assetId'], 'versionId': source['versionId'], 'ensureOwned': True}))
        self.assertNotEqual(copied['versionId'], private['versionId'])
        self.assertEqual(self.version(copied)['admission']['source'], 'official')
        self.assertTrue(self.version(private)['admission']['validationRequired'])

    def test_second_case_must_really_differ_if_supplied(self):
        saved = self.wait(self.upload(self.sample()))
        version = self.version(saved)
        original = version['testCases'][0]
        second = {**original, 'label': '不同标签',
                  'expectation': {'mode': 'json_equals', 'value': {'total': '1.00'}}}
        response = self.post('/validation-suites', {
            'assetId': saved['assetId'], 'versionId': saved['versionId'],
            'configurationId': version['configurationId'], 'cases': [original, second],
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json['code'], 'INVALID_REQUEST')
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])

    def test_manual_draft_create_patch_save_carries_original(self):
        manifest, files = parse_package(self.sample(), 'source.zip')
        first = [{'label': '草稿原例', 'input': {'values': ['1', '2']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '3'}}}]
        second = [{'label': '修订原例', 'input': {'values': ['2', '3']}, 'fileIds': [],
                   'expectation': {'mode': 'json_equals', 'value': {'total': '5'}}}]
        third = [{'label': '保存原例', 'input': {'values': ['4', '5']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '9'}}}]
        draft = self.post('/drafts', {'kind': 'tool', 'name': manifest['name'],
            'description': manifest['description'], 'manifest': manifest,
            'files': [{'path': path, 'base64': base64.b64encode(data).decode()}
                      for path, data in files.items()], 'testCases': first})
        self.assertEqual(draft.status_code, 201, draft.json)
        self.assertEqual(draft.json['content']['testCases'], first)
        patched = self.client.patch(BASE + '/drafts/' + draft.json['draftId'],
                                    json={'testCases': second},
                                    headers={'If-Match': '"1"'})
        self.assertEqual(patched.status_code, 200, patched.json)
        self.assertEqual(patched.json['content']['testCases'], second)
        saved = self.wait(self.post('/drafts/' + draft.json['draftId'] + '/save',
                                    {'draftRevision': 2, 'testCases': third}))
        self.assertEqual(self.version(saved)['testCases'], third)
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])

    def test_old_version_get_projects_case_without_mutating_storage(self):
        saved = self.wait(self.upload(self.sample()))
        version = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        version.pop('testCases')
        self.lib.store.put(self.owner, 'version', saved['versionId'], version)
        viewed = self.version(saved)
        self.assertEqual(viewed['testCases'][0]['expectation'],
                         {'mode': 'json_equals', 'value': {'total': '0.30'}})
        original = {'label': '本人历史原例', 'input': {'values': ['9', '1']},
                    'fileIds': [], 'expectation': {'mode': 'json_equals',
                                                  'value': {'total': '10.00'}}}
        self.lib.store.put(self.owner, 'verification', 'check_legacy', {
            'verificationId': 'check_legacy', 'assetId': saved['assetId'],
            'versionId': saved['versionId'], 'origin': 'validation_suite',
            'createdAt': '2026-10-15T00:00:00Z',
            'cases': [{**original, 'status': 'passed',
                       'result': {'content': {'total': '999.00'}},
                       'error': {'code': 'ignored'}}]})
        viewed = self.version(saved)
        self.assertEqual(viewed['testCases'], [original])
        self.assertNotIn('result', viewed['testCases'][0])
        self.assertNotIn('testCases', self.lib.version(self.owner, saved['assetId'], saved['versionId']))
        draft = self.post('/drafts', {'kind': 'tool', 'name': version['manifest']['name'],
                           'baseAssetId': saved['assetId'], 'baseVersionId': saved['versionId']})
        self.assertEqual(draft.status_code, 201, draft.json)
        self.assertEqual(draft.json['content']['testCases'], [original])

    def test_platform_tool_roundtrip_keeps_separate_cases_and_original(self):
        folder = ROOT / 'docs' / 'capability-library' / 'examples' / 'decimal-total' / 'original'
        raw = zip_bytes({'tool.json': (folder / 'openai-tool.json').read_bytes(),
                         'tool.py': (folder / 'tool.py').read_bytes()})
        cases = [{'label': '独立原例', 'input': {'values': ['3.00', '2.00']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '5.00'}}}]
        saved = self.wait(self.upload(raw, 'native.zip', cases))
        exported = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        self.assertEqual(exported['name'], 'capability.zip')
        package = self.lib.store.blob(self.owner, exported['id'])
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertEqual(json.loads(archive.read('test-cases.json')), cases)
            self.assertNotIn('test-cases.json', [row['path'] for row in
                             json.loads(archive.read('capability.json'))['files']])
        preview = self.client.post(BASE + '/imports/preview',
                                   data={'file': (io.BytesIO(package), 'capability.zip')})
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['testCases'], cases)
        reader = self.app.test_client()
        reader.post('/desktop/api/register',
                    json={'email': 'roundtrip-tool@example.invalid', 'password': 'test-password'})
        imported = self.wait(self.upload(package, 'capability.zip', client=reader), client=reader)
        self.assertEqual(self.version(imported, client=reader)['testCases'], cases)
        self.assertFalse(reader.get(BASE + '/assets/' + imported['assetId']).json['enabled'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        original = self.lib.export(self.owner, {**saved, 'format': 'original'})
        self.assertEqual(self.lib.store.blob(self.owner, original['id']), raw)

    def test_platform_skill_roundtrip_keeps_cases_but_agent_skill_stays_pure(self):
        raw = ('---\nname: synthetic-summary\ndescription: 归纳给定合成事实\n---\n'
               '只归纳用户明确给出的事实，指出未提供的信息。').encode()
        cases = [{'label': '独立任务', 'input': {}, 'fileIds': [],
                  'expectation': {'mode': 'manual_review', 'prompt': '请归纳A和B。',
                                  'criteria': '只提及A和B。'}}]
        saved = self.wait(self.upload(raw, 'SKILL.md', cases))
        standard = self.lib.export(self.owner, {**saved, 'format': 'agent_skills'})
        with zipfile.ZipFile(io.BytesIO(self.lib.store.blob(self.owner, standard['id']))) as archive:
            self.assertEqual(archive.namelist(), ['SKILL.md'])
            self.assertEqual(archive.read('SKILL.md'), raw)
        platform = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        package = self.lib.store.blob(self.owner, platform['id'])
        reader = self.app.test_client()
        reader.post('/desktop/api/register',
                    json={'email': 'roundtrip-skill@example.invalid', 'password': 'test-password'})
        imported = self.wait(self.upload(package, 'capability.zip', client=reader), client=reader)
        self.assertEqual(self.version(imported, client=reader)['testCases'], cases)

    def test_platform_export_removes_account_file_grants_without_losing_expectation(self):
        saved = self.wait(self.upload(self.sample()))
        record = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        original = record['testCases'][0]['expectation']
        record['testCases'][0]['fileIds'] = ['private-file-grant-id']
        self.lib.store.put(self.owner, 'version', saved['versionId'], record)
        exported = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        package = self.lib.store.blob(self.owner, exported['id'])
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            sidecar = archive.read('test-cases.json')
            portable = json.loads(sidecar)
        self.assertNotIn(b'private-file-grant-id', sidecar)
        self.assertEqual(portable[0]['fileIds'], [])
        self.assertEqual(portable[0]['expectation'], original)
        self.assertEqual(self.lib.version(self.owner, saved['assetId'], saved['versionId'])
                         ['testCases'][0]['fileIds'], ['private-file-grant-id'])

    def test_embedded_component_zip_roundtrip_preserves_cases_and_old_json_import(self):
        manifest, files = parse_package(self.sample(), 'source.zip')
        cases = [{'label': '组件独立原例', 'input': {'values': ['4', '6']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '10'}}}]
        draft = self.post('/drafts', {'kind': 'tool', 'name': manifest['name'],
            'description': manifest['description'], 'manifest': manifest,
            'files': [{'path': path, 'base64': base64.b64encode(data).decode()}
                      for path, data in files.items()], 'testCases': cases})
        self.assertEqual(draft.status_code, 201, draft.json)
        saved = self.wait(self.post('/drafts/' + draft.json['draftId'] + '/save',
                                    {'draftRevision': draft.json['revision']}))
        exported = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        self.assertEqual(exported['name'], 'component.zip')
        package = self.lib.store.blob(self.owner, exported['id'])
        snapshot, encoded_cases = component_export(package)
        self.assertEqual(encoded_cases, cases)
        self.assertEqual(snapshot['format'], 'cd80.component/1')
        preview = self.client.post(BASE + '/imports/preview',
                                   data={'file': (io.BytesIO(package), 'component.zip')})
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['testCases'], cases)
        reader = self.app.test_client()
        reader.post('/desktop/api/register',
                    json={'email': 'roundtrip-component@example.invalid', 'password': 'test-password'})
        imported = self.wait(self.upload(package, 'component.zip', client=reader), client=reader)
        version = self.version(imported, client=reader)
        self.assertEqual(version['testCases'], cases)
        self.assertEqual(version['embeddedSnapshot']['snapshotDigest'], snapshot['snapshotDigest'])
        self.assertFalse(reader.get(BASE + '/assets/' + imported['assetId']).json['enabled'])
        old = self.wait(self.upload(canonical(snapshot).encode(), 'component.json', client=reader),
                        client=reader)
        self.assertEqual(self.version(old, client=reader)['embeddedSnapshot']['snapshotDigest'],
                         snapshot['snapshotDigest'])

    def test_portable_original_prepare_keeps_platform_sidecar_and_draft_cases(self):
        manifest, files = parse_package(self.sample(), 'source.zip')
        cases = [{'label': '准备原例', 'input': {'values': ['8', '2']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '10'}}}]
        saved = self.wait(self.upload(self.sample(), 'source.zip', cases))
        exported = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        raw = self.lib.store.blob(self.owner, exported['id'])
        original = portable_assets.import_original(self.lib, self.owner, raw,
                                                    'capability.zip', 'tool', 'portable-platform')
        source = portable_assets.read_source(self.lib, self.owner, original['id'])
        self.assertEqual(source['content']['draft']['testCases'], cases)
        self.assertEqual(source['content']['draft']['manifest']['files'], manifest['files'])
        draft = self.lib.draft(self.owner, source['content']['draft'])
        prepared = self.lib.save_draft(self.owner, draft['draftId'],
                                       {'draftRevision': draft['revision']})
        self.assertEqual(self.version(prepared)['testCases'], cases)
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])

    def test_large_component_zip_and_portable_prepare_preserve_case(self):
        manifest, files = parse_package(self.sample(), 'source.zip')
        files.update({'resources/large-a.txt': b'A' * 650_000,
                      'resources/large-b.txt': b'B' * 650_000})
        cases = [{'label': '大型组件原例', 'input': {'values': ['1', '2']}, 'fileIds': [],
                  'expectation': {'mode': 'json_equals', 'value': {'total': '3'}}}]
        saved = install_snapshot(self.lib, self.owner, snapshot_from_package(manifest, files),
                                 test_cases=cases)
        exported = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        package = self.lib.store.blob(self.owner, exported['id'])
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            self.assertGreater(len(archive.read('component.json')), MAX_FILE)
        original = portable_assets.import_original(self.lib, self.owner, package,
                                                    'component.zip', 'tool', 'large-component')
        source = portable_assets.read_source(self.lib, self.owner, original['id'])
        self.assertEqual(source['content']['draft']['testCases'], cases)
        self.assertEqual(source['content']['draft']['dependencySnapshots'], [])
        draft = self.lib.draft(self.owner, source['content']['draft'])
        prepared = self.lib.save_draft(self.owner, draft['draftId'],
                                       {'draftRevision': draft['revision']})
        self.assertEqual(self.version(prepared)['testCases'], cases)
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])


if __name__ == '__main__':
    unittest.main()
