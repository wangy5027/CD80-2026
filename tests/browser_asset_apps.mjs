import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:' + (process.env.ASSET_APPS_BROWSER_PORT || '49007');
const out = process.env.ASSET_APPS_BROWSER_OUTPUT || 'data/asset-apps-browser';
await mkdir(out, { recursive: true });
await writeFile(out + '/report.json', JSON.stringify({ passed: false, status: 'running', startedAt: new Date().toISOString() }, null, 2));
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const author = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const reader = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [], external = [], evidence = [], mutationLog = [];
for (const page of [author, reader]) {
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (request.method() !== 'GET' && request.url().includes('/asset-apps')) mutationLog.push({ method: request.method(), path: new URL(request.url()).pathname }); });
  await page.route('**/*', route => { const url = route.request().url(); if (url.startsWith(base + '/') || /^(data:|blob:)/.test(url)) return route.continue(); external.push(url); return route.abort(); });
}
async function login(page, email) {
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill(email);
  await page.locator('input[name=password]').fill('fixture-password');
  const initialized = Promise.all(['/state', '/runtime'].map(path => page.waitForResponse(response => response.url() === base + '/desktop/api' + path && response.ok()).then(response => response.finished())));
  await page.locator('form button[type=submit]').click();
  await initialized;
  await page.locator('#composer-input').waitFor();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  await page.getByRole('button', { name: '应用', exact: true }).click();
  await page.locator('.asset-authoring').waitFor();
}
async function get(page, path) {
  const response = await page.request.get(base + '/desktop/api/asset-apps' + path);
  const value = await response.json(); assert.ok(response.ok(), JSON.stringify(value)); return value;
}
async function narrow(page, name, selector) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal overflow at 390px');
  if (selector) await page.locator(selector).first().scrollIntoViewIfNeeded();
  await page.screenshot({ path: out + '/' + name + '.png', fullPage: true });
  await page.setViewportSize({ width: 1440, height: 1000 });
}
const cases = [
  { label: '原任务合成输入', input: { prices: [100, 120, 90, 108] }, expected: { analysis: { change: 8, drawdown: 25, count: 4 } } },
  { label: '适用的新合成输入', input: { prices: [80, 100, 95, 120] }, expected: { analysis: { change: 50, drawdown: 5, count: 4 } } },
];
try {
  const fixture = await (await author.request.get(base + '/__fixture__/state')).json();
  assert.equal(fixture.synthetic, true); assert.equal(fixture.realModel, false);
  await login(author, 'asset-author@example.invalid');
  assert.equal((await get(author, '')).items.length, 0);
  const legacy = (await get(author, '/legacy')).items;
  assert.ok(legacy.length > 0); assert.ok(legacy.every(item => item.name && ['recipe', 'pipeline'].includes(item.kind) && (item.kind !== 'pipeline' || item.agentRef)));
  assert.equal(mutationLog.length, 0, 'Opening applications observes only');
  await author.getByRole('button', { name: '新建应用', exact: true }).click();
  await author.locator('.asset-advanced summary').click();
  await author.getByLabel('完整应用定义', { exact: true }).fill(JSON.stringify(fixture.definition, null, 2));
  const save = author.getByRole('button', { name: '确认并保存新版本', exact: true });
  assert.ok(await save.isEnabled());
  await save.click();
  await author.getByRole('heading', { name: fixture.definition.name, exact: true }).waitFor();
  const stored = (await get(author, '')).items[0], oldVersion = stored.versionId;
  assert.equal(stored.verification.status, 'unverified'); assert.ok(!stored.enabledVersionId);
  assert.ok(await author.getByRole('button', { name: '启用此版本', exact: true }).isDisabled());
  assert.ok(await author.getByRole('button', { name: '填写新输入运行', exact: true }).isDisabled());
  await author.locator('.asset-definition details').first().locator('summary').click();
  assert.match(await author.locator('.asset-definition').textContent(), /round\(\(prices\[-1\]/);
  await author.screenshot({ path: out + '/saved-source.png', fullPage: true });
  assert.equal(await author.getByLabel('验证输入 1', { exact: true }).count(), 0);
  assert.equal(mutationLog.filter(row => /\/verify$/.test(row.path)).length, 0);
  evidence.push({ case: 'DOM new Creator draft, direct immutable save without testing and full code review', appId: stored.id, versionId: oldVersion });
  await author.getByRole('button', { name: '测试此版本', exact: true }).click();
  for (let index = 0; index < cases.length; index++) {
    await author.getByLabel('验证输入 ' + (index + 1), { exact: true }).fill(JSON.stringify(cases[index].input));
    await author.getByLabel('验证预期 ' + (index + 1), { exact: true }).fill(JSON.stringify({ mode: 'json_equals', value: cases[index].expected }));
  }
  await narrow(author, 'validation-mobile', '.asset-validation-form fieldset');
  await author.getByRole('button', { name: '执行这两组验证', exact: true }).click();
  await author.getByRole('heading', { name: '当前版本验证通过', exact: true }).waitFor({ timeout: 90000 });
  const verified = await get(author, '/' + stored.id);
  assert.equal(verified.verification.status, 'passed');
  for (let index = 0; index < 2; index++) {
    const run = await get(author, '/runs/' + verified.verification.runIds[index]);
    assert.deepEqual(run.result, cases[index].expected);
  }
  await author.getByRole('button', { name: '启用此版本', exact: true }).click();
  await author.getByRole('button', { name: '停用此版本', exact: true }).waitFor();
  const runs = [];
  for (const item of cases) {
    await author.getByRole('button', { name: '填写新输入运行', exact: true }).click();
    await author.locator('.asset-new-run textarea').fill(JSON.stringify(item.input.prices));
    await author.getByRole('button', { name: '按这些输入重新运行', exact: true }).click();
    const paused = author.locator('.asset-run[data-status=awaiting_confirmation]');
    await paused.waitFor({ timeout: 90000 });
    const id = await paused.getAttribute('data-asset-run-id');
    const before = await get(author, '/runs/' + id);
    assert.equal(before.steps[0].status, 'succeeded'); assert.notEqual(before.steps[1]?.status, 'succeeded');
    if (!runs.length) { await paused.locator('.asset-run-waiting').scrollIntoViewIfNeeded(); await author.screenshot({ path: out + '/checkpoint.png', fullPage: true }); await narrow(author, 'checkpoint-mobile', '.asset-run-waiting'); }
    await paused.getByRole('button', { name: '检查完成，继续下一步', exact: true }).click();
    await author.locator('.asset-run[data-asset-run-id="' + id + '"][data-status=succeeded]').waitFor({ timeout: 90000 });
    const run = await get(author, '/runs/' + id);
    assert.deepEqual(run.input, item.input); assert.deepEqual(run.result, item.expected);
    assert.deepEqual(run.steps.map(step => step.status), ['succeeded', 'succeeded', 'succeeded']);
    const downloaded = await author.request.get(base + run.artifacts[0].downloadUrl);
    assert.deepEqual(await downloaded.json(), item.input.prices);
    runs.push(run); evidence.push({ case: item.label + ' fresh run and explicit checkpoint resume', runId: id, result: run.result });
  }
  assert.notEqual(runs[0].id, runs[1].id); assert.notEqual(runs[0].artifacts[0].id, runs[1].artifacts[0].id);
  await author.locator('.asset-run-result').scrollIntoViewIfNeeded();
  await author.screenshot({ path: out + '/completed-run.png', fullPage: true });
  const mutationsBeforeObserve = mutationLog.length;
  await author.getByRole('button', { name: '刷新状态', exact: true }).click();
  await author.locator('.asset-run-row').first().click();
  assert.equal(mutationLog.length, mutationsBeforeObserve, 'Refresh and opening existing records cannot execute');
  await author.getByRole('button', { name: '编辑方法', exact: true }).click();
  await author.getByLabel('用途说明', { exact: true }).fill('已审阅第二版；仍然只适用于合成数据。');
  await author.getByRole('button', { name: '检查并预览变化', exact: true }).click();
  await author.locator('[aria-label="应用变化预览"]').waitFor();
  await author.getByRole('button', { name: '确认并保存新版本', exact: true }).click();
  await author.getByRole('button', { name: '编辑方法', exact: true }).waitFor();
  const revised = await get(author, '/' + stored.id);
  assert.notEqual(revised.versionId, oldVersion); assert.equal(revised.verification.status, 'unverified');
  assert.equal((await get(author, '/' + stored.id + '?versionId=' + oldVersion)).definition.description, fixture.definition.description);
  await author.locator('.asset-version select').selectOption(oldVersion);
  await author.getByRole('button', { name: '停用此版本', exact: true }).waitFor();
  const downloadPromise = author.waitForEvent('download');
  await author.getByRole('link', { name: '导出完整实现', exact: true }).click();
  const download = await downloadPromise, packagePath = out + '/synthetic-app.json'; await download.saveAs(packagePath);
  const exported = JSON.parse(await readFile(packagePath, 'utf8'));
  assert.equal(exported.format, 'cd80.app-package/2');
  await login(reader, 'asset-reader@example.invalid');
  const forbidden = await reader.request.get(base + runs[0].artifacts[0].downloadUrl); assert.equal(forbidden.status(), 404);
  await reader.locator('.asset-authoring input[type=file]').setInputFiles(packagePath);
  await reader.getByRole('button', { name: '导入并保存，不运行', exact: true }).click();
  await reader.getByRole('heading', { name: fixture.definition.name, exact: true }).waitFor();
  const imported = (await get(reader, '')).items[0];
  assert.notEqual(imported.id, stored.id); assert.equal(imported.verification.status, 'unverified'); assert.ok(!imported.enabledVersionId);
  assert.deepEqual((await get(reader, '/' + imported.id + '/runs')).items, []);
  assert.ok(await reader.getByRole('button', { name: '填写新输入运行', exact: true }).isDisabled());
  assert.deepEqual(imported.definition.nodes.map(node => node.code), fixture.definition.nodes.map(node => node.code));
  await reader.locator('.asset-validation').scrollIntoViewIfNeeded();
  await reader.screenshot({ path: out + '/imported-disabled.png', fullPage: true });
  await narrow(reader, 'imported-mobile', '.asset-validation');
  evidence.push({ case: 'immutable version history and complete package imported in another account without runs or enablement', oldVersion, newVersion: revised.versionId, importedId: imported.id });
  // Connection preparation is a DOM-only configuration flow. The fixture rejects every connector invocation.
  await author.locator('.asset-authoring input[type=file]').setInputFiles({ name: 'connection-app.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(fixture.connectionPackage)) });
  const sourceIdentity = 'fixture/asset-app-data';
  const authorConnection = author.getByLabel('连接 · ' + sourceIdentity, { exact: true });
  await authorConnection.waitFor();
  assert.deepEqual(await authorConnection.locator('option').evaluateAll(options => options.map(option => option.value)), ['', fixture.connections.author]);
  assert.equal(await authorConnection.inputValue(), '', 'Import must not select the author connection implicitly');
  await authorConnection.selectOption(fixture.connections.author);
  await author.getByRole('button', { name: '导入并保存，不运行', exact: true }).click();
  await author.getByRole('heading', { name: fixture.connectionPackage.definition.name, exact: true }).waitFor();
  const externalApp = (await get(author, '')).items.find(item => item.name === fixture.connectionPackage.definition.name);
  assert.deepEqual(externalApp.runtimeBindings, { [sourceIdentity]: fixture.connections.author });
  await authorConnection.selectOption('');
  const cleared = author.waitForResponse(response => response.url().endsWith('/' + externalApp.id + '/bindings') && response.request().method() === 'POST');
  await author.getByRole('button', { name: '保存连接配置，重新验证', exact: true }).click();
  assert.equal((await cleared).status(), 200);
  assert.deepEqual((await get(author, '/' + externalApp.id)).runtimeBindings, {});
  await authorConnection.selectOption(fixture.connections.author);
  const rebound = author.waitForResponse(response => response.url().endsWith('/' + externalApp.id + '/bindings') && response.request().method() === 'POST');
  await author.getByRole('button', { name: '保存连接配置，重新验证', exact: true }).click();
  assert.equal((await rebound).status(), 200);
  const configured = await get(author, '/' + externalApp.id);
  assert.deepEqual(configured.runtimeBindings, { [sourceIdentity]: fixture.connections.author });
  assert.equal(configured.verification.status, 'unverified'); assert.ok(!configured.enabledVersionId);
  const externalDownload = author.waitForEvent('download');
  await author.getByRole('link', { name: '导出完整实现', exact: true }).click();
  const externalPath = out + '/connection-app.json'; await (await externalDownload).saveAs(externalPath);
  const externalBytes = await readFile(externalPath, 'utf8'); assert.ok(!externalBytes.includes(fixture.connections.author));
  await reader.locator('.asset-authoring input[type=file]').setInputFiles(externalPath);
  const readerConnection = reader.getByLabel('连接 · ' + sourceIdentity, { exact: true }); await readerConnection.waitFor();
  assert.deepEqual(await readerConnection.locator('option').evaluateAll(options => options.map(option => option.value)), ['', fixture.connections.reader]);
  assert.equal(await readerConnection.inputValue(), '');
  await readerConnection.selectOption(fixture.connections.reader);
  await reader.getByRole('button', { name: '导入并保存，不运行', exact: true }).click();
  await reader.getByRole('heading', { name: fixture.connectionPackage.definition.name, exact: true }).waitFor();
  const reboundReader = (await get(reader, '')).items.find(item => item.name === fixture.connectionPackage.definition.name);
  assert.deepEqual(reboundReader.runtimeBindings, { [sourceIdentity]: fixture.connections.reader });
  assert.deepEqual((await get(reader, '/' + reboundReader.id + '/runs')).items, []);
  await narrow(reader, 'connection-mobile', '.asset-connections');
  evidence.push({ case: 'recipient-only connection choices, explicit import binding, detail reconfiguration, private bindings excluded from export', authorAppId: externalApp.id, readerAppId: reboundReader.id, externalExecution: false });
  const finalFixture = await (await author.request.get(base + '/__fixture__/state')).json();
  assert.deepEqual(finalFixture.modelAttempts, []); assert.deepEqual(finalFixture.externalAttempts, []); assert.deepEqual(errors, []); assert.deepEqual(external, []);
  await writeFile(out + '/report.json', JSON.stringify({ passed: true, status: 'completed', completedAt: new Date().toISOString(), realWasi: true, realModel: false, synthetic: true, evidence, mutations: mutationLog, errors, external }, null, 2));
  console.log('Unified App browser acceptance passed: synthetic inputs, actual WASI, no model or formal data.');
} catch (error) {
  await author.screenshot({ path: out + '/failure.png', fullPage: true }).catch(() => {});
  await writeFile(out + '/report.json', JSON.stringify({ passed: false, status: 'failed', completedAt: new Date().toISOString(), error: error.stack, evidence, mutations: mutationLog, errors, external }, null, 2));
  throw error;
} finally { await browser.close(); }
