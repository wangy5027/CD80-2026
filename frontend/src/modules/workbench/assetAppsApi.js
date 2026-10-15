import { request } from './api.js';

const ROOT = '/asset-apps';
const pending = new Map();
const id = value => encodeURIComponent(String(value));

// Preserve unknown outcomes across panel navigation, scoped to the signed-in owner.
export function createAssetAppsApi(owner, signal, transport = request) {
  const get = path => transport(ROOT + path, { signal });
  async function post(path, body) {
    if (signal.aborted) throw new DOMException('页面已关闭', 'AbortError');
    const signature = JSON.stringify([String(owner), path, body]);
    if (!pending.has(signature)) pending.set(signature, crypto.randomUUID());
    try {
      const result = await transport(ROOT + path, { method: 'POST', signal, body, headers: { 'Idempotency-Key': pending.get(signature) } });
      if (signal.aborted) throw new DOMException('页面已关闭', 'AbortError');
      pending.delete(signature);
      return result;
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && !['OUTCOME_UNKNOWN', 'CREATOR_RUNNING'].includes(error.code)) pending.delete(signature);
      throw error;
    }
  }
  return {
    list: () => get(''), legacy: () => get('/legacy'),
    detail: (appId, versionId) => get('/' + id(appId) + (versionId ? '?versionId=' + id(versionId) : '')),
    preview: body => post('/preview', body), creator: body => post('/creator', { kind: 'app', ...body }),
    save: body => post('', body), verify: (appId, body) => post('/' + id(appId) + '/verify', body),
    enable: (appId, body) => post('/' + id(appId) + '/enable', body),
    configure: (appId, body) => post('/' + id(appId) + '/bindings', body),
    runs: appId => get('/' + id(appId) + '/runs'),
    run: (appId, body) => post('/' + id(appId) + '/runs', body), runDetail: runId => get('/runs/' + id(runId)),
    resume: (runId, body) => post('/runs/' + id(runId) + '/resume', body), cancel: (runId, revision) => post('/runs/' + id(runId) + '/cancel', { revision }),
    importPreview: value => post('/import/preview', { package: value }), importApp: (value, bindings = {}, testCases = null) => post('/import', { package: value, bindings, ...(testCases ? { testCases } : {}) }),
    extract: (appId, body) => post('/' + id(appId) + '/extract', body),
    exportUrl: (appId, versionId) => '/desktop/api' + ROOT + '/' + id(appId) + '/export?versionId=' + id(versionId),
  };
}
