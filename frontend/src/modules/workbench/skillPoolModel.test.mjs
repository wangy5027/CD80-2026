import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPersonalComponents, makeAppDefinition, makeSkillDraft, makeSkillDocument, makeToolDraft,
  recommendedBindings } from './skillPoolModel.js';

const skillFields = { name: 'portfolio-review', description: '在需要复核持仓时使用。按输入检查持仓与来源。', body: '# 核对\n\n读取用户本次提供的数据。',
  permissions: { inputFiles: true }, resources: [{ path: 'references/guide.md', text: '# 原样保留的参考资料\n' }] };
const definition = { name: 'calculate_total', description: '汇总本次输入；在需要计算合计时使用。',
  input_schema: { type: 'object', properties: { values: { type: 'array', items: { type: 'number' } } }, required: ['values'] }, strict: true };

test('standard Skill content is valid YAML frontmatter and separate from platform permissions', () => {
  const doc = makeSkillDocument(skillFields);
  assert.match(doc, /^---\nname: "portfolio-review"\ndescription: "在需要复核持仓时使用。按输入检查持仓与来源。"\n---\n\n# 核对/);
  const draft = makeSkillDraft(skillFields, 'personal/fixed');
  assert.equal(draft.files[0].path, 'original/SKILL.md');
  assert.equal(draft.files[0].text, doc);
  assert.deepEqual(draft.files[1], { path: 'original/references/guide.md', text: '# 原样保留的参考资料\n' });
  assert.equal(draft.manifest.source.format, 'agent_skills');
  assert.equal(draft.manifest.permissions.inputFiles, true);
  assert.equal(draft.manifest.permissions.outputFiles, false);
  assert.doesNotMatch(doc, /inputFiles|cd80\.capability/);
});

test('optional standard Skill frontmatter survives without overwriting identity or platform permissions', () => {
  const extra = { license: 'MIT', compatibility: 'Requires local CSV data', metadata: { author: 'owner', revision: 2 }, 'allowed-tools': ['Read', 'Write'] };
  const draft = makeSkillDraft({ ...skillFields, extraFrontmatterText: JSON.stringify(extra) }, 'personal/metadata');
  const source = draft.files[0].text;
  for (const [key, value] of Object.entries(extra)) assert.ok(source.includes(`${JSON.stringify(key)}: ${JSON.stringify(value)}`));
  assert.equal(draft.manifest.permissions.inputFiles, true);
  assert.equal(draft.manifest.source.format, 'agent_skills');
  assert.throws(() => makeSkillDocument({ ...skillFields, extraFrontmatterText: '{"name":"other"}' }), /不能覆盖/);
  assert.throws(() => makeSkillDocument({ ...skillFields, extraFrontmatterText: '[]' }), /JSON 对象/);
});

test('Anthropic Tool JSON stays byte-for-byte separate from optional execution', () => {
  const original = JSON.stringify(definition, null, 2) + '\n';
  const draft = makeToolDraft({ definitionText: original, pythonCode: 'def run(payload):\n    return {"total": sum(payload["values"])}\n', resources: [] }, 'personal/tool-1');
  assert.equal(draft.files[0].text, original);
  assert.equal(draft.files[1].path, 'original/tool.py');
  assert.deepEqual(draft.manifest.tool.inputSchema, definition.input_schema);
  assert.deepEqual(draft.manifest.tool.execution, { type: 'python_wasi', path: 'original/tool.py', callable: 'run' });
  assert.equal(draft.files[0].text.includes('python_wasi'), false);
  const pending = makeToolDraft({ definitionText: original, pythonCode: '', resources: [] }, 'personal/tool-2');
  assert.deepEqual(pending.manifest.tool.execution, { type: 'builtin', handlerId: 'missing-implementation' });
  assert.equal(pending.files.length, 1);
});

test('pipeline uses exact personal component versions and preserves ordered references', () => {
  const assets = [
    { assetId: 's', kind: 'skill', name: '研究', versions: [{ versionId: 'sv1' }] },
    { assetId: 't', kind: 'tool', name: '汇总', versions: [{ versionId: 'tv2' }] },
  ];
  const app = makeAppDefinition({ name: '研究流程', description: '研究后汇总', inputSchemaText: '{"type":"object","properties":{"topic":{"type":"string"}}}', outputsText: '', steps: [
    { id: 'research', title: '研究', assetId: 's', versionId: 'sv1', task: '核对资料', bindingsText: '{"topic":{"from":"input","path":"topic"}}' },
    { id: 'summarize', title: '汇总', assetId: 't', versionId: 'tv2', bindingsText: '{"values":{"from":"step","stepId":"research","path":"items"}}' },
  ] }, assets);
  assert.equal(app.format, 'cd80.app/2');
  assert.deepEqual(app.nodes.map(step => step.id), ['research', 'summarize']);
  assert.deepEqual(app.nodes[0].componentRef, { assetId: 's', versionId: 'sv1' });
  assert.equal(app.nodes[0].task, '核对资料');
  assert.deepEqual(app.nodes[1].inputBindings.values, { from: 'step', stepId: 'research', path: 'items' });
  assert.deepEqual(app.outputs, { result: { from: 'step', stepId: 'summarize', path: '' } });
  assert.deepEqual(recommendedBindings({ kind: 'tool', manifest: { tool: { inputSchema: { properties: { topic: {} } } } } }, app.inputSchema), { topic: { from: 'input', path: 'topic' } });
});

test('invalid standard fields and forward step references fail before creating records', () => {
  assert.throws(() => makeSkillDocument({ ...skillFields, name: 'Claude-helper' }), /Skill 名称/);
  assert.throws(() => makeSkillDocument({ ...skillFields, description: '<tag>test</tag>' }), /XML/);
  assert.throws(() => makeSkillDraft({ ...skillFields, resources: [{ path: '../secret.txt', text: 'x' }] }), /路径/);
  assert.throws(() => makeToolDraft({ definitionText: JSON.stringify({ ...definition, name: 'bad name' }) }), /Tool name/);
  assert.throws(() => makeToolDraft({ definitionText: JSON.stringify({ ...definition, input_schema: { type: 'array' } }) }), /input_schema/);
  const assets = [{ assetId: 's', kind: 'skill', name: '研究', versions: [{ versionId: 'v' }] }];
  assert.throws(() => makeAppDefinition({ name: 'A', description: 'B', inputSchemaText: '{"type":"object"}', steps: [
    { id: 'first', assetId: 's', versionId: 'v', task: 'x', bindingsText: '{"x":{"from":"step","stepId":"second","path":""}}' },
  ] }, assets), /前序步骤/);
});

test('component catalog paginates owner assets and versions, excluding removed assets', async () => {
  const urls = [];
  const get = async url => {
    urls.push(url);
    if (url.startsWith('/assets?') && !url.includes('cursor=')) return { items: [{ assetId: 'a', kind: 'skill', name: 'A', lifecycle: 'active' },
      { assetId: 'deleted', kind: 'tool', deletedAt: 'today', lifecycle: 'active' }], nextCursor: 'next' };
    if (url.startsWith('/assets?')) return { items: [{ assetId: 'b', kind: 'tool', name: 'B', lifecycle: 'active' }], nextCursor: null };
    if (url.includes('/a/versions') && !url.includes('cursor=')) return { items: [{ versionId: 'v1', number: 1 }], nextCursor: 'more' };
    if (url.includes('/a/versions')) return { items: [{ versionId: 'v2', number: 2 }], nextCursor: null };
    return { items: [{ versionId: 'tv1', number: 1 }], nextCursor: null };
  };
  const options = await loadPersonalComponents(get, new AbortController().signal);
  assert.deepEqual(options.map(item => item.assetId), ['a', 'b']);
  assert.deepEqual(options[0].versions.map(item => item.versionId), ['v2', 'v1']);
  assert.ok(urls.every(url => !url.includes('deleted/versions')));
});
