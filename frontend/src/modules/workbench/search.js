import { USER_FACING_ERROR } from '../../shared/userFacingError.js';
const modes = new Set(['auto', 'on', 'off']);

export function normalizeSearchMode(value) {
  return modes.has(value) ? value : 'auto';
}

export function safeSourceUrl(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\u0000-\u0020\u007f]/u.test(value)) return '';
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return '';
    return url.href;
  } catch { return ''; }
}

export function publicSearchSources(value) {
  if (!Array.isArray(value)) return [];
  const seen = new Set();
  return value.slice(0, 20).flatMap((item, index) => {
    const url = safeSourceUrl(item?.url);
    if (!url || seen.has(url)) return [];
    seen.add(url);
    const text = (part, max) => typeof part === 'string' ? part.slice(0, max) : '';
    return [{
      id: text(item?.id, 80) || String(index + 1),
      title: text(item?.title, 240) || url,
      url,
      publishedAt: text(item?.publishedAt, 80),
      snippet: text(item?.snippet, 600),
    }];
  });
}

export function publicSkillsUsed(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.slice(0, 20).map((item) => typeof item === 'string' ? item : item?.name)
    .filter((item) => typeof item === 'string' && item.length > 0 && item.length <= 80))];
}

export function publicToolsUsed(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.slice(0, 40).map((item) => typeof item === 'string' ? item : item?.name)
    .filter((item) => typeof item === 'string' && /^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(item)))];
}

export function applyAssistantSearchEvent(message, event, data) {
  if (!message || message.role !== 'assistant') return '';
  if (event === 'skill_loaded') {
    message.skillsUsed = publicSkillsUsed([...(message.skillsUsed || []), data?.name]);
    return data?.name ? `已加载技能：${String(data.name).slice(0, 80)}` : '已加载技能';
  }
  if (event === 'search_status') {
    if (data?.status === 'searching') return data?.query ? `正在联网搜索：${String(data.query).slice(0, 80)}` : '正在联网搜索';
    if (data?.status === 'complete' || data?.status === 'finished') return '搜索完成，正在整理回复';
    if (data?.status === 'empty') return '未找到搜索结果，正在整理回复';
    if (data?.status === 'error' || data?.status === 'failed') return USER_FACING_ERROR;
    return '';
  }
  if (event === 'search_sources') {
    message.sources = publicSearchSources([...(message.sources || []), ...(Array.isArray(data?.sources) ? data.sources : [])]);
    return message.sources.length ? `找到 ${message.sources.length} 条来源，正在整理回复` : '正在整理回复';
  }
  if (event === 'tool_status') {
    const name = publicToolsUsed([data?.name || data?.tool])[0];
    if (!name) return '';
    const status = String(data?.status || '').toLowerCase();
    if (['running', 'started', 'start', 'executing'].includes(status)) return `正在使用工具：${name}`;
    if (['complete', 'completed', 'succeeded', 'success'].includes(status)) {
      message.toolsUsed = publicToolsUsed([...(message.toolsUsed || []), name]);
      return `工具 ${name} 已完成，正在整理回复`;
    }
    if (['error', 'failed', 'failure'].includes(status)) return USER_FACING_ERROR;
    return '';
  }
  return '';
}

export function mergeCompletedAssistantMessage(task, pending) {
  const completed = task?.messages?.at(-1);
  if (pending?.role !== 'assistant' || completed?.role !== 'assistant') return task;
  completed.sources = publicSearchSources(completed.sources?.length ? completed.sources : pending.sources);
  completed.skillsUsed = publicSkillsUsed(completed.skillsUsed?.length ? completed.skillsUsed : pending.skillsUsed);
  completed.toolsUsed = publicToolsUsed(completed.toolsUsed?.length ? completed.toolsUsed : pending.toolsUsed);
  return task;
}
