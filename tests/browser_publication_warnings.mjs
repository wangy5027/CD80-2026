import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';

// A single publishing-preview check against the disposable evolution fixture.
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:' + (process.env.EVOLUTION_BROWSER_PORT || '49005');
const out = process.env.EVOLUTION_BROWSER_OUTPUT || 'data/evolution-publication-warnings';
await mkdir(out, { recursive: true });
await writeFile(out + '/report.json', JSON.stringify({ passed: false, status: 'running', startedAt: new Date().toISOString() }, null, 2));
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const post = async (path, data) => {
  const response = await page.request.post(base + '/desktop/api' + path, { data, headers: { 'Idempotency-Key': crypto.randomUUID() } });
  assert.ok(response.ok(), await response.text()); return response.json();
};
try {
  assert.equal((await page.request.get(base + '/__fixture__/state')).status(), 200, 'Only the isolated evolution fixture is supported');
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill('evolve-author@example.invalid');
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click();
  await page.getByRole('button', { name: '应用', exact: true }).waitFor();
  const recipe = await post('/evolution/recipes', { name: '发布提示定向检查', goalTemplate: '解释指标收益率的定义' });
  const run = await post('/evolution/recipes/' + recipe.id + '/run', { input: {} });
  let status;
  for (let attempt = 0; attempt < 100; attempt++) {
    status = (await (await page.request.get(base + '/desktop/api/sub-agent-runs/' + run.id)).json()).status;
    if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(status)) break;
    await page.waitForTimeout(50);
  }
  assert.equal(status, 'succeeded');
  await page.getByRole('button', { name: '应用', exact: true }).click();
  await page.locator('.recipe-card').filter({ hasText: recipe.name }).getByRole('button', { name: '发布', exact: true }).click();
  const publisher = page.locator('.release-publisher');
  const previewResponse = page.waitForResponse(response => response.url().endsWith('/evolution/publications/preview') && response.request().method() === 'POST');
  await publisher.getByRole('button', { name: '检查发布内容', exact: true }).click();
  const preview = await (await previewResponse).json();
  assert.equal(preview.warnings, undefined);
  assert.ok(preview.summary.warnings.length);
  for (const warning of preview.summary.warnings) {
    const visibleWarning = publisher.locator('.release-preview > p').filter({ hasText: warning });
    await visibleWarning.waitFor({ state: 'visible' });
    assert.equal(await visibleWarning.count(), 1);
  }
  assert.equal(await publisher.locator('details').getAttribute('open'), null, 'Warnings must be visible without expanding the JSON manifest');
  assert.ok(await publisher.getByRole('button', { name: '确认发布到共创', exact: true }).isDisabled());
  await publisher.locator('.release-confirm input').check();
  assert.ok(await publisher.getByRole('button', { name: '确认发布到共创', exact: true }).isEnabled());
  await publisher.screenshot({ path: out + '/application-publish-warning.png' });
  assert.deepEqual(errors, []);
  await writeFile(out + '/report.json', JSON.stringify({ passed: true, status: 'completed', completedAt: new Date().toISOString(), realModel: false,
    cases: ['application summary warnings visible before publishing without expanding the manifest'], warnings: preview.summary.warnings,
    confirmationRequired: true, published: false, errors }, null, 2));
  console.log('Application publication warning browser check passed');
} catch (error) {
  await writeFile(out + '/report.json', JSON.stringify({ passed: false, status: 'failed', completedAt: new Date().toISOString(), error: error.message, errors }, null, 2));
  await page.screenshot({ path: out + '/failure.png', fullPage: true });
  throw error;
} finally { await browser.close(); }
