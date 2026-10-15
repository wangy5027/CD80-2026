import test from 'node:test';
import assert from 'node:assert/strict';
import { eventBriefCoverageNote, isEventBrief, isLiteAnalysisReport, isLiteChoiceReport,
  isHistoryLite, isRelationLite, liteAngleRecommendations, pendingLiteChoice } from './reportPresentation.js';

test('only the explicit event brief format uses the short first-step report', () => {
  assert.equal(isEventBrief({ report_format: 'event_brief@1', module: 'event' }), true);
  assert.equal(isEventBrief({ module: 'event' }), false);
  assert.equal(isEventBrief({ report_format: 'event_brief@1', module: 'relation' }), false);
  assert.equal(isEventBrief(null), false);
});

test('the final choice belongs to current event, relation, and history lite reports', () => {
  const recommendations = [
    { module: 'history', question: '历史上如何验证？' },
    { module: 'relation', question: '还有哪些关联？' },
    { module: 'event', question: '事件还有哪些条件？' },
  ];
  const relation = { module: 'relation', report_format: 'relation_lite@2', recommendations };
  assert.equal(isRelationLite(relation), true);
  assert.equal(isLiteChoiceReport(relation), true);
  assert.equal(isLiteAnalysisReport(relation), true);
  assert.deepEqual(liteAngleRecommendations(relation).map(item => item.module), ['event', 'relation', 'history']);
  assert.equal(pendingLiteChoice({ report: relation, next_step_state: 'pending' }), true);
  assert.equal(pendingLiteChoice({ report: relation, next_step_state: 'continued' }), false);
  assert.equal(pendingLiteChoice({ report: relation, next_step_state: 'finished' }), false);
  assert.deepEqual(liteAngleRecommendations({ ...relation, recommendations: recommendations.slice(0, 2) }), []);
  assert.deepEqual(liteAngleRecommendations({ ...relation, recommendations: [recommendations[0], recommendations[0], recommendations[2]] }), []);
  assert.equal(isLiteChoiceReport({ module: 'relation', report_format: 'relation_lite@1' }), false);
  assert.equal(isLiteAnalysisReport({ module: 'relation', report_format: 'relation_lite@1' }), true);
  assert.equal(isLiteAnalysisReport({ module: 'event', report_format: 'event_deep_lite@1' }), true);
  assert.equal(isLiteChoiceReport({ module: 'history', report_format: 'relation_lite@2' }), false);
  const history = { module: 'history', report_format: 'history_lite@1', recommendations };
  assert.equal(isHistoryLite(history), true);
  assert.equal(isLiteChoiceReport(history), true);
  assert.equal(isLiteAnalysisReport(history), true);
  assert.deepEqual(liteAngleRecommendations(history).map(item => item.module), ['event', 'relation', 'history']);
  assert.equal(pendingLiteChoice({ report: history, next_step_state: 'pending' }), true);
  assert.equal(isHistoryLite({ ...history, report_format: 'history_lite@2' }), false);
});

test('brief keeps all actual unresolved gaps by the core findings without duplicating them', () => {
  const report = {
    report_format: 'event_brief@1',
    module: 'event',
    key_findings: ['已核实发布消息。'],
    coverage: [
      { status: 'answered', gap: '' },
      { status: 'partial', gap: '小艺的具体功能尚无直接材料' },
      { status: 'unanswered', gap: '缺少独立实测' },
    ],
  };
  assert.equal(eventBriefCoverageNote(report), '仍待核实：小艺的具体功能尚无直接材料；缺少独立实测');
  assert.equal(eventBriefCoverageNote({ ...report, key_findings: ['小艺的具体功能：尚无直接材料'] }), '仍待核实：缺少独立实测');
  assert.equal(eventBriefCoverageNote({ ...report, key_findings: ['小艺的具体功能尚无直接材料；缺少独立实测。'] }), '');
  assert.equal(eventBriefCoverageNote({ ...report, coverage: [{ status: 'answered', gap: '' }] }), '');
  assert.equal(eventBriefCoverageNote({ ...report, report_format: undefined }), '');
});

test('an affirmed fact never hides an opposite unresolved claim', () => {
  const report = {
    report_format: 'event_brief@1', module: 'event',
    key_findings: ['已经核实发布会举办。'],
    coverage: [{ status: 'partial', gap: '尚未核实端侧模型是否发布；缺少官方原文' }],
  };
  assert.equal(eventBriefCoverageNote(report), '仍待核实：尚未核实端侧模型是否发布；缺少官方原文');
});

test('a server-confirmed inline gap for Q1 does not hide a separate Q2 gap', () => {
  const report = {
    report_format: 'event_brief@1', module: 'event',
    key_findings: ['合成报告已经在正文交代 Q1 的关键条件。'],
    coverage: [
      { question_id: 'Q1', status: 'partial', gap: 'Q1 尚缺原始公告' },
      { question_id: 'Q2', status: 'unanswered', gap: 'Q2 尚缺直接资料' },
    ],
  };
  assert.equal(eventBriefCoverageNote(report), '仍待核实：Q1 尚缺原始公告；Q2 尚缺直接资料');
  assert.equal(eventBriefCoverageNote({ ...report, inline_coverage_question_ids: ['Q1'] }), '仍待核实：Q2 尚缺直接资料');
  assert.equal(eventBriefCoverageNote({ ...report, inline_coverage_question_ids: [null, 'Q1', 'Q3'] }), '仍待核实：Q2 尚缺直接资料');
});
