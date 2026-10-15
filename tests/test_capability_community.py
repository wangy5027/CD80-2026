"""Community sharing uses temporary accounts, real WASI and model substitutes."""
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library import community
from backend.app.modules.capability_library.contracts import CapabilityError, canonical, digest
from backend.app.modules.capability_library.packages import file_index, zip_bytes
from backend.app.modules.capability_library.sharing import assert_shareable
from backend.app.modules.capability_library.asset_validation import verify_suite

ROOT = Path(__file__).resolve().parents[1]
BASE = '/desktop/api/community/capabilities'


class CommunityCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-community-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'GITEE_AI_API_KEY': 'test-only',
                               'CAPABILITY_SKIP_PUBLIC_SEED': True, 'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime')})
        self.lib = self.app.extensions['capability_library']
        self.w = self.app.extensions['workbench_service']
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'author@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.other = self.app.test_client()
        self.other.post('/desktop/api/register', json={'email': 'reader@example.invalid', 'password': 'test-password'})
        self.reader = self.other.get('/desktop/api/session').json['user']['id']
        self.counter = 0

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, path='', body=None, client=None, key=None):
        self.counter += 1
        return (client or self.client).post(BASE + path, json=body or {}, headers={'Idempotency-Key': key or str(self.counter)})

    def sample(self, folder='decimal-total', edit=None):
        source = ROOT / 'docs/capability-library/examples' / folder
        files = {p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        manifest = json.loads(files.pop('capability.json'))
        files.pop('provenance.json', None)
        if edit:
            edit(manifest, files)
        manifest['files'] = file_index(files)
        return zip_bytes({**files, 'capability.json': canonical(manifest).encode()})

    def wait(self, job, owner=None):
        end = time.monotonic() + 30
        while job['status'] in {'queued', 'running', 'cancelling'} and time.monotonic() < end:
            time.sleep(.02)
            job = self.lib.store.get(owner or self.owner, 'job', job['jobId'])
        self.assertEqual(job['status'], 'completed', job)
        return job['result']

    def validated(self, folder='decimal-total', edit=None, target=None):
        result = self.lib.import_package(self.owner, self.sample(folder, edit), 'source.zip', target)
        version = self.lib.version(self.owner, result['assetId'], result['versionId'])
        payload = {**{k: result[k] for k in ('assetId', 'versionId')}, 'configurationId': version['configurationId'], 'input': {}, 'fileIds': []}
        if folder == 'decimal-total':
            payload.update(input={'values': ['0.10', '0.20']}, expectation={'mode': 'json_equals', 'value': {'total': '0.30'}})
            tested = self.wait(verify_suite(self.lib, self.owner, {**payload, 'cases': [
                {k: payload[k] for k in ('input', 'fileIds', 'expectation')},
                {'input': {'values': ['1.20', '2.30']}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '3.50'}}}]}))
            self.assertEqual(tested['status'], 'passed')
        else:
            payload['expectation'] = {'mode': 'manual_review', 'prompt': '总结12条反馈，已处理9条。', 'criteria': '保留12与9，不编造。'}

            def events(*_args, **_kwargs):
                yield {'type': 'token', 'text': '24条反馈已处理18条，其余6条。' if '24条' in _args[0][-1]['content'] else '12条反馈已处理9条，其余3条。'}
                yield {'type': 'turn_end', 'tool_calls': []}

            with patch.object(self.w.runtime.provider, 'events', events):
                tested = self.wait(verify_suite(self.lib, self.owner, {**payload, 'cases': [
                    {k: payload[k] for k in ('input', 'fileIds', 'expectation')},
                    {'input': {}, 'fileIds': [], 'expectation': {'mode': 'manual_review', 'prompt': '总结24条反馈，已处理18条。', 'criteria': '保留24与18，不编造。'}}]}))
            self.lib.review(self.owner, tested['verificationId'], {'decision': 'accept'})
        return result

    def publish(self, result, **extra):
        payload = {**{k: result[k] for k in ('assetId', 'versionId')}, **extra}
        preview = self.post('/preview', payload)
        self.assertEqual(preview.status_code, 200, preview.json)
        published = self.post('', {**payload, 'snapshotDigest': preview.json['snapshotDigest'], 'confirmSharing': True})
        self.assertEqual(published.status_code, 201, published.json)
        return published.json

    def skill_with_tool(self):
        tool = self.validated()
        asset = self.lib.asset(self.owner, tool['assetId'])
        self.lib.update_asset(self.owner, asset['assetId'], {'enabled': True, 'activeVersionId': tool['versionId']}, '"' + str(asset['revision']) + '"')
        version = self.lib.version(self.owner, tool['assetId'], tool['versionId'])

        def edit(manifest, _files):
            manifest['dependencies'] = [{'sourceIdentity': version['manifest']['source']['identity'], 'contentDigest': version['contentDigest'], 'required': True, 'reason': '验证精确计算依赖'}]

        return tool, self.validated('summary-skill', edit)

    def test_real_verified_source_published_then_copied_without_private_state(self):
        source = self.validated()
        self.lib.store.put(self.owner, 'credential', 'private-credential', {'value': 'do-not-share-this'})
        self.lib.store.put(self.owner, 'connection', 'private-connection', {'name': 'private', 'endpoint': 'https://private.invalid'})
        release = self.publish(source)
        listing = self.other.get(BASE).json['items']
        self.assertEqual(listing[0]['releaseId'], release['releaseId'])
        detail = self.other.get(BASE + '/' + release['releaseId']).json
        serialized = canonical(detail)
        for secret in (self.owner, 'author@example.invalid', source['assetId'], source['versionId'], 'do-not-share-this'):
            self.assertNotIn(secret, serialized)
        copied = self.post('/' + release['releaseId'] + '/copy', client=self.other)
        self.assertEqual(copied.status_code, 201, copied.json)
        personal = self.lib.asset(self.reader, copied.json['assetId'])
        self.assertFalse(personal['enabled'])
        self.assertIsNone(personal['activeVersionId'])
        self.assertEqual(personal['readiness'], 'ready')
        self.assertEqual(personal['provenance']['communityReleaseId'], release['releaseId'])
        for kind in ('credential', 'connection', 'verification', 'invocation', 'test_case'):
            self.assertEqual(self.lib.store.list(self.reader, kind), [])
        self.assertEqual(self.other.get('/desktop/api/capabilities/assets/' + source['assetId']).status_code, 404)

    def test_unverified_stale_deleted_and_archived_sources_cannot_publish(self):
        source = self.lib.import_package(self.owner, self.sample(), 'source.zip')
        body = {k: source[k] for k in ('assetId', 'versionId')}
        self.assertEqual(self.post('/preview', body).json['code'], 'VERIFICATION_REQUIRED')
        source = self.validated()
        self.lib.runner_revision = 'changed-runner'
        self.assertEqual(self.post('/preview', body).json['code'], 'VERIFICATION_REQUIRED')
        asset = self.lib.asset(self.owner, source['assetId'])
        asset['deletedAt'] = '2026-10-14'
        self.lib.store.put(self.owner, 'asset', asset['assetId'], asset)
        self.assertEqual(self.post('/preview', body).json['code'], 'CAPABILITY_BLOCKED')

    def test_recipient_verifies_enables_invokes_and_exports_independent_source(self):
        source = self.validated()
        release = self.publish(source)
        copied = self.post('/' + release['releaseId'] + '/copy', client=self.other).json
        local = self.lib.version(self.reader, copied['assetId'], copied['versionId'])
        body = {'assetId': copied['assetId'], 'versionId': copied['versionId'],
                'configurationId': local['configurationId'], 'input': {'values': ['1.25', '2.75']}, 'fileIds': []}
        with self.assertRaises(CapabilityError) as blocked:
            self.lib.admit(self.reader, copied['assetId'], copied['versionId'], local['configurationId'])
        self.assertEqual(blocked.exception.code, 'CAPABILITY_DISABLED')
        verified = self.wait(verify_suite(self.lib, self.reader, {**body, 'cases': [
            {'input': body['input'], 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '4.00'}}},
            {'input': {'values': ['1.20', '2.30']}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '3.50'}}}]}), self.reader)
        self.assertEqual(verified['status'], 'passed')
        asset = self.lib.asset(self.reader, copied['assetId'])
        self.lib.update_asset(self.reader, asset['assetId'], {'enabled': True, 'activeVersionId': local['versionId']},
                              '"' + str(asset['revision']) + '"')
        invoked = self.lib.invoke(self.reader, {**body, 'input': {'values': ['7.25', '2.75']}})
        self.assertEqual(invoked['status'], 'succeeded')
        self.assertEqual(invoked['result']['content'], {'total': '10.00'})
        exported = self.lib.export(self.reader, {**body, 'format': 'platform'})
        downloaded = self.other.get(exported['downloadUrl'])
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(self.client.get(exported['downloadUrl']).status_code, 404)
        # A source export carries neither the recipient's verification nor its enablement.
        imported = self.lib.import_package(self.reader, downloaded.data, 'roundtrip.zip', force_new=True)
        restored = self.lib.version(self.reader, imported['assetId'], imported['versionId'])
        self.assertEqual(restored['contentDigest'], local['contentDigest'])
        self.assertEqual(self.lib.files(self.reader, restored), self.lib.files(self.owner, self.lib.version(
            self.owner, source['assetId'], source['versionId'])))
        self.assertFalse(self.lib.asset(self.reader, imported['assetId'])['enabled'])
        self.assertIsNone(self.lib.evidence(self.reader, restored))
        self.assertEqual(self.post('/' + release['releaseId'] + '/withdraw', {'revision': 1}).status_code, 200)
        self.assertEqual(self.lib.invoke(self.reader, body)['result']['content'], {'total': '4.00'})

    def test_preview_is_bound_to_metadata_and_author_confirmation(self):
        source = self.validated()
        body = {k: source[k] for k in ('assetId', 'versionId')}
        preview = self.post('/preview', body).json
        self.assertEqual(self.post('', {**body, 'snapshotDigest': preview['snapshotDigest']}).json['code'], 'SHARING_CONFIRMATION_REQUIRED')
        response = self.post('', {**body, 'title': 'changed', 'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True})
        self.assertEqual(response.json['code'], 'SHARING_PREVIEW_CHANGED')
        self.assertEqual(self.lib.store.list(community.COMMUNITY, 'release'), [])
        self.assertEqual(self.post('/preview', body, client=self.other).status_code, 404)

    def test_preview_includes_complete_root_and_dependency_snapshot_source(self):
        tool, skill = self.skill_with_tool()
        payload = {k: skill[k] for k in ('assetId', 'versionId')}
        preview = self.post('/preview', payload)
        self.assertEqual(preview.status_code, 200, preview.json)
        nodes = [preview.json['dependencies'][0], preview.json['root']]
        total_bytes, total_files = 0, 0
        for source, node in zip((tool, skill), nodes):
            version = self.lib.version(self.owner, source['assetId'], source['versionId'])
            expected = {**self.lib.files(self.owner, version), 'capability.json': canonical(version['manifest']).encode('utf-8')}
            self.assertEqual([item['path'] for item in node['sourceFiles']], sorted(expected))
            for item in node['sourceFiles']:
                raw = expected[item['path']]
                self.assertEqual(item, {'path': item['path'], 'text': raw.decode('utf-8'), 'sha256': digest(raw), 'size': len(raw)})
                total_bytes += len(raw)
                total_files += 1
        self.assertEqual(preview.json['sourceReview'], {
            'fileCount': total_files, 'totalBytes': total_bytes,
            'maxFiles': community.MAX_REVIEW_FILES, 'maxBytes': community.MAX_REVIEW_BYTES})
        # Changing the author's latest version must never replace the reviewed source.
        latest = self.validated('summary-skill', edit=lambda m, _f: m.update(description='new description'), target=skill['assetId'])
        repeated = self.post('/preview', payload)
        self.assertEqual(repeated.json, preview.json)
        changed = self.post('', {**payload, 'versionId': latest['versionId'],
                                 'snapshotDigest': preview.json['snapshotDigest'], 'confirmSharing': True})
        self.assertEqual(changed.status_code, 409, changed.json)
        self.assertEqual(changed.json['code'], 'SHARING_PREVIEW_CHANGED')
        published = self.post('', {**payload, 'snapshotDigest': preview.json['snapshotDigest'], 'confirmSharing': True})
        self.assertEqual(published.status_code, 201, published.json)
        copied = self.post('/' + published.json['releaseId'] + '/copy', client=self.other).json
        copied_version = self.lib.version(self.reader, copied['assetId'], copied['versionId'])
        reviewed = {item['path']: item['text'].encode('utf-8') for item in preview.json['root']['sourceFiles']
                    if item['path'] != 'capability.json'}
        self.assertEqual(self.lib.files(self.reader, copied_version), reviewed)
        self.assertNotIn('sourceFiles', self.other.get(BASE + '/' + published.json['releaseId']).json['root'])

    def test_oversized_review_is_rejected_without_partial_preview_or_release(self):
        _tool, skill = self.skill_with_tool()
        payload = {k: skill[k] for k in ('assetId', 'versionId')}
        preview = self.post('/preview', payload).json
        nodes = [preview['root'], *preview['dependencies']]
        limits = {'MAX_REVIEW_BYTES': max(sum(f['size'] for f in n['sourceFiles']) for n in nodes),
                  'MAX_REVIEW_FILES': max(len(n['sourceFiles']) for n in nodes)}
        for setting, limit in limits.items():
            # Each individual package fits; the aggregate root+dependency must not.
            with self.subTest(setting=setting), patch.object(community, setting, limit):
                refused = self.post('/preview', payload)
                self.assertEqual(refused.status_code, 413, refused.json)
                self.assertEqual(refused.json['code'], 'SHARING_REVIEW_LIMIT_EXCEEDED')
                self.assertNotIn('root', refused.json)
                denied = self.post('', {**payload, 'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True})
                self.assertEqual(denied.status_code, 413, denied.json)
                self.assertEqual(denied.json['code'], 'SHARING_REVIEW_LIMIT_EXCEEDED')
                self.assertEqual(self.lib.store.list(community.COMMUNITY, 'release'), [])

    def test_review_time_does_not_change_snapshot_or_create_duplicate_release(self):
        source = self.validated()
        body = {k: source[k] for k in ('assetId', 'versionId')}
        with patch('zipfile.time.localtime', return_value=(2026, 10, 14, 1, 0, 0, 2, 287, -1)):
            preview = self.post('/preview', body)
        self.assertEqual(preview.status_code, 200, preview.json)
        confirmed = {**body, 'snapshotDigest': preview.json['snapshotDigest'], 'confirmSharing': True}
        with patch('zipfile.time.localtime', return_value=(2026, 10, 14, 1, 5, 0, 2, 287, -1)):
            published = self.post('', confirmed)
        self.assertEqual(published.status_code, 201, published.json)
        with patch('zipfile.time.localtime', return_value=(2026, 10, 14, 2, 0, 0, 2, 287, -1)):
            repeated = self.post('', confirmed)
        self.assertEqual(repeated.status_code, 201, repeated.json)
        self.assertTrue(repeated.json['deduplicated'])
        self.assertEqual(published.json['releaseId'], repeated.json['releaseId'])
        self.assertEqual(len(self.lib.store.list(community.COMMUNITY, 'release')), 1)

    def test_duplicate_and_idempotency_keep_one_copy_and_conflicts_are_explicit(self):
        release = self.publish(self.validated())
        path = '/' + release['releaseId'] + '/copy'
        first = self.post(path, client=self.other, key='copy-once')
        again = self.post(path, client=self.other, key='copy-once')
        self.assertEqual(first.json, again.json)
        self.assertEqual(len(self.lib.store.list(self.reader, 'asset')), 1)
        self.assertEqual(self.post(path, {'confirmDuplicate': True}, client=self.other, key='copy-once').json['code'], 'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.post(path, client=self.other).json['code'], 'DUPLICATE_CONFIRMATION_REQUIRED')
        second = self.post(path, {'confirmDuplicate': True}, client=self.other)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.json['assetId'], second.json['assetId'])
        self.assertEqual(self.lib.asset(self.reader, second.json['assetId'])['name'], '十进制求和_2')

    def test_ensure_owned_reuses_older_exact_version_across_keys(self):
        release = self.publish(self.validated())
        path = '/' + release['releaseId'] + '/copy'
        first = self.post(path, {'ensureOwned': True}, client=self.other, key='install-first')
        self.assertEqual(first.status_code, 201, first.json)
        self.assertFalse(first.json['alreadyOwned'])
        changed = self.lib.import_package(self.reader,
                                          self.sample(edit=lambda manifest, _files: manifest.update(description='本地修改')),
                                          'edited.zip', target=first.json['assetId'])
        self.assertNotEqual(changed['versionId'], first.json['versionId'])
        detail = self.other.get(BASE + '/' + release['releaseId']).json
        self.assertTrue(detail['owned'])
        self.assertEqual(detail['personalCopies'][0]['versionId'], first.json['versionId'])
        second = self.post(path, {'ensureOwned': True}, client=self.other, key='install-second')
        self.assertEqual(second.status_code, 201, second.json)
        self.assertTrue(second.json['alreadyOwned'])
        self.assertEqual((second.json['assetId'], second.json['versionId']),
                         (first.json['assetId'], first.json['versionId']))
        self.assertEqual(len(self.lib.store.list(self.reader, 'asset')), 1)
        third = self.app.test_client()
        third.post('/desktop/api/register', json={'email': 'third@example.invalid', 'password': 'test-password'})
        self.assertFalse(third.get(BASE + '/' + release['releaseId']).json['owned'])

    def test_parallel_cross_key_ensure_owned_has_one_copy(self):
        release = self.publish(self.validated())
        cookie = self.other.get_cookie('ficagent_session').value

        def install(index):
            client = self.app.test_client()
            client.set_cookie('ficagent_session', cookie)
            return client.post(BASE + '/' + release['releaseId'] + '/copy',
                               json={'ensureOwned': True}, headers={'Idempotency-Key': 'install-' + str(index)})

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(install, range(3)))
        self.assertTrue(all(r.status_code == 201 for r in results), [r.json for r in results])
        self.assertEqual(len({r.json['assetId'] for r in results}), 1)
        self.assertEqual(sum(not r.json['alreadyOwned'] for r in results), 1)
        self.assertEqual(len(self.lib.store.list(self.reader, 'asset')), 1)

    def test_concurrent_retries_install_exactly_one_copy(self):
        release = self.publish(self.validated())
        cookie = self.other.get_cookie('ficagent_session').value

        def request_copy(_index):
            client = self.app.test_client()
            client.set_cookie('ficagent_session', cookie)
            response = client.post(BASE + '/' + release['releaseId'] + '/copy', json={},
                                   headers={'Idempotency-Key': 'parallel-copy'})
            return response.status_code, response.json

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request_copy, range(2)))
        self.assertEqual([code for code, _body in results], [201, 201])
        self.assertEqual(results[0][1]['assetId'], results[1][1]['assetId'])
        self.assertEqual(len(self.lib.store.list(self.reader, 'asset')), 1)

    def test_new_release_is_immutable_and_withdrawal_preserves_existing_copies(self):
        source = self.validated()
        first = self.publish(source)
        copied = self.post('/' + first['releaseId'] + '/copy', client=self.other).json
        changed = self.validated(edit=lambda m, _f: m.update(description='新版本说明'), target=source['assetId'])
        second = self.publish(changed)
        self.assertEqual(second['releaseNumber'], 2)
        self.assertEqual(second['previousReleaseId'], first['releaseId'])
        self.assertEqual(self.other.get(BASE + '/' + first['releaseId']).json['root']['manifest']['description'], '对十进制字符串求和。')
        path = '/' + first['releaseId'] + '/withdraw'
        self.assertEqual(self.post(path, {'revision': 1}, client=self.other).status_code, 404)
        self.assertEqual(self.post(path, {'revision': 99}).status_code, 409)
        self.assertEqual(self.post(path, {'revision': 1}).json['status'], 'withdrawn')
        self.assertEqual(self.other.get(BASE + '/' + first['releaseId']).status_code, 404)
        self.assertEqual(self.post('/' + first['releaseId'] + '/copy', {'confirmDuplicate': True}, client=self.other).status_code, 404)
        self.assertEqual(self.lib.asset(self.reader, copied['assetId'])['kind'], 'tool')
        mine = self.client.get(BASE + '?mine=true').json['items']
        self.assertEqual({r['status'] for r in mine}, {'published', 'withdrawn'})

    def test_dependency_bundle_copies_exact_versions_without_auto_enabling(self):
        tool, skill = self.skill_with_tool()
        release = self.publish(skill)
        self.assertEqual(len(release['dependencies']), 1)
        result = self.post('/' + release['releaseId'] + '/copy', client=self.other)
        self.assertEqual(result.status_code, 201, result.json)
        dependency = result.json['dependencies'][0]
        self.assertFalse(dependency['reused'])
        local = self.lib.version(self.reader, result.json['assetId'], result.json['versionId'])
        self.assertEqual(local['dependencyLocks'][0]['versionId'], dependency['versionId'])
        for asset in self.lib.store.list(self.reader, 'asset'):
            self.assertFalse(asset['enabled'])
        self.assertEqual(self.lib.version(self.reader, dependency['assetId'], dependency['versionId'])['contentDigest'], self.lib.version(self.owner, tool['assetId'], tool['versionId'])['contentDigest'])
        second = self.post('/' + release['releaseId'] + '/copy', {'confirmDuplicate': True}, client=self.other)
        self.assertTrue(second.json['dependencies'][0]['reused'])
        self.assertEqual(len(self.lib.store.list(self.reader, 'asset')), 3)

    def test_copy_failure_rolls_back_all_dependency_records_and_blobs(self):
        _tool, skill = self.skill_with_tool()
        release = self.publish(skill)
        install = community._install

        def broken(lib, owner, node, raw, rid, dependencies=()):
            if node['manifest']['kind'] == 'skill':
                raise CapabilityError('TEST_INSTALL_FAILURE', 'synthetic copy failure', 409)
            return install(lib, owner, node, raw, rid, dependencies)

        with patch.object(community, '_install', broken):
            response = self.post('/' + release['releaseId'] + '/copy', client=self.other)
        self.assertEqual(response.status_code, 409)
        for kind in ('asset', 'version'):
            self.assertEqual(self.lib.store.list(self.reader, kind), [])
        self.assertEqual(self.lib.store.db.execute('SELECT COUNT(*) FROM capability_blobs WHERE owner=?', (self.reader,)).fetchone()[0], 0)

    def test_copy_binds_new_dependency_instead_of_archived_identical_version(self):
        _tool, skill = self.skill_with_tool()
        release = self.publish(skill)
        archived = self.lib.import_package(self.reader, self.sample(), 'old-copy.zip')
        version = self.lib.version(self.reader, archived['assetId'], archived['versionId'])
        # Keep an archived exact match that would sort before any newly generated ID.
        self.lib.store.delete(self.reader, 'version', version['versionId'])
        version['versionId'] = 'ver_000000000000000000000000'
        self.lib.store.put(self.reader, 'version', version['versionId'], version)
        asset = self.lib.asset(self.reader, archived['assetId'])
        asset.update(lifecycle='archived', latestVersionId=version['versionId'])
        self.lib.store.put(self.reader, 'asset', asset['assetId'], asset)
        result = self.post('/' + release['releaseId'] + '/copy', client=self.other)
        self.assertEqual(result.status_code, 201, result.json)
        dependency = result.json['dependencies'][0]
        self.assertFalse(dependency['reused'])
        local = self.lib.version(self.reader, result.json['assetId'], result.json['versionId'])
        self.assertEqual(local['dependencyLocks'][0]['versionId'], dependency['versionId'])
        self.assertEqual(self.lib.asset(self.reader, asset['assetId'])['lifecycle'], 'archived')

    def test_missing_dependency_and_injected_owner_are_rejected(self):
        tool, skill = self.skill_with_tool()
        asset = self.lib.asset(self.owner, tool['assetId'])
        asset['deletedAt'] = '2026-10-14'
        self.lib.store.put(self.owner, 'asset', asset['assetId'], asset)
        body = {k: skill[k] for k in ('assetId', 'versionId')}
        self.assertEqual(self.post('/preview', body).json['code'], 'MISSING_DEPENDENCY')
        self.assertEqual(self.post('/preview', {**body, 'owner': self.owner}, client=self.other).status_code, 400)
        self.assertEqual(self.app.test_client().get(BASE).status_code, 401)


class SharingChecksTests(unittest.TestCase):
    def test_secret_metadata_url_source_files_and_private_resources_are_rejected(self):
        for metadata, files in [
            ({'api_key': 'short-secret'}, {}),
            ({'token': 'abc'}, {}),
            ({'environment': {'GITEE_AI_API_KEY': 'abc'}}, {}),
            ({'description': 'https://user:password@host.invalid/path'}, {}),
            ({'source': {'url': 'https://host.invalid/path?api_key=short'}}, {}),
            ({'source': {'url': 'https://host.invalid/path?X-Amz-Signature=short'}}, {}),
            ({'source': {'url': 'https://host.invalid/path#access_token=short'}}, {}),
            ({'description': '/desktop/api/sub-agent-artifacts/private-id'}, {}),
            ({'description': 'project-file:private-input.csv'}, {}),
            ({'description': 'library-file:private-report.pdf'}, {}),
            ({'description': 'C:\\Users\\private\\data.csv'}, {}),
            ({}, {'original/.env': b'KEY=anything'}),
            ({}, {'original/tool.py': b'password = "short"\ndef run(payload): return {}\n'}),
            ({}, {'original/config.json': b'{"cookie":"session=private"}'}),
            ({}, {'original/config.yaml': b'password: short\n'}),
            ({}, {'original/config.yml': b'connection:\n  access_token: short\n'}),
            ({}, {'original/config.yaml': b'password: 2026-10-14\n'}),
            ({}, {'original/config.toml': b'[connection]\npassword = "short"\n'}),
            ({}, {'original/data.png': b'\x89PNG\x00private'}),
        ]:
            with self.subTest(metadata=metadata, files=list(files)), self.assertRaises(CapabilityError):
                assert_shareable(metadata, files)

    def test_schema_secret_field_declaration_and_parameter_paths_are_allowed(self):
        assert_shareable({'inputSchema': {'properties': {'api_key': {'type': 'string', 'description': 'Use a private connection'}}}},
                         {'original/tool.py': b'def run(payload):\n    return {"file": "/input/data.csv"}\n',
                          'original/config.yaml': b'api_key: <YOUR_KEY>\npassword: null\n',
                          'original/config.toml': b'password = "${PASSWORD}"\n'})

    def test_yaml_aliases_are_checked_once_and_invalid_configs_are_rejected(self):
        assert_shareable({}, {'original/example.yaml': b'example: &shared {name: sample}\nagain: [*shared, *shared]\n'})
        for path, data in [('original/example.yaml', b'password: [unterminated'),
                           ('original/example.toml', b'name = [unterminated'),
                           ('original/example.json', b'{"name":')]:
            with self.subTest(path=path), self.assertRaises(CapabilityError) as error:
                assert_shareable({}, {path: data})
            self.assertEqual(error.exception.code, 'INVALID_PACKAGE')

    def test_python_newlines_are_not_json_escaped_unc_paths(self):
        assert_shareable({'code': 'def run(payload):\n    writer = csv.writer(handle, lineterminator="\\n")\n    return {}\n'})
