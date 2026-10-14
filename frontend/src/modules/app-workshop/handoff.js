import { accountDraftKey, readDraft, writeDraft, clearDraft } from '../workbench/draftStore.js';
import { fileFields, initialInputs } from '../workbench/agentForms.js';
import { loadDraft, saveDraft } from '../strategy-workshop/format.js';

export function workshopRequirement(session, fallback = '') {
  const original = String(session?.requirement || fallback || '');
  const supplements = (session?.clarifications || []).filter(value => typeof value === 'string' && value.trim());
  return original + (supplements.length ? '\n\n补充要求：\n' + supplements.map((value, index) => `${index + 1}. ${value}`).join('\n') : '');
}

// The original question is context, never a guessed file id, column name or number.
export function safeHandoffInputs(spec, inputs = {}, files = []) {
  const result = {}, fileNames = new Set(fileFields(spec));
  for (const [name, field] of Object.entries(spec?.inputSchema?.properties || {})) {
    const value = inputs[name];
    if (value === undefined || value === null || ('const' in field && value !== field.const)) continue;
    if (fileNames.has(name) && !files.some(file => String(file.id) === value)) continue;
    if (field.enum && !field.enum.includes(value)) continue;
    if (field.type === 'string' && typeof value !== 'string') continue;
    if (['number', 'integer'].includes(field.type) && (typeof value !== 'number' || !Number.isFinite(value) || (field.type === 'integer' && !Number.isInteger(value)))) continue;
    if (field.type === 'boolean' && typeof value !== 'boolean') continue;
    if (field.type === 'array' && !Array.isArray(value)) continue;
    if (field.type === 'object' && (typeof value !== 'object' || Array.isArray(value))) continue;
    result[name] = value;
  }
  return initialInputs(spec, result);
}

export function prepareAgentHandoff(owner, entry, requirement, inputs, files) {
  const key = accountDraftKey(owner, 'invoke', entry.agentRef);
  const goalKey = accountDraftKey(owner, 'invoke-goal', entry.agentRef);
  const backup = accountDraftKey(owner, 'invoke-backup', entry.agentRef);
  const current = readDraft(key), goal = readDraft(goalKey);
  if (current !== undefined || goal !== undefined) writeDraft(backup, { inputs: current || {}, goal: goal || '' });
  writeDraft(key, safeHandoffInputs(entry.spec, inputs, files));
  writeDraft(goalKey, String(requirement || ''));
}

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
  clearDraft(strategyHandoffKey(owner)); return true;
}
