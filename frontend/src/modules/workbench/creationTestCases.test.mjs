import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, find, content, flush } from './assetComponentHarness.mjs';

const AppCreatePanel = await component('./components/AppCreatePanel.vue');
const CapabilityCreatePanel = await component('./components/CapabilityCreatePanel.vue');
const OriginalTestCaseEditor = await component('./components/OriginalTestCaseEditor.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 5; index++) await flush(); };
const set = (root, label, value) => {
  const node = find(root, item => item.props['aria-label'] === label);
  assert.ok(node, label);
  node.props['onUpdate:modelValue'](value);
};

test('creation case editor preserves a previewed original until changed and checks its saved shape', async () => {
  const root = hostNode('root');
  const initialCase = { label: '原任务用例', input: { values: [1] }, fileBindings: { data: 'file-1' }, expectation: null };
  const app = renderer.createApp(OriginalTestCaseEditor, { kind: 'app', initialCase });
  try {
    app.mount(root); await flush();
    assert.equal(app._instance.exposed.snapshot(), null);
    set(root, '保存原任务输入', '{"values":[1,2]}');
    set(root, '保存独立预期', '{"mode":"json_equals","value":{"total":3}}'); await flush();
    assert.deepEqual(app._instance.exposed.snapshot(), {
      label: '原任务用例', input: { values: [1,2] }, fileBindings: { data: 'file-1' },
      expectation: { mode: 'json_equals', value: { total: 3 } },
    });
    set(root, '保存原任务输入', '[]'); await flush();
    assert.throws(() => app._instance.exposed.snapshot(), /JSON 对象/);
  } finally { app.unmount(); }
});

test('APP file creation submits an edited original case separately and never runs it', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, requests = [], saved = [];
  globalThis.document = { activeElement: null };
  const initialCase = { label: '原任务用例', input: { values: [1] }, fileBindings: {}, expectation: null };
  const definition = { format: 'cd80.app/2', name: '合计 APP', description: '求和',
    inputSchema: { type: 'object', properties: { values: { type: 'array' } }, required: ['values'] },
    nodes: [], outputs: {}, rules: [], testCases: [initialCase] };
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || 'GET', body: options.body });
    if (path.endsWith('/import/file-preview')) return json({ package: { format: 'cd80.app-package/2', digest: 'digest', definition }, definition, issues: [], warnings: [] });
    if (path.endsWith('/asset-apps/import')) return json({ id: 'app-1', versionId: 'v1', verification: { status: 'unverified' } });
    throw new Error('unexpected ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(AppCreatePanel, { ownerId: 'owner-a', onSaved: value => saved.push(value) });
  try {
    app.mount(root); await flush();
    find(root, node => node.tag === 'button' && content(node).includes('从外部导入')).props.onClick(); await flush();
    const upload = find(root, node => node.tag === 'input' && node.props.type === 'file');
    upload.props.onChange({ target: { files: [new File(['{}'], 'sample.json', { type: 'application/json' })], value: 'sample.json' } }); await settle();
    set(root, '保存原任务输入', '{"values":[1,2]}');
    set(root, '保存独立预期', '{"mode":"json_equals","value":{"total":3}}'); await flush();
    find(root, node => node.tag === 'button' && content(node) === '导入并保存，不运行').props.onClick(); await settle();
    const body = JSON.parse(requests.find(item => item.path.endsWith('/asset-apps/import')).body);
    assert.deepEqual(body.testCases, [{ label: '原任务用例', input: { values: [1,2] }, fileBindings: {}, expectation: { mode: 'json_equals', value: { total: 3 } } }]);
    assert.deepEqual(saved, [{ kind: 'app', appId: 'app-1', versionId: 'v1' }]);
    assert.ok(requests.every(item => !/\/verify|\/runs|\/enable/.test(item.path)));
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('Skill raw import freezes edited original case in multipart save', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, requests = [], saved = [];
  globalThis.document = { activeElement: null };
  const initialCase = { label: '原任务用例', input: {}, fileIds: [],
    expectation: { mode: 'manual_review', prompt: '核查数据', criteria: '' } };
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || 'GET', body: options.body });
    if (path.endsWith('/capabilities/imports/preview')) return json({ kind: 'skill', sha256: 'a'.repeat(64), files: [], issues: [], manifest: {}, originalName: 'SKILL.md', testCases: [initialCase] });
    if (path.endsWith('/capabilities/imports')) return json({ assetId: 'skill-1', versionId: 'v1' });
    throw new Error('unexpected ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(CapabilityCreatePanel, { ownerId: 'owner-s', kind: 'skill', onSaved: value => saved.push(value) });
  try {
    app.mount(root); await flush();
    find(root, node => node.tag === 'button' && content(node) === '填写完整原文').props.onClick(); await flush();
    set(root, '完整 SKILL.md 原文', '---\nname: sample\ndescription: 检查数据\n---\n# 检查'); await flush();
    find(root, node => node.tag === 'button' && content(node) === '预览文件清单与兼容性').props.onClick(); await settle();
    set(root, '保存独立验收标准', '应准确列出三项核查结果'); await flush();
    find(root, node => node.tag === 'button' && content(node) === '保存原件，不运行').props.onClick(); await settle();
    const body = requests.find(item => item.path.endsWith('/capabilities/imports') && item.method === 'POST').body;
    assert.deepEqual(JSON.parse(body.get('testCases')), [{ label: '原任务用例', input: {}, fileIds: [],
      expectation: { mode: 'manual_review', prompt: '核查数据', criteria: '应准确列出三项核查结果' } }]);
    assert.deepEqual(saved, [{ kind: 'skill', assetId: 'skill-1', versionId: 'v1' }]);
    assert.ok(requests.every(item => !/\/validation-suites|\/invocations/.test(item.path)));
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});
