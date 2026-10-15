import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick, ref } from 'vue';
import { useStrategyWorkshop } from './useStrategyWorkshop.js';
import { loadDraft, saveDraft, storageKey } from './format.js';

const renderer = createRenderer({
  createElement: () => ({}), createText: () => ({}), createComment: () => ({}),
  insert() {}, remove() {}, setElementText() {}, setText() {}, patchProp() {},
  parentNode: () => null, nextSibling: () => null,
});
const response = (value, status = 200) => new Response(JSON.stringify(value), { status });
const defaults = { symbol: '510300.SH', startDate: '2023-09-18', endDate: '2026-09-18', initialCash: 100000 };
const candidate = { strategyId: 'sma_trend', name: '双均线', version: '1.0.0', selectionDigest: 'selection-hash' };
const selected = { sessionId: 's1', idea: '趋势', status: 'configuring', matchRevision: 1, selectionRevision: 1, candidate, selectionApproval: { decision: 'accept', matchRevision: 1 } };
const preflight = { preflightId: 'p1', requested: defaults, actual: { startDate: '2024-09-18', endDate: '2026-09-18' }, coverage: { required: true, digest: 'coverage-hash', reasons: ['覆盖不足'] }, gate: { eligible: false }, configDigest: 'config-hash' };

async function mounted(fetcher, owner = 'owner-A', personalAppId = '') {
  const oldFetch = globalThis.fetch;
  const oldStorage = globalThis.localStorage;
  const records = new Map();
  globalThis.localStorage = { getItem: (key) => records.get(key), setItem: (key, value) => records.set(key, value) };
  globalThis.fetch = fetcher;
  const ownerRef = ref(owner);
  let workshop;
  const personalAppRef = ref(personalAppId);
  const app = renderer.createApp({ setup() { workshop = useStrategyWorkshop(ownerRef, personalAppRef); return () => null; } });
  app.mount({});
  await settle(() => !workshop.loading.value);
  return { workshop, ownerRef, personalAppRef, records, cleanup() { app.unmount(); globalThis.fetch = oldFetch; globalThis.localStorage = oldStorage; } };
}

async function settle(predicate) {
  for (let i = 0; i < 30; i++) {
    await new Promise((resolve) => setImmediate(resolve));
    await nextTick();
    if (predicate()) return;
  }
  throw new Error('Condition did not settle');
}

function baseGet(url) {
  if (url.endsWith('/capabilities')) return response({ defaults });
  if (url.endsWith('/symbols')) return response([{ symbol: '510300.SH', name: '沪深300ETF' }]);
  if (url.endsWith('/strategies')) return response({ strategies: [] });
  if (url.endsWith('/sessions/current')) return response({ session: null });
  if (url.endsWith('/sessions')) return response({ sessions: [] });
  if (url.endsWith('/runs')) return response({ runs: [] });
  return null;
}

test('new strategy rules enter the saved unverified state before any validation or backtest', async () => {
  const app = { id: 'strategy-app-new', versionId: 'rules-new', name: '我的规则', verification: { status: 'unverified' } };
  const saved = { ...selected, savedPersonalApp: app };
  const posts = [];
  const context = await mounted(async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(url);
      if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
      if (url.endsWith('/selection')) return response(saved);
      throw new Error('Saving should not invoke validation or a run');
    }
    return baseGet(url) || response({});
  }, 'save-first-owner');
  try {
    const w = context.workshop;
    w.idea.value = '趋势'; await w.match(); await w.select('accept');
    assert.equal(w.step.value, 'saved');
    assert.deepEqual(w.personalApp.value, app);
    assert.deepEqual(await w.savePersonalApp(), app);
    assert.equal(posts.some(url => /\/runs$|\/preflight$|\/customization\/confirm$/.test(url)), false);
  } finally { context.cleanup(); }
});

test('saved strategy with no run opens its exact definition and starts verification or editing only on demand', async () => {
  const savedApp = { id: 'strategy-app-exact', versionId: 'rules-exact', name: '自定义策略', verification: { status: 'unverified' } };
  const revisedApp = { ...savedApp, id: 'strategy-app-revised', versionId: 'rules-revised' };
  const definition = { idea: '按自己的规则', candidate: { ...candidate, strategy: { name: '基础模板' } }, customization: { status: 'rules_ready', sourceHash: 'exact-hash', rulePlan: { name: '自定义策略' } } };
  const posts = [];
  const context = await mounted(async (url, options = {}) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); posts.push({ url, body });
      if (url.endsWith('/selection')) return response({ ...selected, sessionId: 'edit-session', savedPersonalApp: revisedApp });
      return response(body.mode === 'edit' ? { ...selected, sessionId: 'edit-session', status: 'awaiting_selection', editingPersonalAppId: savedApp.id, selectionApproval: null } :
        { ...selected, sessionId: 'verify-session', status: 'customizing', savedPersonalApp: savedApp, customization: definition.customization, selectionApproval: null });
    }
    if (url.endsWith(`/personal-apps/${savedApp.id}`)) return response({ app: savedApp, run: null, definition });
    if (url.endsWith('/sessions/verify-session')) return response({ ...selected, sessionId: 'verify-session', status: 'customizing', savedPersonalApp: savedApp, customization: definition.customization, selectionApproval: null });
    return baseGet(url) || response({});
  }, 'saved-detail-owner', savedApp.id);
  try {
    const w = context.workshop;
    assert.equal(w.run.value, null);
    assert.equal(w.step.value, 'saved');
    assert.deepEqual(w.openedPersonalDefinition.value, definition);
    assert.equal(posts.length, 0);
    await w.reconfigurePersonalApp('verify');
    assert.equal(posts[0].body.mode, 'verify');
    assert.equal(w.step.value, 'customization');
    await w.reload();
    assert.equal(w.step.value, 'customization', 'Saved-version verification session restores without a legacy sourcePersonalAppId');
    assert.equal(w.syncError.value, '');
    await w.editPersonalApp();
    assert.equal(posts[1].body.mode, 'edit');
    assert.equal(w.step.value, 'recommendation');
    await w.select('accept');
    assert.equal(w.step.value, 'saved');
    assert.equal(w.personalApp.value.id, revisedApp.id, 'Editing creates a new saved version instead of replacing the opened version');
    assert.equal(w.run.value, null);
  } finally { context.cleanup(); }
});

test('full flow never runs on matching/selection/preflight and requires both explicit diagnostic approvals', async () => {
  const posts = [];
  let createdRun = false;
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    const body = JSON.parse(options.body);
    posts.push({ url, body });
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    if (url.endsWith('/selection')) return response(selected);
    if (url.endsWith('/preflight')) return response(preflight);
    if (url.endsWith('/runs')) { createdRun = true; return response({ runId: 'r1', sessionId: 's1', status: 'queued', mode: 'diagnostic' }); }
    return response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '趋势';
    await w.match();
    assert.equal(w.step.value, 'recommendation');
    assert.equal(createdRun, false);
    await w.select('accept');
    assert.equal(w.step.value, 'config');
    const confirmedPosts = posts.length;
    w.step.value = 'recommendation';
    await w.select('accept');
    assert.equal(w.step.value, 'config');
    assert.equal(posts.length, confirmedPosts, 'Viewing an approved strategy must not resubmit an expired selection');
    await w.checkConfig();
    assert.equal(w.canRun.value, false);
    await w.startRun();
    assert.equal(createdRun, false);
    w.coverageAccepted.value = true;
    assert.equal(w.canRun.value, false);
    w.diagnosticAccepted.value = true;
    assert.equal(w.canRun.value, true);
    await w.startRun();
    assert.equal(createdRun, true);
    const last = posts.at(-1).body;
    assert.equal(last.mode, 'diagnostic');
    assert.equal(last.diagnosticAcknowledged, true);
    assert.deepEqual(last.coverageDecision, { decision: 'accept_actual_interval', coverageDigest: 'coverage-hash' });
    assert.equal(last.configDigest, 'config-hash');
    assert.equal(w.step.value, 'result');
  } finally { context.cleanup(); }
});

test('late matching response from previous account cannot mutate the current owner state', async () => {
  let completeMatch;
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') return new Promise((resolve) => { completeMatch = () => resolve(response(selected)); });
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '旧账号想法';
    const pending = w.match();
    await settle(() => Boolean(completeMatch));
    context.ownerRef.value = 'owner-B';
    await settle(() => !w.loading.value && w.idea.value === '');
    completeMatch();
    await pending;
    assert.equal(w.session.value, null);
    assert.equal(w.idea.value, '');
    assert.equal(w.step.value, 'idea');
    assert.equal(w.busy.value, '');
  } finally { context.cleanup(); }
});

const customRules = { id: 'c1', status: 'rules_ready', summary: '10日均线高于30日买入，否则退出', rulePlan: { name: '自定义均线' }, rulesDigest: 'rules-hash', sourceCode: 'new code', sourceHash: 'source-hash', questions: [], assumptions: [], dataGaps: [] };
const customCandidate = { ...candidate, strategyId: 'custom_123', name: '自定义均线', selectionDigest: 'custom-selection-hash', strategyKind: 'custom' };
const customized = { ...selected, status: 'customizing', candidate, baseCandidate: candidate, selectionRevision: null, selectionApproval: null, customization: customRules };
const validated = { ...customized, status: 'awaiting_custom_selection', candidate: customCandidate, customization: { ...customRules, status: 'validated', validation: { passed: true } } };

test('customization requires explicit rule validation and separate strategy selection before opening configuration', async () => {
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    posts.push({ url, body: JSON.parse(options.body) });
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    if (url.endsWith('/customization')) return response(customized);
    if (url.endsWith('/customization/confirm')) return response(validated);
    if (url.endsWith('/selection')) return response({ ...validated, status: 'configuring', selectionApproval: { decision: 'accept' }, selectionRevision: 2 });
    return response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '10日均线高于30日买入';
    await w.match();
    await w.considerCustomization();
    assert.equal(w.step.value, 'customization');
    assert.equal(w.customization.value.status, 'rules_ready');
    assert.equal(w.session.value.selectionApproval, null);
    assert.equal(w.run.value, null);
    assert.equal(w.preflight.value, null);
    assert.ok(posts.at(-1).url.endsWith('/customization'));
    assert.equal(posts.at(-1).body.selectionDigest, 'selection-hash');
    assert.equal(posts.some((post) => post.url.endsWith('/selection')), false);
    await w.confirmCustomization();
    assert.equal(w.step.value, 'customization');
    assert.equal(w.customization.value.status, 'validated');
    assert.equal(w.candidate.value.selectionDigest, 'custom-selection-hash');
    assert.equal(posts.at(-1).body.customizationId, 'c1');
    assert.equal(posts.at(-1).body.rulesDigest, 'rules-hash');
    assert.equal(w.session.value.selectionApproval, null);
    await w.select('accept');
    assert.equal(w.step.value, 'config');
    assert.equal(posts.at(-1).body.selectionDigest, 'custom-selection-hash');
    assert.equal(posts.some((post) => post.url.endsWith('/runs')), false);
  } finally { context.cleanup(); }
});

test('clarifications create a fresh rule version, reset approvals and can return to the original strategy', async () => {
  const posts = [];
  const awaiting = { ...customized, customization: { ...customRules, id: 'c0', status: 'needs_clarification', rulePlan: null, rulesDigest: null, questions: ['退出条件是什么？'] } };
  let rewriteCount = 0;
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    posts.push({ url, body: JSON.parse(options.body) });
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    if (url.endsWith('/customization')) return response(++rewriteCount === 1 ? awaiting : customized);
    if (url.endsWith('/customization/reset')) return response({ ...selected, status: 'awaiting_selection', customization: null, selectionApproval: null });
    return response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '10日均线高于30日买入';
    await w.match();
    await w.considerCustomization();
    const before = posts.length;
    await w.confirmCustomization();
    assert.equal(posts.length, before, 'Incomplete rules cannot be confirmed');
    w.customizationInstructions.value = '短均线低于或等于长均线时退出';
    await w.rewriteCustomization();
    assert.equal(posts.at(-1).body.instructions, '短均线低于或等于长均线时退出');
    assert.equal(w.customization.value.id, 'c1');
    assert.equal(w.customizationInstructions.value, '');
    assert.equal(w.step.value, 'customization');
    assert.equal(w.session.value.selectionApproval, null);
    await w.returnToBaseStrategy();
    assert.ok(posts.at(-1).url.endsWith('/customization/reset'));
    assert.equal(w.step.value, 'recommendation');
    assert.equal(w.customization.value, null);
    assert.equal(w.candidate.value.strategyId, 'sma_trend');
    assert.equal(w.run.value, null);
  } finally { context.cleanup(); }
});

test('one alignment reply produces reviewable rules before explicit code generation', async () => {
  const posts = [];
  const awaiting = { ...customized, customization: { ...customRules, id: 'c0', status: 'needs_clarification', rulePlan: null, rulesDigest: null,
    questions: ['退出条件是什么？'], assumptions: ['若无其他说明，以次日开盘成交。'], alignmentCompleted: false } };
  const ready = { ...customized, customization: { ...customRules, alignmentCompleted: true } };
  let rewriteCount = 0;
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    posts.push({ url, body: JSON.parse(options.body) });
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    if (url.endsWith('/customization')) return response(++rewriteCount === 1 ? awaiting : ready);
    if (url.endsWith('/customization/confirm')) return response(validated);
    return response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '10日均线高于30日买入';
    await w.match();
    await w.considerCustomization();
    const beforeAnswer = posts.length;
    await w.rewriteCustomization();
    assert.equal(posts.length, beforeAnswer, 'An empty answer must not consume the sole alignment round');
    w.customizationInstructions.value = '短均线低于或等于长均线时退出；同意次日开盘成交';
    await w.rewriteCustomization();
    assert.equal(rewriteCount, 2, 'Exactly one follow-up answer creates a new rule version');
    assert.equal(posts.at(-1).body.instructions, '短均线低于或等于长均线时退出；同意次日开盘成交');
    assert.equal(w.customization.value.status, 'rules_ready');
    assert.equal(w.customization.value.alignmentCompleted, true);
    assert.equal(w.session.value.selectionApproval, null);
    assert.equal(posts.some(({ url }) => url.endsWith('/customization/confirm')), false);
    await w.confirmCustomization();
    assert.equal(w.customization.value.status, 'validated');
    assert.equal(posts.filter(({ url }) => url.endsWith('/customization/confirm')).length, 1);
  } finally { context.cleanup(); }
});

test('incomplete final alignment ends this attempt without another question or code generation', async () => {
  const posts = [];
  const awaiting = { ...customized, customization: { ...customRules, id: 'c0', status: 'needs_clarification', rulePlan: null, rulesDigest: null,
    questions: ['请明确退出条件'], alignmentCompleted: false } };
  const incomplete = { ...customized, customization: { id: 'c1', status: 'alignment_incomplete', summary: '退出条件仍不明确',
    questions: [], assumptions: [], alignmentIssues: ['无法确定卖出时机'], alignmentCompleted: true, rulePlan: null, rulesDigest: null } };
  let rewriteCount = 0;
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    posts.push({ url, body: JSON.parse(options.body) });
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    if (url.endsWith('/customization')) return response(++rewriteCount === 1 ? awaiting : incomplete);
    if (url.endsWith('/customization/reset')) return response({ ...selected, status: 'awaiting_selection', customization: null, selectionApproval: null });
    return response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '不明确的交易想法';
    await w.match();
    await w.considerCustomization();
    w.customizationInstructions.value = '确认想法，但仍无法说明何时卖出';
    await w.rewriteCustomization();
    assert.equal(w.customization.value.status, 'alignment_incomplete');
    assert.deepEqual(w.customization.value.alignmentIssues, ['无法确定卖出时机']);
    const before = posts.length;
    w.customizationInstructions.value = '再问我一次';
    await w.rewriteCustomization();
    await w.confirmCustomization();
    assert.equal(posts.length, before, 'A terminal attempt cannot ask again or compile unapproved rules');
    await w.returnToBaseStrategy();
    assert.equal(w.step.value, 'recommendation');
    assert.equal(w.customization.value, null);
  } finally { context.cleanup(); }
});

test('unsupported external data and failed code validation cannot be confirmed or automatically backtested', async () => {
  for (const status of ['unsupported_data', 'validation_failed']) {
    const posts = [];
    const context = await mounted(async (url, options) => {
      if (options.method !== 'POST') return baseGet(url) || response({});
      posts.push(url);
      if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
      return response({ ...customized, customization: { ...customRules, status, dataGaps: ['微博热度'] } });
    });
    try {
      const w = context.workshop;
      w.idea.value = '根据微博热度交易';
      await w.match();
      await w.considerCustomization();
      const before = posts.length;
      await w.confirmCustomization();
      await w.startRun();
      assert.equal(posts.length, before);
      assert.equal(w.canRun.value, false);
      assert.equal(w.step.value, 'customization');
      assert.equal(w.run.value, null);
    } finally { context.cleanup(); }
  }
});

test('owner changes discard customization state and stale rewrite responses', async () => {
  let finishRewrite;
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    return new Promise((resolve) => { finishRewrite = () => resolve(response(customized)); });
  });
  try {
    const w = context.workshop;
    w.idea.value = '我的均线';
    await w.match();
    const pending = w.considerCustomization();
    await settle(() => Boolean(finishRewrite));
    context.ownerRef.value = 'owner-B';
    await settle(() => !w.loading.value && w.idea.value === '');
    finishRewrite();
    await pending;
    assert.equal(w.customization.value, null);
    assert.equal(w.customizationInstructions.value, '');
    assert.equal(w.session.value, null);
    assert.equal(w.step.value, 'idea');
  } finally { context.cleanup(); }
});

test('restoring a validated custom version reads state without repeating generation, validation or selection', async () => {
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') { posts.push(url); return response({}); }
    if (url.endsWith('/sessions/current')) return response({ session: validated });
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    assert.equal(w.step.value, 'customization');
    assert.equal(w.customization.value.status, 'validated');
    assert.equal(w.candidate.value.selectionDigest, 'custom-selection-hash');
    assert.equal(w.session.value.selectionApproval, null);
    assert.equal(w.run.value, null);
    assert.deepEqual(posts, []);
  } finally { context.cleanup(); }
});

test('uncertain rewrite retry reuses its key and keeps configuration blocked until rules are confirmed', async () => {
  const keys = [];
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    if (url.endsWith('/sessions')) return response({ ...selected, status: 'awaiting_selection', selectionApproval: null });
    const body = JSON.parse(options.body);
    keys.push(body.idempotencyKey);
    if (keys.length === 1) throw new TypeError('connection lost');
    return response(customized);
  });
  try {
    const w = context.workshop;
    w.idea.value = '改成10/30日';
    await w.match();
    await w.considerCustomization();
    assert.match(w.error.value, /无法连接/);
    assert.equal(w.step.value, 'customization');
    assert.equal(w.customization.value, null);
    await w.rewriteCustomization();
    assert.equal(keys.length, 2);
    assert.equal(keys[0], keys[1]);
    assert.equal(w.customization.value.status, 'rules_ready');
    assert.equal(w.preflight.value, null);
    assert.equal(w.run.value, null);
    assert.equal(w.canRun.value, false);
  } finally { context.cleanup(); }
});

test('uncertain network retry reuses the same idempotency key and changed input needs fresh approval', async () => {
  const keys = [];
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    const body = JSON.parse(options.body);
    if (url.endsWith('/sessions')) {
      keys.push(body.idempotencyKey);
      if (keys.length === 1) throw new TypeError('network lost after submission');
      return response(selected);
    }
    if (url.endsWith('/preflight')) return response(preflight);
    return response(selected);
  });
  try {
    const w = context.workshop;
    w.idea.value = '趋势';
    await w.match();
    assert.match(w.error.value, /无法连接/);
    await w.match();
    assert.equal(keys.length, 2);
    assert.equal(keys[0], keys[1]);
    await w.checkConfig();
    w.coverageAccepted.value = true;
    w.diagnosticAccepted.value = true;
    assert.equal(w.canRun.value, true);
    w.form.initialCash = 200000;
    assert.equal(w.preflight.value, null);
    assert.equal(w.canRun.value, false);
    assert.equal(w.coverageAccepted.value, false);
    assert.equal(w.diagnosticAccepted.value, false);
  } finally { context.cleanup(); }
});

test('session histories include unfinished dialogue and linked runs use frozen GET-only transcripts without replacing the active draft', async () => {
  const requests = [];
  const fullConversation = { sessionId: 'old-session', complete: true, entries: [{ id: 'e1', role: 'user', stage: 'matching', text: '原始想法' }, { id: 'e2', role: 'user', stage: 'customization', text: '最新补充' }], runs: [{ id: 'old-run', status: 'completed' }] };
  const frozenConversation = { sessionId: 'old-session', runId: 'old-run', complete: true, entries: fullConversation.entries.slice(0, 1), runs: fullConversation.runs };
  const context = await mounted(async (url, options) => {
    requests.push({ url, method: options.method });
    if (url.endsWith('/sessions/current')) return response({ session: customized });
    if (url.endsWith('/sessions')) return response({ sessions: [{ sessionId: 'old-session', idea: '原始想法', runCount: 1 }, { sessionId: 'unfinished', idea: '未完成的想法', runCount: 0 }] });
    if (url.endsWith('/sessions/old-session/conversation')) return response(fullConversation);
    if (url.endsWith('/runs/old-run/conversation')) return response(frozenConversation);
    if (url.endsWith('/runs/old-run')) return response({ runId: 'old-run', sessionId: 'old-session', status: 'completed', candidate });
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '尚未提交的新想法';
    w.customizationInstructions.value = '尚未提交的退出条件';
    w.form.initialCash = 123000;
    await nextTick();
    assert.equal(w.history.value[1].runCount, 0);
    await w.showHistory();
    await w.openHistory(w.history.value[0]);
    assert.equal(w.conversationOpen.value, true);
    assert.equal(w.conversation.value.entries.length, 2);
    await w.openHistoricalRun({ id: 'old-run' });
    assert.equal(w.step.value, 'result');
    assert.equal(w.conversation.value.runId, 'old-run');
    assert.equal(w.conversation.value.entries.length, 1, 'The run must use its frozen transcript, not later session messages');
    await w.viewRunConversation();
    assert.equal(w.conversationOpen.value, true);
    await w.openHistory({ sessionId: 'old-session' });
    assert.equal(w.conversation.value.entries.length, 2);
    await w.showHistory();
    w.leaveHistory();
    assert.equal(w.step.value, 'customization');
    assert.equal(w.session.value.sessionId, 's1');
    assert.equal(w.idea.value, '尚未提交的新想法');
    assert.equal(w.customizationInstructions.value, '尚未提交的退出条件');
    assert.equal(w.form.initialCash, 123000);
    assert.equal(w.run.value, null);
    assert.equal(w.historyReadOnly.value, false);
    assert.equal(w.conversation.value, null);
    assert.equal(requests.some((item) => item.method === 'POST'), false);
  } finally { context.cleanup(); }
});

test('history requests from a previous account cannot expose its conversation and legacy notice remains explicit', async () => {
  let finishHistory;
  const context = await mounted(async (url) => {
    if (url.endsWith('/sessions/old-session/conversation')) return new Promise((resolve) => { finishHistory = () => resolve(response({ sessionId: 'old-session', complete: true, entries: [{ text: '另一个账号的策略' }], runs: [] })); });
    if (url.endsWith('/sessions/legacy/conversation')) return response({ sessionId: 'legacy', complete: false, notice: '旧记录未保存完整对话。', entries: [], runs: [] });
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    const pending = w.openHistory({ sessionId: 'old-session' });
    await settle(() => Boolean(finishHistory));
    context.ownerRef.value = 'owner-B';
    await settle(() => !w.loading.value && w.busy.value === '');
    finishHistory();
    await pending;
    assert.equal(w.conversation.value, null);
    assert.equal(w.conversationOpen.value, false);
    await w.openHistory({ sessionId: 'legacy' });
    assert.equal(w.conversation.value.complete, false);
    assert.match(w.conversation.value.notice, /未保存完整对话/);
  } finally { context.cleanup(); }
});

test('viewing an ongoing historical run cannot cancel it and returning restores active run observation', async () => {
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') posts.push(url);
    if (url.endsWith('/sessions/current')) return response({ session: { ...selected, runId: 'active-run' } });
    if (url.endsWith('/runs/active-run')) return response({ runId: 'active-run', status: 'running' });
    if (url.endsWith('/runs/old-run/conversation')) return response({ sessionId: 'old', runId: 'old-run', entries: [], runs: [] });
    if (url.endsWith('/runs/old-run')) return response({ runId: 'old-run', status: 'running' });
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    await w.openHistoricalRun({ id: 'old-run' });
    await w.cancelRun();
    assert.deepEqual(posts, []);
    assert.equal(w.run.value.runId, 'old-run');
    w.leaveHistory();
    assert.equal(w.run.value.runId, 'active-run');
    assert.equal(w.step.value, 'result');
    assert.equal(w.historyReadOnly.value, false);
  } finally { context.cleanup(); }
});


test('language mode defaults to beginner, persists per account, and toggles without requests or state changes', async () => {
  const requests = [];
  const context = await mounted(async (url, options) => {
    requests.push({ url, method: options.method });
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    assert.equal(w.languageStyle.value, 'beginner');
    w.idea.value = '尚未发送的想法';
    w.session.value = structuredClone(validated);
    w.preflight.value = structuredClone(preflight);
    w.coverageAccepted.value = true;
    w.diagnosticAccepted.value = true;
    w.step.value = 'config';
    const frozen = JSON.stringify({ session: w.session.value, preflight: w.preflight.value, form: w.form });
    const count = requests.length;
    w.setLanguageStyle('expert');
    await nextTick();
    assert.equal(w.languageStyle.value, 'expert');
    assert.equal(requests.length, count, 'A presentation-only toggle must not issue a request');
    assert.equal(w.step.value, 'config');
    assert.equal(w.coverageAccepted.value, true);
    assert.equal(w.diagnosticAccepted.value, true);
    assert.equal(JSON.stringify({ session: w.session.value, preflight: w.preflight.value, form: w.form }), frozen);
    assert.equal(w.idea.value, '尚未发送的想法');
    context.ownerRef.value = 'owner-B';
    await settle(() => !w.loading.value && w.idea.value === '');
    assert.equal(w.languageStyle.value, 'beginner', 'Another account does not inherit the preference');
    context.ownerRef.value = 'owner-A';
    await settle(() => !w.loading.value && w.languageStyle.value === 'expert');
    assert.equal(w.languageStyle.value, 'expert');
    await w.reload();
    assert.equal(w.languageStyle.value, 'expert', 'Refresh restores the preference');
  } finally { context.cleanup(); }
});

test('only new analysis actions send language style and existing generated responses remain unchanged', async () => {
  const posts = [];
  const original = { ...selected, languageStyle: 'beginner', status: 'awaiting_selection', selectionApproval: null,
    candidate: { ...candidate, languageStyle: 'beginner', explanation: { understoodIdea: '这段原文不应被改写' } } };
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    const body = JSON.parse(options.body);
    posts.push({ url, body });
    if (url.endsWith('/customization/confirm')) return response(validated);
    if (url.endsWith('/customization')) return response({ ...customized, customization: { ...customRules, languageStyle: body.languageStyle } });
    if (url.endsWith('/selection')) return response(selected);
    return response(original);
  });
  try {
    const w = context.workshop;
    w.idea.value = '趋势';
    await w.match();
    assert.equal(posts.at(-1).body.languageStyle, 'beginner');
    w.setLanguageStyle('expert');
    const explanation = JSON.stringify(w.candidate.value.explanation);
    await nextTick();
    assert.equal(w.candidate.value.languageStyle, 'beginner');
    assert.equal(JSON.stringify(w.candidate.value.explanation), explanation);
    await w.match();
    assert.equal(posts.at(-1).body.languageStyle, 'expert');
    await w.considerCustomization();
    assert.equal(posts.at(-1).body.languageStyle, 'expert');
    assert.equal(w.customization.value.languageStyle, 'expert');
    w.setLanguageStyle('beginner');
    await w.confirmCustomization();
    assert.equal(Object.hasOwn(posts.at(-1).body, 'languageStyle'), false, 'Confirming code must not regenerate it in a different style');
    await w.select('accept');
    assert.equal(Object.hasOwn(posts.at(-1).body, 'languageStyle'), false);
  } finally { context.cleanup(); }
});

test('style changes during history viewing preserve archived conversation and code snapshots', async () => {
  let postCount = 0;
  const transcript = { sessionId: 's-old', entries: [{ id: 'e1', role: 'assistant', languageStyle: 'expert', text: 'SMA 金叉入场', details: { candidate: { sourceCode: 'immutable code' } } }] };
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') postCount += 1;
    if (url.endsWith('/conversation')) return response(transcript);
    return baseGet(url) || response({});
  });
  try {
    const w = context.workshop;
    w.idea.value = '保留当前草稿';
    await w.openHistory({ sessionId: 's-old' });
    const before = JSON.stringify(w.conversation.value);
    w.setLanguageStyle('expert');
    w.setLanguageStyle('beginner');
    await nextTick();
    assert.equal(JSON.stringify(w.conversation.value), before);
    assert.equal(w.historyReadOnly.value, true);
    assert.equal(w.conversationOpen.value, true);
    assert.equal(postCount, 0);
    w.leaveHistory();
    assert.equal(w.idea.value, '保留当前草稿');
  } finally { context.cleanup(); }
});


test('saving a completed strategy app prevents duplicate writes and reuses an uncertain save key', async () => {
  let finish;
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    posts.push({ url, body: JSON.parse(options.body) });
    return new Promise(resolve => { finish = resolve; });
  });
  try {
    const w = context.workshop;
    w.run.value = { runId: 'saved-custom-run', status: 'succeeded', result: { custom_strategy: {} } };
    const first = w.savePersonalApp();
    await w.savePersonalApp();
    assert.equal(posts.length, 1);
    assert.equal(w.busy.value, 'save-personal-app');
    finish(response({ error: '保存暂时失败，请重试' }, 503)); await first;
    assert.equal(w.personalApp.value, null);
    assert.match(w.error.value, /重试/);
    const retry = w.savePersonalApp();
    assert.equal(posts[1].body.idempotencyKey, posts[0].body.idempotencyKey);
    const app = { id: 'personal-custom', runId: 'saved-custom-run', name: '我的均线' };
    finish(response({ app }));
    assert.deepEqual(await retry, app);
    assert.deepEqual(w.personalApp.value, app);
    assert.deepEqual(await w.savePersonalApp(), app);
    assert.equal(posts.length, 2);
    assert.ok(posts.every(post => post.url.endsWith('/runs/saved-custom-run/personal-app')));
  } finally { context.cleanup(); }
});

test('only successful strategy results can be saved and prior-owner save completion cannot change the current account', async () => {
  let finish;
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method !== 'POST') return baseGet(url) || response({});
    posts.push(url); return new Promise(resolve => { finish = resolve; });
  });
  try {
    const w = context.workshop;
    for (const status of ['queued', 'running', 'failed', 'cancelled', 'interrupted', 'succeeded']) {
      w.run.value = { runId: 'incomplete', status };
      await w.savePersonalApp();
      assert.equal(posts.length, 0);
    }
    w.run.value = { runId: 'old-account-run', status: 'completed', result: { metrics: {} } };
    const pending = w.savePersonalApp();
    context.ownerRef.value = 'new-app-owner';
    await settle(() => !w.loading.value && w.run.value === null);
    finish(response({ app: { id: 'old-account-app', runId: 'old-account-run' } }));
    assert.equal(await pending, undefined);
    assert.equal(w.personalApp.value, null);
    assert.equal(w.busy.value, '');
  } finally { context.cleanup(); }
});

test('opening a personal strategy loads its frozen custom run and code instead of the latest session', async () => {
  const calls = [];
  const app = { id: 'exact-app', runId: 'original-run', name: '我的10/30均线' };
  const savedRun = { runId: 'original-run', status: 'succeeded', candidate: { strategyKind: 'custom', name: '10/30均线', ruleSummary: { entry: 'MA10 > MA30' } }, result: { custom_strategy: { rules_digest: 'original-rules' } } };
  const context = await mounted(async (url, options) => {
    calls.push({ url, method: options.method });
    if (url.endsWith('/personal-apps/exact-app')) return response({ app, run: savedRun });
    if (url.endsWith('/runs/original-run/code')) return response({ code: 'original custom code MA10 > MA30' });
    if (url.endsWith('/runs/original-run/conversation')) return response({ events: [] });
    if (url.endsWith('/sessions/current')) return response({ session: { ...selected, runId: 'different-latest-run' } });
    return baseGet(url) || response({});
  }, 'personal-app-owner', app.id);
  try {
    const w = context.workshop;
    assert.equal(w.step.value, 'result');
    assert.equal(w.historyReadOnly.value, true);
    assert.equal(w.run.value.runId, savedRun.runId);
    assert.equal(w.candidate.value.ruleSummary.entry, 'MA10 > MA30');
    assert.equal(w.personalApp.value.id, app.id);
    await w.getHistoricalCode();
    assert.equal(w.historicalCode.value, 'original custom code MA10 > MA30');
    await w.viewRunConversation(); w.leaveHistory();
    assert.equal(w.historyReadOnly.value, true);
    assert.equal(w.run.value.runId, savedRun.runId);
    await w.reload();
    assert.equal(w.run.value.runId, savedRun.runId);
    assert.equal(calls.some(call => call.url.endsWith('/sessions/current')), false);
    assert.equal(calls.some(call => call.method === 'POST'), false);
  } finally { context.cleanup(); }
});

test('a missing personal app shows the error without falling back to a different latest strategy', async () => {
  const context = await mounted(async url => url.includes('/personal-apps/') ? response({ error: '找不到这个个人应用' }, 404) : baseGet(url) || response({}), 'missing-app-owner', 'missing-app');
  try {
    assert.equal(context.workshop.run.value, null);
    assert.match(context.workshop.error.value, /找不到/);
  } finally { context.cleanup(); }
});

const rerunApp = { id: 'rerunnable-app', name: '我的10/30均线', runId: 'frozen-app-run' };
const frozenAppRun = { runId: rerunApp.runId, sessionId: 'original-session', status: 'succeeded', requested: defaults,
  candidate: { ...customCandidate, ruleSummary: { entry: 'MA10 > MA30' } }, result: { custom_strategy: { rules_digest: 'frozen-rules', source_hash: 'frozen-code' } } };
const appSession = { ...selected, sessionId: 'app-session', sourcePersonalAppId: rerunApp.id, sourceRunId: frozenAppRun.runId,
  candidate: frozenAppRun.candidate, customization: { ...customRules, status: 'validated', sourceHash: 'frozen-code', rulesDigest: 'frozen-rules' }, rerunDefaults: defaults };

test('personal app reuses its frozen strategy, requires fresh approvals, and supports another run with the latest parameters', async () => {
  const posts = [];
  let sessionCount = 0;
  const changed = { symbol: '000001.SZ', startDate: '2024-01-01', endDate: '2026-09-01', initialCash: 55000 };
  const nextRun = { runId: 'new-app-run', sessionId: appSession.sessionId, sourcePersonalAppId: rerunApp.id, sourceRunId: frozenAppRun.runId,
    candidate: frozenAppRun.candidate, status: 'queued', requested: changed, personalApp: null };
  const original = JSON.stringify(frozenAppRun);
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); posts.push({ url, body });
      if (url.endsWith(`/personal-apps/${rerunApp.id}/sessions`)) return response({ ...appSession, sessionId: ++sessionCount === 1 ? appSession.sessionId : 'app-session-two' }, 201);
      if (url.endsWith('/preflight')) return response({ ...preflight, requested: changed });
      if (url.endsWith('/runs')) return response(nextRun, 201);
    }
    if (url.endsWith(`/personal-apps/${rerunApp.id}`)) return response({ app: rerunApp, run: frozenAppRun });
    if (url.endsWith('/runs/new-app-run')) return response({ ...nextRun, status: 'succeeded', result: frozenAppRun.result });
    return baseGet(url) || response({});
  }, 'repeat-owner', rerunApp.id);
  try {
    const w = context.workshop;
    assert.equal(w.canReconfigurePersonalApp.value, true);
    assert.equal(posts.length, 0, 'Opening the saved app must not run or regenerate its strategy');
    await w.reconfigurePersonalApp();
    assert.equal(w.step.value, 'config');
    assert.equal(w.historyReadOnly.value, false);
    assert.equal(w.run.value.runId, frozenAppRun.runId, 'Keep the prior result while parameters are edited');
    assert.equal(w.candidate.value.strategyId, frozenAppRun.candidate.strategyId);
    assert.equal(w.customization.value.sourceHash, 'frozen-code');
    assert.equal(w.customization.value.rulesDigest, 'frozen-rules');
    w.viewPersonalAppResult();
    assert.equal(w.step.value, 'result');
    await w.reconfigurePersonalApp();
    assert.equal(sessionCount, 1, 'Returning to an unfinished configuration must reuse its session');
    Object.assign(w.form, changed);
    await w.checkConfig();
    await w.startRun();
    assert.equal(posts.filter(post => post.url.endsWith('/runs')).length, 0);
    w.coverageAccepted.value = true; w.diagnosticAccepted.value = true;
    w.form.initialCash = 56000;
    assert.equal(w.preflight.value, null);
    assert.equal(w.canRun.value, false, 'Changing parameters invalidates the frozen preflight');
    w.form.initialCash = changed.initialCash;
    await w.checkConfig();
    w.coverageAccepted.value = true; w.diagnosticAccepted.value = true;
    await w.startRun();
    assert.equal(w.run.value.runId, nextRun.runId);
    assert.equal(w.personalApp.value.id, rerunApp.id, 'The originating app remains identifiable on an unsaved new run');
    assert.equal(w.canReconfigurePersonalApp.value, false, 'An active run cannot be replaced');
    await w.syncRun();
    assert.equal(w.canReconfigurePersonalApp.value, true);
    await w.reconfigurePersonalApp();
    assert.equal(sessionCount, 2);
    assert.equal(w.session.value.sessionId, 'app-session-two');
    assert.deepEqual({ ...w.form }, changed, 'The last run parameters seed the next run');
    assert.equal(JSON.stringify(frozenAppRun), original, 'The original saved result remains immutable');
    assert.equal(posts.some(post => /rematch|customization|selection/.test(post.url)), false);
    assert.equal(posts.filter(post => post.url.endsWith('/runs')).length, 1);
    assert.deepEqual(posts[0].body.languageStyle, 'beginner');
  } finally { context.cleanup(); }
});

test('personal app draft restores edited settings and its new result without overwriting the ordinary workshop', async () => {
  let serverSession = structuredClone(appSession);
  let sessionCount = 0;
  const latestRun = { runId: 'restored-app-run', sessionId: appSession.sessionId, sourcePersonalAppId: rerunApp.id,
    sourceRunId: frozenAppRun.runId, status: 'succeeded', candidate: frozenAppRun.candidate, requested: { ...defaults, initialCash: 66000 }, result: frozenAppRun.result };
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body); posts.push(url);
      if (url.endsWith(`/personal-apps/${rerunApp.id}/sessions`)) {
        serverSession = { ...structuredClone(appSession), sessionId: ++sessionCount === 1 ? appSession.sessionId : 'next-app-session' };
        return response(serverSession, 201);
      }
      if (url.endsWith('/preflight')) { serverSession.preflight = { ...preflight, requested: body }; return response(serverSession.preflight); }
      if (url.endsWith('/runs')) { serverSession.runId = latestRun.runId; return response(latestRun, 201); }
    }
    if (url.endsWith(`/personal-apps/${rerunApp.id}`)) return response({ app: rerunApp, run: frozenAppRun });
    if (url.endsWith(`/sessions/${serverSession.sessionId}`)) return response(serverSession);
    if (url.endsWith(`/runs/${latestRun.runId}`)) return response(latestRun);
    return baseGet(url) || response({});
  }, 'draft-owner', rerunApp.id);
  try {
    const w = context.workshop;
    const ordinary = { idea: '普通工坊未发送的草稿', sessionId: 'normal-session', config: { ...defaults, initialCash: 12345 } };
    saveDraft(globalThis.localStorage, 'draft-owner', ordinary);
    const ordinaryBefore = context.records.get(storageKey('draft-owner'));
    await w.reconfigurePersonalApp();
    w.form.initialCash = 66000;
    await nextTick();
    assert.equal(loadDraft(globalThis.localStorage, 'draft-owner', rerunApp.id).config.initialCash, 66000);
    await w.reload();
    assert.equal(w.step.value, 'config');
    assert.equal(w.session.value.sessionId, appSession.sessionId);
    assert.equal(w.form.initialCash, 66000);
    await w.checkConfig();
    await w.reload();
    assert.equal(w.preflight.value.preflightId, preflight.preflightId);
    assert.equal(w.canRun.value, false, 'Refresh never silently restores diagnostic consent');
    w.coverageAccepted.value = true; w.diagnosticAccepted.value = true;
    await w.startRun();
    await w.reload();
    assert.equal(w.step.value, 'result');
    assert.equal(w.run.value.runId, latestRun.runId, 'Refresh must not force the saved original result over the latest run');
    assert.equal(w.personalApp.value.id, rerunApp.id);
    assert.equal(w.canReconfigurePersonalApp.value, true);
    await w.reconfigurePersonalApp();
    w.form.initialCash = 77000;
    await nextTick();
    await w.reload();
    assert.equal(w.step.value, 'config');
    assert.equal(w.form.initialCash, 77000);
    assert.equal(w.run.value.runId, latestRun.runId, 'An unfinished next configuration retains the preceding new result after refresh');
    w.viewPersonalAppResult();
    assert.equal(w.step.value, 'result');
    assert.equal(w.run.value.runId, latestRun.runId);
    assert.equal(w.canReconfigurePersonalApp.value, true);
    assert.equal(context.records.get(storageKey('draft-owner')), ordinaryBefore);
    assert.equal(posts.filter(url => url.endsWith('/runs')).length, 1);
  } finally { context.cleanup(); }
});

test('failed personal-app session creation retains the result and retries with the same idempotency key', async () => {
  let finish;
  const posts = [];
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') { posts.push({ url, body: JSON.parse(options.body) }); return new Promise(resolve => { finish = resolve; }); }
    if (url.endsWith(`/personal-apps/${rerunApp.id}`)) return response({ app: rerunApp, run: frozenAppRun });
    return baseGet(url) || response({});
  }, 'failure-owner', rerunApp.id);
  try {
    const w = context.workshop;
    const before = JSON.stringify(w.run.value);
    const first = w.reconfigurePersonalApp();
    await w.reconfigurePersonalApp();
    assert.equal(posts.length, 1);
    finish(response({ error: '暂时无法载入，请重试' }, 503)); await first;
    assert.equal(w.step.value, 'result');
    assert.equal(JSON.stringify(w.run.value), before);
    assert.equal(w.session.value, null);
    assert.match(w.error.value, /重试/);
    const retry = w.reconfigurePersonalApp();
    assert.equal(posts[0].body.idempotencyKey, posts[1].body.idempotencyKey);
    finish(response(appSession, 201)); await retry;
    assert.equal(w.step.value, 'config');
    assert.equal(w.error.value, '');
  } finally { context.cleanup(); }
});

test('personal-app drafts are isolated by app and owner, and late creation cannot switch the active app', async () => {
  let finish;
  const secondApp = { ...rerunApp, id: 'second-app', runId: 'second-app-run' };
  const context = await mounted(async (url, options) => {
    if (options.method === 'POST') return new Promise(resolve => { finish = resolve; });
    if (url.endsWith(`/personal-apps/${rerunApp.id}`)) return response({ app: rerunApp, run: frozenAppRun });
    if (url.endsWith('/personal-apps/second-app')) return response({ app: secondApp, run: { ...frozenAppRun, runId: secondApp.runId } });
    return baseGet(url) || response({});
  }, 'isolated-owner', rerunApp.id);
  try {
    const w = context.workshop;
    const pending = w.reconfigurePersonalApp();
    await settle(() => Boolean(finish));
    context.personalAppRef.value = secondApp.id;
    await settle(() => !w.loading.value && w.personalApp.value?.id === secondApp.id);
    finish(response(appSession, 201)); await pending;
    assert.equal(w.session.value, null);
    assert.equal(w.run.value.runId, secondApp.runId);
    assert.equal(w.step.value, 'result');
    assert.equal(loadDraft(globalThis.localStorage, 'isolated-owner', secondApp.id), null);
    saveDraft(globalThis.localStorage, 'isolated-owner', { sessionId: appSession.sessionId, config: { ...defaults, initialCash: 99999 } }, rerunApp.id);
    context.ownerRef.value = 'different-owner';
    context.personalAppRef.value = rerunApp.id;
    await settle(() => !w.loading.value && w.personalApp.value?.id === rerunApp.id);
    assert.equal(w.session.value, null);
    assert.equal(w.form.initialCash, defaults.initialCash);
    assert.equal(loadDraft(globalThis.localStorage, 'different-owner', rerunApp.id), null);
  } finally { context.cleanup(); }
});

test('mismatched or inaccessible personal-app restore retains its original result and never loads another strategy', async () => {
  const calls = [];
  let restored = { ...appSession, sourcePersonalAppId: 'different-app' };
  const context = await mounted(async (url, options) => {
    calls.push({ url, method: options.method });
    if (url.endsWith(`/personal-apps/${rerunApp.id}`)) return response({ app: rerunApp, run: frozenAppRun });
    if (url.endsWith(`/sessions/${appSession.sessionId}`)) return response(restored);
    if (url.endsWith('/runs/wrong-run')) return response({ runId: 'wrong-run', sessionId: 'another-session', sourcePersonalAppId: rerunApp.id });
    return baseGet(url) || response({});
  }, 'restore-owner', rerunApp.id);
  try {
    const w = context.workshop;
    saveDraft(globalThis.localStorage, 'restore-owner', { sessionId: appSession.sessionId, config: defaults }, rerunApp.id);
    await w.reload();
    assert.equal(w.session.value, null);
    assert.equal(w.run.value.runId, frozenAppRun.runId);
    assert.match(w.syncError.value, /不属于当前个人应用/);
    assert.equal(loadDraft(globalThis.localStorage, 'restore-owner', rerunApp.id).sessionId, appSession.sessionId, 'A failed restore remains available for retry');
    restored = { ...appSession, runId: 'wrong-run' };
    await w.reload();
    assert.equal(w.session.value, null);
    assert.equal(w.run.value.runId, frozenAppRun.runId);
    assert.match(w.syncError.value, /不属于当前个人应用/);
    assert.equal(calls.some(call => call.url.endsWith('/sessions/current')), false);
    assert.equal(calls.some(call => call.method === 'POST'), false);
  } finally { context.cleanup(); }
});
