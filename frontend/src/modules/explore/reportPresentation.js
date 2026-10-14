export function isEventBrief(report) {
  return report?.report_format === 'event_brief@1' && report?.module === 'event';
}

export function isEventLite(report) {
  return report?.report_format === 'event_lite@2' && report?.module === 'event';
}

export function isRelationLite(report) {
  return report?.report_format === 'relation_lite@2' && report?.module === 'relation';
}

export function isHistoryLite(report) {
  return report?.report_format === 'history_lite@1' && report?.module === 'history';
}

export function isLiteChoiceReport(report) {
  return isEventLite(report) || isRelationLite(report) || isHistoryLite(report);
}

export function isLiteAnalysisReport(report) {
  return isLiteChoiceReport(report) ||
    (report?.module === 'relation' && report?.report_format === 'relation_lite@1') ||
    (report?.module === 'event' && report?.report_format === 'event_deep_lite@1');
}

export function pendingLiteChoice(run) {
  return isLiteChoiceReport(run?.report) && run?.next_step_state === 'pending';
}

// The three angles are part of this report's contract. Questions remain model-authored.
export function liteAngleRecommendations(report) {
  if (!isLiteChoiceReport(report) || !Array.isArray(report.recommendations) || report.recommendations.length !== 3) return [];
  const angles = ['event', 'relation', 'history'];
  const rows = angles.map(module => report.recommendations.find(item => item?.module === module && typeof item.question === 'string' && item.question.trim()));
  return rows.every(Boolean) && new Set(report.recommendations.map(item => item?.module)).size === 3 ? rows : [];
}

// Preserve the original event-only helpers for older callers.
export const pendingEventChoice = run => isEventLite(run?.report) && pendingLiteChoice(run);
export const eventAngleRecommendations = report => isEventLite(report) ? liteAngleRecommendations(report) : [];

// A brief keeps a material gap beside the finding it qualifies, without
// turning every research limitation into a separate report section.
export function eventBriefCoverageNote(report) {
  if (!isEventBrief(report) || !Array.isArray(report?.coverage)) return '';
  const inline = new Set(Array.isArray(report.inline_coverage_question_ids)
    ? report.inline_coverage_question_ids.filter(id => typeof id === 'string') : []);
  const pending = report.coverage.filter(row =>
    (row?.status === 'partial' || row?.status === 'unanswered') && !inline.has(row?.question_id));
  const findings = Array.isArray(report.key_findings) ? report.key_findings : [];
  const compact = value => String(value || '').toLowerCase().replace(/[^\p{L}\p{N}_]/gu, '');
  const displayed = [...findings];
  const gaps = [];
  for (const row of pending) {
    const gap = typeof row.gap === 'string' ? row.gap.trim().replace(/\s+/g, ' ') : '';
    const needle = compact(gap);
    if (!needle || displayed.some(item => compact(item).includes(needle))) continue;
    gaps.push(gap);
    displayed.push(gap);
  }
  if (!gaps.length) return '';
  return `仍待核实：${gaps.join('；')}`;
}
