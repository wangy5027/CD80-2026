import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, find, all, content, flush, submitEvent } from './assetComponentHarness.mjs';

const CapabilityLibraryPanel = await component('./components/CapabilityLibraryPanel.vue');
const AssetAuthoringPanel = await component('./components/AssetAuthoringPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 5; index++) await flush(); };

test('task Skill capture uses new Creator and saves complete resources, snapshots and source claims', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  const draft = { kind: 'skill', name: '实际方法', description: '完整保存',
    manifest: { format: 'cd80.capability/1', kind: 'skill', files: [], dependencies: [], permissions: {}, skill: { entry: 'original/SKILL.md' } },
    files: [{ path: 'original/SKILL.md', text: '---\nname: test-method\ndescription: test\n---\n原方法' }, { path: 'original/assets/data.bin', base64: 'AAEC/w==' }],
    dependencySnapshots: [{ manifest: { source: { identity: 'dependency' } }, files: { 'tool.py': 'YWJj' }, dependencies: [] }],
    authoringProvenance: { source: 'selected_app_nodes', nodes: [{ nodeId: 'n1', sourceClaim: { type: 'historical', runId: 'r-real' } }] },
  };
  const calls = [];
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null; calls.push({ path, method: options.method || 'GET', body });
    if (path === '/desktop/api/asset-apps/creator') return json({ draft, source: { runId: 'r-real' }, issues: [], warnings: [] });
    if (path === '/desktop/api/capabilities/drafts') return json({ draftId: 'draft-1', revision: 1, content: body });
    if (path === '/desktop/api/capabilities/drafts/draft-1') return json({ draftId: 'draft-1', revision: 2, content: body });
    if (path.endsWith('/draft-1/save')) return json({ jobId: 'save-1', status: 'completed', result: { assetId: 'saved', versionId: 'v1' } });
    return json({ items: [] });
  };
  const root = hostNode('root'), app = renderer.createApp(CapabilityLibraryPanel, { ownerId: 'audit-owner', initialCapture: { kind: 'skill', runId: 'r-real' } });
  try {
    app.mount(root); await settle();
    assert.deepEqual(calls.find(call => call.path.endsWith('/creator')).body, { kind: 'skill', sourceRunId: 'r-real' });
    assert.match(content(root), /源码与方法的来源记录/);
    find(root, node => node.props['aria-label'] === 'original/SKILL.md').props.onInput({ target: { value: '修改后的完整方法' } }); await flush();
    find(root, node => node.tag === 'button' && content(node) === '审阅并保存').props.onClick(); await settle();
    const saved = calls.find(call => call.method === 'PATCH' && call.path.endsWith('/draft-1')).body;
    assert.equal(saved.files[0].text, '修改后的完整方法'); assert.deepEqual(saved.files[1], draft.files[1]);
    assert.deepEqual(saved.dependencySnapshots, draft.dependencySnapshots);
    assert.equal(saved.authoringProvenance.source, 'manual_edit');
    assert.deepEqual(saved.authoringProvenance.nodes, draft.authoringProvenance.nodes);
    assert.deepEqual(saved.authoringProvenance.modifiedFiles, ['original/SKILL.md']);
    assert.ok(calls.every(call => !/evolution|\/verifications|\/validation-suites|\/invocations/.test(call.path)));
    assert.match(content(root), /新内容已保存为版本/);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('successful task App capture sends sourceRunId to unified Creator without creating an old recipe', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  const calls = [], definition = { format: 'cd80.app/2', name: '完整成果', description: '', inputSchema: { type: 'object', properties: {} }, nodes: [], rules: [], outputs: {}, gaps: ['需补数据来源'] };
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : null; calls.push({ path, method: options.method || 'GET', body });
    return json(path.endsWith('/creator') ? { definition, issues: [], warnings: ['保留待补项'] } : { items: [] });
  };
  const root = hostNode('root'), app = renderer.createApp(AssetAuthoringPanel, { ownerId: 'audit-owner', captureRunId: 'source-run' });
  try {
    app.mount(root); await settle();
    assert.deepEqual(calls.filter(call => call.method === 'POST'), [{ path: '/desktop/api/asset-apps/creator', method: 'POST', body: { kind: 'app', sourceRunId: 'source-run' } }]);
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /需补数据来源/); assert.ok(find(root, node => node.props['aria-label'] === '完整应用定义'));
    assert.ok(calls.every(call => !/evolution|\/verify|\/runs/.test(call.path)));
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('a failed validation job still exposes each completed, failed and unstarted case', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document;
  globalThis.document = { activeElement: null };
  const asset = { assetId: 'a1', kind: 'tool', name: '固定工具', description: '', latestVersionId: 'v1', lifecycle: 'active', readiness: 'unverified', enabled: false };
  const version = { versionId: 'v1', number: 1, configurationId: 'c1', readiness: 'unverified', contentValidity: 'valid', runtimeReadiness: 'unverified', contentDigest: 'digest', issues: [], validationPolicy: 'original_and_new',
    testCases: [{ label: '原用例', input: { n: 2 }, fileIds: [], expectation: { mode: 'json_equals', value: { n: 2 } } },
      { label: '新用例', input: { n: 5 }, fileIds: [], expectation: { mode: 'json_equals', value: { n: 5 } } }],
    manifest: { kind: 'tool', files: [], dependencies: [], permissions: { inputFiles: false }, tool: { inputSchema: { type: 'object', properties: { n: { type: 'number' } }, required: ['n'], additionalProperties: false } } } };
  const calls = [], evidence = { verificationId: 'check-1', status: 'failed', cases: [{ label: '原用例', status: 'failed', error: { code: 'INVALID_OUTPUT', error: '实际结果缺少字段' } }, { label: '新用例', status: 'queued' }] };
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, method: options.method || 'GET', body: options.body ? JSON.parse(options.body) : null });
    if (path.endsWith('/validation-suites')) return json({ jobId: 'job-failed', verificationId: 'check-1', status: 'failed', error: { error: '执行失败保留证据' } });
    if (path.endsWith('/verifications/check-1')) return json(evidence);
    if (path.includes('/versions?')) return json({ items: [version] });
    if (path.endsWith('/assets/a1')) return json(asset);
    return json({ items: path.includes('/assets?') ? [asset] : [] });
  };
  const root = hostNode('root'), app = renderer.createApp(CapabilityLibraryPanel, { ownerId: 'audit-owner', initialAssetId: 'a1' });
  try {
    app.mount(root); await settle();
    const panel = find(root, node => node.props['aria-label'] === '原用例与新输入验证');
    const numbers = all(panel, node => node.tag === 'input' && node.props.type === 'number');
    numbers[0].props.onInput({ target: { value: '2' } }); numbers[1].props.onInput({ target: { value: '5' } }); await flush();
    find(find(panel, node => node.tag === 'label' && content(node).includes('将这个不同输入用例加入本次验证')), node => node.tag === 'input').props['onUpdate:modelValue'](true); await flush();
    find(panel, node => node.tag === 'form').props.onSubmit(submitEvent()); await settle();
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /执行失败保留证据|实际结果缺少字段/); assert.match(content(root), /新用例 · queued/);
    assert.equal(calls.filter(call => call.path.endsWith('/validation-suites')).length, 1);
    assert.deepEqual(calls.find(call => call.path.endsWith('/validation-suites')).body.cases.map(row => row.input.n), [2,5]);
    assert.ok(calls.some(call => call.path.endsWith('/verifications/check-1')));
    assert.ok(calls.every(call => call.method === 'GET' || call.path.endsWith('/validation-suites')));
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});
