import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, find, all, content, flush, submitEvent } from './assetComponentHarness.mjs';

const AssetDefinitionView = await component('./components/AssetDefinitionView.vue');
const AssetAppRun = await component('./components/AssetAppRun.vue');
const AssetAuthoringPanel = await component('./components/AssetAuthoringPanel.vue');
const CapabilityValidationSuite = await component('./components/CapabilityValidationSuite.vue');
const AssetConnectionBindings = await component('./components/AssetConnectionBindings.vue');
const definition = { format: 'cd80.app/2', name: '价格方法', description: '两个输入独立验证', inputSchema: { type: 'object', properties: { prices: { type: 'array', items: { type: 'number' } } }, required: ['prices'] }, rules: ['不补造行情'], nodes: [{ id: 'calculate', kind: 'code', title: '计算', code: 'def run(payload):\n    return {"change":0.08}\n# <script>bad()</script>', inputBindings: { prices: { from: 'input', path: 'prices' } }, provenance: { type: 'historical', runId: 'run-original', stepId: 'step-original' }, delivery: { schema: { type: 'object', required: ['change'] }, files: [] }, checkpoint: true }], outputs: { analysis: { from: 'step', stepId: 'calculate', path: '' } }, gaps: [] };

test('actual step details show complete source as text, provenance and explicit parameter bindings', async () => {
  const root = hostNode('root'); const app = renderer.createApp(AssetDefinitionView, { definition });
  try {
    app.mount(root); await flush();
    assert.match(content(root), /历史实际执行/); assert.match(content(root), /本次输入 · prices/); assert.match(content(root), /run-original/); assert.match(content(root), /<script>bad/);
    assert.equal(find(root, node => node.tag === 'script' || Object.hasOwn(node.props, 'innerHTML')), undefined);
    assert.match(content(root), /完成后等待你的检查/);
  } finally { app.unmount(); }
});

test('actual source editor preserves old definition and explicitly marks modified code', async () => {
  const oldDocument = globalThis.document; globalThis.document = { activeElement: null };
  const root = hostNode('root'), changes = []; const app = renderer.createApp(AssetDefinitionView, { definition, editable: true, 'onUpdate:definition': value => changes.push(value) });
  try {
    app.mount(root); await flush();
    find(root, node => node.props['aria-label'] === 'calculate 实现源码').props.onInput({ target: { value: 'def run(payload):\n    return payload' } });
    assert.equal(changes[0].nodes[0].provenance.type, 'modified'); assert.equal(changes[0].nodes[0].provenance.runId, 'run-original');
    assert.match(definition.nodes[0].code, /change/); assert.equal(definition.nodes[0].provenance.type, 'historical');
  } finally { app.unmount(); globalThis.document = oldDocument; }
});

test('a Skill question resumes its own question identity without modifying original App inputs', async () => {
  const oldDocument = globalThis.document; globalThis.document = { activeElement: null };
  const root = hostNode('root'), submissions = [];
  const run = { id: 'run', versionId: 'v1', revision: 8, status: 'awaiting_input', input: { prices: [100, 120] }, steps: [], waiting: { kind: 'skill_question', question: { id: 'q1', prompt: '采用哪个数据口径？', revision: 3 } } };
  const app = renderer.createApp(AssetAppRun, { run, definition, onResume: value => submissions.push(value) });
  try {
    app.mount(root); await flush(); assert.match(content(root), /采用哪个数据口径/); assert.doesNotMatch(content(root), /补充本次输入/);
    const answer = find(root, node => node.props['aria-label'] === '回答 Skill 问题'); answer.props['onUpdate:modelValue']('使用明确提供的日线收盘价'); await flush();
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.deepEqual(submissions, [{ revision: 8, skillAnswer: { questionId: 'q1', revision: 3, answer: '使用明确提供的日线收盘价' } }]);
  } finally { app.unmount(); globalThis.document = oldDocument; }
});

test('checkpoint continuation is explicit, sends current revision and never triggers a new run', async () => {
  const root = hostNode('root'), submissions = [];
  const app = renderer.createApp(AssetAppRun, { run: { id: 'checkpoint', revision: 4, status: 'awaiting_confirmation', input: {}, steps: [], waiting: { nodeId: 'calculate', reason: '检查数值后继续' } }, definition, onResume: value => submissions.push(value) });
  try {
    app.mount(root); await flush(); assert.equal(submissions.length, 0);
    assert.match(content(root), /检查完成，继续下一步/); assert.match(content(root), /检查数值后继续/); find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.deepEqual(submissions, [{ revision: 4 }]);
  } finally { app.unmount(); }
});

test('failed App steps hide backend diagnostics while successful step output and approval reason remain visible', async () => {
  const root = hostNode('root');
  const run = { id: 'failed-run', versionId: 'v1', status: 'failed', revision: 2,
    error: { message: 'PRIVATE_TOP_ERROR' }, result: { traceback: 'PRIVATE_FINAL_TRACE' },
    steps: [
      { nodeId: 'calculate', status: 'succeeded', resolvedInput: { prices: [1, 2] }, result: { change: 1 } },
      { nodeId: 'broken', status: 'failed', error: { message: 'PRIVATE_STEP_ERROR' }, result: { traceback: 'PRIVATE_STEP_TRACE' } },
    ], waiting: { reason: '请核对已授予的文件范围' } };
  const app = renderer.createApp(AssetAppRun, { run, definition });
  try {
    app.mount(root); await flush();
    const view = content(root);
    assert.match(view, /当前网络连接失败/);
    assert.match(view, /"change": 1/);
    assert.doesNotMatch(view, /请核对已授予的文件范围/);
    assert.doesNotMatch(view, /PRIVATE_TOP_ERROR|PRIVATE_FINAL_TRACE|PRIVATE_STEP_ERROR|PRIVATE_STEP_TRACE/);
  } finally { app.unmount(); }
});

test('failed verification case hides full evidence object, successful case remains reviewable', async () => {
  const root = hostNode('root');
  const verification = { status: 'failed', cases: [
    { label: '成功用例', status: 'passed', result: { value: 7 } },
    { label: '失败用例', status: 'failed', error: { traceback: 'PRIVATE_CASE_TRACE' }, result: { output: 'PRIVATE_RESULT' } },
  ] };
  const app = renderer.createApp(CapabilityValidationSuite, { kind: 'skill', allowed: true, verification });
  try {
    app.mount(root); await flush();
    const view = content(root);
    assert.match(view, /当前网络连接失败/);
    assert.match(view, /value/);
    assert.doesNotMatch(view, /PRIVATE_CASE_TRACE|PRIVATE_RESULT/);
  } finally { app.unmount(); }
});

test('saved Skill original case is prefilled and is the only case submitted by default', async () => {
  const root = hostNode('root'), submissions = [];
  const original = { label: '原任务用例', input: { prices: [1, 3] }, fileIds: ['f1'],
    expectation: { mode: 'manual_review', prompt: '核对价格', criteria: '准确指出涨幅' } };
  const app = renderer.createApp(CapabilityValidationSuite, { kind: 'skill', allowed: true, inputFiles: true,
    files: [{ id: 'f1', name: '资料' }], testCases: [original], onVerify: value => submissions.push(value) });
  try {
    app.mount(root); await flush();
    assert.equal(find(root, node => node.props['aria-label'] === '原任务用例 · 任务').value, '核对价格');
    assert.equal(find(root, node => node.props['aria-label'] === '原任务用例 · 验收标准').value, '准确指出涨幅');
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.deepEqual(submissions, [[original]]);
    find(root, node => node.props['aria-label'] === '原任务用例 · 任务').props['onUpdate:modelValue']('临时修改'); await flush();
    find(root, node => node.tag === 'button' && content(node) === '一键填入原用例').props.onClick(); await flush();
    assert.equal(find(root, node => node.props['aria-label'] === '原任务用例 · 任务').value, '核对价格');
    find(root, node => node.tag === 'button' && content(node) === '从原用例填入').props.onClick(); await flush();
    assert.equal(find(root, node => node.props['aria-label'] === '适用的新输入 · 任务').value, '核对价格');
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.equal(submissions.at(-1).length, 1, 'copying a second case does not opt it into execution');
  } finally { app.unmount(); }
});

test('an incomplete saved Skill candidate asks for independent criteria before validation', async () => {
  const root = hostNode('root'), submissions = [];
  const app = renderer.createApp(CapabilityValidationSuite, { kind: 'skill', allowed: true,
    testCases: [{ label: '原任务用例', input: {}, fileIds: [], expectation: { mode: 'manual_review', prompt: '分析数据', criteria: '' } }],
    onVerify: value => submissions.push(value) });
  try {
    app.mount(root); await flush();
    assert.match(content(root), /独立预期尚待补全/);
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.equal(submissions.length, 0);
  } finally { app.unmount(); }
});

test('Skill validation requires distinct actual tasks and keeps each case file grant explicit', async () => {
  const oldDocument = globalThis.document; globalThis.document = { activeElement: null };
  const root = hostNode('root'), submissions = [];
  const app = renderer.createApp(CapabilityValidationSuite, { kind: 'skill', allowed: true, inputFiles: true, files: [{ id: 'f1', name: '本次资料' }], onVerify: value => submissions.push(value) });
  try {
    app.mount(root); await flush(); assert.equal(submissions.length, 0);
    const fill = (label,value) => find(root, node => node.props['aria-label'] === label).props['onUpdate:modelValue'](value);
    fill('原用例 · 任务','分析原资料'); fill('原用例 · 验收标准','可核对原结论');
    fill('适用的新输入 · 任务','分析原资料'); fill('适用的新输入 · 验收标准','可核对新结论');
    find(find(root, node => node.tag === 'label' && content(node).includes('将这个不同输入用例加入本次验证')), node => node.tag === 'input').props['onUpdate:modelValue'](true);
    await flush(); find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.equal(submissions.length, 0); assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /新用例必须使用不同的实际输入或任务/);
    fill('适用的新输入 · 任务','分析不同的新资料'); await flush();
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.equal(submissions.length, 1); assert.equal(submissions[0].length, 2);
    assert.deepEqual(submissions[0].map(item => item.fileIds), [[], []]);
    assert.deepEqual(submissions[0][1].expectation, { mode: 'manual_review', prompt: '分析不同的新资料', criteria: '可核对新结论' });
  } finally { app.unmount(); globalThis.document = oldDocument; }
});

test('Skill cases send independent structured data and constraints without requiring a different task', async () => {
  const oldDocument = globalThis.document; globalThis.document = { activeElement: null };
  const root = hostNode('root'), submissions = [], app = renderer.createApp(CapabilityValidationSuite, { kind: 'skill', allowed: true, onVerify: value => submissions.push(value) });
  try {
    app.mount(root); await flush();
    const fill = (label,value) => find(root, node => node.props['aria-label'] === label).props['onUpdate:modelValue'](value);
    for (const label of ['原用例','适用的新输入']) { fill(label + ' · 任务','分析给定价格'); fill(label + ' · 验收标准','按输入核对变化率'); }
    find(find(root, node => node.tag === 'label' && content(node).includes('将这个不同输入用例加入本次验证')), node => node.tag === 'input').props['onUpdate:modelValue'](true);
    fill('原用例 · 结构化输入','{"prices":[100,108],"constraints":{"currency":"CNY"}}');
    fill('适用的新输入 · 结构化输入','[]'); await flush();
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.equal(submissions.length, 0); assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /实际输入需要是 JSON 对象/);
    fill('适用的新输入 · 结构化输入','{"prices":[80,120],"constraints":{"currency":"CNY"}}'); await flush();
    find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.deepEqual(submissions[0].map(row => row.input.prices), [[100,108],[80,120]]);
    assert.equal(submissions[0][1].input.constraints.currency, 'CNY');
  } finally { app.unmount(); globalThis.document = oldDocument; }
});

test('Tool suites support independent objective assertions and reject an empty rule list', async () => {
  const oldDocument = globalThis.document; globalThis.document = { activeElement: null };
  const root = hostNode('root'), submissions = [], app = renderer.createApp(CapabilityValidationSuite, { kind: 'tool', allowed: true, inputSchema: { type: 'object', properties: { limit: { type: 'integer' } }, required: ['limit'], additionalProperties: false }, onVerify: value => submissions.push(value) });
  try {
    app.mount(root); await flush();
    const numbers = all(root, node => node.tag === 'input' && node.props.type === 'number');
    numbers[0].props.onInput({ target: { value: '5' } }); numbers[1].props.onInput({ target: { value: '3' } });
    find(find(root, node => node.tag === 'label' && content(node).includes('将这个不同输入用例加入本次验证')), node => node.tag === 'input').props['onUpdate:modelValue'](true);
    for (const label of ['原用例','适用的新输入']) find(root, node => node.props['aria-label'] === label + ' · 检查方式').props['onUpdate:modelValue']('assertions');
    await flush(); find(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await flush();
    assert.equal(submissions.length, 0); assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /至少填写一条字段或业务断言/);
    const rules = [{ path: ['records'], op: 'minItems', value: 3 }];
    for (const label of ['原用例','适用的新输入']) find(root, node => node.props['aria-label'] === label + ' · 断言').props['onUpdate:modelValue'](JSON.stringify(rules));
    await flush(); find(root, node => node.tag === 'form').props.onSubmit(submitEvent());
    assert.deepEqual(submissions[0].map(row => row.input.limit), [5,3]);
    assert.deepEqual(submissions[0].map(row => row.expectation), [{ mode: 'assertions', assertions: rules }, { mode: 'assertions', assertions: rules }]);
  } finally { app.unmount(); globalThis.document = oldDocument; }
});

test('connection selector leaves choices explicit, explains missing sources and preserves unavailable selection visibly', async () => {
  const root = hostNode('root'), updates = [];
  const app = renderer.createApp(AssetConnectionBindings, { requirements: [{ sourceIdentity: 'data/source', name: '外部数据', nodeIds: ['fetch'], options: [{ connectionId: 'mine', name: '我的连接', configured: true, enabled: true }, { connectionId: 'disabled', name: '已禁用连接', configured: true, enabled: false }] }, { sourceIdentity: 'missing', name: '未配置来源', nodeIds: ['second'], options: [] }], modelValue: { 'data/source': 'old' }, 'onUpdate:modelValue': value => updates.push(value) });
  try {
    app.mount(root); await flush(); assert.equal(updates.length, 0);
    assert.match(content(root), /此前选择的连接现在不可用/); assert.match(content(root), /尚无这个来源的个人连接/);
    assert.equal(find(root, node => node.tag === 'option' && node.props.value === 'disabled').props.disabled, true);
    find(root, node => node.props['aria-label'] === '连接 · data/source').props.onChange({ target: { value: 'mine' } });
    assert.deepEqual(updates, [{ 'data/source': 'mine' }]);
  } finally { app.unmount(); }
});

test('actual creator panel saves without preview or tests, and keeps a failed save editable', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, oldStorage = globalThis.localStorage;
  globalThis.document = { activeElement: null }; const storage = new Map(); globalThis.localStorage = { getItem: key => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,value), removeItem: key => storage.delete(key) };
  const calls = []; let failSave = true;
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null; calls.push({ path, method: options.method || 'GET', body });
    if (path.endsWith('/preview')) return new Response(JSON.stringify({ definition: body.definition, diff: [], issues: [] }), { headers: { 'Content-Type': 'application/json' } });
    if (options.method === 'POST' && path === '/desktop/api/asset-apps') {
      if (failSave) { failSave = false; return new Response(JSON.stringify({ error: '暂时无法保存' }), { status: 503, headers: { 'Content-Type': 'application/json' } }); }
      return new Response(JSON.stringify({ id: 'a', versionId: 'v', revision: 1, definition, name: definition.name, versions: [{ versionId: 'v' }], verification: { status: 'unverified', cases: [] } }), { headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } });
  };
  const root = hostNode('root'), app = renderer.createApp(AssetAuthoringPanel, { ownerId: 'component-owner' });
  try {
    app.mount(root); await flush(); assert.ok(calls.every(call => call.method === 'GET'));
    find(root, node => node.tag === 'button' && content(node) === '新建应用').props.onClick(); await flush();
    find(root, node => node.props['aria-label'] === '完整应用定义').props['onUpdate:modelValue'](JSON.stringify(definition)); await flush();
    const saveButton = () => find(root, node => node.tag === 'button' && content(node).includes('确认并保存新版本'));
    assert.equal(Boolean(saveButton().props.disabled), false); saveButton().props.onClick(); await flush();
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /暂时无法保存/); assert.ok(find(root, node => node.props['aria-label'] === '完整应用定义')); assert.equal(Boolean(saveButton().props.disabled), false);
    saveButton().props.onClick(); await flush(); assert.match(content(root), /应用已保存/); assert.match(content(root), /已保存 · 未验证/);
    assert.equal(calls.filter(call => /\/runs|\/verify|\/creator|\/preview/.test(call.path)).length, 0);
    assert.equal(find(root, node => node.props['aria-label'] === '验证输入 1'), undefined);
    assert.equal(all(root, node => node.tag === 'button' && /启用此版本|填写新输入运行/.test(content(node))).every(node => node.props.disabled === true), true);
    find(root, node => node.tag === 'button' && content(node) === '返回我的应用').props.onClick(); await flush();
    assert.equal(find(root, node => node.props['aria-label'] === '应用已保存'), undefined);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; globalThis.localStorage = oldStorage; }
});

test('refreshing the same App version preserves in-progress user input', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  const detail = { id: 'refresh-app', versionId: 'v1', enabledVersionId: 'v1', revision: 1, name: definition.name, definition, versions: [{ id: 'v1', number: 1 }, { id: 'v0', number: 0 }], verification: { status: 'passed', cases: [] } };
  globalThis.fetch = async path => new Response(JSON.stringify(path.endsWith('/legacy') || path.endsWith('/runs') ? { items: [] } : path.endsWith('/refresh-app') || path.includes('/refresh-app?') ? detail : { items: [detail] }), { headers: { 'Content-Type': 'application/json' } });
  const root = hostNode('root'), app = renderer.createApp(AssetAuthoringPanel, { ownerId: 'refresh-owner' });
  try {
    app.mount(root); await flush();
    find(root, node => node.tag === 'button' && node.props.class === 'asset-library-item').props.onClick(); await flush();
    assert.deepEqual(all(root, node => node.tag === 'option').map(node => node.props.value), ['v1', 'v0']);
    find(root, node => node.tag === 'button' && content(node) === '填写新输入运行').props.onClick(); await flush();
    const form = find(root, node => node.props.class === 'asset-new-run');
    const input = find(form, node => node.tag === 'textarea'); input.props['onUpdate:modelValue']('[83,120,101]'); await flush();
    find(root, node => node.tag === 'button' && content(node) === '刷新状态').props.onClick(); await flush();
    const preserved = find(root, node => node.props.class === 'asset-new-run'); assert.ok(preserved);
    assert.equal(find(preserved, node => node.tag === 'textarea').value, '[83,120,101]');
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('APP verification keeps edited original on refresh and resets on exact version change', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  const detail = versionId => ({ id: 'case-app', versionId, revision: 1, name: definition.name, definition,
    versions: [{ versionId: 'v1', number: 1 }, { versionId: 'v2', number: 2 }],
    testCases: [{ label: '原任务用例', input: { prices: [versionId === 'v1' ? 1 : 2] }, fileBindings: {},
      expectation: { mode: 'json_equals', value: { result: versionId } } }], verification: { status: 'unverified', cases: [] } });
  globalThis.fetch = async path => {
    if (path.includes('/asset-apps/case-app?')) return new Response(JSON.stringify(detail(path.includes('versionId=v2') ? 'v2' : 'v1')), { headers: { 'Content-Type': 'application/json' } });
    return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } });
  };
  const root = hostNode('root'), app = renderer.createApp(AssetAuthoringPanel, { ownerId: 'case-owner', initialAppId: 'case-app', initialVersionId: 'v1', poolDetail: true });
  try {
    app.mount(root); await flush(); await flush();
    find(root, node => node.tag === 'button' && content(node) === '测试此版本').props.onClick(); await flush();
    const input = () => find(root, node => node.props['aria-label'] === '验证输入 1');
    assert.equal(input().value, '{\n  "prices": [\n    1\n  ]\n}');
    input().props['onUpdate:modelValue']('{"prices":[99]}'); await flush();
    find(root, node => node.tag === 'button' && content(node) === '刷新状态').props.onClick(); await flush(); await flush();
    assert.equal(input().value, '{"prices":[99]}');
    find(root, node => node.tag === 'button' && content(node) === '一键填入原用例').props.onClick(); await flush();
    assert.match(input().value, /\b1\b/);
    find(root, node => node.tag === 'select' && node.parent?.props.class === 'asset-version').props.onChange({ target: { value: 'v2' } }); await flush(); await flush();
    find(root, node => node.tag === 'button' && content(node) === '测试此版本').props.onClick(); await flush();
    assert.match(input().value, /\b2\b/);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('pool App detail retains management and returns to the pool without opening another app list', async () => {
  const old = globalThis.fetch, requests = [], back = [], authored = [];
  globalThis.fetch = async (path, options = {}) => {
    requests.push([path, options.method || 'GET']);
    const value = path.includes('/asset-apps/pool-app?') ? { id: 'pool-app', versionId: 'saved-v1', revision: 1, definition, name: definition.name, versions: [{ versionId: 'saved-v1' }], verification: { status: 'unverified', cases: [] } } : { items: [] };
    return new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
  };
  const root = hostNode('root'), app = renderer.createApp(AssetAuthoringPanel, { ownerId: 'pool-detail-owner', initialAppId: 'pool-app', initialVersionId: 'saved-v1', poolDetail: true, unifiedAuthoring: true, onBack: () => back.push(true), onAuthor: value => authored.push(value) });
  try {
    app.mount(root); await flush(); await flush();
    assert.ok(requests.some(([path]) => path.includes('/asset-apps/pool-app?versionId=saved-v1')));
    assert.match(content(root), /已保存 · 未验证/);
    assert.doesNotMatch(content(root), /把一次成果，变成完整的方法/);
    for (const name of ['测试此版本', '启用此版本', '填写新输入运行']) assert.ok(find(root, node => node.tag === 'button' && content(node) === name));
    find(root, node => node.tag === 'button' && content(node) === '编辑方法').props.onClick();
    assert.equal(authored[0].target.assetId, 'pool-app');
    assert.equal(authored[0].target.baseVersionId, 'saved-v1');
    find(root, node => node.tag === 'button' && content(node) === '← 我的 APP').props.onClick();
    assert.deepEqual(back, [true]);
    assert.equal(requests.some(([,method]) => method === 'POST'), false, 'viewing detail does not verify, enable or run');
  } finally { app.unmount(); globalThis.fetch = old; }
});

test('failed pool detail read retains its exact version for retry and never shows a duplicate app library', async () => {
  const old = globalThis.fetch, opened = [], reads = [];
  let failed = true;
  globalThis.fetch = async path => {
    if (path.includes('/asset-apps/retry-app?')) {
      reads.push(path);
      if (failed) return new Response(JSON.stringify({ error: 'offline' }), { status: 503, headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({ id: 'retry-app', versionId: 'precise-v2', definition, verification: { status: 'unverified' } }), { headers: { 'Content-Type': 'application/json' } });
    }
    return new Response(JSON.stringify({ items: [] }), { headers: { 'Content-Type': 'application/json' } });
  };
  const root = hostNode('root'), app = renderer.createApp(AssetAuthoringPanel, { ownerId: 'retry-owner', initialAppId: 'retry-app', initialVersionId: 'precise-v2', poolDetail: true, onInitialOpened: () => opened.push(true) });
  try {
    app.mount(root); await flush(); await flush();
    assert.deepEqual(opened, []);
    assert.match(content(root), /当前网络连接失败/);
    assert.doesNotMatch(content(root), /从一次成功任务，保存第一套方法/);
    failed = false;
    find(root, node => node.tag === 'button' && content(node) === '重新读取状态').props.onClick();
    await flush(); await flush();
    assert.match(content(root), /价格方法/);
    assert.equal(reads.length, 2);
    assert.ok(reads.every(path => path.includes('versionId=precise-v2')));
  } finally { app.unmount(); globalThis.fetch = old; }
});
