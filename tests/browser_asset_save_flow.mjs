import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:' + (process.env.ASSET_SAVE_BROWSER_PORT || '49009');
const out = process.env.ASSET_SAVE_BROWSER_OUTPUT || 'data/asset-save-flow-browser';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const mutations = [], errors = [], external = [], evidence = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => { if (request.method() !== 'GET') mutations.push({ path: new URL(request.url()).pathname, body: request.url().endsWith('/login') ? { account: 'isolated-fixture' } : request.postDataJSON() }); });
await page.route('**/*', route => { const url = route.request().url(); if (url.startsWith(base + '/') || /^(data:|blob:)/.test(url)) return route.continue(); external.push(url); return route.abort(); });
async function get(path) { const response = await page.request.get(base + '/desktop/api' + path); const value = await response.json(); assert.ok(response.ok(), JSON.stringify(value)); return value; }
async function apps() { await page.getByRole('button', { name: '应用', exact: true }).click(); await page.locator('.asset-authoring').waitFor(); }
async function source(fixture, kind, ids) {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByText(fixture.taskTitle, { exact: true }).first().click();
  const run = page.locator('[data-run-id="' + fixture.sourceRunId + '"]'); await run.waitFor();
  assert.match(await run.textContent(), /任务未全部完成/); assert.match(await run.textContent(), /尚未交付：图表/);
  await run.getByRole('button', { name: kind === 'app' ? '保存为应用' : '沉淀为 ' + (kind === 'tool' ? 'Tool' : 'Skill'), exact: true }).click();
  const picker = page.getByRole('region', { name: '选择实际步骤' }); await picker.waitFor();
  assert.equal(await picker.locator('input:checked').count(), 0);
  assert.match(await picker.textContent(), /原任务未全部完成/);
  for (const id of ids) await picker.getByLabel('保留步骤 ' + id, { exact: true }).check();
  await picker.getByRole('button', { name: '按所选步骤整理草稿', exact: true }).click();
  await page.locator(kind === 'app' ? '.asset-save-toolbar' : '.cap-save-toolbar').waitFor();
  const request = mutations.filter(row => row.path.endsWith('/asset-apps/creator')).at(-1);
  assert.deepEqual(request.body.stepIds, ids);
}
async function savedAsset(kind, name) {
  await page.locator('[aria-label="能力已保存"]').waitFor();
  assert.equal(await page.getByRole('region', { name: '原用例与新输入验证' }).count(), 0);
  const listing = await get('/capabilities/assets?scope=personal&kind=' + kind + '&limit=100');
  const asset = listing.items.find(item => item.name === name); assert.ok(asset); assert.equal(asset.enabled, false);
  const version = (await get('/capabilities/assets/' + asset.assetId + '/versions?scope=personal&limit=100')).items.find(item => item.versionId === asset.latestVersionId);
  assert.equal(version.validationPolicy, 'original_and_new'); assert.notEqual(version.readiness, 'verified');
  let completeSourceFound = false;
  for (const file of version.manifest.files) {
    const response = await page.request.get(base + '/desktop/api/capabilities/assets/' + asset.assetId + '/versions/' + version.versionId + '/files/' + file.path.split('/').map(encodeURIComponent).join('/'));
    assert.ok(response.ok()); const text = await response.text();
    if (text.includes('complete historical source retained')) { assert.ok(text.length > 15000); completeSourceFound = true; }
  }
  assert.ok(completeSourceFound, 'saved package keeps complete long source');
  await page.getByRole('button', { name: '返回我的库', exact: true }).click();
  await page.locator('.cap-card').filter({ hasText: name }).click(); await page.locator('[aria-label="能力详情"]').waitFor();
  evidence.push({ case: kind + ' source steps saved and reopened without execution', assetId: asset.assetId, versionId: version.versionId, fileCount: version.manifest.files.length });
  return version;
}
try {
  const fixture = await (await page.request.get(base + '/__fixture__/state')).json();
  assert.deepEqual(fixture.seedExecutedResults, [{ value: 1 }, { value: 2 }, { value: 3 }]);
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill('save-author@example.invalid'); await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click(); await page.locator('#composer-input').waitFor();
  await apps(); await page.getByRole('button', { name: '新建应用', exact: true }).click();
  await page.locator('.asset-advanced summary').click(); await page.getByLabel('完整应用定义', { exact: true }).fill(JSON.stringify(fixture.definition));
  await page.getByRole('button', { name: '保存应用', exact: true }).click(); await page.locator('[aria-label="应用已保存"]').waitFor();
  assert.equal(await page.getByLabel('验证输入 1', { exact: true }).count(), 0);
  assert.ok(await page.getByRole('button', { name: '启用此版本', exact: true }).isDisabled());
  await page.screenshot({ path: out + '/app-saved.png', fullPage: true });
  const app = (await get('/asset-apps')).items.find(item => item.name === fixture.definition.name);
  assert.equal(app.verification.status, 'unverified'); assert.ok(!app.enabledVersionId);
  assert.deepEqual((await get('/asset-apps/' + app.id + '/runs')).items, []);
  await page.getByRole('button', { name: '返回我的应用', exact: true }).click();
  await page.reload(); await page.locator('.asset-authoring').waitFor(); await apps();
  await page.locator('.asset-library-item').filter({ hasText: fixture.definition.name }).click();
  await page.getByRole('heading', { name: fixture.definition.name, exact: true }).waitFor();
  assert.equal(await page.getByLabel('验证输入 1', { exact: true }).count(), 0);
  evidence.push({ case: 'App direct save, visible confirmation, return and refresh recovery', appId: app.id, versionId: app.versionId });

  await source(fixture, 'app', ['actual-1', 'actual-2']);
  await page.getByLabel('应用名称', { exact: true }).fill('显式保留两步的应用');
  await page.getByRole('button', { name: '保存应用', exact: true }).click(); await page.locator('[aria-label="应用已保存"]').waitFor();
  const captured = (await get('/asset-apps')).items.find(item => item.name === '显式保留两步的应用');
  assert.equal(captured.definition.nodes.length, 2); assert.equal(captured.verification.status, 'unverified');
  await page.getByRole('button', { name: '返回我的应用', exact: true }).click(); await page.locator('.asset-library-item').filter({ hasText: captured.name }).click();
  await page.locator('.asset-node').first().waitFor(); assert.equal(await page.locator('.asset-node').count(), 2);
  evidence.push({ case: 'Partial source explicit multiple steps -> App -> save -> reopen', appId: captured.id });

  for (const [kind, ids] of [['skill', ['actual-1']], ['tool', ['actual-1', 'actual-2']]]) {
    await source(fixture, kind, ids);
    const name = kind === 'skill' ? '单步完整方法' : '多步固定工具';
    await page.locator('.cap-draft-editor').getByLabel('名称', { exact: true }).fill(name);
    assert.ok(await page.locator('.cap-draft-editor textarea').evaluateAll(nodes => nodes.some(node => node.value.length > 15000)), 'full long source is retained');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden');
    const save = page.getByRole('button', { name: '审阅并保存', exact: true }); await save.scrollIntoViewIfNeeded();
    assert.ok(await save.isVisible()); assert.equal(await save.evaluate(node => getComputedStyle(node.parentElement).position), 'sticky');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), '390px no horizontal overflow');
    await page.screenshot({ path: out + '/' + kind + '-save-mobile.png', fullPage: true });
    await save.click(); await page.locator('[aria-label="能力已保存"]').waitFor();
    await page.screenshot({ path: out + '/' + kind + '-saved-mobile.png', fullPage: true });
    await savedAsset(kind, name);
  }
  const final = await (await page.request.get(base + '/__fixture__/state')).json();
  assert.deepEqual(final.executionAttemptsAfterSeed, []);
  const unexpected = mutations.filter(row => /\/verify$|\/verifications|\/validation-suites|\/invocations|\/runs(?:\/|$)|\/enable$/.test(row.path));
  assert.deepEqual(unexpected, []); assert.deepEqual(errors, []); assert.deepEqual(external, []);
  await writeFile(out + '/report.json', JSON.stringify({ passed: true, completedAt: new Date().toISOString(), evidence, mutations, errors, external, executionAttemptsAfterSeed: final.executionAttemptsAfterSeed,
    limitations: ['Synthetic source was executed during fixture setup; no model, real upstream, validation, or asset execution was used by the browser.', 'Production accounts and source records were never mutated.'] }, null, 2));
  console.log(JSON.stringify({ passed: true, scenarios: evidence.length, out }));
} catch (error) {
  await page.screenshot({ path: out + '/failure.png', fullPage: true }).catch(() => {});
  await writeFile(out + '/report.json', JSON.stringify({ passed: false, error: error.stack, evidence, mutations, errors, external }, null, 2));
  throw error;
} finally { await browser.close(); }
