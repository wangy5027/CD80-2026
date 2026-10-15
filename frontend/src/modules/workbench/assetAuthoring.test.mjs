import test from 'node:test';
import assert from 'node:assert/strict';
import { createRenderer, nextTick } from 'vue';
import { createAssetAppsApi } from './assetAppsApi.js';
import { useAssetApps } from './useAssetApps.js';
import { artifactUrl, bindingText, fileAliases, inputDefaults, parseRunInput, resourceFiles, updateResource, verificationCases } from './assetAppsModel.js';

const definition = { format: 'cd80.app/2', name: 'prices', inputSchema: { type: 'object', required: ['prices'], properties: { prices: { type: 'array', items: { type: 'number' } }, label: { type: 'string', default: '研究' }, enabled: { type: 'boolean' }, limit: { type: 'integer' } } }, rules: [], nodes: [{ id: 'read', kind: 'code', inputBindings: { data: { from: 'file', input: 'csv' }, prices: { from: 'input', path: 'prices' } } }], outputs: {} };
const asset = (overrides = {}) => ({ id: 'app-a', versionId: 'v1', revision: 1, definition, verification: { status: 'unverified', cases: [] }, ...overrides });
const flush = async () => { await nextTick(); await new Promise(resolve => setImmediate(resolve)); await nextTick(); };
function harness(transport) {
  const renderer = createRenderer({ createElement: () => ({}), createText: () => ({}), createComment: () => ({}), insert() {}, remove() {}, setText() {}, setElementText() {}, parentNode: () => null, nextSibling: () => null, patchProp() {} });
  let work;
  const app = renderer.createApp({ setup() { work = useAssetApps('owner-a', transport); return () => null; } });
  app.mount({}); return { work, app };
}

test('unified app API uses one relative prefix and preserves unknown writes across panel remounts', async () => {
  const calls = [], controller = new AbortController();
  const transport = async (path, options) => { calls.push({ path, options }); if (calls.length === 1) throw new Error('response lost'); return asset(); };
  const body = { definition };
  await assert.rejects(createAssetAppsApi('owner-unknown', controller.signal, transport).save(body), /response lost/);
  await createAssetAppsApi('owner-unknown', controller.signal, transport).save(body);
  assert.equal(calls[0].path, '/asset-apps'); assert.equal(calls[0].options.headers['Idempotency-Key'], calls[1].options.headers['Idempotency-Key']);
  await createAssetAppsApi('owner-other', controller.signal, transport).save(body);
  assert.notEqual(calls[1].options.headers['Idempotency-Key'], calls[2].options.headers['Idempotency-Key']);
});

test('known write errors permit a new request and modified bodies never reuse an uncertain request identity', async () => {
  const calls = [], controller = new AbortController();
  const api = createAssetAppsApi('owner-errors', controller.signal, async (_path, options) => { calls.push(options.headers['Idempotency-Key']); throw Object.assign(new Error('conflict'), { status: 409 }); });
  await assert.rejects(api.save({ definition })); await assert.rejects(api.save({ definition })); assert.notEqual(calls[0], calls[1]);
  const keys = []; const other = createAssetAppsApi('owner-change', controller.signal, async (_path, options) => { keys.push(options.headers['Idempotency-Key']); throw new Error('unknown'); });
  await assert.rejects(other.save({ definition })); await assert.rejects(other.save({ definition: { ...definition, name: 'new' } })); assert.notEqual(keys[0], keys[1]);
});

test('Creator preserves selected Tool and Skill targets and forwards full draft dependencies', async () => {
  const calls = [], api = createAssetAppsApi('creator-owner', new AbortController().signal, async (path, options) => { calls.push({ path, body: options.body }); return {}; });
  await api.creator({ sourceRunId: 'historical' });
  await api.creator({ kind: 'tool', sourceRunId: 'historical', stepIds: ['one', 'two'] });
  const draft = { kind: 'skill', files: [{ path: 'SKILL.md', text: '# skill' }], dependencySnapshots: [{ files: { 'tool.py': 'YWJj' } }] };
  await api.creator({ kind: 'skill', draft, instruction: '保留完整资源，补充约束' });
  assert.deepEqual(calls.map(call => call.body.kind), ['app', 'tool', 'skill']);
  assert.deepEqual(calls[1].body.stepIds, ['one', 'two']); assert.deepEqual(calls[2].body.draft, draft);
  assert.ok(calls.every(call => call.path === '/asset-apps/creator'));
});

test('a still-running Creator retains its request identity until its eventual response', async () => {
  const keys = [], api = createAssetAppsApi('creator-running-owner', new AbortController().signal, async (_path, options) => {
    keys.push(options.headers['Idempotency-Key']);
    if (keys.length === 1) throw Object.assign(new Error('still running'), { status: 409, code: 'CREATOR_RUNNING' });
    return { definition };
  });
  const body = { instruction: '固定方法', definition };
  await assert.rejects(api.creator(body), /still running/); await api.creator(body);
  assert.equal(keys[0], keys[1]);
});

test('App connection configuration is explicit and import only carries selected recipient bindings', async () => {
  const calls = [], api = createAssetAppsApi('bindings-owner', new AbortController().signal, async (path, options) => { calls.push({ path, body: options.body }); return {}; });
  const packageValue = { format: 'cd80.app-package/2', definition };
  await api.importPreview(packageValue); assert.deepEqual(calls[0].body, { package: packageValue });
  await api.importApp(packageValue, { 'source/data': 'own-connection' });
  await api.configure('my/app', { versionId: 'v2', revision: 3, bindings: {} });
  assert.deepEqual(calls[1].body.bindings, { 'source/data': 'own-connection' });
  assert.deepEqual(calls[2], { path: '/asset-apps/my%2Fapp/bindings', body: { versionId: 'v2', revision: 3, bindings: {} } });
  assert.ok(calls.every(call => !call.path.includes('verify') && !call.path.includes('/runs')));
});

test('run inputs retain declared types and only explicit current file aliases are authorized', () => {
  assert.deepEqual(fileAliases(definition), ['csv']); assert.equal(inputDefaults(definition).label, '研究');
  const parsed = parseRunInput(definition, { prices: '[100,120,90,108]', enabled: false, limit: '4', extra: 'ignore' }, { csv: 'new-file', oldFile: 'private-old' });
  assert.deepEqual(parsed, { input: { prices: [100, 120, 90, 108], enabled: false, limit: 4 }, fileBindings: { csv: 'new-file' } });
  assert.throws(() => parseRunInput(definition, { prices: '{}'}), /类型/);
  assert.throws(() => parseRunInput(definition, { prices: '[]', limit: '1.5'}), /整数/);
  assert.deepEqual(parseRunInput(definition, { prices: '[]' }).fileBindings, {});
});

test('validation requires different input cases and objective nonempty expectations', () => {
  const first = { input: '{"prices":[100,120,90,108]}', expectation: '{"mode":"fields","values":{"analysis.change":0.08}}' };
  const second = { input: '{"prices":[80,100,95,120]}', expectation: '{"mode":"fields","values":{"analysis.change":0.5}}' };
  assert.deepEqual(verificationCases([first])[0].input.prices, [100,120,90,108]);
  assert.equal(verificationCases([first, second]).length, 2);
  assert.throws(() => verificationCases([first, first]), /不同输入/);
  assert.throws(() => verificationCases([first, { ...second, expectation: '{"mode":"fields","values":{}}' }]), /实际检查项/);
  assert.throws(() => verificationCases([first, { ...second, expectation: '{"mode":"model_says_pass"}' }]), /预期模式/);
});

test('component file snapshots preserve binary bytes and editing text labels a modified implementation', () => {
  const node = { kind: 'skill', provenance: { type: 'historical', runId: 'old' }, component: { files: { 'SKILL.md': Buffer.from('# 方法\n', 'utf8').toString('base64'), 'plot.png': btoa('\u0000\u0001'), 'bad.py': '/w==' } } };
  // Unicode source uses a UTF-8 encoded snapshot rather than a browser binary string.
  node.component.files['SKILL.md'] = Buffer.from('# 方法\n', 'utf8').toString('base64');
  const files = resourceFiles(node); assert.equal(files[0].displayText, '# 方法\n'); assert.equal(files[1].displayText, null); assert.equal(files[2].displayText, null);
  const updated = updateResource(node, 'SKILL.md', '# 新方法\n');
  assert.equal(updated.provenance.type, 'modified'); assert.equal(updated.provenance.runId, 'old'); assert.equal(resourceFiles(updated)[0].displayText, '# 新方法\n');
  assert.equal(updated.component.files['plot.png'], node.component.files['plot.png']); assert.equal(node.provenance.type, 'historical');
});

test('input sources remain explicit and artifact links cannot navigate to untrusted protocols', () => {
  assert.equal(bindingText({ from: 'artifact', stepId: 'fetch', name: 'data.csv' }), '前序产物 fetch · data.csv');
  assert.equal(bindingText({ from: 'step', stepId: 'calculate', path: 'return' }), '前序步骤 calculate · return');
  assert.equal(artifactUrl('javascript:alert(1)'), ''); assert.equal(artifactUrl('//evil.test/data'), ''); assert.equal(artifactUrl('/desktop/api/\\evil.test'), '');
  assert.equal(artifactUrl('/desktop/api/asset-apps/artifacts/a'), '/desktop/api/asset-apps/artifacts/a');
});

test('catalog and observation are read only and an unloaded panel cannot accept a late result', async () => {
  const calls = [], delayed = [];
  const h = harness((path, options = {}) => { calls.push(options.method || 'GET'); return new Promise(resolve => delayed.push({ path, resolve })); });
  await flush(); h.app.unmount();
  delayed.forEach(item => item.resolve({ items: [asset({ name: 'private-old-owner' })] })); await flush();
  assert.deepEqual(h.work.s.items, []); assert.ok(calls.every(method => method === 'GET'));
});

test('version selection discards a late previous version and observes the actual run version', async () => {
  let releaseOld; const calls = [];
  const h = harness(async (path, options = {}) => {
    calls.push({ path, method: options.method || 'GET' });
    if (path === '/asset-apps' || path === '/asset-apps/legacy' || path.endsWith('/runs')) return { items: [] };
    if (path.endsWith('versionId=v1')) return new Promise(resolve => { releaseOld = resolve; });
    return asset({ versionId: path.includes('v3') ? 'v3' : 'v2', definition: { ...definition, name: path.includes('v3') ? 'version-three' : 'version-two' } });
  });
  try {
    await flush(); const old = h.work.select('app-a', 'v1'); await flush(); await h.work.select('app-a', 'v2');
    releaseOld(asset({ name: 'old' })); await old; assert.equal(h.work.s.selected.versionId, 'v2');
    await h.work.observeRun({ id: 'run-old', appId: 'app-a', versionId: 'v3' }); assert.equal(h.work.s.activeDefinition.name, 'version-three');
    assert.ok(calls.every(call => call.method === 'GET'));
  } finally { h.app.unmount(); }
});

test('saving disables concurrent mutation and a failed catalog refresh cannot turn successful save into a retry', async () => {
  let resolveSave, failCatalog = false, posts = 0;
  const h = harness(async (path, options = {}) => {
    if (options.method === 'POST') { posts++; return new Promise(resolve => { resolveSave = resolve; }); }
    if (failCatalog && path === '/asset-apps') throw new Error('catalog down');
    return { items: [] };
  });
  try {
    await flush(); const first = h.work.save({ definition }); await flush(); assert.equal(h.work.s.busy, true);
    assert.equal(await h.work.save({ definition }), null); failCatalog = true; resolveSave(asset());
    const saved = await first; assert.equal(saved.id, 'app-a'); assert.equal(posts, 1); assert.match(h.work.s.error, /内容已保存/); assert.equal(h.work.s.busy, false);
  } finally { h.app.unmount(); }
});
