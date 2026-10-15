import test from 'node:test';
import assert from 'node:assert/strict';
import {
  getFinancialDataCapabilities, listFinancialCatalog, listFinancialRows, queryFinancialRows,
  createFinancialRow, updateFinancialRow, deleteFinancialRow,
} from './financialDataApi.js';

test('financial data client uses the existing same-origin Cookie request helper', async () => {
  const original = globalThis.fetch;
  const calls = [];
  try {
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options });
      return new Response(JSON.stringify({ data: [], pagination: { total: null } }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      });
    };
    await getFinancialDataCapabilities();
    await listFinancialCatalog();
    await listFinancialRows('cd80_daily', {
      limit: 2, columns: ['code', 'price'],
      filters: [{ field: 'code', op: 'eq', value: '000001' }],
    });
    await queryFinancialRows('cd80_daily', { limit: 1 });
    await createFinancialRow('cd80_daily', { code: '000002' });
    await updateFinancialRow('cd80_daily', 7, { price: 2.5 });
    await deleteFinancialRow('cd80_daily', 7);
    assert.equal(calls.length, 7);
    assert.equal(calls[0].url, '/desktop/api/financial-data/capabilities');
    assert.equal(calls[1].url, '/desktop/api/financial-data/catalog');
    const url = new URL(calls[2].url, 'http://localhost');
    assert.equal(url.pathname, '/desktop/api/financial-data/tables/cd80_daily/rows');
    assert.equal(url.searchParams.get('limit'), '2');
    assert.equal(url.searchParams.get('columns'), 'code,price');
    assert.deepEqual(JSON.parse(url.searchParams.get('filters')),
                     [{ field: 'code', op: 'eq', value: '000001' }]);
    assert.equal(calls[3].url, '/desktop/api/financial-data/tables/cd80_daily/query');
    assert.deepEqual(JSON.parse(calls[3].options.body), { limit: 1 });
    assert.deepEqual(JSON.parse(calls[4].options.body), { data: { code: '000002' } });
    assert.deepEqual(JSON.parse(calls[5].options.body), { data: { price: 2.5 } });
    assert.equal(calls[6].options.method, 'DELETE');
    for (const call of calls) {
      assert.equal(call.options.credentials, 'include');
      assert.equal(call.options.headers['X-API-Key'], undefined);
    }
  } finally {
    globalThis.fetch = original;
  }
});

test('financial data client rejects path-like table names before fetching', async () => {
  const original = globalThis.fetch;
  try {
    globalThis.fetch = () => { throw new Error('unexpected fetch'); };
    assert.throws(() => listFinancialRows('cd80_daily/../../health'), /数据表名称/);
    assert.throws(() => deleteFinancialRow('cd80_daily', -1), /记录编号/);
  } finally {
    globalThis.fetch = original;
  }
});
