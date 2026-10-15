import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { execFileSync } from 'node:child_process';
import path from 'node:path';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const root = process.cwd(), base = 'http://127.0.0.1:49011';
const directory = path.join(root, '.run/standard-export-downloads');
await mkdir(directory, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
page.setDefaultTimeout(30000);
const errors = [], checks = [], writes = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (['POST', 'PATCH', 'DELETE'].includes(request.method()) && !request.url().endsWith('/login')) writes.push(request.url());
});
async function download(name, category) {
  await page.locator('.apps-library-card').filter({ hasText: name }).first().click();
  const dialog = page.getByRole('dialog'), button = dialog.getByRole('button', { name: '标准格式导出', exact: true });
  await button.waitFor();
  await page.waitForFunction(() => [...document.querySelectorAll('dialog[open] button')].some(b => b.textContent.trim() === '标准格式导出' && !b.disabled));
  const [result] = await Promise.all([page.waitForEvent('download'), button.click()]);
  assert.equal(result.suggestedFilename(), name + '.zip');
  const saved = path.join(directory, category + '.zip');
  await result.saveAs(saved);
  const metadata = JSON.parse(execFileSync(path.join(root, '.venv/bin/python'), ['-c',
    'import json,sys,zipfile\nwith zipfile.ZipFile(sys.argv[1]) as z:\n print(json.dumps({"files":z.namelist(),"readme":z.read("readme.md").decode("utf-8")},ensure_ascii=False))', saved], { encoding: 'utf8' }));
  assert.ok(metadata.readme.includes(name));
  assert.ok(metadata.files.length >= 2);
  if (category === 'builtin') assert.ok(metadata.files.includes('workspace.json'));
  if (category === 'app') assert.ok(metadata.readme.includes('prices'));
  if (category === 'tool') assert.ok(metadata.readme.includes('value'));
  if (category === 'skill') assert.ok(metadata.readme.includes('Summarize only the supplied facts'));
  const bounds = await button.boundingBox(), modal = await dialog.boundingBox();
  assert.ok(bounds.x + bounds.width / 2 > modal.x + modal.width / 2);
  assert.ok(bounds.y < modal.y + 180);
  await page.screenshot({ path: '.run/standard-export-' + category + '.png' });
  checks.push({ category, filename: result.suggestedFilename(), files: metadata.files, readmeChars: metadata.readme.length });
  await dialog.getByRole('button', { name: /^关闭/ }).click();
}
try {
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill('original-case@example.invalid');
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click();
  await page.locator('#composer-input').waitFor();
  await page.getByRole('button', { name: '技能池', exact: true }).click();
  await download('原用例预填 APP', 'app');
  await download('Explore', 'builtin');
  for (const kind of ['Tools', 'Skills']) {
    await page.getByRole('tab', { name: new RegExp(kind) }).click();
    await download('原用例预填 ' + kind.slice(0, -1), kind.slice(0, -1).toLowerCase());
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const closeNav = page.locator('#workspace-sidebar').getByRole('button', { name: '关闭导航', exact: true });
  if (await closeNav.getAttribute('aria-expanded') === 'true') await closeNav.click();
  await closeNav.waitFor({ state: 'hidden' });
  await page.locator('.apps-library-card').filter({ hasText: '原用例预填 Skill' }).first().click();
  const dialog = page.getByRole('dialog');
  await dialog.getByRole('button', { name: '标准格式导出', exact: true }).waitFor();
  assert.ok(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1));
  await page.screenshot({ path: '.run/standard-export-mobile.png' });
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  await writeFile('.run/standard-export-browser.json', JSON.stringify({ status: 'passed', checks, errors, writes }, null, 2));
  console.log(JSON.stringify({ status: 'passed', downloads: checks.length, writes: writes.length }));
} catch (error) {
  await writeFile('.run/standard-export-browser-failure.txt', String(error) + '\n' + await page.locator('body').innerText());
  await page.screenshot({ path: '.run/standard-export-browser-failure.png' });
  throw error;
} finally { await browser.close(); }
