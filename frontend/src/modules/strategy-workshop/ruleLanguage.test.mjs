import test from 'node:test';
import assert from 'node:assert/strict';
import { describeRulePlan, templateRules } from './ruleLanguage.js';

test('plain rule wording keeps inequalities, volume input and history shifts without mutating rules', () => {
  const plan = { entry: { type: 'compare', op: 'gte', left: { type: 'field', name: 'volume' }, right: { type: 'arithmetic', op: 'mul', args: [{ type: 'const', value: 1.5 }, { type: 'indicator', name: 'sma', period: 20, lag: 1, input: { type: 'field', name: 'volume' } }] } }, exit: { type: 'compare', op: 'lt', left: { type: 'indicator', name: 'rsi', period: 14 }, right: { type: 'const', value: 25 } } };
  const before = JSON.stringify(plan);
  const original = { name: '原始名称', summary: '模型原文', entry: 'original entry', exit: 'original exit', warmup_bars: 21 };
  const plain = describeRulePlan(plan, 'beginner', original);
  assert.match(plain.entry, /高于或等于/);
  assert.match(plain.entry, /1\.5/);
  assert.match(plain.entry, /1个交易日前/);
  assert.match(plain.entry, /20个交易日.*成交的数量/);
  assert.match(plain.exit, /低于〔25〕/);
  assert.equal(plain.summary, '模型原文');
  assert.equal(plain.warmup_bars, 21);
  assert.equal(JSON.stringify(plan), before);
  assert.equal(describeRulePlan(plan, 'expert', original), original);
});

test('crossing and target state descriptions do not imply simple comparison or actual fills', () => {
  const plan = { entry: { type: 'cross_above', left: { type: 'field', name: 'close' }, right: { type: 'const', value: 100 } }, exit: { type: 'position', value: 1 } };
  const plain = describeRulePlan(plan, 'beginner');
  assert.match(plain.entry, /上一交易日不高于.*今天变成高于/);
  assert.match(plain.exit, /不代表已经成交/);
  assert.match(plain.semantics.join(''), /同时满足.*先按卖出/);
});

test('unknown old rules keep original text and fixed template wording preserves boundary conditions', () => {
  const original = { entry: '原本已保存的条件', exit: '原本已保存的退出条件' };
  assert.equal(describeRulePlan({ entry: { type: 'future_rule' }, exit: { type: 'position', value: 0 } }, 'beginner', original), original);
  assert.match(templateRules('rsi_reversion', 'beginner', []).join(''), /不高于30.*不低于70/);
  assert.match(templateRules('donchian_breakout', 'beginner', []).join(''), /不把今天算进去/);
  assert.match(templateRules('chandelier_trend', 'beginner', []).join(''), /可能下降/);
  const raw = ['old archived rules'];
  assert.equal(templateRules('custom_xyz', 'beginner', raw), raw);
  assert.equal(templateRules('sma_trend', 'expert', raw), raw);
});
