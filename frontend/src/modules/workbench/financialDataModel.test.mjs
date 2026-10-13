import test from 'node:test';
import assert from 'node:assert/strict';
import {
  parseFieldValue, buildFilters, initialFormValues, buildWriteData,
  buildPageQuery, canMoveToNextPage, tableLabel,
} from './financialDataModel.js';

const fields = [
  { name: '_row_id', type: 'INTEGER' },
  { name: 'ts_code', type: 'TEXT' },
  { name: 'trade_date', type: 'TEXT' },
  { name: 'volume', type: 'INTEGER' },
  { name: 'close', type: 'REAL' },
];

test('source codes and dates stay text while numbers reject unsafe or non-finite input', () => {
  assert.equal(parseFieldValue(fields[1], '000001.SZ'), '000001.SZ');
  assert.equal(parseFieldValue(fields[2], '20261001'), '20261001');
  assert.equal(parseFieldValue(fields[3], '0'), 0);
  assert.equal(parseFieldValue(fields[4], '1.25e2'), 125);
  assert.throws(() => parseFieldValue(fields[3], '9007199254740992'), /精确处理/);
  assert.throws(() => parseFieldValue(fields[3], '1.2'), /整数/);
  assert.throws(() => parseFieldValue(fields[4], '1e309'), /有限数值/);
  assert.throws(() => parseFieldValue(fields[4], 'Infinity'), /有限数值/);
});

test('filters preserve empty text and express NULL and NOT NULL explicitly', () => {
  const result = buildFilters([
    { field: 'ts_code', op: 'eq', value: '' },
    { field: 'trade_date', op: 'gte', value: '20260101' },
    { field: 'close', op: 'gte', value: '10.50' },
  ], fields);
  assert.deepEqual(result, [
    { field: 'ts_code', op: 'eq', value: '' },
    { field: 'trade_date', op: 'gte', value: '20260101' },
    { field: 'close', op: 'gte', value: 10.5 },
  ]);
  assert.deepEqual(buildFilters([
    { field: 'trade_date', op: 'isnull', nullMode: 'notnull' },
  ], fields), [{ field: 'trade_date', op: 'isnull', value: false }]);
  assert.throws(() => buildFilters([{ field: 'volume', op: 'contains', value: '5' }], fields), /不支持/);
});

test('edits only submit changed fields and distinguish empty text from NULL', () => {
  const old = { _row_id: 12, ts_code: '000001.SZ', trade_date: null, volume: 0, close: 10.5 };
  const entries = initialFormValues(fields, old);
  entries.ts_code.mode = 'value';
  entries.trade_date.mode = 'value';
  entries.trade_date.text = '';
  entries.close.mode = 'null';
  assert.deepEqual(buildWriteData(fields, entries, old), { trade_date: '', close: null });
  assert.throws(() => buildWriteData(fields, initialFormValues(fields, old), old), /至少修改/);
  const create = initialFormValues(fields);
  create.ts_code.mode = 'value';
  create.ts_code.text = '000001.SZ';
  create.trade_date.mode = 'null';
  assert.deepEqual(buildWriteData(fields, create), { ts_code: '000001.SZ', trade_date: null });
});

test('multiline TEXT is preserved exactly on create and edit', () => {
  const note = { name: 'factor_description', type: 'TEXT' };
  const original = { _row_id: 7, factor_description: '第一行\n第二行' };
  const entries = initialFormValues([fields[0], note], original);
  assert.equal(entries.factor_description.text, '第一行\n第二行');
  entries.factor_description.mode = 'value';
  entries.factor_description.text = '第一行\n修改后的第二行\n';
  assert.deepEqual(buildWriteData([fields[0], note], entries, original), {
    factor_description: '第一行\n修改后的第二行\n',
  });
});

test('default row order uses cursors beyond the offset ceiling', () => {
  assert.deepEqual(buildPageQuery({
    pageSize: 50, pageIndex: 1, sortBy: '_row_id', order: 'asc', filters: [], cursors: [null, 123],
  }), { limit: 50, sort_by: '_row_id', order: 'asc', filters: [], after_id: 123 });
  assert.equal(canMoveToNextPage({
    pageSize: 50, pageIndex: 30_000, sortBy: '_row_id', order: 'asc', hasMore: true,
  }), true);
  assert.equal(canMoveToNextPage({
    pageSize: 50, pageIndex: 20_000, sortBy: 'trade_date', order: 'desc', hasMore: true,
  }), false);
  assert.throws(() => buildPageQuery({
    pageSize: 50, pageIndex: 20_001, sortBy: 'trade_date', order: 'desc', filters: [], cursors: [],
  }), /100 万/);
});

test('known tables have short Chinese names', () => {
  assert.equal(tableLabel({ name: 'cd80_stock_basic', title: 'Basic information...' }), '股票基础信息');
  assert.equal(tableLabel({ name: 'cd80_future', title: 'Future' }), 'cd80_future');
});
