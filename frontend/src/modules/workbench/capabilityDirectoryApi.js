import { request } from './api.js';
import { allPages } from './evolutionModel.js';
import { capabilityKind } from './capabilityDirectoryModel.js';

const pendingMutations = new Map();
const encode = value => encodeURIComponent(String(value));
const aborted = () => new DOMException('页面已关闭', 'AbortError');

export function createCapabilityDirectoryApi(ownerId, signal, transport = request) {
  const owner = String(ownerId);
  const get = path => transport(path, { signal });
  async function mutate(path, body) {
    if (signal.aborted) throw aborted();
    const signature = JSON.stringify([owner, path, body]);
    if (!pendingMutations.has(signature)) pendingMutations.set(signature, crypto.randomUUID());
    const key = pendingMutations.get(signature);
    try {
      const result = await transport(path, { method: 'POST', signal, body, headers: { 'Idempotency-Key': key } });
      if (signal.aborted) throw aborted();
      pendingMutations.delete(signature);
      return result;
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.code !== 'OUTCOME_UNKNOWN') pendingMutations.delete(signature);
      throw error;
    }
  }
  return {
    personal: kind => allPages(transport, `/capabilities/assets?scope=personal&kind=${capabilityKind(kind)}&limit=100`, { signal }),
    official: kind => allPages(transport, `/capabilities/assets?scope=public&kind=${capabilityKind(kind)}&limit=100`, { signal }),
    community: kind => allPages(transport, `/community/capabilities?kind=${capabilityKind(kind)}&limit=100`, { signal }),
    asset: (assetId, scope = 'personal') => get(`/capabilities/assets/${encode(assetId)}${scope === 'public' ? '?scope=public' : ''}`),
    version: (assetId, versionId, scope = 'personal') => get(`/capabilities/assets/${encode(assetId)}/versions/${encode(versionId)}${scope === 'public' ? '?scope=public' : ''}`),
    release: releaseId => get(`/community/capabilities/${encode(releaseId)}`),
    addOfficial: (assetId, versionId) => mutate(`/capabilities/assets/${encode(assetId)}/copy`, { versionId, ensureOwned: true }),
    addCommunity: releaseId => mutate(`/community/capabilities/${encode(releaseId)}/copy`, { ensureOwned: true }),
  };
}
