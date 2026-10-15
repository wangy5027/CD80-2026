/** Browser acceptance for the built Explore event choice flow. Input arrives via stdin. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const dependencyDir = process.env.PLAYWRIGHT_MODULE_DIR;
const { chromium } = dependencyDir
  ? require(existsSync(path.join(dependencyDir, 'playwright/package.json')) ? path.join(dependencyDir, 'playwright') : dependencyDir)
  : require('playwright');

let stdin = '';
for await (const chunk of process.stdin) stdin += chunk;
const { origin, cookies, output, fixture, questions, maxWaitMs } = JSON.parse(stdin);
assert.equal(new URL(origin).hostname, '127.0.0.1');
assert.equal(questions.length, 2);
await mkdir(output, { recursive: true });

const result = {
  startedAt: new Date().toISOString(), fixture, realModel: !fixture,
  checks: [], runs: [], screenshots: [], pageErrors: [], feedbackPosts: [], ok: false,
};
const save = (name, value) => writeFile(path.join(output, name), JSON.stringify(value, null, 2));
const ids = page => {
  const params = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { sessionId: params.get('session') || '', runId: params.get('run') || '' };
};
async function waitNewRun(page, previous = '') {
  await page.waitForFunction(oldId => {
    const current = new URLSearchParams(location.hash.split('?')[1] || '').get('run');
    return Boolean(current && current !== oldId);
  }, previous, { timeout: 30000 });
  const route = ids(page);
  assert.ok(route.sessionId, 'run route must include the session');
  return route;
}
async function getRun(context, runId) {
  const response = await context.request.get(`${origin}/desktop/api/explore/runs/${encodeURIComponent(runId)}`);
  assert.equal(response.status(), 200, `GET run ${runId}`);
  return (await response.json()).run;
}
async function awaitRun(context, runId, predicate, label) {
  const deadline = Date.now() + maxWaitMs;
  let latest;
  while (Date.now() < deadline) {
    latest = await getRun(context, runId);
    if (predicate(latest)) return latest;
    if (['failed', 'cancelled', 'interrupted'].includes(latest.status))
      throw new Error(`${label} ended ${latest.status}: ${latest.error || 'no detail'}`);
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  throw new Error(`${label} timed out; last status ${latest?.status || 'unknown'}, stage ${latest?.stage || 'unknown'}`);
}
async function stats(context) {
  if (!fixture) return null;
  const response = await context.request.get(`${origin}/__explore_event_choice_fixture__/stats`);
  assert.equal(response.status(), 200);
  return response.json();
}
async function screenshot(page, name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: true, animations: 'disabled' });
  result.screenshots.push(filename);
}
function assertWaiting(run, label) {
  assert.equal(run.status, 'awaiting_feedback', `${label} must wait for a button choice`);
  assert.ok(String(run.initial_answer || '').trim(), `${label} must save its explanation`);
  assert.equal(run.report || null, null, `${label} cannot already have a report`);
  assert.equal((run.materials || []).length, 0, `${label} must not prefetch news`);
}
async function check(label, fn) {
  const started = Date.now();
  const detail = await fn();
  result.checks.push({ label, passed: true, elapsedMs: Date.now() - started, ...(detail || {}) });
}

const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
let context;
let page;
try {
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addCookies(cookies);
  await context.route('**/*', route => {
    const target = new URL(route.request().url());
    return target.origin === origin || ['data:', 'blob:'].includes(target.protocol) ? route.continue() : route.abort();
  });
  page = await context.newPage();
  page.on('pageerror', error => result.pageErrors.push(String(error)));
  page.on('request', request => {
    if (request.method() === 'POST' && /\/desktop\/api\/explore\/runs\/[^/]+\/feedback$/.test(request.url()))
      result.feedbackPosts.push({ runId: request.url().split('/').at(-2), at: new Date().toISOString() });
  });
  await page.goto(`${origin}/desktop/#explore`);
  await page.getByTestId('explore-input').waitFor();

  await check('first answer has exactly two clickable choices and no tool work', async () => {
    await page.getByTestId('explore-input').fill(questions[0]);
    await page.getByTestId('explore-submit').click();
    const route = await waitNewRun(page);
    result.runs.push({ label: 'understood', ...route });
    const run = await awaitRun(context, route.runId, item => item.status === 'awaiting_feedback', 'first answer');
    assertWaiting(run, 'first answer');
    await page.getByTestId('explore-event-initial-answer').waitFor();
    assert.equal(await page.getByTestId('explore-event-understood').count(), 1);
    assert.equal(await page.getByTestId('explore-event-explain-more').count(), 1);
    assert.equal(await page.getByTestId('explore-feedback-options').locator('.explore-feedback-choices > button').count(), 2);
    assert.match(await page.getByTestId('explore-event-understood').innerText(), /我已明白/);
    assert.match(await page.getByTestId('explore-event-explain-more').innerText(), /我需要进一步解释/);
    const before = await stats(context);
    if (fixture) {
      assert.equal(before.initialModelCalls, 1);
      assert.equal(before.reportModelCalls, 0);
      assert.equal(before.searchCalls.length, 0);
      assert.equal(before.fetchCalls, 0);
    }
    await screenshot(page, '01-first-answer-two-choices');
    await save('understood-before-choice.json', run);
    return { runId: route.runId, initialModelCalls: before?.initialModelCalls ?? null };
  });

  await check('refresh restores waiting state; understood finishes without tools', async () => {
    const route = ids(page);
    await page.reload();
    await page.getByTestId('explore-event-understood').waitFor();
    assert.deepEqual(ids(page), route);
    await screenshot(page, '02-waiting-restored');
    await page.getByTestId('explore-event-understood').click();
    const run = await awaitRun(context, route.runId, item => item.status === 'succeeded', 'understood');
    assert.equal(run.feedback_choice, 'understood');
    assert.equal(run.report || null, null);
    assert.equal((run.materials || []).length, 0);
    await page.getByTestId('explore-feedback-complete').waitFor();
    assert.equal(await page.getByTestId('explore-event-understood').count(), 0);
    assert.equal(await page.getByTestId('explore-event-explain-more').count(), 0);
    const after = await stats(context);
    if (fixture) {
      assert.equal(after.initialModelCalls, 1);
      assert.equal(after.reportModelCalls, 0);
      assert.equal(after.searchCalls.length, 0);
      assert.equal(after.fetchCalls, 0);
    }
    await screenshot(page, '03-understood-complete');
    await page.reload();
    await page.getByTestId('explore-feedback-complete').waitFor();
    assert.deepEqual(ids(page), route);
    await save('understood-final.json', run);
    return { toolCalls: after?.searchCalls.length ?? null, report: false };
  });

  await check('further explanation retrieves at most five news items and saves a report', async () => {
    const oldId = ids(page).runId;
    await page.getByTestId('explore-new').click();
    await page.getByTestId('explore-input').fill(questions[1]);
    await page.getByTestId('explore-submit').click();
    const route = await waitNewRun(page, oldId);
    result.runs.push({ label: 'explain_more', ...route });
    let run = await awaitRun(context, route.runId, item => item.status === 'awaiting_feedback', 'second answer');
    assertWaiting(run, 'second answer');
    await page.getByTestId('explore-event-explain-more').waitFor();
    await screenshot(page, '04-second-answer-two-choices');
    await page.getByTestId('explore-event-explain-more').click();
    run = await awaitRun(context, route.runId, item => Boolean(item.report) || ['succeeded', 'partial'].includes(item.status), 'deeper report');
    assert.ok(run.report, 'further explanation must save a report');
    assert.equal(run.feedback_choice, 'explain_more');
    assert.ok(['succeeded', 'partial'].includes(run.status));
    assert.ok((run.materials || []).filter(item => item.kind !== 'user').length <= 5, 'at most five news materials');
    const trace = run.report?.retrieval?.tool_trace || run.retrieval?.tool_trace || [];
    const toolName = item => item.name || item.tool;
    assert.equal(trace.filter(item => toolName(item) === 'web_search').length, 1,
      'saved trace must contain exactly one web search');
    assert.ok(trace.filter(item => toolName(item) === 'fetch_public_page').length <= 5,
      'saved trace must contain at most five public-page reads');
    result.toolTrace = trace.map(item => ({ name: toolName(item), status: item.status,
      arguments: item.arguments, duration_seconds: item.duration_seconds ?? null }));
    await page.getByTestId('explore-report').waitFor();
    assert.equal(await page.getByTestId('explore-event-understood').count(), 0);
    assert.equal(await page.getByTestId('explore-event-explain-more').count(), 0);
    const after = await stats(context);
    if (fixture) {
      assert.equal(after.initialModelCalls, 2);
      assert.equal(after.reportModelCalls, 1);
      assert.equal(after.searchCalls.length, 1, 'deep report should search exactly once');
      assert.equal(after.searchCalls[0].count, 5);
      assert.ok(after.fetchCalls <= 5);
      await save('fixture-stats-final.json', after);
    }
    await screenshot(page, '05-deep-report');
    await page.reload();
    await page.getByTestId('explore-report').waitFor();
    assert.deepEqual(ids(page), route);
    await screenshot(page, '06-report-restored');
    await save('explain-more-final.json', run);
    return { reportId: run.report.id, articleCount: (run.materials || []).filter(item => item.kind !== 'user').length,
      searchCalls: after?.searchCalls.length ?? null, reportModelCalls: after?.reportModelCalls ?? null };
  });

  assert.equal(result.feedbackPosts.length, 2, 'one feedback request per choice');
  assert.equal(result.pageErrors.length, 0, `browser page errors: ${result.pageErrors.join('; ')}`);
  result.ok = true;
} catch (error) {
  result.failure = String(error?.stack || error);
  process.exitCode = 1;
  if (page) try { await screenshot(page, 'failure'); } catch { /* Preserve original error. */ }
} finally {
  if (context) await context.close();
  await browser.close();
  result.finishedAt = new Date().toISOString();
  await save('result.json', result);
  console.log(JSON.stringify({ ok: result.ok, failure: result.failure || null, output,
    checks: result.checks.length, screenshots: result.screenshots.length }));
}
