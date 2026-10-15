import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { request } from '../workbench/api.js';
import { accountDraftKey, readDraft, writeDraft } from '../workbench/draftStore.js';

export function useAppWorkshop(ownerKey) {
  const requirement = ref(''), instructions = ref(''), session = ref(null);
  const loading = ref(true), busy = ref(''), error = ref(''), reviewing = ref(false);
  const step = computed(() => session.value?.appPreview && !reviewing.value ? 3 : session.value ? 2 : 1);
  let owner = '', epoch = 0, disposed = false, restoring = false;
  const controllers = new Set(), keys = new Map();
  const draftKey = () => accountDraftKey(owner, 'app-workshop', 'current');
  const persist = () => { if (owner && !restoring) writeDraft(draftKey(), { requirement: requirement.value, instructions: instructions.value, sessionId: session.value?.id || null, reviewing: reviewing.value }); };
  function cancel() { epoch++; for (const c of controllers) c.abort(); controllers.clear(); }
  async function api(path, options = {}) {
    const at = epoch, c = new AbortController(); controllers.add(c);
    try {
      const result = await request('/app-workshop' + path, { ...options, signal: c.signal });
      if (disposed || at !== epoch) throw new DOMException('Stale response', 'AbortError');
      return result;
    } finally { controllers.delete(c); }
  }
  async function initialize(value) {
    cancel(); owner = String(value || ''); keys.clear(); restoring = true;
    session.value = null; busy.value = ''; error.value = ''; requirement.value = ''; instructions.value = ''; reviewing.value = false;
    if (!owner) { restoring = false; loading.value = false; return; }
    loading.value = true;
    const at = epoch, draft = readDraft(draftKey());
    requirement.value = draft?.requirement || ''; instructions.value = draft?.instructions || ''; reviewing.value = Boolean(draft?.reviewing);
    try {
      const value = draft?.sessionId ? await api('/sessions/' + encodeURIComponent(draft.sessionId)) : draft ? null : (await api('/sessions/current')).session;
      if (value) { session.value = value; requirement.value = draft?.requirement || value.requirement; }
    } catch (e) { if (at === epoch && e.name !== 'AbortError') error.value = e.message; }
    finally { if (at === epoch) { restoring = false; loading.value = false; persist(); } }
  }
  async function action(kind, path, body, apply) {
    if (busy.value || loading.value) return;
    busy.value = kind; error.value = ''; const at = epoch;
    const signature = kind + ':' + JSON.stringify(body);
    if (!keys.has(signature)) keys.set(signature, globalThis.crypto.randomUUID());
    try {
      const value = await api(path, { method: 'POST', body: { ...body, idempotencyKey: keys.get(signature) } });
      keys.delete(signature); apply?.(value); persist(); return value;
    } catch (e) { if (at === epoch && e.name !== 'AbortError') error.value = e.message; }
    finally { if (at === epoch) busy.value = ''; }
  }
  function analyze() {
    const text = requirement.value.trim();
    if (!text) { error.value = '先说说你希望解决什么问题。'; return; }
    return action('analyze', '/sessions', { requirement: text }, value => { session.value = value; instructions.value = ''; });
  }
  function refine() {
    if (!instructions.value.trim() || !session.value) return;
    return action('refine', `/sessions/${encodeURIComponent(session.value.id)}/refine`, { instructions: instructions.value.trim(), revision: session.value.revision }, value => { session.value = value; requirement.value = value.requirement; instructions.value = ''; reviewing.value = false; });
  }
  const create = () => session.value?.appPreview ? (reviewing.value = false, persist()) : session.value && action('create', `/sessions/${encodeURIComponent(session.value.id)}/create`, { revision: session.value.revision }, value => { session.value = value; });
  const save = () => session.value?.status === 'saved' ? Promise.resolve({ session: session.value, appId: session.value.savedAppId, versionId: session.value.savedVersionId }) : session.value?.appPreview && action('save', `/sessions/${encodeURIComponent(session.value.id)}/save`, { revision: session.value.revision, appDigest: session.value.appPreview.appDigest }, value => { session.value = value.session; });
  const handoff = () => session.value?.status === 'handed_off' ? Promise.resolve({ session: session.value, target: Object.fromEntries(Object.entries(session.value.recommendation).filter(([key]) => ['kind', 'agentRef', 'appId', 'versionId'].includes(key))), requirement: session.value.requirement, inputs: session.value.recommendation.inputs || {} }) : session.value?.recommendation && action('handoff', `/sessions/${encodeURIComponent(session.value.id)}/handoff`, { revision: session.value.revision }, value => { session.value = value.session; });
  function startNew() { if (busy.value) return; session.value = null; requirement.value = ''; instructions.value = ''; reviewing.value = false; error.value = ''; keys.clear(); persist(); }
  watch([requirement, instructions], persist, { flush: 'sync' });
  watch(ownerKey, initialize, { immediate: true });
  onBeforeUnmount(() => { disposed = true; cancel(); });
  return { requirement, instructions, session, step, loading, busy, error, analyze, refine, create, save, handoff, startNew, review: () => { reviewing.value = true; persist(); }, reload: () => initialize(owner) };
}
