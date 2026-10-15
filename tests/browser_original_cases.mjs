import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:49011';
const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(30000);
const errors = [], checks = [], submissions = [];
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (request.method() === 'POST' && /\/(verify|validation-suites)$/.test(new URL(request.url()).pathname)) submissions.push(request.postDataJSON());
});
async function pool() { await page.getByRole('button', { name: '技能池', exact: true }).click(); }
async function api(path) { const response = await page.request.get(base + '/desktop/api' + path); assert.ok(response.ok()); return response.json(); }
try {
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill('original-case@example.invalid');
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click();
  await page.locator('#composer-input').waitFor();
  await pool();
  await page.locator('.apps-library-card').filter({ hasText: '原用例预填 APP' }).first().click();
  await page.getByRole('button', { name: '打开我的 APP', exact: true }).click();
  await page.getByRole('button', { name: '测试此版本', exact: true }).click();
  const original = page.getByLabel('验证输入 1', { exact: true });
  assert.deepEqual(JSON.parse(await original.inputValue()), { prices: [100, 120, 90, 108] });
  assert.equal(await page.locator('.asset-extra-case').getAttribute('open'), null);
  assert.equal(await page.getByLabel('验证输入 2', { exact: true }).isVisible(), false);
  await original.fill('{"prices":[5,6]}');
  await page.getByRole('button', { name: '一键填入原用例', exact: true }).click();
  assert.deepEqual(JSON.parse(await original.inputValue()), { prices: [100, 120, 90, 108] });
  await page.locator('.asset-extra-case summary').click();
  await page.getByRole('button', { name: '从原用例填入', exact: true }).click();
  assert.deepEqual(JSON.parse(await page.getByLabel('验证输入 2', { exact: true }).inputValue()), { prices: [100, 120, 90, 108] });
  assert.equal(await page.locator('.asset-extra-case input[type=checkbox]').isChecked(), false);
  await page.locator('.asset-extra-case summary').click();
  await page.locator('.asset-validation-form').scrollIntoViewIfNeeded();
  await page.screenshot({ path: '.run/original-case-app-prefilled.png' });
  await page.getByRole('button', { name: '执行原任务用例验证', exact: true }).click();
  await page.getByRole('heading', { name: '当前版本验证通过', exact: true }).waitFor({ timeout: 60000 });
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(button => button.textContent.trim() === '启用此版本' && !button.disabled), null, { timeout: 60000 });
  assert.equal(submissions.at(-1).cases.length, 1);
  assert.equal(await page.getByRole('button', { name: '启用此版本', exact: true }).isEnabled(), true);
  await page.screenshot({ path: '.run/original-case-app.png', fullPage: true });
  checks.push('APP saved prefill, default collapse, restore/copy, one actual WASI run and enable available');

  await page.getByRole('button', { name: '← 我的 APP', exact: true }).click();
  for (const kind of ['Tools', 'Skills']) {
    await page.getByRole('tab', { name: new RegExp(kind) }).click();
    const noun = kind.slice(0, -1);
    await page.locator('.apps-library-card').filter({ hasText: '原用例预填 ' + noun }).first().click();
    await page.getByRole('button', { name: '管理与验证', exact: true }).click();
    const suite = page.getByRole('region', { name: '原用例与新输入验证' });
    await suite.waitFor();
    assert.equal(await suite.locator('.cap-additional-case').getAttribute('open'), null);
    if (kind === 'Tools') {
      const number = suite.locator('fieldset').first().locator('input[type=number]');
      assert.equal(await number.inputValue(), '7');
      await number.fill('9');
      await suite.getByRole('button', { name: '一键填入原用例', exact: true }).click();
      assert.equal(await number.inputValue(), '7');
      await suite.getByRole('button', { name: '开始原用例验证', exact: true }).click();
      await suite.locator('.cap-suite-results summary').filter({ hasText: '通过' }).waitFor({ timeout: 60000 });
      assert.equal(submissions.at(-1).cases.length, 1);
      checks.push('Tool saved inputs/expectation, restore, one actual WASI run passed');
    } else {
      assert.match(await suite.getByLabel('原任务用例 · 任务', { exact: true }).inputValue(), /项目进展/);
      assert.match(await suite.getByLabel('原任务用例 · 验收标准', { exact: true }).inputValue(), /不得宣称测试通过/);
      await suite.locator('.cap-additional-case > summary').click();
      await suite.getByRole('button', { name: '从原用例填入', exact: true }).click();
      assert.equal(await suite.locator('.cap-additional-case input[type=checkbox]').isChecked(), false);
      await suite.locator('.cap-additional-case > summary').click();
      checks.push('Skill saved task and criteria, optional case copy does not opt into execution');
    }
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => document.querySelector('#workspace-sidebar [aria-label="关闭导航"]'));
    const closeNav = page.locator('#workspace-sidebar').getByRole('button', { name: '关闭导航', exact: true });
    if (await closeNav.getAttribute('aria-expanded') === 'true') await closeNav.click();
    await closeNav.waitFor({ state: 'hidden' });
    await suite.getByRole('heading', { name: '先验证原任务用例', exact: true }).scrollIntoViewIfNeeded();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
    await page.screenshot({ path: '.run/original-case-' + noun.toLowerCase() + '.png', fullPage: true });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.getByRole('button', { name: new RegExp('返回.*' + kind + '目录') }).click();
  }
  assert.deepEqual(errors, []);
  await writeFile('.run/original-case-browser.json', JSON.stringify({ status: 'passed', checks, errors, submissions }, null, 2));
  console.log(JSON.stringify({ status: 'passed', checks }));
} catch (error) {
  await writeFile('.run/original-case-browser-failure.txt', String(error) + '\n' + await page.locator('body').innerText());
  await page.screenshot({ path: '.run/original-case-browser-failure.png', fullPage: true });
  throw error;
} finally { await browser.close(); }
