// The standalone tool form only accepts fields in the server-advertised schema.
// Skill instructions and chat message state never enter this request body.
const propertiesOf = schema => schema?.properties && typeof schema.properties === 'object' && !Array.isArray(schema.properties)
  ? schema.properties : {};

export function initialToolValues(schema) {
  const values = {};
  for (const [name, field] of Object.entries(propertiesOf(schema))) {
    const initial = Object.hasOwn(field, 'const') ? field.const : field.default;
    if (initial === undefined || initial === null) values[name] = '';
    else if (Array.isArray(field.enum)) values[name] = JSON.stringify(initial);
    else if (field.type === 'object' || field.type === 'array') values[name] = JSON.stringify(initial, null, 2);
    else values[name] = String(initial);
  }
  return values;
}

export function parseToolArguments(schema, values) {
  if (schema?.type !== 'object') throw new Error('工具参数定义无效，请刷新后重试。');
  const input = {};
  const required = new Set(Array.isArray(schema.required) ? schema.required : []);
  for (const [name, field] of Object.entries(propertiesOf(schema))) {
    const label = field.title || name;
    let raw = Object.hasOwn(field, 'const') ? field.const : values?.[name];
    if (raw === undefined || raw === null || raw === '' || (typeof raw === 'string' && !raw.trim())) {
      if (required.has(name)) throw new Error(`请填写${label}。`);
      continue;
    }
    let value;
    if (Array.isArray(field.enum)) {
      try { value = JSON.parse(raw); } catch { throw new Error(`${label} 的选项无效。`); }
      if (!field.enum.some(item => JSON.stringify(item) === JSON.stringify(value))) throw new Error(`${label} 的选项无效。`);
    } else if (field.type === 'string') {
      if (typeof raw !== 'string') throw new Error(`${label} 必须是文本。`);
      value = raw.trim();
      if (Number.isFinite(field.minLength) && value.length < field.minLength) throw new Error(`${label} 至少需要 ${field.minLength} 个字符。`);
      if (Number.isFinite(field.maxLength) && value.length > field.maxLength) throw new Error(`${label} 最多允许 ${field.maxLength} 个字符。`);
    } else if (field.type === 'integer' || field.type === 'number') {
      const text = String(raw).trim();
      const pattern = field.type === 'integer' ? /^-?\d+$/ : /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
      value = Number(text);
      if (!pattern.test(text) || !Number.isFinite(value) || (field.type === 'integer' && !Number.isSafeInteger(value))) {
        throw new Error(`${label} 必须是${field.type === 'integer' ? '整数' : '数字'}。`);
      }
      if (Number.isFinite(field.minimum) && value < field.minimum) throw new Error(`${label} 不能小于 ${field.minimum}。`);
      if (Number.isFinite(field.maximum) && value > field.maximum) throw new Error(`${label} 不能大于 ${field.maximum}。`);
    } else if (field.type === 'boolean') {
      if (raw === true || raw === 'true') value = true;
      else if (raw === false || raw === 'false') value = false;
      else throw new Error(`${label} 必须选择是或否。`);
    } else if (field.type === 'object' || field.type === 'array') {
      try { value = typeof raw === 'string' ? JSON.parse(raw) : raw; } catch { throw new Error(`${label} 必须是有效的 JSON。`); }
      if (field.type === 'array' ? !Array.isArray(value) : value === null || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`${label} 必须是 JSON ${field.type === 'array' ? '数组' : '对象'}。`);
      }
    } else {
      throw new Error(`${label} 的参数类型暂不支持直接调用。`);
    }
    input[name] = value;
  }
  return input;
}
