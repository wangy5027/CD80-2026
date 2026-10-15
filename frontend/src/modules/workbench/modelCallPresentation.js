import { USER_FACING_ERROR } from '../../shared/userFacingError.js';

// Owner-scoped diagnostics stay on the server; the ordinary UI only renders this projection.
const hidden = '[内部提示内容已省略]';
const keyName = key => String(key).replace(/[^a-z0-9]/gi, '').toLowerCase();
const privateKey = key => /^(system|developer)|(?:system|developer)(?:prompt|message|instruction)/.test(keyName(key)) ||
  /^(instruction|instructions|prompttemplate|rawrequest|rawresponse|headers|authorization|apikey|token|password|reasoning|reasoningcontent|availabletools|toolcatalog|skills)$/.test(keyName(key));
const diagnosticKey = key => /^(error|errors|errorcode|errormessage|issue|issues|diagnostic|diagnostics|traceback|stack|stacktrace|exception|validationerror|validationerrors|validationfeedback|failurereason|failurecause)$/.test(keyName(key));
const privileged = value => value && typeof value === 'object' && ['system', 'developer'].includes(String(value.role || value.author?.role || '').toLowerCase());
function hasPrivateSyntax(value) {
  const text = value.replace(/\\+(["'])/g, '$1'); // Truncated, JSON-escaped transcripts cannot be parsed normally.
  return /["']?role["']?\s*[:=]\s*["']?(?:system|developer)\b/i.test(text) ||
    /["']?(?:system[_-]?prompt|developer[_-]?prompt|instructions?)["']?\s*[:=]\s*["'{\[]/i.test(text) ||
    /(?:^|\n)\s*(?:system|developer)\s*:|<\/?(?:system|developer)>|\[\s*(?:system|developer)\s*\]/i.test(text);
}
function parseJson(value) {
  if (typeof value !== 'string') return value;
  const text = value.trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  if (!/^[\[{\"]/.test(text)) return value;
  try { return JSON.parse(text); } catch { return value; }
}
function privateTexts(value, found = new Set(), depth = 0, seen = new WeakSet()) {
  if (depth > 12 || found.size > 80) return found;
  const decoded = parseJson(value);
  if (decoded !== value) return privateTexts(decoded, found, depth + 1, seen);
  if (!value || typeof value !== 'object' || seen.has(value)) return found;
  seen.add(value);
  function remember(item) {
    if (typeof item === 'string' && item.trim().length >= 8) found.add(item);
    else if (Array.isArray(item)) item.forEach(remember);
    else if (item && typeof item === 'object') Object.values(item).forEach(remember);
  }
  if (privileged(value)) remember(value.content ?? value.text);
  for (const [key, item] of Object.entries(value)) {
    if (privateKey(key)) remember(item);
    else privateTexts(item, found, depth + 1, seen);
  }
  return found;
}
function clean(value, secrets = [], depth = 0) {
  if (depth > 12) return '[内容较深，已省略]';
  if (typeof value === 'string') {
    const decoded = parseJson(value);
    if (decoded !== value) return clean(decoded, secrets, depth + 1);
    if (hasPrivateSyntax(value)) return hidden; // Incomplete JSON and raw role transcripts are not business previews.
    let text = value;
    for (const secret of secrets) {
      text = text.split(secret).join(hidden);
      for (const line of secret.split('\n').filter(line => line.trim().length >= 24)) text = text.split(line).join(hidden);
    }
    return text;
  }
  if (Array.isArray(value)) return value.slice(0, 20).filter(item => !privileged(item)).map(item => clean(item, secrets, depth + 1));
  if (value && typeof value === 'object') {
    if (privileged(value)) return undefined;
    if (['failed', 'error', 'interrupted', 'cancelled'].includes(String(value.status || '').toLowerCase()))
      return { status: value.status, message: USER_FACING_ERROR };
    return Object.fromEntries(Object.entries(value).filter(([key]) => !privateKey(key) && !diagnosticKey(key) && !['head', 'tail'].includes(keyName(key)))
      .slice(0, 40).map(([key, item]) => [key, clean(item, secrets, depth + 1)]).filter(([, item]) => item !== undefined));
  }
  return value;
}
function display(value, max, secrets = []) {
  if (value == null || value === '') return '';
  const sanitized = clean(value, secrets);
  if (sanitized === undefined) return '';
  const text = typeof sanitized === 'string' ? sanitized : JSON.stringify(sanitized, null, 2);
  return text.length > max ? text.slice(0, max) + '\n…（仅显示节选）' : text;
}
export function modelCallText(value, max = 800) { return display(value, max, [...privateTexts(value)]); }
const stages = { plan: '理解任务', act: '安排下一步', approve: '检查执行方案', review: '检查交付' };
const statuses = { running: '进行中', succeeded: '已完成', failed: '失败', cancelled: '已停止', interrupted: '已中断' };
const businessKeys = ['message', 'query', 'task', 'goal', 'input', 'inputs', 'constraints', 'delegation', 'observations', 'steps', 'artifacts', 'question', 'answer', 'proposal'];
function businessInput(value) {
  const decoded = parseJson(value);
  if (decoded && !Array.isArray(decoded) && typeof decoded === 'object') {
    const entries = businessKeys.filter(key => decoded[key] != null).map(key => [key, decoded[key]]);
    if (entries.length) return Object.fromEntries(entries);
  }
  return decoded;
}
export function presentModelCall(raw, call = {}) {
  const parsed = parseJson(raw), record = parsed && typeof parsed === 'object' ? parsed : {};
  const secrets = [...privateTexts(record), ...privateTexts(call)];
  const omitted = record.detail?.truncated === true;
  const detail = omitted ? {} : record.detail?.value || record;
  const request = detail.request || {}, response = detail.response || {}, transport = detail.transport || {};
  const metadata = [];
  function add(label, value) { const text = display(value, 160, secrets); if (text) metadata.push({ label, value: text }); }
  const stage = record.stage || call.stage, status = record.status || call.status;
  const failed = ['failed', 'cancelled', 'interrupted'].includes(status) || Boolean(record.error || call.error || call.errorCode || call.validationErrors?.length);
  add('阶段', stages[stage] || stage || call.label);
  add('模型', record.model || transport.model || request.model);
  add('状态', statuses[status] || status);
  const started = Date.parse(record.startedAt || call.startedAt), completed = Date.parse(record.completedAt || call.completedAt);
  if (Number.isFinite(started) && Number.isFinite(completed)) add('耗时', Math.max(0, (completed - started) / 1000).toFixed(1) + ' 秒');
  if (!failed) {
    add('结束原因', transport.finishReason);
    add('请求状态', transport.httpStatus);
  }
  add('重试来源', record.retryOf || call.retryOf);
  const messages = Array.isArray(request.messages) ? request.messages.filter(item =>
    item && ['user', 'assistant', 'tool'].includes(String(item.role).toLowerCase())).slice(-6).map(item => ({
      role: ({ user: '业务输入', assistant: '已有回复', tool: '工具结果' })[String(item.role).toLowerCase()], content: businessInput(item.content),
    })) : [];
  const input = display(messages.length ? messages : businessInput(request.payload ?? request.input), 6000, secrets);
  // A failed response may be a raw traceback or provider diagnostic rather
  // than model content. The full private trace remains available server-side.
  const output = failed ? '' : display(response.text || detail.parsed, 3000, secrets);
  const errors = failed ? [{ label: '错误', value: USER_FACING_ERROR }] : [];
  return { metadata, input, output, errors, omitted, partial: response.complete === false };
}
