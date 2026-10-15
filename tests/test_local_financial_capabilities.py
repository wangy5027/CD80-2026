"""Isolated accounts, real WASI and controlled route boundary tests."""
import json
import copy
import tempfile
import threading
import time
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch
from backend.app import create_app
from backend.app.modules.capability_library.contracts import CapabilityError
from backend.app.modules.capability_library.packages import zip_bytes
from backend.app.modules.capability_library.assertions import evaluate, validate_assertions

ROOT = Path(__file__).resolve().parents[1]


class LocalFinancialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app({'TESTING':True, 'DATA_DIR':self.temp.name, 'CAPABILITY_SKIP_PUBLIC_SEED':True,
                               'AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime'), 'GITEE_AI_API_KEY':'test-only'})
        self.client = self.app.test_client()
        self.owner = self.client.post('/desktop/api/register',json={'email':'local@example.invalid','password':'test-password'}).json['user']['id']
        self.lib = self.app.extensions['capability_library']

    def tearDown(self):
        self.app.extensions['workbench_service'].close()
        self.temp.cleanup()

    def package(self, name):
        folder = ROOT/'capabilities/local-financial/packages'/name
        return zip_bytes({p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()})

    def official_with_two_cases(self, identity):
        """Author isolated public fixtures with two examples for copy-only checks."""
        from backend.app.modules.capability_library.catalog import seed_catalog
        from backend.app.modules.capability_library.service import PUBLIC
        from backend.app.modules.capability_library.contracts import canonical, digest
        from backend.app.modules.capability_library.packages import file_index
        statuses = seed_catalog(self.lib)
        self.assertTrue(all(s['status'] == 'published' for s in statuses), statuses)
        asset = next(a for a in self.lib.store.list(PUBLIC, 'asset') if a['sourceIdentity'] == identity)
        source = self.lib.version(PUBLIC, asset['assetId'], asset['latestVersionId'])
        dependency = source['manifest']['dependencies'][0]
        tool = next(v for v in self.lib.store.list(PUBLIC, 'version')
                    if v['contentDigest'] == dependency['contentDigest'])
        manifest = copy.deepcopy(tool['manifest'])
        files = self.lib.files(PUBLIC, tool)
        case = copy.deepcopy(manifest['examples'][0])
        case.update(id='new-input', title='不同的新输入')
        if identity.endswith('financial-compute'):
            content = json.loads(files['original/sample.json'])
            content['records'][0]['source_fields']['eps'] = '0.20'
            content['records'][1]['source_fields']['eps'] = '0.30'
            files['original/new-sample.json'] = canonical(content).encode('utf-8')
            case['filePaths'] = ['original/new-sample.json']
            case['input']['file'] = 'new-sample.json'
            case['assertions'][0]['value'] = '0.10'
            case['assertions'][1]['value'] = '50.0'
        else:
            case['input']['limit'] = 1
            case['prompt'] = '读取东方财富最新1条财经新闻。'
        manifest['examples'].append(case)
        manifest['files'] = file_index(files)
        raw = zip_bytes({**files, 'capability.json': canonical(manifest).encode('utf-8')})
        for data in files.values():
            self.lib.store.blob(PUBLIC, digest(data), data)
        self.lib.store.blob(PUBLIC, digest(raw), raw)
        tool.update(manifest=manifest, contentDigest=digest({'manifest': manifest, 'files': file_index(files)}),
                    originalId=digest(raw), originalName='isolated-official-fixture.zip')
        self.lib.store.put(PUBLIC, 'version', tool['versionId'], tool)
        source['manifest']['dependencies'][0]['contentDigest'] = tool['contentDigest']
        source_files = self.lib.files(PUBLIC, source)
        source['manifest']['files'] = file_index(source_files)
        raw = zip_bytes({**source_files, 'capability.json': canonical(source['manifest']).encode('utf-8')})
        self.lib.store.blob(PUBLIC, digest(raw), raw)
        source.update(contentDigest=digest({'manifest': source['manifest'], 'files': file_index(source_files)}),
                      originalId=digest(raw), originalName='isolated-official-skill-fixture.zip')
        self.lib.store.put(PUBLIC, 'version', source['versionId'], source)
        return source

    def test_real_wasi_news_connector_and_trial_does_not_enable(self):
        ref = self.lib.import_package(self.owner,self.package('cd80-news-tool'),'news.zip')
        version = self.lib.version(self.owner,ref['assetId'],ref['versionId'])
        self.assertEqual(version['readiness'],'unverified',version['issues'])
        fixture = {'source':'eastmoney','items':[{'title':'测试','summary':'资料','published_at':'2026-10-13 09:00:00'}],
            'latest_published_at':'2026-10-13 09:00:00','cache':{'stale':False,'fetched_at':'2026-10-13T09:00:00'},'pagination':{'total':1},'warning':None}
        with patch.object(self.app.extensions['news_service'],'get_news',return_value=fixture) as call:
            record = self.lib.invoke(self.owner,{'assetId':ref['assetId'],'versionId':ref['versionId'],
                'configurationId':version['configurationId'],'input':{'source':'eastmoney','limit':1},'fileIds':[]},threading.Event(),trial=True)
        self.assertEqual(record['result']['content']['records'][0]['title'],'测试')
        self.assertEqual(len(record['dataReads']),1)
        self.assertFalse(self.lib.asset(self.owner,ref['assetId'])['enabled'])
        call.assert_called_once()

    def test_connections_reject_undeclared_write_unknown_user_and_bad_parameters(self):
        c=self.lib.data_connections;d=[{'identity':'cd80.news','operations':['latest']}]
        query={'connection':'cd80.news','operation':'latest','arguments':{'source':'eastmoney','limit':1,'offset':0,'q':''}}
        for bad in [dict(query,operation='delete'),dict(query,connection='https://evil.invalid'),dict(query,arguments={'source':'other'})]:
            with self.assertRaises(CapabilityError): c.call(self.owner,d,bad,threading.Event())
        with self.assertRaises(Exception): c.call('missing-account',d,query,threading.Event())
        response=self.app.test_client().get('/desktop/api/news',headers={'cd80.connector_grant':self.owner})
        self.assertEqual(response.status_code,401)

    def test_cancelled_read_never_calls_source(self):
        stop=threading.Event();stop.set()
        with self.assertRaises(CapabilityError):self.lib.data_connections.call(self.owner,[],{},stop)

    def test_assertions_are_strict_and_malformed_rules_rejected(self):
        self.assertEqual(evaluate({'x':[]},[{'path':['x'],'op':'minItems','value':1}]),[0])
        self.assertEqual(evaluate({'date':'2025-02-31'},[{'path':['date'],'op':'date','value':'iso-date'}]),[0])
        self.assertEqual(evaluate({'n':True},[{'path':['n'],'op':'type','value':'integer'}]),[0])
        with self.assertRaises(ValueError):validate_assertions([{'path':[],'op':'eval','value':'code'}])

    def test_no_write_operation_registered(self):
        for ops in self.lib.data_connections.registry.values():
            for spec in ops.values():
                self.assertIn(spec['method'],('GET','POST'))
                if spec['method']=='POST':
                    self.assertIn(spec['path'], {
                        '/desktop/api/financial-data/tables/{table}/query',
                        '/desktop/api/financial-data/tables/{table}/scan',
                        '/desktop/api/financial-data/tables/{table}/aggregate'})

    def test_package_date_filters_and_decimal_null_behavior(self):
        spec=importlib.util.spec_from_file_location('financial_query',ROOT/'capabilities/local-financial/src/query.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.assertEqual(module.number(None),None)
        self.assertEqual(module.number('000.10'),'0.10')
        payload={'input':{'code':'000001.SH','asset':'index','date':'2025-05-07'},'responses':[{'data':[{'trade_date':'20260918.0'}]}]}
        query=module.execute('history',payload)['request']['arguments']
        self.assertEqual(query['filters'][-1]['value'],'20250507.0')
        payload['input'].pop('date')
        self.assertEqual(module.execute('history',payload)['request']['arguments']['filters'][-1]['value'],'20260918.0')
        payload['responses'].append({'data':[],'pagination':{'has_more':False}})
        result=module.execute('history',payload)['result']
        self.assertEqual(result['records'],[])
        self.assertIn('不自动替换日期',result['warning'])
        self.assertEqual(result['as_of'],'2026-09-18')
        with self.assertRaises(ValueError):module.execute('margin',{'input':{'code':'600000','date':'2025-01-01','start_date':'2024-01-01'},'responses':[]})

    def test_verification_invalidation_and_assertion_case(self):
        ref=self.lib.import_package(self.owner,self.package('cd80-news-tool'),'news.zip')
        version=self.lib.version(self.owner,ref['assetId'],ref['versionId'])
        case=self.lib.save_test_case(self.owner,{'name':'字段规则','assetId':ref['assetId'],'versionId':ref['versionId'],
            'input':{},'fileIds':[],'assertions':[{'path':['source'],'op':'eq','value':'akshare'}]})
        self.assertEqual(case['assertions'][0]['value'],'akshare')
        self.app.config['CD80_READ_API_KEY']='changed-only-in-test'
        with self.assertRaises(CapabilityError) as caught:self.lib.admit(self.owner,ref['assetId'],ref['versionId'],version['configurationId'],trial=True)
        self.assertEqual(caught.exception.code,'STALE_CONFIGURATION')

    def test_failed_source_and_malformed_request_do_not_fallback(self):
        c=self.lib.data_connections;d=[{'identity':'cd80.news','operations':['latest']}]
        with self.assertRaises(CapabilityError):c.call(self.owner,d,{'connection':[],'operation':'latest','arguments':{}},threading.Event())
        from backend.app.modules.workbench.errors import GatewayError
        with patch.object(self.app.extensions['news_service'],'get_news',side_effect=GatewayError('unavailable',502)) as call:
            with self.assertRaises(CapabilityError) as caught:c.call(self.owner,d,{'connection':'cd80.news','operation':'latest','arguments':{'source':'eastmoney','limit':1,'offset':0,'q':''}},threading.Event())
            self.assertEqual(caught.exception.code,'DATA_SOURCE_FAILED')
            self.assertEqual(call.call_count,1)

    def test_published_catalog_copies_are_independent_disabled_and_dependency_bound(self):
        from backend.app.modules.capability_library.catalog import seed_catalog
        from backend.app.modules.capability_library.service import PUBLIC
        statuses=seed_catalog(self.lib)
        self.assertEqual(len(statuses),26)
        self.assertTrue(all(s['status']=='published' for s in statuses),statuses)
        assets=self.lib.store.list(PUBLIC,'asset')
        assets.sort(key=lambda a:a['kind']!='tool')
        for asset in assets:
            response=self.client.post('/desktop/api/capabilities/assets/'+asset['assetId']+'/copy',
                json={'versionId':asset['latestVersionId']},headers={'Idempotency-Key':asset['assetId']})
            self.assertEqual(response.status_code,201,response.json)
            personal=self.lib.asset(self.owner,response.json['assetId'])
            version=self.lib.version(self.owner,personal['assetId'],personal['latestVersionId'])
            self.assertFalse(personal['enabled'])
            self.assertEqual(version['readiness'],'ready',version['issues'])
            self.assertEqual(version['admission']['source'],'official')
            self.assertNotEqual(personal['assetId'],asset['assetId'])
            self.assertEqual(len(version['dependencyLocks']),len(version['manifest']['dependencies']))
            self.assertIsNone(self.lib.evidence(self.owner,version))
        self.assertEqual(len(self.lib.store.list(self.owner,'asset')),26)

    def test_official_file_dependency_is_copied_without_running_packaged_examples(self):
        from backend.app.modules.capability_library.official import prepare
        source=self.official_with_two_cases('cd80/cd80-financial-compute')
        ref=prepare(self.lib,self.owner,source,threading.Event())
        version=self.lib.version(self.owner,ref['assetId'],ref['versionId'])
        dependency=version['dependencyLocks'][0]
        self.assertEqual(self.lib.store.list(self.owner,'invocation'),[])
        self.assertEqual(self.lib.store.list(self.owner,'verification'),[])
        tool_version=self.lib.version(self.owner,dependency['assetId'],dependency['versionId'])
        self.assertEqual(tool_version['validationPolicy'],'original_and_new')
        self.assertEqual(tool_version['admission']['source'],'official')
        self.assertEqual(tool_version['readiness'],'ready')
        self.assertIsNone(self.lib.evidence(self.owner,tool_version))
        self.assertFalse(self.lib.asset(self.owner,ref['assetId'])['enabled'])
        self.assertFalse(self.lib.asset(self.owner,dependency['assetId'])['enabled'])
        other=self.app.test_client()
        other.post('/desktop/api/register',json={'email':'other@example.invalid','password':'test-password'})
        self.assertEqual(other.get('/desktop/api/capabilities/assets?scope=personal').json['items'],[])

    def test_official_skill_preparation_requires_no_personal_connection(self):
        from backend.app.modules.capability_library.official import prepare, copy_version
        source=self.official_with_two_cases('cd80/cd80-news')
        old=copy_version(self.lib,self.owner,source)
        self.assertEqual(old['readiness'],'needs_configuration')
        with patch.object(self.app.extensions['news_service'],'get_news',side_effect=AssertionError('must not read')):
            ref=prepare(self.lib,self.owner,source,threading.Event(),old)
        self.assertEqual(ref['assetId'],old['assetId'])
        version=self.lib.version(self.owner,ref['assetId'],ref['versionId'])
        self.assertEqual(version['readiness'],'ready')
        self.assertFalse(self.lib.asset(self.owner,ref['assetId'])['enabled'])
        dep=version['dependencyLocks'][0]
        self.assertFalse(self.lib.asset(self.owner,dep['assetId'])['enabled'])
        self.assertEqual(self.lib.store.list(self.owner,'invocation'),[])
        self.assertEqual(self.lib.store.list(self.owner,'verification'),[])
        self.assertEqual(self.lib.store.list(self.owner,'connection'),[])

    def test_official_preparation_does_not_read_source_or_enable(self):
        from backend.app.modules.capability_library.official import prepare
        from backend.app.modules.workbench.errors import GatewayError
        source=self.official_with_two_cases('cd80/cd80-news')
        with patch.object(self.app.extensions['news_service'],'get_news',side_effect=GatewayError('failed',502)):
            ref=prepare(self.lib,self.owner,source,threading.Event())
        self.assertEqual(ref['readiness'],'ready')
        self.assertEqual(self.lib.store.list(self.owner,'invocation'),[])
        self.assertFalse(any(a['enabled'] for a in self.lib.store.list(self.owner,'asset')))


if __name__=='__main__':unittest.main()
