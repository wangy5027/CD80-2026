import test from 'node:test';
import assert from 'node:assert/strict';
import { accountDraftKey, readDraft, writeDraft } from './draftStore.js';
import { acceptComposerSubmission, composerSnapshot } from './acceptedComposerDraft.js';

test('accepted new conversation removes the original home attachment draft', () => {
  const key = accountDraftKey('accepted-a', 'composer', 'home:assistant:');
  const otherAccount = accountDraftKey('accepted-b', 'composer', 'home:assistant:');
  const sent = composerSnapshot('读取数据', ['csv-id', 'xlsx-id'], []);
  writeDraft(key, sent);
  writeDraft(otherAccount, composerSnapshot('别人的草稿', ['private-id'], []));
  const accepted = acceptComposerSubmission(key, sent, composerSnapshot('读取数据', ['csv-id', 'xlsx-id'], []));
  assert.deepEqual(accepted, { sourceCleared: true, visibleUnchanged: true });
  assert.equal(readDraft(key), undefined);
  assert.deepEqual(readDraft(otherAccount)?.fileIds, ['private-id']);
});

test('acceptance preserves edits made while submission was pending', () => {
  const key = accountDraftKey('accepted-c', 'composer', 'home:assistant:');
  const sent = composerSnapshot('读取数据', ['csv-id', 'xlsx-id'], []);
  const updated = composerSnapshot('新问题', ['fresh-id'], []);
  writeDraft(key, sent);
  writeDraft(key, updated);
  const accepted = acceptComposerSubmission(key, sent, updated);
  assert.deepEqual(accepted, { sourceCleared: false, visibleUnchanged: false });
  assert.deepEqual(readDraft(key), updated);
});
