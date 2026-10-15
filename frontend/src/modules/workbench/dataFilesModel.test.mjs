import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DATA_FILE_ACCEPT, DATA_FILE_LIMIT_BYTES, dataFileFormat,
  isDataManagementFile, validateDataUpload,
} from './dataFilesModel.js';

test('data upload accepts only the four advertised formats within 10 MiB', () => {
  assert.equal(DATA_FILE_ACCEPT, '.csv,.tsv,.json,.xlsx');
  for (const name of ['sample.csv', 'sample.TSV', 'sample.json', 'sample.xlsx']) {
    assert.equal(validateDataUpload({ name, size: DATA_FILE_LIMIT_BYTES }), '');
  }
  assert.match(validateDataUpload({ name: 'sample.csv', size: DATA_FILE_LIMIT_BYTES + 1 }), /10 MB/);
  assert.match(validateDataUpload({ name: 'sample.zip', size: 1 }), /CSV/);
  assert.match(validateDataUpload({ name: 'sample.csv.exe', size: 1 }), /CSV/);
});

test('only explicit data-management origin is included in personal data area', () => {
  assert.equal(isDataManagementFile({ source: 'data_management' }), true);
  assert.equal(isDataManagementFile({ name: 'sample.csv' }), false);
  assert.equal(dataFileFormat({ name: 'sample.xlsx' }), 'Excel · XLSX');
});
