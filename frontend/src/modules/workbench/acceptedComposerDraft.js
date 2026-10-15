import { clearDraftIfUnchanged, readDraft } from './draftStore.js';

export function composerSnapshot(draft, fileIds, skillNames) {
  return { draft, fileIds: [...fileIds], skillNames: [...skillNames] };
}

const equalSnapshot = (left, right) => JSON.stringify(left) === JSON.stringify(right);

export function acceptComposerSubmission(key, sentSnapshot, currentSnapshot) {
  const sourceCleared = equalSnapshot(readDraft(key), sentSnapshot);
  if (sourceCleared) clearDraftIfUnchanged(key, sentSnapshot);
  return { sourceCleared, visibleUnchanged: sourceCleared && equalSnapshot(currentSnapshot, sentSnapshot) };
}
