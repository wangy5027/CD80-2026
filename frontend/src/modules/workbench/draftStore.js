// Local browser drafts contain only user input; namespace every entry by account.
const memory = new Map();
const listeners = new Map();
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
export const accountDraftKey = (accountId, purpose, entity) => accountId
  ? 'cd80-draft:v1:' + [accountId, purpose, entity].map(value => encodeURIComponent(String(value))).join(':') : '';
export function readDraft(key) {
  if (!key) return undefined;
  if (memory.has(key)) return clone(memory.get(key));
  try {
    const raw = globalThis.localStorage?.getItem(key);
    if (raw != null) { const value = JSON.parse(raw); memory.set(key, value); return clone(value); }
  } catch (_) { /* Storage may be blocked or contain an invalid old entry. */ }
  return undefined;
}
export function writeDraft(key, value) {
  if (!key) return;
  const saved = clone(value);
  memory.set(key, saved);
  try { globalThis.localStorage?.setItem(key, JSON.stringify(saved)); } catch (_) { /* In-memory fallback. */ }
  for (const callback of listeners.get(key) || []) callback(clone(saved));
}
export function clearDraft(key) {
  if (!key) return;
  memory.set(key, undefined);
  try { globalThis.localStorage?.removeItem(key); } catch (_) { /* Shadow the old entry in this session. */ }
  for (const callback of listeners.get(key) || []) callback(undefined);
}
export function clearDraftIfUnchanged(key, snapshot) {
  if (JSON.stringify(readDraft(key)) === JSON.stringify(snapshot)) clearDraft(key);
}
export function subscribeDraft(key, callback) {
  if (!key) return () => {};
  if (!listeners.has(key)) listeners.set(key, new Set());
  listeners.get(key).add(callback);
  return () => { const set = listeners.get(key); set?.delete(callback); if (!set?.size) listeners.delete(key); };
}
export function uploadToDraft(key, field, fallback = {}) {
  // Capture identity now: completion may arrive after navigation or unmount.
  return files => {
    const file = files?.find(item => typeof item?.id === 'string');
    if (file && key) writeDraft(key, { ...(readDraft(key) || fallback), [field]: file.id });
  };
}
