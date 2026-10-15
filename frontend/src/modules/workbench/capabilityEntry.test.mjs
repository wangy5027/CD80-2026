import test from 'node:test';
import assert from 'node:assert/strict';
import { component, renderer, hostNode, content, flush, find } from './assetComponentHarness.mjs';
import { capabilitySelectionReason, capabilityStatus } from './capabilityApi.js';

const Panel = await component('./components/CapabilityLibraryPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });

test('catalog management opens its exact older version even outside the first version page', async () => {
  const oldFetch = globalThis.fetch, calls = [];
  const asset = { assetId: 'a1', kind: 'tool', name: 'versioned-tool', latestVersionId: 'new', activeVersionId: 'active', lifecycle: 'active', enabled: false };
  const version = id => ({ versionId: id, contentDigest: 'digest-' + id, number: id === 'old' ? 1 : 102, readiness: 'unverified', issues: [], manifest: { kind: 'tool', files: [], dependencies: [], permissions: {}, tool: { inputSchema: { type: 'object', properties: {} } } } });
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, method: options.method || 'GET' });
    if (path.endsWith('/assets/a1')) return json(asset);
    if (path.includes('/versions?')) return json({ items: [version('new')], nextCursor: 'older-page' });
    if (path.includes('/versions/old?')) return json(version('old'));
    return json({ items: [] });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'test-owner', initialAssetId: 'a1', initialVersionId: 'old', embedded: true });
  try {
    app.mount(root);
    for (let i = 0; i < 8; i++) await flush();
    assert.ok(calls.some(call => call.path.includes('/versions/old?')));
    assert.match(content(root), /内容标识 digest-old/);
    assert.doesNotMatch(content(root), /内容标识 digest-new/);
    assert.match(content(root), /亲自试一试/);
    assert.match(content(root), /保存为我的测试用例/);
    assert.match(content(root), /我的测试用例/);
    assert.ok(calls.every(call => call.method === 'GET'));
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('server-trusted ready version can be enabled without a fabricated verification result', async () => {
  const asset = { assetId: 'trusted-1', kind: 'skill', name: '公开原版', latestVersionId: 'v1', lifecycle: 'active', enabled: false, revision: 1 };
  const version = { versionId: 'v1', contentDigest: 'trusted-content', number: 1, readiness: 'ready', admission: { mode: 'trusted_release', source: 'community', validationRequired: false }, issues: [], manifest: { kind: 'skill', files: [], dependencies: [{ sourceIdentity: 'cd80/history-tool' }], permissions: {}, tool: {} } };
  const oldFetch = globalThis.fetch, patches = [], selected = [];
  globalThis.fetch = async (path, options = {}) => {
    if (options.method === 'PATCH') {
      patches.push({ path, options });
      return json({ ...asset, enabled: true, activeVersionId: version.versionId, revision: 2 });
    }
    return path.endsWith('/assets/trusted-1') ? json(asset) : path.includes('/versions?') ? json({ items: [version], nextCursor: null }) : json({ items: [] });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'trusted-owner', initialAssetId: asset.assetId, initialVersionId: version.versionId, embedded: true, onSelect: item => selected.push(item) });
  try {
    app.mount(root); for (let i = 0; i < 8; i++) await flush();
    assert.equal(capabilityStatus({ ...asset, readiness: 'ready' }), '免验证 · 可启用');
    assert.match(capabilitySelectionReason({ ...asset, readiness: 'ready' }), /免重复验证/);
    assert.match(content(root), /服务端确认的官方或公开精确版本免重复验证/);
    assert.match(content(root), /启用时会同时启用所需的官方或公开 Tool/);
    assert.match(content(root), /已主动禁用的依赖需先恢复/);
    assert.match(content(root), /可直接启用/);
    assert.match(content(root), /方法和启用条件/);
    assert.doesNotMatch(content(root), /开始验证|我的测试用例|保存为我的测试用例/);
    const enable = find(root, node => node.tag === 'button' && content(node) === '启用并用于对话');
    assert.equal(enable.props.disabled, false);
    await enable.props.onClick(); for (let i = 0; i < 3; i++) await flush();
    assert.equal(patches.length, 1);
    assert.equal(patches[0].path, '/desktop/api/capabilities/assets/trusted-1');
    assert.equal(patches[0].options.method, 'PATCH');
    assert.equal(patches[0].options.credentials, 'include');
    assert.equal(patches[0].options.headers['If-Match'], '"1"');
    assert.deepEqual(JSON.parse(patches[0].options.body), { enabled: true, activeVersionId: 'v1' });
    assert.deepEqual(selected, [{ assetId: 'trusted-1', versionId: 'v1', name: '公开原版', kind: 'skill' }]);
    assert.ok(find(root, node => node.tag === 'button' && content(node) === '用于对话'));
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('failed ready Tool activation does not select it or change its disabled state', async () => {
  const asset = { assetId: 'ready-tool', kind: 'tool', name: '公开工具', latestVersionId: 'v2', lifecycle: 'active', enabled: false, revision: 4 };
  const version = { versionId: 'v2', contentDigest: 'tool-content', number: 2, readiness: 'ready', admission: { mode: 'trusted_release', source: 'official', validationRequired: false }, issues: [], manifest: { kind: 'tool', files: [], dependencies: [], permissions: {}, tool: { inputSchema: { type: 'object', properties: {} } } } };
  const oldFetch = globalThis.fetch, patches = [], selected = [];
  globalThis.fetch = async (path, options = {}) => {
    if (options.method === 'PATCH') {
      patches.push({ path, options });
      return new Response(JSON.stringify({ code: 'DEPENDENCY_DISABLED', error: 'dependency disabled' }), { status: 409, headers: { 'Content-Type': 'application/json' } });
    }
    return path.endsWith('/assets/ready-tool') ? json(asset) : path.includes('/versions?') ? json({ items: [version], nextCursor: null }) : json({ items: [] });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'trusted-owner', initialAssetId: asset.assetId, initialVersionId: version.versionId, embedded: true, onSelect: item => selected.push(item) });
  try {
    app.mount(root); for (let i = 0; i < 8; i++) await flush();
    const enable = find(root, node => node.tag === 'button' && content(node) === '启用并用于对话');
    assert.equal(enable.props.disabled, false);
    await enable.props.onClick(); for (let i = 0; i < 3; i++) await flush();
    assert.equal(patches.length, 1);
    assert.equal(patches[0].options.headers['If-Match'], '"4"');
    assert.deepEqual(JSON.parse(patches[0].options.body), { enabled: true, activeVersionId: 'v2' });
    assert.deepEqual(selected, []);
    assert.equal(find(root, node => node.tag === 'button' && content(node) === '用于对话'), undefined);
    assert.match(content(root), /免验证 · 可启用/);
    assert.match(content(root), /当前网络连接失败/);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('trusted Skill with stale ready or verified configuration exposes both activation controls and can enable', async () => {
  const oldFetch = globalThis.fetch;
  try {
    for (const readiness of ['needs_configuration', 'unverified']) {
      const asset = { assetId: 'stale-skill', kind: 'skill', name: '历史数据方法', latestVersionId: 'v3', lifecycle: 'active', enabled: false, revision: 9 };
      const version = { versionId: 'v3', contentDigest: 'stale-content', number: 3, readiness, configurationStale: true,
        admission: { mode: 'trusted_release', source: 'official', validationRequired: false }, issues: [],
        manifest: { kind: 'skill', files: [], dependencies: [{ sourceIdentity: 'cd80/history-tool' }], permissions: {}, tool: {} } };
      const patches = [], selected = [];
      globalThis.fetch = async (path, options = {}) => {
        if (options.method === 'PATCH') {
          patches.push({ path, options });
          return json({ ...asset, enabled: true, activeVersionId: version.versionId, revision: 10 });
        }
        return path.endsWith('/assets/stale-skill') ? json(asset) : path.includes('/versions?') ? json({ items: [version], nextCursor: null }) : json({ items: [] });
      };
      const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'trusted-owner', initialAssetId: asset.assetId, initialVersionId: version.versionId, embedded: true, onSelect: item => selected.push(item) });
      try {
        app.mount(root); for (let i = 0; i < 8; i++) await flush();
        assert.match(content(root), /此前配置状态已过期；启用时服务端会重新检查当前条件/);
        assert.match(content(root), /已主动禁用的依赖需先恢复/);
        const enable = find(root, node => node.tag === 'button' && content(node) === '启用并用于对话');
        const enableVersion = find(root, node => node.tag === 'button' && content(node) === '启用此版本');
        assert.equal(enable.props.disabled, false, readiness);
        assert.equal(enableVersion.props.disabled, false, readiness);
        await enable.props.onClick(); for (let i = 0; i < 3; i++) await flush();
        assert.equal(patches.length, 1, readiness);
        assert.equal(patches[0].options.headers['If-Match'], '"9"');
        assert.deepEqual(JSON.parse(patches[0].options.body), { enabled: true, activeVersionId: 'v3' });
        assert.deepEqual(selected, [{ assetId: 'stale-skill', versionId: 'v3', name: '历史数据方法', kind: 'skill' }]);
      } finally { app.unmount(); }
    }
  } finally { globalThis.fetch = oldFetch; }
});

test('stale private Skill and trusted Skill with issues have no activation entry', async () => {
  const oldFetch = globalThis.fetch;
  const cases = [
    { assetId: 'private-stale', admission: { mode: 'validation_required', source: null, validationRequired: true }, issues: [] },
    { assetId: 'trusted-issue', admission: { mode: 'trusted_release', source: 'official', validationRequired: false }, issues: [{ code: 'DEPENDENCY_DISABLED' }] },
    { assetId: 'trusted-failed', admission: { mode: 'trusted_release', source: 'official', validationRequired: false }, issues: [], readiness: 'failed' },
  ];
  try {
    for (const item of cases) {
      const asset = { assetId: item.assetId, kind: 'skill', name: item.assetId, latestVersionId: 'v1', lifecycle: 'active', enabled: false, revision: 2 };
      const version = { versionId: 'v1', contentDigest: 'stale-content', number: 1, readiness: item.readiness || 'needs_configuration', configurationStale: true,
        admission: item.admission, issues: item.issues, manifest: { kind: 'skill', files: [], dependencies: [], permissions: {}, tool: {} } };
      globalThis.fetch = async path => path.endsWith('/assets/' + item.assetId) ? json(asset) : path.includes('/versions?') ? json({ items: [version], nextCursor: null }) : json({ items: [] });
      const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'trusted-owner', initialAssetId: asset.assetId, initialVersionId: version.versionId, embedded: true });
      try {
        app.mount(root); for (let i = 0; i < 8; i++) await flush();
        assert.equal(find(root, node => node.tag === 'button' && content(node) === '启用并用于对话'), undefined, item.assetId);
        assert.equal(find(root, node => node.tag === 'button' && content(node) === '启用此版本').props.disabled, true, item.assetId);
      } finally { app.unmount(); }
    }
  } finally { globalThis.fetch = oldFetch; }
});
