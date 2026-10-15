"""Modern App releases: explicit publication and isolated, atomic copies."""
import copy
import tempfile
import time
import unittest
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library.contracts import canonical, digest
from backend.app.modules.capability_library.packages import file_index, zip_bytes
from tests.test_asset_apps import sample
from tests.test_asset_components import manifest

BASE = '/desktop/api/community/apps'


class AppCommunityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-app-community-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'CAPABILITY_SKIP_PUBLIC_SEED': True, 'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.apps = self.app.extensions['asset_apps']
        self.community = self.app.extensions['app_community']
        self.author, self.reader, self.stranger = (self.app.test_client() for _ in range(3))
        for index, client in enumerate((self.author, self.reader, self.stranger)):
            self.assertEqual(client.post('/desktop/api/register', json={
                'email': f'app-community-{index}@example.invalid', 'password': 'test-password'}).status_code, 200)
        self.owner = self.author.get('/desktop/api/session').json['user']['id']
        self.reader_id = self.reader.get('/desktop/api/session').json['user']['id']
        self.serial = 0

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, client, path, payload, *, community=True, key=None):
        self.serial += 1
        prefix = BASE if community else '/desktop/api/asset-apps'
        return client.post(prefix + path, json=payload,
                           headers={'Idempotency-Key': key or str(self.serial)})

    def saved(self, doc=None):
        response = self.post(self.author, '', {'definition': doc or sample()}, community=False)
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def verified(self, app):
        """Use the real WASI executor and two distinct expected outputs."""
        cases = [
            {'label': '原预期', 'input': {'prices': [100, 120, 90, 108]},
             'expectation': {'mode': 'fields', 'values': {'analysis.change': 8, 'analysis.drawdown': 25}}},
            {'label': '新输入', 'input': {'prices': [80, 100, 95, 120]},
             'expectation': {'mode': 'fields', 'values': {'analysis.change': 50, 'analysis.drawdown': 5}}},
        ]
        response = self.post(self.author, '/' + app['id'] + '/verify',
                             {'versionId': app['versionId'], 'cases': cases}, community=False)
        self.assertEqual(response.status_code, 202, response.json)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            current = self.apps.detail(self.owner, app['id'], app['versionId'])
            if current['verification']['status'] != 'running':
                self.assertEqual(current['verification']['status'], 'passed', current['verification'])
                return current
            time.sleep(.03)
        self.fail('App verification did not finish')

    def marked_verified(self, app):
        """Other tests isolate publication mechanics without repeating WASI runs."""
        with self.apps.store.transaction() as db:
            version = self.apps.store.get(db, 'versions', self.owner, app['versionId'])
            version['verification'] = {'status': 'passed', 'cases': [], 'runIds': [],
                'contextDigest': self.apps._context(self.owner, version['definition'], {})}
            self.apps.store.put(db, 'versions', self.owner, version)
        return self.apps.detail(self.owner, app['id'])

    def published(self, app):
        payload = {'appId': app['id'], 'versionId': app['versionId']}
        preview = self.post(self.author, '/preview', payload)
        self.assertEqual(preview.status_code, 200, preview.json)
        response = self.post(self.author, '', {**payload, 'snapshotDigest': preview.json['snapshotDigest'],
                                               'confirmSharing': True})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_real_verified_release_is_explicit_and_public_only_after_confirmation(self):
        app = self.saved()
        payload = {'appId': app['id'], 'versionId': app['versionId']}
        self.assertEqual(self.post(self.author, '/preview', payload).status_code, 409)
        self.assertEqual(self.reader.get(BASE).json['items'], [])
        self.verified(app)
        preview = self.post(self.author, '/preview', payload)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(preview.json['package']['format'], 'cd80.app-package/2')
        self.assertIn('def run(payload)', preview.json['definition']['nodes'][0]['code'])
        self.assertNotIn('verification', preview.json['package'])
        self.assertEqual(self.post(self.author, '', {**payload, 'snapshotDigest': preview.json['snapshotDigest']}).status_code, 409)
        self.assertEqual(self.post(self.author, '', {**payload, 'snapshotDigest': 'wrong',
                                                    'confirmSharing': True}).status_code, 409)
        release = self.published(app)
        self.assertTrue(release['owned'])
        self.assertTrue(release['isOwner'])
        self.assertEqual(release['personalCopies'], [{'appId': app['id'], 'versionId': app['versionId']}])
        reader_list = self.reader.get(BASE + '?limit=1').json
        self.assertEqual(len(reader_list['items']), 1)
        self.assertIsNone(reader_list['nextCursor'])
        self.assertFalse(reader_list['items'][0]['owned'])
        self.assertFalse(reader_list['items'][0]['isOwner'])
        self.assertEqual(reader_list['items'][0]['personalCopies'], [])
        detail = self.reader.get(BASE + '/' + release['releaseId']).json
        self.assertEqual(detail['package']['digest'], digest(detail['definition']))
        self.assertEqual(self.reader.get(BASE + '/' + app['id']).status_code, 404)
        self.assertEqual(self.reader.get('/desktop/api/asset-apps/' + app['id']).status_code, 404)

    def test_copy_is_frozen_owner_scoped_unverified_and_cross_key_deduplicated(self):
        app = self.marked_verified(self.saved())
        enabled = self.post(self.author, '/' + app['id'] + '/enable',
                            {'versionId': app['versionId'], 'revision': app['revision'], 'enabled': True},
                            community=False)
        self.assertEqual(enabled.status_code, 200, enabled.json)
        release = self.published(app)
        path = '/' + release['releaseId'] + '/copy'
        first = self.post(self.reader, path, {}, key='same-copy')
        self.assertEqual(first.status_code, 201, first.json)
        self.assertFalse(first.json['alreadyOwned'])
        repeated = self.post(self.reader, path, {}, key='same-copy')
        self.assertEqual(repeated.json, first.json)
        different = self.post(self.reader, path, {}, key='different-copy')
        self.assertEqual(different.status_code, 201, different.json)
        self.assertTrue(different.json['alreadyOwned'])
        self.assertEqual(different.json['appId'], first.json['appId'])
        copied = self.reader.get('/desktop/api/asset-apps/' + first.json['appId']).json
        self.assertEqual(copied['verification']['status'], 'unverified')
        self.assertIsNone(copied['enabledVersionId'])
        self.assertEqual(copied['runtimeBindings'], {})
        self.assertEqual(copied['provenance']['communityReleaseId'], release['releaseId'])
        self.assertEqual(copied['definition']['nodes'][1]['code'], app['definition']['nodes'][1]['code'])
        self.assertEqual(self.stranger.get('/desktop/api/asset-apps/' + copied['id']).status_code, 404)
        listing = self.reader.get(BASE).json['items'][0]
        self.assertTrue(listing['owned'])
        self.assertEqual(listing['personalCopies'], [{'appId': copied['id'], 'versionId': copied['versionId']}])
        author_copy = self.post(self.author, path, {})
        self.assertEqual(author_copy.json, {'appId': app['id'], 'versionId': app['versionId'], 'alreadyOwned': True})

        changed = copy.deepcopy(sample())
        changed['name'] = '新版价格研究'
        updated = self.post(self.author, '', {'appId': app['id'], 'revision': enabled.json['revision'],
                                              'definition': changed}, community=False)
        self.assertEqual(updated.status_code, 201, updated.json)
        unchanged = self.reader.get(BASE + '/' + release['releaseId']).json
        self.assertEqual(unchanged['definition']['name'], sample()['name'])
        self.assertEqual(copied['definition']['name'], sample()['name'])

    def test_published_app_carries_complete_private_tool_snapshot_across_accounts(self):
        files = {'original/tool.py': b'def run(payload): return {"value": payload["n"] * 2}\n'}
        tool = manifest()
        tool['files'] = file_index(files)
        lib = self.w.capabilities
        installed = lib.import_package(self.owner,
            zip_bytes({**files, 'capability.json': canonical(tool).encode()}), 'tool.zip')
        definition = {'format': 'cd80.app/2', 'name': '已冻结工具应用', 'description': '在独立账户复制组件实现',
            'inputSchema': tool['tool']['inputSchema'],
            'nodes': [{'id': 'double', 'kind': 'tool', 'title': 'Double',
                       'componentRef': {'assetId': installed['assetId'], 'versionId': installed['versionId']},
                       'inputBindings': {'n': {'from': 'input', 'path': 'n'}},
                       'delivery': {'schema': tool['tool']['outputSchema']}}],
            'outputs': {'answer': {'from': 'step', 'stepId': 'double', 'path': 'value'}}}
        app = self.marked_verified(self.saved(definition))
        private_origin = app['definition']['nodes'][0]['component']['provenance']
        self.assertEqual(private_origin['assetId'], installed['assetId'])
        self.assertEqual(private_origin['versionId'], installed['versionId'])
        release = self.published(app)
        public_origin = release['definition']['nodes'][0]['component']['provenance']
        self.assertEqual(public_origin, {'contentDigest': app['definition']['nodes'][0]['component']['contentDigest']})
        self.assertNotIn(installed['assetId'], canonical(release['package']))
        saved = self.post(self.reader, '/' + release['releaseId'] + '/copy', {})
        self.assertEqual(saved.status_code, 201, saved.json)
        copied = self.reader.get('/desktop/api/asset-apps/' + saved.json['appId']).json
        component = copied['definition']['nodes'][0]['component']
        self.assertEqual(component['manifest']['source']['identity'], tool['source']['identity'])
        self.assertEqual(component['files']['original/tool.py'],
                         app['definition']['nodes'][0]['component']['files']['original/tool.py'])
        self.assertNotIn('componentRef', copied['definition']['nodes'][0])
        self.assertEqual(component['provenance'], public_origin)
        self.assertEqual(copied['verification']['status'], 'unverified')
        self.assertIsNone(copied['enabledVersionId'])
        self.assertEqual(self.reader.get('/desktop/api/capabilities/assets?scope=personal&kind=tool').json['items'], [])

    def test_withdraw_prevents_new_copy_and_preserves_existing_personal_copy(self):
        app = self.marked_verified(self.saved())
        release = self.published(app)
        copied = self.post(self.reader, '/' + release['releaseId'] + '/copy', {}).json
        withdrawn = self.post(self.author, '/' + release['releaseId'] + '/withdraw',
                              {'revision': release['revision']})
        self.assertEqual(withdrawn.status_code, 200, withdrawn.json)
        self.assertEqual(withdrawn.json['status'], 'withdrawn')
        self.assertEqual(self.reader.get(BASE + '/' + release['releaseId']).status_code, 404)
        self.assertEqual(self.reader.get(BASE).json['items'], [])
        self.assertEqual(self.post(self.stranger, '/' + release['releaseId'] + '/copy', {}).status_code, 404)
        self.assertEqual(self.reader.get('/desktop/api/asset-apps/' + copied['appId']).status_code, 200)
        self.assertEqual(self.author.get(BASE + '/' + release['releaseId']).json['status'], 'withdrawn')

    def test_stale_context_private_account_and_secret_source_are_rejected(self):
        app = self.marked_verified(self.saved())
        with self.apps.store.transaction() as db:
            version = self.apps.store.get(db, 'versions', self.owner, app['versionId'])
            version['verification']['contextDigest'] = 'stale'
            self.apps.store.put(db, 'versions', self.owner, version)
        payload = {'appId': app['id'], 'versionId': app['versionId']}
        self.assertEqual(self.post(self.author, '/preview', payload).json['code'], 'VERIFICATION_REQUIRED')
        self.assertEqual(self.post(self.reader, '/preview', payload).status_code, 404)
        bad = copy.deepcopy(sample())
        bad['nodes'][0]['code'] = 'API_KEY="fixture-secret-token-123456789"\n' + bad['nodes'][0]['code']
        secret = self.marked_verified(self.saved(bad))
        response = self.post(self.author, '/preview', {'appId': secret['id'], 'versionId': secret['versionId']})
        self.assertEqual(response.status_code, 403, response.json)
        self.assertEqual(self.author.get(BASE).json['items'], [])

    def test_copy_failure_rolls_back_app_and_link_and_can_retry_same_key(self):
        app = self.marked_verified(self.saved())
        release = self.published(app)
        original = self.apps._save_in_transaction
        def fail_after_save(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError('simulated interruption before release link')
        path = '/' + release['releaseId'] + '/copy'
        with patch.object(self.apps, '_save_in_transaction', side_effect=fail_after_save):
            with self.assertRaises(RuntimeError):
                self.community.copy(self.reader_id, release['releaseId'], {}, 'retry-key')
        self.assertEqual(self.apps.store.listing('apps', self.reader_id), [])
        with self.apps.store.transaction() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM asset_community_copies').fetchone()[0], 0)
        retry = self.post(self.reader, path, {}, key='retry-key')
        self.assertEqual(retry.status_code, 201, retry.json)
        self.assertFalse(retry.json['alreadyOwned'])
        self.assertEqual(len(self.apps.store.listing('apps', self.reader_id)), 1)


if __name__ == '__main__':
    unittest.main()
