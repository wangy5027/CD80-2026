export const runLabels = { pending: '尚未开始', queued: '等待执行', running: '正在执行', awaiting_input: '等待补充输入', awaiting_authorization: '等待授权', awaiting_confirmation: '检查后继续', succeeded: '已完成', failed: '执行失败', cancelled: '已取消', cancelling: '正在停止', interrupted: '已中断' };
export const originLabels = { historical: '历史实际执行', modified: '基于原件修改', generated: '新生成，尚需验证' };
export const liveRun = run => ['queued', 'running', 'cancelling'].includes(run?.status);
export const canResume = run => ['awaiting_input', 'awaiting_authorization', 'awaiting_confirmation', 'failed', 'interrupted'].includes(run?.status);
export const canCancel = run => ['queued', 'running', 'awaiting_input', 'awaiting_authorization', 'awaiting_confirmation'].includes(run?.status);
export const pretty = value => JSON.stringify(value, null, 2);
export const clone = value => JSON.parse(JSON.stringify(value));
export function parseObject(text, label = '内容') {
  let value;
  try { value = JSON.parse(text); } catch { throw new Error(label + '不是有效的 JSON，请检查格式。'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(label + '需要是一个 JSON 对象。');
  return value;
}
export function blankDefinition() {
  return { format: 'cd80.app/2', name: '', description: '', inputSchema: { type: 'object', properties: {}, required: [] }, rules: [], nodes: [], outputs: {}, parameterization: [], gaps: [] };
}
export function appStatus(app) {
  if (app.enabledVersionId && app.enabledVersionId === app.versionId) return '已启用';
  return ({ passed: '已验证，待启用', running: '正在验证', failed: '验证未通过', unverified: '已保存 · 未验证' })[app.verification?.status] || '已保存 · 未验证';
}
export function issueText(issue) { return typeof issue === 'string' ? issue : issue?.message || issue?.description || pretty(issue); }
export function bindingText(binding) {
  if (!binding || typeof binding !== 'object') return pretty(binding);
  if (binding.from === 'input') return '本次输入 · ' + (binding.path || '完整输入');
  if (binding.from === 'file') return '本次授权文件 · ' + binding.input;
  if (binding.from === 'step') return '前序步骤 ' + binding.stepId + (binding.path ? ' · ' + binding.path : ' · 完整结果');
  if (binding.from === 'artifact') return '前序产物 ' + binding.stepId + ' · ' + binding.name;
  if (binding.from === 'literal') return '固定值 · ' + pretty(binding.value);
  return pretty(binding);
}
export function fileAliases(definition) {
  return [...new Set((definition?.nodes || []).flatMap(node => Object.values(node.inputBindings || {}).filter(binding => binding?.from === 'file').map(binding => binding.input)).filter(Boolean))];
}
export function inputDefaults(definition) {
  const result = {};
  for (const [key, field] of Object.entries(definition?.inputSchema?.properties || {})) {
    const value = 'const' in field ? field.const : 'default' in field ? field.default : field.type === 'boolean' ? false : '';
    result[key] = ['object', 'array'].includes(field.type) && value !== '' ? pretty(value) : value;
  }
  return result;
}
export function parseRunInput(definition, values, fileBindings = {}) {
  const input = {}, schema = definition?.inputSchema || {};
  for (const [key, field] of Object.entries(schema.properties || {})) {
    let value = values[key];
    if (value === '' || value === undefined || value === null) {
      if ((schema.required || []).includes(key)) throw new Error('请填写' + (field.title || key) + '。');
      continue;
    }
    if (['array', 'object'].includes(field.type) && typeof value === 'string') {
      try { value = JSON.parse(value); } catch { throw new Error((field.title || key) + '的 JSON 格式不正确。'); }
      if (field.type === 'array' ? !Array.isArray(value) : !value || typeof value !== 'object' || Array.isArray(value)) throw new Error((field.title || key) + '的类型不正确。');
    }
    if (['number', 'integer'].includes(field.type)) {
      value = Number(value);
      if (!Number.isFinite(value) || (field.type === 'integer' && !Number.isInteger(value))) throw new Error((field.title || key) + '需要有效的' + (field.type === 'integer' ? '整数。' : '数字。'));
    }
    input[key] = value;
  }
  return { input, fileBindings: Object.fromEntries(fileAliases(definition).filter(key => fileBindings[key]).map(key => [key, fileBindings[key]])) };
}
export function verificationCases(rows) {
  const cases = rows.map((row, index) => ({ label: row.label || (index ? '不同输入' : '原用例'), input: parseObject(row.input, '用例输入'), fileBindings: { ...(row.fileBindings || {}) }, expectation: parseObject(row.expectation, '预期结果') }));
  if (!cases.length || cases.length > 10) throw new Error('请准备原任务用例；一次最多验证10组。');
  if (cases.length > 1 && new Set(cases.map(item => pretty({ input: item.input, fileBindings: item.fileBindings }))).size < 2) throw new Error('不同输入用例需要使用不同的实际输入。');
  for (const item of cases) {
    if (item.expectation.mode === 'json_equals') { if (!Object.hasOwn(item.expectation, 'value')) throw new Error('精确预期需要填写 value。'); }
    else if (item.expectation.mode === 'fields') { if (!item.expectation.values || typeof item.expectation.values !== 'object' || Array.isArray(item.expectation.values) || !Object.keys(item.expectation.values).length) throw new Error('字段预期至少需要一个实际检查项。'); }
    else throw new Error('预期模式仅支持 json_equals 或 fields。');
  }
  return cases;
}
export function artifactUrl(url) {
  return typeof url === 'string' && url.startsWith('/desktop/api/') && !/[\\\u0000-\u0020]/.test(url) ? url : '';
}
export function resourceFiles(node) {
  const raw = node?.component?.files || node?.files || [];
  const files = Array.isArray(raw) ? raw : Object.entries(raw).map(([path, value]) => ({ path, ...(value && typeof value === 'object' ? value : { base64: value }) }));
  return files.map(file => {
    if (typeof file.text === 'string') return { ...file, displayText: file.text };
    const base64 = file.base64;
    if (!base64 || !/\.(?:md|txt|py|json|csv|tsv|yaml|yml|toml|js|mjs|xml|svg|html|css|sql|ini)$/i.test(file.path)) return { ...file, displayText: null };
    try {
      const bytes = Uint8Array.from(atob(base64), char => char.charCodeAt(0));
      return { ...file, displayText: new TextDecoder('utf-8', { fatal: true }).decode(bytes) };
    } catch { return { ...file, displayText: null }; }
  });
}
export function updateResource(node, path, text) {
  const value = clone(node), target = value.component || value;
  if (Array.isArray(target.files)) target.files = target.files.map(file => file.path === path ? { path: file.path, text } : file);
  else { const bytes = new TextEncoder().encode(text); let binary = ''; for (const byte of bytes) binary += String.fromCharCode(byte); target.files = { ...target.files, [path]: btoa(binary) }; }
  value.provenance = { ...(value.provenance || {}), type: 'modified' };
  return value;
}
