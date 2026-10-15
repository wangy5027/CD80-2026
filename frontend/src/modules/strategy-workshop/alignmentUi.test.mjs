import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick } from 'vue';
import { component, hostNode, all, content } from '../workbench/assetComponentHarness.mjs';

const StrategyWorkshop = await component('../strategy-workshop/StrategyWorkshop.vue');
const node = (tag, text = '') => Object.assign(hostNode(tag, text), { querySelector: () => null });
const renderer = createRenderer({
  createElement: tag => node(tag), createText: text => node('#text', text), createComment: text => node('#comment', text),
  setText: (target, text) => { target.text = text; },
  setElementText: (target, text) => { target.text = text; target.children = []; },
  patchProp: (target, key, _previous, value) => { target.props[key] = value; target[key] = value; },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] || null,
  insert(target, parent, anchor = null) {
    if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1);
    target.parent = parent;
    parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, target);
  },
  remove(target) { if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1); target.parent = null; },
});

const response = value => new Response(JSON.stringify(value), { status: 200 });
const candidate = { strategyId: 'sma_trend', name: '双均线', version: '1.0.0', selectionDigest: 'selection-hash' };
async function rendered(customization, { savedPersonalApp = null, matchedCandidate = candidate, expected = customization?.summary } = {}) {
  const oldFetch = globalThis.fetch;
  const oldStorage = globalThis.localStorage;
  const root = node('root');
  const session = { sessionId: 's1', idea: '我的原始策略', status: 'customizing', matchRevision: 1,
    candidate: matchedCandidate, baseCandidate: matchedCandidate, selectionApproval: null, customization, savedPersonalApp };
  globalThis.localStorage = { getItem: () => null, setItem() {} };
  globalThis.fetch = async (url, options = {}) => {
    assert.notEqual(options.method, 'POST', 'Rendering an existing alignment must not submit or generate anything');
    if (url.endsWith('/capabilities')) return response({ defaults: {} });
    if (url.endsWith('/symbols')) return response({ symbols: [] });
    if (url.endsWith('/strategies')) return response({ strategies: [] });
    if (url.endsWith('/sessions')) return response({ sessions: [] });
    if (url.endsWith('/sessions/current')) return response({ session });
    return response({});
  };
  const app = renderer.createApp(StrategyWorkshop, { ownerKey: 'owner-A' });
  try {
    app.mount(root);
    for (let attempt = 0; attempt < 30 && !content(root).includes(expected); attempt++) {
      await new Promise(resolve => setImmediate(resolve));
      await nextTick();
    }
    assert.ok(content(root).includes(expected));
    return { root, cleanup() { app.unmount(); globalThis.fetch = oldFetch; globalThis.localStorage = oldStorage; } };
  } catch (error) {
    app.unmount(); globalThis.fetch = oldFetch; globalThis.localStorage = oldStorage;
    throw error;
  }
}
const actions = (root, action) => all(root, value => value.props?.['data-action'] === action);

test('one-time alignment shows questions and assumptions before the single answer action', async () => {
  const view = await rendered({ id: 'c0', status: 'needs_clarification', summary: '请补充退出条件',
    questions: ['何时退出？'], assumptions: ['默认次日开盘模拟成交'], alignmentCompleted: false });
  try {
    const copy = content(view.root);
    assert.match(copy, /请一次确认以下条件/);
    assert.match(copy, /何时退出？/);
    assert.match(copy, /默认次日开盘模拟成交/);
    assert.match(copy, /提交后会直接整理完整规则/);
    assert.equal(actions(view.root, 'answer-alignment').length, 1);
    assert.equal(actions(view.root, 'confirm-rules').length, 0);
  } finally { view.cleanup(); }
});

test('reviewable and validated rules put explicit progress actions before optional revision', async () => {
  for (const status of ['rules_ready', 'validated']) {
    const view = await rendered({ id: 'c1', status, summary: '已形成完整规则', rulePlan: { name: '新策略' }, rulesDigest: 'rules-hash',
      assumptions: ['按日线交易'], alignmentCompleted: true, ...(status === 'validated' ? { validation: { passed: true } } : {}) });
    try {
      const copy = content(view.root);
      assert.match(copy, /还想调整规则？（可选）/);
      assert.doesNotMatch(copy, /请一次回答上面的问题/);
      assert.equal(actions(view.root, 'answer-alignment').length, 0);
      assert.equal(actions(view.root, status === 'rules_ready' ? 'confirm-rules' : 'accept-custom').length, 1);
      if (status === 'rules_ready') assert.match(copy, /确认并实现新策略/);
    } finally { view.cleanup(); }
  }
});

test('terminal alignment presents concrete issues and no retry or code approval', async () => {
  const view = await rendered({ id: 'c2', status: 'alignment_incomplete', summary: '退出条件仍不明确',
    alignmentCompleted: true, questions: [], alignmentIssues: ['无法确定卖出时机'] });
  try {
    const copy = content(view.root);
    assert.match(copy, /本次未能生成可执行的新策略/);
    assert.match(copy, /无法确定卖出时机/);
    assert.match(copy, /返回原方案/);
    for (const action of ['answer-alignment', 'rewrite-rules', 'confirm-rules', 'accept-custom']) assert.equal(actions(view.root, action).length, 0);
  } finally { view.cleanup(); }
});

test('saved custom strategy displays readable rule summary and keeps exact version inside snapshot', async () => {
  const versionId = 'rules_' + 'a'.repeat(64);
  const view = await rendered({ status: 'rules_ready', summary: '描述方案', rulePlan: {
    name: '自定义策略', entry: { type: 'compare', op: 'gt', left: { type: 'field', name: 'close' }, right: { type: 'const', value: 10 } },
    exit: { type: 'compare', op: 'lt', left: { type: 'field', name: 'close' }, right: { type: 'const', value: 8 } },
  }, ruleSummary: { entry: '收盘价高于 10 元时买入', exit: '收盘价低于 8 元时退出' } }, {
    savedPersonalApp: { id: 'saved-custom', versionId, name: '自定义策略', verification: { status: 'unverified' } },
    expected: '03 / 已保存策略',
  });
  try {
    const receipt = all(view.root, value => Object.hasOwn(value.props || {}, 'data-strategy-saved-receipt'))[0];
    const facts = all(view.root, value => value.tag === 'dl' && value.props?.class === 'sw-facts')[0];
    const snapshot = all(view.root, value => value.tag === 'details' && content(value).includes('查看已保存的规则和代码快照'))[0];
    assert.match(content(receipt), /策略已保存，尚未验证/);
    assert.doesNotMatch(content(receipt), /rules_[a-f0-9]{64}/);
    assert.match(content(facts), /收盘价高于 10 元时买入/);
    assert.match(content(facts), /收盘价低于 8 元时退出/);
    assert.doesNotMatch(content(facts), /\[object Object\]|rules_[a-f0-9]{64}/);
    assert.match(content(snapshot), new RegExp(versionId));
  } finally { view.cleanup(); }
});

test('saved template strategy displays its candidate rules without an object dump', async () => {
  const view = await rendered(null, {
    matchedCandidate: { ...candidate, rules: ['收盘价突破过去 20 天高点时买入', '跌破过去 10 天低点时退出'] },
    savedPersonalApp: { id: 'saved-template', versionId: 'rules-template', name: '通道突破', verification: { status: 'unverified' } },
    expected: '03 / 已保存策略',
  });
  try {
    const facts = all(view.root, value => value.tag === 'dl' && value.props?.class === 'sw-facts')[0];
    assert.match(content(facts), /收盘价突破过去 20 天高点时买入/);
    assert.match(content(facts), /跌破过去 10 天低点时退出/);
    assert.doesNotMatch(content(facts), /\[object Object\]/);
  } finally { view.cleanup(); }
});
