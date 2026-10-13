/**
 * Explore session-menu browser acceptance against browser_explore_fixture.py.
 * News, model replies and failure injection are explicitly synthetic. The
 * fixture creates disposable accounts and SQLite data; no real account is read.
 */
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
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
const output = path.resolve(process.env.EXPLORE_SESSIONS_OUTPUT || path.join(root, 'data', 'explore-session-actions', stamp));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only the local disposable fixture is accepted.');
await mkdir(output, { recursive: true });

const report = {
  startedAt: new Date().toISOString(), fixture: true, realNews: false, realModel: false,
  failureInjected: false, checks: [], screenshots: [], pageErrors: [],
};
let browser;
let context;
let page;
let currentStep = 'fixture availability';
let activeSessionId;
let activeRunId;
let secondarySessionId;

function tid(id) { return page.getByTestId(id); }
function sessionRow(id) { return tid('explore-session-list').locator(`li[data-session-id="${id}"]`); }
function selectedRoute() {
  const query = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { session: query.get('session') || '', run: query.get('run') || '' };
}
async function apiGet(pathname, status = 200) {
  const response = await context.request.get(new URL(pathname, base).href);
  assert.equal(response.status(), status, pathname);
  return status === 200 ? await response.json() : null;
}
async function sessionTitle(id) {
  return (await apiGet(`/desktop/api/explore/sessions/${id}`)).session.title;
}
async function createSession(title) {
  const response = await context.request.post(new URL('/desktop/api/explore/sessions', base).href, { data: { title } });
  assert.equal(response.status(), 201);
  const data = await response.json();
  assert.ok(data.session?.id);
  return data.session.id;
}
async function screenshot(name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: false });
  report.screenshots.push(filename);
}
async function step(name, action) {
  currentStep = name;
  console.log(`RUN ${name}`);
  const began = Date.now();
  const details = await action();
  report.checks.push({ name, passed: true, elapsedMs: Date.now() - began, ...(details || {}) });
  console.log(`PASS ${name}`);
}
async function openMore(id) {
  const button = sessionRow(id).getByTestId('explore-session-more');
  await button.click();
  await tid('explore-session-menu').waitFor({ state: 'visible' });
  return button;
}
async function openRename(id) {
  const button = await openMore(id);
  await tid('explore-session-rename').click();
  await tid('explore-rename-dialog').waitFor({ state: 'visible' });
  return button;
}
async function openDelete(id) {
  const button = await openMore(id);
  await tid('explore-session-delete').click();
  await tid('explore-delete-dialog').waitFor({ state: 'visible' });
  return button;
}
async function unchangedActive(expectedDraft, expectedScroll) {
  assert.deepEqual(selectedRoute(), { session: activeSessionId, run: activeRunId });
  assert.equal(await tid('explore-report').isVisible(), true);
  assert.equal(await tid('explore-input').inputValue(), expectedDraft);
  const scroll = await page.locator('.explore-scroll').evaluate(node => node.scrollTop);
  assert.ok(Math.abs(scroll - expectedScroll) <= 2, `Report scroll moved from ${expectedScroll} to ${scroll}`);
}
async function verifyNoRename(id, title) {
  assert.equal(await sessionTitle(id), title);
  assert.equal(await tid('explore-rename-dialog').count(), 0);
}

try {
  const status = await fetch(new URL('/__explore_fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(status.status, 200, 'Start tests/browser_explore_fixture.py first.');
  const stats = await status.json();
  assert.equal(stats.fixture, true);
  assert.equal(stats.realNews, false);
  assert.equal(stats.realModel, false);
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: process.env.BROWSER_HEADLESS !== '0' });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));

  await step('create two saved sessions and keep a report open in the selected one', async () => {
    await page.goto(base.href);
    await page.getByRole('button', { name: '创建账号', exact: true }).click();
    const form = page.locator('#auth-form');
    await form.locator('input[name="name"]').fill('Explore 会话操作验收');
    await form.locator('input[name="email"]').fill(`explore-session-${Date.now()}@example.test`);
    await form.locator('input[name="password"]').fill('synthetic-session-fixture-password');
    await form.getByRole('button', { name: '创建账号', exact: true }).click();
    await page.getByRole('button', { name: '开始探索', exact: true }).click();
    await tid('explore-workspace').waitFor({ state: 'visible' });
    await tid('explore-input').fill('请分析测试甲集团拟收购测试乙公司的背景、时间线与影响。[SESSION_ACTION_TEST]');
    await tid('explore-submit').click();
    await tid('explore-report').waitFor({ state: 'visible', timeout: 30000 });
    ({ session: activeSessionId, run: activeRunId } = selectedRoute());
    assert.ok(activeSessionId && activeRunId);
    secondarySessionId = await createSession('待处理的另一份探索');
    await page.reload();
    await tid('explore-report').waitFor({ state: 'visible' });
    await sessionRow(secondarySessionId).waitFor({ state: 'visible' });
    assert.deepEqual(selectedRoute(), { session: activeSessionId, run: activeRunId });
    return { selectedSessionId: activeSessionId, otherSessionId: secondarySessionId };
  });

  const draft = '草稿仍留在当前报告：检查交易审批条件。';
  let scrollBefore = 0;
  await step('other session menu never changes report, composer or scroll', async () => {
    await tid('explore-input').fill(draft);
    scrollBefore = await page.locator('.explore-scroll').evaluate(node => {
      node.scrollTo({ top: Math.min(180, node.scrollHeight - node.clientHeight), behavior: 'instant' });
      return node.scrollTop;
    });
    assert.ok(scrollBefore > 0, 'A report must be scrollable for this regression check.');
    await openMore(secondarySessionId);
    await page.waitForFunction(id => {
      const node = document.querySelector(`li[data-session-id="${id}"] [data-testid="explore-session-more"]`);
      return node?.getAttribute('aria-expanded') === 'true' && getComputedStyle(node).opacity === '1';
    }, secondarySessionId, { timeout: 2000 });
    const moreState = await sessionRow(secondarySessionId).getByTestId('explore-session-more').evaluate(node => ({
      opacity: getComputedStyle(node).opacity, expanded: node.getAttribute('aria-expanded'),
      transition: getComputedStyle(node).transition,
      selectorMatched: node.matches('.explore-theme .explore-session-more[aria-expanded="true"]'),
      outerHTML: node.outerHTML,
      stylesheets: [...document.querySelectorAll('link[rel="stylesheet"]')].map(link => link.href),
      matchingRules: [...document.styleSheets].flatMap(sheet => {
        try { return [...sheet.cssRules].filter(rule => rule.selectorText?.includes('explore-session-more') && rule.selectorText.split(',').some(selector => node.matches(selector.trim()))).map(rule => rule.cssText); }
        catch { return []; }
      }),
    }));
    assert.equal(moreState.opacity, '1',
      `The active action trigger must remain visible while its menu is open: ${JSON.stringify(moreState)}`);
    await screenshot('01-other-session-menu-synthetic');
    await unchangedActive(draft, scrollBefore);
    assert.equal(await sessionRow(secondarySessionId).locator('.explore-session-item').getAttribute('aria-current'), null);
    await page.keyboard.press('Escape');
    await tid('explore-session-menu').waitFor({ state: 'hidden' });
    return { scrollBefore };
  });

  await step('rename dialog selects the title and Enter saves the other session', async () => {
    const more = await openRename(secondarySessionId);
    const dialog = tid('explore-rename-dialog');
    const input = tid('explore-rename-input');
    assert.equal(await input.inputValue(), '待处理的另一份探索');
    const selection = await input.evaluate(node => ({ start: node.selectionStart, end: node.selectionEnd, length: node.value.length,
      focused: document.activeElement === node }));
    assert.deepEqual(selection, { start: 0, end: selection.length, length: selection.length, focused: true },
      'Opening rename should focus and select the full old title.');
    await screenshot('02-rename-dialog-synthetic');
    await input.fill('通过 Enter 改名的探索');
    await page.keyboard.press('Enter');
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await sessionTitle(secondarySessionId), '通过 Enter 改名的探索');
    assert.equal(await more.evaluate(node => node === document.activeElement), true,
      'Closing rename should return focus to the target row action.');
    await unchangedActive(draft, scrollBefore);
    await page.reload();
    await sessionRow(secondarySessionId).getByText('通过 Enter 改名的探索').waitFor({ state: 'visible' });
    assert.deepEqual(selectedRoute(), { session: activeSessionId, run: activeRunId });
    await tid('explore-input').fill(draft);
    scrollBefore = await page.locator('.explore-scroll').evaluate(node => {
      node.scrollTo({ top: Math.min(180, node.scrollHeight - node.clientHeight), behavior: 'instant' });
      return node.scrollTop;
    });
    assert.ok(scrollBefore > 0);
    return { persistedTitle: await sessionTitle(secondarySessionId) };
  });

  await step('Cancel, Escape and outside click discard title edits', async () => {
    const saved = '通过 Enter 改名的探索';
    await openRename(secondarySessionId);
    await tid('explore-rename-input').fill('应由取消丢弃');
    await tid('explore-session-dialog-cancel').click();
    await verifyNoRename(secondarySessionId, saved);
    await openRename(secondarySessionId);
    await tid('explore-rename-input').fill('应由 Escape 丢弃');
    await page.keyboard.press('Escape');
    await verifyNoRename(secondarySessionId, saved);
    await openRename(secondarySessionId);
    await tid('explore-rename-input').fill('应由外点丢弃');
    await page.locator('.explore-session-overlay').click({ position: { x: 5, y: 5 } });
    await verifyNoRename(secondarySessionId, saved);
    await unchangedActive(draft, scrollBefore);
  });

  await step('composition Enter does not save and Tab stays inside the dialog', async () => {
    await openRename(secondarySessionId);
    const dialog = tid('explore-rename-dialog');
    const input = tid('explore-rename-input');
    await input.fill('中文输入法候选项尚未确认');
    let patchCalls = 0;
    const observe = request => {
      if (request.method() === 'PATCH' && new URL(request.url()).pathname.endsWith(`/sessions/${secondarySessionId}`)) patchCalls += 1;
    };
    page.on('request', observe);
    await input.evaluate(node => {
      node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '中' }));
      node.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 }));
      node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '中' }));
    });
    await page.waitForTimeout(150);
    page.off('request', observe);
    assert.equal(patchCalls, 0, 'IME confirmation must not issue a rename request.');
    assert.equal(await dialog.isVisible(), true);
    assert.equal(await sessionTitle(secondarySessionId), '通过 Enter 改名的探索');
    for (let index = 0; index < 8; index += 1) {
      await page.keyboard.press(index === 0 ? 'Shift+Tab' : 'Tab');
      assert.equal(await dialog.evaluate(node => node.contains(document.activeElement)), true,
        'Keyboard focus must stay in the active modal.');
    }
    await tid('explore-session-dialog-close').click();
    await dialog.waitFor({ state: 'hidden' });
  });

  await step('one injected 503 keeps the rename draft and allows retry', async () => {
    await openRename(secondarySessionId);
    const input = tid('explore-rename-input');
    const desired = '网络错误后重试成功';
    await input.fill(desired);
    let injected = false;
    await page.route(`**/desktop/api/explore/sessions/${secondarySessionId}`, async route => {
      if (!injected && route.request().method() === 'PATCH') {
        injected = true;
        await route.fulfill({ status: 503, contentType: 'application/json',
          body: JSON.stringify({ error: '合成测试网络故障', message: '合成测试网络故障' }) });
      } else await route.continue();
    });
    report.failureInjected = true;
    await tid('explore-rename-save').click();
    await tid('explore-rename-dialog').getByRole('alert').waitFor({ state: 'visible' });
    assert.equal(injected, true);
    assert.equal(await input.inputValue(), desired, 'Failed save must retain the draft.');
    assert.equal(await sessionTitle(secondarySessionId), '通过 Enter 改名的探索');
    await page.unroute(`**/desktop/api/explore/sessions/${secondarySessionId}`);
    await tid('explore-rename-save').click();
    await tid('explore-rename-dialog').waitFor({ state: 'hidden' });
    assert.equal(await sessionTitle(secondarySessionId), desired);
    await unchangedActive(draft, scrollBefore);
  });

  await step('delete Cancel keeps the other session; Confirm deletes only that session', async () => {
    await openDelete(secondarySessionId);
    await tid('explore-session-dialog-cancel').click();
    await tid('explore-delete-dialog').waitFor({ state: 'hidden' });
    assert.equal(await sessionTitle(secondarySessionId), '网络错误后重试成功');
    await openDelete(secondarySessionId);
    await screenshot('03-delete-dialog-synthetic');
    await tid('explore-delete-confirm').click();
    await tid('explore-delete-dialog').waitFor({ state: 'hidden' });
    await apiGet(`/desktop/api/explore/sessions/${secondarySessionId}`, 404);
    await apiGet(`/desktop/api/explore/sessions/${activeSessionId}`, 200);
    await apiGet(`/desktop/api/explore/runs/${activeRunId}`, 200);
    await unchangedActive(draft, scrollBefore);
    return { removedSessionId: secondarySessionId, selectedRunPreserved: activeRunId };
  });

  await step('390px session menu and modal remain usable without overflow or hiding history', async () => {
    const mobileId = await createSession('手机上待改名的探索');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload();
    await page.waitForFunction(() => document.querySelector('[data-testid="explore-history-drawer"]')?.getAttribute('role') === 'dialog');
    await sessionRow(mobileId).waitFor({ state: 'visible' });
    assert.match(await tid('explore-history-drawer').getAttribute('class') || '', /\bopen\b/);
    await openMore(mobileId);
    await screenshot('04-mobile-session-menu-synthetic');
    await tid('explore-session-rename').click();
    const modal = tid('explore-rename-dialog');
    await modal.waitFor({ state: 'visible' });
    const bounds = await modal.evaluate(node => {
      const dialog = node.querySelector('[role="dialog"], .explore-session-dialog-card') || node;
      const r = dialog.getBoundingClientRect();
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom,
        viewportWidth: document.documentElement.clientWidth, documentWidth: document.documentElement.scrollWidth };
    });
    assert.ok(bounds.left >= -1 && bounds.right <= bounds.viewportWidth + 1, JSON.stringify(bounds));
    assert.ok(bounds.documentWidth <= bounds.viewportWidth + 1, JSON.stringify(bounds));
    await screenshot('05-mobile-rename-dialog-synthetic');
    await tid('explore-rename-input').fill('手机菜单操作成功');
    await tid('explore-rename-save').click();
    await modal.waitFor({ state: 'hidden' });
    assert.equal(await sessionTitle(mobileId), '手机菜单操作成功');
    assert.match(await tid('explore-history-drawer').getAttribute('class') || '', /\bopen\b/);
    assert.deepEqual(selectedRoute(), { session: activeSessionId, run: activeRunId });
    return bounds;
  });

  assert.deepEqual(report.pageErrors, []);
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failedStep = currentStep;
  report.error = String(error?.stack || error);
  if (page) {
    try { await screenshot('failure'); } catch (diagnosticError) { report.diagnosticError = String(diagnosticError); }
    try { await writeFile(path.join(output, 'failure-body.txt'), await page.locator('body').innerText(), 'utf8'); } catch { /* no page */ }
  }
  console.error(report.error);
  process.exitCode = 1;
} finally {
  report.completedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
  if (browser) await browser.close();
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length,
    failedStep: report.failedStep, report: path.join(output, 'report.json') }));
}
