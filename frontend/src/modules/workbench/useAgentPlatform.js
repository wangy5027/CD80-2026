import { reactive, watch, onUnmounted } from 'vue';
import { request } from './api.js';
import { accountDraftKey, readDraft, writeDraft, clearDraft, clearDraftIfUnchanged } from './draftStore.js';
import { normalizeSearchMode } from './search.js';

export const newRequestKey = () => crypto.randomUUID();

export function useAgentPlatform(s, { toast, refreshState, openTask }) {
  const p = reactive({ agents: [], runs: [], runtime: null, loading: false, error: '', busy: {}, selected: null, ownerId: '' });
  const draftKey = (purpose, entity) => accountDraftKey(p.ownerId, purpose, entity);
  const selectionKey = () => draftKey('application-selection', 'catalog');
  function selectEntry(entry) { p.selected = entry; if (entry) writeDraft(selectionKey(), entry.agentRef); else clearDraft(selectionKey()); }
  let epoch = 0;
  let timer;
  let controller;
  const keys = new Map();
  const scope = () => `${s.authenticated ? s.user?.id : ''}|${s.currentTaskId || ''}|${s.view}`;
  const alive = (at, identity) => at === epoch && s.authenticated && identity === scope();
  function reset() {
    epoch++;
    clearTimeout(timer);
    controller?.abort();
    controller = null;
    p.runs = []; p.agents = []; p.runtime = null; p.selected = null; p.error = ''; p.loading = false; p.busy = {};
    p.ownerId = s.authenticated ? String(s.user?.id || '') : '';
    keys.clear();
  }
  async function refresh(options = {}) {
    if (!s.authenticated || !['home', 'applications'].includes(s.view)) return;
    const at = epoch, identity = scope();
    controller?.abort();
    const local = new AbortController(); controller = local;
    p.loading = true;
    try {
      const path = '/agent-runs' + (s.view === 'home' && s.currentTaskId ? '?taskId=' + encodeURIComponent(s.currentTaskId) : '');
      const full = options.catalog !== false;
      const [catalog, runs, runtime] = await Promise.all([
        full ? request('/agents', { signal: local.signal }) : Promise.resolve(null),
        s.view === 'applications' || s.currentTaskId ? request(path, { signal: local.signal }) : Promise.resolve({ runs: [] }),
        full ? request('/agent-runtime', { signal: local.signal }) : Promise.resolve(null),
      ]);
      if (!alive(at, identity)) return;
      if (catalog) {
        p.agents = catalog.agents || [];
        const selectedRef = readDraft(selectionKey());
        p.selected = p.agents.find(entry => entry.agentRef === selectedRef) || null;
      }
      p.runs = runs.runs || [];
      if (runtime) p.runtime = runtime;
      p.error = '';
      // Chat messages are appended after the run state commit. Poll the task
      // independently so a terminal-status observation cannot miss that write.
      if (s.currentTaskId && s.view === 'home' && !s.streaming) {
        const task = await request('/tasks/' + encodeURIComponent(s.currentTaskId), { signal: local.signal });
        if (alive(at, identity) && !s.streaming) s.currentTask = task;
      }
    } catch (error) {
      if (alive(at, identity) && error.name !== 'AbortError') p.error = '状态同步失败：' + error.message + ' 运行不会因连接中断自动停止。';
    } finally { if (alive(at, identity)) p.loading = false; }
  }
  async function poll(full = false) {
    const at = epoch;
    await refresh({ catalog: full });
    if (at === epoch && s.authenticated) timer = setTimeout(() => poll(false), 2500);
  }
  watch(() => [s.authenticated, s.user?.id, s.currentTaskId, s.view], () => { reset(); if (s.authenticated) poll(true); }, { immediate: true });
  onUnmounted(reset);

  async function action(label, path, body = {}, onSuccess, refreshCatalog = false, draftKeys = []) {
    if (p.busy[label]) return;
    const at = epoch, identity = scope();
    p.busy[label] = true;
    const snapshots = draftKeys.map(key => [key, readDraft(key)]);
    const signature = label + ':' + JSON.stringify(body);
    if (!keys.has(signature)) keys.set(signature, newRequestKey());
    try {
      const result = await request(path, { method: 'POST', body: { ...body, idempotencyKey: keys.get(signature) } });
      // Clear only the submitted account's unchanged drafts, even after navigation.
      for (const [key, snapshot] of snapshots) clearDraftIfUnchanged(key, snapshot);
      if (!alive(at, identity)) return;
      keys.delete(signature);
      await refreshState();
      if (!alive(at, identity)) return;
      await onSuccess?.(result);
      if (alive(at, identity)) await refresh({ catalog: refreshCatalog });
      return result;
    } catch (error) { if (alive(at, identity)) toast(error.message, true); }
    finally { if (alive(at, identity)) p.busy[label] = false; }
  }
  const busy = id => Boolean(p.busy[id]);
  const approve = (run, approval, decision) => action(run.id, `/agent-runs/${encodeURIComponent(run.id)}/approvals/${encodeURIComponent(approval.id)}`, { decision, bindingDigest: approval.bindingDigest });
  const cancel = run => action(run.id, `/agent-runs/${encodeURIComponent(run.id)}/cancel`);
  const supply = (run, values) => action(run.id, `/agent-runs/${encodeURIComponent(run.id)}/input`, values, undefined, false, [draftKey('supply', run.id)]);
  const feedback = (run, message) => action(run.id, `/agent-runs/${encodeURIComponent(run.id)}/feedback`, { message }, () => toast('反馈已记录。'), false, [draftKey('feedback', run.id)]);
  const save = run => {
    const approval = [...(run.approvals || [])].reverse().find(a => a.kind === 'save' && ['pending', 'approved'].includes(a.status));
    return action(run.id, '/agents', { runId: run.id, ...(approval ? { approvalId: approval.id, bindingDigest: approval.bindingDigest } : {}) }, () => toast('已保存到个人应用。'), true);
  };
  const revise = (run, values) => action(run.id, '/agent-runs', { mode: 'create', revisionOf: run.id, goal: values.goal, input: values.input, fileIds: values.fileIds || [], taskId: run.taskId || undefined, searchMode: normalizeSearchMode(s.searchMode) }, () => toast('正在设计新版本，完成后需要重新确认。'), false, [draftKey('revision-input', run.id), draftKey('revision-goal', run.id)]);
  const invoke = (entry, values) => action('invoke', '/agent-runs', { mode: 'invoke', agentRef: entry.agentRef, ...values, searchMode: normalizeSearchMode(s.searchMode) }, async result => {
    p.selected = null; toast('已发起应用运行。');
    if (result.taskId) await openTask(result.taskId);
  }, false, [draftKey('invoke', entry.agentRef), selectionKey()]);
  return { p, refresh, busy, approve, cancel, supply, feedback, save, revise, invoke, draftKey, selectEntry };
}
