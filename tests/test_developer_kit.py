"""The downloadable third-party starter must actually import and run."""
import io
import json
import posixpath
import re
import tempfile
import time
import unittest
import zipfile
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from backend.app import create_app
from backend.app.modules.agent_platform.planner import Planner
from tests.test_agent_presets import SummaryProvider


class DeveloperKitTests(unittest.TestCase):
    def test_downloaded_documentation_is_portable_and_preserves_starter_bytes(self):
        with tempfile.TemporaryDirectory(prefix='cd80-kit-docs-') as root:
            app = create_app({'TESTING': True, 'DATA_DIR': root, 'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
            try:
                client = app.test_client()
                client.post('/desktop/api/register', json={'email': 'kit-docs@example.invalid', 'password': 'fixture-password'})
                response = client.get('/desktop/api/evolution/developer-kit')
                self.assertEqual(response.status_code, 200)
                project = Path(__file__).resolve().parents[1]
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    names = set(archive.namelist())
                    self.assertTrue({
                        'docs/capability-library/openapi.yaml',
                        'docs/capability-library/社区发布设计与技术规范.md',
                        'docs/agent-platform/应用包与组件复用.md',
                        'docs/vision-delivery/研究应用与发布接口.md',
                        'docs/asset-authoring/开发与外部接入指南.md',
                        'docs/asset-authoring/接口与存储契约.md',
                        'docs/asset-authoring/导入导出与运行解耦.md',
                        'capabilities/README.md',
                    } <= names)
                    self.assertEqual(archive.read('docs/capability-library/openapi.yaml'),
                        (project / 'docs/capability-library/openapi.yaml').read_bytes())
                    self.assertIn('sourceFiles', archive.read('docs/capability-library/社区发布设计与技术规范.md').decode('utf-8'))
                    self.assertIn('docs/asset-authoring/开发与外部接入指南.md', archive.read('README.md').decode('utf-8'))
                    self.assertIn('/desktop/api/portable-assets/imports',
                                  archive.read('docs/asset-authoring/开发与外部接入指南.md').decode('utf-8'))
                    checked_links = 0
                    for name in sorted(names):
                        if not name.endswith('.md'):
                            continue
                        # Check the delivered archive, including unmodified Skill resources.
                        for target in re.findall(r'\]\((.*?)\)', archive.read(name).decode('utf-8')):
                            url = urlsplit(target.split()[0].strip('<>'))
                            if url.scheme or url.netloc:
                                continue
                            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path))) if url.path else name
                            with self.subTest(document=name, target=target):
                                self.assertIn(resolved, names)
                                if url.fragment and resolved.endswith('.md'):
                                    headings = re.findall(r'^#{1,6}\s+(.+?)\s*#*$', archive.read(resolved).decode('utf-8'), re.MULTILINE)
                                    anchors = {re.sub(r'[^\w\s-]', '', h).lower().replace(' ', '-') for h in headings}
                                    self.assertIn(unquote(url.fragment), anchors)
                            checked_links += 1
                    self.assertGreater(checked_links, 20)
                    self.assertFalse(any('/验收记录/' in name for name in names))
                    api_docs = archive.read('docs/capability-library/接口规范.md').decode('utf-8')
                    self.assertIn('完整项目参考，资料包未收录：`docs/capability-library/验收记录/', api_docs)
                    self.assertIn('../../capabilities/README.md', archive.read('docs/capability-library/能力包规范.md').decode('utf-8'))
                    for starter in ('decimal-total', 'summary-skill'):
                        with zipfile.ZipFile(io.BytesIO(archive.read(f'capabilities/{starter}.zip'))) as package:
                            for resource in package.namelist():
                                self.assertEqual(archive.read(f'capabilities/{starter}/{resource}'), package.read(resource))
                                if resource.startswith('original/'):
                                    self.assertEqual(package.read(resource),
                                        (project / 'docs/capability-library/examples' / starter / resource).read_bytes())
            finally:
                app.extensions['workbench_service'].close()

    def test_downloaded_capability_starters_import_verify_enable_and_repack(self):
        from backend.app.modules.capability_library.packages import parse_package
        from backend.app.modules.capability_library.asset_validation import verify_suite
        with tempfile.TemporaryDirectory(prefix='cd80-kit-capabilities-') as root:
            app = create_app({'TESTING': True, 'DATA_DIR': root, 'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
            w = app.extensions['workbench_service']
            try:
                client = app.test_client()
                user = client.post('/desktop/api/register', json={'email': 'kit-cap@example.invalid', 'password': 'fixture-password'}).json['user']
                response = client.get('/desktop/api/evolution/developer-kit')
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    self.assertIn('docs/capability-library/schemas/capability.schema.json', archive.namelist())
                    raw = archive.read('capabilities/decimal-total.zip')
                    skill_raw = archive.read('capabilities/summary-skill.zip')
                    unpacked = Path(root) / 'starter'
                    archive.extractall(unpacked)
                lib, owner = w.capabilities, user['id']
                imported = lib.import_package(owner, raw, 'decimal-total.zip')
                self.assertFalse(lib.asset(owner, imported['assetId'])['enabled'])
                version = lib.version(owner, imported['assetId'], imported['versionId'])
                self.assertEqual(version['validationPolicy'], 'original_and_new')
                body = {k: version[k] for k in ('assetId', 'versionId', 'configurationId')}
                body.update(input={'values': ['0.10', '0.20']}, fileIds=[])
                job = verify_suite(lib, owner, {**body, 'cases': [
                    {'label': '原小数求和', 'input': {'values': ['0.10', '0.20']}, 'fileIds': [],
                     'expectation': {'mode': 'json_equals', 'value': {'total': '0.30'}}},
                    {'label': '不同输入保留精度', 'input': {'values': ['1.25', '2.75', '-0.50']}, 'fileIds': [],
                     'expectation': {'mode': 'json_equals', 'value': {'total': '3.50'}}},
                ]})
                until = time.monotonic() + 30
                while job['status'] in {'queued', 'running', 'cancelling'} and time.monotonic() < until:
                    time.sleep(.02)
                    job = lib.store.get(owner, 'job', job['jobId'])
                self.assertEqual(job['status'], 'completed', job)
                self.assertEqual(job['result']['status'], 'passed', job)
                self.assertEqual([case['status'] for case in job['result']['cases']], ['passed', 'passed'])
                asset = lib.asset(owner, imported['assetId'])
                lib.update_asset(owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, f'"{asset["revision"]}"')
                result = lib.invoke(owner, {**body, 'input': {'values': ['1.25', '2.75']}})
                self.assertEqual(result['result']['content'], {'total': '4.00'})
                skill = lib.import_package(owner, skill_raw, 'summary-skill.zip')
                self.assertEqual(skill['readiness'], 'unverified')
                self.assertFalse(lib.asset(owner, skill['assetId'])['enabled'])
                # Exercise the exact distributed script after changing source bytes.
                source = unpacked / 'capabilities/decimal-total/original/tool.py'
                source.write_bytes(source.read_bytes() + b'\n# author edit\n')
                target = unpacked / 'edited.zip'
                subprocess.run([sys.executable, str(unpacked / 'pack_capability.py'),
                    str(unpacked / 'capabilities/decimal-total'), str(target)], check=True, capture_output=True)
                manifest, files = parse_package(target.read_bytes(), 'edited.zip')
                self.assertTrue(files['original/tool.py'].endswith(b'# author edit\n'))
                self.assertEqual(manifest['source']['identity'], version['manifest']['source']['identity'])
                edited = lib.import_package(owner, target.read_bytes(), 'edited.zip', target=imported['assetId'])
                self.assertNotEqual(edited['versionId'], imported['versionId'])
                self.assertEqual(edited['readiness'], 'unverified')
            finally:
                w.close()

    def test_download_import_two_inputs_and_shared_copy(self):
        with tempfile.TemporaryDirectory(prefix='cd80-kit-') as root:
            app = create_app({'TESTING': True, 'DATA_DIR': root, 'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
            w = app.extensions['workbench_service']
            try:
                client = app.test_client()
                self.assertEqual(client.get('/desktop/api/evolution/developer-kit').status_code, 401)
                user = client.post('/desktop/api/register', json={'email': 'kit@example.invalid', 'password': 'fixture-password'}).json['user']
                response = client.get('/desktop/api/evolution/developer-kit')
                self.assertEqual(response.status_code, 200)
                with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
                    package = json.loads(archive.read('csv-summary.application.json'))
                    samples = [archive.read(name) for name in ('sample-a.csv', 'sample-b.csv')]
                    self.assertIn('五分钟样例', archive.read('README.md').decode())
                w.platform.planner = Planner(SummaryProvider())
                saved = w.platform.import_application(user['id'], {'package': package, 'idempotencyKey': 'kit'})
                self.assertEqual(w.platform.repository.list_runs(user['id']), [])
                for index, raw in enumerate(samples):
                    file = client.post('/desktop/api/files', data={'file': (io.BytesIO(raw), 'sample.csv')}, content_type='multipart/form-data').json
                    run = w.platform.create(user['id'], {'mode': 'invoke', 'agentRef': saved['agentRef'],
                       'idempotencyKey': 'run-' + str(index), 'fileIds': [file['id']],
                       'input': {'fileId': file['id'], 'groupBy': 'category', 'valueColumn': 'amount', 'aggregation': 'sum'}})
                    until = time.monotonic() + 20
                    while run['status'] not in {'succeeded', 'failed', 'cancelled'} and time.monotonic() < until:
                        if run['status'] == 'awaiting_confirmation':
                            approval = next(a for a in run['approvals'] if a['status'] == 'pending')
                            w.platform.approve(user['id'], run['id'], approval['id'], {'decision': 'approve',
                               'idempotencyKey': 'approve-' + str(index), 'bindingDigest': approval['bindingDigest']})
                        time.sleep(.03)
                        run = w.platform.repository.get(user['id'], run['id'])
                    self.assertEqual(run['status'], 'succeeded', run.get('error'))
                    self.assertEqual(run['output']['stepOutputs']['aggregate']['total'], ['22', '11'][index])
                e = app.extensions['evolution']
                body = {'kind': 'pipeline', 'agentRef': saved['agentRef']}
                preview = e.preview_publication(user['id'], body)
                release = e.publish(user['id'], {**body, 'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True}, 'publish')
                other = app.test_client().post('/desktop/api/register', json={'email': 'kit-reader@example.invalid', 'password': 'fixture-password'}).json['user']
                copied = e.copy_publication(other['id'], release['id'], {}, 'copy')
                self.assertEqual(copied['kind'], 'pipeline')
                modern = w.asset_apps.detail(other['id'], copied['assetId'])
                self.assertEqual(modern['versionId'], copied['versionId'])
                self.assertEqual(modern['verification']['status'], 'unverified')
                self.assertIsNone(modern['enabledVersionId'])
                self.assertTrue(modern['definition']['nodes'])
                self.assertEqual(modern['provenance']['source']['id'], release['id'])
                self.assertNotEqual(copied['application']['agentRef'], saved['agentRef'])
                self.assertEqual(w.platform.repository.list_runs(other['id']), [])
                metadata = w.platform.repository.application_metadata(other['id'])[copied['application']['agentRef']]
                self.assertEqual(metadata['source']['kind'], 'publication')
                self.assertEqual(metadata['source']['id'], release['id'])
                self.assertEqual(metadata['source']['digest'], release['snapshotDigest'])
            finally:
                w.close()


if __name__ == '__main__':
    unittest.main()
