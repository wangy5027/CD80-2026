// Manual assets keep their published standard files separate from this host's
// execution, permission and version metadata.
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' ? value.trim() : '';
const uid = () => crypto.randomUUID();

export function parseJsonObject(source, label) {
  let value;
  try { value = JSON.parse(source); } catch { throw new Error(`${label}不是有效的 JSON。`); }
  if (!object(value)) throw new Error(`${label}必须是 JSON 对象。`);
  return value;
}

export function validateResourcePath(path) {
  if (!text(path) || path.length > 480 || path.startsWith('/') || path.includes('\\') || path.includes(':') || path.split('/').some(part => !part || part === '.' || part === '..' || /[. ]$/.test(part))) {
    throw new Error('资源路径须为安全的相对路径，例如 references/guide.md。');
  }
  if (path === 'SKILL.md' || path === 'tool.json' || path === 'tool.py') throw new Error('资源路径不能覆盖主文件。');
  return path;
}

function resourceFiles(resources) {
  const files = [], seen = new Set();
  for (const item of resources || []) {
    const path = validateResourcePath(text(item.path));
    if (seen.has(path.toLowerCase())) throw new Error('资源路径不能重复。');
    seen.add(path.toLowerCase());
    if ((typeof item.text === 'string') === (typeof item.base64 === 'string')) throw new Error('每个资源只能提供文本或上传文件之一。');
    files.push({ path: 'original/' + path, ...(typeof item.text === 'string' ? { text: item.text } : { base64: item.base64 }) });
  }
  return files;
}

function manifest(kind, name, description, identity, permissions) {
  return {
    format: 'cd80.capability/1', kind, name, description,
    source: { identity, format: kind === 'skill' ? 'agent_skills' : 'claude_tool' },
    files: [], dependencies: [],
    permissions: { network: 'none', externalEffects: 'none', inputFiles: Boolean(permissions?.inputFiles), outputFiles: Boolean(permissions?.outputFiles) },
  };
}

export function makeSkillDocument({ name, description, body, extraFrontmatterText = '' }) {
  const extras = extraFrontmatterText.trim()
    ? parseJsonObject(extraFrontmatterText, 'SKILL.md 额外 frontmatter') : {};
  if (Object.hasOwn(extras, 'name') || Object.hasOwn(extras, 'description')) {
    throw new Error('额外 frontmatter 不能覆盖 name 或 description。');
  }
  const skillName = text(name), skillDescription = text(description), instructions = text(body);
  if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(skillName) || skillName.length > 64 || /anthropic|claude/i.test(skillName)) {
    throw new Error('Skill 名称须为不超过 64 字的小写字母、数字和短横线，且不能包含 anthropic 或 claude。');
  }
  if (!skillDescription || skillDescription.length > 1024 || /<[^>]*>/.test(skillDescription)) {
    throw new Error('Skill 说明须为 1–1024 字，且不能含 XML 标签。请说明何时使用。');
  }
  if (!instructions) throw new Error('请填写 SKILL.md 正文操作说明。');
  const extraLines = Object.entries(extras).map(([key, value]) => `${JSON.stringify(key)}: ${JSON.stringify(value)}`);
  return `---\nname: ${JSON.stringify(skillName)}\ndescription: ${JSON.stringify(skillDescription)}\n${extraLines.length ? extraLines.join('\n') + '\n' : ''}---\n\n${body.trim()}\n`;
}

export function makeSkillDraft(fields, identity = `personal/${uid()}`) {
  const skill = makeSkillDocument(fields);
  const name = text(fields.name), description = text(fields.description);
  return {
    kind: 'skill', name, description,
    manifest: { ...manifest('skill', name, description, identity, fields.permissions), skill: { entry: 'original/SKILL.md' } },
    files: [{ path: 'original/SKILL.md', text: skill }, ...resourceFiles(fields.resources)],
  };
}

export function makeToolDraft(fields, identity = `personal/${uid()}`) {
  const source = fields.definitionText;
  const definition = parseJsonObject(source, 'Tool 定义');
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(definition.name || '')) throw new Error('Tool name 须为 1–128 位字母、数字、下划线或短横线。');
  if (!text(definition.description)) throw new Error('Tool description 必须说明功能和使用时机。');
  if (!object(definition.input_schema) || definition.input_schema.type !== 'object') throw new Error('Tool input_schema 必须是 type 为 object 的 JSON Schema。');
  const code = text(fields.pythonCode);
  if (code && !/(^|\n)\s*def\s+run\s*\(\s*payload\b/.test(code)) throw new Error('执行实现需定义 def run(payload)。');
  const files = [{ path: 'original/tool.json', text: source }, ...resourceFiles(fields.resources)];
  if (code) files.push({ path: 'original/tool.py', text: fields.pythonCode });
  return {
    kind: 'tool', name: definition.name, description: definition.description,
    manifest: {
      ...manifest('tool', definition.name, definition.description, identity, fields.permissions),
      tool: { inputSchema: definition.input_schema,
        execution: code ? { type: 'python_wasi', path: 'original/tool.py', callable: 'run' }
          : { type: 'builtin', handlerId: 'missing-implementation' } },
    }, files,
  };
}

export async function loadPersonalComponents(get, signal) {
  const items = [];
  let cursor = '';
  do {
    const response = await get(`/assets?scope=personal&limit=100${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`, { signal });
    items.push(...response.items.filter(item => !item.deletedAt && item.lifecycle === 'active' && ['skill', 'tool'].includes(item.kind)));
    cursor = response.nextCursor || '';
  } while (cursor && !signal?.aborted);
  const results = await Promise.all(items.map(async item => {
    const versions = [];
    let next = '';
    do {
      const response = await get(`/assets/${encodeURIComponent(item.assetId)}/versions?limit=100${next ? '&cursor=' + encodeURIComponent(next) : ''}`, { signal });
      versions.push(...response.items);
      next = response.nextCursor || '';
    } while (next && !signal?.aborted);
    return { ...item, versions: versions.sort((a, b) => (b.number || 0) - (a.number || 0)) };
  }));
  return results;
}

export function recommendedBindings(component, inputSchema, previousId = '') {
  const properties = inputSchema?.properties || {};
  const toolProperties = component?.kind === 'tool' ? component?.manifest?.tool?.inputSchema?.properties || {} : properties;
  const keys = Object.keys(toolProperties);
  const common = keys.filter(key => Object.hasOwn(properties, key));
  if (common.length) return Object.fromEntries(common.map(key => [key, { from: 'input', path: key }]));
  if (previousId && keys.length === 1) return { [keys[0]]: { from: 'step', stepId: previousId, path: '' } };
  return {};
}

export function makeAppDefinition(fields, assets) {
  const name = text(fields.name), description = text(fields.description);
  if (!name || name.length > 120) throw new Error('请输入不超过 120 字的 APP 名称。');
  if (!description) throw new Error('请填写 APP 的用途。');
  const inputSchema = parseJsonObject(fields.inputSchemaText, 'APP 输入 Schema');
  if (inputSchema.type !== 'object') throw new Error('APP 输入 Schema 的 type 必须是 object。');
  if (!Array.isArray(fields.steps) || !fields.steps.length || fields.steps.length > 64) throw new Error('请添加 1–64 个顺序步骤。');
  const previous = new Set();
  const nodes = fields.steps.map((step, index) => {
    if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(step.id) || previous.has(step.id)) throw new Error(`第 ${index + 1} 步标识无效或重复。`);
    const asset = assets.find(item => item.assetId === step.assetId);
    const version = asset?.versions.find(item => item.versionId === step.versionId);
    if (!asset || !version) throw new Error(`第 ${index + 1} 步请选择本人库里的精确 Skill/Tool 版本。`);
    const bindings = parseJsonObject(step.bindingsText, `第 ${index + 1} 步的参数连接`);
    for (const binding of Object.values(bindings)) {
      if (!object(binding) || !['input', 'literal', 'step', 'file', 'artifact'].includes(binding.from)) throw new Error(`第 ${index + 1} 步存在无效参数来源。`);
      if (['step', 'artifact'].includes(binding.from) && !previous.has(binding.stepId)) throw new Error(`第 ${index + 1} 步只能引用前序步骤。`);
      if (binding.from === 'literal' && !Object.hasOwn(binding, 'value')) throw new Error(`第 ${index + 1} 步的固定值缺少 value。`);
    }
    previous.add(step.id);
    const node = { id: step.id, kind: asset.kind, title: text(step.title) || asset.name,
      componentRef: { assetId: asset.assetId, versionId: version.versionId }, inputBindings: bindings,
      delivery: { schema: {}, files: [], evidenceFields: [] }, checkpoint: false,
      provenance: { type: 'generated', description: '用户手动选择个人库组件并组合。' } };
    if (asset.kind === 'skill') {
      if (!text(step.task)) throw new Error(`第 ${index + 1} 步 Skill 需要填写本步目标。`);
      node.task = step.task.trim();
    }
    return node;
  });
  const outputs = text(fields.outputsText) ? parseJsonObject(fields.outputsText, 'APP 输出连接')
    : { result: { from: 'step', stepId: nodes.at(-1).id, path: '' } };
  for (const binding of Object.values(outputs)) {
    if (!object(binding) || !['step', 'artifact', 'literal'].includes(binding.from)) throw new Error('APP 输出须来自步骤、产物或明确常量。');
    if (['step', 'artifact'].includes(binding.from) && !previous.has(binding.stepId)) throw new Error('APP 输出引用了不存在的步骤。');
  }
  return { format: 'cd80.app/2', name, description, inputSchema, rules: [], nodes, outputs, parameterization: [], gaps: [] };
}
