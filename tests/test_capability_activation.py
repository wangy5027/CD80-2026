"""Explicit activation of trusted Skill/Tool dependency closures."""
import unittest
from unittest.mock import patch

from tests import test_capability_library as helpers
from tests import test_capability_community as sharing
from backend.app.modules.capability_library.contracts import canonical
from backend.app.modules.capability_library.official import copy_closure, copy_version
from backend.app.modules.capability_library.packages import zip_bytes
from backend.app.modules.capability_library.service import PUBLIC


BASE = helpers.BASE


class CapabilityActivationTests(unittest.TestCase):
    setUp = helpers.CapabilityLibraryTests.setUp
    tearDown = helpers.CapabilityLibraryTests.tearDown
    sample = helpers.CapabilityLibraryTests.sample
    upload = helpers.CapabilityLibraryTests.upload
    wait = helpers.CapabilityLibraryTests.wait
    post = helpers.CapabilityLibraryTests.post
    body = helpers.CapabilityLibraryTests.body
    version = helpers.CapabilityLibraryTests.version
    enable = helpers.CapabilityLibraryTests.enable
    verify_new = helpers.CapabilityLibraryTests.verify_new
    validated = helpers.CapabilityLibraryTests.validated

    def public_version(self, identity):
        asset = next(a for a in self.lib.store.list(PUBLIC, 'asset')
                     if a['sourceIdentity'] == identity)
        return self.lib.version(PUBLIC, asset['assetId'], asset['latestVersionId'])

    def copy_history(self):
        return copy_closure(self.lib, self.owner,
                            self.public_version('cd80/cd80-history'))

    def patch_asset(self, asset_id, payload):
        response = self.client.get(BASE + '/assets/' + asset_id)
        return self.client.patch(BASE + '/assets/' + asset_id, json=payload,
                                 headers={'If-Match': response.headers['ETag']})

    def test_history_skill_enables_exact_official_tool_and_can_be_admitted(self):
        copied = self.copy_history()
        lock = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
        tool = self.lib.asset(self.owner, lock['assetId'])
        self.assertFalse(tool['enabled'])
        self.lib.runner_revision += '-changed'
        self.assertTrue(self.version(copied)['configurationStale'])
        self.assertEqual(self.enable(copied).status_code, 200)
        tool = self.lib.asset(self.owner, lock['assetId'])
        root = self.lib.version(self.owner, copied['assetId'], copied['versionId'])
        self.assertTrue(tool['enabled'])
        self.assertEqual(tool['activeVersionId'], lock['versionId'])
        self.assertEqual(root['dependencyLocks'][0]['versionId'], lock['versionId'])
        self.assertEqual(root['dependencyLocks'][0]['configurationId'],
                         self.lib.version(self.owner, lock['assetId'], lock['versionId'])['configurationId'])
        self.assertEqual(self.lib.admit(self.owner, copied['assetId'], copied['versionId'])['versionId'],
                         copied['versionId'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

    def test_trusted_tool_rechecks_stale_connection_configuration(self):
        copied = copy_closure(self.lib, self.owner,
                              self.public_version('cd80/cd80-history-tool'))
        before = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['configurationId']
        self.app.config['CD80_READ_API_KEY'] = 'rotated-test-only-key'
        self.assertTrue(self.version(copied)['configurationStale'])
        self.assertEqual(self.enable(copied).status_code, 200)
        after = self.version(copied)
        self.assertNotEqual(after['configurationId'], before)
        self.assertFalse(after['configurationStale'])

    def test_trusted_tool_rechecks_stale_runtime_before_enable(self):
        copied = copy_closure(self.lib, self.owner,
                              self.public_version('cd80/cd80-history-tool'))
        before = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['configurationId']
        self.lib.runner_revision += '-changed'
        self.assertTrue(self.version(copied)['configurationStale'])
        self.assertEqual(self.enable(copied).status_code, 200)
        after = self.version(copied)
        self.assertNotEqual(after['configurationId'], before)
        self.assertFalse(after['configurationStale'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

    def test_enabled_dependency_keeps_its_chosen_active_version(self):
        copied = self.copy_history()
        lock = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
        public = self.public_version('cd80/cd80-history-tool')
        manifest = {**public['manifest'], 'description': 'A separately released tool version'}
        raw = zip_bytes({**self.lib.files(PUBLIC, public),
                         'capability.json': canonical(manifest).encode('utf-8')})
        with patch('backend.app.modules.capability_library.releases.check_release'):
            newer = self.lib.import_package(PUBLIC, raw, 'new-tool.zip', target=public['assetId'])
        chosen = copy_version(self.lib, self.owner,
                              self.lib.version(PUBLIC, newer['assetId'], newer['versionId']),
                              target=lock['assetId'])
        self.assertEqual(self.enable(chosen).status_code, 200)
        self.assertEqual(self.enable(copied).status_code, 200)
        tool = self.lib.asset(self.owner, lock['assetId'])
        self.assertEqual(tool['activeVersionId'], chosen['versionId'])
        self.assertEqual(self.lib.version(self.owner, copied['assetId'], copied['versionId'])
                         ['dependencyLocks'][0]['versionId'], lock['versionId'])
        self.lib.admit(self.owner, copied['assetId'], copied['versionId'])

    def test_private_unverified_and_old_evidence_do_not_gain_trust(self):
        private = self.wait(self.upload(self.sample('decimal-total')))
        denied = self.enable(private)
        self.assertEqual((denied.status_code, denied.json['code']),
                         (409, 'VERIFICATION_REQUIRED'))

        verified, _body = self.validated()
        self.assertEqual(self.patch_asset(verified['assetId'], {'enabled': False}).status_code, 200)
        self.lib.runner_revision += '-changed'
        stale = self.enable(verified)
        self.assertEqual((stale.status_code, stale.json['code']),
                         (409, 'STALE_CONFIGURATION'))

    def test_explicitly_disabled_dependency_is_not_reenabled(self):
        copied = self.copy_history()
        lock = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
        self.assertEqual(self.patch_asset(lock['assetId'], {'enabled': False}).status_code, 200)
        before = self.lib.asset(self.owner, lock['assetId'])
        denied = self.enable(copied)
        self.assertEqual((denied.status_code, denied.json['code']),
                         (409, 'DEPENDENCY_DISABLED'))
        self.assertEqual(self.lib.asset(self.owner, lock['assetId']), before)
        self.assertFalse(self.lib.asset(self.owner, copied['assetId'])['enabled'])

    def test_archived_and_deleted_dependencies_block_activation(self):
        for disposition in ('archived', 'deleted'):
            with self.subTest(disposition=disposition):
                copied = self.copy_history()
                lock = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
                if disposition == 'archived':
                    changed = self.patch_asset(lock['assetId'], {'lifecycle': 'archived'})
                else:
                    detail = self.client.get(BASE + '/assets/' + lock['assetId'])
                    changed = self.client.delete(BASE + '/assets/' + lock['assetId'],
                                                 headers={'If-Match': detail.headers['ETag']})
                self.assertEqual(changed.status_code, 200)
                before = self.lib.asset(self.owner, lock['assetId'])
                denied = self.enable(copied)
                self.assertEqual((denied.status_code, denied.json['code']),
                                 (409, 'DEPENDENCY_DISABLED'))
                self.assertEqual(self.lib.asset(self.owner, lock['assetId']), before)
                self.assertFalse(self.lib.asset(self.owner, copied['assetId'])['enabled'])
                # Restore only the isolated fixture so the second case can reuse the source.
                if disposition == 'archived':
                    self.assertEqual(self.patch_asset(lock['assetId'], {'lifecycle': 'active'}).status_code, 200)

    def test_recheck_keeps_deleted_exact_lock_even_with_another_copy(self):
        copied = self.copy_history()
        lock = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
        duplicate = copy_version(self.lib, self.owner,
                                 self.public_version('cd80/cd80-history-tool'), force_new=True)
        self.assertNotEqual(lock['assetId'], duplicate['assetId'])
        old = self.client.get(BASE + '/assets/' + lock['assetId'])
        deleted = self.client.delete(BASE + '/assets/' + lock['assetId'],
                                     headers={'If-Match': old.headers['ETag']})
        self.assertEqual(deleted.status_code, 200)

        rechecked = self.post('/assets/' + copied['assetId'] + '/recheck',
                              {'versionId': copied['versionId']})
        self.assertEqual(rechecked.status_code, 200, rechecked.json)
        self.assertEqual(rechecked.json['readiness'], 'blocked')
        self.assertIn('DEPENDENCY_DISABLED', [issue['code'] for issue in rechecked.json['issues']])
        self.assertEqual(rechecked.json['dependencyLocks'], [lock])
        denied = self.enable(copied)
        self.assertEqual((denied.status_code, denied.json['code']),
                         (409, 'DEPENDENCY_DISABLED'))
        self.assertEqual(self.lib.version(self.owner, copied['assetId'], copied['versionId'])
                         ['dependencyLocks'], [lock])
        self.assertFalse(self.lib.asset(self.owner, duplicate['assetId'])['enabled'])

        deleted_detail = self.client.get(BASE + '/assets/' + lock['assetId'] + '?deleted=true')
        restored = self.client.post(BASE + '/assets/' + lock['assetId'] + '/restore',
                                    headers={'If-Match': deleted_detail.headers['ETag']})
        self.assertEqual(restored.status_code, 200)
        recovered = self.post('/assets/' + copied['assetId'] + '/recheck',
                              {'versionId': copied['versionId']})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(recovered.json['dependencyLocks'][0]['versionId'], lock['versionId'])
        self.assertEqual(self.enable(copied).json['code'], 'DEPENDENCY_DISABLED')
        self.assertEqual(self.patch_asset(lock['assetId'],
                                          {'enabled': True, 'activeVersionId': lock['versionId']}).status_code, 200)
        self.assertEqual(self.enable(copied).status_code, 200)
        self.assertTrue(self.lib.asset(self.owner, lock['assetId'])['enabled'])
        self.assertFalse(self.lib.asset(self.owner, duplicate['assetId'])['enabled'])

    def test_new_skill_without_old_lock_can_select_available_tool(self):
        source = self.public_version('cd80/cd80-history')
        copied = copy_version(self.lib, self.owner, source)
        self.assertEqual(copied['readiness'], 'needs_configuration')
        self.assertEqual(self.lib.version(self.owner, copied['assetId'], copied['versionId'])
                         ['dependencyLocks'], [])
        tool = copy_version(self.lib, self.owner,
                            self.public_version('cd80/cd80-history-tool'), force_new=True)
        checked = self.post('/assets/' + copied['assetId'] + '/recheck',
                            {'versionId': copied['versionId']})
        self.assertEqual(checked.status_code, 200)
        self.assertEqual(checked.json['readiness'], 'ready')
        self.assertEqual(checked.json['dependencyLocks'][0]['versionId'], tool['versionId'])
        self.assertEqual(self.enable(copied).status_code, 200)
        self.assertTrue(self.lib.asset(self.owner, tool['assetId'])['enabled'])

    def test_partial_dependency_activation_rolls_back_on_later_refusal(self):
        first = self.public_version('org.example/decimal-total')
        second = self.public_version('cd80/cd80-history-tool')

        def add_dependencies(manifest, _files):
            manifest['dependencies'] = [
                {'sourceIdentity': item['manifest']['source']['identity'],
                 'contentDigest': item['contentDigest'], 'required': True, 'reason': 'exact'}
                for item in (first, second)]

        with patch('backend.app.modules.capability_library.releases.check_release'):
            source = self.lib.import_package(PUBLIC, self.sample('summary-skill', add_dependencies),
                                             'activation-test.zip')
        copied = copy_closure(self.lib, self.owner,
                              self.lib.version(PUBLIC, source['assetId'], source['versionId']))
        locks = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks']
        self.assertEqual(len(locks), 2)
        self.assertEqual(self.patch_asset(locks[1]['assetId'], {'enabled': False}).status_code, 200)
        before = [self.lib.asset(self.owner, lock['assetId']) for lock in locks]
        denied = self.enable(copied)
        self.assertEqual((denied.status_code, denied.json['code']),
                         (409, 'DEPENDENCY_DISABLED'))
        self.assertEqual([self.lib.asset(self.owner, lock['assetId']) for lock in locks], before)
        self.assertFalse(self.lib.asset(self.owner, copied['assetId'])['enabled'])

    def test_dependency_without_trust_or_evidence_cannot_be_activated(self):
        copied = self.copy_history()
        lock = self.lib.version(self.owner, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
        dependency = self.lib.version(self.owner, lock['assetId'], lock['versionId'])
        dependency.pop('admissionGrant')
        self.lib.store.put(self.owner, 'version', dependency['versionId'], dependency)
        denied = self.enable(copied)
        self.assertEqual((denied.status_code, denied.json['code']),
                         (409, 'VERIFICATION_REQUIRED'))
        self.assertFalse(self.lib.asset(self.owner, lock['assetId'])['enabled'])


class CommunityActivationTests(unittest.TestCase):
    setUp = sharing.CommunityCapabilityTests.setUp
    tearDown = sharing.CommunityCapabilityTests.tearDown
    sample = sharing.CommunityCapabilityTests.sample
    wait = sharing.CommunityCapabilityTests.wait
    post = sharing.CommunityCapabilityTests.post
    validated = sharing.CommunityCapabilityTests.validated
    publish = sharing.CommunityCapabilityTests.publish
    skill_with_tool = sharing.CommunityCapabilityTests.skill_with_tool

    def test_community_skill_enables_copied_exact_tool(self):
        _tool, skill = self.skill_with_tool()
        release = self.publish(skill)
        copied = self.post('/' + release['releaseId'] + '/copy', client=self.other).json
        lock = self.lib.version(self.reader, copied['assetId'], copied['versionId'])['dependencyLocks'][0]
        self.assertFalse(self.lib.asset(self.reader, lock['assetId'])['enabled'])
        detail = self.other.get(helpers.BASE + '/assets/' + copied['assetId'])
        enabled = self.other.patch(helpers.BASE + '/assets/' + copied['assetId'],
                                   json={'enabled': True, 'activeVersionId': copied['versionId']},
                                   headers={'If-Match': detail.headers['ETag']})
        self.assertEqual(enabled.status_code, 200, enabled.json)
        self.assertTrue(self.lib.asset(self.reader, lock['assetId'])['enabled'])
        self.lib.admit(self.reader, copied['assetId'], copied['versionId'])
        self.assertEqual(self.lib.store.list(self.reader, 'verification'), [])


if __name__ == '__main__':
    unittest.main()
