import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick } from 'vue';
import { createRequire } from 'node:module';
import { activeRun, feedbackRun, reviewRun, runMaterials, confirmedMaterialIds, materialContentLabel, exploreHash, parseExploreHash, runTree, safeSourceUrl, stageLabel, retrievalSearches, searchStatusLabel, searchFreshnessLabel, sourceKindLabel, normalizeContexts, contextRequest, contextFiles } from './model.js';
import { useExplore } from './useExplore.js';

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

test('event feedback is available only for a saved first answer awaiting a choice', () => {
  const run = { module: 'event', status: 'awaiting_feedback', initial_answer: '简明解释' };
  assert.equal(feedbackRun(run), true);
  assert.equal(feedbackRun({ ...run, status: 'succeeded' }), false);
  assert.equal(feedbackRun({ ...run, initial_answer: '' }), false);
  assert.equal(feedbackRun({ ...run, module: 'relation' }), false);
  assert.equal(stageLabel('awaiting_feedback'), '等待反馈');
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

function harness(initialHash, fetchHandler, { preferences = new Map(), ownerId = 'owner-a' } = {}) {
  const old = { fetch: globalThis.fetch, window: globalThis.window, location: globalThis.location, history: globalThis.history, sessionStorage: globalThis.sessionStorage, localStorage: globalThis.localStorage };
  const listeners = new Map();
  const store = new Map();
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

test('saved first answer restores without posting and a lost feedback response reuses its key', async () => {
  const posts = [];
  const initial = { id: 'r1', session_id: 's1', module: 'event', question: '股价为什么波动？',
    status: 'awaiting_feedback', stage: 'awaiting_feedback', initial_answer: '价格会随预期变化。', report: null };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push({ url: String(url), body: JSON.parse(options.body) });
      if (posts.length === 1) throw new Error('connection lost');
      return response({ run: { ...initial, status: 'succeeded', stage: 'completed' } });
    }
    return response(String(url).endsWith('/sessions/s1')
      ? { session: { id: 's1', title: '股价' }, runs: [initial] }
      : String(url).endsWith('/sessions') ? { sessions: [{ id: 's1', title: '股价' }] }
        : { files: [], contexts: {} });
  });
  try {
    await h.ready();
    assert.equal(h.explore.selectedRun.value.initial_answer, '价格会随预期变化。');
    assert.equal(feedbackRun(h.explore.selectedRun.value), true);
    assert.equal(posts.length, 0);
    assert.equal(await h.explore.chooseFeedback('invalid'), false);
    assert.equal(posts.length, 0);
    assert.equal(await h.explore.chooseFeedback('understood'), false);
    assert.match(h.explore.state.actionError, /再次点击同一选项/);
    assert.equal(await h.explore.chooseFeedback('understood'), true);
    assert.equal(posts.length, 2);
    assert.ok(posts.every(item => item.url.endsWith('/runs/r1/feedback')));
    assert.deepEqual(posts[0].body, posts[1].body);
    assert.equal(posts[0].body.choice, 'understood');
    assert.equal(h.explore.selectedRun.value.status, 'succeeded');
    assert.equal(h.explore.selectedRun.value.initial_answer, '价格会随预期变化。');
    assert.equal(await h.explore.chooseFeedback('understood'), false);
    assert.equal(posts.length, 2);
  } finally { h.close(); }
});

test('further explanation resumes the same run and blocks another choice while submitting', async () => {
  let finish;
  const posts = [];
  const initial = { id: 'r1', session_id: 's1', module: 'event', question: '这次异动怎么理解？',
    status: 'awaiting_feedback', initial_answer: '股价变化可能反映预期变化。', report: null };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(JSON.parse(options.body));
      return new Promise(resolve => { finish = () => resolve(response({ run: { ...initial, status: 'queued', stage: 'queued' } })); });
    }
    return response(String(url).endsWith('/sessions/s1')
      ? { session: { id: 's1', title: '异动' }, runs: [initial] }
      : String(url).endsWith('/sessions') ? { sessions: [{ id: 's1', title: '异动' }] }
        : { files: [], contexts: {} });
  });
  try {
    await h.ready();
    const pending = h.explore.chooseFeedback('explain_more');
    assert.equal(h.explore.state.actionBusy, true);
    assert.equal(await h.explore.chooseFeedback('understood'), false);
    assert.equal(posts.length, 1);
    finish();
    assert.equal(await pending, true);
    assert.equal(posts[0].choice, 'explain_more');
    assert.equal(h.explore.selectedRun.value.id, 'r1');
    assert.equal(h.explore.selectedRun.value.status, 'queued');
    assert.equal(h.explore.isActive.value, true);
  } finally { h.close(); }
});

test('cancelling a waiting explanation retains its answer and rejects a later choice', async () => {
  const posts = [];
  const initial = { id: 'r1', session_id: 's1', module: 'event', question: '为什么涨了？',
    status: 'awaiting_feedback', initial_answer: '可能与预期变化有关。', report: null };
  const h = harness('#explore?session=s1&run=r1', async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(String(url));
      return response({ run: { ...initial, status: 'cancelled', stage: 'cancelled' } });
    }
    return response(String(url).endsWith('/sessions/s1')
      ? { session: { id: 's1', title: '股价' }, runs: [initial] }
      : String(url).endsWith('/sessions') ? { sessions: [{ id: 's1', title: '股价' }] }
        : { files: [], contexts: {} });
  });
  try {
    await h.ready();
    await h.explore.cancel();
    assert.deepEqual(posts, ['/desktop/api/explore/runs/r1/cancel']);
    assert.equal(h.explore.selectedRun.value.status, 'cancelled');
    assert.equal(h.explore.selectedRun.value.initial_answer, '可能与预期变化有关。');
    assert.equal(feedbackRun(h.explore.selectedRun.value), false);
    assert.equal(await h.explore.chooseFeedback('explain_more'), false);
    assert.equal(posts.length, 1);
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
    h.explore.state.draft = '我自己还没提交的问题';
    assert.equal(await h.explore.runRecommendation(report.recommendations[0], 'not-p1'), false);
    const pending = h.explore.runRecommendation(report.recommendations[0], 'p1');
    await h.explore.runRecommendation(report.recommendations[0], 'p1');
    assert.equal(posts.length, 1);
    assert.equal(posts[0].module, 'history');
    assert.equal(posts[0].question, '验证这一判断');
    assert.equal(posts[0].source_report_id, 'p1');
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
