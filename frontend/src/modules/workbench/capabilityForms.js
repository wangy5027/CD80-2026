export function emptyValue(schema = {}) {
  if ('default' in schema) return structuredClone(schema.default);
  if ('const' in schema) return structuredClone(schema.const);
  if (schema.type === 'object') return Object.fromEntries((schema.required || []).map(key => [key, emptyValue(schema.properties?.[key])]));
  if (schema.type === 'array') return Array.from({ length: Math.min(schema.minItems || 0, 20) }, () => emptyValue(schema.items));
  if (schema.type === 'boolean') return false;
  if (schema.type === 'null') return null;
  return '';
}
export function formSupported(schema, depth = 0) {
  if (!schema || depth > 20 || !['object', 'array', 'string', 'number', 'integer', 'boolean', 'null'].includes(schema.type)) return false;
  if (['oneOf', 'anyOf', 'allOf', '$ref', 'patternProperties'].some(key => key in schema)) return false;
  if (schema.type === 'object') return Object.values(schema.properties || {}).every(child => formSupported(child, depth + 1)) && schema.additionalProperties === false;
  return schema.type !== 'array' || formSupported(schema.items, depth + 1);
}
export function fieldErrors(schema, value, path = []) {
  const errors = [];
  const add = message => errors.push({ path, message });
  if (!schema) return errors;
  if ('const' in schema && JSON.stringify(value) !== JSON.stringify(schema.const)) add('请使用指定值。');
  if (schema.enum && !schema.enum.some(v => JSON.stringify(v) === JSON.stringify(value))) add('请从选项中选择。');
  if (schema.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) { add('请填写一组参数。'); return errors; }
    for (const key of schema.required || []) if (!(key in value)) errors.push({ path: [...path, key], message: '请填写此项。' });
    for (const [key, val] of Object.entries(value)) {
      if (schema.properties?.[key]) errors.push(...fieldErrors(schema.properties[key], val, [...path, key]));
      else if (schema.additionalProperties === false) errors.push({ path: [...path, key], message: '工具不接受此参数，请移除。' });
    }
  } else if (schema.type === 'array') {
    if (!Array.isArray(value)) { add('请填写项目列表。'); return errors; }
    if (value.length < (schema.minItems || 0)) add(`至少填写 ${schema.minItems} 项。`);
    if (schema.maxItems != null && value.length > schema.maxItems) add(`最多填写 ${schema.maxItems} 项。`);
    if (schema.uniqueItems && new Set(value.map(v => JSON.stringify(v))).size !== value.length) add('列表中不能有重复项目。');
    value.forEach((v, i) => errors.push(...fieldErrors(schema.items, v, [...path, i])));
  } else if (schema.type === 'string') {
    if (typeof value !== 'string') add('请填写文本。');
    else {
      if (value.length < (schema.minLength || 0)) add(`至少填写 ${schema.minLength} 个字符。`);
      if (schema.maxLength != null && value.length > schema.maxLength) add(`最多填写 ${schema.maxLength} 个字符。`);
      if (schema.pattern) { try { if (!new RegExp(schema.pattern).test(value)) add(schema.description || '格式不符合要求，请参考示例。'); } catch { /* backend validates schema */ } }
    }
  } else if (schema.type === 'number' || schema.type === 'integer') {
    if (typeof value !== 'number' || !Number.isFinite(value)) add('请填写有效数字。');
    else {
      if (schema.type === 'integer' && !Number.isInteger(value)) add('请填写整数。');
      if (schema.minimum != null && value < schema.minimum) add(`不能小于 ${schema.minimum}。`);
      if (schema.maximum != null && value > schema.maximum) add(`不能大于 ${schema.maximum}。`);
      if (schema.exclusiveMinimum != null && value <= schema.exclusiveMinimum) add(`必须大于 ${schema.exclusiveMinimum}。`);
      if (schema.exclusiveMaximum != null && value >= schema.exclusiveMaximum) add(`必须小于 ${schema.exclusiveMaximum}。`);
    }
  } else if (schema.type === 'boolean' && typeof value !== 'boolean') add('请选择是或否。');
  else if (schema.type === 'null' && value !== null) add('此项应为空值。');
  return errors;
}

// Maintainer labels for exact-match legacy packages change presentation only.
export function labeledSchema(schema, labels = {}, path = '') {
  if (!schema) return schema;
  const result = { ...schema };
  if (labels[path]) result.title = labels[path];
  if (schema.properties) result.properties = Object.fromEntries(Object.entries(schema.properties).map(([key, child]) => [key, labeledSchema(child, labels, path ? path + '.' + key : key)]));
  if (schema.items) result.items = labeledSchema(schema.items, labels, path + '[]');
  return result;
}
