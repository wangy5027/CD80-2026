export function isEventBrief(report) {
  return report?.report_format === 'event_brief@1' && report?.module === 'event';
}

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
