import { blankDefinition, parseObject } from './assetAppsModel.js';

export const prettyApp = value => JSON.stringify(value, null, 2);

export function initialAppText() { return prettyApp(blankDefinition()); }

export function exampleAppDefinition() {
  return {
    format: 'cd80.app/2', name: '数字合计示例', description: '对本次输入的数字求和，返回合计和数量。',
    inputSchema: { type: 'object', properties: { values: { type: 'array', items: { type: 'number' }, minItems: 1, title: '数字列表' } },
      required: ['values'], additionalProperties: false },
    rules: ['仅使用本次输入，不读取外部数据。'],
    nodes: [{ id: 'calculate', kind: 'code', title: '计算合计',
      code: 'def run(payload):\n    values = payload["values"]\n    return {"total": sum(values), "count": len(values)}\n',
      files: [{ path: 'references/README.md', text: '本示例只处理本次输入，不接入外部数据。\n' }],
      inputBindings: { values: { from: 'input', path: 'values' } },
      delivery: { schema: { type: 'object', properties: { total: { type: 'number' }, count: { type: 'integer' } },
        required: ['total', 'count'] }, files: [], evidenceFields: ['total', 'count'] },
      checkpoint: false, provenance: { type: 'generated', description: '用户手动填写的完整示例，保存后仍需验证。' } }],
    outputs: { result: { from: 'step', stepId: 'calculate', path: '' } },
    parameterization: [], gaps: [],
  };
}

export function parseManualApp(source) {
  const value = parseObject(source, 'APP 定义');
  if (value.format !== 'cd80.app/2') throw new Error('请使用 cd80.app/2 完整定义。');
  if (!Array.isArray(value.nodes)) throw new Error('完整定义需要 nodes 顺序步骤数组。');
  return value;
}

export function importPreviewValue(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      value.package?.format !== 'cd80.app-package/2' || !value.package.digest ||
      !value.definition || typeof value.definition !== 'object' || Array.isArray(value.definition)) {
    throw new Error('上传预览缺少完整应用包。请重新上传兼容的 APP ZIP/JSON。');
  }
  return { package: value.package, definition: value.definition,
    issues: Array.isArray(value.issues) ? value.issues : [], warnings: Array.isArray(value.warnings) ? value.warnings : [],
    bindingRequirements: Array.isArray(value.bindingRequirements) ? value.bindingRequirements : [] };
}

export function isKnownFailure(error) {
  return error?.status >= 400 && error?.status < 500 && !['OUTCOME_UNKNOWN', 'CREATOR_RUNNING'].includes(error?.code);
}
