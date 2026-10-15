/** Read-only visual check of the new data page against the disposable account fixture. */
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
const output = path.join(root, 'docs', 'sqlite-sync-qa');
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:58996/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname));
await mkdir(output, { recursive: true });

const response = await fetch(new URL('/__financial_fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
assert.equal(response.status, 200, 'Start account_financial_fixture.py first.');
const stats = await response.json();
assert.equal(stats.fixture, true);
assert.equal(stats.temporaryData, true);
assert.equal(stats.realModel, false);
assert.equal(stats.identityMatchesSource, true);
assert.equal(stats.canWriteFromConfiguredAllowlist, true);
assert.equal(stats.accountEmail, accountEmail);

const report = { readOnly: true, temporaryData: true, pageErrors: [], blockedExternal: [], tableCount: null,
  accessLabel: null, desktopScreenshot: 'data-page-desktop.png', mobileScreenshot: 'data-page-mobile.png',
  mobileRecordsScreenshot: 'data-page-mobile-records.png' };
const browser = await chromium.launch({ channel: 'chrome', headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === base.origin || ['data:', 'blob:'].includes(url.protocol)) return route.continue();
    report.blockedExternal.push({ origin: url.origin, method: route.request().method() });
    return route.abort();
  });
  const page = await context.newPage();
  page.on('pageerror', error => report.pageErrors.push(String(error)));
  await page.goto(base.href);
  await page.locator('input[name="email"]').fill(stats.accountEmail);
  await page.locator('input[name="password"]').fill('fixture-password-123');
  await page.getByRole('button', { name: '登录', exact: true }).click();
  await page.getByRole('button', { name: '数据管理', exact: true }).click();
  const panel = page.locator('.financial-data-page');
  await panel.locator('.records-table tbody tr').first().waitFor();
  report.tableCount = await panel.getByLabel('选择数据表').locator('option').count();
  report.accessLabel = await panel.locator('.access-label').innerText();
  assert.equal(report.tableCount, 37);
  assert.equal(report.accessLabel, '可编辑');
  assert.equal(await panel.getByRole('button', { name: '新增记录', exact: true }).isEnabled(), true);
  await page.screenshot({ path: path.join(output, report.desktopScreenshot), fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(output, report.mobileScreenshot), fullPage: true, animations: 'disabled' });
  report.mobileBodyOverflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await panel.locator('.records-table').scrollIntoViewIfNeeded();
  await page.screenshot({ path: path.join(output, report.mobileRecordsScreenshot), fullPage: true, animations: 'disabled' });
  assert.deepEqual(report.pageErrors, []);
  assert.equal(report.blockedExternal.some(item => ['POST', 'PATCH', 'PUT', 'DELETE'].includes(item.method)), false);
  report.passed = true;
  await context.close();
} catch (error) {
  report.passed = false;
  report.error = String(error);
  throw error;
} finally {
  await writeFile(path.join(output, 'data-page-visual-report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
