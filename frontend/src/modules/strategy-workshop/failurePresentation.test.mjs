import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createRenderer } from 'vue';
import { component, hostNode, content } from '../workbench/assetComponentHarness.mjs';
import { isRecordedFailure, publicValidationTrace, recordedEntryText } from './failurePresentation.js';

const WorkshopConversation = await component('../strategy-workshop/WorkshopConversation.vue');
const node = (tag, text = '') => hostNode(tag, text);
const renderer = createRenderer({
  createElement: tag => node(tag), createText: text => node('#text', text), createComment: text => node('#comment', text),
  setText(target, value) { target.text = value; },
  setElementText(target, value) { target.text = value; target.children = []; },
  patchProp(target, key, _old, value) { target.props[key] = value; },
  parentNode: target => target.parent,
  nextSibling: target => target.parent?.children[target.parent.children.indexOf(target) + 1] || null,
  insert(target, parent, anchor = null) {
    if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1);
    target.parent = parent;
    parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, target);
  },
  remove(target) { if (target.parent) target.parent.children.splice(target.parent.children.indexOf(target), 1); target.parent = null; },
});

test('old assistant failure records display the fixed message without changing stored diagnostics', () => {
  const variants = [
    { role: 'assistant', source: 'failure:match:upstream', text: '上游密钥错误' },
    { role: 'assistant', title: '当前步骤未完成', text: '请求格式不正确' },
    { role: 'assistant', details: { error: { message: '服务端内部异常' } }, text: '请检查后台' },
    { role: 'assistant', source: 'customization-confirm', details: { customization: { status: 'validation_failed', validation: { passed: false, message: '内部规则异常', code: 'bad_rule', errorType: 'ParserError' } } }, text: '代码校验未通过' },
    { role: 'assistant', details: { customization: { validation: { passed: false, message: '上游诊断' } } }, text: '重新生成' },
  ];
  for (const entry of variants) {
    const original = structuredClone(entry);
    assert.equal(isRecordedFailure(entry), true);
    assert.equal(recordedEntryText(entry), '当前网络连接失败');
    assert.deepEqual(entry, original);
  }
});

test('ordinary conversation content and a user discussing errors remain unchanged', () => {
  for (const entry of [
    { role: 'assistant', title: '策略分析', text: '回测中失败率下降。' },
    { role: 'user', title: '用户想法', text: '我想分析连接失败的股票。', details: { error: 'quoted by user' } },
  ]) {
    assert.equal(isRecordedFailure(entry), false);
    assert.equal(recordedEntryText(entry), entry.text);
  }
});

test('historical failure renders only public copy while normal dialogue stays readable', () => {
  const root = node('root');
  const failure = { id: 1, role: 'assistant', stage: 'customization', title: '当前步骤未完成', text: '上游返回了私有错误', details: { error: { code: 'bad_response', message: '内部诊断路径' } } };
  const failedValidation = { id: 3, role: 'assistant', stage: 'validation', title: '规则校验', text: '代码校验未通过', details: { customization: { status: 'validation_failed', summary: '私有失败摘要', validation: { passed: false, message: '上游堆栈', code: 'rule_error', errorType: 'ParserError' } } } };
  const ordinary = { id: 2, role: 'user', stage: 'matching', title: '原始想法', text: '比较两条均线', details: { request: { idea: '比较两条均线' } } };
  const app = renderer.createApp(WorkshopConversation, { conversation: { entries: [failure, failedValidation, ordinary], runs: [] }, busy: false });
  try {
    app.mount(root);
    const copy = content(root);
    assert.match(copy, /当前网络连接失败/);
    assert.match(copy, /比较两条均线/);
    assert.doesNotMatch(copy, /上游返回了私有错误|内部诊断路径|bad_response|私有失败摘要|上游堆栈|rule_error|ParserError/);
  } finally { app.unmount(); }
});

test('a failed validation trace never reaches the view; a passed trace is preserved', () => {
  const failed = { validation: { passed: false, message: 'private stack', errorType: 'ImportError' }, originalIdea: 'user idea' };
  const passed = { validation: { passed: true, sampleCases: [1, 2] }, originalIdea: 'user idea' };
  assert.equal(publicValidationTrace(failed), null);
  assert.equal(publicValidationTrace({ validation: { message: 'missing pass' } }), null);
  assert.equal(publicValidationTrace(passed), passed);
});
