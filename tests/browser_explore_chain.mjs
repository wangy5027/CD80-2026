/** Explore event -> relation -> history browser acceptance. Synthetic by default. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const playwright = moduleDir
  ? require(existsSync(path.join(moduleDir, 'playwright', 'package.json')) ? path.join(moduleDir, 'playwright') : moduleDir)
  : require('playwright');
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:49157/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname));
assert.equal(base.pathname, '/desktop/');
const live = process.env.CHAIN_LIVE === '1';
const output = path.resolve(process.env.EXPLORE_CHAIN_OUTPUT || path.join(tmpdir(), `cd80-explore-chain-${Date.now()}`));
await mkdir(output, { recursive: true });
const eventQuestion = process.env.CHAIN_EVENT_QUESTION || (live
  ? '请解释华为与赛力斯围绕问界汽车合作的最新公开进展，并说明双方可能的分工。'
  : '请解释测试甲集团与测试乙公司近期供应合作发生了什么，以及双方可能的分工。');
const stageTimeout = live ? 120000 : 30000;
const report = { live, checks: [], screenshots: [], pageErrors: [], startedAt: new Date().toISOString() };
let browser;
let context;
let page;
let currentStep = 'fixture audit';
const tid = id => page.getByTestId(id);

async function apiGet(pathname) {
  const response = await context.request.get(new URL(pathname, base).href);
  assert.equal(response.status(), 200, pathname);
  return response.json();
}
async function apiPost(pathname, data, expected = 202) {
  const response = await context.request.post(new URL(pathname, base).href, { data });
  if (!(Array.isArray(expected) ? expected : [expected]).includes(response.status()))
    throw new Error(`${pathname}: expected ${expected}, got ${response.status()}: ${(await response.text()).slice(0, 500)}`);
  return response.json();
}
async function audit() { return apiGet('/__explore_chain__/audit'); }
async function getRun(id) { return (await apiGet(`/desktop/api/explore/runs/${id}`)).run; }
async function getFiles(sessionId) { return (await apiGet(`/desktop/api/explore/sessions/${sessionId}/files`)).files; }
async function waitFor(label, probe, timeout = stageTimeout) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    last = await probe();
    if (last) return last;
    await new Promise(resolve => setTimeout(resolve, live ? 500 : 120));
  }
  throw new Error(`Timed out waiting for ${label}`);
}
async function waitRun(id, predicate, label) {
  return waitFor(label, async () => {
    const run = await getRun(id);
    if (run.status === 'failed' || run.status === 'interrupted')
      throw new Error(`${label}: ${run.status}: ${run.error || 'unknown error'}`);
    return predicate(run) ? run : null;
  });
}
async function capture(name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, animations: 'disabled' });
  report.screenshots.push(filename);
}
async function check(name, action) {
  currentStep = name;
  const started = Date.now();
  const details = await action();
  report.checks.push({ name, passed: true, elapsedMs: Date.now() - started, ...(details || {}) });
  console.log(`PASS ${name}`);
}
function route() {
  const params = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { session: params.get('session'), run: params.get('run') };
}
async function fourChoices() {
  await tid('explore-report').waitFor({ state: 'visible', timeout: stageTimeout });
  await waitFor('three recommendations and OK', async () =>
    await tid('explore-recommendation').count() === 3 && await tid('explore-event-finish').isEnabled());
  const modules = await tid('explore-recommendation').evaluateAll(nodes => nodes.map(node => node.dataset.module));
  assert.deepEqual(modules, ['event', 'relation', 'history']);
  assert.equal(await tid('explore-event-finish').isVisible(), true);
  assert.equal(await page.locator('.explore-error').count(), 0);
}
async function reportReady(runId, module) {
  const run = await waitRun(runId, item =>
    ['succeeded', 'partial'].includes(item.status) && item.report?.id, `${module} final report`);
  assert.equal(run.module, module);
  assert.equal(run.report.module, module);
  assert.equal(run.report.recommendations?.length, 3);
  assert.equal(run.next_step_state, 'pending');
  await fourChoices();
  return run;
}
async function choose(module, parent) {
  // The card itself carries data-module; use that exact card to avoid matching its text.
  const card = page.locator(`[data-testid="explore-recommendation"][data-module="${module}"]`);
  assert.equal(await card.count(), 1);
  const responsePromise = page.waitForResponse(response =>
    /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
    && response.request().method() === 'POST', { timeout: stageTimeout });
  await card.click();
  const response = await responsePromise;
  assert.equal(response.status(), 202);
  const child = (await response.json()).run;
  assert.equal(child.module, module);
  assert.equal(child.source_report_id, parent.report.id);
  assert.equal(child.session_id, parent.session_id);
  await waitFor('selected child route', async () => route().run === child.id);
  const old = await getRun(parent.id);
  assert.equal(old.next_step_state, 'continued');
  return child;
}
async function createMatrixParent(module, tag) {
  const session = (await apiPost('/desktop/api/explore/sessions', { title: `合成选项矩阵 ${module} ${tag}` }, 201)).session;
  const question = ({ event: '测试甲集团与测试乙公司近期供应合作发生了什么？',
    relation: '测试甲集团与测试乙公司目前的供应分工是什么？',
    history: '测试甲集团与测试乙公司历史上的供应合作如何形成？' })[module];
  const created = (await apiPost(`/desktop/api/explore/sessions/${session.id}/runs`, {
    module, question, context_mode: 'auto', file_ids: [], idempotency_key: crypto.randomUUID(),
  })).run;
  if (module === 'event') {
    await waitRun(created.id, item => item.status === 'awaiting_feedback', 'matrix event first answer');
    await apiPost(`/desktop/api/explore/runs/${created.id}/feedback`,
      { choice: 'explain_more', idempotency_key: crypto.randomUUID() });
  }
  const parent = await waitRun(created.id, item =>
    ['succeeded', 'partial'].includes(item.status) && item.report?.id, `matrix ${module} report`);
  assert.equal(parent.report.recommendations?.length, 3);
  assert.equal(parent.next_step_state, 'pending');
  await page.goto(new URL(`#explore?session=${encodeURIComponent(session.id)}&run=${encodeURIComponent(created.id)}`, base).href);
  await fourChoices();
  return parent;
}

try {
  const before = await fetch(new URL('/__explore_chain__/audit', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(before.status, 200, 'The chain audit route must be available.');
  const initialAudit = await before.json();
  assert.equal(initialAudit.realModel, live);
  assert.equal(initialAudit.realNews, live);
  if (!live) assert.equal(initialAudit.fixture, true);
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  page = await context.newPage();
  page.setDefaultTimeout(live ? 30000 : 12000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));

  let event;
  let relation;
  let history;
  let sessionId;
  await check('event first answer and both feedback choices', async () => {
    await page.goto(base.href);
    await page.getByRole('button', { name: '创建账号', exact: true }).click();
    const form = page.locator('#auth-form');
    await form.locator('input[name="name"]').fill('Explore 连续流程验收');
    await form.locator('input[name="email"]').fill(`explore-chain-${Date.now()}@example.test`);
    await form.locator('input[name="password"]').fill('isolated-chain-fixture-password');
    await form.getByRole('button', { name: '创建账号', exact: true }).click();
    await page.getByRole('button', { name: '开始探索', exact: true }).click();
    await tid('explore-module-event').click();
    await tid('explore-input').fill(eventQuestion);
    const responsePromise = page.waitForResponse(response =>
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
      && response.request().method() === 'POST', { timeout: stageTimeout });
    await tid('explore-submit').click();
    const response = await responsePromise;
    assert.equal(response.status(), 202);
    const created = (await response.json()).run;
    sessionId = created.session_id;
    assert.ok(sessionId && created.id);
    event = await waitRun(created.id, item => item.status === 'awaiting_feedback', 'event first answer');
    assert.ok(event.initial_answer);
    await tid('explore-event-initial-answer').waitFor({ state: 'visible', timeout: stageTimeout });
    assert.equal(await tid('explore-event-understood').isVisible(), true);
    assert.equal(await tid('explore-event-explain-more').isVisible(), true);
    await capture('01-event-first-answer');
    assert.equal(await page.locator('.explore-error').count(), 0);
    return { sessionId, runId: event.id };
  });

  await check('event report, file pool, and four choices', async () => {
    const responsePromise = page.waitForResponse(response =>
      /\/api\/explore\/runs\/[^/]+\/feedback$/.test(new URL(response.url()).pathname)
      && response.request().method() === 'POST', { timeout: stageTimeout });
    await tid('explore-event-explain-more').click();
    assert.equal((await responsePromise).status(), 202);
    event = await reportReady(event.id, 'event');
    assert.ok(event.initial_answer);
    const files = await getFiles(sessionId);
    assert.ok(files.some(file => String(file.id) === String(event.report.file_id)));
    assert.ok(files.some(file => String(file.id) === String(event.initial_file_ids?.answer)));
    await capture('02-event-report');
    await tid('explore-recommendation').last().scrollIntoViewIfNeeded();
    await capture('02b-event-four-choices');
    return { reportId: event.report.id, fileCount: files.length };
  });

  await check('relation recommendation keeps session and receives event analysis', async () => {
    const child = await choose('relation', event);
    relation = await reportReady(child.id, 'relation');
    assert.equal(relation.source_report_id, event.report.id);
    assert.equal(relation.session_id, sessionId);
    const contextIds = new Set((relation.context_files || []).map(file => String(file.file_id)));
    assert.ok(contextIds.has(String(event.report.file_id)), 'Relation must include the event report file.');
    const old = await getRun(event.id);
    assert.equal(old.report.id, event.report.id);
    assert.equal(old.initial_answer, event.initial_answer);
    await capture('03-relation-report');
    await tid('explore-recommendation').last().scrollIntoViewIfNeeded();
    await capture('03b-relation-four-choices');
    return { runId: relation.id, reportId: relation.report.id, contextFileCount: contextIds.size };
  });

  await check('history first answer appears while the report is still running', async () => {
    const child = await choose('history', relation);
    const started = await waitRun(child.id, item => Boolean(item.initial_answer), 'history first answer');
    history = started;
    assert.ok(started.initial_answer);
    if (!live) {
      const snapshot = await waitFor('synthetic history search gate', async () => {
        const value = await audit();
        return value.historySearchStarted && !value.historySearchReleased ? value : null;
      });
      assert.equal(snapshot.realModel, false);
      assert.equal(started.status, 'running');
      assert.equal(started.report, null);
    }
    await tid('explore-history-initial-answer').waitFor({ state: 'visible', timeout: stageTimeout });
    assert.equal(await tid('explore-feedback-options').count(), 0);
    assert.equal(await tid('explore-confirm-judgment').count(), 0);
    assert.equal(await page.locator('.explore-error').count(), 0);
    await capture('04-history-first-answer');
    if (!live) await apiPost('/__explore_chain__/gate/release', {}, 200);
    return { runId: child.id, statusWhenFirstAnswerObserved: started.status };
  });

  await check('history report, inherited context, and four choices', async () => {
    history = await reportReady(history.id, 'history');
    assert.equal(history.source_report_id, relation.report.id);
    assert.equal(history.session_id, sessionId);
    assert.ok(history.initial_answer);
    const contextIds = new Set((history.context_files || []).map(file => String(file.file_id)));
    assert.ok(contextIds.has(String(relation.report.file_id)), 'History must include relation report file.');
    assert.equal(await tid('explore-history-initial-answer').isVisible(), true);
    const oldRelation = await getRun(relation.id);
    assert.equal(oldRelation.report.id, relation.report.id);
    const oldEvent = await getRun(event.id);
    assert.equal(oldEvent.report.id, event.report.id);
    assert.equal(oldEvent.initial_answer, event.initial_answer);
    await capture('05-history-report');
    await tid('explore-recommendation').last().scrollIntoViewIfNeeded();
    await capture('05b-history-four-choices');
    return { reportId: history.report.id, contextFileCount: contextIds.size };
  });

  await check('file pool shows saved chain inputs, answers, news, and reports', async () => {
    const files = await getFiles(sessionId);
    assert.ok(files.some(file => String(file.id) === String(event.report.file_id)));
    assert.ok(files.some(file => String(file.id) === String(relation.report.file_id)));
    assert.ok(files.some(file => String(file.id) === String(history.report.file_id)));
    await tid('explore-files-float').click();
    await tid('explore-files-dialog').waitFor({ state: 'visible' });
    assert.ok(await tid('explore-file-list').locator('article').count() >= 3);
    await page.waitForTimeout(350); // Let the drawer transition settle for a readable screenshot.
    await capture('06-file-pool');
    await tid('explore-files-close').click();
    return { fileCount: files.length };
  });

  await check('refresh restores history answer and all previous reports', async () => {
    await page.reload();
    await fourChoices();
    assert.equal(await tid('explore-history-initial-answer').isVisible(), true);
    const session = await apiGet(`/desktop/api/explore/sessions/${sessionId}`);
    const runs = session.runs || [];
    assert.ok(runs.some(run => run.id === event.id && run.report?.id === event.report.id && run.initial_answer));
    assert.ok(runs.some(run => run.id === relation.id && run.report?.id === relation.report.id));
    assert.ok(runs.some(run => run.id === history.id && run.report?.id === history.report.id && run.initial_answer));
    await capture('07-after-refresh');
  });

  await check('OK closes the original chain', async () => {
    await tid('explore-event-finish').click();
    await tid('explore-session-closed').waitFor({ state: 'visible', timeout: stageTimeout });
    assert.ok((await apiGet(`/desktop/api/explore/sessions/${sessionId}`)).session.closed_at);
    assert.equal((await getRun(history.id)).next_step_state, 'finished');
    await capture('08-after-ok');
  });

  if (!live) {
    await check('actual model payloads receive prior analysis across both branches', async () => {
      const snapshot = await audit();
      for (const stage of ['relation_select', 'relation_lite_report', 'history_initial', 'history_lite_report'])
        assert.equal(snapshot.analysisMarkerInModelPayload?.[stage], true, stage);
      assert.equal(snapshot.realModel, false);
      assert.equal(snapshot.realNews, false);
      return { marker: snapshot.analysisMarkerInModelPayload, calls: snapshot.calls.length };
    });

    await check('each report angle creates its module and OK closes an independent session', async () => {
      const matrix = [];
      for (const module of ['event', 'relation', 'history']) {
        for (const choice of ['event', 'relation', 'history', 'ok']) {
          const parent = await createMatrixParent(module, choice);
          if (choice === 'ok') {
            await tid('explore-event-finish').click();
            await tid('explore-session-closed').waitFor({ state: 'visible' });
            assert.ok((await apiGet(`/desktop/api/explore/sessions/${parent.session_id}`)).session.closed_at);
            assert.equal((await getRun(parent.id)).next_step_state, 'finished');
          } else {
            const child = await choose(choice, parent);
            const latest = await getRun(child.id);
            if (['queued', 'running'].includes(latest.status)) {
              await apiPost(`/desktop/api/explore/runs/${child.id}/cancel`, {}, [200, 202]);
              await waitRun(child.id, item => !['queued', 'running'].includes(item.status), 'cancel matrix child');
            }
          }
          matrix.push({ parentModule: module, choice });
        }
      }
      report.matrix = matrix;
      assert.equal(matrix.length, 12);
      return { cells: matrix.length };
    });
  }
  assert.deepEqual(report.pageErrors, []);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failedStep = currentStep;
  report.error = String(error?.stack || error);
  if (page) await capture('failure').catch(() => {});
  process.exitCode = 1;
} finally {
  report.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'browser-summary.json'), JSON.stringify(report, null, 2));
  if (browser) await browser.close();
  console.log(JSON.stringify(report, null, 2));
}
