"""Official catalog ownership and explicit independent copies."""
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from tests import test_capability_library as helpers
from backend.app.modules.capability_library.service import PUBLIC
from backend.app.modules.agent_platform.contracts import validate_agent_spec
BASE=helpers.BASE

class OfficialCopyTests(unittest.TestCase):
    setUp=helpers.CapabilityLibraryTests.setUp
    tearDown=helpers.CapabilityLibraryTests.tearDown
    sample=helpers.CapabilityLibraryTests.sample
    post=helpers.CapabilityLibraryTests.post
    wait=helpers.CapabilityLibraryTests.wait
    upload=helpers.CapabilityLibraryTests.upload
    version=helpers.CapabilityLibraryTests.version

    def source(self,identity):
        return next(a for a in self.lib.store.list(PUBLIC,'asset') if a['sourceIdentity']==identity)

    def copies(self,source):
        return self.client.get(BASE+'/assets/'+source['assetId']+'?scope=public').json['personalCopies']

    def test_duplicate_confirmation_names_and_idempotency_for_skill_and_tool(self):
        for identity in ('org.example/summary-skill','org.example/decimal-total'):
            source=self.source(identity);path='/assets/'+source['assetId']+'/copy';body={'versionId':source['latestVersionId']}
            first=self.post(path,body).json
            self.assertEqual(len(self.copies(source)),1)
            denied=self.post(path,body);self.assertEqual(denied.status_code,409)
            self.assertEqual(denied.json['code'],'DUPLICATE_CONFIRMATION_REQUIRED')
            second=self.post(path,{**body,'confirmDuplicate':True},key=identity).json
            repeat=self.post(path,{**body,'confirmDuplicate':True},key=identity).json
            self.assertEqual(second,repeat);self.assertNotEqual(first['assetId'],second['assetId'])
            self.assertEqual(self.lib.asset(self.owner,second['assetId'])['name'],source['name']+'_2')
            self.assertEqual(self.version(first)['contentDigest'],self.version(second)['contentDigest'])
            self.assertFalse(self.lib.asset(self.owner,second['assetId'])['enabled'])
            self.assertEqual(len(self.copies(source)),2)

    def test_older_version_counts_until_asset_deleted_other_accounts_do_not(self):
        source=self.source('org.example/summary-skill')
        copied=self.post('/assets/'+source['assetId']+'/copy',{'versionId':source['latestVersionId']}).json
        self.wait(self.upload(self.sample('summary-skill',lambda m,f:m.update(description='My edit')),copied['assetId']))
        self.assertEqual([(item['assetId'],item['versionId']) for item in self.copies(source)],
                         [(copied['assetId'],copied['versionId'])])
        ensured=self.post('/assets/'+source['assetId']+'/copy',{'versionId':source['latestVersionId'],'ensureOwned':True}).json
        self.assertTrue(ensured['alreadyOwned'])
        self.assertEqual((ensured['assetId'],ensured['versionId']),(copied['assetId'],copied['versionId']))
        fresh=self.post('/assets/'+source['assetId']+'/copy',{'versionId':source['latestVersionId'],'confirmDuplicate':True}).json
        detail=self.client.get(BASE+'/assets/'+fresh['assetId'])
        self.client.delete(BASE+'/assets/'+fresh['assetId'],headers={'If-Match':detail.headers['ETag']})
        self.assertEqual(len(self.copies(source)),1)
        original=self.client.get(BASE+'/assets/'+copied['assetId'])
        self.client.delete(BASE+'/assets/'+copied['assetId'],headers={'If-Match':original.headers['ETag']})
        self.assertEqual(self.copies(source),[])
        other=self.app.test_client();other.post('/desktop/api/register',json={'email':'second@example.invalid','password':'test-password'})
        self.assertEqual(other.get(BASE+'/assets/'+source['assetId']+'?scope=public').json['personalCopies'],[])

    def test_prepare_replay_and_duplicate_dependencies_remain_resolvable(self):
        source=self.source('org.example/decimal-total')
        body={'assetId':source['assetId'],'versionId':source['latestVersionId'],'scope':'public'}
        first=self.wait(self.post('/official-preparations',body,key='prepare'))
        self.assertEqual(first,self.wait(self.post('/official-preparations',body,key='prepare')))
        self.assertEqual(self.post('/official-preparations',body).status_code,409)
        second=self.wait(self.post('/official-preparations',{**body,'confirmDuplicate':True}))
        def add_dependency(m,f):m['dependencies']=[{'required':True,'reason':'test','sourceIdentity':source['sourceIdentity'],'contentDigest':self.version(first)['contentDigest']}]
        imported=self.wait(self.upload(self.sample('summary-skill',add_dependency)))
        self.assertNotIn('MISSING_DEPENDENCY',[i['code'] for i in self.version(imported)['issues']])
        self.assertNotEqual(first['assetId'],second['assetId'])

    def test_update_targets_requested_duplicate_and_preserves_alias(self):
        source=self.source('org.example/summary-skill');path='/assets/'+source['assetId']+'/copy'
        first=self.post(path,{'versionId':source['latestVersionId']}).json
        second=self.post(path,{'versionId':source['latestVersionId'],'confirmDuplicate':True}).json
        with patch('backend.app.modules.capability_library.releases.check_release'):
            updated=self.lib.import_package(PUBLIC,self.sample('summary-skill',lambda m,f:m.update(description='Official new')),'new.zip')
        detail=self.client.get(BASE+'/assets/'+second['assetId'])
        result=self.wait(self.client.post(BASE+'/assets/'+second['assetId']+'/official-update',json={'versionId':updated['versionId']},headers={'If-Match':detail.headers['ETag'],'Idempotency-Key':'update-second'}))
        self.assertEqual(result['assetId'],second['assetId'])
        self.assertEqual(self.lib.asset(self.owner,first['assetId'])['latestVersionId'],first['versionId'])
        self.assertEqual(self.lib.asset(self.owner,second['assetId'])['name'],source['name']+'_2')

    def test_preset_copies_confirm_names_idempotency_and_no_execution(self):
        platform=self.app.extensions['agent_platform'];ref=next(iter(platform.presets));source=platform.presets[ref]
        url='/desktop/api/agents/copy-preset'
        first=self.client.post(url,json={'agentRef':ref,'idempotencyKey':'first'})
        self.assertEqual(first.status_code,201)
        self.assertEqual(self.client.post(url,json={'agentRef':ref,'idempotencyKey':'unconfirmed'}).status_code,409)
        body={'agentRef':ref,'idempotencyKey':'second','confirmDuplicate':True}
        second=self.client.post(url,json=body);self.assertEqual(second.status_code,201)
        self.assertEqual(self.client.post(url,json=body).json,second.json)
        self.assertEqual(second.json['spec']['name'],source['name']+'_2')
        self.assertNotEqual(first.json['assetId'], second.json['assetId'])
        for copied in (first.json, second.json):
            modern = self.app.extensions['workbench_service'].asset_apps.detail(self.owner, copied['assetId'])
            self.assertEqual(modern['versionId'], copied['versionId'])
            self.assertEqual(modern['verification']['status'], 'unverified')
            self.assertIsNone(modern['enabledVersionId'])
            self.assertEqual(copied['detail'], '/desktop/api/asset-apps/' + copied['assetId'])
        validate_agent_spec(second.json['spec'])
        public=next(a for a in platform.list_agents(self.owner) if not a['personal'] and a['agentRef']==ref)
        self.assertEqual(len(public['personalCopies']),2)
        self.assertEqual(platform.repository.list_runs(self.owner),[])
        self.assertEqual(platform.repository.list_agents('other'),[])

    def test_restore_one_of_multiple_copies_keeps_the_other(self):
        source=self.source('org.example/summary-skill');path='/assets/'+source['assetId']+'/copy'
        first=self.post(path,{'versionId':source['latestVersionId']}).json
        second=self.post(path,{'versionId':source['latestVersionId'],'confirmDuplicate':True}).json
        detail=self.client.get(BASE+'/assets/'+first['assetId'])
        removed=self.client.delete(BASE+'/assets/'+first['assetId'],headers={'If-Match':detail.headers['ETag']})
        restored=self.client.post(BASE+'/assets/'+first['assetId']+'/restore',headers={'If-Match':removed.headers['ETag']})
        self.assertEqual(restored.status_code,200)
        self.assertEqual(len(self.copies(source)),2)
        self.assertEqual(self.lib.asset(self.owner,second['assetId'])['name'],source['name']+'_2')

    def test_parallel_copies_allocate_unique_names(self):
        from backend.app.modules.capability_library.official import copy_version
        source=self.source('org.example/summary-skill');version=self.lib.version(PUBLIC,source['assetId'],source['latestVersionId'])
        with ThreadPoolExecutor(max_workers=3) as pool:
            copied=list(pool.map(lambda _:copy_version(self.lib,self.owner,version,force_new=True),range(3)))
        names={self.lib.asset(self.owner,ref['assetId'])['name'] for ref in copied}
        self.assertEqual(names,{source['name'],source['name']+'_2',source['name']+'_3'})

if __name__=='__main__':unittest.main()
