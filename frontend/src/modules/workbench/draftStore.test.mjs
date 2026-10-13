import test from 'node:test';
import assert from 'node:assert/strict';
import { accountDraftKey, readDraft, writeDraft, clearDraft, clearDraftIfUnchanged, subscribeDraft, uploadToDraft } from './draftStore.js';

test('drafts survive a fresh module load and isolate account/entity/purpose with cloned values', async () => {
  const old = globalThis.localStorage; const data = new Map();
  globalThis.localStorage = { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) };
  try {
    const key = accountDraftKey('persist-a', 'invoke', 'csv@1');
    const original = { fileId: 'file-a', options: { rows: 2 } };
    writeDraft(key, original); original.options.rows = 999;
    const loaded = readDraft(key); loaded.options.rows = 888;
    assert.equal(readDraft(key).options.rows, 2);
    const reloaded = await import('./draftStore.js?fresh-draft-test');
    assert.deepEqual(reloaded.readDraft(key), { fileId: 'file-a', options: { rows: 2 } });
    assert.equal(readDraft(accountDraftKey('persist-b', 'invoke', 'csv@1')), undefined);
    assert.equal(readDraft(accountDraftKey('persist-a', 'supply', 'csv@1')), undefined);
    assert.equal(readDraft(accountDraftKey('persist-a', 'invoke', 'csv@2')), undefined);
  } finally { globalThis.localStorage = old; }
});

test('late upload selects its captured field after unmount without overwriting another account or edits', () => {
  const key = accountDraftKey('upload-a', 'revision-input', 'run1');
  const other = accountDraftKey('upload-b', 'revision-input', 'run1');
  writeDraft(key, { firstFile: 'old-first', secondFile: '', groupBy: 'old' });
  const uploaded = uploadToDraft(key, 'secondFile');
  writeDraft(key, { firstFile: 'old-first', secondFile: '', groupBy: 'new' });
  writeDraft(other, { firstFile: 'private-b' });
  let restored;
  const unsubscribe = subscribeDraft(key, value => { restored = value; });
  uploaded([{ id: 'uploaded-second' }]);
  unsubscribe();
  assert.deepEqual(restored, { firstFile: 'old-first', secondFile: 'uploaded-second', groupBy: 'new' });
  assert.deepEqual(readDraft(other), { firstFile: 'private-b' });
  uploaded([]);
  assert.equal(readDraft(key).secondFile, 'uploaded-second');
});

test('successful submission clears only unchanged draft and storage failure falls back to memory', () => {
  const old = globalThis.localStorage;
  globalThis.localStorage = { getItem() { throw Error('blocked'); }, setItem() { throw Error('quota'); }, removeItem() { throw Error('blocked'); } };
  try {
    const key = accountDraftKey('memory-a', 'feedback', 'run1');
    writeDraft(key, 'submitted'); const snapshot = readDraft(key);
    writeDraft(key, 'new edits'); clearDraftIfUnchanged(key, snapshot);
    assert.equal(readDraft(key), 'new edits');
    clearDraftIfUnchanged(key, 'new edits'); assert.equal(readDraft(key), undefined);
    writeDraft(key, { fileId: 'f1' }); clearDraft(key); assert.equal(readDraft(key), undefined);
  } finally { globalThis.localStorage = old; }
});
