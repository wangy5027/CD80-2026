/** Run against tests/browser_ai_failure_fixture.py after building the backup frontend. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_DIR
  ? path.join(process.env.PLAYWRIGHT_MODULE_DIR, 'playwright') : 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/';
const output = process.env.AI_FAILURE_OUTPUT ? path.resolve(process.env.AI_FAILURE_OUTPUT) : '';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const failure = '您当前帐户的思维额度已消耗完毕，请升级您的账户权限或一段时间后重试';
if (output) await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const tid = id => page.getByTestId(id);
const errors = [];
const result = { fixture: true, realModel: false, realNews: false, cases: [], screenshots: [], pageErrors: [], passed: false };
page.on('pageerror', error => errors.push(error.message));
async function capture(name) {
  if (!output) return;
  const file = path.join(output, `${name}.png`);
  await page.screenshot({ path: file, animations: 'disabled' });
  result.screenshots.push(file);
}

async function runRecord(id) {
  const response = await context.request.get(new URL(`api/explore/runs/${id}`, base).href);
  assert.equal(response.status(), 200);
  return (await response.json()).run;
}
async function submit(module, question) {
  await tid(`explore-module-${module}`).click();
  await tid('explore-input').fill(question);
  const posted = page.waitForResponse(response => response.request().method() === 'POST' &&
    /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
  await tid('explore-submit').click();
  const response = await posted;
  assert.equal(response.status(), 202);
  return (await response.json()).run;
}
async function until(id, statuses) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const run = await runRecord(id);
    if (statuses.includes(run.status)) return run;
    await new Promise(resolve => setTimeout(resolve, 180));
  }
  throw new Error(`Run ${id} did not reach ${statuses.join(', ')}`);
}

try {
  const stats = await context.request.get(new URL('/__ai_failure_fixture__/stats', base).href);
  assert.equal(stats.status(), 200);
  assert.equal((await stats.json()).realModel, false);
  await page.goto(base);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('统一失败文案验收');
  await form.locator('input[name="email"]').fill(`ai-failure-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('isolated-fixture-password-123');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('button', { name: '开始探索', exact: true }).click();

  const empty = await submit('relation', '[WEB_SEARCH_EMPTY] 请分析测试甲集团与测试乙公司之间的关系。');
  const emptyDone = await until(empty.id, ['partial', 'succeeded', 'failed']);
  assert.equal(emptyDone.report?.report_variant, 'insufficient_news');
  await tid('explore-ai-failure').waitFor({ state: 'visible' });
  assert.equal(await tid('explore-ai-failure').locator('p').innerText(), failure);
  assert.equal(await tid('explore-report').count(), 0);
  assert.equal(await page.locator('.explore-report-warning').count(), 0);
  await capture('01-empty-news-failure');
  result.cases.push('partial report with no news shows only the fixed message');
  await page.reload();
  await tid('explore-ai-failure').waitFor({ state: 'visible' });
  assert.equal(await tid('explore-ai-failure').locator('p').innerText(), failure);

  const filesResponse = await context.request.get(new URL(`api/explore/sessions/${empty.session_id}/files`, base).href);
  assert.equal(filesResponse.status(), 200);
  const files = (await filesResponse.json()).files;
  const reportFile = files.find(file => file.kind === 'report' && file.source_report_id === emptyDone.report.id);
  assert.ok(reportFile);
  const rawFileResponse = await context.request.get(new URL(`api/explore/sessions/${empty.session_id}/files/${reportFile.id}`, base).href);
  assert.equal(rawFileResponse.status(), 200);
  assert.notEqual((await rawFileResponse.json()).file.content.trim(), failure);
  await tid('explore-files-float').click();
  await page.locator(`.explore-file-item[data-file-id="${reportFile.id}"]`).getByTestId('explore-file-view').click();
  assert.equal((await page.locator('.explore-file-content').innerText()).trim(), failure);
  await tid('explore-files-close').click();
  assert.equal(await tid('explore-event-finish').isVisible(), true);
  result.cases.push('saved failure and report-file preview show the fixed message without changing stored content');
  await tid('explore-new').click();

  const normal = await submit('relation', '请分析测试甲集团与测试乙公司之间的关系。');
  const normalDone = await until(normal.id, ['partial', 'succeeded', 'failed']);
  assert.ok(normalDone.report && normalDone.report.report_variant !== 'insufficient_news');
  await tid('explore-report').waitFor({ state: 'visible' });
  assert.equal(await tid('explore-ai-failure').count(), 0);
  await capture('02-normal-report');
  result.cases.push('normal report remains readable');

  // A saved answer remains readable if only its model-authored follow-up
  // questions failed. Substitute one GET response in the browser, never the DB.
  const sessionPath = new URL(`api/explore/sessions/${normal.session_id}`, base).pathname;
  const sessionRoute = '**/api/explore/sessions/*';
  await page.route(sessionRoute, async route => {
    if (new URL(route.request().url()).pathname !== sessionPath) return route.continue();
    const response = await route.fetch();
    const data = await response.json();
    const run = data.runs.find(item => item.id === normal.id);
    assert.ok(run?.report);
    run.status = 'partial';
    run.report = {
      ...run.report,
      status: 'partial',
      report_variant: 'recommendations_unavailable',
      completion_warning: '合成内部推荐错误，不得在页面显示。',
      recommendations: [],
      retrieval: { ...run.report.retrieval, recommendation_error: 'fixture_recommendation_failure' },
    };
    await route.fulfill({ response, json: data });
  });
  await page.reload();
  await tid('explore-report').waitFor({ state: 'visible' });
  assert.match(await tid('explore-summary').innerText(), /双方可能形成供应与集成分工/);
  assert.equal((await page.locator('.explore-report-warning').innerText()).trim(), failure);
  assert.equal(await tid('explore-ai-failure').count(), 0);
  assert.equal(await tid('explore-recommendation').count(), 0);
  assert.equal(await tid('explore-event-finish').isVisible(), true);
  assert.match(await page.locator('.explore-next').innerText(), new RegExp(failure));
  assert.ok(!(await page.locator('.explore-document').innerText()).includes('合成内部推荐错误'));
  assert.equal((await runRecord(normal.id)).report.recommendations.length, 3);
  await capture('03-recommendations-unavailable');
  result.cases.push('recommendation-only failure retains report body and shows the fixed warning');
  await page.unroute(sessionRoute);
  await tid('explore-new').click();

  const modelFailure = await submit('relation', '[FAIL_EXPLORE] 请分析测试甲集团与测试乙公司之间的关系。');
  const modelFailureDone = await until(modelFailure.id, ['partial', 'succeeded', 'failed']);
  assert.equal(modelFailureDone.report?.report_variant, 'generation_fallback');
  await tid('explore-ai-failure').waitFor({ state: 'visible' });
  assert.equal((await tid('explore-ai-failure').locator('p').innerText()).trim(), failure);
  assert.equal(await tid('explore-report').count(), 0);
  await capture('04-model-generation-failure');
  result.cases.push('report-generation failure shows only the fixed message');
  await tid('explore-new').click();

  const firstFallback = await submit('event', '[FAIL_INITIAL] 请解释测试甲集团与测试乙公司的合作。');
  await until(firstFallback.id, ['awaiting_feedback', 'partial', 'succeeded', 'failed']);
  await tid('explore-event-initial-answer').waitFor({ state: 'visible' });
  assert.equal((await tid('explore-event-initial-answer').locator('.explore-feedback-answer').innerText()).trim(), failure);
  assert.equal(await tid('explore-event-understood').isVisible(), true);
  assert.equal(await tid('explore-event-explain-more').isVisible(), true);
  await capture('05-first-answer-fallback');
  result.cases.push('first-answer timeout shows fixed message while preserving the two feedback actions');
  await tid('explore-new').click();

  const cancelled = await submit('relation', '[WAIT_EXPLORE] 请分析测试甲集团与测试乙公司之间的关系。');
  await tid('explore-progress').waitFor({ state: 'visible' });
  await tid('explore-cancel').click();
  await until(cancelled.id, ['cancelled']);
  await page.locator('.explore-result-error').getByText('已停止本次分析；迟到的结果不会保存。').waitFor({ state: 'visible' });
  assert.equal(await tid('explore-ai-failure').count(), 0);
  await capture('06-user-cancelled');
  result.cases.push('user cancellation keeps its original status');
  assert.deepEqual(errors, []);
  result.passed = true;
  process.stdout.write('Explore failure presentation: empty report, reload, file preview, recommendation-only failure, normal report, model failure, first-answer timeout and cancellation passed.\n');
} catch (error) {
  result.error = error.stack || String(error);
  process.exitCode = 1;
  await capture('failure').catch(() => {});
} finally {
  result.pageErrors = errors;
  if (output) await writeFile(path.join(output, 'browser-summary.json'), JSON.stringify(result, null, 2));
  await browser.close();
}
