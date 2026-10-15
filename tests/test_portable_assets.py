"""External originals survive unsupported runtimes; all state uses temporary DBs."""
import base64
import io
import json
import stat
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library import portable_assets
from backend.app.modules.capability_library.components import snapshot_from_package
from backend.app.modules.capability_library.contracts import CapabilityError, canonical, digest
from backend.app.modules.capability_library.packages import MAX_FILE, MAX_PACKAGE, file_index, zip_bytes
from backend.app.modules.capability_library.portable_routes import portable_assets as blueprint
from backend.app.modules.capability_library.store import Store

BASE = '/desktop/api/portable-assets'
SKILL = b'---\nname: external-method\ndescription: External resources and method\n---\nUse the supplied resources.\n'


def app_definition():
    return {'format': 'cd80.app/2', 'name': 'External calculation', 'inputSchema': {'type': 'object'},
            'nodes': [{'id': 'calculate', 'kind': 'code', 'code': 'def run(payload): return payload\n',
                       'inputBindings': {}, 'delivery': {'schema': {}}}], 'outputs': {}}


def tool_manifest(files):
    return {'format': 'cd80.capability/1', 'kind': 'tool', 'name': 'portable-tool',
            'description': 'A saved implementation', 'source': {'identity': 'test/portable-tool', 'format': 'platform'},
            'files': file_index(files), 'dependencies': [],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
            'tool': {'inputSchema': {'type': 'object'},
                     'execution': {'type': 'python_wasi', 'path': 'tool.py', 'callable': 'run'}}}


class PortableAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-portable-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                               'GITEE_AI_API_KEY': 'fixture-only'})
        if 'portable_assets' not in self.app.blueprints:
            self.app.register_blueprint(blueprint)
        self.w = self.app.extensions['workbench_service']
        self.lib = self.app.extensions['capability_library']
        self.client, self.other = self.app.test_client(), self.app.test_client()
        for number, client in enumerate((self.client, self.other)):
            self.assertEqual(client.post('/desktop/api/register', json={
                'email': f'portable{number}@example.invalid', 'password': 'test-password'}).status_code, 200)
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.counter = 0

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def upload(self, raw, kind='skill', filename='external.bin', *, key=None, client=None, name=None):
        self.counter += 1
        data = {'kind': kind, 'file': (io.BytesIO(raw), filename)}
        if name is not None:
            data['name'] = name
        response = (client or self.client).post(BASE + '/imports', data=data,
                                                headers={'Idempotency-Key': key or str(self.counter)})
        # Large multipart fixtures may spill to a temporary input stream.
        response.request.close()
        response.request.input_stream.close()
        return response

    def assert_roundtrip(self, response, raw):
        self.assertEqual(response.status_code, 201, response.json)
        value = response.json
        self.assertEqual(value['status'], 'stored')
        self.assertEqual(value['runtimeReadiness'], 'unprepared')
        self.assertEqual(value['sha256'], digest(raw))
        download = self.client.get(value['exportUrl'])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, raw)
        self.assertEqual(download.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('attachment', download.headers['Content-Disposition'])
        return value

    def test_three_types_preserve_unknown_originals_without_runtime(self):
        binary = bytes(range(256))
        raw_zip = zip_bytes({'third-party/workflow.yaml': b'provider: unsupported\n',
                             'third-party/execute.ps1': b'Write-Output placeholder',
                             'third-party/resources/image.bin': binary})
        with ExitStack() as stack:
            for target, method in ((self.lib, 'invoke'), (self.lib, 'job'), (self.lib, 'configuration'),
                                   (self.w.platform.executor, 'execute')):
                stack.enter_context(patch.object(target, method, side_effect=AssertionError('runtime must not run')))
            for kind in ('app', 'tool', 'skill'):
                for name, raw in (('foreign.bin', binary), ('foreign.zip', raw_zip)):
                    with self.subTest(kind=kind, name=name):
                        value = self.assert_roundtrip(self.upload(raw, kind, name), raw)
                        self.assertEqual(value['sourceFormat'], 'external')
                        self.assertEqual(self.client.get(BASE + '/' + value['id']).json, value)
                        self.assertEqual(self.other.get(value['exportUrl']).status_code, 404)
                        self.assertEqual(self.other.get(BASE + '/' + value['id']).status_code, 404)
        self.assertEqual(len(self.client.get(BASE).json['items']), 6)
        self.assertEqual(len(self.client.get(BASE + '?kind=app').json['items']), 2)
        self.assertEqual(self.other.get(BASE).json['items'], [])
        for record_kind in ('asset', 'version', 'verification', 'invocation', 'connection', 'job'):
            self.assertEqual(self.lib.store.list(self.owner, record_kind), [])

    def test_known_packages_provide_read_only_source_and_keep_binary_resources(self):
        resources = {'bundle/SKILL.md': SKILL, 'bundle/assets/data.bin': bytes(range(256)),
                     'bundle/scripts/tool.js': b'export const unsupported = true;'}
        skill_raw = zip_bytes(resources)
        skill = self.assert_roundtrip(self.upload(skill_raw, filename='method.zip'), skill_raw)
        source = portable_assets.read_source(self.lib, self.owner, skill['id'])
        self.assertEqual(source['sourceFormat'], 'agent_skills')
        draft = source['content']['draft']
        actual = {row['path']: base64.b64decode(row['base64']) for row in draft['files']}
        self.assertEqual(actual['original/assets/data.bin'], bytes(range(256)))
        self.assertEqual(actual['original/scripts/tool.js'], resources['bundle/scripts/tool.js'])
        self.assertTrue(any(issue['code'] == 'UNSUPPORTED_EXECUTION' for issue in source['issues']))
        code_files = {'tool.py': b'def run(payload): return payload\n', 'assets/data.bin': bytes(range(256))}
        component = snapshot_from_package(tool_manifest(code_files), code_files)
        tool_raw = canonical(component).encode()
        tool = self.assert_roundtrip(self.upload(tool_raw, 'tool', 'tool.json'), tool_raw)
        tool_source = portable_assets.read_source(self.lib, self.owner, tool['id'])
        self.assertEqual(tool_source['content']['draft']['kind'], 'tool')
        self.assertEqual(tool_source['content']['draft']['dependencySnapshots'], [])
        doc = app_definition()
        app_raw = canonical({'format': 'cd80.app-package/2', 'definition': doc, 'digest': digest(doc)}).encode()
        app = self.assert_roundtrip(self.upload(app_raw, 'app', 'app.json'), app_raw)
        app_source = portable_assets.read_source(self.lib, self.owner, app['id'])
        self.assertIsInstance(app_source['content']['definition'], dict)
        self.assertEqual(app_source['content']['definition']['nodes'][0]['code'], doc['nodes'][0]['code'])
        self.assertEqual(self.lib.store.list(self.owner, 'asset'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

    def test_unknown_or_mismatched_source_is_retained_with_actionable_gap(self):
        for kind, filename, raw in (('app', 'unknown.json', b'{"format": []}'),
                                    ('skill', 'unknown.json', b'not even json'),
                                    ('tool', 'SKILL.md', SKILL),
                                    ('app', 'wrong.json', b'{"format":"cd80.app-package/2","definition":{},"digest":"bad"}')):
            with self.subTest(kind=kind, filename=filename):
                saved = self.assert_roundtrip(self.upload(raw, kind, filename), raw)
                source = portable_assets.read_source(self.lib, self.owner, saved['id'])
                self.assertIsNone(source['content'])
                self.assertEqual(source['issues'][0]['code'], 'SOURCE_NEEDS_EDIT')
                self.assertEqual(self.client.get(saved['exportUrl']).data, raw)

    def test_idempotency_conflicts_are_owner_scoped_and_never_overwrite(self):
        first = self.upload(b'first', 'app', key='same').json
        replay = self.upload(b'first', 'app', key='same')
        self.assertEqual(replay.json, first)
        for raw, kind, name in ((b'other', 'app', None), (b'first', 'tool', None), (b'first', 'app', 'new name')):
            response = self.upload(raw, kind, key='same', name=name)
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json['code'], 'IDEMPOTENCY_CONFLICT')
        other = self.upload(b'first', 'app', key='same', client=self.other)
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.json['id'], first['id'])
        self.assertEqual(len(self.lib.store.list(self.owner, 'portable_asset')), 1)
        self.assertEqual(self.client.get(first['exportUrl']).data, b'first')

    def test_failed_transaction_rolls_back_original_and_can_retry(self):
        original_put = self.lib.store.put
        def failing_put(owner, kind, key, value):
            if kind == 'portable_request':
                raise RuntimeError('fixture commit interruption')
            return original_put(owner, kind, key, value)
        with patch.object(self.lib.store, 'put', side_effect=failing_put):
            with self.assertRaises(RuntimeError):
                portable_assets.import_original(self.lib, self.owner, b'atomic', 'source.bin', 'app', 'atomic')
        self.assertEqual(self.lib.store.list(self.owner, 'portable_asset'), [])
        with self.assertRaises(CapabilityError):
            self.lib.store.blob(self.owner, digest(b'atomic'))
        self.assert_roundtrip(self.upload(b'atomic', 'app', 'source.bin', key='atomic'), b'atomic')

    def test_restart_reads_original_without_replaying_any_import(self):
        saved = self.upload(b'persisted', 'tool', 'source.external', key='restart').json
        reopened = Store(Path(self.temp.name) / 'capability-library')
        try:
            restored = SimpleNamespace(store=reopened)
            meta, raw = portable_assets.original(restored, self.owner, saved['id'])
            self.assertEqual(meta, saved)
            self.assertEqual(raw, b'persisted')
        finally:
            reopened.close()

    def test_unsafe_archives_and_filename_disguises_are_rejected(self):
        cases = []
        for names in (('../escape.py',), ('C:/private.py',), ('A.txt', 'a.txt'), ('a', 'a/b.txt'),
                      ('\u00e9.txt', 'e\u0301.txt')):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for name in names:
                    archive.writestr(name, b'unsafe fixture')
            cases.append(buffer.getvalue())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            entry = zipfile.ZipInfo('link')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(entry, b'/outside')
        cases.append(buffer.getvalue())
        compressed = bytearray(zip_bytes({'corrupt.txt': b'compress me ' * 100}))
        # Break a real DEFLATE payload while retaining a valid central directory.
        offset = 30 + int.from_bytes(compressed[26:28], 'little') + int.from_bytes(compressed[28:30], 'little')
        compressed[offset:offset + 3] = b'\xff\xff\xff'
        cases.append(bytes(compressed))
        for raw in cases:
            for filename in ('unsafe.zip', 'disguised.bin'):
                with self.subTest(filename=filename, digest=digest(raw)):
                    response = self.upload(raw, filename=filename)
                    self.assertEqual(response.status_code, 400, response.json)
                    self.assertEqual(response.json['code'], 'INVALID_PACKAGE')
        self.assertEqual(self.upload(b'broken', filename='broken.zip').status_code, 400)
        self.assertEqual(self.upload(b'content', filename='../outside.bin').status_code, 400)
        self.assertEqual(self.lib.store.list(self.owner, 'portable_asset'), [])

    def test_limits_and_auth_are_enforced_before_storage(self):
        for raw in (zip_bytes({'huge.bin': b'x' * (MAX_FILE + 1)}),
                    zip_bytes({str(i) + '.txt': b'x' for i in range(501)}),
                    b'x' * (MAX_PACKAGE + 1)):
            self.assertEqual(self.upload(raw).status_code, 413)
        self.assertEqual(self.upload(b'').status_code, 413)
        self.assertEqual(self.upload(b'content', kind='unknown').status_code, 400)
        anonymous = self.app.test_client()
        self.assertEqual(self.upload(b'content', client=anonymous).status_code, 401)
        self.assertEqual(self.lib.store.list(self.owner, 'portable_asset'), [])

    def test_export_detects_changed_original_instead_of_returning_wrong_bytes(self):
        value = self.upload(b'original').json
        self.lib.store.db.execute('UPDATE capability_blobs SET content=? WHERE owner=? AND id=?',
                                  (b'corrupted', self.owner, value['sha256']))
        response = self.client.get(value['exportUrl'])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'ORIGINAL_CHANGED')

    def test_existing_capability_exports_do_not_require_runtime_or_verification(self):
        records = []
        skill_raw = zip_bytes({'SKILL.md': SKILL, 'scripts/unsupported.js': b'export const x = 1;'})
        records.append((self.lib.import_package(self.owner, skill_raw, 'skill.zip'), skill_raw, ('original', 'platform', 'agent_skills')))
        tool_raw = b'{"type":"function","name":"external_tool","parameters":{"type":"object"}}'
        records.append((self.lib.import_package(self.owner, tool_raw, 'tool.json'), tool_raw, ('original', 'platform', 'openai_responses')))
        for ref, raw, formats in records:
            self.assertFalse(self.lib.asset(self.owner, ref['assetId'])['enabled'])
            self.assertNotEqual(ref['readiness'], 'verified')
            with patch.object(self.lib, 'admit', side_effect=AssertionError('no admit on export')), \
                 patch.object(self.lib, 'configuration', side_effect=AssertionError('no environment check on export')):
                for fmt in formats:
                    with self.subTest(kind=ref['assetId'], format=fmt):
                        output = self.lib.export(self.owner, {**ref, 'format': fmt})
                        data = self.client.get(output['downloadUrl']).data
                        self.assertTrue(data)
                        if fmt == 'original':
                            self.assertEqual(data, raw)
            self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

    def test_owner_original_download_does_not_run_secret_conversion_filter(self):
        raw = SKILL + b'\napi_key="synthetic-secret-fixture-123456"\n'
        ref = self.lib.import_package(self.owner, raw, 'SKILL.md')
        output = self.lib.export(self.owner, {**ref, 'format': 'original'})
        self.assertEqual(self.client.get(output['downloadUrl']).data, raw)
        with self.assertRaises(CapabilityError) as caught:
            self.lib.export(self.owner, {**ref, 'format': 'platform'})
        self.assertEqual(caught.exception.code, 'EXPORT_CONTAINS_SECRET')


if __name__ == '__main__':
    unittest.main()
