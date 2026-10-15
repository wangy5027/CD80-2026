// Minimal visual regression for the home APP creation toggle. Run against the
// disposable 49024 fixture after the frontend build; no APP or model request.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:49024';
const out = '.run/home-creation-visual';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
page.setDefaultTimeout(20000);
const errors = [], requests = [], evidence = {};
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (request.method() === 'POST' && /\/(authoring-sessions|conversations\/[^/]+\/messages)$/.test(new URL(request.url()).pathname))
    requests.push(new URL(request.url()).pathname);
});
const button = () => page.getByRole('button', { name: '创建模式', exact: true });
const composer = () => page.locator('.composer-wrap');
async function style() {
  return button().evaluate(node => {
    const css = getComputedStyle(node), b = node.getBoundingClientRect();
    return { ariaPressed: node.getAttribute('aria-pressed'), background: css.backgroundColor,
      color: css.color, borderColor: css.borderColor, boxShadow: css.boxShadow,
      rect: { x: b.x, y: b.y, right: b.right, bottom: b.bottom } };
  });
}
async function stableInput(width) {
  await page.waitForFunction(() => {
    const sidebar = document.querySelector('#workspace-sidebar');
    const drawer = document.querySelector('.mobile-sidebar-backdrop');
    return innerWidth >= 768 || (getComputedStyle(sidebar).visibility === 'hidden'
      && sidebar.getBoundingClientRect().right <= 1 && !drawer);
  });
  await composer().scrollIntoViewIfNeeded();
  await page.waitForFunction(() => {
    const rect = document.querySelector('.composer-wrap')?.getBoundingClientRect();
    return !!rect && rect.left >= -1 && rect.right <= innerWidth + 1
      && rect.top >= -1 && rect.bottom <= innerHeight + 1;
  });
  // Wait for layout and the pressed style transition to settle before pixels.
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true,
    `No horizontal overflow at ${width}px`);
}
async function capture(label, pressed, width) {
  assert.equal(await button().getAttribute('aria-pressed'), String(pressed));
  await stableInput(width);
  const rendered = await style();
  await page.screenshot({ path: `${out}-${label}.png` });
  return rendered;
}
function assertVisibleContrast(off, on, label) {
  assert.equal(off.ariaPressed, 'false'); assert.equal(on.ariaPressed, 'true');
  assert.ok(['background', 'color', 'borderColor', 'boxShadow'].some(key => off[key] !== on[key]),
    `${label}: computed button style must change when creation mode is selected`);
}

try {
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill('creation-owner@example.invalid');
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click();
  await page.locator('#composer-input').waitFor();
  evidence.desktopOff = await capture('desktop-off', false, 1440);
  await button().click();
  evidence.desktopOn = await capture('desktop-on', true, 1440);
  assertVisibleContrast(evidence.desktopOff, evidence.desktopOn, 'desktop');

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '打开导航', exact: true }).click();
  await page.locator('#workspace-sidebar').getByRole('button', { name: '新任务', exact: true }).click();
  await page.waitForFunction(() => getComputedStyle(document.querySelector('#workspace-sidebar')).visibility === 'hidden');
  await button().click(); // Turn off after closing the drawer.
  evidence.mobileOff = await capture('mobile-off', false, 390);
  await button().click();
  evidence.mobileOn = await capture('mobile-on', true, 390);
  assertVisibleContrast(evidence.mobileOff, evidence.mobileOn, 'mobile');

  assert.deepEqual(requests, [], 'Visual-only toggle must not submit a task or APP');
  assert.deepEqual(errors, []);
  await writeFile(out + '.json', JSON.stringify({ status: 'passed', realModel: false, visualOnly: true,
    evidence, requests, errors }, null, 2));
  console.log(JSON.stringify({ status: 'passed', images: 4, width: [1440, 390] }));
} catch (error) {
  await page.screenshot({ path: out + '-failure.png' }).catch(() => {});
  await writeFile(out + '-failure.txt', String(error.stack || error) + '\n' + await page.locator('body').innerText().catch(() => ''));
  throw error;
} finally { await browser.close(); }
