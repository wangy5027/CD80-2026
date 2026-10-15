import test from 'node:test';
import assert from 'node:assert/strict';
import { all, component, content, flush, hostNode, renderer } from './assetComponentHarness.mjs';

const FinancialDataPanel = await component('./components/FinancialDataPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 6; index++) await flush(); };

test('official catalog selects actual local tables without changing browsing or write permissions', async () => {
  const previousFetch = globalThis.fetch, calls = [];
  let refreshed = false;
  const tables = [
    { name: 'cd80_daily', title: 'Stock daily prices', summary: '股票日行情', row_count: 120,
      provider: 'Tushare', storage: 'local_sqlite', source_file: 'daily.csv',
      sourceNote: '由 Tushare 提供并导入当前本机 SQLite，不代表实时行情。',
      primaryColumns: [{ name: 'ts_code', description: '证券代码' }, { name: 'trade_date', description: '交易日' }],
      dateRange: { status: 'available', field: 'trade_date', label: '交易日', start: '2024-07-01', end: '2026-09-30', granularity: 'day' } },
    { name: 'cd80_fund_nav', title: 'Fund NAV', summary: '公募基金净值', row_count: 8,
      provider: 'Tushare', storage: 'local_sqlite', source_file: '/hidden/path/fund_nav.csv',
      primaryColumns: [{ name: 'fund_code', description: '基金代码' }],
      dateRange: { status: 'no_valid_date', field: 'nav_date', label: '净值日', start: null, end: null } },
  ];
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, method: options.method || 'GET' });
    if (url.endsWith('/financial-data/capabilities')) return json({ canRead: true, canWrite: false });
    if (url.endsWith('/financial-data/catalog')) return json({ data: refreshed ? tables.map(item => item.name === 'cd80_fund_nav'
      ? { ...item, dateRange: { status: 'available', field: 'nav_date', label: '净值日',
        start: '2025-01-01', end: '2026-09-30', granularity: 'day' } } : item) : tables, total: tables.length });
    if (url.endsWith('/schema')) return json({ fields: [
      { name: '_row_id', type: 'INTEGER' }, { name: 'ts_code', type: 'TEXT' }, { name: 'trade_date', type: 'TEXT' }], row_count: 120 });
    if (url.endsWith('/query')) return json({ data: [], pagination: { has_more: false, next_after_id: null } });
    throw new Error('unexpected request ' + url);
  };
  const root = hostNode('root'), app = renderer.createApp(FinancialDataPanel);
  try {
    app.mount(root); await settle();
    assert.match(content(root), /官方数据源 · Tushare/);
    assert.match(content(root), /2024-07-01 至 2026-09-30/);
    assert.match(content(root), /交易日（trade_date）/);
    assert.match(content(root), /daily\.csv/);
    assert.equal(all(root, node => node.tag === 'button' && String(node.props.class).includes('catalog-item')).length, 2);
    assert.equal(all(root, node => node.tag === 'option' && String(node.props.value || '').startsWith('cd80_')).length, 2,
      'the original table selector remains available');
    assert.equal(content(root).includes('新增记录'), false, 'read-only users still have no write entry');
    const fund = all(root, node => node.tag === 'button' && String(node.props.class).includes('catalog-item'))[1];
    fund.props.onClick(); await settle();
    assert.match(content(root), /当前记录没有可确认的有效日期/);
    assert.match(content(root), /fund_nav\.csv/);
    assert.doesNotMatch(content(root), /\/hidden\/path/);
    assert.ok(calls.some(call => call.url.includes('/cd80_fund_nav/schema')));
    assert.ok(calls.some(call => call.url.includes('/cd80_fund_nav/query')));
    assert.equal(calls.some(call => /\/rows(?:\/|$)/.test(call.url) && call.method !== 'GET'), false);
    refreshed = true;
    all(root, node => node.tag === 'button' && content(node) === '刷新数据')[0].props.onClick(); await settle();
    assert.match(content(root), /2025-01-01 至 2026-09-30/);
    assert.equal(all(root, node => node.tag === 'button' && String(node.props.class).includes('catalog-item') && node.props['aria-pressed']).length, 1,
      'refresh keeps the selected table');
  } finally { app.unmount(); globalThis.fetch = previousFetch; }
});
