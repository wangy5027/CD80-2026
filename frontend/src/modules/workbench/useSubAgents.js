import { onUnmounted, reactive, watch } from 'vue';
import { request } from './api.js';

export const subTaskLabels = { queued: '等待执行', planning: '理解任务', running: '正在执行', awaiting_main_approval: '主助手审批中', awaiting_user: '等待你的回答', reviewing: '检查交付', cancelling: '正在停止', succeeded: '已完成', partial: '部分完成', failed: '未完成', cancelled: '已中断', interrupted: '服务重启后中断', withdrawn: '已撤回' };
export function useSubAgents(s, toast) {
  const state = reactive({ queue: null, busy: false, error: '' });
  let epoch = 0, timer = null, disposed = false, readSequence = 0;
  const key = () => globalThis.crypto.randomUUID();
  async function refresh() {
    const sequence = ++readSequence;
    const at = epoch, cid = s.currentTaskId, owner = s.user?.id;
    if (!owner || !cid || !s.runtime?.subAgentAvailable || disposed) return;
    try {
      const q = await request('/conversations/' + encodeURIComponent(cid) + '/queue');
      if (sequence !== readSequence || at !== epoch || owner !== s.user?.id || cid !== s.currentTaskId || disposed) return;
      state.queue = q;
      if (q.items.length && !s.streaming) {
        const task = await request('/tasks/' + encodeURIComponent(cid));
        if (sequence === readSequence && at === epoch && cid === s.currentTaskId && owner === s.user?.id && !s.streaming) s.currentTask = task;
      }
      state.error = '';
    } catch (error) { if (sequence === readSequence && at === epoch) state.error = error.message; }
  }
  async function poll(at) { await refresh(); if (!disposed && at === epoch) timer = setTimeout(() => poll(at), 1000); }
  watch(() => [s.user?.id, s.currentTaskId, s.runtime?.subAgentAvailable], () => {
    epoch++; state.queue = null; state.error = ''; clearTimeout(timer); const at = epoch; timer = setTimeout(() => poll(at), 0);
  });
  async function submit(body, onAccepted) {
    const owner = s.user?.id, cid = s.currentTaskId;
    state.busy = true;
    try {
      const result = await request('/conversations/' + encodeURIComponent(cid || 'new') + '/messages', {
        method: 'POST', headers: { 'Idempotency-Key': key() }, body,
      });
      if (owner !== s.user?.id || cid !== s.currentTaskId) return null;
      onAccepted?.(result);
      s.currentTaskId = result.conversationId;
      localStorage.setItem('cd80-last-task:' + owner, result.conversationId);
      const task = await request('/tasks/' + encodeURIComponent(result.conversationId));
      if (owner !== s.user?.id || s.currentTaskId !== result.conversationId) return null;
      s.currentTask = task;
      s.view = 'home';
      await refresh();
      return result;
    } finally { state.busy = false; }
  }
  async function act(path, body) {
    try {
      await request(path, { method: 'POST', headers: { 'Idempotency-Key': key() }, body });
      await refresh();
    } catch (error) { toast(error.message, true); await refresh(); }
  }
  const cancel = run => act('/sub-agent-runs/' + run.id + '/cancel', { revision: run.revision });
  const answer = (run, value) => act('/sub-agent-runs/' + run.id + '/questions/' + run.question.id + '/answer', { revision: run.revision, answer: value });
  const withdraw = run => act('/conversations/' + run.conversationId + '/queue/items/' + run.id + '/withdraw', { revision: run.revision });
  const resume = () => act('/conversations/' + s.currentTaskId + '/queue/resume', { revision: state.queue.revision });
  onUnmounted(() => { disposed = true; epoch++; clearTimeout(timer); });
  return { state, refresh, submit, cancel, answer, withdraw, resume };
}
