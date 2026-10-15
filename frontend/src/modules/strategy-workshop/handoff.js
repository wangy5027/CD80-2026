import { accountDraftKey, clearDraft, readDraft, writeDraft } from '../workbench/draftStore.js';
import { loadDraft, saveDraft } from './format.js';

// The application workshop may suggest this dedicated workflow. Carry only
// the user's original question and keep their previous local strategy draft.
export const strategyHandoffKey = owner => accountDraftKey(owner, 'strategy-handoff', 'current');

export function prepareStrategyHandoff(owner, requirement) {
  const previous = loadDraft(globalThis.localStorage, owner);
  writeDraft(strategyHandoffKey(owner), { requirement, previous });
  saveDraft(globalThis.localStorage, owner, { idea: requirement, sessionId: null });
}

export function restoreStrategyDraft(owner) {
  const handoff = readDraft(strategyHandoffKey(owner));
  if (!handoff?.previous) return false;
  saveDraft(globalThis.localStorage, owner, handoff.previous);
  clearDraft(strategyHandoffKey(owner));
  return true;
}
