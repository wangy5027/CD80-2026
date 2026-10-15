import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, find, all, content, flush } from './assetComponentHarness.mjs';
const CapabilityLibraryPanel = await component('./components/CapabilityLibraryPanel.vue');
const AssetAuthoringPanel = await component('./components/AssetAuthoringPanel.vue');
const SubAgentPanel = await component('./components/SubAgentPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 5; index++) await flush(); };
const candidates = [
  { stepId: 'original-1', type: 'tool', title: '成功固定工具', status: 'succeeded', reconstructable: true, adopted: null },
  { stepId: 'original-2', type: 'code', title: '成功代码', status: 'succeeded', reconstructable: true, inputStepIds: ['original-1'] },
  { stepId: 'original-3', type: 'tool', title: '证据不完整', status: 'succeeded', reconstructable: false, issues: ['实际调用参数已截断，无法恢复。'] },
];
for (const kind of ['app', 'skill', 'tool']) test(`${kind} source selection starts empty and submits explicit original step IDs`, async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, oldWindow = globalThis.window;
  globalThis.document = { activeElement: null }; const calls = [];
  const definition = { format: 'cd80.app/2', name: '所选步骤', inputSchema: { type: 'object', properties: {} }, nodes: [], outputs: {}, rules: [] };
  const draft = { kind, name: '所选步骤', description: '合成来源', manifest: { kind, files: [], permissions: {}, dependencies: [] }, files: [{ path: 'source.py', text: '# complete source\n'.repeat(500) }] };
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null; calls.push({ path, body, method: options.method || 'GET' });
    if (path.endsWith('/creator')) return json({ candidateSteps: candidates, selectedStepIds: body.stepIds || [], selectionRequired: !body.stepIds,
      source: { runId: 'partial-run', status: 'partial', objectiveCompleted: false }, warnings: ['保留真实成功的子步骤'], definition,
      draft: body.stepIds ? draft : null, issues: [] });
    return json({ items: [] });
  };
  const root = hostNode('root'), app = renderer.createApp(kind === 'app' ? AssetAuthoringPanel : CapabilityLibraryPanel,
    { ownerId: 'selection-owner', ...(kind === 'app' ? { captureRunId: 'partial-run' } : { initialCapture: { kind, runId: 'partial-run' } }) });
  try {
    app.mount(root); await settle();
    assert.match(content(root), /原任务未全部完成/); assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /实际调用参数已截断/);
    const choices = all(find(root, node => node.props['aria-label'] === '选择实际步骤'), node => node.tag === 'input' && node.props.type === 'checkbox');
    assert.equal(choices.length, 3); assert.ok(choices.every(node => !node.props.checked)); assert.equal(choices[2].props.disabled, true);
    const submit = () => find(root, node => node.tag === 'button' && content(node) === '按所选步骤整理草稿');
    assert.equal(submit().props.disabled, true); assert.equal(calls.filter(call => call.method === 'POST').length, 1);
    choices[0].props.onChange({ target: { checked: true } }); await flush();
    if (kind !== 'skill') { choices[1].props.onChange({ target: { checked: true } }); await flush(); }
    submit().props.onClick(); await settle();
    assert.deepEqual(calls.filter(call => call.method === 'POST').at(-1).body.stepIds, kind === 'skill' ? ['original-1'] : ['original-1', 'original-2']);
    const save = find(root, node => node.tag === 'button' && content(node) === (kind === 'app' ? '保存应用' : '审阅并保存'));
    assert.ok(save); assert.equal(Boolean(save.props.disabled), false);
    assert.equal(save.parent.props.class, kind === 'app' ? 'asset-save-toolbar' : 'cap-save-toolbar');
    assert.ok(calls.every(call => !/\/verify|\/validation-suites|\/invocations/.test(call.path)));
    const textEditor = find(root, node => node.props['aria-label'] === (kind === 'app' ? '完整应用定义' : '能力草稿内容'));
    textEditor.props['onUpdate:modelValue'](JSON.stringify({ ...(kind === 'app' ? definition : draft), name: '尚未保存的修改' })); await flush();
    globalThis.window = { confirm: () => false };
    submit().props.onClick(); await settle();
    assert.equal(calls.filter(call => call.path.endsWith('/creator')).length, 2, 'declining replacement preserves edited draft without another Creator request');
    assert.match(content(root), /保存/);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; globalThis.window = oldWindow; }
});

test('a partial or legacy task exposes recovery entry points without claiming full completion or requiring a code file', async () => {
  for (const [status, completion, label] of [
    ['succeeded', undefined, '处理已结束'], ['succeeded', { status: 'complete' }, '已完成'],
    ['partial', { status: 'partial', reasons: ['图表尚未交付。'], missingDeliverables: ['图表'] }, '任务未全部完成'],
    ['failed', undefined, '未完成'],
  ]) {
    const captures = [], root = hostNode('root'), run = { id: 'r', kind: 'delegate', status, completion,
      steps: [{ id: 's', type: 'tool', status: 'succeeded', name: 'fixed_tool' }] };
    const app = renderer.createApp(SubAgentPanel, { controller: {}, run, onCapture: (...args) => captures.push(args) });
    try {
      app.mount(root); await flush();
      assert.equal(content(find(root, node => node.tag === 'header')).includes(label), true);
      const capture = find(root, node => node.props['aria-label'] === '保存与复用成果'); assert.ok(capture);
      const buttons = all(capture, node => node.tag === 'button'); assert.equal(buttons.length, 3);
      buttons[2].props.onClick(); assert.deepEqual(captures[0], ['tool', run]);
      if (completion?.status === 'partial') assert.match(content(root), /尚未交付：图表/);
    } finally { app.unmount(); }
  }
});
