"""Trusted source admission never fabricates verification or bypasses runtime guards."""
import threading
import unittest
from unittest.mock import patch

from tests import test_capability_library as helpers
from tests import test_capability_community as sharing
from backend.app.modules.capability_library.admission import policy
from backend.app.modules.capability_library.official import copy_closure, prepare
from backend.app.modules.capability_library.service import PUBLIC

BASE = helpers.BASE


class OfficialAdmissionTests(unittest.TestCase):
    setUp = helpers.CapabilityLibraryTests.setUp
    tearDown = helpers.CapabilityLibraryTests.tearDown
    sample = helpers.CapabilityLibraryTests.sample
    post = helpers.CapabilityLibraryTests.post
    wait = helpers.CapabilityLibraryTests.wait
    upload = helpers.CapabilityLibraryTests.upload
    enable = helpers.CapabilityLibraryTests.enable
    version = helpers.CapabilityLibraryTests.version
    body = helpers.CapabilityLibraryTests.body

    def source(self, identity='org.example/decimal-total'):
        asset = next(item for item in self.lib.store.list(PUBLIC, 'asset')
                     if item['sourceIdentity'] == identity)
        return self.lib.version(PUBLIC, asset['assetId'], asset['latestVersionId'])

    def test_official_copy_is_ready_disabled_and_real_wasi_runs_without_evidence(self):
        source = self.source()
        copied = copy_closure(self.lib, self.owner, source)
        self.assertEqual(copied['readiness'], 'ready')
        self.assertEqual(copied['admission'], {'mode': 'trusted_release', 'source': 'official',
                                                'validationRequired': False})
        self.assertFalse(self.lib.asset(self.owner, copied['assetId'])['enabled'])
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.lib.store.list(self.owner, 'invocation'), [])
        self.assertEqual(self.enable(copied).status_code, 200)
        invoked = self.wait(self.post('/invocations', self.body(copied)))
        self.assertEqual(invoked['result']['content'], {'total': '0.30'})
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])

    def test_forged_source_needs_validation_until_server_copy_and_edit_loses_grant(self):
        raw = self.sample('decimal-total')
        direct = self.wait(self.upload(raw))
        self.assertEqual(self.version(direct)['admission']['mode'], 'validation_required')
        self.assertEqual(self.enable(direct).status_code, 409)
        source = self.source()
        copied = copy_closure(self.lib, self.owner, source)
        self.assertTrue(copied['alreadyOwned'])
        self.assertEqual(copied['versionId'], direct['versionId'])
        self.assertEqual(self.version(copied)['readiness'], 'ready')
        edited = self.wait(self.upload(self.sample('decimal-total',
            lambda manifest, _files: manifest.update(description='A private edited version')),
            copied['assetId']))
        self.assertEqual(self.version(edited)['readiness'], 'unverified')
        self.assertEqual(self.version(edited)['admission']['source'], None)
        self.assertEqual(self.version(copied)['admission']['source'], 'official')

    def test_legacy_prepare_only_copies_and_stale_runtime_needs_recheck(self):
        source = self.source()
        with patch.object(self.lib, 'invoke', side_effect=AssertionError('must not execute')):
            copied = prepare(self.lib, self.owner, source, threading.Event())
        self.assertEqual(copied['readiness'], 'ready')
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertFalse(self.lib.asset(self.owner, copied['assetId'])['enabled'])
        self.lib.runner_revision += '-changed'
        detail = self.version(copied)
        self.assertTrue(detail['configurationStale'])
        self.assertNotEqual(detail['readiness'], 'ready')
        checked = self.lib.check(self.owner, self.lib.version(self.owner, copied['assetId'], copied['versionId']))
        self.assertEqual(checked['readiness'], 'ready')

    def test_retired_official_source_cannot_be_copied_through_legacy_direct_route(self):
        source = self.source()
        asset = self.lib.asset(PUBLIC, source['assetId'])
        asset['lifecycle'] = 'archived'
        self.lib.store.put(PUBLIC, 'asset', asset['assetId'], asset)
        response = self.post('/assets/' + source['assetId'] + '/copy',
                             {'versionId': source['versionId'], 'confirmDuplicate': True})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'CAPABILITY_BLOCKED')
        self.assertEqual(self.lib.store.list(self.owner, 'asset'), [])


class CommunityAdmissionTests(unittest.TestCase):
    setUp = sharing.CommunityCapabilityTests.setUp
    tearDown = sharing.CommunityCapabilityTests.tearDown
    sample = sharing.CommunityCapabilityTests.sample
    validated = sharing.CommunityCapabilityTests.validated
    publish = sharing.CommunityCapabilityTests.publish
    post = sharing.CommunityCapabilityTests.post
    wait = sharing.CommunityCapabilityTests.wait
    skill_with_tool = sharing.CommunityCapabilityTests.skill_with_tool

    def test_public_copy_ready_without_evidence_and_survives_withdrawal(self):
        source = self.validated()
        release = self.publish(source)
        copied = self.post('/' + release['releaseId'] + '/copy', {'ensureOwned': True},
                           client=self.other).json
        self.assertEqual(copied['readiness'], 'ready')
        self.assertFalse(self.lib.asset(self.reader, copied['assetId'])['enabled'])
        self.assertEqual(self.lib.store.list(self.reader, 'verification'), [])
        local = self.lib.version(self.reader, copied['assetId'], copied['versionId'])
        asset = self.lib.asset(self.reader, copied['assetId'])
        self.lib.update_asset(self.reader, copied['assetId'],
                              {'enabled': True, 'activeVersionId': copied['versionId']},
                              f'"{asset["revision"]}"')
        invoked = self.lib.invoke(self.reader, {'assetId': copied['assetId'],
            'versionId': copied['versionId'], 'configurationId': local['configurationId'],
            'input': {'values': ['1.25', '2.75']}, 'fileIds': []})
        self.assertEqual(invoked['result']['content'], {'total': '4.00'})
        self.assertEqual(self.lib.store.list(self.reader, 'verification'), [])
        self.post('/' + release['releaseId'] + '/withdraw', {'revision': release['revision']})
        self.assertEqual(policy(self.lib, self.reader, local)['source'], 'community')
        # Persisted pre-grant copies remain recognized from server-owned provenance.
        local.pop('admissionGrant')
        local.pop('testCases')
        self.lib.store.put(self.reader, 'version', local['versionId'], local)
        self.assertEqual(policy(self.lib, self.reader, local)['mode'], 'trusted_release')

    def test_shared_dependency_closure_ready_without_recipient_verification(self):
        _tool, skill = self.skill_with_tool()
        release = self.publish(skill)
        copied = self.post('/' + release['releaseId'] + '/copy', {'ensureOwned': True},
                           client=self.other).json
        self.assertEqual(copied['readiness'], 'ready')
        self.assertEqual(len(copied['dependencies']), 1)
        dependency = copied['dependencies'][0]
        self.assertEqual(dependency['readiness'], 'ready')
        self.assertEqual(dependency['admission']['source'], 'community')
        self.assertEqual(self.lib.store.list(self.reader, 'verification'), [])
        again = self.post('/' + release['releaseId'] + '/copy', {'ensureOwned': True},
                          client=self.other, key='second-exact-install').json
        self.assertTrue(again['alreadyOwned'])
        self.assertEqual(again['assetId'], copied['assetId'])
        self.assertEqual(again['dependencies'][0]['assetId'], dependency['assetId'])
        self.assertEqual(len(self.lib.store.list(self.reader, 'asset')), 2)


if __name__ == '__main__':
    unittest.main()
