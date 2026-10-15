/** Account-specific UI acceptance; requires account_financial_fixture.py. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const accountEmail = (process.env.ACCOUNT_TEST_EMAIL || '').trim();
assert.ok(accountEmail, '请设置 ACCOUNT_TEST_EMAIL 为待核验账号邮箱后再运行。');

const require = createRequire(import.meta.url);
const { chromium } = require(path.join(process.env.PLAYWRIGHT_MODULE_DIR, 'playwright'));
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48996/desktop/');
assert.equal(base.protocol, 'http:', 'Use the isolated account fixture over loopback HTTP.');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only a loopback fixture is allowed.');
const output = path.resolve(process.env.ACCOUNT_TEST_OUTPUT || path.join(root, 'docs/sqlite-sync-qa/account-crud'));
await mkdir(output, { recursive: true });
const report = { startedAt: new Date().toISOString(), isolatedDatabase: true, sameAccountIdentity: true,
  realPasswordTested: false, checks: [], pageErrors: [], writes: [], screenshots: [], blockedExternal: [] };
async function stats() {
  const response = await fetch(new URL('/__financial_fixture__/stats', base));
  assert.equal(response.status, 200, 'A disposable fixture is required.');
  const result = await response.json();
  assert.equal(result.fixture, true);
  assert.equal(result.temporaryData, true);
  assert.equal(result.realModel, false);
  assert.equal(result.identityMatchesSource, true);
  assert.equal(result.canWriteFromConfiguredAllowlist, true);
  assert.equal(result.accountEmail, accountEmail);
  return result;
}
report.initial = await stats();
assert.equal(report.initial.auditActions.length, 0, 'Use a fresh temporary database.');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
await context.route('**/*', route => {
  const url = new URL(route.request().url());
  if (url.origin === base.origin || ['data:', 'blob:'].includes(url.protocol)) return route.continue();
  report.blockedExternal.push({ origin: url.origin, method: route.request().method() });
  return route.abort();
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
page.on('pageerror', error => report.pageErrors.push(String(error)));
const panel = page.locator('.financial-data-page');
const dialog = page.getByRole('dialog');
const table = 'cd80_stock_basic';
const rowsPath = `/desktop/api/financial-data/tables/${table}/rows`;
const testCode = '000000.ACCOUNT_TEST';
let rowId;
let currentStep;
async function step(name, action) {
  currentStep = name;
  console.log('RUN', name);
  await action();
  report.checks.push({ name, passed: true });
  console.log('PASS', name);
}
async function screenshot(name) {
  await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
  report.screenshots.push(`${name}.png`);
}
async function setValue(field, value, mode = 'value') {
  await dialog.getByLabel(`${field} 填写方式`, { exact: true }).selectOption(mode);
  if (mode === 'value') await dialog.getByLabel(`${field} 字段值`, { exact: true }).fill(value);
}
function pair(field) {
  return dialog.locator('.detail-pair').filter({ has: page.locator('dt', { hasText: new RegExp(`^${field}\\s`) }) }).locator('dd');
}
async function captureWrite(method, pathname, button, expectedStatus) {
  const pending = page.waitForResponse(r => new URL(r.url()).pathname === pathname && r.request().method() === method);
  await dialog.getByRole('button', { name: button, exact: true }).click();
  const response = await pending;
  assert.equal(response.status(), expectedStatus);
  report.writes.push({ method, path: pathname, status: response.status(), origin: new URL(response.url()).origin });
  return expectedStatus === 204 ? null : response.json();
}
async function showTestRow() {
  await page.reload();
  await panel.getByLabel('选择数据表').waitFor();
  await panel.getByLabel('选择数据表').selectOption(table);
  await panel.locator('.records-table tbody tr').first().waitFor();
  await panel.getByRole('button', { name: '添加筛选条件', exact: true }).click();
  const filter = panel.locator('.filter-row').last();
  await filter.getByRole('combobox').nth(0).selectOption('ts_code');
  await filter.getByRole('combobox').nth(1).selectOption('eq');
  await filter.getByRole('textbox').fill(testCode);
  const response = page.waitForResponse(r => r.url().endsWith(`/${table}/query`) && r.request().method() === 'POST');
  await panel.getByRole('button', { name: '应用筛选', exact: true }).click();
  assert.equal((await response).status(), 200);
}

try {
  await step('账号登录及页面管理员权限', async () => {
    await page.goto(base.href);
    await page.locator('input[name="email"]').fill(report.initial.accountEmail);
    await page.locator('input[name="password"]').fill('fixture-password-123');
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.getByRole('button', { name: '数据管理', exact: true }).click();
    await panel.locator('.records-table tbody tr').first().waitFor();
    assert.equal(await panel.getByLabel('选择数据表').locator('option').count(), 37);
    assert.equal(await panel.locator('.access-label').innerText(), '官方表可编辑');
    assert.equal(await panel.getByRole('button', { name: '新增记录', exact: true }).isEnabled(), true);
    const capability = await context.request.get(new URL('/desktop/api/financial-data/capabilities', base).href);
    assert.equal(capability.status(), 200);
    report.capabilities = await capability.json();
    assert.equal(report.capabilities.canWrite, true);
    await panel.getByLabel('选择数据表').selectOption(table);
    await panel.getByRole('button', { name: '查看记录 #1', exact: true }).waitFor();
    await screenshot('01-account-editable');
  });
  await step('页面新增记录，刷新并筛选确认已保存', async () => {
    await panel.getByRole('button', { name: '新增记录', exact: true }).click();
    await setValue('ts_code', testCode);
    await setValue('symbol', '000000');
    await setValue('name', '账号权限测试_新增');
    await setValue('area', '');
    await setValue('industry', '', 'null');
    const created = await captureWrite('POST', rowsPath, '保存新增', 201);
    rowId = created.data._row_id;
    assert.ok(Number.isSafeInteger(rowId));
    report.testRowId = rowId;
    await dialog.getByRole('button', { name: '编辑记录', exact: true }).waitFor();
    assert.equal(await pair('symbol').innerText(), '000000');
    assert.equal(await pair('area').innerText(), '空字符串');
    assert.equal(await pair('industry').innerText(), 'NULL');
    await screenshot('02-created');
    await showTestRow();
    const row = panel.getByRole('button', { name: `查看记录 #${rowId}`, exact: true });
    await row.waitFor();
    assert.equal(await panel.locator('.records-table tbody tr').count(), 1);
    await row.click();
    await dialog.getByRole('button', { name: '编辑记录', exact: true }).waitFor();
    assert.equal(await pair('name').innerText(), '账号权限测试_新增');
  });
  await step('页面修改自己新增的记录，刷新确认已保存', async () => {
    await dialog.getByRole('button', { name: '编辑记录', exact: true }).click();
    await setValue('name', '账号权限测试_已修改');
    await setValue('area', '隔离测试库');
    const updated = await captureWrite('PATCH', `${rowsPath}/${rowId}`, '保存修改', 200);
    assert.equal(updated.data._row_id, rowId);
    assert.equal(updated.data.name, '账号权限测试_已修改');
    await showTestRow();
    await panel.getByRole('button', { name: `查看记录 #${rowId}`, exact: true }).click();
    await dialog.getByRole('button', { name: '编辑记录', exact: true }).waitFor();
    assert.equal(await pair('name').innerText(), '账号权限测试_已修改');
    assert.equal(await pair('area').innerText(), '隔离测试库');
    assert.equal(await pair('symbol').innerText(), '000000');
    await screenshot('03-updated-after-refresh');
  });
  await step('页面确认删除测试记录，刷新后查无此记录', async () => {
    await dialog.getByRole('button', { name: '删除记录', exact: true }).click();
    await screenshot('04-delete-confirmation');
    await captureWrite('DELETE', `${rowsPath}/${rowId}`, '确认删除', 204);
    await dialog.waitFor({ state: 'hidden' });
    await panel.getByText('没有符合条件的记录。', { exact: true }).waitFor();
    await showTestRow();
    await panel.getByText('没有符合条件的记录。', { exact: true }).waitFor();
    const missing = await context.request.get(new URL(`${rowsPath}/${rowId}`, base).href);
    assert.equal(missing.status(), 404);
    report.deletedRecordReadStatus = missing.status();
    await screenshot('05-deleted-after-refresh');
  });
  await step('临时库行数恢复，仅测试记录产生三次审计操作', async () => {
    report.final = await stats();
    assert.deepEqual(report.final.rowCounts, report.initial.rowCounts);
    assert.deepEqual(report.final.metadataRowCounts, report.initial.metadataRowCounts);
    assert.deepEqual(report.final.auditActions, ['insert', 'update', 'delete'].map(action => ({ table, rowId, action })));
    assert.equal(report.writes.length, 3);
    assert.deepEqual(report.pageErrors, []);
    assert.equal(report.blockedExternal.some(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method)), false);
  });
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failure = { step: currentStep, message: String(error), stack: error.stack };
  await screenshot('failure').catch(() => {});
  process.exitCode = 1;
  console.error(error);
} finally {
  report.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'browser-result.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
