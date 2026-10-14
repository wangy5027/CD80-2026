/** Browser acceptance for the visible history-lite first answer and final choice. */
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
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:49147/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname));
assert.equal(base.pathname, '/desktop/');
const output = path.resolve(process.env.EXPLORE_HISTORY_OUTPUT || path.join(tmpdir(), `cd80-history-browser-${Date.now()}`));
await mkdir(output, { recursive: true });

const result = { fixture: true, realModel: false, realNews: false,
  checks: [], screenshots: [], pageErrors: [], startedAt: new Date().toISOString() };
let browser;
let context;
let page;
let step = 'fixture availability';
const tid = id => page.getByTestId(id);

async function apiGet(pathname) {
  const response = await context.request.get(new URL(pathname, base).href);
  assert.equal(response.status(), 200, pathname);
  return response.json();
}
async function stats() { return apiGet('/__explore_history_fixture__/stats'); }
async function run(id) { return (await apiGet(`/desktop/api/explore/runs/${id}`)).run; }
async function waitFor(label, probe, timeout = 30000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const value = await probe();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 120));
  }
  throw new Error(`Timed out waiting for ${label}`);
}
async function capture(name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, animations: 'disabled' });
  result.screenshots.push(filename);
}
async function check(name, action) {
  step = name;
  const detail = await action();
  result.checks.push({ name, passed: true, ...(detail || {}) });
  console.log(`PASS ${name}`);
}

try {
  const ready = await fetch(new URL('/__explore_history_fixture__/stats', base),
    { signal: AbortSignal.timeout(5000) });
  assert.equal(ready.status, 200, 'Start tests/browser_explore_history.py first.');
  const fixture = await ready.json();
  assert.deepEqual({ fixture: fixture.fixture, realModel: fixture.realModel,
    realNews: fixture.realNews }, { fixture: true, realModel: false, realNews: false });
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 960 } });
  page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => result.pageErrors.push(String(error)));

  let runId;
  let sessionId;
  await check('submit a history question in a disposable account', async () => {
    await page.goto(base.href);
    await page.getByRole('button', { name: '创建账号', exact: true }).click();
    const form = page.locator('#auth-form');
    await form.locator('input[name="name"]').fill('历史验证浏览器验收');
    await form.locator('input[name="email"]').fill(`history-browser-${Date.now()}@example.test`);
    await form.locator('input[name="password"]').fill('synthetic-history-fixture-password');
    await form.getByRole('button', { name: '创建账号', exact: true }).click();
    await page.getByRole('button', { name: '开始探索', exact: true }).click();
    await tid('explore-module-history').click();
    await tid('explore-input').fill('请分析测试甲集团与测试乙公司目前供应合作的历史关联。');
    const submitted = page.waitForResponse(response =>
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
      && response.request().method() === 'POST');
    await tid('explore-submit').click();
    const response = await submitted;
    assert.equal(response.status(), 202);
    const created = (await response.json()).run;
    assert.equal(created.module, 'history');
    runId = created.id;
    sessionId = created.session_id;
    assert.ok(runId && sessionId);
    return { runId, sessionId };
  });

  await check('first answer is visible while the news search is blocked', async () => {
    const waiting = await waitFor('saved first answer and blocked search', async () => {
      const current = await run(runId);
      const fixture = await stats();
      return current.initial_answer && fixture.searchStarted && !fixture.searchReleased
        ? { current, fixture } : null;
    });
    assert.equal(waiting.current.status, 'running');
    assert.equal(waiting.current.phase, 'history_report');
    assert.equal(waiting.current.workflow_version, 'history-lite@1');
    assert.equal(waiting.current.report, null);
    assert.equal(waiting.fixture.initialCalls, 1);
    assert.equal(waiting.fixture.reportCalls, 0);
    assert.equal(waiting.fixture.searchCalls, 1);
    assert.equal(waiting.fixture.searchArguments[0].count, 5);
    await tid('explore-history-initial-answer').waitFor({ state: 'visible', timeout: 12000 });
    assert.match(await tid('explore-history-initial-answer').innerText(), /为什么检索历史关联/);
    assert.match(await tid('explore-history-initial-answer').innerText(), /测试甲集团与测试乙公司/);
    assert.equal(await tid('explore-feedback-options').count(), 0);
    assert.equal(await tid('explore-confirm-judgment').count(), 0);
    assert.equal(await tid('explore-confirm-materials').count(), 0);
    assert.equal(await tid('explore-progress').isVisible(), true);
    const activity = await page.locator('.explore-recent-events').innerText();
    assert.match(activity, /说明历史关联分析目的/);
    assert.match(activity, /已显示初步回复/);
    assert.doesNotMatch(activity, /结合联网信息解释|研究状态已更新/);
    await capture('01-first-answer-during-search');
    return { status: waiting.current.status, phase: waiting.current.phase };
  });

  if (process.env.EXPLORE_HISTORY_PROGRESS_ONLY !== '1') {
  await check('report follows without any user confirmation and preserves the answer', async () => {
    const released = await context.request.post(new URL('/__explore_history_fixture__/release', base).href);
    assert.equal(released.status(), 200);
    const completed = await waitFor('completed history report', async () => {
      const current = await run(runId);
      return ['succeeded', 'partial', 'failed'].includes(current.status) ? current : null;
    });
    assert.equal(completed.status, 'succeeded', completed.error || completed.report?.completion_warning);
    assert.equal(completed.report?.report_format, 'history_lite@1');
    assert.equal(completed.next_step_state, 'pending');
    assert.ok(completed.initial_answer);
    await tid('explore-report').waitFor({ state: 'visible', timeout: 12000 });
    assert.equal(await tid('explore-history-initial-answer').isVisible(), true);
    assert.match(await tid('explore-history-initial-answer').innerText(), /测试甲集团与测试乙公司/);
    assert.equal(await tid('explore-recommendation').count(), 3);
    assert.deepEqual(await tid('explore-recommendation').evaluateAll(nodes => nodes.map(node => node.dataset.module)),
      ['event', 'relation', 'history']);
    assert.equal(await tid('explore-event-finish').isEnabled(), true);
    assert.equal(await tid('explore-event-finish').isVisible(), true);
    assert.equal(await tid('explore-feedback-options').count(), 0);
    await capture('02-report-with-first-answer');
    await tid('explore-event-finish').scrollIntoViewIfNeeded();
    await capture('02b-four-choices');
    return { status: completed.status, reportId: completed.report.id };
  });

  await check('refresh restores the answer, report, and four choices', async () => {
    await page.reload();
    await tid('explore-report').waitFor({ state: 'visible' });
    assert.equal(await tid('explore-history-initial-answer').isVisible(), true);
    assert.equal(await tid('explore-recommendation').count(), 3);
    assert.equal(await tid('explore-event-finish').isEnabled(), true);
    assert.equal((await run(runId)).initial_answer.includes('测试甲集团与测试乙公司'), true);
    await capture('03-after-refresh');
  });

  await check('OK closes the exploration and retains the saved report', async () => {
    await tid('explore-event-finish').click();
    await tid('explore-session-closed').waitFor({ state: 'visible' });
    const saved = await apiGet(`/desktop/api/explore/sessions/${sessionId}`);
    assert.ok(saved.session.closed_at);
    const finished = await run(runId);
    assert.equal(finished.next_step_state, 'finished');
    assert.ok(finished.initial_answer && finished.report);
    await capture('04-after-ok');
  });

  await check('bounded synthetic calls and clean browser', async () => {
    const fixture = await stats();
    assert.equal(fixture.initialCalls, 1);
    assert.equal(fixture.searchCalls, 1);
    assert.equal(fixture.reportCalls, 1);
    assert.ok(fixture.pageCalls <= 5);
    assert.deepEqual(result.pageErrors, []);
    return { initialCalls: fixture.initialCalls, searchCalls: fixture.searchCalls,
      pageCalls: fixture.pageCalls, reportCalls: fixture.reportCalls };
  });
  } else {
    const released = await context.request.post(new URL('/__explore_history_fixture__/release', base).href);
    assert.equal(released.status(), 200);
  }
  result.passed = true;
} catch (error) {
  result.passed = false;
  result.failedStep = step;
  result.error = String(error?.stack || error);
  if (page) await capture('failure').catch(() => {});
  process.exitCode = 1;
} finally {
  result.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'browser-summary.json'), JSON.stringify(result, null, 2));
  if (browser) await browser.close();
  console.log(JSON.stringify(result, null, 2));
}
