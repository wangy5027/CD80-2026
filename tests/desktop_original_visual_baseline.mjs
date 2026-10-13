/** Screenshot the unchanged workbench panels against an isolated browser fixture.
 *
 * Start tests/browser_fixture.py with the desired frontend dist first. The
 * fixture creates throwaway accounts and never starts a real model.
 */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const playwright = moduleDir
  ? require(existsSync(path.join(moduleDir, 'playwright', 'package.json')) ? path.join(moduleDir, 'playwright') : moduleDir)
  : require('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const phase = process.env.VISUAL_PHASE || 'before';
assert.ok(['before', 'after'].includes(phase), 'VISUAL_PHASE must be before or after.');
const output = path.join(root, 'docs', 'sqlite-sync-qa', phase);
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48993/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname));
await mkdir(output, { recursive: true });

const fixture = await fetch(new URL('/__fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
assert.equal(fixture.status, 200, 'Start the isolated tests/browser_fixture.py server first.');
const stats = await fixture.json();
assert.equal(stats.fixture, true);
assert.equal(stats.realModel, false);
assert.equal(stats.realWasi, true);

const report = { phase, frontendFixture: true, realModel: false, panels: {}, pageErrors: [], blockedExternal: [] };
const browser = await playwright.chromium.launch({ channel: 'chrome', headless: true });
try {
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
  await page.goto(base.href);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('视觉基线');
  await form.locator('input[name="email"]').fill('visual-baseline@fixture.test');
  await form.locator('input[name="password"]').fill('visual-fixture-password');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('button', { name: '应用', exact: true }).waitFor({ state: 'visible' });

  async function capture(name, trigger) {
    if (trigger) await trigger.click();
    const panel = page.locator('.main-panel');
    await panel.waitFor({ state: 'visible' });
    await panel.evaluate(element => { element.scrollTop = 0; });
    await page.waitForTimeout(250);
    const image = `${name}.png`;
    await panel.screenshot({ path: path.join(output, image), animations: 'disabled' });
    report.panels[name] = { screenshot: image, text: (await panel.innerText()).slice(0, 1000) };
  }

  await capture('home');
  for (const [name, label] of [
    ['schedule', '日程'], ['applications', '应用'], ['extensions', '扩展'], ['files', '文件'],
  ]) {
    await capture(name, page.getByRole('button', { name: label, exact: true }).first());
  }
  await capture('settings', page.getByRole('button', { name: '账号与设置', exact: true }));
  report.dataManagementNavVisible = await page.getByRole('button', { name: '数据管理', exact: true }).count() > 0;
  assert.deepEqual(report.pageErrors, []);
  assert.equal(report.blockedExternal.some(item => ['POST', 'PATCH', 'PUT', 'DELETE'].includes(item.method)), false);
  report.passed = true;
  await context.close();
} catch (error) {
  report.passed = false;
  report.error = String(error);
  throw error;
} finally {
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2));
  await browser.close();
}
