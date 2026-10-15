import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, h, nextTick, ref } from 'vue';
import { component, hostNode, find, content, flush } from './assetComponentHarness.mjs';

const Panel = await component('./components/SkillPoolPanel.vue');
const Directory = await component('./components/CapabilityDirectoryPanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const renderer = createRenderer({
  createElement: tag => {
    const node = hostNode(tag);
    node.scrollIntoView = () => { node.scrolled = true; }; node.focus = () => { node.focused = true; };
    if (tag === 'dialog') {
      node.showModal = () => { node.open = true; };
      node.close = () => { node.open = false; node.props.onClose?.(); };
    }
    return node;
  },
  createText: text => hostNode('#text', text), createComment: text => hostNode('#comment', text),
  setText: (node, text) => { node.text = text; }, setElementText: (node, text) => { node.text = text; node.children = []; },
  patchProp: (node, key, _previous, value) => { node.props[key] = value; if (!['children', 'parent', 'tag'].includes(key)) node[key] = value; },
  parentNode: node => node.parent, nextSibling: node => node.parent?.children[node.parent.children.indexOf(node) + 1] || null,
  insert(node, parent, anchor = null) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = parent; parent.children.splice(anchor ? parent.children.indexOf(anchor) : parent.children.length, 0, node); },
  remove(node) { if (node.parent) node.parent.children.splice(node.parent.children.indexOf(node), 1); node.parent = null; },
});
const settle = async () => { for (let index = 0; index < 7; index++) await flush(); };
const tab = (root, kind) => find(root, node => node.tag === 'button' && node.props.id === 'pool-tab-' + kind);
const button = (root, label) => find(root, node => node.tag === 'button' && content(node) === label);
const child = (instance, name) => {
  const walk = node => {
    if (!node) return null;
    if (Array.isArray(node)) return node.map(walk).find(Boolean) || null;
    if (node.component?.type?.__name === name) return node.component;
    return walk(node.component?.subTree) || walk(node.children);
  };
  return walk(instance.subTree);
};
const manifest = kind => ({ kind, name: '能力版本', description: '实际方法', source: { identity: 'public/example' },
  files: [{ path: kind === 'skill' ? 'SKILL.md' : 'tool.json' }], dependencies: [], permissions: { network: 'none' },
  ...(kind === 'tool' ? { tool: { inputSchema: { type: 'object', properties: {} } } } : { skill: { entry: 'SKILL.md' } }) });

test('Skills and Tools tabs mount both personal and official/community directories, including published cards', async () => {
  const oldFetch = globalThis.fetch, requests = [];
  globalThis.fetch = async path => {
    requests.push(path);
    if (path.includes('/capabilities/assets?scope=personal&kind=')) return json({ items: [], nextCursor: null });
    if (path.includes('/capabilities/assets?scope=public&kind=skill')) return json({ items: [
      { assetId: 'official-skill', latestVersionId: 'official-v1', kind: 'skill', lifecycle: 'active', name: '官方核对方法', personalCopies: [] }], nextCursor: null });
    if (path.includes('/capabilities/assets?scope=public&kind=tool')) return json({ items: [
      { assetId: 'official-tool', latestVersionId: 'official-tv1', kind: 'tool', lifecycle: 'active', name: '官方计算工具', personalCopies: [] }], nextCursor: null });
    if (path.includes('/community/capabilities?kind=skill')) return json({ items: [
      { releaseId: 'release-skill', kind: 'skill', status: 'published', title: '用户公开方法', owned: false, personalCopies: [] }], nextCursor: null });
    if (path.includes('/community/capabilities?kind=tool')) return json({ items: [
      { releaseId: 'release-tool', kind: 'tool', status: 'published', title: '用户公开工具', owned: false, personalCopies: [] }], nextCursor: null });
    return json({ items: [], agents: [], strategyApps: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'routing-reader' });
  try {
    app.mount(root); await settle();
    tab(root, 'skill').props.onClick(); await settle();
    assert.match(content(root), /我的 Skills/); assert.match(content(root), /平台 Skills/);
    assert.match(content(root), /官方核对方法/); assert.match(content(root), /用户公开方法/);
    tab(root, 'tool').props.onClick(); await settle();
    assert.match(content(root), /我的 Tools/); assert.match(content(root), /平台 Tools/);
    assert.match(content(root), /官方计算工具/); assert.match(content(root), /用户公开工具/);
    assert.ok(requests.some(path => path.includes('/community/capabilities?kind=skill')));
    assert.ok(requests.some(path => path.includes('/community/capabilities?kind=tool')));
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('external asset and prepared conversation capture still enter the full manager', async () => {
  const oldFetch = globalThis.fetch, requests = [];
  globalThis.fetch = async path => {
    requests.push(path);
    if (path.endsWith('/capabilities/assets/asset-1')) return json({ assetId: 'asset-1', kind: 'tool', name: '既有工具', description: '已有版本', latestVersionId: 'new', lifecycle: 'active' });
    if (path.includes('/capabilities/assets/asset-1/versions?')) return json({ items: [{ versionId: 'new', number: 2, readiness: 'unverified', issues: [], contentDigest: 'new', manifest: manifest('tool') }], nextCursor: null });
    if (path.includes('/capabilities/assets/asset-1/versions/old?')) return json({ versionId: 'old', number: 1, readiness: 'unverified', issues: [], contentDigest: 'old', manifest: manifest('tool') });
    return json({ items: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'routing-owner', initialAssetId: 'asset-1' });
  try {
    app.mount(root); await settle();
    assert.match(content(root), /返回 Tools目录/);
    assert.match(content(root), /能力版本/);
    assert.ok(requests.some(path => path.endsWith('/capabilities/assets/asset-1')));
    assert.equal(requests.some(path => path.includes('/community/capabilities?')), false);
  } finally { app.unmount(); }
  requests.length = 0;
  const prepared = { kind: 'tool', draft: { kind: 'tool', name: '来自对话的工具', description: '保留实际步骤',
    manifest: manifest('tool'), files: [{ path: 'tool.json', text: '{}' }] }, warnings: [] };
  const captureRoot = hostNode('root'), capture = renderer.createApp(Panel, { ownerId: 'routing-owner', initialCapture: { kind: 'tool', runId: 'run-1', prepared } });
  try {
    capture.mount(captureRoot); await settle();
    assert.match(content(captureRoot), /返回 Tools目录/);
    assert.match(content(captureRoot), /审阅执行工具草稿/);
    assert.ok(find(captureRoot, node => node.tag === 'input' && node.props.value === '来自对话的工具'));
    assert.equal(requests.some(path => path.includes('/community/capabilities?')), false);
    assert.equal(requests.some(path => path.endsWith('/creator')), false, 'prepared capture is reused');
  } finally { capture.unmount(); globalThis.fetch = oldFetch; }
});

test('Skill save still opens its version while imported APP returns to a highlighted personal card', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, requests = [], authored = [], opened = [];
  globalThis.document = { activeElement: null, querySelector: () => null };
  let saved = false, appSaved = false, releaseDirectory;
  const directoryGate = new Promise(resolve => { releaseDirectory = resolve; });
  globalThis.fetch = async path => {
    requests.push(path);
    if (saved && path.includes('/capabilities/assets?scope=personal&kind=skill')) {
      await directoryGate;
      return json({ items: [{ assetId: 'new-skill', latestVersionId: 'latest-v3', kind: 'skill', name: '新能力', description: '保存', readiness: 'unverified' }], nextCursor: null });
    }
    if (path.includes('/capabilities/assets?scope=')) return json({ items: [], nextCursor: null });
    if (path.endsWith('/capabilities/assets/new-skill')) return json({ assetId: 'new-skill', latestVersionId: 'latest-v3', kind: 'skill', name: '新能力' });
    if (path.includes('/capabilities/assets/new-skill/versions/saved-v2')) return json({ versionId: 'saved-v2', number: 2, readiness: 'unverified', manifest: manifest('skill'), contentDigest: 'saved' });
    if (path.endsWith('/asset-apps')) return json({ items: appSaved ? [{ id: 'new-app', versionId: 'new-v2', name: '新应用', verification: { status: 'unverified' } }] : [] });
    if (path.includes('/asset-apps/new-app?versionId=new-v2')) return json({ id: 'new-app', versionId: 'new-v2', definition: { format: 'cd80.app/2', name: '新应用' }, verification: { status: 'unverified' } });
    return json({ items: [], agents: [], strategyApps: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'save-owner', onAuthor: value => authored.push(value), onOpenAsset: value => opened.push(value) });
  try {
    app.mount(root); await settle();
    tab(root, 'skill').props.onClick(); await settle();
    button(root, '创建 Skill').props.onClick(); await settle();
    const creator = child(app._instance, 'CapabilityCreatePanel');
    assert.ok(creator, 'real Skill creator is mounted');
    saved = true; creator.emit('saved', { kind: 'skill', assetId: 'new-skill', versionId: 'saved-v2' });
    await nextTick();
    const detailDialog = find(root, node => node.tag === 'dialog' && String(node.props.class).includes('apps-library-dialog'));
    assert.ok(detailDialog, 'directory is mounted after save');
    releaseDirectory(); await settle();
    assert.ok(requests.some(path => path.includes('/capabilities/assets/new-skill/versions/saved-v2')));
    assert.match(content(root), /精确版本：V2 · saved-v2/);
    assert.match(content(root), /已保存到技能池/);
    tab(root, 'app').props.onClick(); await settle();
    button(root, '创建 APP').props.onClick(); await settle();
    const appCreator = child(app._instance, 'AppCreatePanel');
    assert.ok(appCreator, 'real APP creator is mounted');
    find(root, node => node.tag === 'button' && content(node).includes('用自然语言创建')).props.onClick(); await settle();
    assert.deepEqual(authored, [{ kind: 'app', intent: 'create', hideDraftOnly: true }]);
    assert.ok(child(app._instance, 'AppCreatePanel'), 'closing the authoring overlay returns to the two-choice page');
    appSaved = true; appCreator.emit('saved', { kind: 'app', appId: 'new-app', versionId: 'new-v2' }); await settle();
    const card = find(root, node => node.tag === 'button' && node.props['data-app-id'] === 'new-app');
    assert.ok(card, 'the saved APP appears in my APPs');
    assert.match(card.props.class, /is-recent/);
    assert.equal(card.scrolled, true);
    assert.equal(card.focused, true);
    assert.match(content(root), /APP 已保存到我的 APP/);
    assert.equal(requests.some(path => path.includes('/asset-apps/new-app?versionId=new-v2')), false, 'saving does not auto-open a detail');
    card.props.onClick(); await settle();
    button(root, '打开我的 APP').props.onClick(); await settle();
    assert.deepEqual(opened, [{ appId: 'new-app', versionId: 'new-v2' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('a natural-language completion request switches to Apps, refreshes and locates the saved card without opening it', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, request = ref(null), requests = [];
  globalThis.document = { activeElement: null, querySelector: () => null };
  globalThis.fetch = async path => {
    requests.push(path);
    if (path.endsWith('/asset-apps')) return json({ items: [{ id: 'language-app', versionId: 'created-v1', name: '自然语言生成', verification: { status: 'unverified' } }] });
    return json({ items: [], agents: [], strategyApps: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp({ setup: () => () => h(Panel, { ownerId: 'natural-owner', appListRequest: request.value }) });
  try {
    app.mount(root); await settle();
    tab(root, 'skill').props.onClick(); await settle();
    request.value = { appId: 'language-app', versionId: 'created-v1', token: 'completed-1' }; await settle();
    assert.equal(tab(root, 'app').props['aria-selected'], true);
    const card = find(root, node => node.tag === 'button' && node.props['data-app-id'] === 'language-app');
    assert.ok(card);
    assert.match(card.props.class, /is-recent/);
    assert.equal(card.scrolled, true);
    assert.equal(card.focused, true);
    assert.equal(Boolean(find(root, node => node.tag === 'dialog' && String(node.props.class).includes('apps-library-dialog'))?.open), false);
    assert.equal(requests.some(path => path.includes('/asset-apps/language-app?versionId=')), false);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('natural-language completion closes the creation page and returns to my APPs', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, request = ref(null);
  globalThis.document = { activeElement: null, querySelector: () => null };
  globalThis.fetch = async path => path.endsWith('/asset-apps')
    ? json({ items: [{ id: 'fresh-app', versionId: 'fresh-v1', name: '新生成 APP', verification: { status: 'unverified' } }] })
    : json({ items: [], agents: [], strategyApps: [], nextCursor: null });
  const root = hostNode('root'), app = renderer.createApp({ setup: () => () => h(Panel, { ownerId: 'creator-owner', appListRequest: request.value }) });
  try {
    app.mount(root); await settle();
    button(root, '创建 APP').props.onClick(); await settle();
    assert.ok(child(app._instance, 'AppCreatePanel'));
    find(root, node => node.tag === 'button' && content(node).includes('用自然语言创建')).props.onClick(); await settle();
    request.value = { appId: 'fresh-app', versionId: 'fresh-v1', token: 'authoring-complete' }; await settle();
    assert.equal(child(app._instance, 'AppCreatePanel'), null);
    assert.ok(find(root, node => node.props['data-app-id'] === 'fresh-app'));
    assert.match(content(root), /我的 APP/);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('closing a detail while an add is pending still refreshes the personal catalog after success', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, requests = [];
  globalThis.document = { activeElement: null };
  let copied = false, completeCopy;
  const copyGate = new Promise(resolve => { completeCopy = resolve; });
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || 'GET' });
    if (path.includes('/capabilities/assets?scope=personal&kind=skill')) return json({ items: copied ? [
      { assetId: 'mine-copy', latestVersionId: 'mine-v1', kind: 'skill', name: '个人已保存', readiness: 'unverified' }] : [], nextCursor: null });
    if (path.includes('/capabilities/assets?scope=public&kind=skill')) return json({ items: [
      { assetId: 'official-source', latestVersionId: 'official-v1', kind: 'skill', lifecycle: 'active', name: '官方待添加',
        owned: copied, personalCopies: copied ? [{ assetId: 'mine-copy', versionId: 'mine-v1' }] : [] }], nextCursor: null });
    if (path.includes('/community/capabilities?kind=skill')) return json({ items: [], nextCursor: null });
    if (path.includes('/capabilities/assets/official-source/copy') && options.method === 'POST') {
      await copyGate; copied = true; return json({ assetId: 'mine-copy', versionId: 'mine-v1', alreadyOwned: false });
    }
    if (path.includes('/capabilities/assets/official-source/versions/official-v1')) return json({
      versionId: 'official-v1', manifest: manifest('skill'), personalCopies: [], owned: false, contentDigest: 'exact-version',
    });
    if (path.includes('/capabilities/assets/official-source')) return json({ assetId: 'official-source', latestVersionId: 'official-v1', kind: 'skill' });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Directory, { ownerId: 'reader', kind: 'skill' });
  try {
    app.mount(root); await settle();
    find(root, node => node.tag === 'button' && String(node.props.class).includes('apps-library-card') && content(node).includes('官方待添加')).props.onClick();
    await settle();
    const add = button(root, '添加为我的 Skill'); assert.ok(add);
    add.props.onClick(); await flush();
    find(root, node => node.tag === 'dialog').close();
    completeCopy(); await settle();
    assert.match(content(root), /个人已保存/);
    assert.match(content(root), /已保存个人副本/);
    assert.ok(requests.filter(item => item.path.includes('/capabilities/assets?scope=personal&kind=skill')).length >= 2);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('new saved strategy opens its unverified pool detail even before a run exists', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, opened = [], requests = [];
  globalThis.document = { activeElement: null };
  globalThis.fetch = async path => {
    requests.push(path);
    if (path.endsWith('/agents')) return json({ agents: [], strategyApps: [{ id: 'strategy-new', versionId: 'rules-v1', name: '我的新策略', verification: { status: 'unverified' } }] });
    if (path.endsWith('/strategy-workshop/personal-apps/strategy-new')) return json({ app: { id: 'strategy-new', versionId: 'rules-v1', name: '我的新策略', verification: { status: 'unverified' } }, run: null, definition: { idea: '按自己的规则买卖' } });
    return json({ items: [], nextCursor: null });
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'strategy-owner', initialStrategyAppId: 'strategy-new', onInitialStrategyOpened: () => opened.push('opened') });
  try {
    app.mount(root); await settle();
    assert.equal(opened.length, 1);
    assert.match(content(root), /我的新策略/);
    assert.match(content(root), /未验证 · 尚未运行诊断回测/);
    assert.ok(requests.some(path => path.endsWith('/strategy-workshop/personal-apps/strategy-new')));
    assert.ok(find(root, node => node.tag === 'dialog' && node.open));
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('an owned public Tool opens manifest details and manages its historical personal version', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, managed = [], requests = [];
  globalThis.document = { activeElement: null };
  globalThis.fetch = async (path, options = {}) => {
    requests.push({ path, method: options.method || 'GET' });
    if (path.includes('/capabilities/assets?')) return json({ items: [], nextCursor: null });
    if (path.includes('/community/capabilities?kind=tool')) return json({ items: [
      { releaseId: 'public-tool', kind: 'tool', title: '公开计算工具', status: 'published', owned: true,
        personalCopies: [{ assetId: 'owned-tool', versionId: 'older-version' }] }], nextCursor: null });
    if (path.endsWith('/community/capabilities/public-tool')) return json({
      releaseId: 'public-tool', kind: 'tool', title: '公开计算工具', owned: true,
      personalCopies: [{ assetId: 'owned-tool', versionId: 'older-version' }],
      root: { manifest: { ...manifest('tool'), dependencies: [{ sourceIdentity: 'public/dependency', contentDigest: 'locked' }] },
        contentDigest: 'public-digest' },
    });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Directory, { ownerId: 'reader', kind: 'tool', onManage: value => managed.push(value) });
  try {
    app.mount(root); await settle();
    find(root, node => node.tag === 'button' && String(node.props.class).includes('apps-library-card') && content(node).includes('公开计算工具')).props.onClick();
    await settle();
    assert.match(content(root), /tool.json/);
    assert.match(content(root), /public\/dependency/);
    assert.equal(button(root, '已拥有').props.disabled, true);
    button(root, '管理与启用').props.onClick(); await settle();
    assert.deepEqual(managed, [{ assetId: 'owned-tool', versionId: 'older-version' }]);
    assert.ok(requests.every(item => item.method === 'GET'));
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});

test('a newly saved version remains the management target after directory refresh even when an older version is active', async () => {
  const oldFetch = globalThis.fetch, oldDocument = globalThis.document, managed = [], requests = [];
  globalThis.document = { activeElement: null };
  globalThis.fetch = async path => {
    requests.push(path);
    if (path.includes('/capabilities/assets?scope=personal&kind=skill')) return json({ items: [
      { assetId: 'versioned', latestVersionId: 'new-v2', activeVersionId: 'old-v1', kind: 'skill', name: '多版本方法', readiness: 'verified' }], nextCursor: null });
    if (path.includes('/capabilities/assets?scope=public&kind=skill') || path.includes('/community/capabilities?kind=skill')) return json({ items: [], nextCursor: null });
    if (path.endsWith('/capabilities/assets/versioned')) return json({ assetId: 'versioned', latestVersionId: 'new-v2', activeVersionId: 'old-v1', kind: 'skill', name: '多版本方法' });
    if (path.endsWith('/capabilities/assets/versioned/versions/new-v2')) return json({ versionId: 'new-v2', number: 2, readiness: 'unverified', manifest: manifest('skill') });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Directory, {
    ownerId: 'version-owner', kind: 'skill', initialAssetId: 'versioned', initialVersionId: 'new-v2', onManage: value => managed.push(value),
  });
  try {
    app.mount(root); await settle();
    assert.ok(requests.some(path => path.endsWith('/capabilities/assets/versioned/versions/new-v2')));
    assert.match(content(root), /精确版本：V2 · new-v2/);
    button(root, '刷新目录').props.onClick(); await settle();
    button(root, '管理与验证').props.onClick(); await settle();
    assert.deepEqual(managed, [{ assetId: 'versioned', versionId: 'new-v2' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; globalThis.document = oldDocument; }
});
