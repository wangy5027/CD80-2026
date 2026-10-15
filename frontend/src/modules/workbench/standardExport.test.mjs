import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, h, ref } from 'vue';
import { component, hostNode, find, content, flush } from './assetComponentHarness.mjs';
import { downloadStandardZip, standardExportPath, standardExportSelection, standardFilenameFromDisposition,
  standardZipName } from './standardExport.js';

const Apps = await component('./components/AppsLibraryPanel.vue');
const Capabilities = await component('./components/CapabilityDirectoryPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const zip = () => new Response(new Blob(['PK\x03\x04archive'], { type: 'application/zip' }),
  { headers: { 'Content-Type': 'application/zip' } });
const settle = async () => { for (let index = 0; index < 6; index++) await flush(); };
const button = (root, label) => find(root, node => node.tag === 'button' && content(node) === label);
const exportButton = root => find(root, node => node.tag === 'button' && node.props['data-standard-export']);
const renderer = createRenderer({
  createElement: tag => {
    const node = hostNode(tag);
    if (tag === 'dialog') {
      node.showModal = () => { node.open = true; };
      node.close = () => { node.open = false; node.props.onClose?.(); };
    }
    return node;
  },
  createText: text => hostNode('#text', text), createComment: text => hostNode('#comment', text),
  setText: (node, text) => { node.text = text; },
  setElementText: (node, text) => { node.text = text; node.children = []; },
  patchProp: (node, key, _previous, value) => { node.props[key] = value; node[key] = value; },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  insert(node, parent, anchor = null) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = parent; parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, node); },
  remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null; },
});

function browser(downloads) {
  const previous = { document: globalThis.document, fetch: globalThis.fetch,
    createObjectURL: URL.createObjectURL, revokeObjectURL: URL.revokeObjectURL };
  globalThis.document = { activeElement: null, body: { appendChild() {} },
    createElement: () => ({ click() { downloads.push(this.download); }, remove() {} }) };
  URL.createObjectURL = () => 'blob:standard-export-test';
  URL.revokeObjectURL = () => {};
  return () => {
    globalThis.document = previous.document; globalThis.fetch = previous.fetch;
    URL.createObjectURL = previous.createObjectURL; URL.revokeObjectURL = previous.revokeObjectURL;
  };
}

test('exact source and version are preserved, while ZIP names keep Chinese safely', () => {
  const cards = [
    [{ source: 'asset', id: 'app-1', versionId: 'old', name: '个人APP' }, { versionId: 'new' }, 'new'],
    [{ source: 'strategy', id: 'rules-1', name: '策略' }, { app: { versionId: 'rules-v2' } }, 'rules-v2'],
    [{ source: 'community-app', id: 'release-1', name: '公开' }, { sourceVersionId: 'published-v3' }, 'published-v3'],
    [{ source: 'builtin', id: 'explore', name: 'Explore' }, { workflow: '研究' }, ''],
    [{ source: 'legacy-recipe', id: 'recipe-1', name: '旧应用' }, { goalTemplate: '研究' }, ''],
  ];
  for (const [card, detail, versionId] of cards) {
    assert.equal(standardExportSelection('app', card, detail).versionId, versionId);
  }
  const capability = standardExportSelection('tool', { source: 'official', id: 'tool/一', versionId: 'old', name: '工具' },
    { manifest: { name: '工具/一' }, version: { versionId: 'exact-v2' } });
  assert.equal(capability.versionId, 'exact-v2');
  assert.equal(standardZipName('工具/一:测试'), '工具_一_测试.zip');
  assert.equal(standardZipName('测'.repeat(100)), '测'.repeat(78) + '.zip', 'UTF-8 filename stem stays within 235 bytes');
  assert.equal(standardZipName('  A\u0000/B.  '), 'A__B.zip');
  assert.equal(standardExportSelection('skill', { source: 'community', id: 'release', versionId: 'private-v1', name: '发布' },
    { manifest: { name: '发布' }, version: { versionId: 'private-v2' } }).versionId, '',
  'a frozen release must never use a publisher private version id');
  assert.deepEqual(Object.fromEntries(new URL('http://local' + standardExportPath(capability)).searchParams),
    { kind: 'tool', source: 'official', id: 'tool/一', versionId: 'exact-v2' });
});

test('ZIP download includes credentials and rejects errors or wrong content without saving', async () => {
  const selection = { kind: 'app', source: 'asset', id: 'app', versionId: 'v1', name: '测试应用' };
  const saved = [];
  const filename = await downloadStandardZip(selection, { fetcher: async (_path, options) => {
    assert.equal(options.credentials, 'include');
    assert.equal(options.headers.Accept, 'application/zip');
    return zip();
  }, save: (_blob, name) => saved.push(name) });
  assert.equal(filename, '测试应用.zip');
  assert.deepEqual(saved, ['测试应用.zip']);
  const serverName = '服务端中文名称.zip';
  const disposition = `attachment; filename="asset.zip"; filename*=UTF-8''${encodeURIComponent(serverName)}`;
  assert.equal(standardFilenameFromDisposition(disposition), serverName);
  assert.equal(standardFilenameFromDisposition("attachment; filename*=UTF-8''..%2Fprivate.zip"), '');
  assert.equal(standardFilenameFromDisposition("attachment; filename*=UTF-8''%E0%A4%A.zip"), '');
  assert.equal(standardFilenameFromDisposition(`attachment; filename*=UTF-8''${encodeURIComponent('测'.repeat(100) + '.zip')}`), '');
  await downloadStandardZip(selection, { fetcher: async () => new Response(new Blob(['PK\x03\x04archive']),
    { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': disposition } }),
  save: (_blob, name) => saved.push(name) });
  await downloadStandardZip(selection, { fetcher: async () => new Response(new Blob(['PK\x03\x04archive']),
    { headers: { 'Content-Type': 'application/zip', 'Content-Disposition': "attachment; filename*=UTF-8''..%2Fprivate.zip" } }),
  save: (_blob, name) => saved.push(name) });
  assert.deepEqual(saved, ['测试应用.zip', serverName, '测试应用.zip']);
  await assert.rejects(downloadStandardZip(selection, { fetcher: async () => json({ error: 'not a ZIP' }), save: () => saved.push('bad') }), /标准包下载失败/);
  await assert.rejects(downloadStandardZip(selection, { fetcher: async () => new Response('', { status: 403 }), save: () => saved.push('bad') }), /标准包下载失败/);
  assert.deepEqual(saved, ['测试应用.zip', serverName, '测试应用.zip']);
});

test('APP detail exports its selected saved version and closing cancels a pending download', async () => {
  const downloads = [], restore = browser(downloads), requests = [];
  let releasePending;
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, signal: options.signal });
    if (path.includes('/skill-pool/standard-export')) {
      if (releasePending) return new Promise(resolve => { releasePending = resolve; });
      return zip();
    }
    if (path.endsWith('/asset-apps')) return json({ items: [{ id: 'app-1', versionId: 'v2', name: '投资助手', verification: { status: 'unverified' } }] });
    if (path.includes('/asset-apps/app-1?versionId=v2')) return json({ id: 'app-1', versionId: 'v2', definition: { format: 'cd80.app/2', name: '投资助手', nodes: [] } });
    return json({ agents: [], strategyApps: [], items: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp(Apps, { ownerId: 'app-owner' });
  try {
    app.mount(root); await settle();
    assert.equal(exportButton(root), undefined, 'closed directory does not show an export button');
    find(root, node => node.tag === 'button' && String(node.props.class).includes('apps-library-card') && content(node).includes('投资助手')).props.onClick();
    await settle();
    assert.equal(exportButton(root).props.disabled, false);
    exportButton(root).props.onClick(); await settle();
    assert.deepEqual(downloads, ['投资助手.zip']);
    const path = requests.find(item => item.path.includes('/skill-pool/standard-export')).path;
    assert.deepEqual(Object.fromEntries(new URL('http://local' + path).searchParams),
      { kind: 'app', source: 'asset', id: 'app-1', versionId: 'v2' });
    releasePending = true;
    exportButton(root).props.onClick(); await flush();
    assert.equal(exportButton(root).props.disabled, true);
    button(root, '关闭').props.onClick(); await settle();
    releasePending(zip()); await settle();
    assert.deepEqual(downloads, ['投资助手.zip'], 'closed detail cannot save a late response');
  } finally { app.unmount(); restore(); }
});

test('public Tool exports its release directly without first adding a personal copy', async () => {
  const downloads = [], restore = browser(downloads), requests = [];
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || 'GET' });
    if (path.includes('/skill-pool/standard-export')) return zip();
    if (path.includes('/capabilities/assets?')) return json({ items: [], nextCursor: null });
    if (path.includes('/community/capabilities?kind=tool')) return json({ items: [
      { releaseId: 'published-1', kind: 'tool', title: '公开计算', status: 'published', owned: false, personalCopies: [] }], nextCursor: null });
    if (path.endsWith('/community/capabilities/published-1')) return json({ releaseId: 'published-1', root: {
      manifest: { kind: 'tool', name: '公开计算', description: '计算', source: { identity: 'public/tool' }, files: [], dependencies: [], permissions: {},
        tool: { inputSchema: { type: 'object' } } }, contentDigest: 'digest' } });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Capabilities, { ownerId: 'tool-reader', kind: 'tool' });
  try {
    app.mount(root); await settle();
    find(root, node => node.tag === 'button' && String(node.props.class).includes('apps-library-card') && content(node).includes('公开计算')).props.onClick();
    await settle();
    assert.equal(exportButton(root).props['data-standard-export'], 'tool');
    exportButton(root).props.onClick(); await settle();
    assert.deepEqual(downloads, ['公开计算.zip']);
    const path = requests.find(item => item.path.includes('/skill-pool/standard-export')).path;
    assert.deepEqual(Object.fromEntries(new URL('http://local' + path).searchParams),
      { kind: 'tool', source: 'community', id: 'published-1', versionId: '' });
    assert.equal(requests.some(item => item.method === 'POST'), false, 'export does not create a copy');
  } finally { app.unmount(); restore(); }
});

test('official Skill keeps its public exact version and shows a generic download failure', async () => {
  const downloads = [], restore = browser(downloads), requests = [];
  globalThis.fetch = async path => {
    requests.push(path);
    if (path.includes('/skill-pool/standard-export')) return new Response('private server diagnostic', { status: 500 });
    if (path.includes('/capabilities/assets?scope=personal&kind=skill') || path.includes('/community/capabilities?kind=skill'))
      return json({ items: [], nextCursor: null });
    if (path.includes('/capabilities/assets?scope=public&kind=skill')) return json({ items: [
      { assetId: 'official-skill', latestVersionId: 'pub-v3', kind: 'skill', name: '官方方法', lifecycle: 'active' }], nextCursor: null });
    if (path.endsWith('/capabilities/assets/official-skill?scope=public')) return json({ assetId: 'official-skill', latestVersionId: 'pub-v3' });
    if (path.endsWith('/capabilities/assets/official-skill/versions/pub-v3?scope=public')) return json({
      versionId: 'pub-v3', manifest: { kind: 'skill', name: '官方方法', description: '完整方法', source: { identity: 'official/skill' },
        files: [{ path: 'SKILL.md' }], dependencies: [], permissions: {}, skill: { entry: 'SKILL.md' } },
    });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Capabilities, { ownerId: 'reader', kind: 'skill' });
  try {
    app.mount(root); await settle();
    find(root, node => node.tag === 'button' && String(node.props.class).includes('apps-library-card') && content(node).includes('官方方法')).props.onClick();
    await settle();
    assert.equal(exportButton(root).props['data-standard-export'], 'skill');
    exportButton(root).props.onClick(); await settle();
    const path = requests.find(item => item.includes('/skill-pool/standard-export'));
    assert.deepEqual(Object.fromEntries(new URL('http://local' + path).searchParams),
      { kind: 'skill', source: 'official', id: 'official-skill', versionId: 'pub-v3' });
    assert.match(content(root), /当前网络连接失败/);
    assert.doesNotMatch(content(root), /private server diagnostic/);
    assert.deepEqual(downloads, []);
  } finally { app.unmount(); restore(); }
});

test('a deep-linked capability uses its saved display name in the detail and ZIP fallback', async () => {
  const downloads = [], restore = browser(downloads);
  globalThis.fetch = async path => {
    if (path.includes('/skill-pool/standard-export')) return zip();
    if (path.includes('/capabilities/assets?') || path.includes('/community/capabilities?'))
      return json({ items: [], nextCursor: null });
    if (path.endsWith('/capabilities/assets/hidden')) return json({
      assetId: 'hidden', displayName: '我重命名的工具', name: '原始能力名', latestVersionId: 'saved-v2',
    });
    if (path.endsWith('/capabilities/assets/hidden/versions/saved-v2')) return json({
      versionId: 'saved-v2', manifest: { kind: 'tool', name: '原始能力名', description: '完整计算',
        source: { identity: 'personal/tool' }, files: [], dependencies: [], permissions: {},
        tool: { inputSchema: { type: 'object' } } },
    });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Capabilities, {
    ownerId: 'owner', kind: 'tool', initialAssetId: 'hidden', initialVersionId: 'saved-v2',
  });
  try {
    app.mount(root); await settle();
    const title = find(root, node => node.tag === 'h2' && node.props.id === 'capability-directory-tool-detail');
    assert.equal(content(title), '我重命名的工具');
    assert.match(content(root), /原始能力名/, 'the underlying manifest remains visible');
    exportButton(root).props.onClick(); await settle();
    assert.deepEqual(downloads, ['我重命名的工具.zip']);
  } finally { app.unmount(); restore(); }
});

test('account change cancels an APP ZIP whose server response arrives late', async () => {
  const downloads = [], restore = browser(downloads), owner = ref('first');
  let complete;
  globalThis.fetch = async path => {
    if (path.includes('/skill-pool/standard-export')) return new Promise(resolve => { complete = resolve; });
    if (path.endsWith('/asset-apps')) return json({ items: [{ id: 'one', versionId: 'v1', name: '旧账号应用' }] });
    if (path.includes('/asset-apps/one?versionId=v1')) return json({ id: 'one', versionId: 'v1', definition: { name: '旧账号应用', nodes: [] } });
    return json({ agents: [], strategyApps: [], items: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp({ setup: () => () => h(Apps, { ownerId: owner.value }) });
  try {
    app.mount(root); await settle();
    find(root, node => node.tag === 'button' && String(node.props.class).includes('apps-library-card') && content(node).includes('旧账号应用')).props.onClick();
    await settle();
    exportButton(root).props.onClick(); await flush();
    owner.value = 'second'; await settle();
    complete(zip()); await settle();
    assert.deepEqual(downloads, []);
  } finally { app.unmount(); restore(); }
});
