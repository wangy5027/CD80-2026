const ROOT = '/desktop/api/skill-pool/standard-export';

export function standardExportSelection(kind, item, detail) {
  if (!item?.source || !item.id || !detail || !['app', 'skill', 'tool'].includes(kind)) return null;
  const versionId = kind === 'app'
    ? item.source === 'asset' ? detail.versionId
      : item.source === 'strategy' ? detail.app?.versionId
        : item.source === 'community-app' ? detail.sourceVersionId
          : item.versionId
    : item.source === 'community' ? '' : detail.version?.versionId || item.versionId;
  return { kind, source: item.source, id: item.id, versionId: versionId || '', name: item.name };
}

export function standardExportPath(selection) {
  const query = new URLSearchParams({ kind: selection.kind, source: selection.source,
    id: selection.id, versionId: selection.versionId || '' });
  return ROOT + '?' + query;
}

export function standardZipName(name) {
  const value = String(name || '').replace(/\p{C}/gu, '_').replace(/[<>:"/\\|?*]/g, '_')
    .replace(/^[ .]+|[ .]+$/g, '') || '未命名资产';
  const chars = Array.from(value), encoder = new TextEncoder();
  while (encoder.encode(chars.join('')).length > 235) chars.pop();
  return chars.join('') + '.zip';
}

export function standardFilenameFromDisposition(header) {
  const match = /(?:^|;)\s*filename\*\s*=\s*(?:"UTF-8''([^"]*)"|UTF-8''([^;]*))/i.exec(header || '');
  if (!match) return '';
  try {
    const name = decodeURIComponent((match[1] ?? match[2]).trim());
    if (!name.toLowerCase().endsWith('.zip')) return '';
    const stem = name.slice(0, -4);
    return stem && standardZipName(stem) === name ? name : '';
  } catch (_) { return ''; }
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  try { link.click(); }
  finally {
    link.remove();
    const cleanup = setTimeout(() => URL.revokeObjectURL(url), 60000);
    cleanup?.unref?.();
  }
}

export async function downloadStandardZip(selection, { signal, fetcher = globalThis.fetch, save = saveBlob } = {}) {
  if (signal?.aborted) throw new DOMException('下载已取消', 'AbortError');
  const response = await fetcher(standardExportPath(selection), {
    method: 'GET', credentials: 'include', headers: { Accept: 'application/zip' }, signal,
  });
  if (!response.ok || !/^(application\/zip|application\/x-zip-compressed)(?:;|$)/i.test(response.headers.get('content-type') || '')) {
    throw new Error('标准包下载失败');
  }
  const blob = await response.blob();
  if (signal?.aborted) throw new DOMException('下载已取消', 'AbortError');
  if (!blob.size) throw new Error('标准包下载失败');
  const filename = standardFilenameFromDisposition(response.headers.get('content-disposition')) || standardZipName(selection.name);
  save(blob, filename);
  return filename;
}
