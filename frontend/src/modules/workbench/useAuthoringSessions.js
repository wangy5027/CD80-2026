import { onUnmounted, reactive, watch } from 'vue';
import { createAuthoringApi } from './authoringApi.js';
import { authoringActive, authoringAllowed, authoringBudget, authoringConfirmation, sessionFromResponse } from './authoringModel.js';

// One owner-scoped store is shared by chat cards, the library and the detail panel.
export function createAuthoringStore(transport, changed = () => {}) {
  const state = reactive({ items: [], details: {}, pendingChat: {}, selectedId: '', open: false, loading: false, busy: false, error: '', errorCode: '', pendingRetry: false, conversationId: '', owner: '', notice: '' });
  let controller = new AbortController(), api, epoch = 0, viewSequence = 0, reads = new Map(), listSequence = 0, timer, disposed = false, retryOperation;
  const valid = at => !disposed && at === epoch && !controller.signal.aborted;
  const announce = () => changed(state);
  function merge(value) {
    const session = sessionFromResponse(value);
    if (!session?.id) return;
    const previous = state.details[session.id];
    if (previous?.session?.revision > session.revision) return;
    if (session.revision > state.pendingChat[session.id]?.revision) delete state.pendingChat[session.id];
    const snapshot = { ...(previous || {}), ...value, session };
    if (previous?.session?.currentCandidateId !== session.currentCandidateId) {
      for (const field of ['candidate', 'reviewSnapshot', 'testPlan', 'evidence']) if (!(field in value)) delete snapshot[field];
    }
    state.details[session.id] = snapshot;
    const at = state.items.findIndex(item => item.id === session.id);
    if (at < 0) state.items.unshift(session); else state.items.splice(at, 1, session);
    announce(); return session;
  }
  function schedule() {
    clearTimeout(timer);
    if (!disposed && state.owner) timer = setTimeout(async () => {
      try {
        await list();
        const ids = new Set(state.items.filter(authoringActive).map(item => item.id));
        for (const id of Object.keys(state.pendingChat)) ids.add(id);
        if (state.selectedId && state.open) ids.add(state.selectedId);
        await Promise.allSettled([...ids].map(id => read(id, false)));
      } finally { schedule(); }
    }, state.items.some(authoringActive) || Object.keys(state.pendingChat).length ? 1200 : 3500);
  }
  function reset(owner = '', conversationId = '') {
    epoch++; viewSequence++; listSequence++; controller.abort(); clearTimeout(timer); controller = new AbortController(); reads = new Map(); retryOperation = null;
    Object.assign(state, { items: [], details: {}, pendingChat: {}, selectedId: '', open: false, loading: false, busy: false, error: '', errorCode: '', pendingRetry: false, owner: String(owner || ''), conversationId: String(conversationId || ''), notice: '' });
    api = owner ? createAuthoringApi(owner, controller.signal, transport) : null;
    if (owner) { list(); schedule(); } announce();
  }
  function context(conversationId) { state.conversationId = String(conversationId || ''); list(); }
  async function list() {
    if (!api || disposed) return;
    const at = epoch, sequence = ++listSequence;
    try {
      const result = await api.list({ limit: 100 });
      if (!valid(at) || sequence !== listSequence) return;
      for (const session of result.items || []) merge({ session });
    } catch (error) { if (valid(at) && sequence === listSequence && error.name !== 'AbortError') { state.error = error.message; state.errorCode = error.code || ''; } }
  }
  async function read(id, show = true) {
    if (!api || !id) return;
    const at = epoch, sequence = (reads.get(id) || 0) + 1; reads.set(id, sequence);
    if (show) { viewSequence++; state.selectedId = id; state.open = true; state.loading = true; state.error = ''; }
    try {
      const result = await api.detail(id);
      if (!valid(at) || reads.get(id) !== sequence) return;
      const merged = merge(result);
      if (id === state.selectedId && result.session?.phase === 'committing' && api.pendingConfirmation(id)) { state.pendingRetry = true; retryOperation = client => client.retryConfirmation(id); }
      if (id === state.selectedId && result.session?.phase === 'completed') { state.pendingRetry = false; retryOperation = null; }
      return merged;
    } catch (error) { if (valid(at) && error.name !== 'AbortError' && (show || state.selectedId === id)) { state.error = error.message; state.errorCode = error.code || ''; if ([403,404].includes(error.status) && state.details[id]?.session) state.details[id].session.allowedActions = []; } }
    finally { if (valid(at) && show && state.selectedId === id) state.loading = false; }
  }
  async function mutate(operation) {
    if (!api || disposed || state.busy) return null;
    const at = epoch, view = viewSequence, client = api;
    state.busy = true; state.error = ''; state.errorCode = ''; state.notice = ''; state.pendingRetry = false;
    try {
      const result = await operation(client);
      if (!valid(at)) return null;
      retryOperation = null;
      const session = merge(result);
      if (session) { if (view === viewSequence) { state.selectedId = session.id; state.open = true; } await read(session.id, false); }
      schedule(); return result;
    } catch (error) {
      if (valid(at) && error.name !== 'AbortError') {
        state.error = error.message; state.errorCode = error.code || '';
        state.pendingRetry = !error.status || error.status >= 500 || ['OUTCOME_UNKNOWN', 'COMMIT_IN_PROGRESS'].includes(error.code);
        retryOperation = state.pendingRetry ? operation : null;
        if (state.selectedId) await read(state.selectedId, false);
      }
      return null;
    } finally { if (valid(at)) state.busy = false; }
  }
  function create(body) {
    const frozen = JSON.parse(JSON.stringify({ conversationId: state.conversationId || null, ...body }));
    if (!state.busy) {
      // A new create has no session yet. An older completed selection must not clear
      // this request's unknown-outcome retry when its detail is refreshed.
      viewSequence++; state.selectedId = ''; state.open = false;
    }
    return mutate(client => client.create(frozen));
  }
  function act(id, action, body) {
    if (state.pendingChat[id]) { state.error = '聊天中的修改正在同步，请收到新草稿后再操作。'; return Promise.resolve(null); }
    const frozen = JSON.parse(JSON.stringify(body)); return mutate(client => client.action(id, action, frozen));
  }
  function beginChatEdit(id) {
    const session = state.details[id]?.session;
    if (!session || state.pendingChat[id]) return null;
    const ticket = { id, epoch, revision: session.revision, token: crypto.randomUUID() };
    state.notice = '';
    state.pendingChat[id] = { revision: session.revision, token: ticket.token, runId: '' };
    schedule(); return ticket;
  }
  function chatSubmitted(ticket, run) {
    if (!ticket || !valid(ticket.epoch) || state.pendingChat[ticket.id]?.token !== ticket.token) return;
    if (run?.id) state.pendingChat[ticket.id].runId = run.id;
    observeChatRuns(run ? [run] : []); read(ticket.id, false);
  }
  function chatFailed(ticket, error) {
    if (!ticket || !valid(ticket.epoch) || state.pendingChat[ticket.id]?.token !== ticket.token) return;
    // A failed follow-up read must not unlock a message that was already accepted.
    if (!state.pendingChat[ticket.id].runId && error.status >= 400 && error.status < 500) delete state.pendingChat[ticket.id];
    read(ticket.id, false);
  }
  async function observeChatRuns(runs) {
    const at = epoch;
    for (const [id, pending] of Object.entries(state.pendingChat)) {
      const run = runs.find(item => item.id === pending.runId);
      if (!run || !['succeeded','partial','failed','cancelled','interrupted','withdrawn'].includes(run.status)) continue;
      const session = await read(id, false);
      if (valid(at) && session && state.pendingChat[id]?.token === pending.token) {
        delete state.pendingChat[id];
        if (session.revision === pending.revision) state.notice = '聊天任务已结束，草稿尚未接收新修改；请查看对话结果。';
      }
    }
  }
  function append(id, instruction, sourceRefs = [], runtimeBindings) {
    const session = state.details[id]?.session;
    if (!session || !authoringAllowed(session, 'messages')) return Promise.resolve(null);
    return act(id, 'messages', { revision: session.revision, instruction, ...(sourceRefs.length ? { sourceRefs } : {}), ...(runtimeBindings !== undefined ? { runtimeBindings } : {}) });
  }
  function editContent(id, candidateContent) {
    const session = state.details[id]?.session;
    if (!session || !authoringAllowed(session, 'messages')) return Promise.resolve(null);
    return act(id, 'messages', { revision: session.revision, candidateContent });
  }
  function answer(id, answer, sourceRefs = [], runtimeBindings) {
    const session = state.details[id]?.session;
    return act(id, 'answers', { revision: session.revision, questionId: session.question.id, answer, ...(sourceRefs.length ? { sourceRefs } : {}), ...(runtimeBindings !== undefined ? { runtimeBindings } : {}) });
  }
  function resume(id, mode = 'resume', acknowledgeUnknownRequests = []) {
    const detail = state.details[id], budget = authoringBudget(detail);
    return act(id, 'continue', { revision: detail.session.revision, mode, budgetRoundId: budget.roundId,
      ...(mode === 'extend_budget' ? { confirmationId: budget.confirmationId } : {}),
      ...(acknowledgeUnknownRequests.length ? { acknowledgeUnknownRequests } : {}) });
  }
  function confirm(id, mode) {
    try { return act(id, 'confirm', authoringConfirmation(state.details[id], mode)); }
    catch (error) { state.error = error.message; return Promise.resolve(null); }
  }
  function cancel(id) { return act(id, 'cancel', { revision: state.details[id].session.revision }); }
  function close() { viewSequence++; state.open = false; state.loading = false; }
  function ingest(value) {
    const session = sessionFromResponse(value), id = session?.id || value?.authoringSessionId;
    if (!id || !state.owner) return;
    if (session) merge({ session }); read(id, false);
  }
  function dispose() { disposed = true; epoch++; controller.abort(); clearTimeout(timer); }
  return { s: state, reset, context, list, read, create, append, editContent, answer, resume, confirm, cancel, close, ingest, dispose, beginChatEdit, chatSubmitted, chatFailed, observeChatRuns,
    retry: () => retryOperation ? mutate(retryOperation) : Promise.resolve(null) };
}

export function useAuthoringSessions(workbench, transport) {
  const store = createAuthoringStore(transport);
  watch(() => workbench.user?.id, owner => store.reset(owner, workbench.currentTaskId), { immediate: true });
  watch(() => workbench.currentTaskId, id => store.context(id));
  onUnmounted(store.dispose);
  return store;
}
