import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick, ref } from 'vue';
import { useAppWorkshop } from './useAppWorkshop.js';
import { accountDraftKey, readDraft, writeDraft } from '../workbench/draftStore.js';
import { safeHandoffInputs, prepareAgentHandoff, prepareStrategyHandoff, restoreStrategyDraft, workshopRequirement } from './handoff.js';
import { loadDraft, saveDraft } from '../strategy-workshop/format.js';

const renderer = createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {}, setElementText() {}, setText() {}, patchProp() {}, parentNode: () => null, nextSibling: () => null });
const response = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const analyzed = { id: 'aw1', revision: 1, requirement: '汇总交易', status: 'analyzed', analysis: { understanding: '整理交易数据', workflow: [], extraRequirements: [] }, recommendation: { name: '表格', kind: 'agent', agentRef: 'csv@1' }, appPreview: null };
const preview = { ...analyzed, revision: 2, status: 'draft_ready', appPreview: { name: '我的汇总', appDigest: 'exact-preview', executionStatus: 'not_run' } };
const settle = async predicate => { for (let i = 0; i < 40; i++) { await new Promise(r => setImmediate(r)); await nextTick(); if (predicate()) return; } throw Error('Did not settle'); };
async function mount(fetcher, account) {
  const old = globalThis.fetch; globalThis.fetch = fetcher; const owner = ref(account); let w;
  const app = renderer.createApp({ setup() { w = useAppWorkshop(owner); return () => null; } }); app.mount({});
  await settle(() => !w.loading.value);
  return { w, owner, close() { app.unmount(); globalThis.fetch = old; } };
}

test('create and save use the exact preview without invoking any run; review is local and saved reopening is read-only', async () => {
  const posts = [];
  const h = await mount(async (url, o = {}) => {
    if (o.method !== 'POST') return response({ session: null });
    posts.push({ url, body: JSON.parse(o.body) });
    return response(url.endsWith('/create') ? preview : url.endsWith('/save') ? { session: { ...preview, status: 'saved', savedAppId: 'app-personal', savedVersionId: 'av-1' }, appId: 'app-personal', versionId: 'av-1' } : analyzed);
  }, 'aw-save');
  try {
    h.w.requirement.value = '汇总交易'; await h.w.analyze(); assert.equal(h.w.step.value, 2);
    await h.w.create(); assert.equal(h.w.step.value, 3);
    h.w.review(); assert.equal(h.w.step.value, 2); await h.w.create(); assert.equal(h.w.step.value, 3); assert.equal(posts.length, 2);
    const saved = await h.w.save(); assert.equal(saved.appId, 'app-personal');
    assert.equal(posts.at(-1).body.appDigest, 'exact-preview'); assert.equal(posts.at(-1).body.revision, 2);
    await h.w.save(); assert.equal(posts.length, 3); assert.ok(posts.every(p => p.url.startsWith('/desktop/api/app-workshop/')));
  } finally { h.close(); }
});

test('new create response is already saved unverified and opens exact APP without a second save request', async () => {
  const posts = [], saved = { ...preview, status: 'saved', savedAppId: 'app-auto', savedVersionId: 'av-auto' };
  const h = await mount(async (url, options = {}) => {
    if (options.method !== 'POST') return response({ session: null });
    posts.push(url);
    return response(url.endsWith('/create') ? saved : analyzed);
  }, 'aw-auto-save');
  try {
    h.w.requirement.value = '汇总交易'; await h.w.analyze(); await h.w.create();
    assert.equal(h.w.step.value, 3);
    assert.equal(h.w.session.value.status, 'saved');
    assert.deepEqual(await h.w.save(), { session: saved, appId: 'app-auto', versionId: 'av-auto' });
    assert.equal(posts.filter(url => url.endsWith('/save')).length, 0);
  } finally { h.close(); }
});

test('double click deduplicates writes and retries reuse an idempotency key', async () => {
  let finish; const posts = [];
  const h = await mount(async (url, o = {}) => o.method === 'POST' ? (posts.push(JSON.parse(o.body)), new Promise(resolve => { finish = resolve; })) : response({ session: null }), 'aw-retry');
  try {
    h.w.requirement.value = '统计'; const first = h.w.analyze(); await h.w.analyze(); assert.equal(posts.length, 1);
    finish(response({ error: '暂时失败' }, 503)); await first; assert.equal(h.w.error.value, '暂时失败');
    const retry = h.w.analyze(); assert.equal(posts[1].idempotencyKey, posts[0].idempotencyKey);
    finish(response(analyzed)); await retry; assert.equal(h.w.step.value, 2);
  } finally { h.close(); }
});

test('late previous-account analysis is discarded, while unsent drafts remain account-scoped', async () => {
  let finish;
  const h = await mount(async (url, o = {}) => o.method === 'POST' ? new Promise(resolve => { finish = resolve; }) : response({ session: null }), 'aw-old');
  try {
    h.w.requirement.value = '旧账号需求'; const pending = h.w.analyze();
    h.owner.value = 'aw-new'; await settle(() => !h.w.loading.value && h.w.requirement.value === '');
    h.w.requirement.value = '新账号草稿'; finish(response(analyzed)); await pending;
    assert.equal(h.w.session.value, null); assert.equal(h.w.requirement.value, '新账号草稿');
    assert.equal(readDraft(accountDraftKey('aw-old', 'app-workshop', 'current')).requirement, '旧账号需求');
  } finally { h.close(); }
});

test('refresh restores the saved session and unfinished supplement using GET only', async () => {
  writeDraft(accountDraftKey('aw-restore', 'app-workshop', 'current'), { sessionId: 'aw1', requirement: '原始问题', instructions: '未发送补充', reviewing: true });
  const calls = [];
  const h = await mount(async (url, o = {}) => { calls.push([url, o.method]); return response(preview); }, 'aw-restore');
  try { assert.equal(h.w.step.value, 2); assert.equal(h.w.instructions.value, '未发送补充'); assert.equal(h.w.session.value.id, 'aw1'); assert.deepEqual(calls, [['/desktop/api/app-workshop/sessions/aw1', undefined]]); }
  finally { h.close(); }
});

test('legacy unfinished supplement does not block either recommendation handoff or pool-based creation', async () => {
  for (const action of ['handoff', 'create']) {
    const owner = 'aw-legacy-' + action, posts = [];
    writeDraft(accountDraftKey(owner, 'app-workshop', 'current'), { sessionId: 'aw1', requirement: '原始问题', instructions: '旧版未提交文字' });
    const h = await mount(async (url, o = {}) => {
      if (o.method !== 'POST') return response(analyzed);
      posts.push({ url, body: JSON.parse(o.body) });
      return response(action === 'create' ? preview : { session: { ...analyzed, status: 'handed_off' }, target: { kind: 'agent', agentRef: 'csv@1' }, requirement: analyzed.requirement });
    }, owner);
    try {
      assert.equal(h.w.instructions.value, '旧版未提交文字');
      await h.w[action]();
      assert.equal(posts.length, 1); assert.ok(posts[0].url.endsWith('/' + action));
      assert.equal(posts[0].body.instructions, undefined, 'Unsubmitted legacy text must not silently become a requirement');
      assert.equal(h.w.error.value, '');
    } finally { h.close(); }
  }
});

test('restored preview without composition remains viewable and saves without regenerating its app', async () => {
  const posts = [];
  const h = await mount(async (url, o = {}) => {
    if (o.method !== 'POST') return response({ session: preview });
    posts.push({ url, body: JSON.parse(o.body) });
    return response({ session: { ...preview, status: 'saved', savedAppId: 'app-restored', savedVersionId: 'av-restored' }, appId: 'app-restored', versionId: 'av-restored' });
  }, 'aw-old-preview');
  try {
    assert.equal(h.w.step.value, 3); assert.equal(h.w.session.value.appPreview.composition, undefined);
    h.w.review(); await h.w.create(); assert.equal(posts.length, 0);
    assert.equal((await h.w.save()).appId, 'app-restored');
    assert.equal(posts.length, 1); assert.ok(posts[0].url.endsWith('/save'));
    assert.equal(posts[0].body.appDigest, preview.appPreview.appDigest);
  } finally { h.close(); }
});

test('completed handoff reopens the recommendation locally without writing a finished session again', async () => {
  const calls = [], completed = { ...analyzed, status: 'handed_off' };
  const h = await mount(async (url, o = {}) => { calls.push(o.method || 'GET'); return response({ session: completed }); }, 'aw-handed-off');
  try {
    const value = await h.w.handoff();
    assert.equal(value.target.agentRef, 'csv@1'); assert.equal(value.requirement, analyzed.requirement);
    assert.deepEqual(calls, ['GET']); assert.equal(h.w.session.value.status, 'handed_off');
    h.w.startNew(); assert.equal(h.w.step.value, 1); assert.equal(h.w.requirement.value, '');
  } finally { h.close(); }
});

test('handoff keeps the original question separate from strictly typed legal inputs and backs up earlier edits', () => {
  const spec = { permissions: { files: ['fileId'] }, inputSchema: { properties: { fileId: { type: 'string' }, limit: { type: 'integer' }, groupBy: { type: 'string' }, mode: { type: 'string', enum: ['sum'] } } } };
  const question = '请帮我分析每个月的交易';
  const result = safeHandoffInputs(spec, { fileId: question, limit: question, groupBy: '月份', mode: 'bad', unknown: question }, [{ id: 'owned-file' }]);
  assert.deepEqual(result, { fileId: '', limit: '', groupBy: '月份', mode: '' });
  const key = accountDraftKey('handoff-owner', 'invoke', 'csv@1'); writeDraft(key, { fileId: 'old', groupBy: '行业' });
  prepareAgentHandoff('handoff-owner', { agentRef: 'csv@1', spec }, question, { limit: 10 }, []);
  assert.equal(readDraft(accountDraftKey('handoff-owner', 'invoke-goal', 'csv@1')), question);
  assert.equal(readDraft(key).limit, 10);
  assert.equal(readDraft(accountDraftKey('handoff-owner', 'invoke-backup', 'csv@1')).inputs.groupBy, '行业');
  assert.equal(readDraft(accountDraftKey('other-owner', 'invoke-goal', 'csv@1')), undefined);
});

test('strategy handoff creates an unsubmitted draft and can restore the exact prior session', () => {
  const old = globalThis.localStorage, data = new Map(); globalThis.localStorage = { getItem: k => data.get(k), setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) };
  try {
    saveDraft(globalThis.localStorage, 'strategy-owner', { idea: '旧策略', sessionId: 'old-session', customizationInstructions: '10日' });
    prepareStrategyHandoff('strategy-owner', '新的策略需求');
    assert.equal(loadDraft(globalThis.localStorage, 'strategy-owner').idea, '新的策略需求');
    assert.equal(loadDraft(globalThis.localStorage, 'strategy-owner').sessionId, null);
    assert.equal(restoreStrategyDraft('another-owner'), false);
    assert.equal(restoreStrategyDraft('strategy-owner'), true);
    assert.equal(loadDraft(globalThis.localStorage, 'strategy-owner').sessionId, 'old-session');
    assert.equal(loadDraft(globalThis.localStorage, 'strategy-owner').customizationInstructions, '10日');
  } finally { globalThis.localStorage = old; }
});

test('handoff and saved-app context preserve every supplement without mutating original demand or structured inputs', () => {
  const session = { requirement: '汇总交易', clarifications: ['按月汇总', '导出表格'], recommendation: { inputs: { groupBy: '月份' } } };
  const before = JSON.stringify(session);
  assert.equal(workshopRequirement(session), '汇总交易\n\n补充要求：\n1. 按月汇总\n2. 导出表格');
  assert.equal(JSON.stringify(session), before);
  assert.equal(workshopRequirement({ requirement: '只有原问题', clarifications: [] }), '只有原问题');
});
