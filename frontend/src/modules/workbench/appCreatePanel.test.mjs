import test from 'node:test';
import assert from 'node:assert/strict';
import { h, ref } from 'vue';
import { component, renderer, hostNode, find, all, content, flush } from './assetComponentHarness.mjs';
import { exampleAppDefinition } from './appCreateModel.js';

const Panel = await component('./components/AppCreatePanel.vue');
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 6; index++) await flush(); };
const button = (root, label) => find(root, node => node.tag === 'button' && content(node).includes(label));
const upload = async (root, file) => {
  find(root, node => node.props['aria-label'] === '上传 APP Python、JSON 或 ZIP').props.onChange({ target: { files: [file], value: file.name } });
  await settle();
};

test('APP creator offers only external import and natural language creation', async () => {
  const authored = [], root = hostNode('root');
  const app = renderer.createApp(Panel, { ownerId: 'owner', onAuthor: value => authored.push(value) });
  try {
    app.mount(root); await settle();
    const cards = find(root, node => node.props.class === 'choice-grid');
    assert.equal(cards.children.filter(node => node.tag === 'button').length, 2);
    assert.match(content(cards), /从外部导入/);
    assert.match(content(cards), /用自然语言创建/);
    assert.doesNotMatch(content(root), /手写完整实现|组合现有组件|完整 APP 定义 JSON/);
    button(root, '用自然语言创建').props.onClick(); await settle();
    assert.deepEqual(authored, [{ kind: 'app', intent: 'create', hideDraftOnly: true }]);
  } finally { app.unmount(); }
});

test('external path offers modern APP examples, format contracts and data interface download', async () => {
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner' });
  try {
    app.mount(root); await settle(); button(root, '从外部导入').props.onClick(); await settle();
    assert.deepEqual(all(root, node => node.tag === 'a').map(node => [content(node), node.props.href]), [
      ['下载完整 JSON 示例', '/desktop/api/asset-apps/import/template.json'],
      ['下载 Python 示例', '/desktop/api/asset-apps/import/example.py'],
      ['下载源码 ZIP 示例', '/desktop/api/asset-apps/import/template.zip'],
      ['下载 APP 格式规范', '/desktop/api/asset-apps/import/schema.json'],
      ['下载 APP 接口说明', '/desktop/api/asset-apps/import/specification.json'],
      ['下载数据接口资料', '/desktop/api/asset-apps/import/data-interface.zip'],
    ]);
    assert.match(find(root, node => node.props['aria-label'] === '上传 APP Python、JSON 或 ZIP').props.accept, /\.py,.zip,.json/);
    button(root, '返回创建方式').props.onClick(); await settle();
    assert.match(content(root), /用自然语言创建/);
  } finally { app.unmount(); }
});

test('Python upload saves the previewed package without running or blocking on runtime issues', async () => {
  const oldFetch = globalThis.fetch, calls = [], saved = [];
  const definition = exampleAppDefinition(), pkg = { format: 'cd80.app-package/2', definition, digest: 'sample-digest' };
  globalThis.fetch = async (path, options = {}) => {
    const body = options.body instanceof FormData ? options.body : options.body ? JSON.parse(options.body) : null;
    calls.push({ path, body });
    if (path.endsWith('/asset-apps/import/file-preview')) return json({ package: pkg, definition, issues: [{ message: '连接尚未配置' }], warnings: [] });
    if (path.endsWith('/asset-apps/import')) return json({ id: 'app-python', versionId: 'av1', verification: { status: 'unverified' } });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner', onSaved: value => saved.push(value) });
  try {
    app.mount(root); await settle(); button(root, '从外部导入').props.onClick(); await settle();
    await upload(root, new File(['APP_INTERFACE = {}\ndef run(payload):\n    return payload\n'], 'example.py', { type: 'text/x-python' }));
    assert.equal(calls[0].body instanceof FormData, true);
    assert.equal(calls[0].body.get('file').name, 'example.py');
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /连接尚未配置/);
    assert.equal(button(root, '导入并保存，不运行').props.disabled, false);
    button(root, '导入并保存，不运行').props.onClick(); await settle();
    assert.deepEqual(calls.find(call => call.path.endsWith('/asset-apps/import')).body, { package: pkg, bindings: {} });
    assert.deepEqual(saved, [{ kind: 'app', appId: 'app-python', versionId: 'av1' }]);
    assert.ok(calls.every(call => !/\/verify|\/enable|\/runs|\/creator/.test(call.path)));
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('unknown ZIP save retries the same frozen package and key', async () => {
  const oldFetch = globalThis.fetch, posts = [], saved = [];
  const definition = exampleAppDefinition(), pkg = { format: 'cd80.app-package/2', definition, digest: 'zip-digest' };
  globalThis.fetch = async (path, options = {}) => {
    if (path.endsWith('/asset-apps/import/file-preview')) return json({ package: pkg, definition, issues: [], warnings: [] });
    if (path.endsWith('/asset-apps/import')) {
      posts.push({ body: JSON.parse(options.body), key: options.headers['Idempotency-Key'] });
      return posts.length === 1 ? json({ error: '结果未确认' }, 503) : json({ id: 'app-zip', versionId: 'av2' });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner', onSaved: value => saved.push(value) });
  try {
    app.mount(root); await settle(); button(root, '从外部导入').props.onClick(); await settle();
    await upload(root, new File(['zip bytes'], 'source.zip', { type: 'application/zip' }));
    button(root, '导入并保存，不运行').props.onClick(); await settle();
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /上次保存结果尚未核实/);
    assert.equal(button(root, '返回 Apps').props.disabled, true);
    assert.equal(find(root, node => node.tag === 'fieldset').props.disabled, true);
    button(root, '核实原提交').props.onClick(); await settle();
    assert.equal(posts.length, 2);
    assert.deepEqual(posts[0].body, posts[1].body);
    assert.equal(posts[0].key, posts[1].key);
    assert.deepEqual(saved, [{ kind: 'app', appId: 'app-zip', versionId: 'av2' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('changing account aborts a pending import and discards its late result', async () => {
  const oldFetch = globalThis.fetch, owner = ref('first'), saved = [];
  const definition = exampleAppDefinition(), pkg = { format: 'cd80.app-package/2', definition, digest: 'digest' };
  let resolveSave, signal;
  globalThis.fetch = async (path, options = {}) => {
    if (path.endsWith('/asset-apps/import/file-preview')) return json({ package: pkg, definition, issues: [], warnings: [] });
    if (path.endsWith('/asset-apps/import')) { signal = options.signal; return new Promise(resolve => { resolveSave = resolve; }); }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp({ setup: () => () => h(Panel, { ownerId: owner.value, onSaved: value => saved.push(value) }) });
  try {
    app.mount(root); await settle(); button(root, '从外部导入').props.onClick(); await settle();
    await upload(root, new File(['{}'], 'app.json', { type: 'application/json' }));
    button(root, '导入并保存，不运行').props.onClick(); await settle();
    owner.value = 'second'; await settle();
    assert.equal(signal.aborted, true);
    resolveSave(json({ id: 'stale', versionId: 'old-version' })); await settle();
    assert.deepEqual(saved, []);
    assert.equal(find(root, node => node.props.class === 'choice-grid').children.filter(node => node.tag === 'button').length, 2);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});
