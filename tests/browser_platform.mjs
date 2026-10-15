/**
 * UI acceptance against tests/browser_fixture.py, with actual WASI calculation.
 *
 * PLAYWRIGHT_MODULE_DIR may be a node_modules directory or the playwright package.
 * BROWSER_BASE_URL defaults to http://127.0.0.1:48993/desktop/.
 * All business actions use the UI; the sole direct GET is fixture counters.
 * Screenshots, exact downloads and the report go to data/browser-acceptance/.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const playwright = moduleDir
  ? require(existsSync(path.join(moduleDir, 'playwright', 'package.json')) ? path.join(moduleDir, 'playwright') : moduleDir)
  : require('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const output = path.join(root, 'data', 'browser-acceptance', stamp);
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48993/desktop/');
assert.equal(base.protocol, 'http:', 'The fixture must be an HTTP loopback server.');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only the local fixture is allowed.');
await mkdir(output, { recursive: true });

const report = { startedAt: new Date().toISOString(), baseUrl: base.href, realWasi: true, realModel: false, checks: [], pageErrors: [], screenshots: [], downloads: [] };
let browser;
let page;
let currentStep = 'fixture availability';
const primaryName = `sales-first-${Date.now()}.csv`;
const secondName = `sales-second-${Date.now()}.csv`;
const firstCsv = 'category,amount,cost\nA,10,100\nB,7,70\nA,5,50\n';
const secondCsv = 'category,amount\nA,1\nC,4\nC,6\n';

async function stats() {
  const response = await fetch(new URL('/__fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200, 'Start tests/browser_fixture.py before browser acceptance.');
  const value = await response.json();
  assert.equal(value.fixture, true, 'Refusing to run against a production backend.');
  assert.equal(value.realWasi, true);
  assert.equal(value.realModel, false);
  return value;
}

async function waitForStats(predicate, timeout = 3000) {
  const until = Date.now() + timeout;
  let value;
  do {
    value = await stats();
    if (predicate(value)) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  } while (Date.now() < until);
  assert.fail(`Fixture counters did not reach the expected state: ${JSON.stringify(value)}`);
}

async function step(name, action) {
  currentStep = name;
  console.log(`RUN ${name}`);
  const began = Date.now();
  const detail = await action();
  report.checks.push({ name, passed: true, elapsedMs: Date.now() - began, ...(detail || {}) });
  console.log(`PASS ${name}`);
}

async function screenshot(name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: true });
  report.screenshots.push(filename);
}

function cardById(id) {
  assert.match(id, /^[A-Za-z0-9_-]+$/, 'Run ID must be an observed server or rendered identifier.');
  return page.locator(`article.agent-run-card[data-run-id="${id}"]`);
}

async function statusIs(card, desired, timeout = 15000) {
  // Also wake immediately for a failed/cancelled run, rather than hiding failure
  // behind a long success wait. No API substitutes for the rendered state.
  const selector = [...new Set([desired, 'failed', 'cancelled', 'interrupted'])]
    .map(status => `.agent-status.${status}`).join(',');
  await card.locator(selector).waitFor({ state: 'visible', timeout });
  assert.ok((await card.locator('.agent-status').getAttribute('class')).split(/\s+/).includes(desired),
    `Expected ${desired}; rendered card: ${(await card.innerText()).slice(0, 3000)}`);
}

async function newTask() {
  await page.getByRole('button', { name: '新任务', exact: true }).first().click();
  await page.getByLabel('输入消息', { exact: true }).waitFor({ state: 'visible' });
  await page.getByLabel('对话方式', { exact: true }).selectOption('platform');
}

async function attachCsv(name, contents) {
  await page.getByRole('button', { name: '添加附件', exact: true }).click();
  const dialog = page.getByRole('dialog');
  const choosing = page.waitForEvent('filechooser');
  await dialog.getByRole('button', { name: '上传新文件', exact: true }).click();
  await (await choosing).setFiles({ name, mimeType: 'text/csv', buffer: Buffer.from(contents) });
  await dialog.locator('.modal-option').filter({ hasText: name }).waitFor({ state: 'visible' });
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: `移除${name}`, exact: true }).waitFor({ state: 'visible' });
}

async function attachExisting(name) {
  await page.getByRole('button', { name: '添加附件', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await dialog.locator('.modal-option').filter({ hasText: name }).click();
  await dialog.getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: `移除${name}`, exact: true }).waitFor({ state: 'visible' });
}

async function send(message) {
  await page.getByLabel('输入消息', { exact: true }).fill(message);
  await page.getByRole('button', { name: '发送消息', exact: true }).click();
}

async function dynamicRun(message, desired = 'awaiting_confirmation') {
  await newTask();
  await attachExisting(primaryName);
  await send(message);
  const card = page.locator('article.agent-run-card[data-run-id]');
  await card.waitFor({ state: 'visible' });
  assert.equal(await card.count(), 1, 'A new task must show exactly its own run.');
  const id = await card.getAttribute('data-run-id');
  await statusIs(cardById(id), desired);
  return cardById(id);
}

async function downloadCsv(card, name, expected) {
  const downloading = page.waitForEvent('download');
  await card.getByRole('link', { name: /summary\.csv/ }).click();
  const download = await downloading;
  assert.equal(download.suggestedFilename(), 'summary.csv');
  const target = path.join(output, name);
  await download.saveAs(target);
  const actual = await readFile(target);
  assert.equal(actual.toString('utf8'), expected, 'Downloaded CSV must match exactly.');
  report.downloads.push({ path: target, bytes: actual.length, sha256: createHash('sha256').update(actual).digest('hex') });
}

async function parametersText(card) {
  const parameters = card.locator('.agent-parameters');
  if (!(await parameters.isVisible())) await card.getByText('本次参数', { exact: true }).click();
  return parameters.innerText();
}

try {
  report.initialStats = await stats();
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: process.env.BROWSER_HEADLESS !== '0' });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 }, acceptDownloads: true });
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(15000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));

  await step('register through the UI', async () => {
    await page.goto(base.href);
    await page.getByRole('button', { name: '创建账号', exact: true }).click();
    const form = page.locator('#auth-form');
    await form.locator('input[name="name"]').fill('浏览器验收');
    await form.locator('input[name="email"]').fill(`browser-${Date.now()}@example.test`);
    await form.locator('input[name="password"]').fill('browser-fixture-password');
    await form.getByRole('button', { name: '创建账号', exact: true }).click();
    await page.getByRole('button', { name: '应用', exact: true }).waitFor({ state: 'visible' });
  });

  await step('ordinary chat does not execute an application', async () => {
    const before = await stats();
    await send('普通聊天：请解释复利概念。');
    await page.locator('.message.assistant .message-content').filter({ hasText: '这是浏览器验收的普通聊天回复' }).waitFor({ state: 'visible' });
    await page.locator('.stop-bar').waitFor({ state: 'hidden' });
    const after = await stats();
    assert.equal(after.toolCalls, before.toolCalls);
    assert.equal(after.generationCalls, before.generationCalls);
    assert.equal(after.chatCalls, before.chatCalls + 1);
    assert.equal(await page.locator('article.agent-run-card[data-run-id]').count(), 0);
  });

  let firstRunId;
  let savedRef;
  await step('upload and create a preview with zero tool execution', async () => {
    await newTask();
    await attachCsv(primaryName, firstCsv);
    const before = await stats();
    await send('创建 CSV 汇总应用，按 category 分组，对 amount 求和并输出 CSV。');
    const card = page.locator('article.agent-run-card[data-run-id]');
    await card.waitFor({ state: 'visible' });
    firstRunId = await card.getAttribute('data-run-id');
    await statusIs(cardById(firstRunId), 'awaiting_confirmation');
    assert.match(await cardById(firstRunId).innerText(), /尚未运行工具或测试数据/);
    assert.equal((await stats()).toolCalls, before.toolCalls);
    assert.equal((await stats()).generationCalls, before.generationCalls + 1);
    await screenshot('01-preview-before-confirmation');
    return { runId: firstRunId, toolCalls: before.toolCalls };
  });

  await step('refresh preserves the pending confirmation without executing', async () => {
    const before = await stats();
    await page.reload();
    await statusIs(cardById(firstRunId), 'awaiting_confirmation');
    await cardById(firstRunId).getByRole('button', { name: '确认并运行', exact: true }).waitFor({ state: 'visible' });
    assert.equal((await stats()).toolCalls, before.toolCalls);
    assert.equal((await stats()).generationCalls, before.generationCalls);
  });

  await step('confirmation executes real WASI and downloads exact CSV', async () => {
    const before = await stats();
    const card = cardById(firstRunId);
    await card.getByRole('button', { name: '确认并运行', exact: true }).click();
    await statusIs(card, 'succeeded', 20000);
    const result = await card.locator('.agent-result').innerText();
    assert.match(result, /A=15/); assert.match(result, /B=7/); assert.match(result, /总计\s*22/);
    const after = await stats();
    assert.equal(after.toolCalls, before.toolCalls + 1);
    assert.equal(after.toolSuccesses, before.toolSuccesses + 1);
    await downloadCsv(card, 'first-summary.csv', 'category,total_amount\nA,15\nB,7\n');
    await screenshot('02-executed-result');
  });

  let revisedRunId;
  await step('revising the sum column creates a new preview without changing the original', async () => {
    const before = await stats();
    const original = cardById(firstRunId);
    const originalParameters = await parametersText(original);
    const originalResult = await original.locator('.agent-result').innerText();
    const originalVersion = await original.locator('.agent-card-head small').innerText();
    assert.match(originalParameters, /valueColumn\s+amount/);
    await original.getByText('修改需求或参数并生成新版本', { exact: true }).click();
    await original.getByLabel('修改后的目标与规则', { exact: true }).fill('[REVISION_PARAMETERS] 保留 category 分组，将求和列从 amount 改为 cost，输出新的 CSV。');
    const revisionForm = original.locator('.agent-input-form');
    assert.equal(await revisionForm.getByLabel(/求和列/).inputValue(), 'amount');
    await revisionForm.getByLabel(/求和列/).fill('cost');
    // Observe the response to the UI's own click; no API request replaces the UI.
    const creating = page.waitForResponse(response => new URL(response.url()).pathname === '/desktop/api/agent-runs'
      && response.request().method() === 'POST');
    await revisionForm.getByRole('button', { name: '生成修改后的流程', exact: true }).click();
    const response = await creating;
    assert.equal(response.status(), 202);
    const created = await response.json();
    assert.equal(created.revisionOf, firstRunId);
    assert.equal(created.input.valueColumn, 'cost', 'The server must freeze the edited parameter, not copy the previous input.');
    assert.equal(created.input.groupBy, 'category');
    const revised = cardById(created.id);
    await statusIs(revised, 'awaiting_confirmation');
    revisedRunId = await revised.getAttribute('data-run-id');
    assert.equal(revisedRunId, created.id);
    assert.notEqual(revisedRunId, firstRunId);
    assert.match(await parametersText(revised), /valueColumn\s+cost/);
    assert.equal(await parametersText(original), originalParameters);
    assert.equal(await original.locator('.agent-result').innerText(), originalResult);
    assert.equal(await original.locator('.agent-card-head small').innerText(), originalVersion);
    assert.notEqual((await revised.locator('.agent-card-head small').innerText()).split(' · ')[0], originalVersion.split(' · ')[0]);
    assert.equal(await revised.locator('.agent-result').count(), 0);
    assert.equal(await revised.locator('.agent-artifacts').count(), 0);
    const after = await stats();
    assert.equal(after.toolCalls, before.toolCalls);
    assert.equal(after.generationCalls, before.generationCalls + 1);
    await screenshot('02a-revised-parameters-preview');
    return { originalRunId: firstRunId, revisedRunId, originalValueColumn: 'amount', revisedValueColumn: 'cost' };
  });

  await step('confirming the revised parameters calculates cost and preserves the original result', async () => {
    const before = await stats();
    const revised = cardById(revisedRunId);
    await revised.getByRole('button', { name: '确认并运行', exact: true }).click();
    await statusIs(revised, 'succeeded', 20000);
    const result = await revised.locator('.agent-result').innerText();
    assert.match(result, /A=150/); assert.match(result, /B=70/); assert.match(result, /总计\s*220/);
    assert.match(await parametersText(revised), /valueColumn\s+cost/);
    await downloadCsv(revised, 'revised-cost-summary.csv', 'category,total_amount\nA,150\nB,70\n');
    const original = cardById(firstRunId);
    await statusIs(original, 'succeeded');
    assert.match(await parametersText(original), /valueColumn\s+amount/);
    assert.match(await original.locator('.agent-result').innerText(), /总计\s*22(?:\D|$)/);
    await downloadCsv(original, 'original-summary-after-revision.csv', 'category,total_amount\nA,15\nB,7\n');
    const after = await stats();
    assert.equal(after.toolCalls, before.toolCalls + 1);
    assert.equal(after.toolSuccesses, before.toolSuccesses + 1);
    await screenshot('02b-revised-cost-result');
  });

  await step('feedback and explicit save do not execute again', async () => {
    const before = await stats();
    const card = cardById(firstRunId);
    await card.getByText('反馈结果', { exact: true }).click();
    await card.getByPlaceholder('结果是否符合预期？', { exact: true }).fill('结果符合预期，请保留这套可复用的流程。');
    await card.getByRole('button', { name: '提交反馈', exact: true }).click();
    await card.getByRole('button', { name: '保存应用', exact: true }).click();
    await card.locator('.agent-saved').waitFor({ state: 'visible' });
    savedRef = (await card.locator('.agent-saved').innerText()).replace(/^已保存：\s*/, '').trim();
    assert.match(savedRef, /^[A-Za-z0-9_-]+@1$/);
    assert.equal((await stats()).toolCalls, before.toolCalls);
    await page.getByRole('button', { name: '应用', exact: true }).click();
    const personal = page.locator('.agent-catalog-card').filter({ hasText: '浏览器测试 CSV 汇总应用' });
    await personal.waitFor({ state: 'visible' });
    assert.equal(await personal.count(), 1);
    assert.match(await personal.innerText(), /个人应用/);
    return { savedRef };
  });

  await step('saved application requires a new file and reuses the definition', async () => {
    const before = await stats();
    const personal = page.locator('.agent-catalog-card').filter({ hasText: '浏览器测试 CSV 汇总应用' });
    await personal.getByRole('button', { name: '填写参数并运行', exact: true }).click();
    // Revision forms also exist inside historical cards. Scope to the selected
    // catalog entry's section rather than matching hidden historical forms.
    const form = page.locator('section.agent-run-card .agent-input-form');
    const files = form.getByLabel(/输入文件/);
    assert.equal(await files.inputValue(), '', 'Reused applications must not select the previous file.');
    const choosing = page.waitForEvent('filechooser');
    await form.getByRole('button', { name: '上传新文件', exact: true }).click();
    await (await choosing).setFiles({ name: secondName, mimeType: 'text/csv', buffer: Buffer.from(secondCsv) });
    await files.getByRole('option', { name: secondName, exact: true }).waitFor({ state: 'attached' });
    await files.selectOption({ label: secondName });
    await form.getByLabel(/分组列/).fill('category');
    await form.getByLabel(/求和列/).fill('amount');
    const invoking = page.waitForResponse(response => new URL(response.url()).pathname === '/desktop/api/agent-runs'
      && response.request().method() === 'POST');
    await form.getByRole('button', { name: '用这些输入直接运行', exact: true }).click();
    const invokeResponse = await invoking;
    assert.equal(invokeResponse.status(), 202);
    const invoked = await invokeResponse.json();
    const card = cardById(invoked.id);
    await statusIs(card, 'succeeded', 20000);
    assert.equal((await card.locator('.agent-file-list').innerText()).trim(), `输入：${secondName}`);
    assert.equal(await card.getByRole('button', { name: '确认并运行', exact: true }).count(), 0);
    const result = await card.locator('.agent-result').innerText();
    assert.match(result, /A=1/); assert.match(result, /C=10/); assert.match(result, /总计\s*11/);
    const after = await stats();
    assert.equal(after.generationCalls, before.generationCalls);
    assert.equal(after.toolCalls, before.toolCalls + 1);
    await downloadCsv(card, 'second-summary.csv', 'category,total_amount\nA,1\nC,10\n');
    await screenshot('03-personal-application-reused');
  });

  await step('rejecting a new preview prevents all tool execution', async () => {
    const before = await stats();
    const card = await dynamicRun('创建拒绝测试 CSV 应用，按 category 分组对 amount 求和。');
    await card.getByRole('button', { name: '拒绝', exact: true }).click();
    await statusIs(card, 'cancelled');
    assert.equal((await stats()).toolCalls, before.toolCalls);
    assert.equal(await card.locator('.agent-artifacts').count(), 0);
  });

  await step('cancelling generation prevents a usable preview and tools', async () => {
    const before = await stats();
    const card = await dynamicRun('[WAIT_GENERATION] 创建 CSV 应用，按 category 分组对 amount 求和。', 'generating');
    await card.getByRole('button', { name: '停止本次运行', exact: true }).click();
    await statusIs(card, 'cancelled');
    assert.equal((await stats()).toolCalls, before.toolCalls);
    assert.equal(await card.getByRole('button', { name: '确认并运行', exact: true }).count(), 0);
    assert.equal(await card.locator('.agent-artifacts').count(), 0);
  });

  await step('cancelling an executing WASI tool stops later steps', async () => {
    const before = await stats();
    const card = await dynamicRun('[WAIT_TOOL] 创建 CSV 应用，按 category 分组对 amount 求和。');
    assert.equal((await stats()).toolCalls, before.toolCalls);
    await card.getByRole('button', { name: '确认并运行', exact: true }).click();
    await statusIs(card, 'running');
    await waitForStats(value => value.toolCalls === before.toolCalls + 1);
    await card.getByRole('button', { name: '停止本次运行', exact: true }).click();
    await statusIs(card, 'cancelled');
    const after = await stats();
    assert.equal(after.toolSuccesses, before.toolSuccesses);
    assert.equal(after.toolFailures, before.toolFailures + 1);
    assert.equal(after.explanationCalls, before.explanationCalls);
    assert.equal(await card.locator('.agent-artifacts').count(), 0);
    assert.equal(await card.locator('.agent-result').count(), 0);
    await screenshot('04-cancelled-real-wasi-tool');
  });

  assert.deepEqual(report.pageErrors, [], 'Browser JavaScript errors must be resolved.');
  report.finalStats = await stats();
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failedStep = currentStep;
  report.error = String(error?.stack || error);
  if (page) {
    try {
      const snapshot = typeof page.locator('body').ariaSnapshot === 'function'
        ? await page.locator('body').ariaSnapshot() : await page.locator('body').innerText();
      await writeFile(path.join(output, 'failure-body.txt'), snapshot, 'utf8');
      console.error(`BODY SNAPSHOT (${currentStep}):\n${snapshot.slice(0, 18000)}`);
      await screenshot('failure');
    } catch (diagnosticError) {
      report.diagnosticError = String(diagnosticError);
    }
  }
  console.error(report.error);
  process.exitCode = 1;
} finally {
  report.completedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
  if (browser) await browser.close();
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, failedStep: report.failedStep, report: path.join(output, 'report.json') }));
}
