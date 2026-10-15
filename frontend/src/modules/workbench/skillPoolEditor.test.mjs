import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, find, all, content, flush, submitEvent } from './assetComponentHarness.mjs';

const Editor = await component('./components/SkillPoolEditor.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 5; index++) await flush(); };
const field = (root, selector) => find(root, selector);
const set = (node, value) => { assert.ok(node?.props?.['onUpdate:modelValue']); node.props['onUpdate:modelValue'](value); };

test('manual Skill save keeps input after failed save and patches the same draft on retry', async () => {
  const oldFetch = globalThis.fetch, requests = [], emitted = [];
  let saves = 0, patched = null;
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ path, method: options.method || 'GET', body, headers: options.headers });
    if (path.endsWith('/capabilities/drafts') && options.method === 'POST') return json({ draftId: 'draft-1', revision: 1 });
    if (path.endsWith('/capabilities/drafts/draft-1') && options.method === 'PATCH') {
      patched = body;
      return new Response(JSON.stringify({ error: '草稿已变化', code: 'REVISION_CONFLICT' }), { status: 409, headers: { 'Content-Type': 'application/json' } });
    }
    if (path.endsWith('/capabilities/drafts/draft-1') && !options.method) return json({ draftId: 'draft-1', revision: patched ? 2 : 1, content: patched });
    if (path.endsWith('/capabilities/drafts/draft-1/save')) {
      saves++;
      return saves === 1 ? json({ status: 'failed', error: { error: '暂时无法保存' } })
        : json({ status: 'completed', result: { assetId: 'skill-1', versionId: 'v1' } });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Editor, { kind: 'skill', ownerId: 'owner-1', onSaved: result => emitted.push(result) });
  try {
    app.mount(root); await settle();
    const form = field(root, node => node.tag === 'form');
    form.props.onSubmit(submitEvent()); await settle();
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /Skill 名称/);
    assert.equal(requests.length, 0);
    const inputs = all(root, node => node.tag === 'input' && node.props.type !== 'checkbox' && node.props.type !== 'file');
    const areas = all(root, node => node.tag === 'textarea');
    set(inputs[0], 'portfolio-review'); set(areas[0], '在需要复核持仓时使用，检查数据与来源。'); set(areas[1], '# 核对\n按原始数据逐项核对。');
    set(field(root, node => node.props['aria-label'] === '保存原任务要求'), '按输入核对持仓');
    set(field(root, node => node.props['aria-label'] === '保存独立验收标准'), '准确列出总额与来源');
    await settle(); form.props.onSubmit(submitEvent()); await settle();
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /暂时无法保存/);
    assert.match(content(root), /按原始数据逐项核对/);
    set(areas[1], '# 核对\n按原始数据逐项核对，并写出依据。'); await settle();
    form.props.onSubmit(submitEvent()); await settle();
    assert.deepEqual(emitted, [{ kind: 'skill', assetId: 'skill-1', versionId: 'v1' }]);
    assert.equal(requests.filter(item => item.path.endsWith('/capabilities/drafts') && item.method === 'POST').length, 1);
    assert.deepEqual(requests.find(item => item.path.endsWith('/capabilities/drafts') && item.method === 'POST').body.testCases,
      [{ label: '原任务用例', input: {}, fileIds: [], expectation: { mode: 'manual_review', prompt: '按输入核对持仓', criteria: '准确列出总额与来源' } }]);
    assert.equal(requests.filter(item => item.method === 'PATCH').length, 1);
    assert.equal(requests.filter(item => item.path.endsWith('/draft-1') && item.method === 'GET').length, 2);
    assert.equal(requests.find(item => item.method === 'PATCH').headers['If-Match'], '"1"');
    assert.equal(requests.filter(item => item.path.endsWith('/save')).at(-1).body.draftRevision, 2);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('unknown capability save locks edits and retries the same draft, revision, body and key', async () => {
  const oldFetch = globalThis.fetch, requests = [], emitted = [];
  let saves = 0;
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ path, method: options.method || 'GET', body, headers: options.headers });
    if (path.endsWith('/capabilities/drafts') && options.method === 'POST') return json({ draftId: 'draft-unknown', revision: 1 });
    if (path.endsWith('/capabilities/drafts/draft-unknown/save')) {
      saves++;
      return saves === 1 ? new Response(JSON.stringify({ error: '连接中断' }), { status: 503, headers: { 'Content-Type': 'application/json' } })
        : json({ status: 'completed', result: { assetId: 'skill-unknown', versionId: 'v1' } });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Editor, { kind: 'skill', ownerId: 'owner-unknown', onSaved: result => emitted.push(result) });
  try {
    app.mount(root); await settle();
    const inputs = all(root, node => node.tag === 'input' && node.props.type !== 'checkbox' && node.props.type !== 'file');
    const areas = all(root, node => node.tag === 'textarea');
    set(inputs[0], 'review-data'); set(areas[0], '在需要复核数据时使用。'); set(areas[1], '# 核对\n逐项检查。'); await settle();
    const form = field(root, node => node.tag === 'form'); form.props.onSubmit(submitEvent()); await settle();
    assert.match(content(root), /核实原提交/);
    assert.equal(field(root, node => node.tag === 'fieldset').props.disabled, true);
    set(areas[1], '# 用户在未知结果后输入的新正文'); await settle(); // Bypass disabled in test; pending request snapshot must still win.
    form.props.onSubmit(submitEvent()); await settle();
    const posts = requests.filter(item => item.path.endsWith('/save'));
    assert.equal(posts.length, 2);
    assert.deepEqual(posts[0].body, posts[1].body);
    assert.equal(posts[0].headers['Idempotency-Key'], posts[1].headers['Idempotency-Key']);
    assert.equal(requests.filter(item => item.path.endsWith('/capabilities/drafts') && item.method === 'POST').length, 1);
    assert.equal(requests.filter(item => item.method === 'PATCH').length, 0);
    assert.deepEqual(emitted, [{ kind: 'skill', assetId: 'skill-unknown', versionId: 'v1' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

for (const terminal of ['cancelled', 'interrupted']) test(`${terminal} save checks savedVersionId before any new attempt`, async () => {
  const oldFetch = globalThis.fetch, requests = [], emitted = [];
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || 'GET' });
    if (path.endsWith('/capabilities/drafts') && options.method === 'POST') return json({ draftId: 'draft-terminal', revision: 1 });
    if (path.endsWith('/capabilities/drafts/draft-terminal/save')) return json({ status: terminal });
    if (path.endsWith('/capabilities/drafts/draft-terminal')) return json({ draftId: 'draft-terminal', revision: 1, savedVersionId: 'version-already-saved' });
    if (path.includes('/capabilities/assets?')) return json({ items: [{ assetId: 'asset-saved', kind: 'skill', lifecycle: 'active', name: '已保存' }], nextCursor: null });
    if (path.includes('/capabilities/assets/asset-saved/versions')) return json({ items: [{ versionId: 'version-already-saved', number: 1 }], nextCursor: null });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Editor, { kind: 'skill', ownerId: 'owner-terminal', onSaved: result => emitted.push(result) });
  try {
    app.mount(root); await settle();
    const inputs = all(root, node => node.tag === 'input' && node.props.type !== 'checkbox' && node.props.type !== 'file');
    const areas = all(root, node => node.tag === 'textarea');
    set(inputs[0], 'review-data'); set(areas[0], '在需要复核数据时使用。'); set(areas[1], '# 核对\n逐项检查。'); await settle();
    field(root, node => node.tag === 'form').props.onSubmit(submitEvent()); await settle();
    assert.deepEqual(emitted, [{ kind: 'skill', assetId: 'asset-saved', versionId: 'version-already-saved' }]);
    assert.equal(requests.filter(item => item.path.endsWith('/save')).length, 1);
    assert.equal(requests.filter(item => item.path.endsWith('/draft-terminal') && item.method === 'GET').length, 1);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('manual APP editor loads exact personal component versions and saves an unverified pipeline', async () => {
  const oldFetch = globalThis.fetch, requests = [], emitted = [];
  let appSaves = 0;
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null;
    requests.push({ path, body, method: options.method || 'GET', headers: options.headers });
    if (path.includes('/capabilities/assets?')) return json({ items: [{ assetId: 'tool-1', kind: 'tool', name: '求和', lifecycle: 'active', readiness: 'unverified', latestVersionId: 'tv2', enabled: false }], nextCursor: null });
    if (path.includes('/capabilities/assets/tool-1/versions')) return json({ items: [{ versionId: 'tv2', number: 2, readiness: 'unverified', manifest: { tool: { inputSchema: { properties: { values: { type: 'array' } } } } } }], nextCursor: null });
    if (path.endsWith('/asset-apps/preview')) return json({ issues: [] });
    if (path.endsWith('/asset-apps') && options.method === 'POST') {
      appSaves++;
      return appSaves === 1 ? new Response(JSON.stringify({ error: '响应暂不可用' }), { status: 503, headers: { 'Content-Type': 'application/json' } })
        : json({ id: 'app-1', versionId: 'av-1', verification: { status: 'unverified' } });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Editor, { kind: 'app', ownerId: 'owner-1', onSaved: result => emitted.push(result) });
  try {
    app.mount(root); await settle();
    const fields = all(root, node => node.tag === 'input' && node.props.type !== 'checkbox' && node.props.type !== 'file');
    set(fields[0], '数据汇总 APP'); set(fields[1], '按顺序处理输入数据');
    const schema = all(root, node => node.tag === 'textarea')[0];
    set(schema, '{"type":"object","properties":{"values":{"type":"array"}}}');
    const add = field(root, node => node.tag === 'button' && content(node).includes('添加顺序步骤'));
    add.props.onClick(); await settle();
    assert.match(content(root), /求和/);
    assert.match(content(root), /未验证/);
    const select = field(root, node => node.tag === 'select');
    select.props.onChange({ target: { value: 'tool-1|tv2' } }); await settle();
    const form = field(root, node => node.tag === 'form'); form.props.onSubmit(submitEvent()); await settle();
    assert.match(content(root), /核实原提交/);
    assert.equal(field(root, node => node.tag === 'fieldset').props.disabled, true);
    set(fields[0], '用户后来输入的新名字'); await settle(); // Simulates an illicit event; the pending snapshot still wins.
    form.props.onSubmit(submitEvent()); await settle();
    const saves = requests.filter(item => item.path.endsWith('/asset-apps') && item.method === 'POST');
    const saved = saves[0];
    assert.equal(saves.length, 2);
    assert.deepEqual(saves[0].body, saves[1].body);
    assert.equal(saves[0].headers['Idempotency-Key'], saves[1].headers['Idempotency-Key']);
    assert.equal(saves[0].body.definition.name, '数据汇总 APP');
    assert.deepEqual(saved.body.definition.nodes[0].componentRef, { assetId: 'tool-1', versionId: 'tv2' });
    assert.deepEqual(saved.body.definition.nodes[0].inputBindings.values, { from: 'input', path: 'values' });
    assert.deepEqual(saved.body.definition.outputs.result.stepId, saved.body.definition.nodes[0].id);
    assert.deepEqual(emitted, [{ kind: 'app', appId: 'app-1', versionId: 'av-1' }]);
    assert.ok(requests.every(item => !/\/verify|\/enable|\/runs/.test(item.path)));
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});
