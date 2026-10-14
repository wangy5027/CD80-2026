// Presentation only: never replace stored reports, research memory, or diagnostics.
export const AI_FAILURE_MESSAGE = '您当前帐户的思维额度已消耗完毕，请升级您的账户权限或一段时间后重试';

const failedStates = new Set(['failed', 'error', 'interrupted', 'timed_out']);
const settledStates = new Set(['succeeded', 'partial', 'done', 'completed']);
const stoppedStates = new Set(['cancelled', 'canceled', 'stopped']);
const failedVariants = new Set([
  'generation_fallback', 'insufficient_news', 'search_unavailable', 'no_news',
  'insufficient_evidence', 'review_timeout_checkpoint',
]);
const initialFallbackModes = new Set(['model_partial', 'source_excerpt', 'knowledge_fallback', 'system_fallback']);

// Legacy messages have no structured result status. Match explicit failure
// replies at the start, never an isolated uncertainty word in a useful answer.
export function isUnusableAiText(value) {
  const text = typeof value === 'string' ? value.trim() : '';
  if (!text) return true;
  if (text === AI_FAILURE_MESSAGE) return true;
  const lead = text.replace(/^[\s#>*`]+/u, '').replace(/^(?:(?:很抱歉|抱歉|对不起)[，,。.!！：:\s]*)+/u, '');
  return /^(?:(?:我|我们|本助手|AI|模型)(?:现在|当前|暂时|目前)?(?:无法|不能|没法|未能)(?:为您|为你)?(?:回答|作答|完成|处理(?:您的|你的|此|这个)?(?:请求|任务)|提供(?:所需|准确)?(?:答案|回答)|联网|访问(?:互联网|网络))|(?:目前|暂时|当前)?(?:无法|未能)(?:回答|作答|完成(?:本次|本轮|该|这个)?(?:任务|请求|分析|报告)|生成(?:完整)?(?:回答|报告)))/iu.test(lead)
    || /^(?:(?:本轮|本次)(?:新闻)?(?:搜索|检索)?|(?:我|我们)(?:目前|暂时)?|(?:目前|暂时))?(?:没有找到|未找到|未取得|未获取|找不到)(?:任何|可用的?|相关的?|符合条件的?)*(?:新闻|资料|信息|结果|数据|报道)/u.test(lead)
    || /^(?:模型|AI|服务|接口|请求|生成|分析|搜索|检索)(?:调用|运行|处理|响应)?(?:失败|超时|异常|不可用|出错)(?:[：:，,。.!！\s]|$)/iu.test(lead)
    || /^(?:本次运行超过总时间限制|运行失败)(?:[：:，,。.!！\s]|$)/u.test(lead)
    || /^(?:i(?: am|'m)?\s+(?:sorry[,.:!]?\s+)?(?:cannot|can't|am unable to)|unable to)\s+(?:answer|complete|fulfil|fulfill|perform|find|access|browse)\b/iu.test(lead);
}

export function isExploreInitialAiFailure(run) {
  if (!run || stoppedStates.has(run.status)) return false;
  if (initialFallbackModes.has(run.initial_answer_mode)) return true;
  if (typeof run.initial_answer === 'string' && run.initial_answer.trim()) return isUnusableAiText(run.initial_answer);
  return run.status === 'awaiting_feedback';
}

function reportHasBody(report) {
  const pieces = [report.summary, report.answer, report.explanation, report.markdown,
    ...(Array.isArray(report.key_findings) ? report.key_findings : []),
    ...(Array.isArray(report.sections) ? report.sections.flatMap(row => [row?.body, row?.text, row?.content]) : [])];
  return pieces.some(value => typeof value === 'string' && value.trim());
}

export function isExploreAiFailure(run, selectedReport = run?.report) {
  if (!run || stoppedStates.has(run.status)) return false;
  if (failedStates.has(run.status)) return true;
  // Waiting for input and executing a new stage are not empty AI replies.
  if (!settledStates.has(run.status)) return false;
  const report = selectedReport;
  if (!report) return isExploreInitialAiFailure(run) || isUnusableAiText(run.initial_answer);
  if (failedVariants.has(report.report_variant) || failedStates.has(report.status)) return true;
  if (report.retrieval?.report_fallback_reason) return true;
  if (report.answer_status === 'unanswered' || !reportHasBody(report)) return true;
  // Structured reports can legitimately describe a gap before presenting a
  // sourced analysis. Do not treat that single sentence as the whole outcome.
  if (!report.report_variant && !report.answer_status && !report.sections?.length
      && report.summary?.trim() && isUnusableAiText(report.summary)) return true;
  const searches = report.retrieval?.searches;
  if (run.status === 'partial' && Array.isArray(report.sources) && !report.sources.length
      && Array.isArray(searches) && searches.length
      && searches.every(row => row?.result_count === 0)) return true;
  return false;
}
