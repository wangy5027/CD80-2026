import test from 'node:test';
import assert from 'node:assert/strict';
import { conversationMessages, timeline, modelCallTracePath, createModelCallTraceLoader } from './subAgentView.js';
import { USER_FACING_ERROR } from '../../shared/userFacingError.js';
test('old planning failure with no persisted user appears before its saved error reply', () => {
 const run = {id:'old',kind:'pending',status:'failed',message:'analyze',answer:'timeout'};
 const messages = [{role:'assistant',content:'timeout',subAgentMessageId:'old:assistant'}];
 const rows = conversationMessages(messages,[run]);
 assert.deepEqual(rows.map(r=>r.subAgentRun?.id || r.content),['analyze','old',USER_FACING_ERROR]);
 assert.equal(rows.filter(r=>r.subAgentRun).length,1);
});
test('only assistant replies tied to failed runs are masked; user and partial replies remain', () => {
 const messages=[{role:'user',content:'原始需求',subAgentMessageId:'failed:user'},
  {role:'assistant',content:'内部异常 abc123',subAgentMessageId:'failed:assistant'},
  {role:'assistant',content:'部分完成的实际分析',subAgentMessageId:'partial:assistant'},
  {role:'assistant',content:'普通回答'}];
 const rows=conversationMessages(messages,[{id:'failed',status:'failed',kind:'chat',message:'原始需求'},
  {id:'partial',status:'partial',kind:'chat',message:'其他需求'}]);
 assert.deepEqual(rows.filter(r=>r.role==='assistant').map(r=>r.content),
  [USER_FACING_ERROR,'部分完成的实际分析','普通回答']);
 assert.equal(rows.find(r=>r.role==='user').content,'原始需求');
});
test('task belongs immediately after its user message, before final answer and later turns', () => {
 const runs = [{ id:'a', kind:'delegate', status:'succeeded', message:'first' }, {id:'b',status:'queued',message:'second'}];
 const rows = conversationMessages([{role:'user',content:'first',subAgentMessageId:'a:user'},{role:'assistant',content:'result',subAgentMessageId:'a:assistant'}], runs);
 assert.deepEqual(rows.map(r=>r.subAgentRun?.id || r.content), ['first','a','result','second','b']);
 assert.equal(rows.filter(r=>r.subAgentRun?.id==='a').length,1);
});
test('completed old chat is not duplicated and repeated prompts use distinct task ids', () => {
 const messages=[{role:'user',content:'same',subAgentMessageId:'a:user'},{role:'assistant',content:'answer'},{role:'user',content:'same',subAgentMessageId:'b:user'}];
 const rows=conversationMessages(messages,[{id:'a',message:'same',kind:'chat',status:'succeeded'},{id:'b',message:'same',kind:'delegate',status:'running'}]);
 assert.equal(rows.length,4); assert.equal(rows.at(-1).subAgentRun.id,'b');
});
test('terminal task cannot display active steps even for an older record', () => {
 const rows=timeline({status:'failed',steps:[{id:'s',status:'running',type:'code',startedAt:'2026-01-01'}],modelCalls:[{id:'m',status:'running',stage:'approve',startedAt:'2026-01-02'}]});
 assert.ok(rows.every(r=>r.status==='failed')); assert.equal(rows[0].id,'s');
 assert.ok(rows.every(r=>r.error===USER_FACING_ERROR));
});

test('timeline keeps successful output while failed steps expose no diagnostics or traceback result', () => {
 const rows=timeline({status:'failed',steps:[
  {id:'ok',status:'succeeded',result:{content:{value:3}}},
  {id:'bad',status:'failed',error:'PRIVATE_CODE',errorSummary:'provider-secret',result:{content:{traceback:'hidden'}}},
 ],modelCalls:[{id:'m',status:'failed',errorCode:'MODEL_INVALID_JSON',validationErrors:['private'],summary:'raw failure'}]});
 assert.deepEqual(rows[0].result,{content:{value:3}});
 assert.equal(rows[1].error,USER_FACING_ERROR); assert.equal(rows[1].errorSummary,USER_FACING_ERROR);
 assert.equal(rows[1].result,undefined);
 assert.equal(rows[2].summary,USER_FACING_ERROR);
 assert.doesNotMatch(JSON.stringify(rows),/PRIVATE_CODE|provider-secret|traceback|MODEL_INVALID_JSON|raw failure/);
});

test('successful action decisions remain in the model timeline with their trace and retry metadata', () => {
 const call={id:'call',stage:'act',status:'succeeded',traceAvailable:true,traceUrl:'/desktop/api/sub-agent-runs/run/model-calls/call',retryOf:'failed-call',validationErrors:[],finalDecision:'accept'};
 const rows=timeline({id:'run',status:'succeeded',modelCalls:[call]});
 assert.equal(rows.length,1); assert.equal(rows[0].entryType,'model');
 assert.equal(rows[0].traceUrl,call.traceUrl); assert.equal(rows[0].retryOf,'failed-call'); assert.equal(rows[0].finalDecision,'accept');
});

test('trace paths only address this run and call on the local API, with old records unavailable', () => {
 const call={id:'c',traceAvailable:true,traceUrl:'/desktop/api/sub-agent-runs/r/model-calls/c'};
 assert.equal(modelCallTracePath('r',call),call.traceUrl);
 for(const traceUrl of ['https://elsewhere.invalid/trace','//elsewhere.invalid/trace','javascript:alert(1)','/desktop/api/sub-agent-runs/other/model-calls/c',call.traceUrl+'?download=1']) {
  assert.equal(modelCallTracePath('r',{...call,traceUrl}),'');
 }
 assert.equal(modelCallTracePath('r',{...call,traceAvailable:false}),'');
 assert.equal(modelCallTracePath('r',{id:'legacy'}),'');
});

test('trace reading is lazy, authenticates same-origin, and caches the visible record', async () => {
 const calls=[];
 const loader=createModelCallTraceLoader(async (path,options)=>{calls.push({path,options});return {ok:true,json:async()=>({schemaVersion:1,request:{messages:[]},recordInfo:{truncated:true},detail:{head:'first',tail:'last'}})};});
 assert.equal(calls.length,0); assert.equal(loader.state.status,'idle');
 await loader.load('/trace');
 assert.equal(calls.length,1); assert.equal(calls[0].options.credentials,'same-origin');
 assert.ok(calls[0].options.signal instanceof AbortSignal);
 assert.equal(loader.state.status,'loaded'); assert.match(loader.state.text,/"truncated": true/); assert.match(loader.state.text,/"tail": "last"/);
 await loader.load('/trace'); assert.equal(calls.length,1);
});

test('failed trace reads show only the shared message and can be retried explicitly', async () => {
 let count=0;
 const loader=createModelCallTraceLoader(async()=>++count===1?{ok:false,status:503,json:async()=>({error:'记录暂时不可用'})}:{ok:true,json:async()=>({response:{text:'saved'}})});
 await loader.load('/trace'); assert.equal(loader.state.status,'error'); assert.equal(loader.state.error,USER_FACING_ERROR);
 assert.equal(count,1);
 await loader.load('/trace'); assert.equal(count,2); assert.equal(loader.state.status,'loaded'); assert.match(loader.state.text,/saved/);
});

test('a trace response from a replaced or disposed view cannot expose the old record', async () => {
 let finish,signal;
 const loader=createModelCallTraceLoader((_path,options)=>{signal=options.signal;return new Promise(resolve=>{finish=resolve;});});
 const pending=loader.load('/old'); loader.reset(); assert.equal(signal.aborted,true);
 finish({ok:true,json:async()=>({request:{messages:[{content:'old private data'}]}})}); await pending;
 assert.equal(loader.state.status,'idle'); assert.equal(loader.state.text,'');
 const late=loader.load('/other'); loader.dispose();
 finish({ok:true,json:async()=>({response:{text:'late'}})}); await late;
 assert.equal(loader.state.text,'');
 await loader.load('/after-dispose'); assert.equal(loader.state.status,'idle');
});
