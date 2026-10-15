import test from 'node:test';
import assert from 'node:assert/strict';
import { initialToolValues, parseToolArguments } from './toolForm.js';

const schema = { type: 'object', properties: {
  query: { type: 'string', maxLength: 20 },
  count: { type: 'integer', minimum: 1, maximum: 5 },
  freshness: { type: 'string', enum: ['oneDay', 'oneWeek'] },
  redOnly: { type: 'boolean' },
  options: { type: 'object' },
  codes: { type: 'array' },
  adjustment: { type: 'integer', enum: [1, 2, 3] },
}, required: ['query'], additionalProperties: false };

test('standalone tool form omits blank optional values and unknown fields', () => {
  const values = initialToolValues(schema);
  assert.equal(values.redOnly, '');
  assert.deepEqual(parseToolArguments(schema, { ...values, query: '  成都八零  ', secret: 'must not send' }), { query: '成都八零' });
});

test('standalone tool form preserves enum types, false, numbers and JSON structures', () => {
  const values = { query: '新闻', count: '4', freshness: JSON.stringify('oneDay'), redOnly: 'false', options: '{"sort":"new"}', codes: '["000001"]', adjustment: JSON.stringify(2) };
  assert.deepEqual(parseToolArguments(schema, values), {
    query: '新闻', count: 4, freshness: 'oneDay', redOnly: false,
    options: { sort: 'new' }, codes: ['000001'], adjustment: 2,
  });
});

test('standalone tool form rejects missing, invalid, or out-of-range arguments before POST', () => {
  assert.throws(() => parseToolArguments(schema, {}), /请填写query/);
  assert.throws(() => parseToolArguments(schema, { query: '新闻', count: '1.2' }), /整数/);
  assert.throws(() => parseToolArguments(schema, { query: '新闻', count: '6' }), /不能大于/);
  assert.throws(() => parseToolArguments(schema, { query: '新闻', adjustment: JSON.stringify(9) }), /选项无效/);
  assert.throws(() => parseToolArguments(schema, { query: '新闻', options: '[]' }), /JSON 对象/);
  assert.throws(() => parseToolArguments(schema, { query: '新闻', redOnly: 'maybe' }), /是或否/);
});

test('defaults populate form without making optional booleans implicitly false', () => {
  const withDefault = { type: 'object', properties: { limit: { type: 'integer', default: 5 }, active: { type: 'boolean' }, mode: { type: 'string', enum: ['new'], default: 'new' } } };
  const values = initialToolValues(withDefault);
  assert.deepEqual(parseToolArguments(withDefault, values), { limit: 5, mode: 'new' });
});
