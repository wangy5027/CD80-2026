export const terminalStatuses = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);
export const statusLabels = { generating: '正在设计', awaiting_input: '待补充参数', awaiting_confirmation: '待确认流程', queued: '等待执行', awaiting_authorization: '待授权文件', running: '正在运行', succeeded: '已完成', failed: '运行失败', cancelling: '正在停止', cancelled: '已取消', interrupted: '已中断' };
export const parameterLabels = { fileId: '输入文件', groupBy: '分组列', groupColumn: '分组列', valueColumn: '求和列', aggregation: '汇总方式', text: '文本', delimiter: '分隔符', encoding: '编码' };
export function fileFields(spec) {
  return [...new Set(['fileId', ...(spec?.permissions?.files || []), ...(spec?.tools || []).flatMap(t => t.permissions?.files || [])])].filter(key => key in (spec?.inputSchema?.properties || {}));
}
export function initialInputs(spec, initial = {}, fresh = false) {
  const result = {};
  const files = new Set(fileFields(spec));
  for (const [name, schema] of Object.entries(spec?.inputSchema?.properties || {})) {
    if (fresh && files.has(name)) result[name] = '';
    else if (name in initial) result[name] = ['object', 'array'].includes(schema.type) ? JSON.stringify(initial[name], null, 2) : initial[name];
    else if ('const' in schema) result[name] = schema.const;
    else if ('default' in schema && !files.has(name)) result[name] = ['object', 'array'].includes(schema.type) ? JSON.stringify(schema.default) : schema.default;
    else result[name] = schema.type === 'boolean' ? false : '';
  }
  return result;
}
export function parseInputs(spec, values) {
  const input = {};
  const required = spec?.inputSchema?.required || [];
  for (const [name, schema] of Object.entries(spec?.inputSchema?.properties || {})) {
    let value = values[name];
    if (value === '' || value === undefined || value === null) {
      if (required.includes(name)) throw new Error(`请填写${schema.title || parameterLabels[name] || name}。`);
      continue;
    }
    if (['array', 'object'].includes(schema.type) && typeof value === 'string') {
      try { value = JSON.parse(value); } catch { throw new Error(`${name} 的结构化参数格式不正确。`); }
    }
    if (['integer', 'number'].includes(schema.type)) {
      value = Number(value);
      if (!Number.isFinite(value) || (schema.type === 'integer' && !Number.isInteger(value))) throw new Error(`${name} 必须是${schema.type === 'integer' ? '整数' : '数字'}。`);
    }
    input[name] = value;
  }
  return { input, fileIds: [...new Set(fileFields(spec).flatMap(key => typeof input[key] === 'string' && input[key] ? [input[key]] : []))] };
}
