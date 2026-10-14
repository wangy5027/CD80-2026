export const modules = Object.freeze([
  { id: 'event', title: '理解事件', description: '梳理发生了什么、背景与可能的影响路径。', example: '最近的半导体产业政策发生了什么变化？主要影响路径是什么？' },
  { id: 'relation', title: '发现关联', description: '追踪主体之间有方向、有条件的研究线索。', example: '这项政策可能通过哪些环节影响 A 股半导体产业链？' },
  { id: 'history', title: '历史验证', description: '分析主体与关联主体的历史联系，并结合历史新闻理解当前关系。', example: '这家公司和关联主体的当前联系，可能受到哪些历史事件影响？' },
]);

export const exploreModels = Object.freeze([
  { id: 'GLM-5.3-Flash', label: 'GLM-5.3-Flash', description: '响应更快' },
  { id: 'GLM-5.3', label: 'GLM-5.3', description: '完整模型' },
]);
export const defaultExploreModel = exploreModels[0].id;
export const validExploreModel = value => exploreModels.some(model => model.id === value);

export const moduleTitle = id => modules.find(item => item.id === id)?.title || '探索分析';
export const maxContextFiles = 8;
export function normalizeContexts(value) {
  const contexts = {};
  for (const { id } of modules) {
    const item = value?.[id] || {};
    const mode = item.mode === 'manual' ? 'manual' : 'auto';
    const fileIds = Array.isArray(item.file_ids) ? [...new Set(item.file_ids.filter(fileId => typeof fileId === 'string' && fileId))].slice(0, maxContextFiles) : [];
    contexts[id] = { mode, file_ids: mode === 'manual' ? fileIds : [] };
  }
  return contexts;
}
export function contextRequest(module, context) {
  const selected = normalizeContexts({ [module]: context })[module] || { mode: 'auto', file_ids: [] };
  return { module, mode: selected.mode, file_ids: selected.file_ids };
}
export const fileKindLabel = kind => ({ material: '材料', report: '报告' })[kind] || '文件';
export const fileOriginLabel = origin => ({ web_search: '网页搜索', news: 'AkShare 快讯', public_page: '公开网页', structured_data: '结构化数据', tool_result: '研究工具', user: '用户提供', report: '研究报告' })[origin] || '来源未标明';
export const contextFiles = run => Array.isArray(run?.context_files) ? run.context_files.filter(item => item && typeof item === 'object') : [];
export const activeRun = run => ['queued', 'running'].includes(run?.status);
export const feedbackRun = run => run?.module === 'event' && run?.status === 'awaiting_feedback' && Boolean(String(run?.initial_answer || '').trim());
export const isHistoryLiteRun = run => run?.module === 'history' && String(run?.workflow_version || '').startsWith('history-lite@');
export const reviewRun = run => ['awaiting_clarification', 'awaiting_review'].includes(run?.status);
export const terminalRun = run => ['succeeded', 'partial', 'failed', 'cancelled', 'interrupted'].includes(run?.status);

// First-step searches inform the short answer but are not a user-facing source list.
// An understood historical run moves to phase "done" without a deep report.
export function isEventInitial(run) {
  if (run?.module !== 'event' || !String(run?.workflow_version || '').startsWith('event-lite@') || run?.report) return false;
  return run.phase === 'initial_explanation' ||
    (Boolean(String(run.initial_answer || '').trim()) && run.feedback_choice !== 'explain_more' && run.phase !== 'event_deep_report');
}

export function visibleRunMaterials(run) {
  return isEventInitial(run) ? [] : runMaterials(run);
}

export function visibleNewFiles(files, run) {
  const rows = Array.isArray(files) ? files.filter(file => file && typeof file === 'object') : [];
  if (!isEventInitial(run)) return rows;
  const fileIds = new Set(Object.values(run.initial_file_ids || {}).map(String));
  return rows.filter(file => fileIds.has(String(file.id)) ||
    (String(file.source_run_id) === String(run.id) && file.phase === 'initial_explanation' && file.source_id == null &&
      ((file.kind === 'material' && file.origin === 'user') || (file.kind === 'report' && file.origin === 'report'))));
}

export function eventInitialProgressLabel(run) {
  return run?.stage === 'explaining' || run?.model_progress ? '正在回复' : '正在探索';
}

export function parseExploreHash(hash = '') {
  const match = /^#explore(?:\?([^#]*))?$/.exec(String(hash));
  if (!match) return { isExplore: false, sessionId: '', runId: '' };
  const params = new URLSearchParams(match[1] || '');
  return { isExplore: true, sessionId: params.get('session') || '', runId: params.get('run') || '' };
}

export function exploreHash(sessionId = '', runId = '') {
  const params = new URLSearchParams();
  if (sessionId) params.set('session', String(sessionId));
  if (sessionId && runId) params.set('run', String(runId));
  return '#explore' + (params.size ? '?' + params.toString() : '');
}

export function runTree(runs) {
  if (!Array.isArray(runs)) return [];
  const ordered = runs.filter(run => run?.id).map((run, index) => ({ run, index })).sort((a, b) => {
    const aTime = Date.parse(a.run.created_at || '') || 0;
    const bTime = Date.parse(b.run.created_at || '') || 0;
    return aTime - bTime || a.index - b.index;
  });
  const byReport = new Map(ordered.filter(({ run }) => run.report?.id).map(({ run }) => [String(run.report.id), String(run.id)]));
  const children = new Map();
  const roots = [];
  for (const { run } of ordered) {
    const parentId = byReport.get(String(run.source_report_id || '')) ||
      (ordered.some(item => String(item.run.id) === String(run.source_run_id || '')) ? String(run.source_run_id) : '');
    if (!parentId || parentId === String(run.id)) roots.push(run);
    else {
      if (!children.has(parentId)) children.set(parentId, []);
      children.get(parentId).push(run);
    }
  }
  const output = [];
  const visited = new Set();
  function visit(run, depth) {
    const id = String(run.id);
    if (visited.has(id)) return;
    visited.add(id);
    output.push({ ...run, depth: Math.min(depth, 6) });
    for (const child of children.get(id) || []) visit(child, depth + 1);
  }
  for (const root of roots) visit(root, 0);
  for (const { run } of ordered) visit(run, 0);
  return output;
}

export function safeSourceUrl(value) {
  if (typeof value !== 'string' || value.length > 2048 || /[\u0000-\u0020\u007f]/u.test(value)) return '';
  try {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) return '';
    const host = url.hostname.toLowerCase().replace(/^\[|\]$/g, '');
    if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal')) return '';
    if (host.includes(':') || /^(?:127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(host)) return '';
    if (/^172\.(?:1[6-9]|2[0-9]|3[01])\./.test(host)) return '';
    return url.href;
  } catch { return ''; }
}

export function reportSources(report) {
  return Array.isArray(report?.sources) ? report.sources : [];
}

export function runMaterials(run) {
  return Array.isArray(run?.materials) ? run.materials.filter(item => item && typeof item === 'object' && item.id) : [];
}

export function confirmedMaterialIds(run) {
  const ids = Array.isArray(run?.report?.confirmed_sources) ? run.report.confirmed_sources : run?.confirmed_sources;
  const allowed = new Set(runMaterials(run).map(item => String(item.id)));
  return Array.isArray(ids) ? ids.map(item => String(item?.id || item)).filter(id => allowed.has(id)) : [];
}

export function materialContentLabel(kind) {
  return ({ search_snippet: '搜索摘要，未读取网页全文', news_excerpt: '快讯摘录，未读取新闻全文', extracted_page_text: '已提取的公开网页正文', metadata_only: '仅取得页面元数据，未提取正文', structured_data: '结构化查询结果', tool_excerpt: '工具摘录，正文范围未标明', user_input: '用户提供的原文' })[kind] || '材料摘录，正文范围未标明';
}

export function retrievalSearches(record) {
  return Array.isArray(record?.retrieval?.searches) ? record.retrieval.searches.filter(item => item && typeof item === 'object') : [];
}

export function searchStatusLabel(status) {
  return ({ complete: '检索完成', empty: '未找到结果', failed: '检索失败', error: '检索失败' })[status] || '状态未知';
}

export function searchFreshnessLabel(value) {
  return ({ oneDay: '最近 1 天', oneWeek: '最近 1 周', oneMonth: '最近 1 月', noLimit: '不限时间' })[value] || (typeof value === 'string' && value ? value : '未提供');
}

export function sourceKindLabel(kind) {
  return ({ web_search: '网页搜索', news: 'AkShare 快讯', public_page: '公开网页', structured_data: '结构化数据', tool_result: '研究工具', user: '用户提供材料' })[kind] || '来源类型未标明';
}

export function sourceLabel(source) {
  return source?.title || source?.source_name || source?.source || '未提供标题';
}

export function stageLabel(stage) {
  return ({ queued: '已受理', searching_web: '正在联网搜索', reading_pages: '正在读取新闻正文', retrieving_news: '正在检索新闻材料', explaining: '正在结合联网信息解释', checking_intent: '正在核对研究问题', awaiting_feedback: '等待反馈', awaiting_clarification: '等待明确问题', awaiting_review: '等待确认材料', awaiting_input: '等待确认判断', preparing_report: '准备生成报告', analyzing: '正在调用模型分析', validating: '正在校验报告与来源', saving: '正在保存报告', completed: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '运行中断' })[stage] || stage || '等待服务端状态';
}
