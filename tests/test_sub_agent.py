import json
import struct
import tempfile
import threading
import time
import unittest
import zlib
import io
from unittest.mock import patch
from pathlib import Path

from backend.app import create_app
from backend.app.modules.sub_agent.store import Store, TERMINAL
from tests.sub_agent_fixture import seed, FixtureModel, MESSAGE, FOLLOWUP, CSV, wait_job
from backend.app.modules.capability_library.packages import zip_bytes, file_index
from backend.app.modules.capability_library.asset_validation import verify_suite


class SubAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='cd80-subagent-')
        self.app=create_app({'TESTING':True,'DATA_DIR':self.temp.name,'GITEE_AI_API_KEY':'fixture-only',
            'CAPABILITY_SKIP_PUBLIC_SEED':True})
        self.client=self.app.test_client()
        self.client.post('/desktop/api/register',json={'email':'sub@example.invalid','password':'test-password'})
        self.owner=self.client.get('/desktop/api/session').json['user']['id']
        self.w=self.app.extensions['workbench_service']; self.s=self.app.extensions['sub_agents']
        self.assertTrue(self.s.executor.status()['available'])
        self.refs=seed(self.w.capabilities,self.owner)
        self.s.model=FixtureModel(self.refs)
        self.count=0

    def tearDown(self):
        self.w.close(); self.temp.cleanup()

    def post(self,path,body,key=None,client=None):
        self.count+=1
        return (client or self.client).post('/desktop/api'+path,json=body,headers={'Idempotency-Key':key or str(self.count)})

    def submit(self,message=MESSAGE,cid='new',key=None):
        response=self.post('/conversations/'+cid+'/messages',{'message':message,'capabilityRefs':self.refs},key)
        self.assertEqual(response.status_code,202,response.json)
        return response.json

    def wait(self,rid,statuses=TERMINAL):
        until=time.monotonic()+50
        while time.monotonic()<until:
            run=self.s.store.run(self.owner,rid)
            if run['status'] in statuses:return run
            time.sleep(.03)
        self.fail('Timed out: '+json.dumps(run,ensure_ascii=False))

    def test_execution_context_is_internal_and_bound_to_idempotency(self):
        payload = {'message': '解释收益率', 'executionContext': {'kind': 'recipe', 'recipeId': 'forged', 'name': 'forged'}}
        with patch.object(self.s, 'kick'):
            external = self.post('/conversations/new/messages', payload, 'forged-context')
            self.assertEqual(external.status_code, 202, external.json)
            self.assertNotIn('executionContext', external.json)
            self.assertNotIn('executionContext', external.json['payload'])
            context = {'kind': 'recipe', 'recipeId': 'recipe-a', 'name': '研究方法'}
            internal = self.s.submit(self.owner, 'new', {'message': '解释收益率'}, 'internal-context', execution_context=context)
            self.assertEqual(internal['executionContext'], context)
            repeated = self.s.submit(self.owner, 'new', {'message': '解释收益率'}, 'internal-context', execution_context=context)
            self.assertEqual(repeated['id'], internal['id'])
            with self.assertRaises(Exception) as raised:
                self.s.submit(self.owner, 'new', {'message': '解释收益率'}, 'internal-context', execution_context={**context, 'recipeId': 'recipe-b'})
            self.assertEqual(raised.exception.status, 409)

    def test_tc_sa_001_real_wasi_three_corrections_queue_and_refresh(self):
        entered, release=threading.Event(), threading.Event()
        def gate(owner,rid,stop):
            entered.set()
            while not release.wait(.02):
                if stop.is_set():return
        self.s.before_code=gate
        approval_entered, approval_release = threading.Event(), threading.Event()
        original_model = self.s.model.call
        def approve_gate(stage, payload, *args):
            if stage == 'approve':
                approval_entered.set()
                if not approval_release.wait(10):
                    raise AssertionError('approval gate timeout')
            return original_model(stage, payload, *args)
        self.s.model.call = approve_gate
        first=self.submit(key='first')
        self.assertTrue(approval_entered.wait(15))
        awaiting = self.s.store.run(self.owner, first['id'])
        self.assertEqual(awaiting['status'], 'awaiting_main_approval')
        self.assertFalse(entered.is_set())
        self.assertFalse(awaiting['approvals'])
        approval_release.set()
        self.assertTrue(entered.wait(15))
        run=self.s.store.run(self.owner,first['id'])
        self.assertEqual(run['correctionsUsed'],0)
        self.assertEqual(run['approvals'][0]['decision'],'approve')
        self.assertEqual(sum(s['type']=='code' and s['status']=='succeeded' for s in run['steps']),0)
        second=self.submit(FOLLOWUP,run['conversationId'])
        # Fresh HTTP reads model a disconnected/reloaded observer; execution is independent.
        queue=self.client.get('/desktop/api/conversations/'+run['conversationId']+'/queue').json
        self.assertEqual(queue['activeRunId'],first['id'])
        self.assertEqual(queue['items'][1]['status'],'queued')
        self.assertNotIn(('plan',FOLLOWUP),self.s.model.calls)
        same=self.submit(key='first')
        self.assertEqual(first['id'],same['id'])
        release.set()
        done=self.wait(first['id'])
        self.assertEqual(done['status'],'succeeded',done.get('answer'))
        self.assertEqual(done['correctionsUsed'],3)
        self.assertEqual(len(done['approvals']),1)
        self.assertEqual([s['status'] for s in done['steps'] if s['type']=='code'],['failed','failed','succeeded'])
        self.assertIn('SyntaxError',done['steps'][1]['error'])
        self.assertIn('KeyError',done['steps'][2]['error'])
        self.assertEqual([r['decision'] for r in done['reviews']],['revise','accept'])
        assets={a['name']:a for a in done['deliveries']}
        self.assertEqual(set(assets),{'chart.png','used.csv','analysis.py'})
        self.assertEqual(self.client.get(assets['used.csv']['downloadUrl']).data,CSV)
        png=self.client.get(assets['chart.png']['downloadUrl']).data
        self.assertEqual(png[:8],b'\x89PNG\r\n\x1a\n')
        width,height=struct.unpack('>II',png[16:24]);self.assertEqual((width,height),(640,320))
        cursor=8; compressed=b''
        while cursor<len(png):
            size=struct.unpack('>I',png[cursor:cursor+4])[0]; kind=png[cursor+4:cursor+8]; data=png[cursor+8:cursor+8+size]
            self.assertEqual(zlib.crc32(kind+data)&0xffffffff,struct.unpack('>I',png[cursor+8+size:cursor+12+size])[0])
            if kind==b'IDAT':compressed+=data
            cursor+=12+size
        self.assertEqual(len(zlib.decompress(compressed)),height*(1+width*3))
        following=self.wait(second['id']);self.assertEqual(following['status'],'succeeded')
        self.assertIn('5条',following['answer'])
        task=self.w.get_task(self.owner,run['conversationId'])
        self.assertEqual([m['role'] for m in task['messages']],['user','assistant','user','assistant'])
        self.assertEqual(len(self.w.capabilities.store.list(self.owner,'asset')),2)
        events=self.s.store.events(self.owner,run['id'])
        self.assertEqual(sum(e['type']=='correction.started' for e in events),3)

    def test_fourth_correction_is_blocked(self):
        self.s.model.always_fail=True
        run=self.wait(self.submit()['id'])
        self.assertEqual(run['status'],'failed')
        self.assertEqual(run['correctionsUsed'],3)
        self.assertEqual(len([s for s in run['steps'] if s['type']=='code']),4)
        self.assertIn('3次',run['answer'])

    def test_cancel_pauses_queue_and_late_result_cannot_succeed(self):
        entered=threading.Event()
        def gate(owner,rid,stop):entered.set();stop.wait(10)
        self.s.before_code=gate
        first=self.submit();self.assertTrue(entered.wait(15))
        second=self.submit(FOLLOWUP,first['conversationId'])
        run=self.s.store.run(self.owner,first['id'])
        self.assertEqual(self.post('/sub-agent-runs/'+run['id']+'/cancel',{'revision':run['revision']}).status_code,200)
        self.assertEqual(self.wait(run['id'])['status'],'cancelled')
        q=self.s.queue(self.owner,run['conversationId']);self.assertTrue(q['paused'])
        self.assertEqual(self.s.store.run(self.owner,second['id'])['status'],'queued')

    def test_owner_isolation_idempotency_and_legacy_guard(self):
        entered, release=threading.Event(),threading.Event()
        def gate(owner,rid,stop):entered.set();stop.wait(3)
        self.s.before_code=gate
        run=self.submit(key='unique');self.assertTrue(entered.wait(15))
        other=self.app.test_client();other.post('/desktop/api/register',json={'email':'other@example.invalid','password':'test-password'})
        self.assertEqual(other.get('/desktop/api/sub-agent-runs/'+run['id']).status_code,404)
        self.assertEqual(other.get('/desktop/api/conversations/'+run['conversationId']+'/queue').status_code,404)
        self.assertEqual(self.post('/conversations/new/messages',{'message':'different'},'unique').status_code,409)
        self.assertEqual(self.client.post('/desktop/api/chat',json={'message':'bypass','taskId':run['conversationId']}).status_code,409)
        self.assertEqual(self.client.post('/desktop/api/agent-runs',json={'taskId':run['conversationId']}).status_code,409)
        self.assertEqual(self.client.post('/desktop/api/agent-dispatch',json={'taskId':run['conversationId'],'message':'bypass'}).status_code,409)
        self.assertEqual(self.post('/sub-agent-runs/'+run['id']+'/cancel',{'revision':0}).status_code,409)

    def test_restart_interrupts_waiting_and_preserves_queue(self):
        original=self.s.model.call
        self.s.model.call=lambda stage,payload,*args: {'mode':'ask','question':'Which range?'} if stage=='plan' else original(stage,payload,*args)
        first=self.submit();run=self.wait(first['id'],{'awaiting_user'})
        second=self.submit(FOLLOWUP,first['conversationId'])
        self.s.close()
        recovered=Store(self.temp.name)
        self.assertEqual(recovered.run(self.owner,first['id'])['status'],'interrupted')
        self.assertEqual(recovered.run(self.owner,second['id'])['status'],'queued')
        with recovered.transaction() as db:q=recovered.get(db,self.owner,'queue',first['conversationId'])
        self.assertTrue(q['paused']);self.assertIsNone(q['activeRunId'])

    def test_dedicated_answer_not_queue_and_withdraw(self):
        original=self.s.model.call
        def model(stage,payload,*args):
            if stage=='plan':
                if not payload['answers']:return {'mode':'ask','question':'Which range?'}
                return {'mode':'answer','answer':'已收到指定范围。'}
            return original(stage,payload,*args)
        self.s.model.call=model
        first=self.submit();run=self.wait(first['id'],{'awaiting_user'})
        second=self.submit(FOLLOWUP,first['conversationId'])
        path='/conversations/'+first['conversationId']+'/queue/items/'+second['id']+'/withdraw'
        self.assertEqual(self.post(path,{'revision':second['revision']}).status_code,200)
        path='/sub-agent-runs/'+run['id']+'/questions/'+run['question']['id']+'/answer'
        self.assertEqual(self.post(path,{'revision':run['revision'],'answer':'指定范围'}).status_code,200)
        self.assertEqual(self.wait(first['id'])['status'],'succeeded')

    def test_code_denied_when_forbidden_or_required_tool_not_used(self):
        original=self.s.model.call
        def model(stage,payload,*args):
            value=original(stage,payload,*args)
            if stage=='plan':value['allowTemporaryCode']=False
            return value
        self.s.model.call=model
        done=self.wait(self.submit()['id'])
        self.assertEqual(done['status'],'failed')
        self.assertFalse(any(s['type']=='code' and s['status']=='succeeded' for s in done['steps']))
        self.assertFalse(done['approvals'])

    def test_code_download_is_exact_self_contained_executed_source(self):
        done=self.wait(self.submit()['id'])
        self.assertEqual(done['status'],'succeeded',done.get('answer'))
        source=next(a for a in done['artifacts'] if a['name']=='analysis.py')
        code=self.s.store.artifact(self.owner,source['id'])[1].decode()
        data=next(a for a in done['artifacts'] if a['name']=='sample.csv')
        # Downloaded code includes the helper and runs in a fresh WASI guest.
        replay=self.s.executor.execute(code,{'files':{'sample':'/input/sample.csv'}},{'sample.csv':CSV})
        self.assertEqual(replay['result']['maxDrawdown'],'0.25')
        self.assertEqual(replay['files']['used.csv'],CSV)

    def test_review_cannot_accept_without_required_tool_execution(self):
        original = self.s.model.call
        def model(stage, payload, *args):
            if stage == 'plan':
                value = original(stage, payload, *args)
                value.update(requiredCapabilities=[self.refs[0]['assetId']], requiredExtensions=[])
                return value
            if stage == 'act':
                return {'type': 'finish', 'artifactIds': [], 'answer': 'unsupported success'}
            if stage == 'review':
                return {'decision': 'accept', 'answer': 'unsupported success'}
            return original(stage, payload, *args)
        self.s.model.call = model
        done = self.wait(self.submit()['id'])
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['correctionsUsed'], 3)
        self.assertFalse(done.get('deliveries'))

    def test_background_ordinary_chat_also_queues_without_duplicate_messages(self):
        entered,release=threading.Event(),threading.Event()
        self.s.model.call=lambda *args:{'mode':'chat'}
        def events(*args,**kwargs):
            entered.set();release.wait(10)
            yield {'type':'token','text':'plain answer'}
            yield {'type':'turn_end','tool_calls':[]}
        with patch.object(self.w.runtime.provider,'events',events):
            first=self.submit('ordinary')
            self.assertTrue(entered.wait(10))
            second=self.submit('next ordinary',first['conversationId'])
            self.assertEqual(second['status'],'queued')
            release.set()
            self.assertEqual(self.wait(first['id'])['status'],'succeeded')
            self.assertEqual(self.wait(second['id'])['status'],'succeeded')
        messages=self.w.get_task(self.owner,first['conversationId'])['messages']
        self.assertEqual([m['role'] for m in messages],['user','assistant','user','assistant'])

    def test_migrated_csv_receives_complete_result_artifact(self):
        lib=self.w.capabilities
        content={'source':'independent-fixture','records':[{'code':'000001','value':'0.30'},{'code':'000002','value':None}],
                 'coverage':{'retrieval_complete':True},'pagination':{'has_more':False}}
        files={'original/tool.py':('def run(payload):\n    result = '+repr(content)+'\n    result["records"]=result["records"][:payload.get("limit",2)]\n    return result\n').encode()}
        manifest={'format':'cd80.capability/1','kind':'tool','name':'complete-result','description':'Independent complete data fixture',
            'source':{'identity':'fixture/complete-result','format':'platform'},'dependencies':[],
            'permissions':{'network':'none','externalEffects':'none','inputFiles':False,'outputFiles':False},'files':file_index(files),
            'tool':{'inputSchema':{'type':'object','properties':{'limit':{'type':'integer','minimum':1,'maximum':2}},'additionalProperties':False},'execution':{'type':'python_wasi','path':'original/tool.py','callable':'run'}}}
        producer=lib.import_package(self.owner,zip_bytes({**files,'capability.json':json.dumps(manifest).encode()}),'producer.zip')
        root=Path(__file__).resolve().parents[1]
        export=lib.import_package(self.owner,(root/'capabilities/local-financial/migration-candidates/cd80-csv-export-tool.zip').read_bytes(),'csv.zip')
        uploaded=self.client.post('/desktop/api/files',data={'file':(io.BytesIO(json.dumps(content).encode()),'complete.json')}).json
        short_content={**content,'records':content['records'][:1]}
        short_upload=self.client.post('/desktop/api/files',data={'file':(io.BytesIO(json.dumps(short_content).encode()),'one.json')}).json
        self.refs=[]
        for ref,args,ids,expected in [(producer,{},[],content),(export,{'file':uploaded['id']+'.json'},[uploaded['id']],{'rows':2,'file':'result.csv','source':'independent-fixture','warnings':[]})]:
            v=lib.version(self.owner,ref['assetId'],ref['versionId'])
            new_args, new_files, new_expected = ({'limit':1}, [], short_content) if ref is producer else (
                {'file':short_upload['id']+'.json'}, [short_upload['id']], {**expected,'rows':1})
            cases=[{'input':a,'fileIds':f,'expectation':{'mode':'json_equals','value':e}}
                   for a,f,e in [(args,ids,expected),(new_args,new_files,new_expected)]]
            job=verify_suite(lib,self.owner,{'assetId':v['assetId'],'versionId':v['versionId'],'configurationId':v['configurationId'],'cases':cases})
            self.assertEqual(wait_job(lib,self.owner,job)['status'],'passed')
            a=lib.asset(self.owner,v['assetId']);lib.update_asset(self.owner,a['assetId'],{'enabled':True,'activeVersionId':v['versionId']},f'"{a["revision"]}"')
            self.refs.append({k:v[k] for k in ('assetId','versionId')})
        def model(stage,payload,*_):
            if stage=='plan':return {'mode':'delegate','goal':'Export fixture','deliverables':['CSV'],'acceptanceCriteria':['2 rows','leading zeros'],
                'constraints':['no network'],'preferredCapabilities':self.refs,'requiredCapabilities':[],'allowTemporaryCode':False,'requiredExtensions':['.csv']}
            if stage=='act':
                done=[s for s in payload['steps'] if s['status']=='succeeded']
                if not done:return {'type':'tool','name':'cap_'+producer['versionId'],'arguments':{}}
                if len(done)==1:
                    artifact=next(a for a in payload['artifacts'] if a['name']=='tool-result.json')
                    return {'type':'tool','name':'cap_'+export['versionId'],'arguments':{'file':artifact['id']+'.json'},'inputRefs':[artifact['id']]}
                return {'type':'finish','answer':'Exported two fixture rows.','artifactIds':[a['id'] for a in payload['artifacts'] if a['name']=='result.csv']}
            if stage=='review':return {'decision':'accept','answer':'Exported two fixture rows.'}
            raise AssertionError(stage)
        self.s.model.call=model
        run=self.wait(self.submit('Export complete fixture JSON to CSV')['id'])
        self.assertEqual(run['status'],'succeeded',run.get('answer'))
        artifact=next(a for a in run['deliveries'] if a['name']=='result.csv')
        text=self.client.get(artifact['downloadUrl']).data.decode('utf-8-sig')
        self.assertIn('000001,0.30',text);self.assertIn('000002,',text)
        self.assertFalse(any(s['type']=='code' for s in run['steps']))

    def test_previous_tool_artifact_is_granted_to_next_tool(self):
        lib=self.w.capabilities
        files={'original/tool.py':b"import csv\ndef run(payload):\n    return {'count':len(list(csv.DictReader(open(payload['filePath']))))}\n"}
        manifest={'format':'cd80.capability/1','kind':'tool','name':'count-rows','description':'Count actual granted CSV',
            'source':{'identity':'fixture/count','format':'platform'},'dependencies':[],
            'permissions':{'network':'none','externalEffects':'none','inputFiles':True,'outputFiles':False},'files':file_index(files),
            'tool':{'inputSchema':{'type':'object','properties':{'filePath':{'type':'string'}},'required':['filePath'],'additionalProperties':False},
                'execution':{'type':'python_wasi','path':'original/tool.py','callable':'run'}}}
        imported=lib.import_package(self.owner,zip_bytes({**files,'capability.json':json.dumps(manifest).encode()}),'counter.zip')
        v=lib.version(self.owner,imported['assetId'],imported['versionId'])
        uploaded=self.client.post('/desktop/api/files',data={'file':(io.BytesIO(CSV),'sample.csv')}).json
        shorter=self.client.post('/desktop/api/files',data={'file':(io.BytesIO(b''.join(CSV.splitlines(keepends=True)[:3])),'short.csv')}).json
        cases=[{'fileIds':[item['id']], 'input':{'filePath':'/input/'+item['id']+'.csv'},
                'expectation':{'mode':'json_equals','value':{'count':count}}} for item,count in [(uploaded,5),(shorter,2)]]
        job=verify_suite(lib,self.owner,{**{k:v[k] for k in ('assetId','versionId','configurationId')},'cases':cases})
        self.assertEqual(wait_job(lib,self.owner,job)['status'],'passed')
        a=lib.asset(self.owner,v['assetId']);lib.update_asset(self.owner,a['assetId'],{'enabled':True,'activeVersionId':v['versionId']},f'"{a["revision"]}"')
        self.refs.append({k:v[k] for k in ('assetId','versionId')})
        original=self.s.model.call;alias='cap_'+v['versionId']
        def model(stage,payload,*args):
            if stage=='act' and any(s['type']=='tool' and s['status']=='succeeded' for s in payload['steps']) and not any(s.get('name')==alias for s in payload['steps']):
                data=next(a for a in payload['artifacts'] if a['name']=='sample.csv')
                return {'type':'tool','name':alias,'arguments':{'filePath':'/input/'+data['id']+'.csv'},'inputRefs':[data['id']]}
            return original(stage,payload,*args)
        self.s.model.call=model
        done=self.wait(self.submit()['id']);self.assertEqual(done['status'],'succeeded',done.get('answer'))
        step=next(s for s in done['steps'] if s.get('name')==alias)
        self.assertIn('"count": 5',step['result']['resultPreview'])
        calls=[i for i in lib.store.list(self.owner,'invocation') if i.get('taskId')==done['conversationId'] and i['assetId']==v['assetId']]
        self.assertEqual(len(calls),1);self.assertTrue(calls[0]['fileGrants'])

    def test_main_direct_tool_then_answer_without_delegation(self):
        def model(stage, payload, *args):
            self.assertEqual(stage, 'plan')
            if not payload['steps']:
                return {'mode': 'direct', 'action': {'type': 'tool', 'name': 'cap_' + self.refs[0]['versionId'], 'arguments': {}, 'purpose': '读取样例条数'}}
            self.assertIn('rows', payload['observations'][-1]['resultPreview'])
            return {'mode': 'answer', 'answer': '样例有5条数据。'}
        self.s.model.call = model
        run = self.wait(self.submit('查询样例有多少条数据')['id'])
        self.assertEqual(run['status'], 'succeeded', run.get('answer'))
        self.assertEqual(run['kind'], 'direct')
        self.assertNotIn('delegation', run)
        self.assertEqual(len(run['steps']), 1)
        self.assertEqual(run['steps'][0]['label'], 'sample-reader')
        self.assertIn('5', run['steps'][0]['summary'])
        self.assertEqual(run['steps'][0]['purpose'], '读取样例条数')

    def test_main_direct_upgrades_same_run_and_reuses_actual_data(self):
        original = self.s.model.call
        def model(stage, payload, *args):
            if stage == 'plan' and not payload['steps']:
                return {'mode': 'direct', 'action': {'type': 'tool', 'name': 'cap_' + self.refs[0]['versionId'], 'arguments': {}}}
            if stage == 'plan':
                self.assertTrue(any(a['name'] == 'sample.csv' for a in payload['artifacts']))
            return original(stage, payload, *args)
        self.s.model.call = model
        first = self.submit()
        run = self.wait(first['id'])
        self.assertEqual(run['status'], 'succeeded', run.get('answer'))
        self.assertEqual(run['logicalTaskId'], first['logicalTaskId'])
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertEqual(len([s for s in run['steps'] if s['type'] == 'tool']), 1)
        data = next(a for a in run['artifacts'] if a['name'] == 'sample.csv')
        self.assertTrue(all(s['inputRefs'] == [data['id']] for s in run['steps'] if s['type'] == 'code'))

    def test_dependency_assessment_cannot_silently_choose_chat(self):
        def model(stage, payload, *args):
            return {'mode': 'chat', 'assessment': {'dependentProcessing': True}}
        self.s.model.call = model
        with patch.object(self.w, 'start_chat', side_effect=AssertionError('must not use chat')):
            run = self.wait(self.submit('先读取资料，再处理和生成报告')['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertFalse(run['steps'])

    def test_model_timeout_retry_is_not_task_failure(self):
        from backend.app.modules.workbench.errors import GatewayError
        count = 0
        def model(stage, payload, *args):
            nonlocal count
            count += 1
            if count == 1:
                raise GatewayError('old misleading deadline text', 504, 'agent_model_deadline')
            return {'mode': 'answer', 'answer': '请求重试成功。'}
        self.s.model.call = model
        run = self.wait(self.submit()['id'])
        self.assertEqual(run['status'], 'succeeded', run.get('answer'))
        self.assertEqual(run['correctionsUsed'], 1)
        self.assertEqual([r['status'] for r in run['modelCalls']], ['failed', 'succeeded'])
        self.assertNotIn('retryNotice', run)
        self.assertFalse(self.s.queue(self.owner, run['conversationId'])['paused'])
        messages = self.w.get_task(self.owner, run['conversationId'])['messages']
        self.assertFalse(any('任务未完成' in m['content'] for m in messages))

    def test_approval_timeout_exhaustion_closes_step_and_pauses_queue(self):
        from backend.app.modules.workbench.errors import GatewayError
        original = self.s.model.call
        entered, release = threading.Event(), threading.Event()
        def model(stage, payload, *args):
            if stage == 'approve':
                entered.set(); release.wait(10)
                raise GatewayError('deadline', 504, 'agent_model_deadline')
            return original(stage, payload, *args)
        self.s.model.call = model
        first = self.submit(); self.assertTrue(entered.wait(15))
        second = self.submit(FOLLOWUP, first['conversationId'])
        release.set()
        run = self.wait(first['id'])
        self.assertEqual(run['status'], 'failed', run.get('answer'))
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertFalse(any(s['status'] == 'running' for s in run['steps'] + run['modelCalls']))
        self.assertEqual(len([s for s in run['steps'] if s['type'] == 'tool']), 1)
        q = self.s.queue(self.owner, run['conversationId'])
        self.assertTrue(q['paused']); self.assertIn('超时', q['pauseReason'])
        self.assertEqual(self.s.store.run(self.owner, second['id'])['status'], 'queued')
        with self.assertRaises(Exception):
            self.s.mutate(self.owner, run['id'], lambda r: r.update(status='succeeded'))

    def test_whole_task_deadline_has_no_running_steps_and_pauses(self):
        self.s.max_seconds = -1
        run = self.wait(self.submit()['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertTrue(self.s.queue(self.owner, run['conversationId'])['paused'])
        self.assertFalse(any(s['status'] == 'running' for s in run.get('modelCalls', [])))

    def test_application_requires_explicit_intent_and_handoff_pauses(self):
        self.s.model.call = lambda *args: {'mode': 'application', 'assessment': {'explicitApplication': False}}
        with patch.object(self.w.platform, '_dispatch') as dispatch:
            run = self.wait(self.submit()['id'])
            self.assertEqual(run['status'], 'failed'); dispatch.assert_not_called()
        self.s.model.call = lambda *args: {'mode': 'application', 'assessment': {'explicitApplication': True}}
        with patch.object(self.w.platform, '_dispatch', return_value={'intent': 'clarify', 'question': '请补充应用参数。'}) as dispatch:
            run = self.wait(self.submit('创建一个可重复使用的应用')['id'])
            self.assertEqual(run['kind'], 'application'); dispatch.assert_called_once()
            self.assertTrue(self.s.queue(self.owner, run['conversationId'])['paused'])

    def test_main_direct_tool_respects_disabled_web_search(self):
        from backend.app.modules.workbench.tool_registry import ToolSpec, ToolResult
        calls = []
        self.w.runtime.tools.register(ToolSpec('test_search', 'Web search', {'type': 'object'},
            lambda args, ctx: (calls.append(args), ToolResult({'found': True}))[1], capabilities=frozenset({'search'})))
        def model(stage, payload, *args):
            self.assertNotIn('test_search', [t['function']['name'] for t in payload['availableTools']])
            return {'mode': 'direct', 'action': {'type': 'tool', 'name': 'test_search', 'arguments': {}}}
        self.s.model.call = model
        result = self.post('/conversations/new/messages', {'message': '查询数据', 'searchMode': 'off'}).json
        run = self.wait(result['id'])
        self.assertEqual(run['status'], 'failed')
        self.assertFalse(calls)


if __name__=='__main__':unittest.main()
