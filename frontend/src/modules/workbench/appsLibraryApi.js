import { request } from './api.js';
import { allPages } from './evolutionModel.js';

// Retain keys after an unknown result, including when a panel is remounted.
// Owner is part of the signature, so a later account cannot reuse the key.
const pendingMutations = new Map();
const encode = value => encodeURIComponent(String(value));
const aborted = () => new DOMException('页面已关闭', 'AbortError');

export function createAppsLibraryApi(ownerId, signal, transport = request) {
  const owner = String(ownerId);
  const get = path => transport(path, { signal });
  async function mutate(path, body, { bodyKey = false } = {}) {
    if (signal.aborted) throw aborted();
    const signature = JSON.stringify([owner, path, body]);
    if (!pendingMutations.has(signature)) pendingMutations.set(signature, crypto.randomUUID());
    const key = pendingMutations.get(signature);
    try {
      const result = await transport(path, { method: 'POST', signal,
        headers: { 'Idempotency-Key': key }, body: bodyKey ? { ...body, idempotencyKey: key } : body });
      if (signal.aborted) throw aborted();
      pendingMutations.delete(signature);
      return result;
    } catch (error) {
      if (error.status >= 400 && error.status < 500 && error.code !== 'OUTCOME_UNKNOWN') pendingMutations.delete(signature);
      throw error;
    }
  }
  return {
    personalAssets: () => get('/asset-apps'),
    agents: () => get('/agents'),
    personalRecipes: () => get('/evolution/recipes'),
    modernPublic: () => allPages(transport, '/community/apps?limit=100', { signal }),
    legacyPublic: () => allPages(transport, '/evolution/publications', { signal }),
    asset: (appId, versionId) => get('/asset-apps/' + encode(appId) + (versionId ? '?versionId=' + encode(versionId) : '')),
    personalStrategy: appId => get('/strategy-workshop/personal-apps/' + encode(appId)),
    modernRelease: releaseId => get('/community/apps/' + encode(releaseId)),
    legacyRelease: releaseId => get('/evolution/publications/' + encode(releaseId) + '/export'),
    sharingPreview: (appId, versionId) => transport('/community/apps/preview', { method: 'POST', signal, body: { appId, versionId } }),
    publish: preview => mutate('/community/apps', {
      appId: preview.appId, versionId: preview.versionId, title: preview.title, description: preview.description,
      snapshotDigest: preview.snapshotDigest, confirmSharing: true,
    }),
    withdraw: (releaseId, revision) => mutate('/community/apps/' + encode(releaseId) + '/withdraw', { revision }),
    copyModern: releaseId => mutate('/community/apps/' + encode(releaseId) + '/copy', {}),
    copyPreset: (agentRef, confirmDuplicate = false) => mutate('/agents/copy-preset', { agentRef, confirmDuplicate }, { bodyKey: true }),
    copyLegacy: (releaseId, dependencyBindings = {}, confirmDuplicate = false) => mutate('/evolution/publications/' + encode(releaseId) + '/copy',
      { confirmDuplicate, ...(Object.keys(dependencyBindings).length ? { dependencyBindings } : {}) }),
  };
}
