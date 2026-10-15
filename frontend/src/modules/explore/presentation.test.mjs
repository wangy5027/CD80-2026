import test from 'node:test';
import assert from 'node:assert/strict';
import { formatEvidenceTime, sourceSupports, publicRunActivity, publicStageTitle } from './presentation.js';
import { confirmedMaterialIds, fileOriginLabel, materialContentLabel, searchStatusLabel, sourceKindLabel, stageLabel } from './model.js';

test('confirmed materials prefer saved report evidence after an analysis completes', () => {
  const materials = [{ id: 'm1' }, { id: 'm2' }];
  assert.deepEqual(confirmedMaterialIds({ materials, confirmed_sources: [], report: { confirmed_sources: [{ id: 'm1' }, 'unknown'] } }), ['m1']);
  assert.deepEqual(confirmedMaterialIds({ materials, confirmed_sources: ['m1'], report: { confirmed_sources: [] } }), []);
  assert.deepEqual(confirmedMaterialIds({ materials, confirmed_sources: ['m2'] }), ['m2']);
});

test('evidence time keeps date-only precision and converts explicit ISO instants to Shanghai time', () => {
  assert.equal(formatEvidenceTime(null), '未知');
  assert.equal(formatEvidenceTime('  '), '未知');
  assert.equal(formatEvidenceTime('2026-10-13'), '2026年10月13日');
  assert.equal(formatEvidenceTime('2026-10-12T16:15Z'), '2026年10月13日 00:15 UTC+8');
  assert.equal(formatEvidenceTime('2026-10-13T13:15:42.123Z'), '2026年10月13日 21:15:42.123 UTC+8');
  assert.equal(formatEvidenceTime('2026-10-13T09:15:42+08:00'), '2026年10月13日 09:15:42 UTC+8');
  assert.equal(formatEvidenceTime('去年秋季'), '去年秋季');
  assert.equal(formatEvidenceTime('  来源标注：2026年秋  '), '  来源标注：2026年秋  ');
  assert.equal(formatEvidenceTime('2026-02-30'), '2026-02-30');
  assert.equal(formatEvidenceTime('2026-10-13T13:15'), '2026-10-13T13:15');
});

test('source supports use explicit source_ref or evidence mapping, never a section or text resemblance', () => {
  const report = {
    sources: [{ id: 'S1', evidence_id: 'evidence-1' }, { id: 'S2', evidence_id: 'evidence-2' }],
    sections: [{ source_refs: ['S1'], body: '同一主题不能成为主张匹配依据' }],
    claims: [
      { id: 'C1', text: '明确支持的主张', type: 'fact', support_status: 'traceable', conditions: '限本次材料', supports: [
        { source_ref: 'S1', evidence_id: 'evidence-1', quote: '逐字材料', locations: [{ start: 10, end: 14 }, { start: -1, end: 4 }], background_only: false },
      ] },
      { id: 'C2', text: '未关联来源的主张', supports: [{ source_ref: 'S2', evidence_id: 'evidence-2', quote: '另一个来源' }] },
      { id: 'C3', text: '只提供证据 ID 的主张', supports: [{ evidence_id: 'evidence-1', quote: '可追踪证据' }] },
    ],
  };
  const rows = sourceSupports(report, 'S1');
  assert.deepEqual(rows.map(row => [row.claimId, row.quote]), [['C1', '逐字材料'], ['C3', '可追踪证据']]);
  assert.deepEqual(rows[0].locations, [{ start: 10, end: 14 }]);
  assert.deepEqual(sourceSupports(report, 'S2').map(row => row.claimId), ['C2']);
  assert.deepEqual(sourceSupports({ sources: report.sources, sections: report.sections }, 'S1'), []);
  assert.deepEqual(sourceSupports(report, 'unknown'), []);
});

test('public activity exposes only safe result metadata, has stable keys, and falls back to saved stages', () => {
  const run = { id: 'r1', retrieval: {
    tool_trace: [
      { call_id: 'private-id', name: 'web_search', status: 'complete', started_at: '2026-10-13T02:00:00Z', duration_seconds: 1.27, round: 1,
        arguments: { query: '/private/report' }, result: { token: 'SECRET', error: '/private/path' }, error: '/private/path' },
      { name: 'toString', status: 'error', duration_seconds: 0.2, round: 2, result: { text: 'SECRET' } },
    ],
    model_trace: [{ stage: 'research', status: 'complete', duration_seconds: 2.1, output_chars: 182, round: 1, raw_payload: 'SECRET' }],
  } };
  const rows = publicRunActivity(run);
  assert.deepEqual(rows.map(row => row.key), ['r1:model:0', 'r1:tool:0', 'r1:tool:1']);
  assert.equal(rows[1].title, '网页搜索');
  assert.equal(rows[1].statusLabel, '已完成');
  assert.equal(rows[1].durationLabel, '用时 1.3 秒');
  assert.equal(rows[2].title, '研究工具');
  assert.equal(rows[2].statusLabel, '失败');
  assert.equal(publicRunActivity({ retrieval: { tool_trace: [{ name: 'constructor', status: 'toString' }] } })[0].statusLabel, '状态未记录');
  assert.equal(JSON.stringify(rows).includes('SECRET'), false);
  assert.equal(JSON.stringify(rows).includes('/private'), false);
  assert.deepEqual(publicRunActivity(run), rows);
  assert.deepEqual(publicRunActivity({ id: 'r2', stage_history: [{ stage: 'saving', at: '2026-10-13T02:00:00Z' }] }).map(row => row.title), ['保存报告']);
  assert.deepEqual(publicRunActivity({ id: 'r3', stage_history: [{ stage: '/private/path', at: '/private/path' }] }).map(row => [row.title, row.at]), [['研究状态已更新', '']]);
});

test('new source and status labels explain actual content scope without changing old labels', () => {
  assert.equal(sourceKindLabel('public_page'), '公开网页');
  assert.equal(sourceKindLabel('structured_data'), '结构化数据');
  assert.equal(materialContentLabel('extracted_page_text'), '已提取的公开网页正文');
  assert.equal(materialContentLabel('metadata_only'), '仅取得页面元数据，未提取正文');
  assert.equal(materialContentLabel('structured_data'), '结构化查询结果');
  assert.equal(fileOriginLabel('public_page'), '公开网页');
  assert.equal(searchStatusLabel('error'), '检索失败');
  assert.equal(stageLabel('awaiting_clarification'), '等待明确问题');
  assert.equal(stageLabel('reading_pages'), '正在读取新闻正文');
  assert.equal(stageLabel('explaining'), '正在结合联网信息解释');
  assert.equal(stageLabel('interrupted'), '运行中断');
  assert.equal(sourceKindLabel('news'), 'AkShare 快讯');
  assert.deepEqual(publicRunActivity({ id: 'r4', stage_history: [{ stage: 'reading_pages' }, { stage: 'explaining' }] }).map(row => row.title), ['读取新闻正文', '结合联网信息解释']);
});

test('history-lite first answer stages describe purpose before any news search', () => {
  const history = { id: 'history-run', module: 'history', workflow_version: 'history-lite@1', stage_history: [
    { stage: 'explaining' }, { stage: 'history_initial_ready' }, { stage: 'searching_web' },
  ] };
  assert.deepEqual(publicRunActivity(history).map(row => row.title),
    ['说明历史关联分析目的', '已显示初步回复', '联网搜索']);
  assert.equal(publicStageTitle(history, 'explaining'), '说明历史关联分析目的');
  assert.equal(publicStageTitle(history, 'history_initial_ready'), '已显示初步回复');
  assert.deepEqual(publicRunActivity({ ...history, module: 'event' }).map(row => row.title),
    ['结合联网信息解释', '研究状态已更新', '联网搜索']);
  assert.equal(publicStageTitle({ ...history, workflow_version: 'history@old' }, 'explaining'), '结合联网信息解释');
});
