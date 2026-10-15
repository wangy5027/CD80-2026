import test from 'node:test';
import assert from 'node:assert/strict';
import { chartModel, configFrom, isActiveRun, loadDraft, metricCards, percent, saveDraft, storageKey, validateConfig } from './format.js';
import { workshopRequest } from './api.js';

test('all service execution states remain observable until genuinely terminal', () => {
  for (const status of ['queued', 'validating', 'running', 'cancelling', 'cancel_requested']) assert.equal(isActiveRun({ status }), true);
  for (const status of ['succeeded', 'completed', 'failed', 'cancelled', 'interrupted']) assert.equal(isActiveRun({ status }), false);
});

test('missing and invalid metrics never become zero or a fabricated report', () => {
  const cards = metricCards({ strategy_result: { metrics: { total_return: 0, trade_count: 0, win_rate: null, sharpe_ratio: null, unavailable_reasons: { win_rate: 'no_closed_round_trips', sharpe_ratio: 'zero_daily_volatility' } } } });
  assert.equal(cards[0].value, '0.00%');
  assert.equal(cards[3].value, '无法计算');
  assert.equal(cards[4].value, '0 次');
  assert.equal(cards[5].value, '无法计算');
  assert.equal(cards[5].reason, '尚无已平仓交易。');
  assert.equal(percent(Infinity), '无法计算');
  assert.equal(percent(undefined), '无法计算');
});

test('config validation catches calendar overflow, symbol scope and cents', () => {
  const valid = { symbol: '510300.SH', startDate: '2024-02-29', endDate: '2026-09-18', initialCash: '100000.00' };
  assert.deepEqual(validateConfig(valid), {});
  assert.ok(validateConfig({ ...valid, startDate: '2025-02-29' }).startDate);
  assert.ok(validateConfig({ ...valid, endDate: '2023-01-01' }).endDate);
  assert.ok(validateConfig({ ...valid, symbol: 'BTC' }).symbol);
  assert.ok(validateConfig({ ...valid, initialCash: 0 }).initialCash);
  assert.ok(validateConfig({ ...valid, initialCash: 100.001 }).initialCash);
  assert.deepEqual(configFrom({ symbol: '000001.SZ', start_date: '2025-01-01', end_date: '2025-02-01', initial_cash: 5000 }), { symbol: '000001.SZ', startDate: '2025-01-01', endDate: '2025-02-01', initialCash: 5000 });
});

test('drafts are isolated by account and tolerate corrupt or denied storage', () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  saveDraft(storage, 'owner-A', { idea: '趋势', sessionId: 'session-1', config: { symbol: '000001.SZ' } });
  assert.equal(loadDraft(storage, 'owner-A').idea, '趋势');
  assert.equal(loadDraft(storage, 'owner-B'), null);
  values.set(storageKey('owner-C'), '{broken');
  assert.equal(loadDraft(storage, 'owner-C'), null);
  const denied = { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('full'); } };
  assert.equal(loadDraft(denied, 'owner-A'), null);
  assert.doesNotThrow(() => saveDraft(denied, 'owner-A', {}));
});

test('charts use shared date coordinates and retain negative drawdown semantics', () => {
  const a = [{ date: '2025-01-02', net_value: 1, drawdown: 0 }, { date: '2025-01-03', net_value: .9, drawdown: -.1 }, { date: '2025-01-06', net_value: 1.1, drawdown: 0 }];
  const b = [{ date: '2025-01-02', net_value: 1, drawdown: 0 }, { date: '2025-01-06', net_value: 1, drawdown: 0 }];
  const model = chartModel(a, b);
  assert.equal(model.empty, false);
  assert.equal(model.firstDate, '2025-01-02');
  assert.equal(model.lastDate, '2025-01-06');
  assert.equal(model.strategyPath.split(' ')[0], model.benchmarkPath.split(' ')[0]);
  assert.ok(!model.strategyPath.includes('NaN'));
  const drawdown = chartModel(a, b, 'drawdown');
  assert.ok(drawdown.ticks.some((tick) => tick.label.startsWith('-')));
  assert.equal(chartModel([], []).empty, true);
  assert.equal(chartModel(a.slice(0, 1), b).empty, true);
});

test('API binds cookies and exact workshop body without account authority in payload', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async (url, options) => {
      assert.equal(url, '/desktop/api/strategy-workshop/sessions');
      assert.equal(options.credentials, 'include');
      assert.deepEqual(JSON.parse(options.body), { idea: '趋势', idempotencyKey: 'key1' });
      return new Response(JSON.stringify({ sessionId: 's1' }), { status: 200 });
    };
    assert.deepEqual(await workshopRequest('/sessions', { method: 'POST', body: { idea: '趋势', idempotencyKey: 'key1' } }), { sessionId: 's1' });
  } finally { globalThis.fetch = original; }
});

test('API preserves gate errors and aborts rather than relabeling them as network loss', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = async () => new Response(JSON.stringify({ error: { message: '正式报告暂不可用', code: 'formal_data_gate' } }), { status: 422 });
    await assert.rejects(workshopRequest('/runs'), (error) => error.status === 422 && error.code === 'formal_data_gate' && error.message === '正式报告暂不可用');
    globalThis.fetch = async () => { throw new DOMException('abort', 'AbortError'); };
    await assert.rejects(workshopRequest('/runs'), (error) => error.name === 'AbortError');
  } finally { globalThis.fetch = original; }
});
