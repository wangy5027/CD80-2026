import test from 'node:test';
import assert from 'node:assert/strict';
import { dataMessage, languageStorageKey, languageStyleLabel, loadLanguageStyle, normalizeLanguageStyle, saveLanguageStyle } from './language.js';
import { metricCards } from './format.js';

test('style preferences reject stale values and survive storage access/write failures', () => {
  const old = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
  try {
    assert.equal(normalizeLanguageStyle('professional'), 'beginner');
    assert.equal(normalizeLanguageStyle(null), 'beginner');
    assert.equal(languageStyleLabel(undefined), '原始表达');
    assert.notEqual(languageStorageKey('user:1'), languageStorageKey('user%3A1'));
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new Error('disabled'); } });
    assert.equal(loadLanguageStyle('user'), 'beginner');
    assert.doesNotThrow(() => saveLanguageStyle('user', 'expert'));
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => 'outdated', setItem() { throw new Error('full'); } } });
    assert.equal(loadLanguageStyle('user'), 'beginner');
    assert.doesNotThrow(() => saveLanguageStyle('user', 'expert'));
  } finally {
    if (old) Object.defineProperty(globalThis, 'localStorage', old);
    else delete globalThis.localStorage;
  }
});

test('novice metric explanations preserve financial values and definitions', () => {
  const result = { strategy_result: { metrics: { total_return: .2, annualized_return: .1, max_drawdown: .08, sharpe_ratio: 1.2, trade_count: 2, win_rate: .5 } } };
  const original = JSON.stringify(result);
  const beginner = metricCards(result, 'beginner');
  const expert = metricCards(result, 'expert');
  assert.deepEqual(beginner.map(({ key, value }) => ({ key, value })), expert.map(({ key, value }) => ({ key, value })));
  assert.equal(JSON.stringify(result), original);
  assert.match(beginner.find((x) => x.key === 'max_drawdown').hint, /不是相对本金/);
  assert.match(beginner.find((x) => x.key === 'annualized_return').hint, /不代表未来/);
  assert.match(expert.find((x) => x.key === 'sharpe_ratio').hint, /√252/);
});

test('novice data explanations preserve exact dates and leave unknown or expert messages unchanged', () => {
  const original = '请求开始日 2023-09-18 调整为 2024-08-01（行情覆盖、预热或交易日对齐）。';
  assert.match(dataMessage(original, 'beginner'), /2023-09-18.*2024-08-01/);
  assert.equal(dataMessage(original, 'expert'), original);
  assert.equal(dataMessage('某个新的数据异常', 'beginner'), '某个新的数据异常');
  assert.match(dataMessage('ETF 缺完整分红和复权因子，本次只检验未经复权的价格规则。', 'beginner'), /不能算作完整投资回报/);
});
