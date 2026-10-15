"""Recovery and portable byte-preserving export audit with isolated real services."""
import base64
import io
import json
import unittest
import zipfile
from unittest.mock import patch

from backend.app.modules.agent_platform.asset_contracts import definition, delivery_check, resource_files
from backend.app.modules.capability_library.contracts import CapabilityError, digest
from backend.app.modules.sub_agent.png_plot import line_chart
from tests import test_asset_apps as fixtures
from tests import test_asset_apps_skill_integration as skill_fixtures


class AssetAppsRecoveryAuditTests(unittest.TestCase):
    setUp = fixtures.AssetAppTests.setUp
    tearDown = fixtures.AssetAppTests.tearDown
    post = fixtures.AssetAppTests.post
    save = fixtures.AssetAppTests.save
    wait_run = fixtures.AssetAppTests.wait_run
    verify = fixtures.AssetAppTests.verify
    enable = fixtures.AssetAppTests.enable

    def checkpoint(self):
        doc = fixtures.sample()
        doc['nodes'][0]['checkpoint'] = True
        app = self.enable(self.verify(self.save(doc)))
        response = self.post('/' + app['id'] + '/runs', {'input': {'prices': [100, 120, 90, 108]}})
        self.assertEqual(response.status_code, 202, response.json)
        run = self.wait_run(response.json['id'])
        self.assertEqual(run['status'], 'awaiting_confirmation')
        return app, run

    def test_resume_rechecks_disabled_version(self):
        app, run = self.checkpoint()
        response = self.post('/' + app['id'] + '/enable', {'versionId': app['versionId'], 'revision': app['revision'], 'enabled': False})
        self.assertEqual(response.status_code, 200, response.json)
        response = self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision']})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(self.service.run(self.owner, run['id'])['status'], 'awaiting_confirmation')

    def test_resume_rechecks_original_execution_context(self):
        app, run = self.checkpoint()
        executor = self.w.platform.executor
        old = executor.fuel
        try:
            executor.fuel += 1
            response = self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision']})
            self.assertEqual(response.status_code, 409, response.json)
        finally:
            executor.fuel = old
        self.assertEqual(self.service.run(self.owner, run['id'])['steps'][1]['status'], 'pending')

    def test_skill_context_tracks_actual_model_endpoint_and_options(self):
        doc = skill_fixtures.pipeline()
        provider = self.w.platform.planner.provider
        initial = self.service._context(self.owner, doc)
        original, options = provider.base, dict(provider.request_options)
        try:
            provider.base = 'https://fixture-changed.example.invalid/v1'
            self.assertNotEqual(self.service._context(self.owner, doc), initial)
            provider.base = original
            provider.request_options = {**options, 'max_tokens': 901}
            self.assertNotEqual(self.service._context(self.owner, doc), initial)
        finally:
            provider.base, provider.request_options = original, options

    def test_empty_resume_does_not_release_missing_inputs(self):
        app = self.enable(self.verify(self.save()))
        response = self.post('/' + app['id'] + '/runs', {'input': {}})
        self.assertEqual(response.status_code, 202, response.json)
        run = response.json
        self.assertEqual(run['status'], 'awaiting_input')
        response = self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision']})
        self.assertEqual(response.status_code, 422, response.json)
        self.assertEqual(self.service.run(self.owner, run['id'])['status'], 'awaiting_input')

    def test_resume_checks_artifact_against_original_accepted_digest(self):
        _, run = self.checkpoint()
        original = run['steps'][0]['artifacts'][0]
        changed = b'[1,2,3,4]'
        with self.service.store.transaction() as db:
            meta = dict(original, sha256=digest(changed), size=len(changed))
            db.execute('UPDATE asset_artifacts SET doc=?,data=? WHERE owner=? AND id=?',
                       (json.dumps(meta), changed, self.owner, original['id']))
        response = self.post('/runs/' + run['id'] + '/resume', {'revision': run['revision']})
        self.assertEqual(response.status_code, 409, response.json)

    def test_regular_run_rejects_credential_inputs(self):
        app = self.enable(self.verify(self.save()))
        response = self.post('/' + app['id'] + '/runs', {'input': {'prices': [1, 2], 'api_key': 'private-input-value'}})
        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(response.json['code'], 'INPUT_CONTAINS_CREDENTIAL')
        self.assertNotIn('private-input-value', response.get_data(as_text=True))

    def test_reuploaded_identical_bytes_are_not_a_different_validation_case(self):
        uploaded = [self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'[100,120,90,108]'), 'prices.json')}).json
                    for _ in range(2)]
        # The upload service may deduplicate equal bytes; neither identity shape
        # is allowed to count the same actual input as a second validation case.
        doc = fixtures.sample()
        doc['inputSchema'] = {'type': 'object', 'properties': {}, 'additionalProperties': False}
        doc['nodes'][0]['inputBindings'] = {'source': {'from': 'file', 'input': 'source'}}
        doc['nodes'][0]['code'] = 'import json\ndef run(payload):\n    with open(payload["source"]) as f: prices=json.load(f)\n    with open("/output/prices.json","w") as f: json.dump(prices,f)\n    return {"count":len(prices)}\n'
        app = self.save(doc)
        cases = [{'label': '重复字节' + str(index), 'input': {}, 'fileBindings': {'source': item['id']},
                  'expectation': {'mode': 'fields', 'values': {'analysis.change': 8}}} for index, item in enumerate(uploaded)]
        response = self.post('/' + app['id'] + '/verify', {'versionId': app['versionId'], 'cases': cases})
        self.assertEqual(response.status_code, 400, response.json)

    def test_binary_resources_export_and_import_without_execution_or_loss(self):
        doc = fixtures.sample()
        png = line_chart(['A', 'B'], [1, 2])
        raw = b'\x00\x81\xfe\xfffont-resource\x00'
        doc['nodes'][0]['files'] = [
            {'path': 'assets/example.png', 'base64': base64.b64encode(png).decode()},
            {'path': 'assets/font.bin', 'base64': base64.b64encode(raw).decode()}]
        app = self.save(doc)
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('export/import cannot execute')):
            response = self.client.get('/desktop/api/asset-apps/' + app['id'] + '/export')
            self.assertEqual(response.status_code, 200, response.json)
            exported = response.json
            self.assertEqual(exported['definition']['nodes'][0]['files'], doc['nodes'][0]['files'])
            imported = self.post('/import', {'package': exported}, client=self.other)
        self.assertEqual(imported.status_code, 201, imported.json)
        self.assertEqual(imported.json['definition']['nodes'][0]['files'], doc['nodes'][0]['files'])
        self.assertEqual(imported.json['verification']['status'], 'unverified')
        self.assertIsNone(imported.json['enabledVersionId'])

    def test_binary_export_keeps_secret_and_private_reference_checks(self):
        samples = [b'\x00token="secret-real-value"\x80',
                   b'\x00D:\\private\\input.csv\x80',
                   b'\x00/desktop/api/asset-apps/artifacts/private-id\x80']
        for raw in samples:
            with self.subTest(raw=raw):
                doc = fixtures.sample()
                doc['nodes'][0]['files'] = [{'path': 'assets/data.bin', 'base64': base64.b64encode(raw).decode()}]
                app = self.save(doc)
                response = self.client.get('/desktop/api/asset-apps/' + app['id'] + '/export')
                self.assertEqual(response.status_code, 403, response.json)

    def test_compressed_resource_cannot_hide_credentials(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('config.json', '{"api_key":"private-value"}')
        doc = fixtures.sample()
        doc['nodes'][0]['files'] = [{'path': 'assets/config.zip', 'base64': base64.b64encode(buf.getvalue()).decode()}]
        app = self.save(doc)
        response = self.client.get('/desktop/api/asset-apps/' + app['id'] + '/export')
        self.assertEqual(response.status_code, 403, response.json)


class AppFileContractAuditTests(unittest.TestCase):
    def test_reserved_entry_cannot_silently_replace_a_resource(self):
        with self.assertRaises(CapabilityError):
            resource_files([{'path': '__app_entry__.py', 'text': 'old implementation'}])
        doc = fixtures.sample()
        doc['nodes'][0]['files'] = [{'path': '__app_entry__.py', 'text': 'old implementation'}]
        self.assertTrue(definition(doc)[1])

    def test_declared_mime_must_match_actual_file_type(self):
        with self.assertRaises(CapabilityError) as error:
            delivery_check({}, {'result.json': b'{"value":1}'}, {'files': [{'name': 'result.json', 'mediaType': 'application/pdf'}]})
        self.assertEqual(error.exception.code, 'DELIVERY_INVALID')

    def test_png_json_and_csv_content_are_parsed(self):
        valid = line_chart(['A', 'B'], [1, 2])
        delivery_check({}, {'chart.png': valid}, {'files': [{'name': 'chart.png', 'mediaType': 'image/png'}]})
        delivery_check({}, {'data.csv': b'a,b\n1,2\n'}, {'files': ['data.csv']})
        for name, raw in [('chart.png', valid[:-10]), ('chart.png', b'not PNG'), ('data.json', b'{broken'),
                          ('data.json', b'{"value":NaN}'), ('data.csv', b'a,b\n1\n')]:
            with self.subTest(name=name, raw=raw[:16]), self.assertRaises(CapabilityError):
                delivery_check({}, {name: raw}, {'files': [name]})


if __name__ == '__main__':
    unittest.main()
