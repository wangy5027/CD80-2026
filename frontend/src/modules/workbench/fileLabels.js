// Keep original names intact; content fingerprints distinguish uploaded versions.
export function fileLabel(file) {
  const name = file?.name || '文件';
  if (/^[a-f0-9]{32}$/i.test(file?.md5 || '')) return `${name} · MD5 ${file.md5.slice(0, 8)}`;
  if (/^[a-f0-9]{64}$/i.test(file?.sha256 || '')) return `${name} · SHA256 ${file.sha256.slice(0, 8)}`;
  return file?.id ? `${name} · ${String(file.id).slice(0, 8)}` : name;
}

export function fileIdentityHint(file) {
  return [file?.name || '文件', file?.md5 && `MD5: ${file.md5}`, file?.sha256 && `SHA256: ${file.sha256}`].filter(Boolean).join('\n');
}
