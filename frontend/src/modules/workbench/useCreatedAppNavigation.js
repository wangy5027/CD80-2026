import { onScopeDispose, watch } from 'vue';
import { sessionFromResponse } from './authoringModel.js';

const trackedIntents = new Set(['create', 'prepare_import', 'capture', 'edit']);
const ownerId = value => String(value || '');

// Only sessions returned by this page's create/retry action may trigger navigation.
export function useCreatedAppNavigation(authoring, getOwnerId, onSaved) {
  const pending = new Map();
  const handled = new Set();
  let currentOwner = ownerId(getOwnerId());
  let disposed = false;

  function clear() {
    for (const stop of pending.values()) stop();
    pending.clear();
    handled.clear();
  }

  watch(() => ownerId(getOwnerId()), next => {
    if (next === currentOwner) return;
    currentOwner = next;
    clear();
  }, { flush: 'sync' });
  onScopeDispose(() => { disposed = true; clear(); });

  function trackStarted(response, startedOwnerId) {
    const session = sessionFromResponse(response);
    const owner = ownerId(startedOwnerId);
    if (disposed || !owner || owner !== currentOwner || owner !== ownerId(authoring.s.owner)
        || !session?.id || session.kind !== 'app' || session.mode === 'draft_only'
        || !trackedIntents.has(session.intent) || handled.has(session.id) || pending.has(session.id)) return;

    const id = session.id;
    function evaluate() {
      if (disposed || owner !== currentOwner || owner !== ownerId(getOwnerId())
          || owner !== ownerId(authoring.s.owner)) return;
      const latest = authoring.s.details[id]?.session
        || authoring.s.items.find(item => item.id === id)
        || session;
      if (authoring.s.busy || latest.kind !== 'app' || latest.mode === 'draft_only'
          || !trackedIntents.has(latest.intent) || latest.phase !== 'completed'
          || !latest.result?.assetId || !latest.result?.versionId) return;
      const stop = pending.get(id);
      pending.delete(id);
      handled.add(id);
      stop?.();
      onSaved({ appId: latest.result.assetId, versionId: latest.result.versionId });
    }

    // A create response may already be completed; otherwise observe only this ID.
    const stop = watch(() => {
      const detail = authoring.s.details[id]?.session;
      const item = authoring.s.items.find(value => value.id === id);
      return [detail?.phase, detail?.kind, detail?.intent, detail?.mode,
        detail?.result?.assetId, detail?.result?.versionId,
        item?.phase, item?.kind, item?.intent, item?.mode,
        item?.result?.assetId, item?.result?.versionId,
        authoring.s.busy, authoring.s.owner];
    }, evaluate, { flush: 'sync' });
    pending.set(id, stop);
    evaluate();
  }

  return { trackStarted };
}
