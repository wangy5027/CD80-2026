/** Read-only acceptance against real data through an isolated login fixture. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:49021';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(30000);
const errors = [], writes = [], checks = [];
page.on('pageerror', e => errors.push(e.message));
page.on('request', r => {
  if (r.url().includes('/financial-data/') && !['GET', 'HEAD'].includes(r.method()) && !r.url().endsWith('/query')) writes.push(r.url());
});
const panel = page.locator('.financial-data-page');
async function step(name, action) { await action(); checks.push(name); console.log('PASS', name); }
async function choose(table) {
  const response = page.waitForResponse(r => r.url().endsWith(`/tables/${table}/query`) && r.request().method() === 'POST');
  await panel.getByLabel('选择数据表', { exact: true }).selectOption(table);
  assert.equal((await response).status(), 200);
  await panel.locator('.records-table tbody tr').first().waitFor();
  assert.equal(await panel.locator('.catalog-detail-title code').innerText(), table);
}
async function noOverflow() {
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  assert.equal(await panel.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
}
let catalog;
try {
  await step('登录读取37张真实本地表，普通账号只读', async () => {
    await page.goto(base + '/desktop/');
    await page.locator('input[name=email]').fill('catalog-reader@example.invalid');
    await page.locator('input[name=password]').fill('fixture-password');
    await page.locator('form button[type=submit]').click();
    await page.locator('#composer-input').waitFor();
    const pending = page.waitForResponse(r => r.url().endsWith('/financial-data/catalog'));
    await page.getByRole('button', { name: '数据管理', exact: true }).click();
    const response = await pending;
    assert.equal(response.status(), 200);
    catalog = await response.json();
    assert.equal(catalog.total, 37);
    assert.ok(catalog.data.every(t => t.provider === 'Tushare' && t.storage === 'local_sqlite' && !/[\\/]/.test(t.source_file)));
    await panel.locator('.records-table tbody tr').first().waitFor();
    assert.equal(await panel.locator('.catalog-item').count(), 37);
    assert.equal(await panel.locator('.access-label').innerText(), '官方表只读');
    assert.equal(await panel.getByRole('button', { name: '新增记录', exact: true }).count(), 0);
    await panel.getByRole('heading', { name: '官方数据源 · Tushare', exact: true }).waitFor();
  });
  await step('日期范围及主要列匹配目录响应；月度、无日期、IPO空值处理正确', async () => {
    for (const table of ['cd80_daily', 'cd80_cn_cpi', 'cd80_ipo', 'cd80_factor_list']) {
      await choose(table);
      const data = catalog.data.find(t => t.name === table);
      const facts = await panel.locator('.catalog-facts').innerText();
      assert.ok(facts.includes(data.source_file));
      if (data.dateRange.status === 'available') {
        assert.ok(facts.includes(data.dateRange.start));
        assert.ok(facts.includes(data.dateRange.end));
        assert.ok(facts.includes(data.dateRange.field));
      } else assert.ok(facts.includes('此表没有适用的主要日期列'));
      assert.deepEqual(await panel.locator('.catalog-columns li code').allTextContents(), data.primaryColumns.map(c => c.name));
    }
    const cpi = catalog.data.find(t => t.name === 'cd80_cn_cpi');
    assert.match(cpi.dateRange.start, /^\d{4}-\d{2}$/);
    assert.ok(!catalog.data.find(t => t.name === 'cd80_ipo').dateRange.end.includes('NULL'));
    await choose('cd80_daily');
    await noOverflow();
    await panel.locator('.official-catalog').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '.run/data-catalog-desktop.png' });
  });
  await step('目录搜索、选择及原记录筛选分页保留', async () => {
    await panel.getByLabel('搜索数据表', { exact: true }).fill('中信');
    assert.equal(await panel.locator('.catalog-item').count(), 1);
    const p = page.waitForResponse(r => r.url().endsWith('/tables/cd80_ci_index_member/query'));
    await panel.locator('.catalog-item').click();
    assert.equal((await p).status(), 200);
    await panel.getByLabel('搜索数据表', { exact: true }).fill('');
    assert.equal(await panel.getByLabel('选择数据表').inputValue(), 'cd80_ci_index_member');
    await choose('cd80_stock_basic');
    await panel.getByRole('button', { name: '查看数据记录', exact: true }).click();
    await panel.getByRole('button', { name: '下一页', exact: true }).click();
    await panel.locator('.pagination-bar').getByText(/第 2 页/).waitFor();
    await panel.getByRole('button', { name: '添加筛选条件', exact: true }).click();
    const filter = panel.locator('.filter-row');
    await filter.getByRole('combobox').nth(0).selectOption('ts_code');
    await filter.getByRole('textbox').fill('000001.SZ');
    const p2 = page.waitForResponse(r => r.url().endsWith('/tables/cd80_stock_basic/query'));
    await panel.getByRole('button', { name: '应用筛选', exact: true }).click();
    assert.equal((await p2).status(), 200);
    await panel.locator('.records-table tbody tr').first().waitFor();
    assert.equal(await panel.locator('.records-table tbody tr').count(), 1);
    const refresh = page.waitForResponse(r => r.url().endsWith('/financial-data/catalog'));
    await panel.getByRole('button', { name: '刷新数据', exact: true }).click();
    assert.equal((await refresh).status(), 200);
    assert.equal(await panel.getByLabel('选择数据表').inputValue(), 'cd80_stock_basic');
    await panel.locator('.records-table tbody tr').first().waitFor();
    assert.equal(await panel.locator('.records-table tbody tr').count(), 1);
    await panel.locator('.row-action').click();
    await page.getByRole('dialog').locator('.detail-grid').waitFor();
    assert.equal(await page.getByRole('dialog').getByRole('button', { name: '编辑记录', exact: true }).count(), 0);
    await page.getByRole('button', { name: '关闭弹窗', exact: true }).click();
  });
  await step('手机与短屏无页面横向溢出，目录和正文均可滚动', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    const close = page.locator('#workspace-sidebar').getByRole('button', { name: '关闭导航', exact: true });
    if (await close.getAttribute('aria-expanded') === 'true') await close.click();
    await close.waitFor({ state: 'hidden' });
    await noOverflow();
    const listScroll = await panel.locator('.catalog-list').evaluate(el => {
      el.scrollTop = el.scrollHeight;
      return { top: el.scrollTop, height: el.clientHeight, full: el.scrollHeight };
    });
    assert.ok(listScroll.top > 0);
    await panel.locator('.catalog-detail').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '.run/data-catalog-mobile.png' });
    await panel.getByRole('button', { name: '查看数据记录', exact: true }).click();
    await panel.getByRole('button', { name: '下一页', exact: true }).scrollIntoViewIfNeeded();
    await page.setViewportSize({ width: 1280, height: 650 });
    await noOverflow();
    await panel.locator('.catalog-detail').scrollIntoViewIfNeeded();
    await page.screenshot({ path: '.run/data-catalog-short-screen.png' });
  });
  assert.deepEqual(errors, []);
  assert.deepEqual(writes, []);
  await writeFile('.run/data-catalog-browser.json', JSON.stringify({ status: 'passed', tables: catalog.total, checks, errors, writes }, null, 2));
  console.log(JSON.stringify({ status: 'passed', checks: checks.length, writes: writes.length }));
} catch (error) {
  await writeFile('.run/data-catalog-browser-failure.txt', String(error) + '\n' + await page.locator('body').innerText());
  await page.screenshot({ path: '.run/data-catalog-browser-failure.png' });
  throw error;
} finally { await browser.close(); }
