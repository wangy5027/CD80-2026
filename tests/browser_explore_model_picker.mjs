/** Isolated UI/API acceptance: synthetic model/news; never uses user data. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_DIR
  ? path.join(process.env.PLAYWRIGHT_MODULE_DIR, 'playwright') : 'playwright');
const base = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48996/desktop/';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname));
const out = process.env.EXPLORE_MODEL_PICKER_OUTPUT;
assert.ok(out, 'Set EXPLORE_MODEL_PICKER_OUTPUT to an isolated output directory.');
await mkdir(out, { recursive: true });
const result = { fixture: true, realModel: false, realNews: false, checks: [], screenshots: [], pageErrors: [] };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
const page = await context.newPage();
page.on('pageerror', error => result.pageErrors.push(error.message));
const tid = id => page.getByTestId(id);
const trigger = () => tid('explore-model-trigger');
async function pick(model) {
  await trigger().click();
  await tid(`explore-model-${model}`).click();
  assert.equal((await trigger().innerText()).includes(model), true);
}
async function capture(name) {
  const filename = path.join(out, `${name}.png`);
  await page.screenshot({ path: filename, animations: 'disabled' });
  result.screenshots.push(filename);
}
async function runRequest(question) {
  await tid('explore-module-relation').click();
  await page.locator('[data-testid="explore-composer"] textarea').fill(question);
  const waiting = page.waitForResponse(response => /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname)
    && response.request().method() === 'POST');
  await tid('explore-submit').click();
  const response = await waiting;
  assert.equal(response.status(), 202);
  return { body: response.request().postDataJSON(), run: (await response.json()).run };
}
async function getRun(id) {
  const response = await context.request.get(new URL(`api/explore/runs/${id}`, base).href);
  assert.equal(response.status(), 200);
  return (await response.json()).run;
}
async function waitRun(id, statuses) {
  const deadline = Date.now() + 12000;
  while (Date.now() < deadline) {
    const run = await getRun(id);
    if (statuses.includes(run.status)) return run;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error(`Run ${id} did not reach ${statuses}`);
}
try {
  const stats = await context.request.get(new URL('/__explore_fixture__/stats', base).href);
  assert.equal((await stats.json()).realModel, false);
  await page.goto(base);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('模型选择验收');
  await form.locator('input[name="email"]').fill(`model-picker-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('isolated-fixture-password-123');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('button', { name: '开始探索', exact: true }).click();
  await trigger().waitFor();
  assert.match(await trigger().innerText(), /GLM-5\.3-Flash/);
  result.checks.push('default Flash');
  await trigger().click();
  assert.equal(await page.getByRole('menuitemradio').count(), 2);
  assert.equal(await tid('explore-model-GLM-5.3-Flash').getAttribute('aria-checked'), 'true');
  await capture('desktop-model-menu');
  await page.keyboard.press('End');
  await page.keyboard.press('Enter');
  assert.equal(await trigger().getAttribute('aria-expanded'), 'false');
  assert.ok(!(await trigger().innerText()).includes('Flash'));
  await trigger().press('ArrowDown');
  await page.keyboard.press('Escape');
  assert.equal(await trigger().getAttribute('aria-expanded'), 'false');
  assert.equal(await trigger().evaluate(el => el === document.activeElement), true);
  await trigger().click();
  await page.locator('[data-testid="explore-composer"] textarea').click();
  assert.equal(await trigger().getAttribute('aria-expanded'), 'false');
  result.checks.push('two choices, selected marker, keyboard, Escape and outside click');
  await page.reload();
  await trigger().waitFor();
  assert.ok(!(await trigger().innerText()).includes('Flash'));
  result.checks.push('preference survives reload');
  await pick('GLM-5.3-Flash');
  await page.locator('.explore-history-close').click();
  for (const width of [320, 375, 390, 430, 690, 768]) {
    await page.setViewportSize({ width, height: 844 });
    await trigger().click();
    if (width === 390) await capture('mobile-model-menu');
    const geometry = await page.evaluate(() => {
      const r = document.querySelector('[data-testid="explore-model-menu"]').getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, width: innerWidth, scrollWidth: document.documentElement.scrollWidth };
    });
    assert.ok(geometry.left >= 0 && geometry.right <= geometry.width && geometry.top >= 0, JSON.stringify(geometry));
    assert.ok(geometry.scrollWidth <= geometry.width + 1, JSON.stringify(geometry));
    await page.keyboard.press('Escape');
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await tid('explore-history-toggle').click();
  result.checks.push('menu fits 320, 375, 390, 430, 690 and 768 px viewports');
  const first = await runRequest('[WAIT_EXPLORE] 请分析测试甲集团拟收购测试乙公司的关联路径。');
  assert.equal(first.body.model, 'GLM-5.3-Flash');
  assert.equal(first.run.model, 'GLM-5.3-Flash');
  await tid('explore-progress').waitFor();
  await pick('GLM-5.3');
  assert.equal((await getRun(first.run.id)).model, 'GLM-5.3-Flash');
  await capture('running-model-next-send');
  result.checks.push('Flash is sent and persisted; changing picker while running keeps current model');
  await tid('explore-cancel').click();
  await waitRun(first.run.id, ['cancelled']);
  await tid('explore-new').click();
  assert.ok(!(await trigger().innerText()).includes('Flash'));
  const second = await runRequest('请分析测试甲集团拟收购测试乙公司的关联路径。');
  assert.equal(second.body.model, 'GLM-5.3');
  assert.equal(second.run.model, 'GLM-5.3');
  const completed = await waitRun(second.run.id, ['succeeded', 'partial', 'failed']);
  // This older synthetic fixture may mark its material coverage partial.
  assert.ok(['succeeded', 'partial'].includes(completed.status));
  assert.equal(completed.model, 'GLM-5.3');
  assert.ok(completed.report);
  const pool = await context.request.get(new URL(`api/explore/sessions/${second.run.session_id}/files`, base).href);
  assert.ok((await pool.json()).files.length > 0);
  result.checks.push('next send uses GLM-5.3 through completed report and file pool');
  assert.deepEqual(result.pageErrors, []);
  result.passed = true;
} catch (error) {
  result.passed = false;
  result.error = error.stack;
  await capture('failure').catch(() => {});
  process.exitCode = 1;
} finally {
  await writeFile(path.join(out, 'browser-summary.json'), JSON.stringify(result, null, 2));
  await browser.close();
  console.log(JSON.stringify(result, null, 2));
}
