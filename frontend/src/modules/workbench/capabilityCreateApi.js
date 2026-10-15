import { request } from './api.js';

export const MAX_ORIGINAL_BYTES = 20 * 1024 * 1024;
const pendingImports = new Map();
const identity = (owner, kind) => JSON.stringify([String(owner), kind]);

// Retain the exact File and request key while this browser tab stays open. A
// remounted editor can check the original operation instead of creating one.
export function pendingCapabilityImport(owner, kind) { return pendingImports.get(identity(owner, kind)) || null; }
export function rememberCapabilityImport(owner, kind, value) { pendingImports.set(identity(owner, kind), value); }
export function forgetCapabilityImport(owner, kind) { pendingImports.delete(identity(owner, kind)); }

export function createCapabilityCreateApi(_owner, signal, transport = request) {
  const root = '/capabilities';
  return {
    preview(file, kind) {
      const body = new FormData(); body.append('file', file, file.name); body.append('kind', kind);
      return transport(root + '/imports/preview', { method: 'POST', body, signal });
    },
    start(file, expectedDigest, key, testCases = null) {
      const body = new FormData(); body.append('file', file, file.name); body.append('expectedDigest', expectedDigest);
      if (testCases) body.append('testCases', JSON.stringify(testCases));
      return transport(root + '/imports', { method: 'POST', body, signal, headers: { 'Idempotency-Key': key } });
    },
    job(jobId) { return transport(root + '/jobs/' + encodeURIComponent(jobId), { signal }); },
    importRecord(importId) { return transport(root + '/imports/' + encodeURIComponent(importId), { signal }); },
  };
}
