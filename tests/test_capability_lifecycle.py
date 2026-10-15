"""Personal copies: explicit upgrades, effective readiness and recoverable deletion."""
import unittest
from unittest.mock import patch
from tests import test_capability_library as helpers
BASE=helpers.BASE
from backend.app.modules.capability_library.service import PUBLIC
from backend.app.modules.capability_library.contracts import CapabilityError

class CapabilityLifecycleTests(unittest.TestCase):
    setUp=helpers.CapabilityLibraryTests.setUp
    tearDown=helpers.CapabilityLibraryTests.tearDown
    sample=helpers.CapabilityLibraryTests.sample
    post=helpers.CapabilityLibraryTests.post
    wait=helpers.CapabilityLibraryTests.wait
    upload=helpers.CapabilityLibraryTests.upload
    version=helpers.CapabilityLibraryTests.version
    body=helpers.CapabilityLibraryTests.body
    enable=helpers.CapabilityLibraryTests.enable
    validated=helpers.CapabilityLibraryTests.validated
    verify_new=helpers.CapabilityLibraryTests.verify_new

    def official(self):
        public=next(a for a in self.lib.store.list(PUBLIC,'asset') if a['sourceIdentity']=='org.example/summary-skill')
        personal=self.post('/assets/'+public['assetId']+'/copy',{'versionId':public['latestVersionId']}).json
        with patch('backend.app.modules.capability_library.releases.check_release'):
            new=self.lib.import_package(PUBLIC,self.sample('summary-skill',lambda m,f:m.update(description='Updated official description')),'new.zip')
        return public,personal,new

    def test_update_is_read_only_until_confirmed_and_retains_edits(self):
        public,personal,new=self.official()
        changed=self.wait(self.upload(self.sample('summary-skill',lambda m,f:m.update(description='Personal edit')),personal['assetId']))
        saved=self.lib.asset(self.owner,personal['assetId'])
        detail=self.client.get(BASE+'/assets/'+personal['assetId'])
        self.assertTrue(detail.json['officialUpdate']['available'])
        self.assertEqual(saved,self.lib.asset(self.owner,personal['assetId']))
        response=self.client.post(BASE+'/assets/'+personal['assetId']+'/official-update',json={'versionId':new['versionId']},headers={'If-Match':detail.headers['ETag'],'Idempotency-Key':'upgrade'})
        result=self.wait(response)
        after=self.client.get(BASE+'/assets/'+personal['assetId']).json
        self.assertFalse(after['enabled']);self.assertFalse(after['officialUpdate']['available'])
        self.assertEqual(after['latestVersionId'],result['versionId'])
        self.assertEqual(self.version(changed)['manifest']['description'],'Personal edit')
        self.assertEqual(len(self.client.get(BASE+'/assets/'+personal['assetId']+'/versions').json['items']),3)
        self.assertEqual(self.version(result)['readiness'],'ready')
        self.assertEqual(self.client.post(BASE+'/assets/'+personal['assetId']+'/official-update',json={'versionId':new['versionId']},headers={'If-Match':detail.headers['ETag'],'Idempotency-Key':'stale'}).status_code,412)

    def test_edit_alone_is_not_an_official_release(self):
        source=next(a for a in self.lib.store.list(PUBLIC,'asset') if a['sourceIdentity']=='org.example/summary-skill')
        copied=self.post('/assets/'+source['assetId']+'/copy',{'versionId':source['latestVersionId']}).json
        self.wait(self.upload(self.sample('summary-skill',lambda m,f:m.update(description='Personal edit')),copied['assetId']))
        self.assertFalse(self.client.get(BASE+'/assets/'+copied['assetId']).json['officialUpdate']['available'])

    def test_changed_public_version_requires_new_confirmation(self):
        public,personal,new=self.official()
        from backend.app.modules.capability_library.lifecycle import update_official
        import threading
        with self.assertRaises(CapabilityError) as error:
            update_official(self.lib,self.owner,personal['assetId'],public['latestVersionId'],'"'+str(self.lib.asset(self.owner,personal['assetId'])['revision'])+'"',threading.Event())
        self.assertEqual(error.exception.code,'REVISION_CONFLICT')
        self.assertEqual(self.lib.asset(self.owner,personal['assetId'])['latestVersionId'],personal['versionId'])

    def test_runtime_change_invalidates_display_and_recheck_does_not_validate(self):
        result,body=self.validated()
        self.lib.runner_revision+='changed'
        detail=self.client.get(BASE+'/assets/'+result['assetId']).json
        self.assertTrue(detail['configurationStale']);self.assertEqual(detail['readiness'],'unverified')
        self.assertEqual(self.post('/invocations',body).status_code,409)
        checked=self.post('/assets/'+result['assetId']+'/recheck',{'versionId':result['versionId']})
        self.assertEqual(checked.status_code,200)
        self.assertFalse(self.version(result)['configurationStale'])
        self.assertEqual(self.version(result)['readiness'],'unverified')
        self.assertEqual(self.enable(result).status_code,409)

    def test_delete_hides_blocks_history_and_restores_disabled(self):
        for folder in ('summary-skill','decimal-total'):
            result=self.wait(self.upload(self.sample(folder)))
            url=BASE+'/assets/'+result['assetId']
            detail=self.client.get(url)
            response=self.client.delete(url,headers={'If-Match':detail.headers['ETag']})
            self.assertEqual(response.status_code,200)
            self.assertNotIn(result['assetId'],[a['assetId'] for a in self.client.get(BASE+'/assets').json['items']])
            self.assertIn(result['assetId'],[a['assetId'] for a in self.client.get(BASE+'/assets?deleted=true').json['items']])
            with self.assertRaises(CapabilityError):self.lib.admit(self.owner,result['assetId'],result['versionId'],historical=True,trial=True)
            self.assertEqual(self.client.delete(url,headers={'If-Match':detail.headers['ETag']}).status_code,412)
            restored=self.client.post(url+'/restore',headers={'If-Match':response.headers['ETag']})
            self.assertEqual(restored.status_code,200);self.assertFalse(restored.json['enabled']);self.assertNotIn('deletedAt',restored.json)
        source=self.lib.store.list(PUBLIC,'asset')[0]
        self.assertEqual(self.client.delete(BASE+'/assets/'+source['assetId'],headers={'If-Match':'"1"'}).status_code,404)
        other=self.app.test_client();other.post('/desktop/api/register',json={'email':'other@example.invalid','password':'test-password'})
        self.assertEqual(other.delete(url,headers={'If-Match':restored.headers['ETag']}).status_code,404)

if __name__=='__main__':unittest.main()
