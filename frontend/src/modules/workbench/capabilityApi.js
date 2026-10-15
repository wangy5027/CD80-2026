import { request } from './api.js';

export const capabilityPath = '/capabilities';
export const readinessLabels = { checking: '检查中', needs_configuration: '待配置', blocked: '不可调用', unverified: '待验证', verifying: '验证中', verified: '已验证', ready: '免验证 · 可启用', failed: '验证未通过' };
export function capabilityStatus(asset) {
  if (asset.deletedAt) return '已删除';
  if (asset.lifecycle === 'archived') return '已归档';
  if (asset.enabled && ['verified', 'ready'].includes(asset.readiness)) return '已启用';
  if (asset.readiness === 'verified') return '已验证，尚未启用';
  if (asset.readiness === 'ready') return '免验证 · 可启用';
  return readinessLabels[asset.readiness] || '待检查';
}
export function capabilitySelectionReason(asset, selected = false) {
  if (asset.deletedAt) return '已删除，请先在技能池中恢复。';
  if (selected) return '本轮已选择，无需重复添加；可在输入框上方移除。';
  if (asset.lifecycle === 'archived') return '已归档，不能新增选择。请到技能池详情恢复，再检查验证并启用。';
  const reasons = {
    checking: '正在检查，暂不能选择。请到技能池详情查看检查进度。',
    needs_configuration: '缺少配置或依赖，暂不能验证启用。请到技能池详情查看并处理缺失项。',
    blocked: '检查未通过，不能启用。请到技能池详情查看具体原因，处理后重新导入。',
    unverified: '尚未验证。请到技能池详情完成测试并确认结果，再点击“启用此版本”。',
    verifying: '验证尚未完成。请到技能池详情查看结果；Skill 还需确认符合预期，再启用。',
    failed: '验证未通过。请到技能池详情查看失败原因，重新验证通过后再启用。',
  };
  if (reasons[asset.readiness]) return reasons[asset.readiness];
  if (asset.readiness === 'verified' && !asset.enabled) return '已验证，但尚未启用。请到技能池详情点击“启用此版本”。';
  if (asset.readiness === 'ready' && !asset.enabled) return '该官方或公开精确版本已获服务端确认，免重复验证；请到技能池详情点击“启用此版本”。';
  if (asset.enabled && ['verified', 'ready'].includes(asset.readiness) && asset.lifecycle === 'active') return '';
  return '当前状态不支持选择。请到技能池详情检查状态。';
}
export function capGet(path, options = {}) { return request(capabilityPath + path, options); }
export function capPost(path, body, options = {}) {
  return capGet(path, { ...options, method: 'POST', body, headers: { 'Idempotency-Key': crypto.randomUUID(), ...options.headers } });
}
export function capPatch(path, body, revision) {
  return capGet(path, { method: 'PATCH', body, headers: { 'If-Match': `"${revision}"` } });
}
export async function waitCapabilityJob(job, signal, progress = () => {}) {
  let current = job;
  while (['queued', 'running', 'cancelling'].includes(current.status)) {
    if (signal?.aborted) throw new DOMException('已停止等待', 'AbortError');
    progress(current);
    await new Promise(resolve => setTimeout(resolve, 350));
    current = await capGet('/jobs/' + encodeURIComponent(job.jobId), { signal });
  }
  progress(current);
  if (current.status !== 'completed') { const error = new Error(current.error?.error || `任务${current.status}，请检查记录后重试。`); error.details = current.error?.details; throw error; }
  return current.result;
}
