"""Standard capability ZIPs remain readable, private and directly importable."""
import io
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from backend.app import create_app
from backend.app.modules.capability_library import community
from backend.app.modules.capability_library.contracts import CapabilityError, canonical, digest
from backend.app.modules.capability_library.packages import component_export, file_index, zip_bytes
from backend.app.modules.capability_library.asset_validation import verify_suite
from backend.app.modules.capability_library.service import PUBLIC
from backend.app.modules.capability_library.standard_exports import export_standard

ROOT = Path(__file__).resolve().parents[1]


class StandardCapabilityExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-standard-export-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'GITEE_AI_API_KEY': 'test-only',
                               'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime')})
        self.lib = self.app.extensions['capability_library']
        self.w = self.app.extensions['workbench_service']
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={
            'email': 'export-owner@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.other = self.app.test_client()
        self.other.post('/desktop/api/register', json={
            'email': 'export-reader@example.invalid', 'password': 'test-password'})
        self.reader = self.other.get('/desktop/api/session').json['user']['id']

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def tool_package(self, extra=None):
        folder = ROOT / 'docs/capability-library/examples/decimal-total'
        files = {path.relative_to(folder).as_posix(): path.read_bytes()
                 for path in folder.rglob('*') if path.is_file()}
        manifest = json.loads(files.pop('capability.json'))
        files.pop('provenance.json', None)
        files.update(extra or {})
        manifest['files'] = file_index(files)
        return zip_bytes({**files, 'capability.json': canonical(manifest).encode('utf-8')})

    @staticmethod
    def entries(raw):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            return {name: archive.read(name) for name in archive.namelist()}

    def test_personal_tool_preserves_readme_source_cases_and_imports_without_running(self):
        original_readme = b'# Original source README\nDo not replace this file.\n'
        cases = [{'label': '原始小数求和', 'input': {'values': ['0.10', '0.20']},
                  'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '0.30'}}}]
        binary_resource = b'\x89PNG\r\n\x1a\n\x00private-example-pixels'
        saved = self.lib.import_package(self.owner, self.tool_package({
            'original/README.md': original_readme,
            'original/chart.png': binary_resource}), 'source.zip', test_cases=cases)
        asset = self.lib.asset(self.owner, saved['assetId'])
        asset['displayName'] = '中文小数工具'
        self.lib.store.put(self.owner, 'asset', asset['assetId'], asset)
        before = len(self.lib.store.list(self.owner, 'version'))
        name, raw = export_standard(self.lib, self.owner, 'tool', 'personal',
                                    saved['assetId'], saved['versionId'])
        self.assertEqual(name, '中文小数工具')
        self.assertEqual(len(self.lib.store.list(self.owner, 'version')), before)
        files = self.entries(raw)
        self.assertIn('中文小数工具', files['readme.md'].decode('utf-8'))
        self.assertIn('原始小数求和', files['readme.md'].decode('utf-8'))
        self.assertEqual(files['implementation/source/root/original/README.md'], original_readme)
        self.assertEqual(files['implementation/source/root/original/chart.png'], binary_resource)
        self.assertIn('二进制资源', files['readme.md'].decode('utf-8'))
        self.assertIn('standard-tool/tool.json', files)
        self.assertEqual(json.loads(files['standard-tool/tool.json'])['input_schema'],
                         self.lib.version(self.owner, saved['assetId'], saved['versionId'])['manifest']['tool']['inputSchema'])
        self.assertEqual(json.loads(files['test-cases.json']), cases)
        component, exported_cases = component_export(raw)
        self.assertEqual(component['manifest']['name'],
                         self.lib.version(self.owner, saved['assetId'], saved['versionId'])['manifest']['name'])
        self.assertEqual(exported_cases, cases)
        imported = self.lib.import_package(self.reader, raw, '中文小数工具.zip')
        imported_asset = self.lib.asset(self.reader, imported['assetId'])
        imported_version = self.lib.version(self.reader, imported['assetId'], imported['versionId'])
        self.assertFalse(imported_asset['enabled'])
        self.assertEqual(imported_version['readiness'], 'unverified')
        self.assertEqual(imported_version['testCases'], cases)
        self.assertEqual(self.lib.files(self.reader, imported_version)['original/README.md'], original_readme)
        self.assertEqual(self.lib.files(self.reader, imported_version)['original/chart.png'], binary_resource)
        self.assertEqual(self.lib.store.list(self.reader, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.reader, 'invocation'), [])

    def test_skill_standard_directory_keeps_original_readme_and_official_closure(self):
        original_readme = b'# Native README\n'
        raw = zip_bytes({'SKILL.md': b'---\nname: plain-summary\ndescription: Summarize a short note.\n---\nFollow the note.\n',
                         'README.md': original_readme,
                         'references/method.md': b'Use only the supplied facts.\n'})
        personal = self.lib.import_package(self.owner, raw, 'native-skill.zip')
        _, package = export_standard(self.lib, self.owner, 'skill', 'personal',
                                     personal['assetId'], personal['versionId'])
        files = self.entries(package)
        self.assertEqual(files['standard-skill/README.md'], original_readme)
        self.assertEqual(files['implementation/source/root/original/README.md'], original_readme)
        self.assertIn('standard-skill/SKILL.md', files)
        self.assertIn('standard-skill/references/method.md', files)
        self.assertIn('Follow the note.', files['readme.md'].decode('utf-8'))
        self.assertIn('Native README', files['readme.md'].decode('utf-8'))
        imported = self.lib.import_package(self.reader, package, 'plain-summary.zip')
        self.assertEqual(self.lib.version(self.reader, imported['assetId'], imported['versionId'])['readiness'],
                         'unverified')

        public_assets = self.lib.store.list(PUBLIC, 'asset')
        skill = next(asset for asset in public_assets if asset['kind'] == 'skill')
        tool = next(asset for asset in public_assets if asset['kind'] == 'tool')
        name, package = export_standard(self.lib, self.reader, 'skill', 'official',
                                        skill['assetId'], skill['latestVersionId'])
        self.assertEqual(name, skill['name'])
        snapshot, _ = component_export(package)
        self.assertEqual(snapshot['manifest']['kind'], 'skill')
        self.assertEqual(snapshot['manifest']['source']['identity'],
                         self.lib.version(PUBLIC, skill['assetId'], skill['latestVersionId'])['manifest']['source']['identity'])
        self.assertEqual(self.entries(package)['component.json'], canonical(snapshot).encode('utf-8'))
        self.assertEqual(self.lib.store.list(self.reader, 'invocation'), [])

        dependency = self.lib.import_package(self.owner, self.tool_package(), 'decimal-total.zip')
        source = ROOT / 'docs/capability-library/examples/summary-skill'
        resource_files = {path.relative_to(source).as_posix(): path.read_bytes()
                          for path in source.rglob('*') if path.is_file()}
        manifest = json.loads(resource_files.pop('capability.json'))
        resource_files.pop('provenance.json', None)
        dependency_version = self.lib.version(self.owner, dependency['assetId'], dependency['versionId'])
        manifest['dependencies'] = [{'sourceIdentity': dependency_version['manifest']['source']['identity'],
                                     'contentDigest': dependency_version['contentDigest'], 'required': True,
                                     'reason': 'Use the exact decimal tool.'}]
        manifest['files'] = file_index(resource_files)
        dependent = self.lib.import_package(self.owner, zip_bytes({
            **resource_files, 'capability.json': canonical(manifest).encode('utf-8')}), 'dependent-skill.zip')
        _, dependent_zip = export_standard(self.lib, self.owner, 'skill', 'personal',
                                           dependent['assetId'], dependent['versionId'])
        dependent_snapshot, _ = component_export(dependent_zip)
        self.assertEqual(len(dependent_snapshot['dependencies']), 1)
        self.assertIn('implementation/source/dependency-1/original/tool.py', self.entries(dependent_zip))

    def test_community_export_uses_only_frozen_public_snapshot(self):
        source = self.lib.import_package(self.owner, self.tool_package(), 'source.zip',
            test_cases=[{'label': 'Private author case only', 'input': {'values': ['0.10', '0.20']},
                         'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '0.30'}}}])
        version = self.lib.version(self.owner, source['assetId'], source['versionId'])
        job = verify_suite(self.lib, self.owner, {
            'assetId': source['assetId'], 'versionId': source['versionId'],
            'configurationId': version['configurationId'],
            'cases': [
                {'label': 'a', 'input': {'values': ['0.10', '0.20']}, 'fileIds': [],
                 'expectation': {'mode': 'json_equals', 'value': {'total': '0.30'}}},
                {'label': 'b', 'input': {'values': ['1.20', '2.30']}, 'fileIds': [],
                 'expectation': {'mode': 'json_equals', 'value': {'total': '3.50'}}},
            ]})
        limit = time.monotonic() + 30
        while job['status'] in {'queued', 'running'} and time.monotonic() < limit:
            time.sleep(.02)
            job = self.lib.store.get(self.owner, 'job', job['jobId'])
        self.assertEqual(job['status'], 'completed', job)
        self.assertEqual(job['result']['status'], 'passed')
        body = {'assetId': source['assetId'], 'versionId': source['versionId'],
                'title': '公开小数工具', 'description': '只公开准确的计算源码。'}
        preview = community.preview(self.lib, self.owner, body)
        release = community.publish(self.lib, self.owner, {
            **body, 'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True})
        release_id = release['releaseId']
        name, package = export_standard(self.lib, self.reader, 'tool', 'community', release_id)
        self.assertEqual(name, '公开小数工具')
        self.assertNotIn('Private author case only', package.decode('latin1'))
        files = self.entries(package)
        self.assertNotIn('Private author case only', files['readme.md'].decode('utf-8'))
        self.assertNotIn(self.owner, files['readme.md'].decode('utf-8'))
        component, cases = component_export(package)
        self.assertEqual(component['contentDigest'], release['contentDigest'])
        self.assertEqual(cases, json.loads(files['test-cases.json']) if 'test-cases.json' in files else None)
        imported = self.lib.import_package(self.reader, package, 'community.zip')
        self.assertFalse(self.lib.asset(self.reader, imported['assetId'])['enabled'])
        self.assertEqual(self.lib.version(self.reader, imported['assetId'], imported['versionId'])['readiness'],
                         'unverified')
        self.assertEqual(self.lib.store.list(self.reader, 'verification'), [])

    def test_cross_account_and_tampered_sidecar_rejected(self):
        source = self.lib.import_package(self.owner, self.tool_package(), 'source.zip')
        with self.assertRaises(CapabilityError) as caught:
            export_standard(self.lib, self.reader, 'tool', 'personal', source['assetId'], source['versionId'])
        self.assertEqual(caught.exception.status, 404)
        _, package = export_standard(self.lib, self.owner, 'tool', 'personal',
                                     source['assetId'], source['versionId'])
        files = self.entries(package)
        files['readme.md'] += b'changed'
        with self.assertRaises(CapabilityError) as changed:
            component_export(zip_bytes(files))
        self.assertEqual(changed.exception.code, 'INVALID_PACKAGE')
        files = self.entries(package)
        files['unexpected/secret.txt'] = b'not indexed'
        with self.assertRaises(CapabilityError) as extra:
            component_export(zip_bytes(files))
        self.assertEqual(extra.exception.code, 'INVALID_PACKAGE')


if __name__ == '__main__':
    unittest.main()
