import test from 'node:test';
import assert from 'node:assert/strict';
import { readDraft } from '../workbench/draftStore.js';
import { loadDraft, saveDraft } from './format.js';
import { prepareStrategyHandoff, restoreStrategyDraft, strategyHandoffKey } from './handoff.js';

test('application recommendation preserves the prior strategy draft within one account', () => {
  const previousStorage = globalThis.localStorage;
  const values = new Map();
  globalThis.localStorage = {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: key => values.delete(key),
  };
  try {
    saveDraft(globalThis.localStorage, 'owner-a', { idea: '原来的策略', sessionId: 'session-1' });
    prepareStrategyHandoff('owner-a', '使用新需求分析趋势');
    assert.equal(loadDraft(globalThis.localStorage, 'owner-a').idea, '使用新需求分析趋势');
    assert.equal(loadDraft(globalThis.localStorage, 'owner-a').sessionId, null);
    assert.equal(readDraft(strategyHandoffKey('owner-a')).previous.sessionId, 'session-1');
    assert.equal(loadDraft(globalThis.localStorage, 'owner-b'), null);
    assert.equal(restoreStrategyDraft('owner-a'), true);
    assert.equal(loadDraft(globalThis.localStorage, 'owner-a').sessionId, 'session-1');
    assert.equal(readDraft(strategyHandoffKey('owner-a')), undefined);
  } finally {
    globalThis.localStorage = previousStorage;
  }
});
