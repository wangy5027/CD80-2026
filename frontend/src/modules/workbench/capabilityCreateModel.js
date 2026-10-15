import { MAX_ORIGINAL_BYTES } from './capabilityCreateApi.js';

export function originalFilename(kind) { return kind === 'skill' ? 'SKILL.md' : 'tool.json'; }
export function originalType(kind) { return kind === 'skill' ? 'text/markdown' : 'application/json'; }

export function rawOriginalFile(kind, value) {
  if (kind !== 'skill' && kind !== 'tool') throw new Error('请选择 Skill 或 Tool。');
  if (typeof value !== 'string' || !value.trim()) throw new Error(`请填写完整的 ${originalFilename(kind)} 原文。`);
  const file = new File([value], originalFilename(kind), { type: originalType(kind) });
  if (file.size > MAX_ORIGINAL_BYTES) throw new Error('原件不能超过 20 MiB。');
  return file;
}

export function checkOriginalFile(kind, file) {
  if (kind !== 'skill' && kind !== 'tool') throw new Error('请选择 Skill 或 Tool。');
  if (!file || !file.size) throw new Error('请选择非空原件。');
  if (file.size > MAX_ORIGINAL_BYTES) throw new Error('原件不能超过 20 MiB。');
  const extension = kind === 'skill' ? /\.(md|zip)$/i : /\.(json|zip)$/i;
  if (!extension.test(file.name)) throw new Error(kind === 'skill' ? 'Skill 请上传 SKILL.md 或 ZIP。' : 'Tool 请上传 JSON 或 ZIP。');
  return file;
}

export function normalizeOriginalPreview(value, expectedKind) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || value.kind !== expectedKind ||
      !/^[a-f0-9]{64}$/i.test(value.sha256 || '') || !Array.isArray(value.files) || !Array.isArray(value.issues) ||
      !value.manifest || typeof value.manifest !== 'object' || Array.isArray(value.manifest)) {
    throw new Error('服务端未返回完整的原件预览，请重新检查文件。');
  }
  return { ...value, warnings: Array.isArray(value.warnings) ? value.warnings : [] };
}

export function originalReadiness(value) {
  return ({ blocked: '暂不可调用', needs_configuration: '待配置', unverified: '待验证' })[value] || '待检查';
}

export function knownImportFailure(error) {
  return error?.status >= 400 && error?.status < 500 && error?.code !== 'OUTCOME_UNKNOWN';
}
