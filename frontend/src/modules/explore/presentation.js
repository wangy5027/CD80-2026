const isoDay = /^(\d{4})-(\d{2})-(\d{2})$/;
const isoInstant = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(\.\d{1,9})?)?(Z|[+-]\d{2}:\d{2})$/;

function validDay(year, month, day) {
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  return date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day;
}

function validInstant(value) {
  const match = isoInstant.exec(value);
  if (!match) return null;
  const [, year, month, day, hour, minute, second, fraction, zone] = match;
  if (!validDay(Number(year), Number(month), Number(day)) || Number(hour) > 23 || Number(minute) > 59 || Number(second || 0) > 59) return null;
  if (zone !== 'Z' && (Number(zone.slice(1, 3)) > 23 || Number(zone.slice(4)) > 59)) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : { date, second: second || '', fraction: fraction || '' };
}

/** Format known ISO instants without inventing a clock time for date-only evidence. */
export function formatEvidenceTime(value) {
  if (typeof value !== 'string' || !value.trim()) return '未知';
  const day = isoDay.exec(value);
  if (day) {
    return validDay(Number(day[1]), Number(day[2]), Number(day[3]))
      ? `${day[1]}年${day[2]}月${day[3]}日` : value;
  }
  const instant = validInstant(value);
  if (!instant) return value;
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', ...(instant.second ? { second: '2-digit' } : {}),
    hourCycle: 'h23',
  }).formatToParts(instant.date).map(part => [part.type, part.value]));
  const seconds = instant.second ? `:${parts.second}${instant.fraction}` : '';
  return `${parts.year}年${parts.month}月${parts.day}日 ${parts.hour}:${parts.minute}${seconds} UTC+8`;
}

/** Return only claim supports explicitly tied to this saved source. */
export function sourceSupports(report, sourceId) {
  if (typeof sourceId !== 'string' || !sourceId || !Array.isArray(report?.claims)) return [];
  const sources = Array.isArray(report?.sources) ? report.sources : [];
  const matchingSource = sources.find(source => source && (source.id === sourceId || source.evidence_id === sourceId));
  const expectedId = matchingSource?.id || sourceId;
  const expectedEvidenceId = matchingSource?.evidence_id || '';
  const rows = [];
  for (const claim of report.claims) {
    if (!claim || typeof claim !== 'object' || !Array.isArray(claim.supports)) continue;
    claim.supports.forEach((support, index) => {
      if (!support || typeof support !== 'object') return;
      const byRef = support.source_ref === expectedId;
      const byEvidence = Boolean(expectedEvidenceId && support.evidence_id === expectedEvidenceId &&
        (!support.source_ref || support.source_ref === expectedId));
      if (!byRef && !byEvidence) return;
      rows.push({
        key: `${String(claim.id || 'claim')}:${index}`,
        claimId: typeof claim.id === 'string' ? claim.id : '',
        claimText: typeof claim.text === 'string' ? claim.text : '',
        claimType: typeof claim.type === 'string' ? claim.type : '',
        supportStatus: typeof claim.support_status === 'string' ? claim.support_status : '',
        conditions: typeof claim.conditions === 'string' ? claim.conditions : '',
        quote: typeof support.quote === 'string' ? support.quote : '',
        sourceRef: typeof support.source_ref === 'string' ? support.source_ref : '',
        evidenceId: typeof support.evidence_id === 'string' ? support.evidence_id : '',
        locations: Array.isArray(support.locations) ? support.locations
          .filter(location => location && Number.isInteger(location.start) && Number.isInteger(location.end) && location.start >= 0 && location.end >= location.start)
          .map(location => ({ start: location.start, end: location.end })) : [],
        backgroundOnly: support.background_only === true,
      });
    });
  }
  return rows;
}

const toolLabels = Object.freeze({
  web_search: '网页搜索', sfc_news: '财经新闻查询', sfc_search: '金融信息查询',
  sfc_timeline: '财经快讯查询', sfc_announcements: '公司公告查询',
  sfc_research: '研究报告查询', sfc_quotes: '行情信息查询',
  sfc_finance: '财务数据查询', sfc_themes: '主题资料查询',
  sfc_history: '历史行情查询', fetch_public_page: '读取公开网页',
  read_evidence: '续读研究材料', load_skill: '加载研究能力',
});
const stageLabels = Object.freeze({
  queued: '已受理研究', searching_web: '联网搜索', reading_pages: '读取新闻正文',
  retrieving_news: '检索新闻材料', explaining: '结合联网信息解释',
  checking_intent: '核对研究问题', awaiting_clarification: '等待明确问题',
  awaiting_review: '等待确认材料', awaiting_input: '等待确认判断',
  preparing_report: '准备报告', analyzing: '模型分析', validating: '校验报告',
  saving: '保存报告', completed: '报告已保存', failed: '研究失败',
  cancelled: '研究已取消', interrupted: '研究已中断',
});
export function publicStageTitle(run, stage) {
  if (run?.module === 'history' && String(run?.workflow_version || '').startsWith('history-lite@')) {
    if (stage === 'explaining') return '说明历史关联分析目的';
    if (stage === 'history_initial_ready') return '已显示初步回复';
  }
  return Object.hasOwn(stageLabels, stage) ? stageLabels[stage] : '研究状态已更新';
}
const statusLabels = Object.freeze({
  complete: '已完成', error: '失败', timeout: '超时', invalid_arguments: '未执行', running: '进行中',
});

function duration(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 3600) return { seconds: null, label: '' };
  return { seconds: value, label: `用时 ${value.toFixed(value < 1 ? 2 : 1)} 秒` };
}

function safeAt(value) {
  return typeof value === 'string' && validInstant(value) ? value : '';
}

function safeRound(value) {
  return Number.isInteger(value) && value >= 0 && value <= 100 ? value : null;
}

/** Public UI rows from persisted research metadata; no tool payloads or errors leave this projection. */
export function publicRunActivity(run) {
  const retrieval = run?.retrieval && typeof run.retrieval === 'object' ? run.retrieval : {};
  const tools = Array.isArray(retrieval.tool_trace) ? retrieval.tool_trace : [];
  const models = Array.isArray(retrieval.model_trace) ? retrieval.model_trace : [];
  const runKey = typeof run?.id === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(run.id) ? run.id : 'run';
  const rows = [];
  models.forEach((record, index) => {
    if (!record || typeof record !== 'object') return;
    const elapsed = duration(record.duration_seconds);
    const status = Object.hasOwn(statusLabels, record.status) ? record.status : 'unknown';
    rows.push({
      key: `${runKey}:model:${index}`, kind: 'model',
      title: record.stage === 'ledger' ? '整理证据账本' : record.stage === 'research' ? '模型检查研究线索' : '模型研究阶段',
      status, statusLabel: Object.hasOwn(statusLabels, status) ? statusLabels[status] : '状态未记录',
      durationSeconds: elapsed.seconds, durationLabel: elapsed.label, at: '',
      round: safeRound(record.round),
      outputChars: Number.isInteger(record.output_chars) && record.output_chars >= 0 ? record.output_chars : null,
      order: index,
    });
  });
  tools.forEach((record, index) => {
    if (!record || typeof record !== 'object') return;
    const elapsed = duration(record.duration_seconds);
    const status = Object.hasOwn(statusLabels, record.status) ? record.status : 'unknown';
    rows.push({
      key: `${runKey}:tool:${index}`, kind: 'tool',
      title: Object.hasOwn(toolLabels, record.name) ? toolLabels[record.name] : '研究工具',
      status, statusLabel: Object.hasOwn(statusLabels, status) ? statusLabels[status] : '状态未记录',
      durationSeconds: elapsed.seconds, durationLabel: elapsed.label,
      at: safeAt(record.started_at), round: safeRound(record.round),
      outputChars: null, order: index,
    });
  });
  if (rows.length) {
    return rows.sort((a, b) => (a.round ?? Infinity) - (b.round ?? Infinity)
      || (a.kind === b.kind ? a.order - b.order : a.kind === 'model' ? -1 : 1))
      .map(({ order, ...row }) => row);
  }
  const history = Array.isArray(run?.stage_history) ? run.stage_history : [];
  return history.filter(item => item && typeof item === 'object').map((item, index) => ({
    key: `${runKey}:stage:${index}`, kind: 'stage', title: publicStageTitle(run, item.stage),
    status: 'recorded', statusLabel: '已记录', durationSeconds: null, durationLabel: '',
    at: safeAt(item.at), round: null, outputChars: null,
  }));
}
