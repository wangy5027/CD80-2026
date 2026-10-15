export const DATA_FILE_LIMIT_BYTES = 10 * 1024 * 1024;
export const DATA_FILE_ACCEPT = '.csv,.tsv,.json,.xlsx';

const DATA_EXTENSIONS = new Set(['csv', 'tsv', 'json', 'xlsx']);

export function dataFileExtension(name) {
  const match = String(name || '').toLowerCase().match(/\.([a-z0-9]+)$/);
  return match?.[1] || '';
}

export function dataFileFormat(file) {
  const format = String(file?.dataInfo?.format || '').trim().toLowerCase();
  const extension = DATA_EXTENSIONS.has(format) ? format : dataFileExtension(file?.name);
  return extension === 'xlsx' ? 'Excel · XLSX' : extension.toUpperCase() || '未知格式';
}

export function isDataManagementFile(file) {
  return file?.source === 'data_management';
}

export function validateDataUpload(file) {
  if (!file || !DATA_EXTENSIONS.has(dataFileExtension(file.name)))
    return '仅支持 CSV、TSV、JSON 和 XLSX 数据文件。';
  if (!Number.isFinite(file.size) || file.size < 0 || file.size > DATA_FILE_LIMIT_BYTES)
    return '单个数据文件不得超过 10 MB。';
  return '';
}
