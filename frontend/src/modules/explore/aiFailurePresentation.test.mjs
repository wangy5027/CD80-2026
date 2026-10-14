import assert from 'node:assert/strict';
import { test } from 'node:test';
import { AI_FAILURE_MESSAGE, isExploreAiFailure, isExploreInitialAiFailure, isUnusableAiText } from './aiFailurePresentation.js';

const validReport = { summary: '政策通过需求和成本影响企业，具体影响取决于业务结构。', sections: [{ body: '分析内容' }], sources: [{ id: 'S1' }], report_variant: 'relation_analysis' };

test('AI failure text is exact and does not disclose diagnostic messages', () => {
  assert.equal(AI_FAILURE_MESSAGE, '您当前帐户的思维额度已消耗完毕，请升级您的账户权限或一段时间后重试');
});

test('all stored failure variants and missing terminal answers are covered', () => {
  for (const report_variant of ['generation_fallback', 'insufficient_news', 'search_unavailable', 'no_news', 'insufficient_evidence', 'review_timeout_checkpoint']) {
    assert.equal(isExploreAiFailure({ status: 'partial' }, { ...validReport, report_variant }), true, report_variant);
  }
  for (const status of ['failed', 'interrupted', 'error']) assert.equal(isExploreAiFailure({ status }), true);
  assert.equal(isExploreAiFailure({ status: 'succeeded' }), true);
  assert.equal(isExploreAiFailure({ status: 'succeeded' }, { title: '无正文' }), true);
  assert.equal(isExploreAiFailure({ status: 'partial' }, { ...validReport, answer_status: 'unanswered' }), true);
  assert.equal(isExploreAiFailure({ status: 'partial' }, { ...validReport, retrieval: { report_fallback_reason: 'invalid_json' } }), true);
  assert.equal(isExploreAiFailure({ status: 'partial' }, { ...validReport, sources: [], retrieval: { searches: [{ result_count: 0 }] } }), true);
});

test('successful, still running, clarification, and cancelled states remain usable', () => {
  assert.equal(isExploreAiFailure({ status: 'succeeded' }, validReport), false);
  assert.equal(isExploreAiFailure({ status: 'partial' }, { ...validReport, completion_warning: '部分限制说明待完善' }), false);
  assert.equal(isExploreAiFailure({ status: 'partial' }, { ...validReport, report_variant: 'recommendations_unavailable', retrieval: { recommendation_error: 'invalid_recommendations' } }), false);
  assert.equal(isExploreAiFailure({ status: 'succeeded' }, { ...validReport, summary: '没有找到相关新闻，但已有的三条公告表明公司业务有所变化。' }), false);
  for (const status of ['queued', 'running', 'awaiting_feedback', 'awaiting_clarification', 'awaiting_input', 'cancelled', 'stopped']) {
    assert.equal(isExploreAiFailure({ status }, { report_variant: 'generation_fallback' }), false, status);
  }
});

test('initial fallback is separate from a later successful report and does not mutate stored data', () => {
  for (const initial_answer_mode of ['model_partial', 'source_excerpt', 'knowledge_fallback', 'system_fallback']) {
    const run = { status: 'succeeded', initial_answer_mode, initial_answer: '系统保存的内容', report: validReport };
    const before = JSON.stringify(run);
    assert.equal(isExploreInitialAiFailure(run), true);
    assert.equal(isExploreAiFailure(run), false);
    assert.equal(JSON.stringify(run), before);
  }
  assert.equal(isExploreInitialAiFailure({ status: 'awaiting_feedback', initial_answer: '' }), true);
  assert.equal(isExploreInitialAiFailure({ status: 'running' }), false);
  assert.equal(isExploreInitialAiFailure({ status: 'cancelled', initial_answer_mode: 'system_fallback' }), false);
});

test('explicit inability and service failures use fallback without masking normal uncertainty or quotations', () => {
  for (const text of ['', '   ', '抱歉，我无法回答这个问题。', '我目前不能完成这个任务。', '本轮未取得相关新闻，无法核实。', '找不到相关资料。', '请求超时，请稍后重试。', '模型运行失败：错误代码123', '本次运行超过总时间限制。', '运行失败，请检查流程或工具代码。', 'I cannot answer this request.']) {
    assert.equal(isUnusableAiText(text), true, text);
  }
  for (const text of ['现有证据无法证明因果关系，但需求变化是一个可能的解释。', '研究未找到显著关系，这不等于不存在影响。', '报告引用“我无法回答”的说法来说明局限。', '数据中的失败率为3%，比去年有所下降。']) {
    assert.equal(isUnusableAiText(text), false, text);
  }
});
