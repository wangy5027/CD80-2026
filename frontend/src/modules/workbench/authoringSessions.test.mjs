import test from 'node:test';
import assert from 'node:assert/strict';
import { createAuthoringApi } from './authoringApi.js';
import { createAuthoringStore } from './useAuthoringSessions.js';
import { authoringConfirmation, authoringCases, authoringBudget, firstAssetAppRun, authoringConnectionRequirements } from './authoringModel.js';

const flush = () => new Promise(resolve => setImmediate(resolve));
const session = (revision = 1, values = {}) => ({ id: 'as-1', kind: 'tool', phase: 'preparing', revision, currentCandidateId: 'candidate-1', budgetRoundId: 'round-1', allowedActions: ['messages','draft_only','cancel'], ...values });
const detail = (revision = 1) => ({ session: session(revision, { phase: 'awaiting_review', allowedActions: ['messages','draft_only','save_and_enable'] }), candidate: { id: 'candidate-1', contentDigest: 'content-1', content: { name: '方法' } }, reviewSnapshot: { id: 'review-1', candidateId: 'candidate-1', contentDigest: 'content-1', reviewDigest: 'review-digest' }, evidence: [] });

test('queued chat editing immediately blocks old confirmation until a newer server revision arrives', async () => {
  let current = detail(4); const writes = [];
  const store = createAuthoringStore(async (path, options) => { if (options.method) writes.push(path); return path.includes('?') ? { items: [] } : current; });
  try {
    store.reset('owner'); await store.read('as-1');
    const ticket = store.beginChatEdit('as-1');
    assert.equal(store.s.pendingChat['as-1'].revision, 4);
    assert.equal(store.beginChatEdit('as-1'), null);
    await store.confirm('as-1','save_and_enable'); assert.deepEqual(writes, []);
    store.chatSubmitted(ticket, { id: 'queued-run', status: 'queued' }); await flush();
    assert.ok(store.s.pendingChat['as-1']); assert.equal(store.s.details['as-1'].session.phase, 'awaiting_review');
    store.chatFailed(ticket, { status: 404 }); await flush(); assert.ok(store.s.pendingChat['as-1']);
    current = { session: session(5, { currentCandidateId: null }) }; await store.read('as-1');
    assert.equal(store.s.pendingChat['as-1'], undefined); assert.equal(store.s.details['as-1'].reviewSnapshot, undefined);
  } finally { store.dispose(); }
});

test('a rejected or terminated chat edit unlocks only its own unchanged draft after reading actual state', async () => {
  const store = createAuthoringStore(async path => path.includes('?') ? { items: [] } : detail(4));
  try {
    store.reset('owner'); await store.read('as-1');
    const rejected = store.beginChatEdit('as-1'); store.chatFailed(rejected, { status: 409 });
    assert.equal(store.s.pendingChat['as-1'], undefined);
    const ticket = store.beginChatEdit('as-1'); store.chatSubmitted(ticket, { id: 'failed-run', status: 'queued' }); await flush();
    await store.observeChatRuns([{ id: 'other-run', status: 'failed' }]); assert.ok(store.s.pendingChat['as-1']);
    await store.observeChatRuns([{ id: 'failed-run', status: 'failed' }]);
    assert.equal(store.s.pendingChat['as-1'], undefined); assert.match(store.s.notice, /尚未接收/);
    const oldOwner = store.beginChatEdit('as-1'); store.reset('other-owner'); store.chatSubmitted(oldOwner, { id: 'late-run' });
    assert.deepEqual(store.s.pendingChat, {});
  } finally { store.dispose(); }
});

test('final confirmation binds the reviewed candidate, digest, snapshot and base revision', () => {
  const value = detail(9); value.session.target = { assetId: 'tool', baseVersionId: 'v1', baseRevision: 6 };
  assert.deepEqual(authoringConfirmation(value, 'save_and_enable'), { revision: 9, mode: 'save_and_enable', candidateId: 'candidate-1', contentDigest: 'content-1', reviewSnapshotId: 'review-1', reviewDigest: 'review-digest', baseRevision: 6 });
  value.session.currentCandidateId = 'candidate-2';
  assert.throws(() => authoringConfirmation(value, 'save_and_enable'), /变化/);
  value.session.currentCandidateId = 'candidate-1'; value.reviewSnapshot.contentDigest = 'other';
  assert.throws(() => authoringConfirmation(value, 'save_and_enable'), /变化/);
});
test('draft only can preserve requirements before a candidate or validation exists', () => {
  assert.deepEqual(authoringConfirmation({ session: session(2, { currentCandidateId: null }) }, 'draft_only'), { revision: 2, mode: 'draft_only' });
  assert.throws(() => authoringConfirmation({ session: session() }, 'save_and_enable'), /不能/);
});
test('current results exclude late evidence from a superseded candidate and keep simulation explicit', () => {
  const rows = authoringCases({ session: session(5, { currentCandidateId: 'new' }), evidence: [
    { candidateId: 'old', executionMode: 'real', caseResults: [{ status: 'passed', result: 'old result' }] },
    { candidateId: 'new', executionMode: 'simulated', caseResults: [{ status: 'failed', label: 'new case' }] },
  ] });
  assert.equal(rows.length, 1); assert.equal(rows[0].executionMode, 'simulated'); assert.equal(rows[0].status, 'failed');
});
test('budget uses the server round and keeps consumed and lifetime separate', () => {
  const budget = authoringBudget({ session: session(), budget: { id: 'round-2', number: 2, confirmationId: 'signed', limits: { modelRequests: 32 }, consumed: { modelRequests: 8 }, lifetimeConsumed: { modelRequests: 40 } } });
  assert.equal(budget.roundId, 'round-2'); assert.equal(budget.consumed.modelRequests, 8); assert.equal(budget.cumulative.modelRequests, 40); assert.equal(budget.confirmationId, 'signed');
});
test('uncertain mutation reuses its identity across API instances, while another owner is isolated', async () => {
  const keys = []; let fail = true;
  const transport = async (_path, options) => { keys.push(options.headers['Idempotency-Key']); if (fail) { fail = false; throw Object.assign(new Error('response lost'), { status: 503 }); } return { session: session() }; };
  const body = { kind: 'tool', intent: 'create', instruction: 'uncertain test' };
  await assert.rejects(createAuthoringApi('owner-one', new AbortController().signal, transport).create(body));
  await createAuthoringApi('owner-one', new AbortController().signal, transport).create(body);
  await createAuthoringApi('owner-two', new AbortController().signal, transport).create(body);
  assert.equal(keys[0], keys[1]); assert.notEqual(keys[1], keys[2]);
});
test('unknown creation retry freezes conversation and input even after navigation', async () => {
  const writes = []; let attempt = 0;
  const transport = async (path, options) => {
    if (options.method === 'POST') { writes.push(options.body); if (++attempt === 1) throw Object.assign(new Error('unknown'), { status: 503 }); return { session: session() }; }
    return path.includes('?') ? { items: [] } : detail();
  };
  const store = createAuthoringStore(transport);
  try {
    store.reset('owner', 'conversation-a'); await flush();
    await store.create({ kind: 'tool', intent: 'create', instruction: 'fixed requirement' });
    assert.equal(store.s.pendingRetry, true); store.context('conversation-b'); await store.retry();
    assert.deepEqual(writes[0], writes[1]); assert.equal(writes[1].conversationId, 'conversation-a');
  } finally { store.dispose(); }
});
test('a previous completed APP cannot consume the next creation retry after its response is lost', async () => {
  const writes = [], old = session(2, { id: 'old-app', kind: 'app', intent: 'create', mode: 'prepare',
    phase: 'completed', result: { assetId: 'saved-old', versionId: 'v1' } });
  const next = session(1, { id: 'next-app', kind: 'app', intent: 'create', mode: 'prepare' });
  let lost = false;
  const transport = async (path, options = {}) => {
    if (options.method === 'POST') {
      writes.push({ body: options.body, key: options.headers['Idempotency-Key'] });
      if (options.body.instruction === '新的需求' && !lost) {
        lost = true;
        throw Object.assign(new Error('response lost'), { status: 503 });
      }
      return { session: options.body.instruction === '旧的需求' ? old : next };
    }
    return path.includes('?') ? { items: [] } : { session: old };
  };
  const store = createAuthoringStore(transport);
  try {
    store.reset('consecutive-app-owner');
    await store.create({ kind: 'app', intent: 'create', instruction: '旧的需求', mode: 'prepare' });
    assert.equal(store.s.selectedId, 'old-app');
    await store.create({ kind: 'app', intent: 'create', instruction: '新的需求', mode: 'prepare' });
    assert.equal(store.s.pendingRetry, true);
    assert.equal(store.s.selectedId, '');
    await store.retry();
    assert.equal(store.s.selectedId, 'next-app');
    assert.equal(store.s.pendingRetry, false);
    assert.deepEqual(writes[1], writes[2]);
    assert.equal(writes.length, 3);
  } finally { store.dispose(); }
});
test('owner change discards a late detail response and close is observation only', async () => {
  let deliver; const writes = [];
  const transport = async (path, options) => { if (options.method) writes.push(path); if (path.includes('?')) return { items: [] }; return new Promise(resolve => { deliver = resolve; }); };
  const store = createAuthoringStore(transport);
  try {
    store.reset('old'); await flush(); const reading = store.read('as-1'); await flush(); store.reset('new'); deliver(detail()); await reading;
    assert.equal(store.s.items.length, 0); assert.deepEqual(store.s.details, {}); store.close(); assert.equal(writes.length, 0);
  } finally { store.dispose(); }
});
test('a lower revision cannot replace an updated candidate or revive its old review', async () => {
  let resolveRead;
  const store = createAuthoringStore(async path => path.includes('?') ? { items: [] } : new Promise(resolve => { resolveRead = resolve; }));
  try {
    store.reset('owner'); await flush(); store.ingest({ session: session(7, { currentCandidateId: 'new' }) }); await flush();
    resolveRead(detail(3)); await flush();
    assert.equal(store.s.details['as-1'].session.revision, 7); assert.equal(store.s.details['as-1'].candidate, undefined); assert.equal(store.s.details['as-1'].reviewSnapshot, undefined);
  } finally { store.dispose(); }
});
test('resume keeps current budget while explicit extension includes its server confirmation', async () => {
  const writes = [], current = detail(); current.session = session(11, { phase: 'budget_paused', allowedActions: ['resume','extend_budget'] }); current.budget = { id: 'round-1', confirmationId: 'signed-limit', consumed: { modelRequests: 32, repairRounds: 2 } };
  const store = createAuthoringStore(async (path, options) => { if (options.method) { writes.push(options.body); return { session: current.session }; } return path.includes('?') ? { items: [] } : current; });
  try {
    store.reset('owner'); await store.read('as-1'); await store.resume('as-1'); await store.resume('as-1','extend_budget');
    assert.deepEqual(writes[0], { revision: 11, mode: 'resume', budgetRoundId: 'round-1' });
    assert.deepEqual(writes[1], { revision: 11, mode: 'extend_budget', budgetRoundId: 'round-1', confirmationId: 'signed-limit' });
    assert.equal(store.s.details['as-1'].budget.consumed.modelRequests, 32);
  } finally { store.dispose(); }
});
test('conflict refreshes state without resubmitting mutation and leaves error available', async () => {
  let writes = 0; let current = detail(2);
  const store = createAuthoringStore(async (path, options) => { if (options.method) { writes++; current = detail(4); throw Object.assign(new Error('另一个页面已修改'), { status: 409, code: 'REVISION_CONFLICT' }); } return path.includes('?') ? { items: [] } : current; });
  try {
    store.reset('owner'); await store.read('as-1'); await store.append('as-1','仍保留此输入');
    assert.equal(writes, 1); assert.equal(store.s.details['as-1'].session.revision, 4); assert.equal(store.s.errorCode, 'REVISION_CONFLICT'); assert.equal(store.s.pendingRetry, false);
  } finally { store.dispose(); }
});
test('a refreshed observer recovers a lost confirmation with the original body and idempotency key', async () => {
  const calls = [], owner = 'confirm-reload', original = detail(6); let unknown = true;
  const transport = async (path, options) => {
    if (options.method === 'POST') { calls.push({ body: options.body, key: options.headers['Idempotency-Key'] }); if (unknown) { unknown = false; throw Object.assign(new Error('response lost'), { status: 503 }); } return { ...original, session: session(7, { phase: 'completed', allowedActions: [] }) }; }
    return path.includes('?') ? { items: [] } : { ...original, session: session(7, { phase: 'committing', allowedActions: [] }), commit: { stage: 'failed' } };
  };
  const api = createAuthoringApi(owner, new AbortController().signal, transport);
  await assert.rejects(api.action('as-1','confirm',authoringConfirmation(original,'save_and_enable')));
  const store = createAuthoringStore(transport);
  try {
    store.reset(owner); await store.read('as-1'); assert.equal(store.s.pendingRetry, true); await store.retry();
    assert.deepEqual(calls[0], calls[1]); assert.equal(calls[1].body.revision, 6);
    assert.equal(calls.length, 2);
  } finally { store.dispose(); }
});
test('a late mutation updates its record without reopening it after the user opens another draft', async () => {
  let finishWrite, markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  const store = createAuthoringStore(async (path, options) => {
    if (options.method) return new Promise(resolve => { finishWrite = resolve; markStarted(); });
    if (path.includes('?')) return { items: [] };
    return path.endsWith('/as-2') ? { session: session(1,{id:'as-2'}) } : detail(2);
  });
  try {
    store.reset('navigation-owner'); await store.read('as-1'); const saving = store.append('as-1','update'); await started;
    await store.read('as-2'); finishWrite({ session: session(3) }); await saving;
    assert.equal(store.s.selectedId, 'as-2'); assert.equal(store.s.details['as-1'].session.revision, 3);
    store.close(); assert.equal(store.s.open, false);
  } finally { store.dispose(); }
});
test('file supplements and unknown acknowledgement preserve explicit intent without injecting grants or budget', async () => {
  const writes = [], current = detail(); current.session.question = {id:'question'}; current.budget = {id:'round'};
  const store = createAuthoringStore(async (path, options) => { if (options.method) { writes.push({path,body:options.body}); return current; } return path.includes('?') ? {items:[]} : current; });
  const sourceRefs = [{kind:'upload',reference:{fileId:'file-one'}}];
  try {
    store.reset('supplement-owner'); await store.read('as-1'); await store.append('as-1','使用新文件',sourceRefs); await store.answer('as-1','这是补充资料',sourceRefs); await store.resume('as-1','resume',['unknown-one']);
    assert.deepEqual(writes[0].body,{revision:1,instruction:'使用新文件',sourceRefs});
    assert.deepEqual(writes[1].body,{revision:1,questionId:'question',answer:'这是补充资料',sourceRefs});
    assert.deepEqual(writes[2].body,{revision:1,mode:'resume',budgetRoundId:'round',acknowledgeUnknownRequests:['unknown-one']});
    assert.ok(writes.every(row => !Object.hasOwn(row.body,'limits') && !Object.hasOwn(row.body,'consumed')));
  } finally { store.dispose(); }
});
test('App run cards deduplicate run and assistant references but retain separate executions of the same version', () => {
  const first = { assetAppRunId:'run-one', assetAppId:'app', assetAppVersionId:'version' }, second = { ...first,assetAppRunId:'run-two' };
  const rows = [{role:'user'}, {subAgentRun:first}, {...first,role:'assistant'}, {subAgentRun:second}, {...second,role:'assistant'}];
  assert.deepEqual(rows.map((_,index) => firstAssetAppRun(rows,index)), [null,first,null,second,null]);
});
test('connection needs follow nested snapshots and expose only matching owned options', () => {
  const component = {manifest:{name:'报价服务',tool:{execution:{type:'mcp_http',connectionIdentity:'quotes'}}},dependencies:[{manifest:{tool:{execution:{type:'mcp_http',connectionIdentity:'calendar'}}}}]};
  const result = authoringConnectionRequirements({candidate:{content:{nodes:[{id:'read',component}]}}},[
    {connectionId:'mine',sourceIdentity:'quotes',name:'我的报价',configured:true,enabled:true,credential:'must-not-appear'},
    {connectionId:'other-source',sourceIdentity:'other',name:'不相关',configured:true,enabled:true},
    {connectionId:'disabled',sourceIdentity:'calendar',configured:true,enabled:false},
  ]);
  assert.equal(result.length,2); assert.deepEqual(result[0].nodeIds,['read']); assert.deepEqual(result[0].options,[{connectionId:'mine',name:'我的报价',configured:true,enabled:true}]); assert.equal(result[1].options[0].enabled,false); assert.doesNotMatch(JSON.stringify(result),/must-not-appear|other-source/);
});
test('connection supplement sends explicit complete mappings without changing the candidate identity or creating a session', async () => {
  const writes=[],current=detail();current.session.question={id:'connection-question'};
  const store=createAuthoringStore(async(path,options)=>{ if(options.method){writes.push({path,body:options.body});return current;} return path.includes('?')?{items:[]}:current; });
  try {
    store.reset('connection-owner');await store.read('as-1');await store.answer('as-1','选择本人连接',[],{quotes:'my-connection'});await store.append('as-1','更新连接',[],{});
    assert.deepEqual(writes[0],{path:'/authoring-sessions/as-1/answers',body:{revision:1,questionId:'connection-question',answer:'选择本人连接',runtimeBindings:{quotes:'my-connection'}}});
    assert.deepEqual(writes[1].body.runtimeBindings,{});assert.equal(writes[1].path,'/authoring-sessions/as-1/messages');
  } finally {store.dispose();}
});
