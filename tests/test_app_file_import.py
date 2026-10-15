"""Complete App uploads are read-only until explicitly saved by their owner."""
import base64
import ast
import copy
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile
from unittest.mock import patch

from jsonschema import Draft202012Validator

from backend.app import create_app
from backend.app.modules.agent_platform.asset_imports import MAX_UPLOAD, template_definition
from backend.app.modules.capability_library.contracts import digest


def archive(entries):
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as value:
            for name, data in entries:
                value.writestr(name, data)
    return out.getvalue()


class AppFileImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='app-file-import-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                               'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'upload@example.invalid', 'password': 'test-password'})
        self.doc = template_definition()
        self.raw = json.dumps(self.doc, ensure_ascii=False).encode()

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def upload(self, raw=None, name='app.json', client=None):
        return (client or self.client).post('/desktop/api/asset-apps/import/file-preview',
            data={'file': (io.BytesIO(self.raw if raw is None else raw), name)}, content_type='multipart/form-data')

    def test_raw_zip_template_and_exported_package_preserve_full_implementation(self):
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('upload must not execute')):
            raw = self.upload()
            self.assertEqual(raw.status_code, 200, raw.json)
            self.assertEqual(raw.json['definition']['nodes'][0]['code'], self.doc['nodes'][0]['code'])
            self.assertEqual(raw.json['issues'], [])
            self.assertEqual(self.client.get('/desktop/api/asset-apps').json['items'], [])
            for entry in ('app.json', 'application.json', 'my-app/app.json'):
                zipped = self.upload(archive([(entry, self.raw)]), 'app.zip')
                self.assertEqual(zipped.status_code, 200, zipped.json)
                self.assertEqual(zipped.json['package'], raw.json['package'])
            package = raw.json['package']
            saved = self.client.post('/desktop/api/asset-apps/import', json={'package': package},
                headers={'Idempotency-Key': 'upload-one'})
            self.assertEqual(saved.status_code, 201, saved.json)
            self.assertEqual(saved.json['testCases'], self.doc['testCases'])
            self.assertEqual(saved.json['verification']['status'], 'unverified')
            self.assertIsNone(saved.json['enabledVersionId'])
            replacement = [{'label': '我补充的原例', 'input': {'a': 4, 'b': 5},
                            'fileBindings': {}, 'expectation': {'mode': 'json_equals',
                                'value': {'result': {'sum': 9}}}}]
            overridden = self.client.post('/desktop/api/asset-apps/import',
                json={'package': package, 'testCases': replacement},
                headers={'Idempotency-Key': 'upload-overridden'})
            self.assertEqual(overridden.status_code, 201, overridden.json)
            self.assertEqual(overridden.json['testCases'], replacement)
            self.assertEqual(overridden.json['definition']['testCases'], replacement)
            replay = self.client.post('/desktop/api/asset-apps/import', json={'package': package},
                headers={'Idempotency-Key': 'upload-one'})
            self.assertEqual(replay.json['id'], saved.json['id'])
            exported = self.client.get('/desktop/api/asset-apps/' + saved.json['id'] + '/export').json
            self.assertEqual(self.upload(json.dumps(exported).encode()).status_code, 200)
            exported['definition']['name'] = 'changed'
            self.assertEqual(self.upload(json.dumps(exported).encode()).status_code, 400)
            template = self.client.get('/desktop/api/asset-apps/import/template.zip')
            self.assertEqual(template.status_code, 200)
            self.assertEqual(self.upload(template.data, 'template.zip').status_code, 200)

    def test_archive_layout_metadata_and_no_silent_resource_loss(self):
        metadata = self.upload(archive([('wrapped/app.json', self.raw), ('.DS_Store', b'finder')]), 'app.zip')
        self.assertEqual(metadata.status_code, 200, metadata.json)
        self.assertTrue(any('元数据' in msg for msg in metadata.json['warnings']))
        for entries in [[], [('a/b/app.json', self.raw)], [('main.json', self.raw)],
                        [('app.json', self.raw), ('source.py', b'def run(p): return p')],
                        [('app.json', self.raw), ('application.json', self.raw)]]:
            with self.subTest(entries=[x[0] for x in entries]):
                self.assertEqual(self.upload(archive(entries), 'app.zip').status_code, 422)

    def test_path_duplicates_links_encryption_and_bomb_are_rejected(self):
        for name in ('../app.json', '/app.json', 'a\\app.json', 'a/../app.json'):
            self.assertEqual(self.upload(archive([(name, self.raw)]), 'app.zip').status_code, 400)
        for names in [('app.json', 'app.json'), ('app.json', 'APP.JSON'), ('dir', 'dir/app.json')]:
            self.assertEqual(self.upload(archive([(n, self.raw) for n in names]), 'app.zip').status_code, 400)
        symlink = zipfile.ZipInfo('app.json')
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.assertEqual(self.upload(archive([(symlink, self.raw)]), 'app.zip').status_code, 400)
        encrypted = bytearray(archive([('app.json', self.raw)]))
        central = encrypted.index(b'PK\x01\x02')
        encrypted[6] |= 1
        encrypted[central + 8] |= 1
        self.assertEqual(self.upload(bytes(encrypted), 'app.zip').status_code, 400)
        self.assertEqual(self.upload(archive([('app.json', b'x' * (1024 * 1024))]), 'app.zip').status_code, 413)
        self.assertEqual(self.upload(archive([(f'{i}.json', b'{}') for i in range(65)]), 'app.zip').status_code, 413)

    def test_large_embedded_resources_remain_complete(self):
        doc = copy.deepcopy(self.doc)
        # Non-ASCII binary avoids accidentally generating a secret/path token.
        payload = bytes(128 + byte % 128 for byte in os.urandom(900 * 1024))
        doc['nodes'][0]['files'] = [{'path': 'assets/example.bin', 'base64': base64.b64encode(payload).decode()}]
        result = self.upload(archive([('app.json', json.dumps(doc).encode())]), 'app.zip')
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(base64.b64decode(result.json['definition']['nodes'][0]['files'][0]['base64']), payload)

    def test_limits_malformed_and_authentication(self):
        self.assertEqual(self.upload(b'{}', 'app.txt').status_code, 400)
        self.assertEqual(self.upload(b'[]').status_code, 400)
        self.assertEqual(self.upload(b'{"format":1,"format":2}').status_code, 400)
        self.assertEqual(self.upload(b'not zip', 'app.zip').status_code, 400)
        self.assertEqual(self.upload(b'').status_code, 413)
        self.assertEqual(self.upload(b'x' * (MAX_UPLOAD + 1)).status_code, 413)
        self.assertEqual(self.upload(client=self.app.test_client()).status_code, 401)
        self.assertEqual(self.app.test_client().get('/desktop/api/asset-apps/import/template.zip').status_code, 401)
        self.assertEqual(self.client.post('/desktop/api/asset-apps/import/file-preview').status_code, 400)

    def test_wrong_field_types_return_blockers_or_explicit_errors(self):
        for field, change, expected_path in [
            ('kind', lambda doc: doc['nodes'][0].update(kind=[]), 'nodes.0.kind'),
            ('binding from', lambda doc: doc['nodes'][0]['inputBindings']['a'].__setitem__('from', []), 'nodes.0.inputBindings.a'),
            ('output step id', lambda doc: doc['outputs']['result'].update(stepId=[]), 'outputs.result'),
            ('incomplete component ref', lambda doc: doc['nodes'][0].update(componentRef={'assetId': 'missing'}), 'nodes.0.componentRef'),
        ]:
            with self.subTest(field=field):
                doc = copy.deepcopy(self.doc)
                change(doc)
                response = self.upload(json.dumps(doc).encode())
                self.assertEqual(response.status_code, 200, response.json)
                self.assertIn(expected_path, [item['path'] for item in response.json['issues']])

        for field, change in [
            ('component list', lambda doc: doc['nodes'][0].update(component=[1])),
            ('component files list', lambda doc: doc['nodes'][0].update(component={'files': [1]})),
            ('code list', lambda doc: doc['nodes'][0].update(code=[1])),
        ]:
            with self.subTest(field=field):
                doc = copy.deepcopy(self.doc)
                change(doc)
                response = self.upload(json.dumps(doc).encode())
                self.assertEqual(response.status_code, 422, response.json)
                self.assertIn(response.json['code'], {'INVALID_DEFINITION', 'INVALID_SNAPSHOT'})

        saved = self.client.post('/desktop/api/asset-apps', json={'definition': self.doc},
                                 headers={'Idempotency-Key': 'bad-expectation-setup'}).json
        cases = [{'input': {'a': 1, 'b': 2}, 'expectation': {'mode': []}},
                 {'input': {'a': 2, 'b': 3}, 'expectation': {'mode': 'json_equals', 'value': {}}}]
        rejected = self.client.post('/desktop/api/asset-apps/' + saved['id'] + '/verify',
                                    json={'versionId': saved['versionId'], 'cases': cases},
                                    headers={'Idempotency-Key': 'bad-expectation'})
        self.assertEqual(rejected.status_code, 400, rejected.json)
        self.assertEqual(rejected.json['code'], 'INVALID_EXPECTATION')
        bad_ref = copy.deepcopy(self.doc)
        bad_ref['nodes'][0]['componentRef'] = {'assetId': 'missing'}
        rejected_ref = self.client.post('/desktop/api/asset-apps', json={'definition': bad_ref},
                                        headers={'Idempotency-Key': 'bad-component-ref'})
        self.assertEqual(rejected_ref.status_code, 422, rejected_ref.json)
        self.assertEqual(rejected_ref.json['code'], 'INVALID_COMPONENT_REF')
        invalid_package = self.client.post('/desktop/api/asset-apps/import/preview', json={'package': [1]})
        self.assertEqual(invalid_package.status_code, 400, invalid_package.json)
        self.assertEqual(invalid_package.json['code'], 'INVALID_PACKAGE')

    def test_machine_contracts_and_downloaded_examples_match_actual_import(self):
        names = ('schema.json', 'specification.json', 'template.json', 'example.py', 'template.zip', 'data-interface.zip')
        downloads = {}
        for name in names:
            response = self.client.get('/desktop/api/asset-apps/import/' + name)
            self.assertEqual(response.status_code, 200, (name, response.json))
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            downloads[name] = response.data
            self.assertEqual(self.app.test_client().get('/desktop/api/asset-apps/import/' + name).status_code, 401)
        schema = json.loads(downloads['schema.json'])
        Draft202012Validator.check_schema(schema)
        template = json.loads(downloads['template.json'])
        Draft202012Validator(schema).validate(template)
        real_app = json.loads((Path(__file__).resolve().parents[1] / 'examples' / 'official-history-app' / 'app.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema).validate(real_app)
        specification = json.loads(downloads['specification.json'])
        self.assertEqual(specification['openapi'], '3.1.0')
        self.assertIn('/desktop/api/asset-apps/import/file-preview', specification['paths'])
        self.assertIn('codeFile', specification['x-cd80-transport']['zip'])
        def refs(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == '$ref' and isinstance(child, str):
                        assert child.startswith('#/'), child
                        pointer = specification
                        for piece in child[2:].split('/'):
                            pointer = pointer[piece.replace('~1', '/').replace('~0', '~')]
                    else:
                        refs(child)
            elif isinstance(value, list):
                for child in value:
                    refs(child)
        refs(specification)
        self.assertEqual(self.upload(downloads['template.json']).status_code, 200)
        py_preview = self.upload(downloads['example.py'], 'example.py')
        self.assertEqual(py_preview.status_code, 200, py_preview.json)
        self.assertEqual(py_preview.json['definition']['nodes'][0]['code'], downloads['example.py'].decode())
        self.assertEqual(py_preview.json['issues'], [])
        with zipfile.ZipFile(io.BytesIO(downloads['template.zip'])) as zipped:
            self.assertEqual(set(zipped.namelist()), {'app.json', 'src/main.py', 'assets/notes.txt'})
        zip_preview = self.upload(downloads['template.zip'], 'template.zip')
        self.assertEqual(zip_preview.status_code, 200, zip_preview.json)
        self.assertEqual(zip_preview.json['issues'], [])
        with zipfile.ZipFile(io.BytesIO(downloads['data-interface.zip'])) as zipped:
            self.assertEqual(set(zipped.namelist()), {'CONTRACT.md', 'openapi.json', 'data_catalog.json'})
            self.assertIn('openapi', json.loads(zipped.read('openapi.json')))
        examples = Path(__file__).resolve().parents[1] / 'examples' / 'app-import-contract'
        self.assertEqual(json.loads((examples / 'complete-app.json').read_text()), template)
        self.assertEqual(json.loads((examples / 'cd80.app-2.schema.json').read_text()), schema)
        self.assertEqual(json.loads((examples / 'app-import.openapi.json').read_text()), specification)
        self.assertEqual((examples / 'single-file.py').read_bytes(), downloads['example.py'])
        self.assertEqual(self.upload((examples / 'export-package.json').read_bytes()).status_code, 200)
        self.assertEqual(self.upload((examples / 'source-app.zip').read_bytes(), 'source-app.zip').status_code, 200)

    def test_python_literal_conversion_is_lossless_and_save_only(self):
        source = self.client.get('/desktop/api/asset-apps/import/example.py').data.decode()
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('preview/save must not execute')):
            preview = self.upload(source.encode(), 'example.py')
            self.assertEqual(preview.status_code, 200, preview.json)
            doc = preview.json['definition']
            self.assertEqual(doc['nodes'][0]['code'], source)
            self.assertEqual(doc['nodes'][0]['inputBindings'], {'a': {'from': 'input', 'path': 'a'}, 'b': {'from': 'input', 'path': 'b'}})
            self.assertEqual(doc['nodes'][0]['delivery']['schema'], ast.literal_eval(ast.parse(source).body[0].value)['outputSchema'])
            saved = self.client.post('/desktop/api/asset-apps/import', json={'package': preview.json['package']},
                headers={'Idempotency-Key': 'python-file-save'})
            self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(saved.json['definition']['nodes'][0]['code'], source)
        self.assertEqual(saved.json['verification']['status'], 'unverified')
        self.assertIsNone(saved.json['enabledVersionId'])
        self.assertEqual(self.app.test_client().get('/desktop/api/asset-apps/' + saved.json['id']).status_code, 401)
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'other-upload@example.invalid', 'password': 'test-password'})
        self.assertEqual(other.get('/desktop/api/asset-apps/' + saved.json['id']).status_code, 404)

    def test_source_zip_resolves_code_and_binary_resources_without_silent_loss(self):
        doc = copy.deepcopy(self.doc)
        code = doc['nodes'][0].pop('code')
        doc['nodes'][0].update(codeFile='src/main.py', resourceFiles=['assets/config.bin', 'src/helper.py'])
        binary = b'\x00\xff\x81\r\n'
        zipped = archive([('wrapper/app.json', json.dumps(doc).encode()),
                          ('wrapper/src/main.py', code.encode()), ('wrapper/assets/config.bin', binary),
                          ('wrapper/src/helper.py', b'VALUE = 3\r\n')])
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('ZIP preview/save must not execute')):
            preview = self.upload(zipped, 'source.zip')
            self.assertEqual(preview.status_code, 200, preview.json)
            normalized = preview.json['definition']
            node = normalized['nodes'][0]
            self.assertNotIn('codeFile', node)
            self.assertNotIn('resourceFiles', node)
            self.assertEqual(node['code'], code)
            self.assertEqual({item['path']: base64.b64decode(item['base64']) for item in node['files']},
                             {'assets/config.bin': binary, 'src/helper.py': b'VALUE = 3\r\n'})
            saved = self.client.post('/desktop/api/asset-apps/import', json={'package': preview.json['package']},
                headers={'Idempotency-Key': 'source-zip-save'})
            self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(saved.json['verification']['status'], 'unverified')
        self.assertIsNone(saved.json['enabledVersionId'])
        self.assertEqual(saved.json['definition']['nodes'][0]['files'], node['files'])

    def test_source_zip_rejects_unsafe_unresolved_duplicate_and_unclaimed_files(self):
        source = copy.deepcopy(self.doc)
        source['nodes'][0].pop('code')
        source['nodes'][0]['codeFile'] = 'src/main.py'
        code = b'def run(payload):\n    return payload\n'
        def zipped(doc, extra=()):
            return self.upload(archive([('app.json', json.dumps(doc).encode()), ('src/main.py', code), *extra]), 'app.zip')
        missing = copy.deepcopy(source)
        missing['nodes'][0]['codeFile'] = 'missing.py'
        self.assertEqual(zipped(missing).json['code'], 'APP_SOURCE_MISSING')
        traversal = copy.deepcopy(source)
        traversal['nodes'][0]['codeFile'] = '../src/main.py'
        self.assertEqual(zipped(traversal).status_code, 400)
        self.assertEqual(zipped(source, [('unclaimed.txt', b'hello')]).json['code'], 'APP_SOURCE_UNDECLARED')
        repeated = copy.deepcopy(source)
        repeated['nodes'][0]['resourceFiles'] = ['src/main.py']
        self.assertEqual(zipped(repeated).json['code'], 'APP_SOURCE_DUPLICATE')
        conflict = copy.deepcopy(source)
        conflict['nodes'][0]['files'] = [{'path': 'assets/data.bin', 'text': 'old'}]
        conflict['nodes'][0]['resourceFiles'] = ['assets/data.bin']
        self.assertEqual(zipped(conflict, [('assets/data.bin', b'new')]).status_code, 422)
        both = copy.deepcopy(source)
        both['nodes'][0]['code'] = code.decode()
        self.assertEqual(zipped(both).status_code, 422)
        no_external_json = self.upload(json.dumps(source).encode())
        self.assertEqual(no_external_json.json['code'], 'APP_SOURCE_MISSING')
        old_package = {'format': 'cd80.app-package/2', 'definition': source, 'digest': '0' * 64}
        self.assertEqual(zipped(old_package).json['code'], 'INVALID_PACKAGE')

    def test_python_dynamic_or_ambiguous_interface_is_rejected_without_execution(self):
        valid = self.client.get('/desktop/api/asset-apps/import/example.py').data.decode()
        cases = [
            'APP_INTERFACE = dict(name="dynamic")\ndef run(payload): return payload\n',
            valid.replace('APP_INTERFACE = {', 'APP_INTERFACE = {', 1) + '\nAPP_INTERFACE = {}\n',
            valid.replace('"a": {"type": "number"}', '"a.b": {"type": "number"}'),
            valid.replace('"required": ["a", "b"]', '"required": ["a"]'),
            valid.replace('"additionalProperties": False', '"additionalProperties": True'),
            valid.replace('"rules": ["返回 a 与 b 的和。"]', '"rules": ("返回 a 与 b 的和。",)'),
            valid.replace('"a": {"type": "number"}', '1: {"type": "number"}'),
            valid.replace('"additionalProperties": False', '"patternProperties": {".*": {"type": "number"}}, "additionalProperties": False'),
            valid.replace('def run(payload):', 'def calculate(payload):'),
        ]
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('parser must not execute')):
            for index, source in enumerate(cases):
                with self.subTest(index=index):
                    response = self.upload(source.encode(), 'external.py')
                    self.assertEqual(response.status_code, 422, response.json)
        self.assertEqual(self.client.get('/desktop/api/asset-apps').json['items'], [])

    def test_representable_runtime_issue_can_be_privately_saved_without_public_share_gate(self):
        doc = copy.deepcopy(self.doc)
        doc['nodes'][0]['code'] = 'api_key = "synthetic-example-token-12345"\n# intentionally no run function\n'
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('private save must not execute')):
            preview = self.upload(json.dumps(doc).encode())
            self.assertEqual(preview.status_code, 200, preview.json)
            self.assertTrue(any(issue['path'] == 'nodes.0.code' for issue in preview.json['issues']))
            saved = self.client.post('/desktop/api/asset-apps/import', json={'package': preview.json['package']},
                headers={'Idempotency-Key': 'runtime-gap-save'})
            self.assertEqual(saved.status_code, 201, saved.json)
        self.assertEqual(saved.json['verification']['status'], 'unverified')
        self.assertIsNone(saved.json['enabledVersionId'])
        self.assertTrue(saved.json['issues'])


if __name__ == '__main__':
    unittest.main()
