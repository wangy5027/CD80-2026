import assert from 'node:assert/strict';
import test from 'node:test';

import { assetHandoffKey, prepareAssetHandoff, safeAssetHandoffInputs, workshopRequirement } from './handoff.js';
import { readDraft } from '../workbench/draftStore.js';

test('recommended asset carries original question and only typed safe inputs', () => {
  const detail = { id: 'app-a', versionId: 'av-a', definition: { inputSchema: { properties: {
    amount: { type: 'number' }, symbol: { type: 'string' }, fileId: { type: 'string' },
    enabled: { type: 'boolean' }, secretToken: { type: 'string' }, count: { type: 'integer' },
  } } } };
  const inputs = { amount: 100, symbol: '510300.SH', fileId: 'foreign-file', enabled: true,
    secretToken: 'never', count: 1.5, unknown: 'ignored' };
  assert.deepEqual(safeAssetHandoffInputs(detail.definition, inputs),
    { amount: 100, symbol: '510300.SH', enabled: true });
  prepareAssetHandoff('user-a', detail, '比较两次结果', inputs);
  assert.deepEqual(readDraft(assetHandoffKey('user-a', 'app-a')), {
    appId: 'app-a', versionId: 'av-a', requirement: '比较两次结果',
    inputs: { amount: 100, symbol: '510300.SH', enabled: true },
  });
  assert.equal(readDraft(assetHandoffKey('user-b', 'app-a')), undefined);
});

test('requirement retains original text and supplements', () => {
  assert.equal(workshopRequirement({ requirement: '分析收益', clarifications: ['按月', '', '保留原值'] }),
    '分析收益\n\n补充要求：\n1. 按月\n2. 保留原值');
});
