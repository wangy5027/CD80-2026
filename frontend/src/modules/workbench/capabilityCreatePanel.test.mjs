import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { h, ref } from 'vue';
import { component, renderer, hostNode, find, all, content, flush, submitEvent } from './assetComponentHarness.mjs';

const Panel = await component('./components/CapabilityCreatePanel.vue');
const json = value => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const settle = async () => { for (let index = 0; index < 6; index++) await flush(); };
const button = (root, label) => find(root, node => node.tag === 'button' && content(node).includes(label));
const set = (node, value) => { assert.ok(node?.props?.['onUpdate:modelValue']); node.props['onUpdate:modelValue'](value); };
const digest = value => createHash('sha256').update(value).digest('hex');
const preview = (kind, sha, extra = {}) => ({ sha256: sha, originalName: kind === 'skill' ? 'SKILL.md' : 'tool.json', kind,
  name: kind === 'skill' ? 'review-source' : 'review_source', description: 'Description', sourceIdentity: 'source/review',
  manifest: { format: 'cd80.capability/1', kind, source: { format: kind === 'skill' ? 'agent_skills' : 'claude_tool' } },
  files: [{ path: kind === 'skill' ? 'original/SKILL.md' : 'original/tool.json', sha256: sha, size: 12 }],
  issues: [], staticReadiness: 'unverified', contentValidity: 'valid', warnings: [], ...extra });

test('complete SKILL.md text previews exact bytes, then saves the checked original without execution', async () => {
  const oldFetch = globalThis.fetch, calls = [], saved = [];
  const original = '---\r\nname: review-source\r\ndescription: Review source\r\n---\r\n\r\n# Exact text\r\n';
  globalThis.fetch = async (path, options = {}) => {
    calls.push({ path, options });
    if (path.endsWith('/capabilities/imports/preview')) {
      const file = options.body.get('file');
      assert.equal(options.body.get('kind'), 'skill');
      assert.equal(await file.text(), original);
      return json(preview('skill', digest(Buffer.from(original))));
    }
    if (path.endsWith('/capabilities/imports')) {
      const file = options.body.get('file');
      assert.equal(await file.text(), original);
      assert.equal(options.body.get('expectedDigest'), digest(Buffer.from(original)));
      return json({ jobId: 'job-skill', importId: 'imp-skill', status: 'completed', result: { assetId: 'asset-skill', versionId: 'version-skill' } });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner-skill-raw', kind: 'skill', onSaved: item => saved.push(item) });
  try {
    app.mount(root); await settle();
    button(root, '填写完整原文').props.onClick(); await settle();
    set(find(root, node => node.props['aria-label'] === '完整 SKILL.md 原文'), original); await settle();
    button(root, '预览文件清单与兼容性').props.onClick(); await settle();
    assert.match(content(root), /原件 SHA-256/);
    button(root, '保存原件，不运行').props.onClick(); await settle();
    assert.deepEqual(saved, [{ kind: 'skill', assetId: 'asset-skill', versionId: 'version-skill' }]);
    assert.equal(calls.filter(item => item.path.endsWith('/capabilities/imports')).length, 1);
    assert.ok(calls.every(item => !/\/verifications|\/invocations|\/enable/.test(item.path)));
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('Tool JSON without implementation shows unavailable status; unknown import retries the original file and key', async () => {
  const oldFetch = globalThis.fetch, calls = [], saved = [];
  let attempts = 0;
  const source = '{"name":"review_source","description":"Review","input_schema":{"type":"object"}}';
  const sha = digest(Buffer.from(source));
  globalThis.fetch = async (path, options = {}) => {
    if (path.endsWith('/capabilities/imports/preview')) return json(preview('tool', sha, { staticReadiness: 'needs_configuration',
      issues: [{ code: 'MISSING_IMPLEMENTATION', message: '缺少执行实现', severity: 'configuration' }] }));
    if (path.endsWith('/capabilities/imports')) {
      calls.push({ text: await options.body.get('file').text(), digest: options.body.get('expectedDigest'), key: options.headers['Idempotency-Key'] });
      attempts++;
      return attempts === 1 ? new Response(JSON.stringify({ error: '暂时未确认' }), { status: 503, headers: { 'Content-Type': 'application/json' } })
        : json({ jobId: 'job-tool', importId: 'imp-tool', status: 'completed', result: { assetId: 'asset-tool', versionId: 'version-tool' } });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner-tool-unknown', kind: 'tool', onSaved: item => saved.push(item) });
  try {
    app.mount(root); await settle();
    button(root, '填写完整原文').props.onClick(); await settle();
    assert.match(content(root), /分项填写.*ZIP/);
    set(find(root, node => node.props['aria-label'] === '完整 tool.json 原文'), source); await settle();
    button(root, '预览文件清单与兼容性').props.onClick(); await settle();
    assert.match(content(root), /缺少执行实现.*才能启用/);
    button(root, '保存原件，不运行').props.onClick(); await settle();
    assert.equal(button(root, '返回技能池').props.disabled, true);
    assert.equal(button(root, '上传原件 / ZIP').props.disabled, true);
    set(find(root, node => node.props['aria-label'] === '完整 tool.json 原文'), source + '\n'); await settle(); // Disabled DOM cannot do this.
    button(root, '核实原提交').props.onClick(); await settle();
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], calls[1]);
    assert.equal(calls[0].text, source);
    assert.deepEqual(saved, [{ kind: 'tool', assetId: 'asset-tool', versionId: 'version-tool' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('ZIP upload keeps File identity, and owner switch aborts late save without cross-account emission', async () => {
  const oldFetch = globalThis.fetch, owner = ref('owner-zip-first'), saved = [];
  let resolveSave, firstSignal, starts = 0;
  const file = new File(['PK test fixture'], 'skill-package.zip', { type: 'application/zip' });
  const sha = digest(Buffer.from(await file.arrayBuffer()));
  globalThis.fetch = async (path, options = {}) => {
    if (path.endsWith('/capabilities/imports/preview')) return json(preview('skill', sha, { originalName: file.name }));
    if (path.endsWith('/capabilities/imports')) {
      starts++;
      if (starts === 1) { firstSignal = options.signal; return new Promise(resolve => { resolveSave = resolve; }); }
      assert.equal(options.body.get('expectedDigest'), sha);
      return json({ jobId: 'job-retried', status: 'completed', result: { assetId: 'asset-after-return', versionId: 'version-after-return' } });
    }
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp({ setup: () => () => h(Panel, { ownerId: owner.value, kind: 'skill', onSaved: item => saved.push(item) }) });
  try {
    app.mount(root); await settle();
    button(root, '上传原件 / ZIP').props.onClick(); await settle();
    find(root, node => node.props['aria-label'] === '上传 Skill 或 Tool 原件').props.onChange({ target: { files: [file], value: file.name } }); await settle();
    button(root, '导入并保存，不运行').props.onClick(); await settle();
    assert.ok(resolveSave);
    owner.value = 'owner-zip-second'; await settle();
    assert.equal(firstSignal.aborted, true);
    resolveSave(json({ jobId: 'stale-job', status: 'completed', result: { assetId: 'stale', versionId: 'stale' } })); await settle();
    assert.deepEqual(saved, []);
    assert.doesNotMatch(content(root), /上次导入结果尚未核实/);
    owner.value = 'owner-zip-first'; await settle();
    assert.match(content(root), /当前网络连接失败/); assert.doesNotMatch(content(root), /上次导入结果尚未核实/);
    button(root, '核实原提交').props.onClick(); await settle();
    assert.equal(starts, 2);
    assert.deepEqual(saved, [{ kind: 'skill', assetId: 'asset-after-return', versionId: 'version-after-return' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});

test('the existing structured Skill form remains available inside the new creator', async () => {
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner-structured', kind: 'skill' });
  try {
    app.mount(root); await settle();
    button(root, '分项填写').props.onClick(); await settle();
    assert.match(content(root), /标准 SKILL.md/);
    assert.match(content(root), /额外标准 frontmatter/);
    assert.match(content(root), /附加资源/);
  } finally { app.unmount(); }
});

test('structured Skill first-create unknown locks both editors and retries its original request key', async () => {
  const oldFetch = globalThis.fetch, creates = [], saved = [];
  globalThis.fetch = async (path, options = {}) => {
    if (path.endsWith('/capabilities/drafts') && options.method === 'POST') {
      creates.push({ body: JSON.parse(options.body), key: options.headers['Idempotency-Key'] });
      return creates.length === 1 ? new Response(JSON.stringify({ error: '结果未知' }), { status: 503, headers: { 'Content-Type': 'application/json' } })
        : json({ draftId: 'draft-recovered', revision: 1 });
    }
    if (path.endsWith('/capabilities/drafts/draft-recovered/save')) return json({ status: 'completed', result: { assetId: 'asset-structured', versionId: 'version-structured' } });
    throw new Error('unexpected request ' + path);
  };
  const root = hostNode('root'), app = renderer.createApp(Panel, { ownerId: 'owner-structured-unknown', kind: 'skill', onSaved: item => saved.push(item) });
  try {
    app.mount(root); await settle();
    button(root, '分项填写').props.onClick(); await settle();
    const inputs = all(root, node => node.tag === 'input' && node.props.type !== 'checkbox' && node.props.type !== 'file');
    const areas = all(root, node => node.tag === 'textarea');
    set(inputs[0], 'review-source'); set(areas[0], 'Check provided sources.'); set(areas[1], '# Steps\nCheck the source.'); await settle();
    const form = find(root, node => node.tag === 'form');
    form.props.onSubmit(submitEvent()); await settle();
    assert.match(content(root), /核实原提交/);
    assert.equal(button(root, '上传原件 / ZIP').props.disabled, true);
    assert.equal(button(root, '返回技能池').props.disabled, true);
    assert.equal(button(root, '取消').props.disabled, true);
    set(areas[1], '# Changed after unknown'); await settle();
    form.props.onSubmit(submitEvent()); await settle();
    assert.equal(creates.length, 2);
    assert.deepEqual(creates[0], creates[1]);
    assert.deepEqual(saved, [{ kind: 'skill', assetId: 'asset-structured', versionId: 'version-structured' }]);
  } finally { app.unmount(); globalThis.fetch = oldFetch; }
});
