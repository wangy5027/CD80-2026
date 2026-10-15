import test from 'node:test';
import assert from 'node:assert/strict';
import { authoringCreateRequest } from './authoringCreateRequest.js';

test('home and skill-pool APP creation preserve the same full requirement and format', () => {
  const instruction = '  创建一个读取 CSV 的应用。\n输入必须是有表头的 UTF-8；按品类汇总金额，输出 CSV 和校验报告。  ';
  const expected = { kind: 'app', intent: 'create', instruction, mode: 'prepare', sourceRefs: [] };
  assert.deepEqual(authoringCreateRequest({ kind: 'app', intent: 'create', instruction }), expected);
  assert.deepEqual(authoringCreateRequest({ kind: 'app', intent: 'create', instruction, conversationId: null }), { ...expected, conversationId: null });
});

test('home creation carries only explicitly selected owned file identities', () => {
  const request = authoringCreateRequest({ instruction: '请创建 APP', conversationId: 'existing-task',
    fileIds: ['data-file', 'spec-file'] });
  assert.deepEqual(request.sourceRefs, [
    { kind: 'upload', reference: { fileId: 'data-file' } },
    { kind: 'upload', reference: { fileId: 'spec-file' } },
  ]);
  assert.equal(request.conversationId, 'existing-task');
  assert.equal(request.mode, 'prepare');
  assert.equal(request.intent, 'create');
  assert.equal(request.kind, 'app');
});

test('skill-pool import and edit keep their existing contract through the shared builder', () => {
  const imported = authoringCreateRequest({ kind: 'app', intent: 'create', mode: 'prepare', importedId: 'portable',
    fallbackInstruction: '准备使用这份导入原件，保留原有业务规则。' });
  assert.equal(imported.intent, 'prepare_import');
  assert.equal(imported.instruction, '准备使用这份导入原件，保留原有业务规则。');
  assert.deepEqual(imported.sourceRefs, [{ kind: 'import_draft', reference: { portableAssetId: 'portable' } }]);
  const target = { assetId: 'asset', baseVersionId: 'v1' };
  const edit = authoringCreateRequest({ kind: 'tool', intent: 'edit', mode: 'draft_only', instruction: '原文', target });
  assert.equal(edit.kind, 'tool');
  assert.equal(edit.intent, 'edit');
  assert.equal(edit.mode, 'draft_only');
  assert.deepEqual(edit.target, target);
});
