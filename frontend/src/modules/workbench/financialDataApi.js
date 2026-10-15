// Same-origin client for the authenticated SQLite financial-data bridge.

import { request } from './api.js';

const ROOT = '/financial-data';

function tablePath(table) {
  if (typeof table !== 'string' || !/^cd80_[a-z0-9_]+$/.test(table)) {
    throw new Error('数据表名称不正确。');
  }
  return `${ROOT}/tables/${table}`;
}

function rowPath(table, rowId) {
  if (!Number.isSafeInteger(rowId) || rowId < 1) throw new Error('记录编号不正确。');
  return `${tablePath(table)}/rows/${rowId}`;
}

function queryString(options = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(options)) {
    if (value === undefined || value === null) continue;
    if (!['limit', 'offset', 'after_id', 'sort_by', 'order', 'columns', 'filters', 'include_total'].includes(key)) {
      throw new Error(`不支持的查询参数：${key}`);
    }
    const encoded = key === 'filters' ? JSON.stringify(value)
      : key === 'columns' && Array.isArray(value) ? value.join(',')
        : String(value);
    params.set(key, encoded);
  }
  return params.size ? `?${params}` : '';
}

export const getFinancialDataCapabilities = () => request(`${ROOT}/capabilities`);
export const listFinancialTables = () => request(`${ROOT}/tables`);
export const listFinancialCatalog = () => request(`${ROOT}/catalog`);
export const getFinancialTableSchema = (table) => request(`${tablePath(table)}/schema`);
export const listFinancialRows = (table, options = {}) => request(`${tablePath(table)}/rows${queryString(options)}`);
export const queryFinancialRows = (table, options = {}) => request(`${tablePath(table)}/query`, {
  method: 'POST', body: options,
});
export const getFinancialRow = (table, rowId) => request(rowPath(table, rowId));
export const createFinancialRow = (table, data) => request(`${tablePath(table)}/rows`, {
  method: 'POST', body: { data },
});
export const createFinancialRowsBatch = (table, rows) => request(`${tablePath(table)}/rows/batch`, {
  method: 'POST', body: { rows },
});
export const updateFinancialRow = (table, rowId, data) => request(rowPath(table, rowId), {
  method: 'PATCH', body: { data },
});
export const deleteFinancialRow = (table, rowId) => request(rowPath(table, rowId), {
  method: 'DELETE',
});
