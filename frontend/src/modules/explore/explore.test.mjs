import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick } from 'vue';
import { createRequire } from 'node:module';
import { activeRun, feedbackRun, reviewRun, runMaterials, visibleRunMaterials, visibleNewFiles, isEventInitial, isHistoryLiteRun, eventInitialProgressLabel, confirmedMaterialIds, materialContentLabel, exploreHash, parseExploreHash, runTree, safeSourceUrl, stageLabel, retrievalSearches, searchStatusLabel, searchFreshnessLabel, sourceKindLabel, normalizeContexts, contextRequest, contextFiles } from './model.js';
import { useExplore } from './useExplore.js';
import { eventAngleRecommendations, isEventLite, pendingEventChoice } from './reportPresentation.js';

const require = createRequire(import.meta.url);
const format = require('../workbench/pages/format.js');
const response = data => new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } });
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); };

test('Explore hash preserves session and run while rejecting unrelated hashes', () => {
  const hash = exploreHash('会话/1', 'r?2');
  assert.deepEqual(parseExploreHash(hash), { isExplore: true, sessionId: '会话/1', runId: 'r?2' });
  assert.deepEqual(parseExploreHash('#other'), { isExplore: false, sessionId: '', runId: '' });
});

test('run tree groups late branches under their actual parent report', () => {
  const runs = [
    { id: 'root', report: { id: 'p0' }, created_at: '2026-01-01T00:00:00Z' },
    { id: 'a', source_report_id: 'p0', report: { id: 'pa' }, created_at: '2026-01-02T00:00:00Z' },
    { id: 'a1', source_report_id: 'pa', report: { id: 'pa1' }, created_at: '2026-01-03T00:00:00Z' },
    { id: 'b', source_report_id: 'p0', report: { id: 'pb' }, created_at: '2026-01-04T00:00:00Z' },
    { id: 'a2', source_report_id: 'pa', status: 'failed', created_at: '2026-01-05T00:00:00Z' },
  ];
  assert.deepEqual(runTree(runs).map(run => [run.id, run.depth]), [['root', 0], ['a', 1], ['a1', 2], ['a2', 2], ['b', 1]]);
});

test('only safe external source links can be opened; report Markdown escapes injected HTML', () => {
  assert.equal(safeSourceUrl('javascript:alert(1)'), '');
  assert.equal(safeSourceUrl('https://localhost/private'), '');
  assert.equal(safeSourceUrl('http://192.168.1.2/key'), '');
  assert.equal(safeSourceUrl('https://user:pass@example.com/article'), '');
  assert.equal(safeSourceUrl('https://example.com/article'), 'https://example.com/article');
  const html = format.renderProse('<img src=x onerror=alert(1)> [open](javascript:alert(1))');
  assert.ok(html.includes('&lt;img'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('href="javascript:'));
});

test('search trace labels preserve actual scope, outcomes and source kind; old reports remain readable', () => {
  assert.equal(stageLabel('searching_web'), '正在联网搜索');
  assert.equal(stageLabel('checking_intent'), '正在核对研究问题');
  assert.equal(stageLabel('preparing_report'), '准备生成报告');
  assert.deepEqual(retrievalSearches({ sources: [] }), []);
  assert.deepEqual(retrievalSearches({ retrieval: { searches: [{ query: '半导体', freshness: 'oneWeek', count: 0, status: 'empty' }] } }), [{ query: '半导体', freshness: 'oneWeek', count: 0, status: 'empty' }]);
  assert.equal(searchFreshnessLabel('oneWeek'), '最近 1 周');
  assert.equal(searchFreshnessLabel('noLimit'), '不限时间');
  assert.equal(searchStatusLabel('empty'), '未找到结果');
  assert.equal(searchStatusLabel('failed'), '检索失败');
  assert.equal(sourceKindLabel('web_search'), '网页搜索');
  assert.equal(sourceKindLabel('news'), 'AkShare 快讯');
  assert.equal(sourceKindLabel('user'), '用户提供材料');
  assert.equal(sourceKindLabel(undefined), '来源类型未标明');
});

test('review materials use only saved source IDs and identify excerpts without claiming full text', () => {
  const run = { status: 'awaiting_review', materials: [{ id: 'S1', content_kind: 'search_snippet' }, null, { id: 'U1', content_kind: 'user_input' }], confirmed_sources: [{ id: 'U1' }, 'missing', { id: 'S1' }] };
  assert.equal(activeRun(run), false);
  assert.equal(reviewRun(run), true);
  assert.deepEqual(runMaterials(run).map(item => item.id), ['S1', 'U1']);
  assert.deepEqual(confirmedMaterialIds(run), ['U1', 'S1']);
  assert.deepEqual(runMaterials({ report: { id: 'old' } }), []);
  assert.deepEqual(confirmedMaterialIds({ report: { id: 'old' } }), []);
  assert.equal(materialContentLabel('search_snippet'), '搜索摘要，未读取网页全文');
  assert.equal(materialContentLabel('news_excerpt'), '快讯摘录，未读取新闻全文');
});

test('first event answer hides even historical saved news and only announces its input and answer files', () => {
  const initial = {
    id: 'r1', module: 'event', workflow_version: 'event-lite@1', phase: 'initial_explanation',
    status: 'awaiting_feedback', initial_answer: '简明解释', initial_file_ids: { input: 'f-input', answer: 'f-answer' },
    materials: [{ id: 'S1', title: '旧版首步曾保存的新闻', kind: 'web_search' }],
    retrieval: { searches: [{ query: '旧版首步搜索' }], tool_trace: [{ name: 'web_search' }] },
  };
  const files = [
    { id: 'f-source', source_run_id: 'r1', phase: 'initial_explanation', source_id: 'S1', kind: 'material', origin: 'web_search' },
    { id: 'f-input', source_run_id: 'r1', phase: 'initial_explanation', source_id: null, kind: 'material', origin: 'user' },
    { id: 'f-answer', source_run_id: 'r1', phase: 'initial_explanation', source_id: null, kind: 'report', origin: 'report' },
    { id: 'f-other', source_run_id: 'another-run', phase: 'event_deep_report', kind: 'material', origin: 'web_search' },
  ];
  assert.equal(isEventInitial(initial), true);
  assert.deepEqual(visibleRunMaterials(initial), []);
  assert.deepEqual(visibleNewFiles(files, initial).map(file => file.id), ['f-input', 'f-answer']);
  assert.equal(eventInitialProgressLabel({ ...initial, stage: 'searching_web' }), '正在探索');
  assert.equal(eventInitialProgressLabel({ ...initial, stage: 'explaining', model_progress: { output_chars: 3 } }), '正在回复');
  assert.equal(isEventInitial({ ...initial, phase: 'done', status: 'succeeded', feedback_choice: 'understood' }), true);
  const deep = { ...initial, phase: 'event_deep_report', status: 'running', feedback_choice: 'explain_more' };
  assert.equal(isEventInitial(deep), false);
  assert.deepEqual(visibleRunMaterials(deep), initial.materials);
  assert.deepEqual(visibleNewFiles(files, deep), files);
  assert.equal(isEventInitial({ ...initial, report: { id: 'deep-report' } }), false);
  assert.equal(isEventInitial({ ...initial, module: 'relation' }), false);
});

test('active first event step shows only its progress while the deep step keeps the research composer', async () => {
  for (const phase of ['initial_explanation', 'event_deep_report']) {
    const run = { id: 'r1', session_id: 's1', module: 'event', workflow_version: 'event-lite@1',
      phase, status: 'running', stage: phase === 'initial_explanation' ? 'searching_web' : 'analyzing',
      initial_answer: phase === 'event_deep_report' ? '首步简答' : null, report: null };
    const h = harness('#explore?session=s1&run=r1', async rawUrl => {
      const url = String(rawUrl);
      if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1' }, runs: [run] });
      if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's1' }] });
      return response({ files: [], contexts: {} });
    });
    try {
      await h.ready();
      assert.equal(h.explore.showComposer.value, phase === 'event_deep_report');
    } finally { h.close(); }
  }
});

test('history first answer stays available while retrieval runs, after the report, and after reload', async () => {
  const intro = {
    id: 'r-history', session_id: 's-history', module: 'history', workflow_version: 'history-lite@1',
    phase: 'history_report', status: 'running', stage: 'retrieving_news', question: '两家公司历史上有何联系？',
    initial_answer: '当前关系可能受到过去合作的影响，因此要检索历史关联新闻。', report: null,
  };
  const report = {
    id: 'p-history', module: 'history', report_format: 'history_lite@1',
    recommendations: [
      { module: 'event', question: '当时发生了什么？' },
      { module: 'relation', question: '还有哪些关联主体？' },
      { module: 'history', question: '更早还有哪些合作？' },
    ],
  };
  const completed = { ...intro, status: 'succeeded', stage: 'completed', next_step_state: 'pending', report };
  let current = intro;
  const fetcher = async rawUrl => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s-history')) return response({ session: { id: 's-history' }, runs: [current] });
    if (url.endsWith('/runs/r-history')) return response({ run: completed });
    if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's-history' }] });
    return response({ files: [], contexts: {} });
  };
  const first = harness('#explore?session=s-history&run=r-history', fetcher);
  try {
    await first.ready();
    assert.equal(isHistoryLiteRun(first.explore.selectedRun.value), true);
    assert.equal(first.explore.selectedRun.value.initial_answer, intro.initial_answer);
    assert.equal(first.explore.isActive.value, true);
    assert.equal(first.explore.showComposer.value, false);
    await new Promise(resolve => setTimeout(resolve, 1800));
    await flush();
    assert.equal(first.explore.selectedRun.value.initial_answer, intro.initial_answer);
    assert.equal(first.explore.selectedReport.value.id, report.id);
    assert.equal(first.explore.canChooseLiteRecommendation.value, true);
    assert.equal(first.explore.showComposer.value, false);
  } finally { first.close(); }
  current = completed;
  const reloaded = harness('#explore?session=s-history&run=r-history', fetcher);
  try {
    await reloaded.ready();
    assert.equal(reloaded.explore.selectedRun.value.initial_answer, intro.initial_answer);
    assert.equal(reloaded.explore.selectedReport.value.id, report.id);
    assert.equal(reloaded.explore.canChooseLiteRecommendation.value, true);
  } finally { reloaded.close(); }
});

test('module file preferences are isolated, bounded, and old run snapshots are readable', () => {
  const contexts = normalizeContexts({ event: { mode: 'manual', file_ids: ['f1', 'f2', 'f1'] }, relation: { mode: 'manual', file_ids: ['f3'] }, history: { mode: 'auto', file_ids: ['f4'] } });
  assert.deepEqual(contexts.event.file_ids, ['f1', 'f2']);
  assert.deepEqual(contexts.relation.file_ids, ['f3']);
  assert.deepEqual(contexts.history.file_ids, []);
  assert.deepEqual(contextRequest('event', contexts.event), { module: 'event', mode: 'manual', file_ids: ['f1', 'f2'] });
  assert.deepEqual(contextFiles({ context_files: [{ file_id: 'f1', content_excerpt: '真实摘录\n第二行', truncated: true }, null] }), [{ file_id: 'f1', content_excerpt: '真实摘录\n第二行', truncated: true }]);
  assert.deepEqual(contextFiles({ report: { id: 'old' } }), []);
});

test('the second event answer accepts one model-authored question for each angle', () => {
  const recommendations = [
    { module: 'history', question: '过去类似变化何时失效？', reason: '验证条件' },
    { module: 'event', question: '这次公告中的关键变化是什么？', reason: '厘清事实' },
    { module: 'relation', question: '供应商会受到哪些传导影响？', reason: '查找关系' },
  ];
  const report = { id: 'p1', module: 'event', report_format: 'event_lite@2', recommendations };
  assert.equal(isEventLite(report), true);
  assert.deepEqual(eventAngleRecommendations(report).map(item => item.module), ['event', 'relation', 'history']);
  assert.equal(pendingEventChoice({ report, next_step_state: 'pending' }), true);
  assert.equal(pendingEventChoice({ report, next_step_state: 'continued' }), false);
  assert.deepEqual(eventAngleRecommendations({ ...report, recommendations: recommendations.slice(0, 2) }), []);
});

function harness(initialHash, fetchHandler, { preferences = new Map(), pendingStore = new Map(), ownerId = 'owner-a' } = {}) {
  const old = { fetch: globalThis.fetch, window: globalThis.window, location: globalThis.location, history: globalThis.history, sessionStorage: globalThis.sessionStorage, localStorage: globalThis.localStorage };
  const listeners = new Map();
  const store = pendingStore;
  globalThis.window = { addEventListener(name, listener) { listeners.set(name, listener); }, removeEventListener(name) { listeners.delete(name); } };
  globalThis.location = { pathname: '/desktop/', search: '', hash: initialHash };
  globalThis.history = { pushState(_state, _title, url) { globalThis.location.hash = new URL(url, 'http://example.test').hash; }, replaceState(_state, _title, url) { globalThis.location.hash = new URL(url, 'http://example.test').hash; } };
  globalThis.sessionStorage = { getItem(key) { return store.get(key) || null; }, setItem(key, value) { store.set(key, value); }, removeItem(key) { store.delete(key); } };
  globalThis.localStorage = { getItem(key) { return preferences.get(key) || null; }, setItem(key, value) { preferences.set(key, value); }, removeItem(key) { preferences.delete(key); } };
  globalThis.fetch = fetchHandler;
  const renderer = createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode() { return null; }, nextSibling() { return null; }, patchProp() {} });
  let explore;
  const app = renderer.createApp({ setup() { explore = useExplore(ownerId); return () => null; } });
  app.mount({});
  return { explore, store, preferences, listeners, async ready() { await flush(); await flush(); }, close() { app.unmount(); Object.assign(globalThis, old); } };
}

test('history stays visible through navigation and only an explicit hide persists for this account', async () => {
  const preferences = new Map();
  const runs = [{ id: 'r1', session_id: 's1', status: 'succeeded', report: { id: 'p1' } }];
  const fetcher = async url => response(String(url).endsWith('/sessions/s1')
    ? { session: { id: 's1', title: '研究' }, runs }
    : { sessions: [{ id: 's1', title: '研究' }] });
  const first = harness('#explore?session=s1&run=r1', fetcher, { preferences });
  try {
    await first.ready();
    assert.equal(first.explore.state.historyOpen, true);
    first.explore.selectRun('r1');
    first.explore.newExplore();
    await first.explore.openSession('s1', 'r1');
    assert.equal(first.explore.state.historyOpen, true);
    first.explore.setHistoryOpen(false);
    first.explore.selectRun('r1');
    first.explore.newExplore();
    assert.equal(first.explore.state.historyOpen, false);
  } finally { first.close(); }
  const sameAccount = harness('#explore', fetcher, { preferences });
  try { assert.equal(sameAccount.explore.state.historyOpen, false); }
  finally { sameAccount.close(); }
  const otherAccount = harness('#explore', fetcher, { preferences, ownerId: 'owner-b' });
  try { assert.equal(otherAccount.explore.state.historyOpen, true); }
  finally { otherAccount.close(); }
});

test('Explore offers only Flash and a retired saved preference cannot change historical run models', async () => {
  const preferences = new Map();
  const runs = [{ id: 'r1', session_id: 's1', module: 'event', model: 'GLM-5.3', status: 'succeeded', report: { id: 'p1' } }];
  const fetcher = async url => response(String(url).endsWith('/sessions/s1')
    ? { session: { id: 's1', title: '研究' }, runs }
    : { sessions: [{ id: 's1', title: '研究' }] });
  const first = harness('#explore?session=s1&run=r1', fetcher, { preferences });
  try {
    await first.ready();
    assert.equal(first.explore.state.model, 'GLM-5.3-Flash');
    assert.equal(first.explore.setModel('GLM-5.3'), false);
    assert.equal(first.explore.setModel('GLM-5.3-Flash'), true);
    assert.equal(first.explore.setModel('unlisted-model'), false);
    first.explore.selectRun('r1');
    first.explore.newExplore();
    await first.explore.openSession('s1', 'r1');
    assert.equal(first.explore.state.model, 'GLM-5.3-Flash');
    assert.equal(first.explore.selectedRun.value.model, 'GLM-5.3');
    assert.equal(preferences.get('ficagent-explore-model:owner-a'), 'GLM-5.3-Flash');
  } finally { first.close(); }
  const sameOwner = harness('#explore', fetcher, { preferences });
  try { assert.equal(sameOwner.explore.state.model, 'GLM-5.3-Flash'); }
  finally { sameOwner.close(); }
  const otherOwner = harness('#explore', fetcher, { preferences, ownerId: 'owner-b' });
  try { assert.equal(otherOwner.explore.state.model, 'GLM-5.3-Flash'); }
  finally { otherOwner.close(); }
  preferences.set('ficagent-explore-model:owner-a', 'GLM-5.3');
  const invalid = harness('#explore', fetcher, { preferences });
  try { assert.equal(invalid.explore.state.model, 'GLM-5.3-Flash'); }
  finally { invalid.close(); }
});

test('new Explore submission freezes the selected model before creating its session', async () => {
  const posts = [];
  let resolveSession;
  const h = harness('#explore', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions') && options.method === 'POST') return new Promise(resolve => { resolveSession = resolve; });
    if (url.endsWith('/sessions/s1/runs') && options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      return response({ run: { id: 'r1', session_id: 's1', module: 'event', model: posts.at(-1).model, status: 'succeeded', report: { id: 'p1' } } });
    }
    if (url.endsWith('/sessions')) return response({ sessions: [] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    h.explore.setModel('GLM-5.3-Flash');
    h.explore.state.draft = '本周发生了什么？';
    const pending = h.explore.submit();
    await flush();
    assert.equal(h.explore.state.submitting, true);
    assert.equal(h.explore.setModel('GLM-5.3-Flash'), false);
    h.explore.state.model = 'GLM-5.3'; // Simulate a late unsupported state update during createSession.
    resolveSession(response({ session: { id: 's1', title: '本周发生了什么？' } }));
    assert.equal(await pending, true);
    assert.equal(posts.length, 1);
    assert.equal(posts[0].model, 'GLM-5.3-Flash');
  } finally { h.close(); }
});

test('a history row can be renamed or deleted without changing the open report or draft', async () => {
  const sessions = [{ id: 's1', title: '当前探索' }, { id: 's2', title: '另一探索' }];
  const writes = [];
  const report = { id: 'r1', session_id: 's1', status: 'succeeded', report: { id: 'p1', title: '正在阅读的报告' } };
  const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    const method = options.method || 'GET';
    if (url.endsWith('/sessions/s2') && method === 'PATCH') {
      const title = JSON.parse(options.body).title;
      writes.push({ method, url, title });
      sessions[1] = { ...sessions[1], title };
      return response({ session: { ...sessions[1] } });
    }
    if (url.endsWith('/sessions/s2') && method === 'DELETE') {
      writes.push({ method, url });
      sessions.pop();
      return response({ deleted: true });
    }
    if (url.endsWith('/sessions/s1')) return response({ session: { ...sessions[0] }, runs: [report] });
    if (url.endsWith('/sessions')) return response({ sessions: [...sessions] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    h.explore.state.draft = '保留这一段未提交的问题';
    const originalHash = globalThis.location.hash;
    assert.equal(h.explore.startRename(h.explore.state.sessions[1]), true);
    assert.equal(h.explore.state.renameSessionId, 's2');
    h.explore.state.renameDraft = '  新名称  ';
    assert.equal(await h.explore.rename(), true);
    assert.deepEqual(writes[0], { method: 'PATCH', url: '/desktop/api/explore/sessions/s2', title: '新名称' });
    assert.equal(h.explore.state.sessions[1].title, '新名称');
    assert.equal(h.explore.state.session.title, '当前探索');
    assert.equal(h.explore.selectedReport.value?.title, '正在阅读的报告');
    assert.equal(h.explore.state.draft, '保留这一段未提交的问题');
    assert.equal(globalThis.location.hash, originalHash);
    assert.equal(h.explore.startDelete(h.explore.state.sessions[1]), true);
    assert.equal(h.explore.state.deleteSessionTitle, '新名称');
    assert.equal(await h.explore.removeSession(), true);
    assert.equal(writes[1].method, 'DELETE');
    assert.deepEqual(h.explore.state.sessions.map(item => item.id), ['s1']);
    assert.equal(h.explore.selectedReport.value?.id, 'p1');
    assert.equal(h.explore.state.draft, '保留这一段未提交的问题');
    assert.equal(globalThis.location.hash, originalHash);
  } finally { h.close(); }
});

test('session action errors keep the modal and draft; an in-flight submit cannot duplicate the request', async () => {
  let resolveRename;
  let patchCount = 0;
  const sessions = [{ id: 's1', title: '原名' }];
  const h = harness('#explore?session=s1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s1') && options.method === 'PATCH') {
      patchCount += 1;
      if (patchCount === 1) return new Response(JSON.stringify({ error: '暂时无法保存' }), { status: 503, headers: { 'Content-Type': 'application/json' } });
      return new Promise(resolve => { resolveRename = () => { sessions[0] = { id: 's1', title: '新名称' }; resolve(response({ session: sessions[0] })); }; });
    }
    if (url.endsWith('/sessions/s1')) return response({ session: { ...sessions[0] }, runs: [] });
    if (url.endsWith('/sessions')) return response({ sessions: [...sessions] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    assert.equal(h.explore.startRename(), true);
    h.explore.state.renameDraft = '  ';
    assert.equal(await h.explore.rename(), false);
    assert.match(h.explore.state.sessionActionError, /请输入/);
    assert.equal(patchCount, 0);
    h.explore.state.renameDraft = '字'.repeat(121);
    assert.equal(await h.explore.rename(), false);
    assert.match(h.explore.state.sessionActionError, /120/);
    assert.equal(patchCount, 0);
    h.explore.state.renameDraft = '  新名称  ';
    assert.equal(await h.explore.rename(), false);
    assert.equal(h.explore.state.renameOpen, true);
    assert.equal(h.explore.state.renameDraft, '  新名称  ');
    assert.equal(h.explore.state.sessionActionError, '暂时无法保存');
    const pending = h.explore.rename();
    assert.equal(h.explore.closeSessionAction(), false);
    assert.equal(await h.explore.rename(), false);
    assert.equal(patchCount, 2);
    resolveRename();
    assert.equal(await pending, true);
    assert.equal(h.explore.state.renameOpen, false);
    assert.equal(h.explore.state.session.title, '新名称');
    assert.equal(h.explore.state.sessionActionError, '');
    assert.equal(h.explore.startRename(), true);
    assert.equal(h.explore.closeSessionAction(), true);
  } finally { h.close(); }
});

test('late row rename updates history after navigation without replacing the newly opened session', async () => {
  let resolveRename;
  const sessions = [{ id: 's1', title: '第一页' }, { id: 's2', title: '待改名' }, { id: 's3', title: '后来打开' }];
  const h = harness('#explore?session=s1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s2') && options.method === 'PATCH') return new Promise(resolve => {
      resolveRename = () => { sessions[1] = { id: 's2', title: '已改名' }; resolve(response({ session: sessions[1] })); };
    });
    if (url.endsWith('/sessions/s1')) return response({ session: { ...sessions[0] }, runs: [] });
    if (url.endsWith('/sessions/s3')) return response({ session: { ...sessions[2] }, runs: [] });
    if (url.endsWith('/sessions')) return response({ sessions: [...sessions] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    assert.equal(h.explore.startRename(h.explore.state.sessions[1]), true);
    h.explore.state.renameDraft = '已改名';
    const pending = h.explore.rename();
    await h.explore.openSession('s3');
    assert.equal(h.explore.state.renameOpen, false);
    resolveRename();
    assert.equal(await pending, true);
    assert.equal(h.explore.state.session.id, 's3');
    assert.equal(h.explore.state.session.title, '后来打开');
    assert.equal(h.explore.state.sessions.find(item => item.id === 's2')?.title, '已改名');
    assert.match(globalThis.location.hash, /session=s3/);
  } finally { h.close(); }
});

test('deleting the former session does not interrupt a different session that is still loading', async () => {
  let resolveDelete;
  let resolveNext;
  const sessions = [{ id: 's1', title: '原会话' }, { id: 's2', title: '新会话' }];
  const h = harness('#explore?session=s1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s1') && options.method === 'DELETE') return new Promise(resolve => {
      resolveDelete = () => { sessions.shift(); resolve(response({ deleted: true })); };
    });
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '原会话' }, runs: [] });
    if (url.endsWith('/sessions/s2')) return new Promise(resolve => {
      resolveNext = () => resolve(response({ session: { id: 's2', title: '新会话' }, runs: [] }));
    });
    if (url.endsWith('/sessions')) return response({ sessions: [...sessions] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    assert.equal(h.explore.startDelete(), true);
    const deletion = h.explore.removeSession();
    const navigation = h.explore.openSession('s2');
    assert.equal(h.explore.state.loadingSession, true);
    assert.equal(h.explore.state.session.id, 's1');
    resolveDelete();
    assert.equal(await deletion, true);
    assert.equal(h.explore.state.loadingSession, true);
    assert.equal(h.explore.state.session.id, 's1');
    resolveNext();
    await navigation;
    assert.equal(h.explore.state.session.id, 's2');
    assert.equal(h.explore.state.loadingSession, false);
    assert.match(globalThis.location.hash, /session=s2/);
  } finally { h.close(); }
});

test('deleting the destination cancels its stale GET and leaves a usable new exploration', async () => {
  let resolveDelete;
  let resolveTarget;
  const sessions = [{ id: 's1', title: '当前' }, { id: 's2', title: '将删除' }];
  const h = harness('#explore?session=s1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s2') && options.method === 'DELETE') return new Promise(resolve => {
      resolveDelete = () => { sessions.pop(); resolve(response({ deleted: true })); };
    });
    if (url.endsWith('/sessions/s1')) return response({ session: { ...sessions[0] }, runs: [] });
    if (url.endsWith('/sessions/s2')) return new Promise(resolve => {
      resolveTarget = () => resolve(response({ session: { id: 's2', title: '迟到的旧响应' }, runs: [] }));
    });
    if (url.endsWith('/sessions')) return response({ sessions: [...sessions] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    assert.equal(h.explore.startDelete(h.explore.state.sessions[1]), true);
    const deletion = h.explore.removeSession();
    const navigation = h.explore.openSession('s2');
    assert.equal(h.explore.state.loadingSession, true);
    resolveDelete();
    assert.equal(await deletion, true);
    assert.equal(h.explore.state.session, null);
    assert.equal(h.explore.state.loadingSession, false);
    assert.equal(h.explore.state.contextLoading, false);
    assert.equal(globalThis.location.hash, '#explore');
    resolveTarget();
    await navigation;
    assert.equal(h.explore.state.session, null);
    assert.equal(h.explore.canSubmit.value, false);
    h.explore.state.draft = '一个新问题';
    assert.equal(h.explore.canSubmit.value, true);
  } finally { h.close(); }
});

test('refresh restores the selected saved report with GET requests only', async () => {
  const calls = [];
  const run = { id: 'r1', session_id: 's1', module: 'event', question: '研究问题', status: 'succeeded', report: { id: 'p1', title: '真实已保存报告' } };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    calls.push([String(url), options.method || 'GET']);
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '会话' }, runs: [run] } : { sessions: [{ id: 's1', title: '会话' }] });
  });
  try {
    await h.ready();
    assert.equal(h.explore.selectedReport.value?.title, '真实已保存报告');
    assert.ok(calls.length >= 4);
    assert.ok(calls.every(item => item[1] === 'GET'));
    assert.ok(calls.some(item => item[0].endsWith('/sessions/s1/files')));
    assert.ok(calls.some(item => item[0].endsWith('/sessions/s1/context')));
  } finally { h.close(); }
});

test('shared file pool keeps per-module selections, sends a frozen context, and removes future preferences', async () => {
  const requests = [];
  const files = [
    { id: 'f1', name: '网页摘录', kind: 'material', origin: 'web_search' },
    { id: 'f2', name: '旧报告', kind: 'report', origin: 'report' },
  ];
  const contexts = { event: { mode: 'manual', file_ids: ['f1'] }, relation: { mode: 'manual', file_ids: ['f2'] }, history: { mode: 'auto', file_ids: [] } };
  const root = { id: 'r1', session_id: 's1', module: 'event', question: '起点', status: 'succeeded', report: { id: 'p1' }, context_files: [{ file_id: 'f1', name: '网页摘录', content_excerpt: '服务端真实文本', truncated: false }] };
  const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    const method = options.method || 'GET';
    requests.push({ url, method, body: options.body ? JSON.parse(options.body) : null });
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '研究' }, runs: [root] });
    if (url.endsWith('/sessions/s1/files')) return response({ files: [...files] });
    if (url.includes('/sessions/s1/files/f1') && method === 'GET') return response({ file: { ...files[0], content: '搜索摘要\n第二行' } });
    if (url.includes('/sessions/s1/files/f1') && method === 'PATCH') { files[0].name = JSON.parse(options.body).name; return response({ file: { ...files[0] } }); }
    if (url.includes('/sessions/s1/files/f1') && method === 'DELETE') { files.splice(0, 1); for (const item of Object.values(contexts)) item.file_ids = item.file_ids.filter(id => id !== 'f1'); return response({ deleted: true }); }
    if (url.endsWith('/sessions/s1/context') && method === 'GET') return response({ contexts });
    if (url.includes('/sessions/s1/context?')) return response({ context: { module: 'event', mode: 'auto', preview_files: [] } });
    if (url.endsWith('/sessions/s1/context') && method === 'PUT') { const body = JSON.parse(options.body); contexts[body.module] = { mode: body.mode, file_ids: body.file_ids }; return response({ context: { module: body.module, ...contexts[body.module] } }); }
    if (url.endsWith('/sessions/s1/runs') && method === 'POST') { const body = JSON.parse(options.body); return response({ run: { id: 'r2', session_id: 's1', module: body.module, question: body.question, status: 'succeeded', report: { id: 'p2' }, context_selection: { module: body.module, mode: body.context_mode, file_ids: body.file_ids }, context_files: [{ file_id: 'f2', name: '旧报告', content_excerpt: 'AI 生成摘要', truncated: true, used_chars: 40, original_chars: 500 }] } }); }
    return response({ sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    assert.deepEqual(h.explore.currentContext.value.file_ids, ['f1']);
    h.explore.state.module = 'relation';
    await flush();
    assert.deepEqual(h.explore.currentContext.value.file_ids, ['f2']);
    h.explore.toggleContextFile('f1', true);
    assert.deepEqual(h.explore.currentContext.value.file_ids, ['f2', 'f1']);
    await h.explore.saveContext();
    assert.deepEqual(contexts.relation.file_ids, ['f2', 'f1']);
    h.explore.state.module = 'event';
    await flush();
    assert.deepEqual(h.explore.currentContext.value.file_ids, ['f1']);
    await h.explore.openFile('f1');
    assert.equal(h.explore.state.fileDetail.content, '搜索摘要\n第二行');
    h.explore.state.fileNameDraft = '清晰名称';
    await h.explore.renameFile();
    assert.equal(h.explore.state.files[0].name, '清晰名称');
    h.explore.state.module = 'relation';
    h.explore.state.draft = '新的关联问题';
    await h.explore.submit();
    const create = requests.find(item => item.method === 'POST' && item.url.endsWith('/sessions/s1/runs'));
    assert.deepEqual(create.body.file_ids, ['f2', 'f1']);
    assert.equal(create.body.context_mode, 'manual');
    assert.equal(h.explore.selectedRun.value.context_files[0].truncated, true);
    h.explore.state.module = 'event';
    h.explore.toggleContextFile('f2', true);
    assert.deepEqual(h.explore.currentContext.value.file_ids, ['f1', 'f2']);
    await h.explore.removeFile();
    assert.equal(h.explore.state.files.some(item => item.id === 'f1'), false);
    assert.deepEqual(h.explore.state.contexts.relation.file_ids, ['f2']);
    assert.deepEqual(h.explore.state.contextDrafts.event.file_ids, ['f2']);
    assert.equal(h.explore.state.runs[0].context_files[0].content_excerpt, '服务端真实文本');
  } finally { h.close(); }
});

test('branch submission uses the selected report and reuses its key after a lost response', async () => {
  const posts = [];
  const root = { id: 'r1', session_id: 's1', module: 'event', question: '起点', status: 'succeeded', report: { id: 'p1', title: '起点报告' } };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { id: 'r2', session_id: 's1', module: 'relation', question: '后续关联', source_report_id: 'p1', status: 'awaiting_input' } });
    }
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '会话' }, runs: [root] } : { sessions: [{ id: 's1', title: '会话' }] });
  });
  try {
    await h.ready();
    h.explore.state.module = 'relation';
    h.explore.state.draft = '后续关联';
    await h.explore.submit();
    assert.match(h.explore.state.actionError, /再次提交会沿用同一请求标识/);
    await h.explore.submit();
    assert.equal(posts.length, 2);
    assert.equal(posts[0].source_report_id, 'p1');
    assert.equal(posts[0].idempotency_key, posts[1].idempotency_key);
    assert.equal(h.explore.state.runs.length, 2);
    assert.equal(h.explore.state.runs[0].report.id, 'p1');
  } finally { h.close(); }
});

test('a retired model selection cannot alter the retry key after a lost create response', async () => {
  const posts = [];
  const root = { id: 'r1', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'p1' } };
  const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body);
      posts.push(body);
      if (posts.length < 3) throw new Error('connection lost');
      return response({ run: { id: 'r2', session_id: 's1', module: body.module, model: body.model, status: 'succeeded', report: { id: 'p2' } } });
    }
    return response(String(rawUrl).endsWith('/sessions/s1')
      ? { session: { id: 's1', title: '研究' }, runs: [root] }
      : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    h.explore.state.draft = '同一个问题';
    await h.explore.submit();
    assert.equal(h.explore.setModel('GLM-5.3'), false);
    await h.explore.submit();
    h.explore.setModel('GLM-5.3-Flash');
    await h.explore.submit();
    assert.deepEqual(posts.map(body => body.model), ['GLM-5.3-Flash', 'GLM-5.3-Flash', 'GLM-5.3-Flash']);
    assert.equal(posts[0].idempotency_key, posts[1].idempotency_key);
    assert.equal(posts[0].idempotency_key, posts[2].idempotency_key);
  } finally { h.close(); }
});

test('retired or unknown historical runs require a fresh Flash study; Flash regeneration stays frozen', async () => {
  for (const originalModel of ['GLM-5.3', undefined, 'GLM-5.3-Flash']) {
    const posts = [];
    const run = { id: 'r1', session_id: 's1', module: 'event', question: '原问题', status: 'succeeded', report: { id: 'p1' } };
    if (originalModel) run.model = originalModel;
    const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
      if (options.method === 'POST') {
        const body = JSON.parse(options.body);
        posts.push(body);
        return response({ run: { id: 'r2', session_id: 's1', module: body.module, status: 'queued' } });
      }
      return response(String(rawUrl).endsWith('/sessions/s1')
        ? { session: { id: 's1', title: '研究' }, runs: [run] }
        : { sessions: [{ id: 's1', title: '研究' }] });
    });
    try {
      await h.ready();
      h.explore.setModel('GLM-5.3-Flash');
      await h.explore.regenerate();
      if (originalModel === 'GLM-5.3-Flash') {
        assert.equal(posts.length, 1);
        assert.equal(posts[0].model, 'GLM-5.3-Flash');
      } else {
        assert.equal(posts.length, 0);
        assert.match(h.explore.state.actionError, /新建 Flash 研究/);
        assert.equal(h.explore.newFlashFromLegacy(), true);
        assert.equal(h.explore.state.draft, '原问题');
        assert.equal(h.explore.state.model, 'GLM-5.3-Flash');
      }
    } finally { h.close(); }
  }
});

test('late session response does not replace a newly chosen exploration', async () => {
  let resolveSession;
  const h = harness('#explore?session=s1', async url => {
    if (String(url).endsWith('/sessions/s1')) return new Promise(resolve => { resolveSession = resolve; });
    return response({ sessions: [{ id: 's1', title: 'Old' }] });
  });
  try {
    await flush(); await flush();
    h.explore.newExplore();
    resolveSession(response({ session: { id: 's1', title: 'Old' }, runs: [] }));
    await flush();
    assert.equal(h.explore.state.session, null);
    assert.equal(globalThis.location.hash, '#explore');
  } finally { h.close(); }
});

test('saved clarification restores by GET only and a lost clarification response reuses its key', async () => {
  const posts = [];
  const initial = { id: 'r1', session_id: 's1', module: 'event', question: '半导体', status: 'awaiting_clarification', report: null,
    review: { revision: 2, question: '想研究哪一方面？', interpreted_question: '半导体', options: [{ id: 'o1', question: '政策如何影响供应链？' }] },
    materials: [{ id: 'S1', text_used: '一段摘要\n第二行', content_kind: 'search_snippet' }], retrieval: { searches: [{ query: '半导体政策', count: 1, status: 'complete' }] } };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push({ url: String(url), body: JSON.parse(options.body) });
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { ...initial, status: 'running', stage: 'analyzing', review: null, resolved_question: '政策如何影响供应链？' } });
    }
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs: [initial] } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    assert.equal(h.explore.selectedReport.value, null);
    assert.equal(h.explore.state.reviewQuestion, '');
    assert.equal(h.explore.selectedRun.value.materials[0].text_used, '一段摘要\n第二行');
    assert.equal(posts.length, 0);
    h.explore.state.reviewQuestion = '政策如何影响供应链？';
    await h.explore.clarify();
    assert.match(h.explore.state.actionError, /再次提交会沿用同一请求标识/);
    await h.explore.clarify();
    assert.equal(posts.length, 2);
    assert.ok(posts.every(item => item.url.endsWith('/runs/r1/clarify')));
    assert.deepEqual(posts[0].body, posts[1].body);
    assert.equal(posts[0].body.revision, 2);
    assert.equal(posts[0].body.question, '政策如何影响供应链？');
    assert.equal(h.explore.selectedRun.value.status, 'running');
    assert.equal(h.explore.state.reviewQuestion, '');
    assert.deepEqual(h.explore.state.selectedSourceIds, []);
  } finally { h.close(); }
});

test('a clarification candidate sends option_id directly and never needs a second confirmation', async () => {
  const posts = [];
  const initial = { id: 'r1', session_id: 's1', module: 'relation', question: '最近的新闻', status: 'awaiting_clarification',
    review: { revision: 1, question: '想研究什么关联？', interpreted_question: '最近的新闻',
      options: [{ id: 'o1', label: '政策关联', question: '研究政策与产业链的关联' }] }, materials: [{ id: 'S1' }] };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { ...initial, status: 'running', stage: 'analyzing', review: null, resolved_question: '研究政策与产业链的关联' } });
    }
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs: [initial] } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    assert.equal(h.explore.state.reviewQuestion, '');
    await h.explore.clarify('not-an-option');
    assert.equal(posts.length, 0);
    await h.explore.clarify('o1');
    await h.explore.clarify('o1');
    assert.equal(posts.length, 2);
    assert.deepEqual(Object.keys(posts[0]).sort(), ['idempotency_key', 'option_id', 'revision']);
    assert.equal(posts[0].option_id, 'o1');
    assert.equal(posts[0].idempotency_key, posts[1].idempotency_key);
    assert.equal(h.explore.selectedRun.value.status, 'running');
  } finally { h.close(); }
});

test('confirmation requires selected material IDs and keeps the same key after a lost response', async () => {
  const posts = [];
  const run = { id: 'r1', session_id: 's1', module: 'relation', question: '产业链关联', status: 'awaiting_review', report: null,
    review: { revision: 4, question: '产业链关联' }, materials: [{ id: 'S1' }, { id: 'U1' }] };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push({ url: String(url), body: JSON.parse(options.body) });
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { ...run, status: 'running', stage: 'analyzing', confirmed_sources: ['U1'] } });
    }
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs: [run] } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    assert.deepEqual(h.explore.state.selectedSourceIds, ['S1', 'U1']);
    h.explore.toggleMaterial('S1', false);
    h.explore.toggleMaterial('U1', false);
    await h.explore.confirmMaterials();
    assert.equal(posts.length, 0);
    h.explore.toggleMaterial('U1', true);
    await h.explore.confirmMaterials();
    await h.explore.confirmMaterials();
    assert.equal(posts.length, 2);
    assert.ok(posts.every(item => item.url.endsWith('/runs/r1/confirm')));
    assert.deepEqual(posts[0].body, posts[1].body);
    assert.deepEqual(posts[0].body.source_ids, ['U1']);
    assert.equal(posts[0].body.revision, 4);
    assert.equal(h.explore.selectedRun.value.status, 'running');
  } finally { h.close(); }
});

test('late clarification response cannot overwrite another selected run', async () => {
  let resolveClarify;
  const first = { id: 'r1', session_id: 's1', question: '问题一', status: 'awaiting_clarification', review: { revision: 1, question: '明确问题一' } };
  const second = { id: 'r2', session_id: 's1', question: '问题二', status: 'awaiting_review', review: { revision: 1, question: '问题二' }, materials: [{ id: 'S1' }] };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') return new Promise(resolve => { resolveClarify = resolve; });
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs: [first, second] } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    h.explore.state.reviewQuestion = '更明确的问题一';
    const pending = h.explore.clarify();
    await flush();
    h.explore.selectRun('r2');
    resolveClarify(response({ run: { ...first, status: 'awaiting_review', review: { revision: 2, question: '更明确的问题一' } } }));
    await pending;
    assert.equal(h.explore.selectedRun.value.id, 'r2');
    assert.equal(h.explore.state.reviewQuestion, '问题二');
    assert.equal(h.explore.state.runs[0].status, 'awaiting_clarification');
  } finally { h.close(); }
});

test('pending create keys survive another branch submission and clear only their own signature', async () => {
  const posts = [];
  const runs = [
    { id: 'a', session_id: 's1', module: 'event', question: 'A', status: 'succeeded', report: { id: 'pa' } },
    { id: 'b', session_id: 's1', module: 'event', question: 'B', status: 'succeeded', report: { id: 'pb' } },
  ];
  const h = harness('#explore?session=s1&run=a', async (url, options = {}) => {
    if (options.method === 'POST') {
      const body = JSON.parse(options.body);
      posts.push(body);
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { id: `new-${posts.length}`, session_id: 's1', module: body.module, question: body.question, source_report_id: body.source_report_id, status: 'awaiting_review', review: { revision: 1, interpreted_question: body.question }, materials: [{ id: 'U1' }] } });
    }
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    h.explore.state.draft = '从 A 延伸';
    await h.explore.submit();
    h.explore.selectRun('b');
    h.explore.state.draft = '从 B 延伸';
    await h.explore.submit();
    h.explore.selectRun('a');
    h.explore.state.draft = '从 A 延伸';
    await h.explore.submit();
    assert.equal(posts.length, 3);
    assert.equal(posts[0].source_report_id, 'pa');
    assert.equal(posts[1].source_report_id, 'pb');
    assert.equal(posts[0].idempotency_key, posts[2].idempotency_key);
    assert.notEqual(posts[0].idempotency_key, posts[1].idempotency_key);
    assert.equal(h.store.size, 0);
  } finally { h.close(); }
});

test('a lost create response keeps its key in memory when session storage is unavailable', async () => {
  const posts = [];
  const root = { id: 'a', session_id: 's1', module: 'event', question: 'A', status: 'succeeded', report: { id: 'pa' } };
  const h = harness('#explore?session=s1&run=a', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { id: 'new', session_id: 's1', module: 'event', question: '问题', status: 'awaiting_review' } });
    }
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs: [root] } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    globalThis.sessionStorage.setItem = () => { throw new Error('blocked'); };
    h.explore.state.draft = '问题';
    await h.explore.submit();
    await h.explore.submit();
    assert.equal(posts.length, 2);
    assert.equal(posts[0].idempotency_key, posts[1].idempotency_key);
  } finally { h.close(); }
});

test('opening another session blocks writing through the previous session and source report', async () => {
  const posts = [];
  let resolveSecond;
  const first = { id: 'a', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'pa' } };
  const second = { id: 'b', session_id: 's2', module: 'event', status: 'succeeded', report: { id: 'pb' } };
  const h = harness('#explore?session=s1&run=a', async (url, options = {}) => {
    if (options.method === 'POST') { posts.push(String(url)); return response({ run: {} }); }
    if (String(url).endsWith('/sessions/s2')) return new Promise(resolve => { resolveSecond = resolve; });
    if (String(url).endsWith('/sessions/s1')) return response({ session: { id: 's1', title: 'A' }, runs: [first] });
    return response({ sessions: [{ id: 's1', title: 'A' }, { id: 's2', title: 'B' }] });
  });
  try {
    await h.ready();
    h.explore.state.draft = '新问题';
    const opening = h.explore.openSession('s2');
    assert.equal(h.explore.state.loadingSession, true);
    assert.equal(h.explore.canSubmit.value, false);
    await h.explore.submit();
    assert.deepEqual(posts, []);
    resolveSecond(response({ session: { id: 's2', title: 'B' }, runs: [second] }));
    await opening;
    assert.equal(h.explore.state.session.id, 's2');
    assert.equal(h.explore.state.sourceReportId, 'pb');
    assert.equal(h.explore.canSubmit.value, true);
  } finally { h.close(); }
});

test('a completed report follows the original source only if the next-question source was not changed', async () => {
  const choices = [
    { name: 'unchanged', expected: 'pNew' },
    { name: 'cleared', expected: '' },
    { name: 'another report', expected: 'pAlt' },
  ];
  for (const choice of choices) {
    const original = { id: 'old', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'p0' } };
    const alternate = { id: 'alt', session_id: 's1', module: 'relation', status: 'succeeded', report: { id: 'pAlt' } };
    const running = { id: 'branch', session_id: 's1', module: 'event', question: '继续研究', source_report_id: 'p0', status: 'running', report: null };
    let resolvePoll;
    const polled = new Promise(resolve => { resolvePoll = resolve; });
    const h = harness('#explore?session=s1&run=branch', async rawUrl => {
      const url = String(rawUrl);
      if (url.endsWith('/runs/branch')) {
        resolvePoll();
        return response({ run: { ...running, status: 'succeeded', report: { id: 'pNew' } } });
      }
      if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '研究' }, runs: [original, alternate, running] });
      if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's1', title: '研究' }] });
      return response({ files: [], contexts: {}, context: { mode: 'auto', file_ids: [] } });
    });
    try {
      await h.ready();
      assert.equal(h.explore.state.sourceReportId, 'p0');
      if (choice.name === 'cleared') h.explore.state.sourceReportId = '';
      if (choice.name === 'another report') h.explore.useSuggestion({ question: '下一题', module: 'relation' }, 'pAlt');
      let timeout;
      try {
        await Promise.race([polled, new Promise((_resolve, reject) => { timeout = setTimeout(() => reject(new Error('run poll did not occur')), 4000); })]);
      } finally { clearTimeout(timeout); }
      await flush();
      assert.equal(h.explore.selectedReport.value?.id, 'pNew', choice.name);
      assert.equal(h.explore.state.sourceReportId, choice.expected, choice.name);
      h.explore.selectRun('old');
      assert.equal(h.explore.state.sourceReportId, 'p0', 'selecting an old report still chooses that report');
    } finally { h.close(); }
  }
});

test('an old branch network error is not shown after selecting another report', async () => {
  let rejectPost;
  const runs = [
    { id: 'a', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'pa' } },
    { id: 'b', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'pb' } },
  ];
  const h = harness('#explore?session=s1&run=a', async (url, options = {}) => {
    if (options.method === 'POST') return new Promise((_resolve, reject) => { rejectPost = reject; });
    return response(String(url).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs } : { sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    h.explore.state.draft = 'A 问题';
    const pending = h.explore.submit();
    await flush();
    h.explore.selectRun('b');
    rejectPost(new Error('connection lost'));
    await pending;
    assert.equal(h.explore.selectedRun.value.id, 'b');
    assert.equal(h.explore.state.actionError, '');
  } finally { h.close(); }
});

test('clicking a recommendation creates one branch immediately with its module and preserves a separate draft', async () => {
  const posts = [];
  let resolvePost;
  const report = { id: 'p1', recommendations: [{ module: 'history', question: '  验证这一判断  ', reason: '需要历史材料' }] };
  const root = { id: 'r1', session_id: 's1', module: 'event', status: 'succeeded', report };
  const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      return new Promise(resolve => { resolvePost = resolve; });
    }
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '研究' }, runs: [root] });
    if (url.endsWith('/sessions/s1/context')) return response({ contexts: { history: { mode: 'manual', file_ids: ['f1'] } } });
    if (url.endsWith('/sessions/s1/files')) return response({ files: [{ id: 'f1', name: '上次报告' }] });
    return response({ sessions: [{ id: 's1', title: '研究' }] });
  });
  try {
    await h.ready();
    h.explore.state.module = 'relation';
    assert.equal(h.explore.setModel('GLM-5.3'), false);
    h.explore.state.draft = '我自己还没提交的问题';
    assert.equal(await h.explore.runRecommendation(report.recommendations[0], 'not-p1'), false);
    const pending = h.explore.runRecommendation(report.recommendations[0], 'p1');
    await h.explore.runRecommendation(report.recommendations[0], 'p1');
    assert.equal(posts.length, 1);
    assert.equal(posts[0].module, 'history');
    assert.equal(posts[0].question, '验证这一判断');
    assert.equal(posts[0].source_report_id, 'p1');
    assert.equal(posts[0].model, 'GLM-5.3-Flash');
    assert.equal(posts[0].context_mode, 'manual');
    assert.deepEqual(posts[0].file_ids, ['f1']);
    assert.equal(h.explore.state.draft, '我自己还没提交的问题');
    resolvePost(response({ run: { id: 'r2', session_id: 's1', module: 'history', question: '验证这一判断', source_report_id: 'p1', status: 'awaiting_input' } }));
    await pending;
    assert.equal(h.explore.selectedRun.value.id, 'r2');
    assert.equal(h.explore.state.module, 'history');
    assert.equal(h.explore.state.draft, '我自己还没提交的问题');
  } finally { h.close(); }
});

test('retrying a recommendation after a lost response keeps its original idempotency key', async () => {
  const posts = [];
  const root = { id: 'r1', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'p1' } };
  const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { id: 'r2', session_id: 's1', module: 'relation', question: '关联问题', source_report_id: 'p1', status: 'running' } });
    }
    return response(String(rawUrl).endsWith('/sessions/s1') ? { session: { id: 's1', title: '研究' }, runs: [root] } : { sessions: [{ id: 's1' }] });
  });
  try {
    await h.ready();
    const recommendation = { module: 'relation', question: '关联问题' };
    await h.explore.runRecommendation(recommendation, 'p1');
    assert.match(h.explore.state.actionError, /沿用同一请求标识/);
    await h.explore.runRecommendation(recommendation, 'p1');
    assert.equal(posts.length, 2);
    assert.equal(posts[0].idempotency_key, posts[1].idempotency_key);
    assert.equal(h.explore.selectedRun.value.id, 'r2');
  } finally { h.close(); }
});

test('an active run refreshes newly saved files while running and late files from another session are ignored', async () => {
  const running = { id: 'r1', session_id: 's1', module: 'event', status: 'running', stage: 'searching_web' };
  let resolveLateFiles;
  let oldFileReads = 0;
  const h = harness('#explore?session=s1&run=r1', async rawUrl => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '旧会话' }, runs: [running] });
    if (url.endsWith('/sessions/s2')) return response({ session: { id: 's2', title: '新会话' }, runs: [] });
    if (url.endsWith('/sessions/s1/files')) {
      oldFileReads += 1;
      if (oldFileReads === 1) return response({ files: [] });
      return new Promise(resolve => { resolveLateFiles = resolve; });
    }
    if (url.endsWith('/sessions/s2/files')) return response({ files: [{ id: 's2-f1', name: '新会话材料' }] });
    if (url.endsWith('/runs/r1')) return response({ run: { ...running, stage: 'checking_intent', materials: [{ id: 'S1' }] } });
    return response({ sessions: [{ id: 's1' }, { id: 's2' }] });
  });
  try {
    await h.ready();
    assert.equal(h.explore.state.files.length, 0);
    await new Promise(resolve => setTimeout(resolve, 1700));
    assert.equal(oldFileReads, 2, 'running state poll requests a bounded file-pool refresh');
    assert.equal(typeof resolveLateFiles, 'function');
    await h.explore.openSession('s2');
    assert.deepEqual(h.explore.state.files.map(file => file.id), ['s2-f1']);
    resolveLateFiles(response({ files: [{ id: 's1-f1', name: '迟到的旧材料' }] }));
    await flush();
    assert.deepEqual(h.explore.state.files.map(file => file.id), ['s2-f1']);
  } finally { h.close(); }
});

test('running-state file refresh reveals server material before the report finishes', async () => {
  const running = { id: 'r1', session_id: 's1', module: 'relation', status: 'running', stage: 'searching_web' };
  let fileReads = 0;
  const h = harness('#explore?session=s1&run=r1', async rawUrl => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '研究' }, runs: [running] });
    if (url.endsWith('/sessions/s1/files')) {
      fileReads += 1;
      return response({ files: fileReads === 1 ? [] : [{ id: 'f1', name: '联网搜索摘要', kind: 'material' }] });
    }
    if (url.endsWith('/runs/r1')) return response({ run: { ...running, stage: 'checking_intent', materials: [{ id: 'S1' }] } });
    return response({ sessions: [{ id: 's1' }] });
  });
  try {
    await h.ready();
    await new Promise(resolve => setTimeout(resolve, 1700));
    assert.equal(h.explore.selectedReport.value, null);
    assert.deepEqual(h.explore.state.files.map(file => file.id), ['f1']);
    assert.ok(fileReads <= 2, 'one file GET per state poll');
  } finally { h.close(); }
});

test('the file pool still follows an active session branch while an older report is selected', async () => {
  const earlier = { id: 'r1', session_id: 's1', module: 'event', status: 'succeeded', report: { id: 'p1' } };
  const background = { id: 'r2', session_id: 's1', module: 'history', status: 'running', stage: 'searching_web', source_report_id: 'p1' };
  let fileReads = 0;
  let backgroundReads = 0;
  const h = harness('#explore?session=s1&run=r1', async rawUrl => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '研究' }, runs: [earlier, background] });
    if (url.endsWith('/sessions/s1/files')) {
      fileReads += 1;
      return response({ files: fileReads === 1 ? [] : [{ id: 'f2', name: '后台新材料' }] });
    }
    if (url.endsWith('/runs/r2')) { backgroundReads += 1; return response({ run: { ...background, stage: 'checking_intent' } }); }
    return response({ sessions: [{ id: 's1' }] });
  });
  try {
    await h.ready();
    assert.equal(h.explore.selectedReport.value.id, 'p1');
    await new Promise(resolve => setTimeout(resolve, 2100));
    assert.equal(backgroundReads, 1);
    assert.deepEqual(h.explore.state.files.map(file => file.id), ['f2']);
    assert.equal(h.explore.selectedRun.value.id, 'r1');
    assert.ok(fileReads <= 2, 'background branch adds at most one file GET per status poll');
  } finally { h.close(); }
});

test('saved first event answer restores and a lost feedback reply reuses its key after refresh', async () => {
  const pendingStore = new Map();
  const posts = [];
  const first = { id: 'r1', session_id: 's1', module: 'event', question: '为什么波动？', status: 'awaiting_feedback', stage: 'awaiting_feedback', initial_answer: '预期发生变化。', report: null };
  const fetcher = async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/runs/r1/feedback') && options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { ...first, status: 'queued', stage: 'queued' } });
    }
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '事件' }, runs: [first] });
    if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's1', title: '事件' }] });
    return response({ files: [], contexts: {} });
  };
  const h1 = harness('#explore?session=s1&run=r1', fetcher, { pendingStore });
  try {
    await h1.ready();
    assert.equal(feedbackRun(h1.explore.selectedRun.value), true);
    assert.equal(h1.explore.showComposer.value, false);
    assert.equal(posts.length, 0);
    assert.equal(await h1.explore.chooseFeedback('explain_more'), false);
  } finally { h1.close(); }
  const h2 = harness('#explore?session=s1&run=r1', fetcher, { pendingStore });
  try {
    await h2.ready();
    assert.equal(h2.explore.selectedRun.value.initial_answer, '预期发生变化。');
    assert.equal(posts.length, 1);
    assert.equal(await h2.explore.chooseFeedback('explain_more'), true);
    assert.deepEqual(posts[0], posts[1]);
    assert.equal(h2.explore.selectedRun.value.status, 'queued');
    assert.equal(await h2.explore.chooseFeedback('understood'), false);
  } finally { h2.close(); }
});

test('an understood first answer continues its own memory rather than its ancestor report', async () => {
  for (const ancestor of [null, 'earlier-report']) {
    const writes = [];
    const first = { id: 'event-first', session_id: 's1', module: 'event',
      question: '继续理解事件', source_report_id: ancestor, status: 'succeeded',
      feedback_choice: 'understood', initial_answer: '本轮新增的解释', report: null,
      memory_output: { id: 'memory-first', version: 2 } };
    const fetcher = async (rawUrl, options = {}) => {
      const url = String(rawUrl);
      if (url.endsWith('/sessions/s1/runs') && options.method === 'POST') {
        const body = JSON.parse(options.body);
        writes.push(body);
        return response({ run: { id: 'relation-next', session_id: 's1',
          module: 'relation', status: 'queued', source_run_id: first.id,
          source_report_id: null } });
      }
      if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '记忆接续' }, runs: [first] });
      if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's1', title: '记忆接续' }] });
      return response({ files: [], contexts: {} });
    };
    const h = harness('#explore?session=s1&run=event-first', fetcher);
    try {
      await h.ready();
      assert.equal(h.explore.state.sourceReportId, '');
      h.explore.state.module = 'relation';
      h.explore.state.draft = '这与另一家公司有什么关联？';
      await h.explore.submit();
      assert.equal(writes.length, 1);
      assert.equal(writes[0].source_run_id, first.id);
      assert.equal(writes[0].source_report_id, null);
      assert.deepEqual(runTree([first, { id: 'relation-next', source_run_id: first.id }]).map(item => item.depth), [0, 1]);
    } finally { h.close(); }
  }
});

test('each event angle starts one sourced Explore branch and refreshes its parent choice', async () => {
  const recommendations = [
    { module: 'event', question: '公告究竟改变了什么？', reason: '补全事件' },
    { module: 'relation', question: '上下游会如何受影响？', reason: '追踪主体' },
    { module: 'history', question: '过去何时出现过类似情况？', reason: '核对经验' },
  ];
  for (const item of recommendations) {
    const report = { id: 'p1', module: 'event', report_format: 'event_lite@2', recommendations };
    let parent = { id: 'r1', session_id: 's1', module: 'event', question: '事件问题', status: 'succeeded', next_step_state: 'pending', report };
    const writes = [];
    let parentReads = 0;
    const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
      const url = String(rawUrl);
      if (url.endsWith('/sessions/s1/runs') && options.method === 'POST') {
        writes.push(JSON.parse(options.body));
        parent = { ...parent, next_step_state: 'continued' };
        return response({ run: { id: `child-${item.module}`, session_id: 's1', module: item.module, question: item.question, source_report_id: 'p1', status: 'queued' } });
      }
      if (url.endsWith('/runs/r1')) { parentReads += 1; return response({ run: parent }); }
      if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1', title: '事件' }, runs: [parent] });
      if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's1', title: '事件' }] });
      return response({ files: [], contexts: {} });
    });
    try {
      await h.ready();
      assert.equal(h.explore.canChooseEventRecommendation.value, true);
      assert.equal(h.explore.showComposer.value, false);
      assert.equal(await h.explore.runRecommendation(item, 'p1'), true);
      assert.equal(writes.length, 1);
      assert.equal(writes[0].module, item.module);
      assert.equal(writes[0].question, item.question);
      assert.equal(writes[0].source_report_id, 'p1');
      assert.equal(parentReads, 1);
      h.explore.selectRun('r1');
      assert.equal(h.explore.selectedRun.value.next_step_state, 'continued');
      assert.equal(h.explore.canChooseEventRecommendation.value, false);
      assert.equal(await h.explore.runRecommendation(item, 'p1'), false);
      assert.equal(writes.length, 1);
    } finally { h.close(); }
  }
});

test('relation lite final choice starts only a recommended branch or closes the whole session', async () => {
  const recommendations = [
    { module: 'history', question: '过去类似关系何时成立？' },
    { module: 'relation', question: '另一家供应商如何传导影响？' },
    { module: 'event', question: '公告到底确认了哪些事实？' },
  ];
  const report = { id: 'relation-report', module: 'relation', report_format: 'relation_lite@2', recommendations };
  const original = { id: 'relation-run', session_id: 'session-1', module: 'relation', status: 'succeeded', next_step_state: 'pending', report };
  let parent = original;
  const writes = [];
  const branching = harness('#explore?session=session-1&run=relation-run', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/sessions/session-1/runs') && options.method === 'POST') {
      writes.push(JSON.parse(options.body));
      parent = { ...parent, next_step_state: 'continued' };
      return response({ run: { id: 'child-run', session_id: 'session-1', module: 'history', status: 'queued', source_report_id: report.id } });
    }
    if (url.endsWith('/runs/relation-run')) return response({ run: parent });
    if (url.endsWith('/sessions/session-1')) return response({ session: { id: 'session-1' }, runs: [parent] });
    if (url.endsWith('/sessions')) return response({ sessions: [{ id: 'session-1' }] });
    return response({ files: [], contexts: {} });
  });
  try {
    await branching.ready();
    assert.equal(branching.explore.canChooseLiteRecommendation.value, true);
    assert.equal(branching.explore.showComposer.value, false);
    assert.equal(await branching.explore.runRecommendation({ module: 'history', question: '另一道问题' }, report.id), false);
    assert.equal(await branching.explore.runRecommendation(recommendations[0], report.id), true);
    assert.deepEqual(writes.map(({ module, question, source_report_id }) => ({ module, question, source_report_id })),
      [{ module: 'history', question: recommendations[0].question, source_report_id: report.id }]);
    branching.explore.selectRun(original.id);
    assert.equal(branching.explore.selectedRun.value.next_step_state, 'continued');
    assert.equal(await branching.explore.finishAnalysis(), false);
    assert.equal(await branching.explore.runRecommendation(recommendations[1], report.id), false);
  } finally { branching.close(); }

  let closed = false;
  let finishCalls = 0;
  const ending = harness('#explore?session=session-1&run=relation-run', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (url.endsWith('/runs/relation-run/finish') && options.method === 'POST') {
      finishCalls += 1;
      closed = true;
      return response({ run: { ...original, next_step_state: 'finished' }, session: { id: 'session-1', closed_at: '2026-10-14T10:00:00Z' } });
    }
    if (url.endsWith('/sessions/session-1')) return response({ session: { id: 'session-1', ...(closed ? { closed_at: '2026-10-14T10:00:00Z' } : {}) }, runs: [original] });
    if (url.endsWith('/sessions')) return response({ sessions: [{ id: 'session-1' }] });
    return response({ files: [], contexts: {} });
  });
  try {
    await ending.ready();
    assert.equal(await ending.explore.finishAnalysis(), true);
    assert.equal(ending.explore.sessionClosed.value, true);
    assert.equal(ending.explore.selectedReport.value.id, report.id);
    assert.equal(ending.explore.showComposer.value, false);
    assert.equal(ending.explore.canStartRun.value, false);
    assert.equal(await ending.explore.finishAnalysis(), false);
    assert.equal(await ending.explore.runRecommendation(recommendations[0], report.id), false);
    ending.explore.state.draft = '新问题';
    assert.equal(await ending.explore.submit(), false);
    assert.equal(finishCalls, 1);
  } finally { ending.close(); }
});

test('finish retries with one key and a refreshed closed session cannot start another flow', async () => {
  const pendingStore = new Map();
  const posts = [];
  const report = { id: 'p1', module: 'event', report_format: 'event_lite@2', recommendations: [
    { module: 'event', question: '事件后续是什么？' }, { module: 'relation', question: '哪些主体受到影响？' }, { module: 'history', question: '历史上有何先例？' },
  ] };
  const initial = { id: 'r1', session_id: 's1', module: 'event', status: 'succeeded', next_step_state: 'pending', report };
  const closed = { id: 's1', title: '事件', closed_at: '2026-10-14T09:30:00Z' };
  let finished = false;
  const fetcher = async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (options.method === 'POST') {
      posts.push({ url, body: JSON.parse(options.body) });
      if (url.endsWith('/runs/r1/finish')) {
        if (posts.length === 1) throw new Error('connection lost');
        finished = true;
        return response({ run: { ...initial, next_step_state: 'finished' }, session: closed });
      }
      throw new Error('closed session must not accept another write');
    }
    if (url.endsWith('/sessions/s1')) return response({ session: finished ? closed : { id: 's1', title: '事件' }, runs: [{ ...initial, next_step_state: finished ? 'finished' : 'pending' }] });
    if (url.endsWith('/sessions')) return response({ sessions: [finished ? closed : { id: 's1', title: '事件' }] });
    return response({ files: [], contexts: {} });
  };
  const h1 = harness('#explore?session=s1&run=r1', fetcher, { pendingStore });
  try {
    await h1.ready();
    assert.equal(await h1.explore.finishEvent(), false);
    assert.match(h1.explore.state.actionError, /沿用本次请求/);
  } finally { h1.close(); }
  const h2 = harness('#explore?session=s1&run=r1', fetcher, { pendingStore });
  try {
    await h2.ready();
    assert.equal(await h2.explore.finishEvent(), true);
    assert.deepEqual(posts[0], posts[1]);
    assert.equal(h2.explore.sessionClosed.value, true);
    assert.equal(h2.explore.showComposer.value, false);
    assert.equal(h2.explore.canStartRun.value, false);
    assert.equal(await h2.explore.finishEvent(), false);
    assert.equal(await h2.explore.runRecommendation(report.recommendations[0], 'p1'), false);
    assert.equal(await h2.explore.saveContext(), false);
    h2.explore.state.draft = '绕过只读限制';
    assert.equal(await h2.explore.submit(), false);
    assert.equal(posts.length, 2);
  } finally { h2.close(); }
  const h3 = harness('#explore?session=s1&run=r1', fetcher);
  try {
    await h3.ready();
    assert.equal(h3.explore.sessionClosed.value, true);
    assert.equal(h3.explore.selectedReport.value.id, 'p1');
    assert.equal(h3.explore.showComposer.value, false);
    assert.equal(h3.explore.startRename(), false);
    assert.equal(h3.explore.startDelete(), false);
    h3.explore.newExplore();
    assert.equal(h3.explore.sessionClosed.value, false);
    assert.equal(h3.explore.showComposer.value, true);
  } finally { h3.close(); }
});

test('event feedback and final OK each ignore a second click while the first request is pending', async () => {
  const report = { id: 'p1', module: 'event', report_format: 'event_lite@2', recommendations: [
    { module: 'event', question: '发生了什么？' }, { module: 'relation', question: '关联主体是谁？' }, { module: 'history', question: '过去如何变化？' },
  ] };
  const first = { id: 'r1', session_id: 's1', module: 'event', status: 'awaiting_feedback', initial_answer: '先看预期。' };
  let resolveFeedback;
  let resolveFinish;
  const posts = [];
  const h = harness('#explore?session=s1&run=r1', async (rawUrl, options = {}) => {
    const url = String(rawUrl);
    if (options.method === 'POST') {
      posts.push(url);
      if (url.endsWith('/feedback')) return new Promise(resolve => { resolveFeedback = () => resolve(response({ run: { ...first, status: 'succeeded', next_step_state: 'pending', report } })); });
      if (url.endsWith('/finish')) return new Promise(resolve => { resolveFinish = () => resolve(response({ run: { ...first, status: 'succeeded', next_step_state: 'finished', report }, session: { id: 's1', closed_at: '2026-10-14T10:00:00Z' } })); });
    }
    if (url.endsWith('/sessions/s1')) return response({ session: { id: 's1' }, runs: [first] });
    if (url.endsWith('/sessions')) return response({ sessions: [{ id: 's1' }] });
    return response({ files: [], contexts: {} });
  });
  try {
    await h.ready();
    const feedbackPending = h.explore.chooseFeedback('explain_more');
    assert.equal(await h.explore.chooseFeedback('understood'), false);
    assert.equal(posts.length, 1);
    resolveFeedback();
    assert.equal(await feedbackPending, true);
    const finishPending = h.explore.finishEvent();
    assert.equal(await h.explore.finishEvent(), false);
    assert.equal(posts.length, 2);
    resolveFinish();
    assert.equal(await finishPending, true);
    assert.equal(h.explore.sessionClosed.value, true);
  } finally { h.close(); }
});
