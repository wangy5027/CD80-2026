export const authoringPhases = {
  preparing: '整理方法', checking: '检查实现', validating: '验证实际效果', repairing: '修复技术问题',
  awaiting_input: '需要补充信息', awaiting_authorization: '等待授权', budget_paused: '本轮额度已用完',
  awaiting_review: '请审阅实际效果', committing: '正在保存', completed: '已完成', failed: '草稿已保留',
  cancelled: '已停止，草稿保留', interrupted: '处理已中断，草稿保留',
};
export const kindLabel = kind => ({ app: 'App', tool: 'Tool', skill: 'Skill' })[kind] || '资产';
export const authoringActive = session => ['preparing', 'checking', 'validating', 'repairing', 'committing'].includes(session?.phase);
export const authoringAllowed = (session, action) => Array.isArray(session?.allowedActions) && session.allowedActions.includes(action);
export const authoringTitle = (session, candidate) => candidate?.content?.name || candidate?.content?.manifest?.name || session?.name || session?.title || session?.instruction || '整理可复用方法';
export const prettyAuthoring = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2);
export function currentEvidence(detail) {
  const id = detail?.candidate?.id || detail?.session?.currentCandidateId;
  return (Array.isArray(detail?.evidence) ? detail.evidence : detail?.evidence ? [detail.evidence] : [])
    .filter(item => !id || item.candidateId === id);
}
export function authoringCases(detail) {
  return currentEvidence(detail).flatMap(item => (item.caseResults || item.cases || []).map(row => ({ ...row, executionMode: row.executionMode || item.executionMode })));
}
export function authoringBudget(detail) {
  const budget = detail?.budget || detail?.session?.budget || {};
  const round = budget.currentRound || budget.round || budget;
  return { roundId: round.id || detail?.session?.budgetRoundId, confirmationId: budget.confirmationId || round.confirmationId,
    number: round.number, limits: round.limits || {}, consumed: round.consumed || {}, cumulative: budget.cumulative || budget.totalConsumed || budget.lifetimeConsumed };
}
export function authoringConfirmation(detail, mode) {
  const session = detail?.session;
  if (!session || !authoringAllowed(session, mode)) throw new Error('当前状态不能进行此操作，请刷新后查看下一步。');
  if (mode === 'draft_only') return { revision: session.revision, mode };
  const candidate = detail.candidate, review = detail.reviewSnapshot;
  if (!candidate || !review || candidate.id !== session.currentCandidateId || review.candidateId !== candidate.id || review.contentDigest !== candidate.contentDigest)
    throw new Error('审阅内容已变化，请读取当前候选及实际结果后重新确认。');
  return { revision: session.revision, mode, candidateId: candidate.id, contentDigest: candidate.contentDigest,
    reviewSnapshotId: review.id, reviewDigest: review.reviewDigest,
    ...(session.target?.baseRevision != null ? { baseRevision: session.target.baseRevision } : {}) };
}
export function authoringSource(kind, reference) { return { kind, reference }; }
export function sessionFromResponse(value) { return value?.session || value?.authoringSession || (value?.id && value?.phase ? value : null); }
export function firstAssetAppRun(messages, index) {
  const reference = message => message?.assetAppRunId ? message : message?.subAgentRun?.assetAppRunId ? message.subAgentRun : null;
  const run = reference(messages[index]);
  return run && !messages.slice(0,index).some(message => reference(message)?.assetAppRunId === run.assetAppRunId) ? run : null;
}
export function authoringConnectionRequirements(detail, connections) {
  const found = new Map((detail?.bindingRequirements || []).map(item => [item.sourceIdentity, { ...item }]));
  const visit = (value, nodeId = '', depth = 0) => {
    if (!value || typeof value !== 'object' || depth > 12) return;
    const manifest = value.manifest, execution = manifest?.tool?.execution;
    if (execution?.type === 'mcp_http' && execution.connectionIdentity) {
      const identity = execution.connectionIdentity;
      if (!found.has(identity)) found.set(identity, { sourceIdentity: identity, name: manifest.name || identity, nodeIds: [] });
      const item = found.get(identity); item.nodeIds = [...new Set([...(item.nodeIds || []), ...(nodeId ? [nodeId] : [])])];
    }
    if (value.component) visit(value.component,nodeId,depth+1);
    for (const node of value.nodes || []) visit(node,node.id || nodeId,depth+1);
    for (const field of ['dependencies','dependencySnapshots']) {
      const rows = value[field];
      for (const row of Array.isArray(rows) ? rows : rows && typeof rows === 'object' ? Object.values(rows) : []) visit(row,nodeId,depth+1);
    }
  };
  visit(detail?.session?.draftContent || detail?.candidate?.content);
  return [...found.values()].filter(item => item.sourceIdentity).map(item => ({ ...item,
    options: connections ? connections.filter(connection => connection.sourceIdentity === item.sourceIdentity).map(connection => ({ connectionId: connection.connectionId, name: connection.name, configured: connection.configured === true, enabled: connection.enabled !== false })) : item.options || [],
  }));
}
