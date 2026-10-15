import { USER_FACING_ERROR, userFacingError } from '../../shared/userFacingError.js';

const terminalStates = new Set(['succeeded', 'partial', 'failed', 'cancelled', 'interrupted', 'withdrawn']);
export const isTerminal = run => terminalStates.has(run.status);
export function conversationMessages(messages = [], runs = []) {
  const matched = new Set(), rows = [];
  const visibleMessage = message => {
    if (message.role !== 'assistant' || !runs.some(run => ['failed', 'interrupted'].includes(run.status) &&
      message.subAgentMessageId === run.id + ':assistant')) return message;
    return { ...message, content: USER_FACING_ERROR };
  };
  for (const [index, message] of messages.entries()) {
    // Early failures in the first backend version saved only the assistant reply.
    const orphan = message.role === 'assistant' && runs.find(r =>
      !matched.has(r.id) && message.subAgentMessageId === r.id + ':assistant' &&
      !messages.some(m => m.role === 'user' && (m.subAgentMessageId === r.id + ':user' ||
        !m.subAgentMessageId && m.content === r.message && (!m.at || !r.createdAt || m.at >= r.createdAt))));
    if (orphan && orphan.status !== 'withdrawn') {
      matched.add(orphan.id);
      rows.push({ role: 'user', content: orphan.message, rowKey: 'pending-user-' + orphan.id });
      if (orphan.kind !== 'chat' || !isTerminal(orphan))
        rows.push({ role: 'assistant', content: '', rowKey: 'run-' + orphan.id, subAgentRun: orphan });
    }
    rows.push({ ...visibleMessage(message), rowKey: message.subAgentMessageId || 'message-' + index });
    if (message.role !== 'user') continue;
    const run = runs.find(r => !matched.has(r.id) && (message.subAgentMessageId === r.id + ':user' ||
      !message.subAgentMessageId && message.content === r.message && (!message.at || !r.createdAt || message.at >= r.createdAt)));
    if (run) {
      matched.add(run.id);
      if (run.status !== 'withdrawn' && (run.kind !== 'chat' || !isTerminal(run)))
        rows.push({ role: 'assistant', content: '', rowKey: 'run-' + run.id, subAgentRun: run });
    }
  }
  for (const run of runs) {
    if (matched.has(run.id) || run.status === 'withdrawn' || run.kind === 'chat' && isTerminal(run)) continue;
    rows.push({ role: 'user', content: run.message, rowKey: 'pending-user-' + run.id });
    rows.push({ role: 'assistant', content: '', rowKey: 'run-' + run.id, subAgentRun: run });
  }
  return rows;
}
export function duration(item) {
  if (!item.startedAt || !item.completedAt) return '';
  return Math.max(0, (Date.parse(item.completedAt) - Date.parse(item.startedAt)) / 1000).toFixed(1) + ' 秒';
}
export function timeline(run) {
  const rows = (run.steps || []).filter(s => s.status !== 'awaiting_approval').map(s => ({ ...s, entryType: 'step', label: s.label || ({ code: '执行临时代码', tool: '调用能力', read: '读取数据' })[s.type] || '执行操作' }));
  rows.push(...(run.capabilityLoads || []).map(item => ({ ...item, entryType: 'capability' })));
  for (const call of run.modelCalls || []) {
    rows.push({ ...call, entryType: 'model', label: call.label || '处理请求', summary: call.summary || (call.status === 'succeeded' ? '已完成本阶段判断' : '') });
  }
  // Old running records may predate terminal step cleanup; never show activity in a finished task.
  return rows.sort((a, b) => (a.startedAt || '').localeCompare(b.startedAt || '')).map(item => {
    const ended = isTerminal(run) && item.status === 'running';
    const failed = ended || ['failed', 'interrupted', 'cancelled'].includes(item.status);
    const visible = { ...item, ...(ended ? { status: 'failed', error: USER_FACING_ERROR } : {}) };
    if (visible.error) visible.error = userFacingError(visible.error);
    if (visible.errorSummary) visible.errorSummary = userFacingError(visible.errorSummary);
    if (failed) {
      // Failed results can contain provider traces or runtime diagnostics.
      delete visible.result;
      delete visible.resultPreview;
      delete visible.purpose;
      if (visible.summary) visible.summary = USER_FACING_ERROR;
    }
    delete visible.errorCode;
    delete visible.validationErrors;
    delete visible.diagnostic;
    delete visible.traceback;
    return visible;
  });
}

export function modelCallTracePath(runId, call) {
  if (!runId || !call?.id || call.traceAvailable === false) return '';
  const expected = '/desktop/api/sub-agent-runs/' + encodeURIComponent(runId) + '/model-calls/' + encodeURIComponent(call.id);
  return call.traceUrl === expected ? expected : '';
}

// A view-owned loader: nothing is requested until load(), and disposed responses are ignored.
export function createModelCallTraceLoader(request = fetch, changed = () => {}) {
  let state = { status: 'idle', text: '', error: '' }, generation = 0, controller = null, cachedPath = '', disposed = false;
  const update = value => { state = value; changed(state); };
  function reset() {
    generation++; controller?.abort(); controller = null; cachedPath = '';
    update({ status: 'idle', text: '', error: '' });
  }
  return {
    get state() { return state; },
    reset,
    dispose() { reset(); disposed = true; },
    async load(path) {
      if (disposed || !path || (cachedPath === path && ['loading', 'loaded'].includes(state.status))) return;
      controller?.abort(); controller = new AbortController();
      const signal = controller.signal, current = ++generation;
      cachedPath = path;
      update({ status: 'loading', text: '', error: '' });
      try {
        const response = await request(path, { credentials: 'same-origin', signal, headers: { Accept: 'application/json' } });
        let data;
        try { data = await response.json(); } catch { throw new Error('请求记录格式无法读取，请重试。'); }
        if (!response.ok) throw new Error(typeof data?.error === 'string' ? data.error : `请求记录读取失败（${response.status}）`);
        if (!data || typeof data !== 'object') throw new Error('请求记录格式无法读取，请重试。');
        if (disposed || signal.aborted || current !== generation) return;
        update({ status: 'loaded', text: JSON.stringify(data, null, 2), error: '' });
      } catch (error) {
        if (disposed || signal.aborted || current !== generation || error.name === 'AbortError') return;
        update({ status: 'error', text: '', error: userFacingError(error) });
      }
    }
  };
}
