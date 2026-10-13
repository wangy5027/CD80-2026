/** Real V3 Explore browser smoke. No model, search, news or report stubs. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { mkdir, rename, writeFile } from 'node:fs/promises';
import path from 'node:path';

const require = createRequire(import.meta.url);
const dependencyDir = process.env.PLAYWRIGHT_MODULE_DIR;
const { chromium } = dependencyDir
  ? require(existsSync(path.join(dependencyDir, 'playwright/package.json')) ? path.join(dependencyDir, 'playwright') : dependencyDir)
  : require('playwright');

let input = '';
for await (const chunk of process.stdin) input += chunk;
const { origin, cookies, output } = JSON.parse(input);
assert.equal(new URL(origin).hostname, '127.0.0.1');
await mkdir(output, { recursive: true });

const result = { temporaryAccount: true, realServices: true, build: '', startedAt: new Date().toISOString(),
  runs: [], screenshots: [], errors: [], ok: false };
const save = (name, value) => writeFile(path.join(output, name), JSON.stringify(value, null, 2));
const currentIds = page => {
  const params = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { sessionId: params.get('session') || '', runId: params.get('run') || '' };
};
const screenshot = async (page, name) => {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, animations: 'allow' });
  result.screenshots.push(filename);
};
const waitForNewRun = async (page, previous = '') => {
  await page.waitForFunction(oldId => {
    const runId = new URLSearchParams(location.hash.split('?')[1] || '').get('run');
    return Boolean(runId && runId !== oldId);
  }, previous, { timeout: 35000 });
  return currentIds(page);
};

const browser = await chromium.launch({ channel: 'chrome', headless: true });
let context;
let page;
let video;
try {
  context = await browser.newContext({ viewport: { width: 1440, height: 900 },
    recordVideo: { dir: output, size: { width: 1440, height: 900 } } });
  await context.addCookies(cookies);
  await context.route('**/*', route => {
    const target = new URL(route.request().url());
    return target.origin === origin || ['data:', 'blob:'].includes(target.protocol) ? route.continue() : route.abort();
  });
  page = await context.newPage();
  video = page.video();
  page.on('pageerror', error => result.errors.push(String(error)));
  await page.goto(origin + '/desktop/#explore');
  await page.getByTestId('explore-input').waitFor();
  result.build = await page.locator('script[type="module"]').first().getAttribute('src').catch(() => 'unknown');
  await screenshot(page, '01-entry');

  async function observeRun(runId, label, maxWaitMs) {
    const record = { label, runId, stages: [], selectedClarification: null, materialCountDuringRun: 0 };
    result.runs.push(record);
    const deadline = Date.now() + maxWaitMs;
    let lastStage;
    let run;
    while (Date.now() < deadline) {
      const reply = await context.request.get(`${origin}/desktop/api/explore/runs/${encodeURIComponent(runId)}`);
      assert.equal(reply.status(), 200, `${label} run GET`);
      run = (await reply.json()).run;
      if (run.stage !== lastStage) {
        lastStage = run.stage;
        const item = { stage: run.stage, status: run.status, at: new Date().toISOString() };
        record.stages.push(item);
        console.log(JSON.stringify({ run: label, ...item }));
      }
      const materialCount = Array.isArray(run.materials) ? run.materials.length : 0;
      if (materialCount > record.materialCountDuringRun && !run.report) {
        record.materialCountDuringRun = materialCount;
        if (!record.materialsCaptured) {
          await screenshot(page, `${label}-materials-in-progress`);
          record.materialsCaptured = true;
        }
      }
      if (run.status === 'awaiting_clarification' && !record.selectedClarification) {
        record.selectedClarification = run.review?.options?.[0] || null;
        await screenshot(page, `${label}-clarification`);
        if (!record.selectedClarification) break;
        await page.getByTestId('explore-review-option').first().click();
      }
      if (run.report || ['failed', 'cancelled', 'interrupted', 'awaiting_review', 'awaiting_input'].includes(run.status)) break;
      await page.waitForTimeout(1200);
    }
    assert.ok(run, `${label}: no run returned`);
    record.status = run.status;
    record.stageHistory = run.stage_history || [];
    record.answerStatus = run.report?.answer_status || null;
    record.reportVariant = run.report?.report_variant || null;
    record.sourceReportId = run.source_report_id || null;
    record.sources = (run.report?.sources || []).map(source => ({ id: source.id, kind: source.kind,
      title: source.title, url: source.url || null, content_kind: source.content_kind || null }));
    record.materials = (run.materials || []).map(source => ({ id: source.id, kind: source.kind,
      title: source.title, url: source.url || null, content_kind: source.content_kind || null }));
    record.error = run.error || null;
    await save(`${label}-saved-run.json`, run);
    return run;
  }

  const question = '查找并核对中国人民银行 2024 年 9 月 27 日降准公告，只梳理公告发布日期、调整幅度与生效时间；缺少公告原文就明确限制，不分析资产价格或投资效果。';
  result.question = question;
  await page.getByTestId('explore-input').fill(question);
  await page.getByTestId('explore-submit').click();
  const firstIds = await waitForNewRun(page);
  assert.ok(firstIds.sessionId);
  await screenshot(page, '02-research-start');
  const first = await observeRun(firstIds.runId, 'first', 180000);
  if (!first.report) throw new Error(`First real run ended without a report: ${first.status}${first.error ? ` (${first.error})` : ''}`);
  await page.getByTestId('explore-report').waitFor({ timeout: 20000 });
  await screenshot(page, '03-report');
  result.firstReportId = first.report.id;
  result.externalSources = (first.report.sources || []).filter(source => source.kind !== 'user' && /^https?:\/\//.test(source.url || ''))
    .map(source => ({ id: source.id, kind: source.kind, title: source.title, url: source.url }));
  if (await page.getByTestId('explore-open-materials').count()) {
    await page.getByTestId('explore-open-materials').first().click();
    await page.getByTestId('explore-materials').waitFor();
    await page.waitForTimeout(350);
    result.visibleMaterialLinks = await page.getByTestId('explore-materials').locator('a[href]').evaluateAll(nodes => nodes.map(node => ({ text: node.textContent?.trim(), href: node.href })));
    await screenshot(page, '04-real-sources');
    await page.getByTestId('explore-materials-close').click();
  }
  const beforeReload = currentIds(page);
  await page.reload();
  await page.getByTestId('explore-report').waitFor({ timeout: 20000 });
  assert.deepEqual(currentIds(page), beforeReload, 'refresh restored the same saved report');
  await screenshot(page, '05-refreshed-report');

  await page.getByRole('button', { name: '基于这份报告继续' }).click();
  await page.getByTestId('explore-module-relation').click();
  const branchQuestion = '基于上述公告，核对这次降准安排与同期公开市场操作之间的关系；只陈述公开资料明确披露的时间、措施和主体，缺少来源的机制不下结论。';
  result.branchQuestion = branchQuestion;
  await page.getByTestId('explore-input').fill(branchQuestion);
  await page.getByTestId('explore-source-context').waitFor();
  await screenshot(page, '06-branch-draft');
  await page.getByTestId('explore-submit').click();
  const branchIds = await waitForNewRun(page, firstIds.runId);
  assert.equal(branchIds.sessionId, firstIds.sessionId);
  const branch = await observeRun(branchIds.runId, 'branch', 180000);
  assert.equal(branch.source_report_id, first.report.id, 'branch must use the chosen report');
  result.branchStarted = true;
  if (branch.report) {
    await page.getByTestId('explore-report').waitFor({ timeout: 20000 });
    await screenshot(page, '07-branch-report');
  } else {
    await screenshot(page, '07-branch-final-state');
  }
  result.ok = Boolean(result.externalSources.length && result.branchStarted && !result.errors.length && branch.report);
  if (!result.externalSources.length) result.failure = 'First report had no linked external sources.';
  else if (!branch.report) result.failure = `Branch started but did not finish with a report: ${branch.status}`;
  else if (result.errors.length) result.failure = 'Browser JavaScript errors occurred.';
} catch (error) {
  result.failure = String(error);
  process.exitCode = 1;
  if (page) {
    try { await screenshot(page, 'failure'); } catch { /* Browser may already be closed. */ }
    try {
      const openMaterials = page.getByTestId('explore-open-materials').first();
      if (await openMaterials.isVisible()) {
        await openMaterials.click();
        await page.getByTestId('explore-materials').waitFor();
        await page.waitForTimeout(350);
        result.failureMaterialLinks = await page.getByTestId('explore-materials').locator('a[href]')
          .evaluateAll(nodes => nodes.map(node => ({ text: node.textContent?.trim(), href: node.href })));
        await screenshot(page, 'failure-materials');
      }
    } catch { /* Preserve the original failure if the UI is no longer available. */ }
  }
} finally {
  if (context) await context.close();
  await browser.close();
  if (video) {
    try {
      const filename = path.join(output, 'real-v3-full.webm');
      await rename(await video.path(), filename);
      result.video = filename;
    } catch (error) { result.errors.push(`Video finalization failed: ${error}`); }
  }
  if (!result.ok) process.exitCode = 1;
  result.finishedAt = new Date().toISOString();
  await save('result.json', result);
  console.log(JSON.stringify({ ok: result.ok, failure: result.failure || null, output,
    firstStatus: result.runs[0]?.status, branchStatus: result.runs[1]?.status,
    externalSources: result.externalSources?.length || 0 }));
}
