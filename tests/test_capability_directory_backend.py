"""Exact-version directory ownership and static original-upload contract."""
import io
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from tests import test_capability_library as helpers
from backend.app.modules.capability_library.contracts import digest
from backend.app.modules.capability_library.packages import zip_bytes
from backend.app.modules.capability_library.service import PUBLIC

BASE = helpers.BASE


class CapabilityDirectoryBackendTests(unittest.TestCase):
    setUp = helpers.CapabilityLibraryTests.setUp
    tearDown = helpers.CapabilityLibraryTests.tearDown
    sample = helpers.CapabilityLibraryTests.sample
    post = helpers.CapabilityLibraryTests.post
    wait = helpers.CapabilityLibraryTests.wait
    upload = helpers.CapabilityLibraryTests.upload

    def source(self, identity):
        return next(a for a in self.lib.store.list(PUBLIC, 'asset') if a['sourceIdentity'] == identity)

    def preview(self, raw, filename='tool.zip', kind='tool'):
        return self.client.post(BASE + '/imports/preview', data={
            'file': (io.BytesIO(raw), filename), 'kind': kind}, content_type='multipart/form-data')

    def import_checked(self, raw, sha256, filename='tool.zip', key='checked-import'):
        return self.client.post(BASE + '/imports', data={
            'file': (io.BytesIO(raw), filename), 'expectedDigest': sha256},
            headers={'Idempotency-Key': key}, content_type='multipart/form-data')

    def test_official_ensure_owned_installs_exact_closure_without_execution(self):
        tool = self.source('org.example/decimal-total')
        tool_version = self.lib.version(PUBLIC, tool['assetId'], tool['latestVersionId'])

        def dependency(manifest, _files):
            manifest['dependencies'] = [{'sourceIdentity': tool['sourceIdentity'],
                                         'contentDigest': tool_version['contentDigest'],
                                         'required': True, 'reason': '精确依赖'}]

        with patch('backend.app.modules.capability_library.releases.check_release'):
            source = self.lib.import_package(PUBLIC, self.sample('summary-skill', dependency), 'bundle.zip')
        route = '/assets/' + source['assetId'] + '/copy'
        body = {'versionId': source['versionId'], 'ensureOwned': True}
        first = self.post(route, body, key='first-install')
        self.assertEqual(first.status_code, 201, first.json)
        self.assertFalse(first.json['alreadyOwned'])
        self.assertEqual(len(first.json['dependencies']), 1)
        dependency_copy = first.json['dependencies'][0]
        root = self.lib.version(self.owner, first.json['assetId'], first.json['versionId'])
        self.assertEqual(root['dependencyLocks'][0]['versionId'], dependency_copy['versionId'])
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), 2)
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])
        self.assertTrue(all(not a['enabled'] for a in self.lib.store.list(self.owner, 'asset')))
        second = self.post(route, body, key='second-install')
        self.assertEqual(second.status_code, 201, second.json)
        self.assertTrue(second.json['alreadyOwned'])
        self.assertEqual((second.json['assetId'], second.json['versionId']),
                         (first.json['assetId'], first.json['versionId']))
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), 2)
        public = self.client.get(BASE + '/assets/' + source['assetId'] + '/versions/' + source['versionId'] + '?scope=public').json
        self.assertTrue(public['owned'])
        self.assertEqual(public['personalCopies'][0]['versionId'], first.json['versionId'])

    def test_ensure_owned_repairs_missing_dependency_of_legacy_root_copy(self):
        tool = self.source('org.example/decimal-total')
        tool_version = self.lib.version(PUBLIC, tool['assetId'], tool['latestVersionId'])

        def dependency(manifest, _files):
            manifest['dependencies'] = [{'sourceIdentity': tool['sourceIdentity'],
                                         'contentDigest': tool_version['contentDigest'],
                                         'required': True, 'reason': '精确依赖'}]

        with patch('backend.app.modules.capability_library.releases.check_release'):
            source = self.lib.import_package(PUBLIC, self.sample('summary-skill', dependency), 'bundle.zip')
        route = '/assets/' + source['assetId'] + '/copy'
        legacy = self.post(route, {'versionId': source['versionId']}).json
        self.assertIn('MISSING_DEPENDENCY', [item['code'] for item in legacy['issues']])
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), 1)
        ensured = self.post(route, {'versionId': source['versionId'], 'ensureOwned': True}).json
        self.assertTrue(ensured['alreadyOwned'])
        self.assertEqual(ensured['versionId'], legacy['versionId'])
        self.assertEqual(len(ensured['dependencies']), 1)
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), 2)
        version = self.lib.version(self.owner, legacy['assetId'], legacy['versionId'])
        self.assertEqual(len(version['dependencyLocks']), 1)
        self.assertNotIn('MISSING_DEPENDENCY', [item['code'] for item in version['issues']])

    def test_official_cross_key_parallel_and_account_isolation(self):
        source = self.source('org.example/summary-skill')
        version = self.lib.version(PUBLIC, source['assetId'], source['latestVersionId'])
        from backend.app.modules.capability_library.official import copy_closure
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _i: copy_closure(self.lib, self.owner, version), range(3)))
        self.assertEqual(len({r['assetId'] for r in results}), 1)
        self.assertEqual(sum(not r['alreadyOwned'] for r in results), 1)
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'other-directory@example.invalid', 'password': 'test-password'})
        detail = other.get(BASE + '/assets/' + source['assetId'] + '/versions/' + source['latestVersionId'] + '?scope=public').json
        self.assertFalse(detail['owned'])
        self.assertEqual(detail['personalCopies'], [])

    def test_official_preparation_ensure_owned_is_install_only(self):
        source = self.source('org.example/summary-skill')
        body = {'assetId': source['assetId'], 'versionId': source['latestVersionId'],
                'scope': 'public', 'ensureOwned': True}
        first = self.wait(self.post('/official-preparations', body, key='prepare-install'))
        second = self.wait(self.post('/official-preparations', body, key='prepare-install-again'))
        self.assertFalse(first['alreadyOwned'])
        self.assertTrue(second['alreadyOwned'])
        self.assertEqual(first['assetId'], second['assetId'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])
        self.assertFalse(self.lib.asset(self.owner, first['assetId'])['enabled'])

    def test_preview_and_import_native_tool_zip_retains_original_bytes(self):
        definition = {'name': 'zip_tool', 'description': 'ZIP tool',
                      'input_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                                       'required': ['value']}}
        raw = zip_bytes({'bundle/tool.json': json.dumps(definition).encode(),
                         'bundle/tool.py': b'def run(payload):\n    return {"value": payload["value"]}\n',
                         'bundle/references/readme.txt': b'exact original resource',
                         'bundle/bin/example.dat': b'\x00\x01\x02'})
        before = len(self.lib.store.list(self.owner, 'asset'))
        preview = self.preview(raw)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['sha256'], digest(raw))
        self.assertEqual(preview.json['staticReadiness'], 'unverified')
        self.assertEqual(preview.json['manifest']['tool']['execution']['path'], 'original/tool.py')
        self.assertEqual({item['path'] for item in preview.json['files']},
                         {'original/tool.json', 'original/tool.py', 'original/references/readme.txt',
                          'original/bin/example.dat'})
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), before)
        wrong = self.import_checked(raw, '0' * 64, key='wrong-digest')
        self.assertEqual((wrong.status_code, wrong.json['code']), (409, 'IMPORT_PREVIEW_CHANGED'))
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), before)
        imported = self.wait(self.import_checked(raw, preview.json['sha256']))
        version = self.lib.version(self.owner, imported['assetId'], imported['versionId'])
        self.assertEqual(self.lib.store.blob(self.owner, version['originalId']), raw)
        self.assertEqual(self.lib.files(self.owner, version)['original/references/readme.txt'], b'exact original resource')
        self.assertEqual(self.lib.files(self.owner, version)['original/bin/example.dat'], b'\x00\x01\x02')
        self.assertEqual(version['readiness'], 'unverified')
        self.assertFalse(self.lib.asset(self.owner, imported['assetId'])['enabled'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

    def test_definition_only_tool_is_saved_but_cannot_be_enabled(self):
        raw = json.dumps({'name': 'definition_only', 'description': 'No handler',
                          'input_schema': {'type': 'object'}}).encode()
        preview = self.preview(raw, 'tool.json')
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['staticReadiness'], 'needs_configuration')
        self.assertIn('MISSING_IMPLEMENTATION', [item['code'] for item in preview.json['issues']])
        imported = self.wait(self.import_checked(raw, preview.json['sha256'], 'tool.json', 'definition-only'))
        self.assertEqual(imported['readiness'], 'needs_configuration')
        detail = self.client.get(BASE + '/assets/' + imported['assetId'])
        enabled = self.client.patch(BASE + '/assets/' + imported['assetId'],
                                    json={'enabled': True, 'activeVersionId': imported['versionId']},
                                    headers={'If-Match': detail.headers['ETag']})
        self.assertEqual(enabled.status_code, 403)

    def test_preview_rejects_unsafe_archive_and_ambiguous_entry(self):
        definition = b'{"name":"two","input_schema":{"type":"object"}}'
        ambiguous = zip_bytes({'SKILL.md': b'---\nname: two\ndescription: demo\n---\nText',
                               'tool.json': definition})
        self.assertEqual(self.preview(ambiguous).status_code, 400)
        duplicate = zip_bytes({'tool.json': definition, 'other.json': definition})
        self.assertEqual(self.preview(duplicate).status_code, 400)
        import zipfile
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as archive:
            archive.writestr('../tool.json', definition)
        denied = self.preview(out.getvalue())
        self.assertEqual(denied.status_code, 400)
        self.assertEqual(len(self.lib.store.list(self.owner, 'asset')), 0)


if __name__ == '__main__':
    unittest.main()
