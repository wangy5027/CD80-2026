import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:49023';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(30000);
const checks = [], errors = [], writes = [], uploads = [];
page.on('pageerror', e => errors.push(e.message));
page.on('request', r => {
  if (r.method() === 'POST' && r.url().endsWith('/api/files')) uploads.push(r.url());
  if (r.url().includes('/financial-data/') && !['GET', 'HEAD'].includes(r.method()) && !r.url().endsWith('/query')) writes.push(r.url());
});
const panel = page.locator('.personal-data-files');
const csv = Buffer.from('category,amount\n小麦,11\n玉米,19\n');
const xlsxPath = '.run/personal-data-example.xlsx';
execFileSync('.venv/bin/python', ['-c', 'from openpyxl import Workbook\nw=Workbook();s=w.active;s.title="销售";s.append(["品类","数量"]);s.append(["稻米",7]);s.append(["大豆",9]);w.save(".run/personal-data-example.xlsx")']);
const xlsx = await readFile(xlsxPath);
let csvId, xlsxId, largeId;
async function step(name, fn) { await fn(); checks.push(name); console.log('PASS', name); }
async function openData() {
  await page.getByRole('button', { name: '数据管理', exact: true }).click();
  await panel.waitFor();
}
async function upload(name, buffer, mimeType = 'application/octet-stream') {
  const chooser = page.waitForEvent('filechooser');
  await panel.getByRole('button', { name: '上传数据文件', exact: true }).click();
  const choice = await chooser;
  assert.equal(await page.locator('#file-picker').getAttribute('accept'), '.csv,.tsv,.json,.xlsx');
  const pending = page.waitForResponse(r => r.url().endsWith('/api/files') && r.request().method() === 'POST');
  await choice.setFiles({ name, mimeType, buffer });
  const response = await pending;
  assert.equal(response.status(), 201, await response.text());
  const meta = await response.json();
  assert.equal(meta.source, 'data_management');
  await panel.locator(`[data-file-id="${meta.id}"]`).waitFor();
  await panel.getByRole('button', { name: '上传数据文件', exact: true }).waitFor();
  return meta;
}
async function attachDialog() {
  await page.getByRole('button', { name: '添加文件或技能池', exact: true }).click();
  await page.getByRole('button', { name: '上传或选择文件', exact: true }).click();
  return page.getByRole('dialog', { name: '添加文件', exact: true });
}
try {
  await step('四种允许格式可上传并持久显示，官方只读不阻断个人文件', async () => {
    await page.goto(base + '/desktop/');
    await page.locator('input[name=email]').fill('data-owner@example.invalid');
    await page.locator('input[name=password]').fill('fixture-password');
    await page.locator('form button[type=submit]').click();
    await page.locator('#composer-input').waitFor();
    await openData();
    csvId = (await upload('个人销售.csv', csv, 'text/csv')).id;
    xlsxId = (await upload('工作表.xlsx', xlsx, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')).id;
    await upload('样例.tsv', Buffer.from('city\tvalue\n成都\t3\n'), 'text/tab-separated-values');
    await upload('样例.json', Buffer.from('{"name":"仅本账号","value":8}'), 'application/json');
    assert.equal(await panel.locator('.personal-file-row').count(), 4);
    await page.reload(); await panel.waitFor();
    assert.equal(await panel.locator('.personal-file-row').count(), 4);
    await page.getByRole('heading', { name: '官方数据源 · Tushare', exact: true }).waitFor();
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: '.run/personal-data-desktop.png' });
  });
  await step('格式及超限前置拦截，恰好10MiB可上传，重复不生成副本', async () => {
    for (const file of [{ name: 'run.exe', buffer: Buffer.from('exe') }, { name: 'too-large.csv', buffer: Buffer.alloc(10 * 1024 * 1024 + 1, 65) }]) {
      const count = uploads.length, chooser = page.waitForEvent('filechooser');
      await panel.getByRole('button', { name: '上传数据文件', exact: true }).click();
      await (await chooser).setFiles({ ...file, mimeType: 'application/octet-stream' });
      await page.getByText('当前网络连接失败', { exact: true }).first().waitFor();
      assert.equal(uploads.length, count);
    }
    assert.equal((await upload('个人销售.csv', csv, 'text/csv')).id, csvId);
    assert.equal(await panel.locator('.personal-file-row').count(), 4);
    const large = Buffer.alloc(10 * 1024 * 1024);
    large.write('x\n');
    large.fill(Buffer.from('1\n'), 2);
    largeId = (await upload('边界数据.csv', large, 'text/csv')).id;
  });
  await step('主问答从数据管理选择CSV和Excel，只有所选文件进入规划', async () => {
    await page.locator('#workspace-sidebar').getByRole('button', { name: '新任务', exact: true }).click();
    const dialog = await attachDialog();
    const ordinaryChooser = page.waitForEvent('filechooser');
    await dialog.getByRole('button', { name: '上传新文件', exact: true }).click();
    const ordinaryChoice = await ordinaryChooser;
    assert.equal(await page.locator('#file-picker').getAttribute('accept'), '');
    await ordinaryChoice.setFiles([]);
    const group = dialog.getByLabel('数据管理中的文件', { exact: true });
    await group.getByRole('button').filter({ hasText: '个人销售.csv' }).click();
    await group.getByRole('button').filter({ hasText: '工作表.xlsx' }).click();
    await page.screenshot({ path: '.run/personal-data-chat-picker.png' });
    await dialog.getByRole('button', { name: '完成', exact: true }).click();
    assert.equal(await page.locator('.attach-chip').count(), 2);
    await page.locator('#composer-input').fill('请读取所选CSV和Excel中的数据。');
    const posted = page.waitForRequest(r => /\/conversations\/[^/]+\/messages$/.test(r.url()) && r.method() === 'POST');
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    assert.deepEqual(new Set((await posted).postDataJSON().fileIds), new Set([csvId, xlsxId]));
    await page.getByText(/已引用所选数据文件：/).first().waitFor();
    const fixture = await (await context.request.get(base + '/__personal_data_fixture__/state')).json();
    assert.equal(fixture.realModel, false);
    const call = fixture.calls.at(-1);
    assert.deepEqual(new Set(call.fileIds), new Set([csvId, xlsxId]));
    assert.ok(call.previews.join(' ').includes('小麦'));
    assert.ok(call.previews.join(' ').includes('稻米'));
  });
  await step('数据页一键引用10MiB文件，不因旧4MiB限制失败', async () => {
    await page.locator('#workspace-sidebar').getByRole('button', { name: '新任务', exact: true }).click();
    await openData();
    await panel.locator(`[data-file-id="${largeId}"]`).getByRole('button', { name: /在问答中引用/ }).click();
    await page.locator('#composer-input').fill('请查看这个大文件的预览。');
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await page.getByText(/已引用所选数据文件：边界数据.csv/).first().waitFor();
    const fixture = await (await context.request.get(base + '/__personal_data_fixture__/state')).json();
    assert.equal(fixture.calls.at(-1).sizes[0], 10 * 1024 * 1024);
    assert.ok(fixture.calls.at(-1).previews[0].length < 14000);
  });
  await step('原文件浏览器下载及跨账号隔离', async () => {
    await openData();
    const pending = page.waitForEvent('download');
    await panel.locator(`[data-file-id="${csvId}"]`).getByRole('button', { name: /下载/ }).click();
    const downloaded = await pending;
    assert.equal(downloaded.suggestedFilename(), '个人销售.csv');
    assert.deepEqual(await readFile(await downloaded.path()), csv);
    const other = await browser.newContext();
    await other.request.post(base + '/desktop/api/login', { data: { email: 'data-other@example.invalid', password: 'fixture-password' } });
    assert.equal((await other.request.get(base + '/desktop/api/state')).status(), 200);
    assert.equal((await (await other.request.get(base + '/desktop/api/state')).json()).files.length, 0);
    assert.equal((await other.request.get(base + '/desktop/api/files/' + csvId)).status(), 404);
    assert.equal((await other.request.delete(base + '/desktop/api/files/' + csvId)).status(), 404);
    await other.close();
  });
  await step('手机滚动、删除同步，以及官方目录故障时个人区仍可用', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    const close = page.locator('#workspace-sidebar').getByRole('button', { name: '关闭导航', exact: true });
    if (await close.getAttribute('aria-expanded') === 'true') await close.click();
    await close.waitFor({ state: 'hidden' });
    await panel.scrollIntoViewIfNeeded();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({ path: '.run/personal-data-mobile.png' });
    await panel.locator(`[data-file-id="${csvId}"]`).getByRole('button', { name: /删除/ }).click();
    await page.getByRole('dialog').getByRole('button', { name: '删除', exact: true }).click();
    await panel.locator(`[data-file-id="${csvId}"]`).waitFor({ state: 'hidden' });
    assert.equal((await context.request.get(base + '/desktop/api/files/' + csvId)).status(), 404);
    await page.route('**/financial-data/catalog', route => route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"fixture unavailable"}' }));
    await page.reload(); await panel.waitFor();
    assert.equal(await panel.locator('.personal-file-row').count(), 4);
    assert.equal(await panel.getByRole('button', { name: '上传数据文件', exact: true }).isEnabled(), true);
  });
  assert.deepEqual(errors, []); assert.deepEqual(writes, []);
  await writeFile('.run/personal-data-browser.json', JSON.stringify({ status: 'passed', checks, errors, financialWrites: writes, realModel: false, fixtureAccounts: true }, null, 2));
  console.log(JSON.stringify({ status: 'passed', checks: checks.length }));
} catch (error) {
  await writeFile('.run/personal-data-browser-failure.txt', String(error) + '\n' + await page.locator('body').innerText());
  await page.screenshot({ path: '.run/personal-data-browser-failure.png' });
  throw error;
} finally { await context.close(); await browser.close(); }
