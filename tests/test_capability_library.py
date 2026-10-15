"""End-to-end lifecycle tests using isolated databases and the real WASI runner."""
import copy
import base64
import io
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from backend.app import create_app
from backend.app.modules.capability_library.contracts import canonical, digest
from backend.app.modules.capability_library.packages import parse_package, zip_bytes, file_index
from backend.app.modules.capability_library.mcp import protect_secret, _MAC_KEY_NAME
from backend.app.modules.capability_library.contracts import CapabilityError

ROOT=Path(__file__).resolve().parents[1]
BASE='/desktop/api/capabilities'


class CapabilityLibraryTests(unittest.TestCase):
    def setUp(self):
        validation_root=ROOT/'data'/'capability-validation'
        validation_root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='cd80-capability-',dir=validation_root)
        self.app=create_app({'TESTING':True,'DATA_DIR':self.temp.name,'GITEE_AI_API_KEY':'test-only','AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        self.client=self.app.test_client()
        self.client.post('/desktop/api/register',json={'email':'owner@example.invalid','password':'test-password'})
        self.lib=self.app.extensions['capability_library']
        self.backend=self.app.extensions['workbench_service']
        self.owner=self.client.get('/desktop/api/session').json['user']['id']
        self.counter=0

    def tearDown(self):
        self.backend.close()
        self.temp.cleanup()

    def post(self,path,body=None,key=None):
        self.counter+=1
        return self.client.post(BASE+path,json=body or {},headers={'Idempotency-Key':key or str(self.counter)})

    def wait(self,response):
        self.assertIn(response.status_code,(200,201,202),response.json)
        job=response.json
        if 'jobId' not in job: return job
        end=time.monotonic()+30
        while job['status'] in {'queued','running','cancelling'} and time.monotonic()<end:
            time.sleep(.02)
            job=self.client.get(BASE+'/jobs/'+job['jobId']).json
        self.assertEqual(job['status'],'completed',job)
        return job['result']

    def sample(self,folder='decimal-total',edit=None):
        path=ROOT/'docs/capability-library/examples'/folder
        files={str(p.relative_to(path)).replace('\\','/'):p.read_bytes() for p in path.rglob('*') if p.is_file()}
        if edit:
            manifest=json.loads(files['capability.json'])
            edit(manifest,files)
            manifest['files']=file_index({p:b for p,b in files.items() if p not in {'capability.json','provenance.json'}})
            files['capability.json']=canonical(manifest).encode()
        return zip_bytes(files)

    def upload(self,raw,target=None,key=None):
        self.counter+=1
        data={'file':(io.BytesIO(raw),'test.zip')}
        if target: data['targetAssetId']=target
        return self.client.post(BASE+'/imports',data=data,headers={'Idempotency-Key':key or str(self.counter)})

    def version(self,result):
        return self.client.get(f"{BASE}/assets/{result['assetId']}/versions/{result['versionId']}").json

    def body(self,result):
        version=self.version(result)
        return {'assetId':result['assetId'],'versionId':result['versionId'],'configurationId':version['configurationId'],'input':{'values':['0.10','0.20']},'fileIds':[]}

    def enable(self,result):
        asset=self.client.get(BASE+'/assets/'+result['assetId'])
        return self.client.patch(BASE+'/assets/'+result['assetId'],json={'enabled':True,'activeVersionId':result['versionId']},headers={'If-Match':asset.headers['ETag']})

    def verify_new(self, body, new_input, new_expectation=None, new_files=None):
        first = {k: body[k] for k in ('input', 'fileIds', 'expectation')}
        second = {'input': new_input, 'fileIds': body['fileIds'] if new_files is None else new_files,
                  'expectation': new_expectation or body['expectation']}
        return self.post('/validation-suites', {**{k: body[k] for k in ('assetId', 'versionId', 'configurationId')},
                                              'cases': [first, second]})

    def validated(self):
        result=self.wait(self.upload(self.sample()))
        body=self.body(result)
        validation=self.wait(self.verify_new({**body,'expectation':{'mode':'json_equals','value':{'total':'0.30'}}},
            {'values': ['1.20', '2.30']}, {'mode': 'json_equals', 'value': {'total': '3.50'}}))
        self.assertEqual(validation['status'],'passed')
        self.assertEqual(self.enable(result).status_code,200)
        return result,body

    def test_real_wasi_lifecycle_export_disable_and_cross_account(self):
        result,body=self.validated()
        invocation=self.wait(self.post('/invocations',body))
        self.assertEqual(invocation['result']['content'],{'total':'0.30'})
        self.assertEqual(invocation['status'],'succeeded')
        export=self.wait(self.post('/exports',{'assetId':result['assetId'],'versionId':result['versionId'],'format':'platform'}))
        raw=self.client.get(export['downloadUrl']).data
        parsed,files=parse_package(raw,'roundtrip.zip')
        self.assertEqual(parsed,self.version(result)['manifest'])
        self.assertEqual(files['original/tool.py'],self.lib.files(self.owner,self.version(result))['original/tool.py'])
        other=self.app.test_client()
        other.post('/desktop/api/register',json={'email':'other@example.invalid','password':'test-password'})
        for path in ['/assets/'+result['assetId'],'/invocations/'+invocation['invocationId'],export['downloadUrl'][len(BASE):]]:
            self.assertEqual(other.get(BASE+path).status_code,404)
        asset=self.client.get(BASE+'/assets/'+result['assetId'])
        self.assertEqual(self.client.patch(BASE+'/assets/'+result['assetId'],json={'enabled':False},headers={'If-Match':asset.headers['ETag']}).status_code,200)
        self.assertEqual(self.post('/invocations',body).status_code,403)
        self.assertEqual(self.client.patch(BASE+'/assets/'+result['assetId'],json={'enabled':True},headers={'If-Match':asset.headers['ETag']}).status_code,412)

    def test_import_never_executes_and_unsafe_package_is_blocked(self):
        marker=Path(self.temp.name)/'must-not-exist'
        def edit(manifest,files):
            files['original/tool.py']=f"open({str(marker)!r}, 'w').write('bad')\ndef run(payload): return {{}}\n".encode()
            manifest['permissions']['externalEffects']='write'
        with patch.object(self.lib.executor,'execute',side_effect=AssertionError('import executed code')):
            result=self.wait(self.upload(self.sample(edit=edit)))
        self.assertFalse(marker.exists())
        self.assertEqual(result['readiness'],'blocked')
        self.assertEqual(self.post('/verifications',{**self.body(result),'expectation':{'mode':'json_equals','value':{}}}).status_code,403)
        self.assertEqual(self.post('/invocations',self.body(result)).status_code,403)

    def test_dedup_new_version_and_immutable_original(self):
        raw=self.sample()
        first=self.wait(self.upload(raw))
        same=self.wait(self.upload(raw))
        self.assertEqual(first['versionId'],same['versionId'])
        self.assertTrue(same['deduplicated'])
        changed=self.wait(self.upload(self.sample(edit=lambda m,f:m.update(description='changed')),first['assetId']))
        self.assertNotEqual(first['versionId'],changed['versionId'])
        self.assertEqual(self.version(first)['manifest']['description'],'对十进制字符串求和。')
        self.assertEqual(self.client.get(BASE+'/assets/'+first['assetId']).json['activeVersionId'],None)

    def test_path_traversal_case_collision_symlink_and_hash_mismatch(self):
        for entries in [[('../escape',b'x')],[('a',b'x'),('A',b'y')]]:
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w') as archive:
                for name,data in entries: archive.writestr(name,data)
            with self.assertRaises(Exception): parse_package(buf.getvalue(),'bad.zip')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as archive:
            info=zipfile.ZipInfo('link'); info.external_attr=0o120777<<16
            archive.writestr(info,b'/tmp/secret')
        with self.assertRaises(Exception): parse_package(buf.getvalue(),'bad.zip')
        manifest,files=parse_package(self.sample(),'sample.zip')
        files['original/tool.py']+=b'\n'
        with self.assertRaises(Exception): parse_package(zip_bytes({**files,'capability.json':canonical(manifest).encode()}),'bad.zip')

    def test_missing_dependencies_only_rechecked_on_reimport(self):
        tool=self.wait(self.upload(self.sample()))
        identity=self.version(tool)['manifest']['source']['identity']
        content_digest=self.version(tool)['contentDigest']
        def edit(manifest,files):
            manifest['dependencies']=[{'sourceIdentity':identity,'contentDigest':content_digest,'required':True,'reason':'test'}]
        skill_package=self.sample('summary-skill',edit)
        # A different account has no dependency initially.
        owner='isolated-test-owner'
        missing=self.lib.import_package(owner,skill_package,'skill.zip')
        self.assertEqual(missing['readiness'],'needs_configuration')
        self.lib.import_package(owner,self.sample(),'tool.zip')
        self.assertEqual(self.lib.version(owner,missing['assetId'],missing['versionId'])['readiness'],'needs_configuration')
        rechecked=self.lib.import_package(owner,skill_package,'skill.zip')
        self.assertEqual(rechecked['versionId'],missing['versionId'])
        self.assertEqual(rechecked['readiness'],'unverified')

    def test_idempotency_etag_and_owner_bound_pagination(self):
        first=self.upload(self.sample(),key='stable')
        second=self.upload(self.sample(),key='stable')
        self.assertEqual(first.json['jobId'],second.json['jobId'])
        self.wait(first)
        different=self.upload(self.sample('summary-skill'),key='stable')
        self.assertEqual(different.status_code,409)
        page=self.client.get(BASE+'/assets?scope=public&limit=1').json
        cursor=page['nextCursor']
        self.assertIsNotNone(cursor)
        self.assertEqual(self.client.get(BASE+'/assets?scope=personal&limit=1&cursor='+cursor).status_code,400)

    def test_skill_trial_uses_provider_then_requires_human_review(self):
        result=self.wait(self.upload(self.sample('summary-skill')))
        body={**self.body(result),'input':{},'expectation':{'mode':'manual_review','prompt':'Summarize: revenue 10.','criteria':'Preserve 10.'}}
        def events(*args,**kwargs):
            self.assertTrue(args[0][-1]['content'].startswith(('Summarize: revenue 10.', 'Summarize: revenue 20.')))
            self.assertNotIn('Read references',args[0][0]['content'])
            yield {'type':'token','text':'Revenue: 20.' if 'revenue 20.' in args[0][-1]['content'] else 'Revenue: 10.'}
            yield {'type':'turn_end','tool_calls':[]}
        with patch.object(self.backend.runtime.provider,'events',events):
            tested=self.wait(self.verify_new(body, {}, {'mode': 'manual_review', 'prompt': 'Summarize: revenue 20.', 'criteria': 'Preserve 20.'}))
        self.assertEqual(tested['status'],'awaiting_review')
        self.assertEqual(self.enable(result).status_code,409)
        reviewed=self.post('/verifications/'+tested['verificationId']+'/review',{'decision':'accept'})
        self.assertEqual(reviewed.json['status'],'passed')
        self.assertEqual(self.enable(result).status_code,200)

    def test_tool_call_from_chat_has_real_trace_and_lock(self):
        result,body=self.validated()
        calls=[]
        def events(messages,*args,**kwargs):
            calls.append(messages)
            if len(calls)==1:
                name=next(d['function']['name'] for d in kwargs['tools'] if d['function']['name'].startswith('cap_'))
                yield {'type':'turn_end','tool_calls':[{'id':'call_1','type':'function','function':{'name':name,'arguments':'{"values":["0.10","0.20"]}'}}]}
            else:
                self.assertIn('0.30',messages[-1]['content'])
                yield {'type':'token','text':'实际结果是 0.30。'}
                yield {'type':'turn_end','tool_calls':[]}
        with patch.object(self.backend.runtime.provider,'events',events):
            response=self.client.post('/desktop/api/chat',json={'message':'调用求和工具','capabilityRefs':[{'assetId':result['assetId'],'versionId':result['versionId']}]})
            stream=response.data.decode()
        self.assertEqual(response.status_code,200)
        self.assertIn('tool_completed',stream)
        task=self.backend.store.get(self.owner)['tasks'][0]
        self.assertEqual(task['capabilityLocks'][result['assetId']]['versionId'],result['versionId'])
        self.assertTrue(task['messages'][-1]['capabilityUsages'])
        changed=self.wait(self.upload(self.sample(edit=lambda m,f:m.update(description='new')),result['assetId']))
        conflict=self.client.post('/desktop/api/chat',json={'message':'again','taskId':task['id'],'capabilityRefs':[{'assetId':result['assetId'],'versionId':changed['versionId']}]})
        self.assertEqual(conflict.status_code,409)

    def test_plain_provider_definitions_remain_uncallable(self):
        for definition in [{'type':'function','name':'echo','description':'echo','parameters':{'type':'object'}},{'name':'echo','input_schema':{'type':'object'}}]:
            manifest,files=parse_package(canonical(definition).encode(),'tool.json')
            result=self.lib.import_package(self.owner,canonical(definition).encode(),'tool.json')
            self.assertEqual(result['readiness'],'needs_configuration')

    def test_automatic_tool_discovery_uses_main_graph_and_real_wasi(self):
        result, _ = self.validated()
        turns = []
        def events(messages, model, *args, **kwargs):
            turns.append(messages)
            names = [d['function']['name'] for d in kwargs['tools']]
            self.assertIn('load_skill', names)  # old skills remain available
            self.assertIn(result['assetId'], messages[0]['content'])
            if len(turns) == 1:
                name, arguments = 'library_load', {'assetId': result['assetId'], 'versionId': result['versionId']}
            elif len(turns) == 2:
                name, arguments = next(n for n in names if n.startswith('cap_')), {'values': ['0.10', '0.20']}
            else:
                self.assertIn('0.30', messages[-1]['content'])
                yield {'type': 'token', 'text': '工具返回 0.30。'}
                yield {'type': 'turn_end', 'tool_calls': []}
                return
            yield {'type': 'turn_end', 'tool_calls': [{'id': str(len(turns)), 'type': 'function', 'function': {'name': name, 'arguments': canonical(arguments)}}]}
        with patch.object(self.backend.runtime.provider, 'events', events):
            response = self.client.post('/desktop/api/chat', json={'message': '请调用十进制求和工具计算0.10+0.20', 'searchMode': 'off'})
            stream = response.data.decode()
        self.assertNotIn('event: error', stream)
        self.assertIn('tool_completed', stream)
        task = self.backend.store.get(self.owner)['tasks'][0]
        self.assertIn(result['assetId'], task['capabilityLocks'])
        self.assertTrue(self.lib.store.list(self.owner, 'invocation')[-1]['status'] == 'succeeded')
        self.assertFalse(self.backend.runtime._capability_sessions)

    def test_automatic_skill_loads_body_reference_and_persists_trace(self):
        result = self.wait(self.upload(self.sample('summary-skill')))
        def text_events(*args, **kwargs):
            yield {'type': 'token', 'text': '资料包含24条反馈。' if '反馈24条' in args[0][-1]['content'] else '资料包含12条反馈。'}
            yield {'type': 'turn_end', 'tool_calls': []}
        with patch.object(self.backend.runtime.provider, 'events', text_events):
            checked = self.wait(self.verify_new({**self.body(result), 'input': {}, 'expectation': {'mode': 'manual_review', 'prompt': '反馈12条', 'criteria': '保留12'}},
                {}, {'mode': 'manual_review', 'prompt': '反馈24条', 'criteria': '保留24'}))
        self.post('/verifications/' + checked['verificationId'] + '/review', {'decision': 'accept'})
        self.assertEqual(self.enable(result).status_code, 200)
        count = 0
        def events(messages, *args, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                name, args = 'library_load', {'assetId': result['assetId'], 'versionId': result['versionId']}
            elif count == 2:
                self.assertIn('Read references/method.md', messages[-1]['content'])
                name, args = 'library_resource', {'assetId': result['assetId'], 'path': 'original/references/method.md'}
            else:
                self.assertIn('Separate supplied facts', messages[-1]['content'])
                yield {'type': 'token', 'text': '根据摘要方法：12条反馈。'}
                yield {'type': 'turn_end', 'tool_calls': []}
                return
            yield {'type': 'turn_end', 'tool_calls': [{'id': str(count), 'type': 'function', 'function': {'name': name, 'arguments': canonical(args)}}]}
        with patch.object(self.backend.runtime.provider, 'events', events):
            stream = self.client.post('/desktop/api/chat', json={'message': '使用文本总结skill，反馈12条。找不到就直接回答找不到。'}).data.decode()
        self.assertNotIn('event: error', stream)
        self.assertIn('skill_loaded', stream)
        task = self.backend.store.get(self.owner)['tasks'][0]
        self.assertIn('资料摘要方法', task['messages'][-1]['skillsUsed'])

    def test_missing_skill_stop_does_not_leak_model_fallback(self):
        def events(*args, **kwargs):
            yield {'type': 'token', 'text': '找不到，但我自行总结全文如下：不应泄露的总结'}
            yield {'type': 'turn_end', 'tool_calls': []}
        with patch.object(self.backend.runtime.provider, 'events', events):
            stream = self.client.post('/desktop/api/chat', json={'message': '使用文本总结skill，找不到就直接回答找不到。资料：12条反馈。'}).data.decode()
        self.assertNotIn('不应泄露的总结', stream)
        self.assertIn('已按你的要求停止', stream)

    def test_discovery_pages_are_owner_scoped_and_disabled_cannot_load(self):
        from backend.app.modules.capability_library.chat_bridge import ChatCapabilities
        result, _ = self.validated()
        self.backend.store.update(self.owner, lambda state: state['tasks'].append({'id': 'bridge-test', 'messages': []}))
        bridge = ChatCapabilities(self.lib, self.owner, 'bridge-test', [], [])
        asset = self.lib.asset(self.owner, result['assetId'])
        for i in range(35):
            self.lib.store.put(self.owner, 'asset', f'fixture-{i:03}', {**asset, 'assetId': f'fixture-{i:03}'})
        first = bridge.catalog()
        self.assertEqual(len(first['items']), 30)
        self.assertEqual(len(bridge.catalog(first['nextOffset'])['items']), 6)
        self.lib.store.put('different-owner', 'asset', 'hidden', {**asset, 'assetId': 'hidden', 'name': '私密能力'})
        self.assertNotIn('私密能力', canonical(bridge.catalog()))
        asset['enabled'] = False
        self.lib.store.put(self.owner, 'asset', asset['assetId'], asset)
        outcome = bridge.execute('library_load', result, lambda _: None, threading.Event())
        self.assertEqual(outcome['code'], 'CAPABILITY_DISABLED')
        self.assertFalse(bridge.loaded)

    def test_usage_examples_are_pinned_to_manifest_and_available_before_copy(self):
        public = self.client.get(BASE + '/assets?scope=public').json['items']
        for asset in public:
            versions = self.client.get(BASE + '/assets/' + asset['assetId'] + '/versions?scope=public').json['items']
            self.assertIsNotNone(versions[0]['usageGuide'])
        result = self.wait(self.upload(self.sample(edit=lambda m, f: m.update(description='改过用途'))))
        versions = self.client.get(BASE + '/assets/' + result['assetId'] + '/versions').json['items']
        self.assertEqual(versions[0]['usageGuide']['expected'], {'total': '0.30'})
        self.assertIn('作者随版本提供', versions[0]['usageGuide']['source'])

    def test_selected_tool_is_a_preference_not_a_forced_execution(self):
        ref, _ = self.validated()
        before = len(self.lib.store.list(self.owner, 'invocation'))
        def events(*args, **kwargs):
            yield {'type': 'token', 'text': '该工具用于十进制字符串求和。本轮仅说明用途，没有执行。'}
            yield {'type': 'turn_end', 'tool_calls': []}
        with patch.object(self.backend.runtime.provider, 'events', events):
            response = self.client.post('/desktop/api/chat', json={'message': '介绍所选工具的用途，不要执行。',
                'capabilityRefs': [{k: ref[k] for k in ('assetId', 'versionId')}]})
            stream = response.data.decode()
        self.assertNotIn('event: error', stream)
        self.assertNotIn('event: tool_started', stream)
        self.assertEqual(len(self.lib.store.list(self.owner, 'invocation')), before)

    def test_dpapi_does_not_store_or_return_plaintext(self):
        secret=b'private-secret-fixture'
        encrypted=protect_secret(secret,data_dir=self.temp.name)
        self.assertNotIn(secret,encrypted)
        self.assertEqual(protect_secret(encrypted,decrypt=True,data_dir=self.temp.name),secret)
        connection=self.post('/connections',{'name':'fixture','sourceIdentity':'test/fixture','transport':'streamable_http','endpoint':'https://example.invalid/mcp','authMode':'bearer'}).json
        result=self.post('/connections/'+connection['connectionId']+'/credentials',{'action':'set','value':secret.decode()})
        self.assertEqual(result.status_code,200)
        self.assertNotIn(secret.decode(),json.dumps(result.json))
        stored=self.lib.store.get(self.owner,'credential',connection['connectionId'])
        self.assertNotIn(secret.decode(),json.dumps(stored))
        self.assertEqual(self.lib.mcp_client(self.owner,connection,threading.Event()).secret,secret.decode())
        if os.name!='nt':
            key=Path(self.temp.name)/_MAC_KEY_NAME
            self.assertTrue(key.is_file())
            self.assertEqual(key.stat().st_mode & 0o077,0)
            self.assertEqual(key.stat().st_uid,os.getuid())

    def test_non_windows_lost_key_and_corrupt_ciphertext_fail_without_overwrite(self):
        if os.name=='nt': self.skipTest('Windows continues using DPAPI')
        connection=self.post('/connections',{'name':'fixture','sourceIdentity':'test/fixture','transport':'streamable_http','endpoint':'https://example.invalid/mcp','authMode':'bearer'}).json
        key=connection['connectionId']
        self.assertEqual(self.post('/connections/'+key+'/credentials',{'action':'set','value':'first-secret'}).status_code,200)
        record=self.lib.store.get(self.owner,'credential',key)
        key_file=Path(self.temp.name)/_MAC_KEY_NAME
        original_key=key_file.read_bytes()
        key_file.unlink()
        failed=self.post('/connections/'+key+'/credentials',{'action':'set','value':'replacement-secret'})
        self.assertEqual(failed.status_code,503,failed.json)
        self.assertEqual(self.lib.store.get(self.owner,'credential',key),record)
        self.assertFalse(key_file.exists())
        with self.assertRaises(CapabilityError) as caught:
            self.lib.mcp_client(self.owner,connection,threading.Event())
        self.assertEqual(caught.exception.code,'CREDENTIAL_STORE_UNAVAILABLE')
        another=self.post('/connections',{'name':'second','sourceIdentity':'test/second','transport':'streamable_http','endpoint':'https://example.invalid/mcp','authMode':'bearer'}).json
        failed=self.post('/connections/'+another['connectionId']+'/credentials',{'action':'set','value':'new-secret'})
        self.assertEqual(failed.status_code,503,failed.json)
        self.assertFalse(key_file.exists())

        # Restore the original key only to isolate the separate corruption case.
        key_file.write_bytes(original_key)
        key_file.chmod(0o600)
        corrupt={'value':base64.b64encode(b'CD80F1:damaged').decode()}
        self.lib.store.put(self.owner,'credential',key,corrupt)
        failed=self.post('/connections/'+key+'/credentials',{'action':'set','value':'replacement-secret'})
        self.assertEqual(failed.status_code,503,failed.json)
        self.assertEqual(self.lib.store.get(self.owner,'credential',key),corrupt)

    def test_non_windows_key_permissions_and_symlinks_fail_closed(self):
        if os.name=='nt': self.skipTest('Windows continues using DPAPI')
        secret=b'private-secret-fixture'
        encrypted=protect_secret(secret,data_dir=self.temp.name)
        key_file=Path(self.temp.name)/_MAC_KEY_NAME
        key_file.chmod(0o644)
        with self.assertRaises(CapabilityError) as caught:
            protect_secret(encrypted,decrypt=True,data_dir=self.temp.name)
        self.assertEqual(caught.exception.code,'CREDENTIAL_STORE_UNAVAILABLE')
        key_file.chmod(0o600)
        backup=Path(self.temp.name)/'saved-key'
        key_file.rename(backup)
        key_file.symlink_to(backup)
        with self.assertRaises(CapabilityError) as caught:
            protect_secret(encrypted,decrypt=True,data_dir=self.temp.name)
        self.assertEqual(caught.exception.code,'CREDENTIAL_STORE_UNAVAILABLE')

    def test_real_readonly_mcp_and_unknown_permissions(self):
        tool={'name':'echo','description':'Read-only echo fixture','inputSchema':{'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False},'annotations':{'readOnlyHint':True}}
        calls=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_args): pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                method=body['method']
                calls.append(method)
                if method=='notifications/initialized':
                    self.send_response(202); self.end_headers(); return
                if method=='initialize': result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
                elif method=='tools/list': result={'tools':[tool]}
                elif method=='tools/call': result={'content':[{'type':'text','text':body['params']['arguments']['text']}],'structuredContent':{'text':body['params']['arguments']['text']}}
                else: result={}
                data=canonical({'jsonrpc':'2.0','id':body['id'],'result':result}).encode()
                self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(data))); self.send_header('Mcp-Session-Id','fixture-session'); self.end_headers(); self.wfile.write(data)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        address=f'http://127.0.0.1:{server.server_port}/mcp'
        try:
            connection=self.post('/connections',{'name':'fixture','sourceIdentity':'fixture/mcp','endpoint':address,'transport':'streamable_http','authMode':'none'}).json
            self.assertEqual(calls,[],'saving must not connect')
            discovery=self.wait(self.post('/connections/'+connection['connectionId']+'/discover'))
            self.assertNotIn('tools/call',calls)
            path='/connections/'+connection['connectionId']+'/tools/import'
            payload={'discoveryId':discovery['discoveryId'],'toolNames':['echo']}
            blocked=self.wait(self.post(path,payload))['items'][0]
            self.assertEqual(blocked['readiness'],'blocked')
            self.assertEqual(self.post('/verifications',{**self.body(blocked),'expectation':{'mode':'schema'}}).status_code,403)
            self.lib.policy[address]={'maintainer':'test fixture maintainer','evidence':'local implementation only returns supplied string','tools':{'echo':digest(tool)}}
            accepted=self.wait(self.post(path,payload))['items'][0]
            body={**self.body(accepted),'input':{'text':'hello'}}
            tested=self.wait(self.verify_new({**body,'expectation':{'mode':'json_equals','value':{'text':'hello'}}},
                {'text': 'new input'}, {'mode': 'json_equals', 'value': {'text': 'new input'}}))
            self.assertEqual(tested['status'],'passed')
            self.assertEqual(self.enable(accepted).status_code,200)
            invoked=self.wait(self.post('/invocations',body))
            self.assertEqual(invoked['result']['content'],{'text':'hello'})
            tool['description']='changed remotely'
            failed_job=self.post('/invocations',body)
            key=failed_job.json['jobId']
            end=time.monotonic()+5
            while time.monotonic()<end:
                state=self.client.get(BASE+'/jobs/'+key).json
                if state['status'] in {'failed','completed'}: break
                time.sleep(.02)
            self.assertEqual(state['error']['code'],'STALE_CONFIGURATION')
            self.assertEqual(calls.count('tools/call'),3,'schema change must block before execution')
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_sandbox_package_resources_and_readonly_mount(self):
        def edit(manifest,files):
            files['original/data.txt']=b'original-content'
            files['original/tool.py']=b'def run(payload):\n    return {"total":open("data.txt").read()}\n'
        result=self.wait(self.upload(self.sample(edit=edit)))
        tested=self.wait(self.verify_new({**self.body(result),'expectation':{'mode':'json_equals','value':{'total':'original-content'}}}, {'values': ['1.00']}))
        self.assertEqual(tested['status'],'passed')
        def write(manifest,files):
            edit(manifest,files)
            files['original/tool.py']=b'def run(payload):\n    open("data.txt","w").write("bad")\n    return {"total":"bad"}\n'
        changed=self.wait(self.upload(self.sample(edit=write),result['assetId']))
        response=self.verify_new({**self.body(changed),'expectation':{'mode':'json_equals','value':{'total':'bad'}}}, {'values': ['1.00']})
        key=response.json['jobId']
        for _ in range(250):
            state=self.client.get(BASE+'/jobs/'+key).json
            if state['status'] in {'failed','completed'}: break
            time.sleep(.02)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(self.lib.files(self.owner,self.version(changed))['original/data.txt'],b'original-content')

    def test_archived_history_disabled_and_public_copy_independence(self):
        result,_body=self.validated()
        asset=self.client.get(BASE+'/assets/'+result['assetId'])
        response=self.client.patch(BASE+'/assets/'+result['assetId'],json={'lifecycle':'archived'},headers={'If-Match':asset.headers['ETag']})
        self.assertEqual(response.status_code,200,response.json)
        with self.assertRaises(Exception): self.lib.admit(self.owner,result['assetId'],result['versionId'])
        self.lib.admit(self.owner,result['assetId'],result['versionId'],historical=True)
        self.client.patch(BASE+'/assets/'+result['assetId'],json={'enabled':False},headers={'If-Match':response.headers['ETag']})
        with self.assertRaises(Exception): self.lib.admit(self.owner,result['assetId'],result['versionId'],historical=True)
        public=self.client.get(BASE+'/assets?scope=public').json['items']
        skill=next(a for a in public if a['kind']=='skill')
        copied=self.post('/assets/'+skill['assetId']+'/copy',{'versionId':skill['latestVersionId']}).json
        self.assertNotEqual(copied['assetId'],skill['assetId'])
        self.assertFalse(self.client.get(BASE+'/assets/'+copied['assetId']).json['enabled'])

    def test_unknown_native_semantics_block_instead_of_drop(self):
        definition={'type':'function','function':{'name':'strict_tool','strict':True,'parameters':{'type':'object'}}}
        result=self.lib.import_package(self.owner,canonical(definition).encode(),'tool.json')
        self.assertEqual(result['readiness'],'blocked')

    def test_file_grants_do_not_carry_over_to_next_invocation(self):
        uploaded=self.client.post('/desktop/api/files',data={'file':(io.BytesIO(b'authorized-only'),'sample.txt')}).json
        file_id=uploaded['id']
        def edit(manifest,files):
            manifest['permissions']['inputFiles']=True
            manifest.pop('examples', None)  # This fixture changes the tool contract.
            schema={'type':'object','properties':{'path':{'type':'string'}},'required':['path'],'additionalProperties':False}
            manifest['tool']['inputSchema']=schema
            files['original/openai-tool.json']=canonical({'type':'function','function':{'name':'read_input','description':'Read granted input','parameters':schema}}).encode()
            files['original/tool.py']=b'from pathlib import Path\ndef run(payload):\n    return {"total":Path(payload["path"]).read_text()}\n'
        result=self.wait(self.upload(self.sample(edit=edit)))
        body={**self.body(result),'input':{'path':'/input/'+file_id+'.txt'},'fileIds':[file_id]}
        second=self.client.post('/desktop/api/files',data={'file':(io.BytesIO(b'new-authorized-input'),'new.txt')}).json
        verification=self.wait(self.verify_new({**body,'expectation':{'mode':'json_equals','value':{'total':'authorized-only'}}},
            {'path': '/input/' + second['id'] + '.txt'}, {'mode': 'json_equals', 'value': {'total': 'new-authorized-input'}}, [second['id']]))
        self.assertEqual(verification['status'],'passed')
        self.assertEqual(self.enable(result).status_code,200)
        job=self.post('/invocations',{**body,'fileIds':[]}).json
        for _ in range(250):
            state=self.client.get(BASE+'/jobs/'+job['jobId']).json
            if state['status'] in {'completed','failed'}: break
            time.sleep(.02)
        self.assertEqual(state['status'],'failed')
        raw=b'---\nname: hooked\ndescription: sample\nhooks: requires-shell\n---\nSample\n'
        result=self.lib.import_package(self.owner,raw,'SKILL.md')
        self.assertEqual(result['readiness'],'blocked')



    def test_free_trial_cannot_enable_and_case_owner_isolation(self):
        ref = self.wait(self.upload(self.sample()))
        body = self.body(ref)
        trial = self.wait(self.post('/trials', body))
        self.assertEqual(trial['result']['content'], {'total': '0.30'})
        self.assertEqual(self.lib.store.list(self.owner, 'verification'), [])
        self.assertEqual(self.enable(ref).status_code, 409)
        case = {k: v for k, v in body.items() if k != 'configurationId'}
        case.update(name='小数用例', expected={'total': '0.30'})
        saved = self.post('/test-cases', case, key='same-case').json
        self.assertEqual(saved['caseId'], self.post('/test-cases', case, key='same-case').json['caseId'])
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email':'case-other@example.invalid','password':'test-password'})
        self.assertEqual(other.get(BASE+'/test-cases?assetId='+ref['assetId']).status_code,404)
        self.assertEqual(other.delete(BASE+'/test-cases/'+saved['caseId']).status_code,404)
        self.assertEqual(other.get(BASE+'/trials/'+trial['trialId']).status_code,404)
        self.assertEqual(self.client.delete(BASE+'/test-cases/'+saved['caseId']).status_code,200)
        self.assertEqual(self.client.get(BASE+'/trials/'+trial['trialId']).json['result']['content'], {'total':'0.30'})
        checked = self.wait(self.verify_new({**body,'expectation':{'mode':'json_equals','value':{'total':'0.30'}}},
            {'values': ['1.20', '2.30']}, {'mode': 'json_equals', 'value': {'total': '3.50'}}))
        self.wait(self.post('/trials',body))
        self.assertEqual(self.version(ref)['lastVerificationId'], checked['verificationId'])

    def test_embedded_examples_validation_roundtrip_and_legacy(self):
        from backend.app.modules.capability_library.usage_guides import usage_guide
        legacy = json.loads((ROOT/'backend/app/modules/capability_library/legacy_usage_guides.json').read_text(encoding='utf-8'))
        self.assertGreaterEqual(len(legacy),2)
        old_skill=self.wait(self.upload(self.sample('summary-skill',edit=lambda m,f:m.pop('examples'))))
        self.assertIsNotNone(self.version(old_skill)['usageGuide'])
        ref=self.wait(self.upload(self.sample()))
        exported=self.wait(self.post('/exports',{'assetId':ref['assetId'],'versionId':ref['versionId'],'format':'platform'}))
        manifest,_=parse_package(self.client.get(exported['downloadUrl']).data,'roundtrip.zip')
        self.assertEqual(manifest['examples'][0]['expected'],{'total':'0.30'})
        changed=self.wait(self.upload(self.sample(edit=lambda m,f:m['examples'][0].update(input={'values':[1]}))))
        self.assertEqual(changed['readiness'],'blocked')
        old=self.wait(self.upload(self.sample(edit=lambda m,f:m.pop('examples'))))
        self.assertEqual(old['readiness'],'unverified')
        self.assertIsNone(self.version(old)['usageGuide'])

    def test_trial_input_errors_include_field_path(self):
        ref=self.wait(self.upload(self.sample()))
        response=self.post('/trials',{**self.body(ref),'input':{'values':[2]}})
        job=response.json
        for _ in range(250):
            state=self.client.get(BASE+'/jobs/'+job['jobId']).json
            if state['status'] not in {'queued','running'}: break
            time.sleep(.02)
        self.assertEqual(state['status'],'failed')
        self.assertEqual(state['error']['details']['fields'][0]['path'],['values',0])
        self.assertEqual(self.lib.store.list(self.owner,'verification'),[])

    def test_test_case_rejects_secrets_and_invalid_file_grants(self):
        ref=self.wait(self.upload(self.sample()))
        payload={'name':'case','assetId':ref['assetId'],'versionId':ref['versionId'],'input':{},'fileIds':['missing']}
        self.assertEqual(self.post('/test-cases',payload).status_code,403)
        payload.update(fileIds=[],input={'api_key':'secret-value-123456789'})
        self.assertEqual(self.post('/test-cases',payload).status_code,400)

    def test_official_release_rejects_missing_changed_or_unreviewed_evidence(self):
        from backend.app.modules.capability_library.releases import check_release
        from backend.app.modules.capability_library.contracts import CapabilityError
        evidence=json.loads((ROOT/'backend/app/modules/capability_library/official_example_evidence.json').read_text(encoding='utf-8'))
        for folder in ('decimal-total','summary-skill'):
            manifest=json.loads((ROOT/'docs/capability-library/examples'/folder/'capability.json').read_text(encoding='utf-8'))
            self.assertTrue(check_release(manifest,evidence))
            changed=copy.deepcopy(manifest);changed['description']='changed'
            with self.assertRaises(CapabilityError): check_release(changed,evidence)
            no_example=copy.deepcopy(manifest);no_example.pop('examples')
            with self.assertRaises(CapabilityError): check_release(no_example,evidence)
        unreviewed=copy.deepcopy(evidence)
        for record in unreviewed['examples']: record.pop('review',None)
        with self.assertRaises(CapabilityError): check_release(manifest,unreviewed)

if __name__=='__main__': unittest.main()
