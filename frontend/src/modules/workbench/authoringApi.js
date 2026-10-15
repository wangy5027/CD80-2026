import { request } from './api.js';

const pending = new Map();
const root = '/authoring-sessions';
const encode = value => encodeURIComponent(String(value));
async function signature(owner, path, body) {
  const bytes = new TextEncoder().encode(JSON.stringify([String(owner), path, body]));
  const hash = await crypto.subtle.digest('SHA-256', bytes);
  return 'cd80-authoring-request:' + owner + ':' + [...new Uint8Array(hash)].map(byte => byte.toString(16).padStart(2, '0')).join('');
}
function remember(key, value) { pending.set(key, value); try { sessionStorage.setItem(key, value); } catch { /* memory fallback */ } }
function recall(key) { if (pending.has(key)) return pending.get(key); try { return sessionStorage.getItem(key); } catch { return null; } }
function forget(key) { pending.delete(key); try { sessionStorage.removeItem(key); } catch { /* unavailable storage */ } }
export function createAuthoringApi(owner, signal, transport = request) {
  const get = path => transport(root + path, { signal });
  const confirmationKey = id => 'cd80-authoring-confirmation:' + owner + ':' + id;
  const confirmation = id => { try { return JSON.parse(recall(confirmationKey(id)) || 'null'); } catch { return null; } };
  async function post(path, body) {
    const key = await signature(owner, path, body);
    if (signal.aborted) throw new DOMException('观察已关闭', 'AbortError');
    const identity = recall(key) || crypto.randomUUID(); remember(key, identity);
    const confirmationId = path.match(/^\/([^/]+)\/confirm$/)?.[1];
    if (confirmationId && body.mode === 'save_and_enable') remember(confirmationKey(decodeURIComponent(confirmationId)), JSON.stringify({ body }));
    const finish = () => { forget(key); if (confirmationId) forget(confirmationKey(decodeURIComponent(confirmationId))); };
    try {
      const result = await transport(root + path, { method: 'POST', signal, body, headers: { 'Idempotency-Key': identity } });
      if (signal.aborted) throw new DOMException('观察已关闭', 'AbortError');
      if (!(confirmationId && result.session?.phase === 'committing')) finish(); return result;
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && !['OUTCOME_UNKNOWN', 'COMMIT_IN_PROGRESS'].includes(error.code)) finish();
      throw error;
    }
  }
  return {
    list: (filters = {}) => get('?' + new URLSearchParams(Object.entries(filters).filter(([, value]) => value != null && value !== '')).toString()),
    detail: id => get('/' + encode(id)), create: body => post('', body),
    action: (id, action, body) => post('/' + encode(id) + '/' + action, body),
    pendingConfirmation: id => confirmation(id),
    retryConfirmation: id => { const saved = confirmation(id); if (!saved) throw new Error('当前浏览器没有原提交身份，请先刷新核实状态。'); return post('/' + encode(id) + '/confirm', saved.body); },
  };
}
