import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, reactive, nextTick } from 'vue';
import { fileFields, initialInputs, parseInputs } from './agentForms.js';
import { useAgentPlatform } from './useAgentPlatform.js';
import { readDraft, writeDraft } from './draftStore.js';

const spec = { id: 'csv', version: '1', permissions: { files: ['fileId'] }, inputSchema: {
  type: 'object', properties: { fileId: { type: 'string', default: 'old-file' }, groupBy: { type: 'string' }, valueColumn: { type: 'string' }, aggregation: { type: 'string', const: 'sum' }, limit: { type: 'integer' }, options: { type: 'object' } }, required: ['fileId', 'groupBy', 'valueColumn'],
} };
test('personal app opens with empty file input and preserves explicitly declared non-file defaults', () => {
  const values = initialInputs(spec, { fileId: 'prior-run-file' }, true);
  assert.equal(values.fileId, ''); assert.equal(values.aggregation, 'sum');
  assert.deepEqual(fileFields(spec), ['fileId']);
});
test('input form extracts only schema parameters and explicit file grants', () => {
  const result = parseInputs(spec, { fileId: 'f2', groupBy: 'category', valueColumn: 'amount', aggregation: 'sum', limit: '4', options: '{"round":2}', hostPath: 'no' });
  assert.deepEqual(result, { input: { fileId: 'f2', groupBy: 'category', valueColumn: 'amount', aggregation: 'sum', limit: 4, options: { round: 2 } }, fileIds: ['f2'] });
  assert.throws(() => parseInputs(spec, { fileId: '' }), /输入文件/);
  assert.throws(() => parseInputs(spec, { fileId: 'f', groupBy: 'g', valueColumn: 'v', limit: '1.2' }), /整数/);
});

const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); };
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
function harness() {
  const renderer = createRenderer({
    createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
    insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode() { return null; }, nextSibling() { return null; }, patchProp() {},
  });
  const s = reactive({ authenticated: true, user: { id: 'user-a' }, currentTaskId: 'task-a', view: 'home', streaming: false });
  let platform;
  const notices = [];
  const app = renderer.createApp({ setup() { platform = useAgentPlatform(s, { toast: (...args) => notices.push(args), refreshState: async () => {}, openTask: async () => {} }); return () => null; } });
  app.mount({});
  return { s, platform, app, notices };
}
test('polling is read-only and stale account results cannot populate the new account', async () => {
  const old = globalThis.fetch; const calls = []; const delayed = [];
  globalThis.fetch = (url, options = {}) => { calls.push(options.method || 'GET'); return new Promise(resolve => delayed.push({ url, resolve })); };
  const h = harness();
  try {
    await flush();
    const oldRequests = delayed.splice(0);
    h.s.user = { id: 'user-b' }; h.s.currentTaskId = null;
    await flush();
    for (const item of oldRequests) item.resolve(response(item.url.includes('/agents') ? { agents: [{ agentRef: 'private-a' }] } : item.url.includes('/agent-runs') ? { runs: [{ id: 'private-run', status: 'succeeded' }] } : {}));
    await flush();
    assert.deepEqual(h.platform.p.agents, []); assert.deepEqual(h.platform.p.runs, []);
    assert.ok(calls.every(method => method === 'GET'));
    for (const item of delayed) item.resolve(response({ agents: [], runs: [] }));
    await flush();
  } finally { h.app.unmount(); globalThis.fetch = old; }
});
test('approval double click starts one POST and sends exact binding and stable idempotency key', async () => {
  const old = globalThis.fetch; const posts = []; let resolvePost;
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') { posts.push({ url, body: JSON.parse(options.body) }); return new Promise(resolve => { resolvePost = resolve; }); }
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try {
    await flush();
    const run = { id: 'run1' }, approval = { id: 'approval1', bindingDigest: 'exact-version' };
    const first = h.platform.approve(run, approval, 'approve');
    const second = h.platform.approve(run, approval, 'approve');
    await flush();
    assert.equal(posts.length, 1); assert.equal(posts[0].body.bindingDigest, 'exact-version'); assert.ok(posts[0].body.idempotencyKey);
    resolvePost(response({ id: 'run1', status: 'queued' })); await Promise.all([first, second]);
    assert.equal(posts.length, 1);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('routine refresh only reads runs, explicit refresh reloads catalog and runtime', async () => {
  const old = globalThis.fetch; const urls = [];
  globalThis.fetch = async url => { urls.push(url); return response({ agents: [], runs: [] }); };
  const h = harness();
  try {
    await flush(); urls.length = 0;
    await h.platform.refresh({ catalog: false });
    assert.deepEqual(urls, ['/desktop/api/agent-runs?taskId=task-a', '/desktop/api/tasks/task-a']);
    urls.length = 0;
    await h.platform.refresh();
    assert.ok(urls.includes('/desktop/api/agent-runtime')); assert.ok(urls.includes('/desktop/api/agents'));
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('unchanged terminal run state still synchronizes a later appended chat message', async () => {
  const old = globalThis.fetch; let finalMessageWritten = false;
  globalThis.fetch = async url => {
    if (url.includes('/tasks/')) return response({ id: 'task-a', messages: finalMessageWritten ? [{ role: 'assistant', content: '最终结果' }] : [] });
    if (url.includes('/agent-runs')) return response({ runs: [{ id: 'run1', status: 'succeeded' }] });
    return response({ agents: [] });
  };
  const h = harness();
  try {
    await flush();
    assert.deepEqual(h.s.currentTask.messages, []);
    finalMessageWritten = true;
    await h.platform.refresh({ catalog: false });
    assert.equal(h.s.currentTask.messages[0].content, '最终结果');
    assert.equal(h.platform.p.runs[0].status, 'succeeded');
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('explicit save ignores rejected approvals and selects latest usable binding', async () => {
  const old = globalThis.fetch; const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') posts.push(JSON.parse(options.body));
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try {
    await flush();
    await h.platform.save({ id: 'save-run', approvals: [
      { id: 'old', kind: 'save', status: 'rejected', bindingDigest: 'old' },
      { id: 'new', kind: 'save', status: 'pending', bindingDigest: 'new-version' },
    ] });
    assert.equal(posts[0].approvalId, 'new'); assert.equal(posts[0].bindingDigest, 'new-version');
    await h.platform.save({ id: 'another-run', approvals: [{ id: 'declined', kind: 'save', status: 'rejected' }] });
    assert.equal(posts[1].approvalId, undefined);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('revision submits edited schema inputs and only currently selected file grants', async () => {
  const old = globalThis.fetch; const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') { posts.push({ url, body: JSON.parse(options.body) }); return response({ id: 'new-run', status: 'generating' }); }
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try {
    await flush();
    h.s.searchMode = 'off';
    const run = { id: 'original-run', taskId: 'task-a', input: { fileId: 'original-file', groupBy: 'category', valueColumn: 'amount' }, allowedFileIds: ['original-file', 'extra-private-file'] };
    const edited = initialInputs(spec, run.input);
    assert.equal(edited.valueColumn, 'amount');
    edited.valueColumn = 'cost'; edited.fileId = 'new-file';
    const values = { goal: '按category汇总cost', ...parseInputs(spec, edited) };
    await h.platform.revise(run, values);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].body.mode, 'create');
    assert.equal(posts[0].body.revisionOf, 'original-run');
    assert.equal(posts[0].body.input.valueColumn, 'cost');
    assert.equal(posts[0].body.goal, '按category汇总cost');
    assert.equal(posts[0].body.searchMode, 'off');
    assert.deepEqual(posts[0].body.fileIds, ['new-file']);
    assert.equal(run.input.valueColumn, 'amount');
    assert.deepEqual(run.allowedFileIds, ['original-file', 'extra-private-file']);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('direct application invocation carries the selected webpage search mode', async () => {
  const old = globalThis.fetch; const posts = [];
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === 'POST') { posts.push(JSON.parse(options.body)); return response({ id: 'new-run', status: 'running' }); }
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try {
    await flush();
    h.s.searchMode = 'off';
    await h.platform.invoke({ agentRef: 'csv@1' }, { input: { fileId: 'f1' }, fileIds: ['f1'] });
    assert.equal(posts.length, 1);
    assert.equal(posts[0].mode, 'invoke');
    assert.equal(posts[0].searchMode, 'off');
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('legacy application and invocation draft survive skill pool navigation without leaking to another account', async () => {
  const old = globalThis.fetch;
  const entry = { agentRef: 'csv@1', spec };
  globalThis.fetch = async () => response({ agents: [entry], runs: [] });
  const h = harness();
  try {
    await flush(); h.s.view = 'extensions'; await flush();
    h.platform.selectEntry(entry);
    const key = h.platform.draftKey('invoke', entry.agentRef);
    writeDraft(key, { fileId: 'draft-file', groupBy: 'category' });
    h.s.view = 'files'; await flush(); h.s.view = 'extensions'; await flush();
    assert.equal(h.platform.p.selected.agentRef, entry.agentRef);
    assert.equal(readDraft(key).fileId, 'draft-file');
    await h.platform.refresh(); assert.equal(readDraft(key).groupBy, 'category');
    h.s.user = { id: 'other-draft-user' }; await flush();
    assert.equal(h.platform.p.selected, null);
    assert.equal(readDraft(h.platform.draftKey('invoke', entry.agentRef)), undefined);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('failed feedback keeps its draft while successful submission after navigation clears original scope only', async () => {
  const old = globalThis.fetch; let fail = true; let finish;
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === 'POST') {
      if (fail) return new Response(JSON.stringify({ error: 'try again' }), { status: 409, headers: { 'Content-Type': 'application/json' } });
      return new Promise(resolve => { finish = resolve; });
    }
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try {
    await flush(); const key = h.platform.draftKey('feedback', 'draft-feedback-run');
    writeDraft(key, 'keep this feedback');
    await h.platform.feedback({ id: 'draft-feedback-run' }, 'keep this feedback');
    assert.equal(readDraft(key), 'keep this feedback');
    fail = false; const pending = h.platform.feedback({ id: 'draft-feedback-run' }, 'keep this feedback');
    await flush(); h.s.user = { id: 'new-draft-user' }; await flush();
    const other = h.platform.draftKey('feedback', 'draft-feedback-run'); writeDraft(other, 'private other draft');
    finish(response({ id: 'draft-feedback-run' })); await pending;
    assert.equal(readDraft(key), undefined); assert.equal(readDraft(other), 'private other draft');
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('application adaptation creates an independent design using only explicitly selected components and fresh inputs', async () => {
  const old = globalThis.fetch; const posts = [];
  globalThis.fetch = async (_url, options = {}) => {
    if (options.method === 'POST') { posts.push({ url: _url, body: JSON.parse(options.body) }); return response({ id: 'adapted-run', status: 'generating' }); }
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try {
    await flush(); h.s.searchMode = 'off';
    await h.platform.adapt({ agentRef: 'original@1', input: { fileId: 'old-file' } }, { goal: '增加比较结果', input: { symbol: '000001' }, fileIds: [], capabilityRefs: [{ assetId: 'asset-a', versionId: 'version-a' }] });
    assert.equal(posts[0].url, '/desktop/api/agent-runs');
    assert.equal(posts[0].body.baseAgentRef, 'original@1'); assert.equal(posts[0].body.mode, 'create');
    assert.equal(posts[0].body.revisionOf, undefined); assert.equal(posts[0].body.searchMode, 'off');
    assert.deepEqual(posts[0].body.fileIds, []); assert.deepEqual(posts[0].body.input, { symbol: '000001' });
    assert.deepEqual(posts[0].body.capabilityRefs, [{ assetId: 'asset-a', versionId: 'version-a' }]);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});

test('copying a personal application saves a definition without initiating a run', async () => {
  const old = globalThis.fetch; const posts = [];
  globalThis.fetch = async (url, options = {}) => {
    if (options.method === 'POST') { posts.push({ url, body: JSON.parse(options.body) }); return response({ agentRef: 'copy@1', spec: { name: '独立副本' } }); }
    return response({ agents: [], runs: [] });
  };
  const h = harness();
  try { await flush(); await h.platform.fork({ agentRef: 'source@1' }); assert.equal(posts.length, 1); assert.equal(posts[0].url, '/desktop/api/agents/fork'); assert.equal(posts[0].body.agentRef, 'source@1'); assert.ok(posts[0].body.idempotencyKey); }
  finally { h.app.unmount(); globalThis.fetch = old; }
});

test('late component discovery cannot expose the previous account catalog after account switching', async () => {
  const old = globalThis.fetch; let finish;
  globalThis.fetch = async url => url.endsWith('/agent-components') ? new Promise(resolve => { finish = resolve; }) : response({ agents: [], runs: [] });
  const h = harness();
  try {
    await flush(); const pending = h.platform.loadComponents(); await flush();
    h.s.user = { id: 'next-user' }; await flush();
    finish(response({ components: [{ assetId: 'private-old-user' }] })); await pending;
    assert.deepEqual(h.platform.p.components, []);
  } finally { h.app.unmount(); globalThis.fetch = old; }
});
