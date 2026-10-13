import { request } from '../workbench/api.js';

const prefix = '/explore';
const segment = id => encodeURIComponent(String(id));

export const listSessions = signal => request(`${prefix}/sessions`, { signal });
export const createSession = (title, signal) => request(`${prefix}/sessions`, { method: 'POST', body: { title }, signal });
export const getSession = (id, signal) => request(`${prefix}/sessions/${segment(id)}`, { signal });
export const listSessionFiles = (id, signal) => request(`${prefix}/sessions/${segment(id)}/files`, { signal });
export const getSessionFile = (sessionId, fileId, signal) => request(`${prefix}/sessions/${segment(sessionId)}/files/${segment(fileId)}`, { signal });
export const renameSessionFile = (sessionId, fileId, name, signal) => request(`${prefix}/sessions/${segment(sessionId)}/files/${segment(fileId)}`, { method: 'PATCH', body: { name }, signal });
export const removeSessionFile = (sessionId, fileId, signal) => request(`${prefix}/sessions/${segment(sessionId)}/files/${segment(fileId)}`, { method: 'DELETE', signal });
export function getSessionContext(sessionId, options = {}, signal) {
  const params = new URLSearchParams();
  if (options.module) params.set('module', options.module);
  if (options.source_report_id) params.set('source_report_id', String(options.source_report_id));
  const query = params.size ? `?${params}` : '';
  return request(`${prefix}/sessions/${segment(sessionId)}/context${query}`, { signal });
}
export const saveSessionContext = (sessionId, body, signal) => request(`${prefix}/sessions/${segment(sessionId)}/context`, { method: 'PUT', body, signal });
export const renameSession = (id, title, signal) => request(`${prefix}/sessions/${segment(id)}`, { method: 'PATCH', body: { title }, signal });
export const deleteSession = (id, signal) => request(`${prefix}/sessions/${segment(id)}`, { method: 'DELETE', signal });
export const createRun = (sessionId, body, signal) => request(`${prefix}/sessions/${segment(sessionId)}/runs`, { method: 'POST', body, signal });
export const getRun = (id, signal) => request(`${prefix}/runs/${segment(id)}`, { signal });
export const cancelRun = (id, signal) => request(`${prefix}/runs/${segment(id)}/cancel`, { method: 'POST', signal });
export const retryRun = (id, idempotencyKey, signal) => request(`${prefix}/runs/${segment(id)}/retry`, { method: 'POST', body: { idempotency_key: idempotencyKey }, signal });
export const provideJudgment = (id, judgment, signal) => request(`${prefix}/runs/${segment(id)}/input`, { method: 'POST', body: { judgment }, signal });
export const clarifyRun = (id, body, signal) => request(`${prefix}/runs/${segment(id)}/clarify`, { method: 'POST', body, signal });
export const confirmRun = (id, body, signal) => request(`${prefix}/runs/${segment(id)}/confirm`, { method: 'POST', body, signal });
export const getReport = (id, signal) => request(`${prefix}/reports/${segment(id)}`, { signal });

export function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export async function downloadReport(id, signal) {
  let response;
  try { response = await fetch(`/desktop/api/explore/reports/${segment(id)}/markdown`, { credentials: 'include', signal }); }
  catch (error) { if (error.name === 'AbortError') throw error; throw new Error('下载失败，请检查网站连接。'); }
  if (!response.ok) {
    let message = '';
    try { message = (await response.json())?.error || ''; } catch { /* non-JSON error */ }
    throw new Error(message || `下载失败（${response.status}）`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `Explore-报告-${String(id).replace(/[^a-zA-Z0-9_-]/g, '')}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function downloadSessionFile(sessionId, fileId, name, signal) {
  let response;
  try { response = await fetch(`/desktop/api/explore/sessions/${segment(sessionId)}/files/${segment(fileId)}/download`, { credentials: 'include', signal }); }
  catch (error) { if (error.name === 'AbortError') throw error; throw new Error('文件下载失败，请检查网站连接。'); }
  if (!response.ok) {
    let message = '';
    try { message = (await response.json())?.error || ''; } catch { /* non-JSON error */ }
    throw new Error(message || `文件下载失败（${response.status}）`);
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  const cleanName = String(name || 'Explore-文件').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '_').slice(0, 120);
  link.download = cleanName.toLowerCase().endsWith('.md') ? cleanName : `${cleanName}.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
