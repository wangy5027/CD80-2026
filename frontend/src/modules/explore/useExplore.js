import { computed, onMounted, onUnmounted, reactive, watch } from 'vue';
import * as api from './api.js';
import { activeRun, contextRequest, defaultExploreModel, exploreHash, feedbackRun, isEventInitial, isHistoryLiteRun, legacyExploreRun, maxContextFiles, normalizeContexts, parseExploreHash, reviewRun, runMaterials, runTree, terminalRun, validExploreModel } from './model.js';
import { isLiteChoiceReport, liteAngleRecommendations, pendingLiteChoice } from './reportPresentation.js';

const pendingKey = ownerId => `ficagent-explore-pending:${ownerId}`;
const historyKey = ownerId => `ficagent-explore-history:${ownerId}`;
const modelKey = ownerId => `ficagent-explore-model:${ownerId}`;
const pendingSignature = (kind, sessionId, body) => JSON.stringify([kind, String(sessionId), body]);

function initialHistoryOpen(ownerId) {
  try { return localStorage.getItem(historyKey(ownerId)) !== 'closed'; }
  catch { return true; }
}

function initialModel(ownerId) {
  try {
    const saved = localStorage.getItem(modelKey(ownerId));
    return validExploreModel(saved) ? saved : defaultExploreModel;
  } catch { return defaultExploreModel; }
}

export function useExplore(ownerId) {
  const state = reactive({
    sessions: [], session: null, runs: [], selectedRunId: '', module: 'event', model: initialModel(ownerId), draft: '', sourceReportId: '',
    loading: true, loadingSession: false, submitting: false, actionBusy: false, error: '', actionError: '',
    renameOpen: false, renameDraft: '', renameSessionId: '', deleteOpen: false, deleteSessionId: '', deleteSessionTitle: '', sessionActionError: '', historyOpen: initialHistoryOpen(ownerId), judgment: '',
    reviewQuestion: '', selectedSourceIds: [],
    files: [], filesLoading: false, fileDetail: null, fileLoading: false, fileBusy: false, fileError: '', fileNameDraft: '', fileDeleteOpen: false,
    contexts: normalizeContexts(), contextDrafts: normalizeContexts(), contextPreview: null, contextLoading: false, contextLoaded: true, contextBusy: false, contextError: '',
  });
  const selectedRun = computed(() => state.runs.find(run => String(run.id) === String(state.selectedRunId)) || null);
  const selectedReport = computed(() => selectedRun.value?.report || null);
  const sourceReport = computed(() => state.runs.find(run => String(run?.report?.id) === String(state.sourceReportId))?.report || null);
  const tree = computed(() => runTree(state.runs));
  const isActive = computed(() => activeRun(selectedRun.value));
  const currentContext = computed(() => state.contextDrafts[state.module] || { mode: 'auto', file_ids: [] });
  const contextDirty = computed(() => JSON.stringify(contextRequest(state.module, currentContext.value)) !== JSON.stringify(contextRequest(state.module, state.contexts[state.module])));
  const selectedContextFiles = computed(() => state.files.filter(file => currentContext.value.file_ids.includes(String(file.id))));
  const sessionClosed = computed(() => Boolean(state.session?.closed_at));
  const canAct = computed(() => !state.loading && !state.loadingSession && state.contextLoaded && !state.contextLoading && !state.submitting && !state.actionBusy && !state.contextBusy && !state.fileBusy && !isActive.value && !sessionClosed.value);
  const canStartRun = computed(() => canAct.value && selectedRun.value?.status !== 'awaiting_feedback' && !isLiteChoiceReport(selectedReport.value));
  const canChooseLiteRecommendation = computed(() => canAct.value && pendingLiteChoice(selectedRun.value) && liteAngleRecommendations(selectedReport.value).length === 3);
  const showComposer = computed(() => !sessionClosed.value && selectedRun.value?.status !== 'awaiting_feedback' &&
    !(isActive.value && (isEventInitial(selectedRun.value) || isHistoryLiteRun(selectedRun.value))) && !isLiteChoiceReport(selectedReport.value));
  const canSubmit = computed(() => Boolean(state.draft.trim()) && canStartRun.value);
  const requestControllers = new Set();
  let mounted = true;
  let navigationEpoch = 0;
  let navigationTargetSessionId = '';
  let sessionsEpoch = 0;
  let sessionActionEpoch = 0;
  let pollTimer = null;
  let pollEpoch = 0;
  let backgroundPollTimer = null;
  let backgroundPollEpoch = 0;
  let backgroundRunCursor = 0;
  let reviewDraftSignature = '';
  let pendingMemory = null;
  let previewEpoch = 0;
  let fileRequestEpoch = 0;
  let filesEpoch = 0;
  let activeFilePoll = null;

  function setHistoryOpen(open) {
    state.historyOpen = Boolean(open);
    try { localStorage.setItem(historyKey(ownerId), state.historyOpen ? 'open' : 'closed'); }
    catch { /* A blocked preference store must not block navigation. */ }
  }

  function setModel(model) {
    if (state.submitting || !validExploreModel(model)) return false;
    state.model = model;
    try { localStorage.setItem(modelKey(ownerId), model); }
    catch { /* A blocked preference store must not block research. */ }
    return true;
  }

  function resetSessionAction() {
    sessionActionEpoch += 1;
    state.renameOpen = false;
    state.renameSessionId = '';
    state.renameDraft = '';
    state.deleteOpen = false;
    state.deleteSessionId = '';
    state.deleteSessionTitle = '';
    state.sessionActionError = '';
  }
  function closeSessionAction() {
    if (state.actionBusy || (!state.renameOpen && !state.deleteOpen)) return false;
    resetSessionAction();
    return true;
  }
  function listedSession(session = state.session) {
    if (!session?.id) return null;
    const id = String(session.id);
    return (String(state.session?.id) === id ? state.session : null)
      || state.sessions.find(item => String(item.id) === id) || null;
  }
  function startRename(session = state.session) {
    const target = listedSession(session);
    if (!target || target.closed_at || state.actionBusy) return false;
    resetSessionAction();
    state.renameSessionId = String(target.id);
    state.renameDraft = String(target.title || '');
    state.renameOpen = true;
    return true;
  }
  function startDelete(session = state.session) {
    const target = listedSession(session);
    if (!target || target.closed_at || state.actionBusy) return false;
    resetSessionAction();
    state.deleteSessionId = String(target.id);
    state.deleteSessionTitle = String(target.title || '未命名探索');
    state.deleteOpen = true;
    return true;
  }

  async function refreshFiles(sessionId = state.session?.id, { quiet = false } = {}) {
    if (!sessionId) return;
    const navigation = navigationEpoch;
    const epoch = ++filesEpoch;
    if (!quiet) state.filesLoading = true;
    if (!quiet) state.fileError = '';
    try {
      const result = await call(signal => api.listSessionFiles(sessionId, signal));
      if (mounted && navigation === navigationEpoch && epoch === filesEpoch && String(state.session?.id) === String(sessionId)) {
        state.files = Array.isArray(result?.files) ? result.files : [];
      }
    } catch (error) {
      if (!quiet && mounted && navigation === navigationEpoch && epoch === filesEpoch && String(state.session?.id) === String(sessionId) && error.name !== 'AbortError') state.fileError = error.message;
    } finally { if (mounted && navigation === navigationEpoch && epoch === filesEpoch && String(state.session?.id) === String(sessionId) && !quiet) state.filesLoading = false; }
  }
  function pollFiles(sessionId) {
    if (!sessionId || state.filesLoading || state.fileBusy) return;
    const token = { sessionId: String(sessionId), navigation: navigationEpoch };
    if (activeFilePoll?.sessionId === token.sessionId && activeFilePoll.navigation === token.navigation) return;
    activeFilePoll = token;
    void refreshFiles(sessionId, { quiet: true }).finally(() => {
      if (activeFilePoll === token) activeFilePoll = null;
    });
  }
  async function loadContexts(sessionId = state.session?.id) {
    if (!sessionId) return;
    const navigation = navigationEpoch;
    state.contextLoading = true;
    state.contextLoaded = false;
    state.contextError = '';
    try {
      const result = await call(signal => api.getSessionContext(sessionId, {}, signal));
      if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId)) {
        state.contexts = normalizeContexts(result?.contexts);
        state.contextDrafts = normalizeContexts(result?.contexts);
        state.contextLoaded = true;
      }
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId) && error.name !== 'AbortError') state.contextError = error.message;
    } finally { if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId)) state.contextLoading = false; }
  }
  async function refreshContextPreview() {
    const sessionId = state.session?.id;
    const module = state.module;
    const sourceReportId = state.sourceReportId;
    const epoch = ++previewEpoch;
    if (!sessionId || currentContext.value.mode !== 'auto') { state.contextPreview = null; return; }
    state.contextPreview = null;
    try {
      const result = await call(signal => api.getSessionContext(sessionId, { module, source_report_id: sourceReportId }, signal));
      if (mounted && epoch === previewEpoch && String(state.session?.id) === String(sessionId) && state.module === module && String(state.sourceReportId) === String(sourceReportId)) state.contextPreview = result?.context || null;
    } catch { if (mounted && epoch === previewEpoch) state.contextPreview = null; }
  }
  function setContextMode(mode) {
    if (sessionClosed.value || !['auto', 'manual'].includes(mode) || state.contextBusy || state.contextLoading || !state.contextLoaded || state.submitting) return;
    const current = currentContext.value;
    state.contextDrafts[state.module] = { mode, file_ids: mode === 'manual' ? [...current.file_ids] : [] };
    state.contextError = '';
  }
  function toggleContextFile(fileId, checked) {
    if (sessionClosed.value || currentContext.value.mode !== 'manual' || state.contextBusy || state.contextLoading || !state.contextLoaded || state.fileBusy || state.submitting) return;
    const id = String(fileId);
    if (!state.files.some(file => String(file.id) === id)) return;
    const ids = currentContext.value.file_ids.filter(item => item !== id);
    if (checked && ids.length >= maxContextFiles) { state.contextError = `每个模块最多选择 ${maxContextFiles} 个文件。`; return; }
    state.contextDrafts[state.module] = { mode: 'manual', file_ids: checked ? [...ids, id] : ids };
    state.contextError = '';
  }
  async function saveContext() {
    const sessionId = state.session?.id;
    if (!sessionId || sessionClosed.value || state.contextBusy || state.fileBusy || state.submitting || state.loadingSession || !state.contextLoaded) return false;
    const navigation = navigationEpoch;
    const body = contextRequest(state.module, currentContext.value);
    state.contextBusy = true;
    state.contextError = '';
    try {
      const result = await call(signal => api.saveSessionContext(sessionId, body, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId)) return;
      state.contexts[body.module] = normalizeContexts({ [body.module]: result?.context || body })[body.module];
      return true;
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId) && error.name !== 'AbortError') state.contextError = error.message;
      return false;
    } finally { if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId)) state.contextBusy = false; }
  }
  async function openFile(fileId) {
    const sessionId = state.session?.id;
    if (!sessionId) return;
    const navigation = navigationEpoch;
    const fileEpoch = ++fileRequestEpoch;
    state.fileLoading = true;
    state.fileError = '';
    state.fileDetail = null;
    state.fileDeleteOpen = false;
    try {
      const result = await call(signal => api.getSessionFile(sessionId, fileId, signal));
      if (!mounted || navigation !== navigationEpoch || fileEpoch !== fileRequestEpoch || String(state.session?.id) !== String(sessionId)) return;
      state.fileDetail = result?.file || null;
      state.fileNameDraft = state.fileDetail?.name || '';
    } catch (error) {
      if (mounted && navigation === navigationEpoch && fileEpoch === fileRequestEpoch && String(state.session?.id) === String(sessionId) && error.name !== 'AbortError') state.fileError = error.message;
    } finally { if (mounted && navigation === navigationEpoch && fileEpoch === fileRequestEpoch && String(state.session?.id) === String(sessionId)) state.fileLoading = false; }
  }
  function clearFileDetail() {
    fileRequestEpoch += 1;
    state.fileDetail = null;
    state.fileLoading = false;
    state.fileDeleteOpen = false;
  }
  async function renameFile() {
    const file = state.fileDetail;
    const sessionId = state.session?.id;
    const name = state.fileNameDraft.trim();
    if (!sessionId || sessionClosed.value || !file?.id || !name || state.fileBusy) return;
    const navigation = navigationEpoch;
    filesEpoch += 1;
    state.filesLoading = false;
    state.fileBusy = true;
    state.fileError = '';
    try {
      const result = await call(signal => api.renameSessionFile(sessionId, file.id, name, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId) || String(state.fileDetail?.id) !== String(file.id)) return;
      state.fileDetail = { ...state.fileDetail, ...(result?.file || {}), content: state.fileDetail.content };
      state.files = state.files.map(item => String(item.id) === String(file.id) ? { ...item, ...(result?.file || {}) } : item);
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId) && String(state.fileDetail?.id) === String(file.id) && error.name !== 'AbortError') state.fileError = error.message;
    } finally { if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId)) state.fileBusy = false; }
  }
  async function removeFile() {
    const file = state.fileDetail;
    const sessionId = state.session?.id;
    if (!sessionId || sessionClosed.value || !file?.id || state.fileBusy) return;
    const navigation = navigationEpoch;
    const unsaved = Object.fromEntries(['event', 'relation', 'history'].filter(module =>
      JSON.stringify(contextRequest(module, state.contextDrafts[module])) !== JSON.stringify(contextRequest(module, state.contexts[module]))
    ).map(module => [module, contextRequest(module, state.contextDrafts[module])]));
    filesEpoch += 1;
    state.filesLoading = false;
    state.fileBusy = true;
    state.fileError = '';
    try {
      await call(signal => api.removeSessionFile(sessionId, file.id, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId)) return;
      state.fileDetail = null;
      state.fileDeleteOpen = false;
      await Promise.all([refreshFiles(sessionId), loadContexts(sessionId)]);
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId) || !state.contextLoaded) return;
      for (const [module, draft] of Object.entries(unsaved)) {
        state.contextDrafts[module] = normalizeContexts({ [module]: { ...draft, file_ids: draft.file_ids.filter(id => id !== String(file.id)) } })[module];
      }
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId) && error.name !== 'AbortError') state.fileError = error.message;
    } finally { if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId)) state.fileBusy = false; }
  }
  async function downloadFile(file = state.fileDetail) {
    const sessionId = state.session?.id;
    if (!sessionId || !file?.id) return;
    const navigation = navigationEpoch;
    state.fileError = '';
    try { await call(signal => api.downloadSessionFile(sessionId, file.id, file.name, signal)); }
    catch (error) { if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId) && error.name !== 'AbortError') state.fileError = error.message; }
  }

  function syncReviewDraft(run, force = false) {
    const signature = reviewRun(run) ? `${run.id}:${run.status}:${run.review?.revision ?? ''}` : '';
    if (!force && signature === reviewDraftSignature) return;
    reviewDraftSignature = signature;
    state.reviewQuestion = signature && run?.status === 'awaiting_review' ? String(run.review?.interpreted_question || run.question || '') : '';
    state.selectedSourceIds = run?.status === 'awaiting_review' ? runMaterials(run).map(item => String(item.id)) : [];
  }

  function pendingRead() {
    if (pendingMemory) return pendingMemory;
    try {
      const saved = JSON.parse(sessionStorage.getItem(pendingKey(ownerId)) || 'null');
      if (saved?.signature && saved?.key) pendingMemory = { [saved.signature]: saved.key };
      else pendingMemory = saved?.entries && typeof saved.entries === 'object' && !Array.isArray(saved.entries) ? saved.entries : {};
    } catch { pendingMemory = {}; }
    return pendingMemory;
  }
  function pendingWrite(entries) {
    pendingMemory = entries;
    try { sessionStorage.setItem(pendingKey(ownerId), JSON.stringify({ entries })); } catch { /* storage unavailable */ }
  }
  function pendingClear(kind, sessionId, body) {
    const entries = pendingRead();
    delete entries[pendingSignature(kind, sessionId, body)];
    try {
      if (Object.keys(entries).length) pendingWrite(entries);
      else sessionStorage.removeItem(pendingKey(ownerId));
    } catch { /* storage unavailable */ }
  }
  function keyFor(kind, sessionId, body) {
    const signature = pendingSignature(kind, sessionId, body);
    const entries = pendingRead();
    if (entries[signature]) return entries[signature];
    const key = api.newIdempotencyKey();
    entries[signature] = key;
    pendingWrite(entries);
    return key;
  }
  async function call(fn) {
    const controller = new AbortController();
    requestControllers.add(controller);
    try { return await fn(controller.signal); }
    finally { requestControllers.delete(controller); }
  }
  function writeHash(sessionId = '', runId = '', mode = 'push') {
    const hash = exploreHash(sessionId, runId);
    if (location.hash === hash) return;
    const url = location.pathname + location.search + hash;
    if (mode === 'replace') history.replaceState(null, '', url);
    else history.pushState(null, '', url);
  }
  function stopPolling() {
    pollEpoch += 1;
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
  }
  function stopBackgroundWatch() {
    backgroundPollEpoch += 1;
    if (backgroundPollTimer) clearTimeout(backgroundPollTimer);
    backgroundPollTimer = null;
  }
  function scheduleBackgroundWatch(delay = 2000) {
    stopBackgroundWatch();
    if (!mounted || !state.session?.id || activeRun(selectedRun.value)) return;
    const running = state.runs.filter(activeRun);
    if (!running.length) return;
    const epoch = backgroundPollEpoch;
    const navigation = navigationEpoch;
    const sessionId = String(state.session.id);
    const runId = String(running[backgroundRunCursor++ % running.length].id);
    backgroundPollTimer = setTimeout(async () => {
      if (!mounted || epoch !== backgroundPollEpoch || navigation !== navigationEpoch || String(state.session?.id) !== sessionId || activeRun(selectedRun.value)) return;
      try {
        const result = await call(signal => api.getRun(runId, signal));
        if (!mounted || epoch !== backgroundPollEpoch || navigation !== navigationEpoch || String(state.session?.id) !== sessionId || activeRun(selectedRun.value)) return;
        upsertRun(result?.run);
        if (terminalRun(result?.run) || reviewRun(result?.run)) refreshFiles(sessionId);
        else pollFiles(sessionId);
        scheduleBackgroundWatch();
      } catch (error) {
        if (error.name !== 'AbortError' && mounted && epoch === backgroundPollEpoch) scheduleBackgroundWatch(4000);
      }
    }, delay);
  }
  function upsertRun(run) {
    if (!run?.id) return;
    const index = state.runs.findIndex(item => String(item.id) === String(run.id));
    const previous = index < 0 ? null : state.runs[index];
    if (index < 0) state.runs.push(run);
    else state.runs.splice(index, 1, run);
    if (String(state.selectedRunId) === String(run.id)) {
      // A later terminal record supersedes a transient status-poll error.
      if (terminalRun(run) && state.actionError.startsWith('状态更新失败：')) state.actionError = '';
      const originalSourceId = previous?.source_report_id ?? run.source_report_id ?? '';
      if (run.report?.id && !previous?.report?.id && String(state.sourceReportId || '') === String(originalSourceId)) {
        state.sourceReportId = run.report.id;
      }
      if (run.status === 'awaiting_input') state.judgment = run.proposed_judgment || run.question || '';
      syncReviewDraft(run);
    }
  }
  function schedulePoll(runId) {
    stopPolling();
    if (!activeRun(selectedRun.value) || String(selectedRun.value?.id) !== String(runId)) return;
    const epoch = pollEpoch;
    pollTimer = setTimeout(async () => {
      if (!mounted || epoch !== pollEpoch || String(state.selectedRunId) !== String(runId)) return;
      try {
        const result = await call(signal => api.getRun(runId, signal));
        if (!mounted || epoch !== pollEpoch || String(state.selectedRunId) !== String(runId)) return;
        upsertRun(result?.run);
        if (terminalRun(result?.run)) {
          refreshSessions();
          refreshFiles(state.session?.id);
          scheduleBackgroundWatch();
        } else if (reviewRun(result?.run)) {
          refreshFiles(state.session?.id);
          scheduleBackgroundWatch();
        } else if (activeRun(result?.run)) {
          pollFiles(state.session?.id);
          schedulePoll(runId);
        } else {
          refreshFiles(state.session?.id);
          scheduleBackgroundWatch();
        }
      } catch (error) {
        if (error.name !== 'AbortError' && mounted && epoch === pollEpoch) {
          state.actionError = `状态更新失败：${error.message}。可刷新页面重新读取，分析不会自动重跑。`;
          pollTimer = setTimeout(() => schedulePoll(runId), 4000);
        }
      }
    }, 1600);
  }
  function selectRun(runId, routeMode = 'push') {
    if (state.renameOpen || state.deleteOpen) resetSessionAction();
    stopPolling();
    stopBackgroundWatch();
    const chosen = state.runs.find(run => String(run.id) === String(runId)) || state.runs.at(-1);
    state.selectedRunId = String(chosen?.id || '');
    const run = selectedRun.value;
    if (['event', 'relation', 'history'].includes(run?.module)) state.module = run.module;
    state.sourceReportId = run?.report?.id || (run?.memory_output ? '' : run?.source_report_id) || '';
    state.judgment = run?.status === 'awaiting_input' ? run.proposed_judgment || run.question || '' : '';
    syncReviewDraft(run, true);
    state.actionError = '';
    if (state.session?.id) writeHash(state.session.id, state.selectedRunId, routeMode);
    if (activeRun(run)) schedulePoll(run.id);
    else scheduleBackgroundWatch();
  }
  async function refreshSessions() {
    const navigation = navigationEpoch;
    const epoch = ++sessionsEpoch;
    try {
      const result = await call(signal => api.listSessions(signal));
      if (mounted && epoch === sessionsEpoch) state.sessions = (Array.isArray(result?.sessions) ? result.sessions : []).map(item =>
        String(item.id) === String(state.session?.id) && state.session?.closed_at ? { ...item, closed_at: state.session.closed_at } : item);
    } catch (error) {
      if (mounted && navigation === navigationEpoch && epoch === sessionsEpoch && error.name !== 'AbortError') state.error = error.message;
    }
  }
  async function openSession(sessionId, runId = '', routeMode = 'push') {
    if (!sessionId) { newExplore(routeMode); return; }
    const epoch = ++navigationEpoch;
    navigationTargetSessionId = String(sessionId);
    stopPolling();
    stopBackgroundWatch();
    state.loadingSession = true;
    state.contextLoaded = false;
    state.error = '';
    state.actionError = '';
    resetSessionAction();
    try {
      const result = await call(signal => api.getSession(sessionId, signal));
      if (!mounted || epoch !== navigationEpoch) return;
      state.session = result?.session || null;
      state.runs = Array.isArray(result?.runs) ? result.runs : [];
      const chosen = state.runs.find(run => String(run.id) === String(runId)) || state.runs.at(-1);
      state.selectedRunId = '';
      selectRun(chosen?.id || '', routeMode);
      if (!state.session) throw new Error('探索会话不存在或无权访问。');
      await Promise.all([refreshFiles(sessionId), loadContexts(sessionId)]);
    } catch (error) {
      if (!mounted || epoch !== navigationEpoch || error.name === 'AbortError') return;
      state.session = null;
      state.runs = [];
      state.selectedRunId = '';
      state.sourceReportId = '';
      state.files = [];
      state.fileDetail = null;
      state.contexts = normalizeContexts();
      state.contextDrafts = normalizeContexts();
      state.contextPreview = null;
      state.contextLoaded = false;
      state.error = error.message;
    } finally { if (mounted && epoch === navigationEpoch) state.loadingSession = false; }
  }
  function newExplore(routeMode = 'push') {
    navigationEpoch += 1;
    navigationTargetSessionId = '';
    stopPolling();
    stopBackgroundWatch();
    state.loading = false;
    state.loadingSession = false;
    state.contextLoading = false;
    state.contextBusy = false;
    state.fileLoading = false;
    state.fileBusy = false;
    state.session = null;
    state.runs = [];
    state.selectedRunId = '';
    state.sourceReportId = '';
    state.draft = '';
    state.module = 'event';
    state.error = '';
    state.actionError = '';
    resetSessionAction();
    state.judgment = '';
    state.reviewQuestion = '';
    state.selectedSourceIds = [];
    reviewDraftSignature = '';
    state.files = [];
    state.filesLoading = false;
    state.fileDetail = null;
    state.fileError = '';
    state.contexts = normalizeContexts();
    state.contextDrafts = normalizeContexts();
    state.contextPreview = null;
    state.contextLoaded = true;
    state.contextError = '';
    previewEpoch += 1;
    fileRequestEpoch += 1;
    writeHash('', '', routeMode);
  }
  function newFlashFromLegacy() {
    const run = selectedRun.value;
    if (!legacyExploreRun(run)) return false;
    const question = String(run.question || '');
    const module = ['event', 'relation', 'history'].includes(run.module) ? run.module : 'event';
    newExplore();
    state.model = defaultExploreModel;
    state.module = module;
    state.draft = question;
    return true;
  }
  function onLocationChange() {
    const route = parseExploreHash(location.hash);
    if (!route.isExplore || !mounted) return;
    if (!route.sessionId) {
      if (state.session) newExplore('replace');
      return;
    }
    if (String(route.sessionId) !== String(state.session?.id)) openSession(route.sessionId, route.runId, 'replace');
    else if (String(route.runId || '') !== String(state.selectedRunId)) selectRun(route.runId, 'replace');
  }
  async function initialize() {
    state.loading = true;
    await refreshSessions();
    if (!mounted) return;
    const route = parseExploreHash(location.hash);
    if (route.sessionId) await openSession(route.sessionId, route.runId, 'replace');
    state.loading = false;
  }
  function useSuggestion(item, sourceId = '') {
    if (!item?.question || sessionClosed.value || !showComposer.value) return;
    state.draft = String(item.question);
    state.module = ['event', 'relation', 'history'].includes(item.module) ? item.module : state.module;
    if (sourceId) state.sourceReportId = sourceId;
  }
  async function createAnalysis({ question, module, sourceReportId }, { clearDraft = false, choiceRunId = '' } = {}) {
    if (!(choiceRunId ? canChooseLiteRecommendation.value : canStartRun.value) || !question || !['event', 'relation', 'history'].includes(module)) return false;
    // Capture the next-run choice before createSession or any other awaited work.
    const model = validExploreModel(state.model) ? state.model : defaultExploreModel;
    const navigation = navigationEpoch;
    const selection = state.selectedRunId;
    state.submitting = true;
    state.actionError = '';
    try {
      let sessionId = state.session?.id;
      if (!sessionId) {
        const result = await call(signal => api.createSession(question.slice(0, 80), signal));
        if (!mounted || navigation !== navigationEpoch) return;
        state.session = result?.session;
        sessionId = state.session?.id;
        if (!sessionId) throw new Error('服务端未返回探索会话。');
        state.runs = [];
        writeHash(sessionId, '', 'replace');
        await refreshSessions();
        if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId)) return false;
      }
      const context = contextRequest(module, state.contextDrafts[module]);
      const body = { module, question, source_report_id: sourceReportId, context_mode: context.mode, file_ids: context.file_ids, model };
      // The first event explanation can continue before a formal report exists.
      if (!selectedRun.value?.report && selectedRun.value?.memory_output?.id &&
          (!sourceReportId || String(sourceReportId) === String(selectedRun.value.source_report_id || '')) &&
          String(selectedRun.value.session_id) === String(sessionId)) {
        body.source_report_id = null;
        body.source_run_id = selectedRun.value.id;
      }
      const key = keyFor('create', sessionId, body);
      const result = await call(signal => api.createRun(sessionId, { ...body, idempotency_key: key }, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId)) return;
      if (!result?.run?.id) throw new Error('服务端未返回分析记录。');
      pendingClear('create', sessionId, body);
      state.contexts[module] = { mode: context.mode, file_ids: [...context.file_ids] };
      upsertRun(result.run);
      if (choiceRunId) {
        const parent = state.runs.find(run => String(run.id) === String(choiceRunId));
        if (parent) upsertRun({ ...parent, next_step_state: 'continued' });
      }
      refreshFiles(sessionId);
      if (clearDraft && state.draft.trim() === question) state.draft = '';
      if (String(state.selectedRunId) === String(selection)) selectRun(result.run.id);
      else scheduleBackgroundWatch();
      refreshSessions();
      if (choiceRunId) {
        try {
          const refreshed = await call(signal => api.getRun(choiceRunId, signal));
          if (mounted && navigation === navigationEpoch && String(state.session?.id) === String(sessionId) && refreshed?.run?.id) {
            const previous = state.runs.find(run => String(run.id) === String(choiceRunId));
            upsertRun({ ...previous, ...refreshed.run, report: refreshed.run.report || previous?.report });
          }
        } catch { /* The successful branch stays visible; the parent is already marked continued locally. */ }
      }
      return true;
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(selection) && error.name !== 'AbortError') {
        state.actionError = clearDraft
          ? `${error.message}。输入已保留；再次提交会沿用同一请求标识（模型选择不变时）。`
          : `${error.message}。再次点击该推荐会沿用同一请求标识（模型选择不变时）；原输入已保留。`;
      }
      return false;
    } finally { if (mounted) state.submitting = false; }
  }
  async function submit() {
    if (!canSubmit.value) return false;
    return createAnalysis({ question: state.draft.trim(), module: state.module, sourceReportId: state.sourceReportId || null }, { clearDraft: true });
  }
  async function runRecommendation(item, sourceId = selectedReport.value?.id) {
    const question = String(item?.question || '').trim();
    const module = item?.module;
    const sourceReportId = String(sourceId || '');
    if (!question || !['event', 'relation', 'history'].includes(module) || !sourceReportId || String(selectedReport.value?.id) !== sourceReportId) return false;
    if (isLiteChoiceReport(selectedReport.value)) {
      if (!canChooseLiteRecommendation.value || !liteAngleRecommendations(selectedReport.value).some(row => row.module === module && row.question === item.question)) return false;
      return createAnalysis({ question, module, sourceReportId }, { choiceRunId: selectedRun.value.id });
    }
    return createAnalysis({ question, module, sourceReportId });
  }
  async function confirmJudgment() {
    const run = selectedRun.value;
    if (sessionClosed.value || run?.status !== 'awaiting_input' || !state.judgment.trim() || state.actionBusy) return;
    const navigation = navigationEpoch;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const result = await call(signal => api.provideJudgment(run.id, state.judgment.trim(), signal));
      if (!mounted || navigation !== navigationEpoch || String(state.selectedRunId) !== String(run.id)) return;
      upsertRun(result?.run);
      if (activeRun(result?.run)) schedulePoll(run.id);
    } catch (error) { if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id) && error.name !== 'AbortError') state.actionError = error.message; }
    finally { if (mounted) state.actionBusy = false; }
  }
  async function chooseFeedback(choice) {
    const run = selectedRun.value;
    if (sessionClosed.value || !feedbackRun(run) || !['understood', 'explain_more'].includes(choice) || state.actionBusy) return false;
    const navigation = navigationEpoch;
    const body = { choice };
    state.actionBusy = true;
    state.actionError = '';
    try {
      const key = keyFor('feedback', run.id, body);
      const result = await call(signal => api.submitFeedback(run.id, { ...body, idempotency_key: key }, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.selectedRunId) !== String(run.id)) return false;
      if (!result?.run?.id) throw new Error('服务端未返回更新后的解释记录。');
      pendingClear('feedback', run.id, body);
      upsertRun(result.run);
      if (activeRun(result.run)) schedulePoll(run.id);
      else if (terminalRun(result.run)) {
        refreshSessions();
        refreshFiles(run.session_id);
        scheduleBackgroundWatch();
      }
      return true;
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id) && error.name !== 'AbortError') state.actionError = `${error.message}。再次点击同一选项会沿用本次请求。`;
      return false;
    } finally { if (mounted) state.actionBusy = false; }
  }
  async function finishAnalysis() {
    const run = selectedRun.value;
    if (sessionClosed.value || !pendingLiteChoice(run) || state.actionBusy || state.submitting) return false;
    const navigation = navigationEpoch;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const key = keyFor('finish', run.id, {});
      const result = await call(signal => api.finishRun(run.id, key, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(run.session_id)) return false;
      if (!result?.run?.id || String(result.run.id) !== String(run.id) || !result?.session?.closed_at) throw new Error('服务端未确认会话已结束。');
      pendingClear('finish', run.id, {});
      upsertRun(result.run);
      state.session = { ...state.session, ...result.session };
      state.sessions = state.sessions.map(item => String(item.id) === String(state.session.id) ? { ...item, ...result.session } : item);
      state.fileDeleteOpen = false;
      state.contextDrafts = normalizeContexts(state.contexts);
      refreshSessions();
      return true;
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id) && error.name !== 'AbortError') state.actionError = `${error.message}。再次点击“OK，结束”会沿用本次请求。`;
      return false;
    } finally { if (mounted) state.actionBusy = false; }
  }
  function toggleMaterial(id, checked) {
    const run = selectedRun.value;
    if (sessionClosed.value || run?.status !== 'awaiting_review') return;
    const value = String(id);
    if (!runMaterials(run).some(item => String(item.id) === value)) return;
    if (checked && !state.selectedSourceIds.includes(value)) state.selectedSourceIds = [...state.selectedSourceIds, value];
    if (!checked) state.selectedSourceIds = state.selectedSourceIds.filter(item => item !== value);
  }
  async function clarify(optionId = '') {
    const run = selectedRun.value;
    const question = state.reviewQuestion.trim();
    if (sessionClosed.value || !reviewRun(run) || !run.review || state.actionBusy) return;
    const option = optionId && run.status === 'awaiting_clarification'
      ? (run.review.options || []).find(item => String(item.id) === String(optionId)) : null;
    if (optionId && !option) return;
    if (!option && !question) return;
    const navigation = navigationEpoch;
    const revision = run.review.revision;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const body = option ? { revision, option_id: String(option.id) } : { revision, question };
      const key = keyFor('clarify', run.id, body);
      const result = await call(signal => api.clarifyRun(run.id, { ...body, idempotency_key: key }, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.selectedRunId) !== String(run.id)) return;
      if (!result?.run?.id) throw new Error('服务端未返回更新后的研究记录。');
      pendingClear('clarify', run.id, body);
      upsertRun(result.run);
      if (terminalRun(result.run) || reviewRun(result.run)) refreshFiles(state.session?.id);
      if (activeRun(result.run)) schedulePoll(run.id);
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id) && error.name !== 'AbortError') {
        state.actionError = `${error.message}。问题已保留；再次提交会沿用同一请求标识。`;
      }
    } finally { if (mounted) state.actionBusy = false; }
  }
  async function confirmMaterials() {
    const run = selectedRun.value;
    if (sessionClosed.value || run?.status !== 'awaiting_review' || !run.review || state.actionBusy) return;
    const sourceIds = runMaterials(run).map(item => String(item.id)).filter(id => state.selectedSourceIds.includes(id));
    if (!sourceIds.length) return;
    const navigation = navigationEpoch;
    const revision = run.review.revision;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const body = { revision, source_ids: sourceIds };
      const key = keyFor('confirm', run.id, body);
      const result = await call(signal => api.confirmRun(run.id, { ...body, idempotency_key: key }, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.selectedRunId) !== String(run.id)) return;
      if (!result?.run?.id) throw new Error('服务端未返回更新后的研究记录。');
      pendingClear('confirm', run.id, body);
      upsertRun(result.run);
      if (activeRun(result.run)) schedulePoll(run.id);
    } catch (error) {
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id) && error.name !== 'AbortError') {
        state.actionError = `${error.message}。材料选择已保留；再次提交会沿用同一请求标识。`;
      }
    } finally { if (mounted) state.actionBusy = false; }
  }
  async function cancel() {
    const run = selectedRun.value;
    if (sessionClosed.value || !run || (!activeRun(run) && !feedbackRun(run) && !reviewRun(run) && run.status !== 'awaiting_input') || state.actionBusy) return;
    const navigation = navigationEpoch;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const result = await call(signal => api.cancelRun(run.id, signal));
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id)) {
        upsertRun(result?.run);
        refreshFiles(run.session_id);
      }
      if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id)) stopPolling();
      if (mounted && navigation === navigationEpoch) scheduleBackgroundWatch();
    } catch (error) { if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(run.id) && error.name !== 'AbortError') state.actionError = error.message; }
    finally { if (mounted) state.actionBusy = false; }
  }
  async function retry() {
    const run = selectedRun.value;
    if (sessionClosed.value || !run || !['failed', 'cancelled', 'interrupted'].includes(run.status) || state.actionBusy) return;
    if (legacyExploreRun(run)) {
      state.actionError = '旧研究的模型已停用或未记录，无法安全重试；请新建 Flash 研究。';
      return false;
    }
    const navigation = navigationEpoch;
    const selection = state.selectedRunId;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const key = keyFor('retry', run.id, {});
      const result = await call(signal => api.retryRun(run.id, key, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(run.session_id)) return;
      if (!result?.run?.id) throw new Error('服务端未返回重试记录。');
      pendingClear('retry', run.id, {});
      upsertRun(result?.run);
      if (terminalRun(result?.run) || reviewRun(result?.run)) refreshFiles(run.session_id);
      if (String(state.selectedRunId) === String(selection)) selectRun(result?.run?.id);
      else scheduleBackgroundWatch();
    } catch (error) { if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(selection) && error.name !== 'AbortError') state.actionError = `${error.message}。再次重试会沿用同一请求标识。`; }
    finally { if (mounted) state.actionBusy = false; }
  }
  async function regenerate() {
    const run = selectedRun.value;
    if (sessionClosed.value || !run || isLiteChoiceReport(run.report) || !terminalRun(run) || !state.session?.id || state.actionBusy) return;
    if (legacyExploreRun(run)) {
      state.actionError = '旧研究的冻结模型不能安全沿用；请新建 Flash 研究。';
      return false;
    }
    const sessionId = state.session.id;
    const navigation = navigationEpoch;
    const selection = state.selectedRunId;
    state.actionBusy = true;
    state.actionError = '';
    try {
      const context = contextRequest(run.module, state.contextDrafts[run.module]);
      const body = { module: run.module, question: run.question, source_report_id: run.source_report_id || null, context_mode: context.mode, file_ids: context.file_ids };
      // A Flash regeneration remains a new, explicitly frozen Flash run.
      body.model = defaultExploreModel;
      const signatureBody = { ...body, previous_run_id: run.id };
      const key = keyFor('regenerate', sessionId, signatureBody);
      const result = await call(signal => api.createRun(sessionId, { ...body, idempotency_key: key }, signal));
      if (!mounted || navigation !== navigationEpoch || String(state.session?.id) !== String(sessionId)) return;
      if (!result?.run?.id) throw new Error('服务端未返回重新生成的记录。');
      pendingClear('regenerate', sessionId, signatureBody);
      state.contexts[run.module] = { mode: context.mode, file_ids: [...context.file_ids] };
      upsertRun(result?.run);
      if (terminalRun(result?.run) || reviewRun(result?.run)) refreshFiles(sessionId);
      if (String(state.selectedRunId) === String(selection)) selectRun(result?.run?.id);
      else scheduleBackgroundWatch();
    } catch (error) { if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(selection) && error.name !== 'AbortError') state.actionError = `${error.message}。再次生成会沿用同一请求标识。`; }
    finally { if (mounted) state.actionBusy = false; }
  }
  async function rename() {
    const sessionId = state.renameSessionId;
    if (!state.renameOpen || !sessionId || listedSession({ id: sessionId })?.closed_at || state.actionBusy) return false;
    const title = String(state.renameDraft || '').trim();
    if (!title || Array.from(title).length > 120) {
      state.sessionActionError = !title ? '请输入会话名称。' : '会话名称最多 120 个字。';
      return false;
    }
    const action = sessionActionEpoch;
    state.actionBusy = true;
    state.sessionActionError = '';
    try {
      const result = await call(signal => api.renameSession(sessionId, title, signal));
      if (!mounted) return false;
      const updated = result?.session?.id ? result.session : { id: sessionId, title };
      sessionsEpoch += 1;
      state.sessions = state.sessions.map(item => String(item.id) === String(sessionId) ? { ...item, ...updated } : item);
      if (String(state.session?.id) === String(sessionId)) state.session = { ...state.session, ...updated };
      if (action === sessionActionEpoch && state.renameSessionId === sessionId) resetSessionAction();
      void refreshSessions();
      return true;
    } catch (error) {
      if (mounted && action === sessionActionEpoch && state.renameOpen && state.renameSessionId === sessionId && error.name !== 'AbortError') state.sessionActionError = error.message;
      return false;
    }
    finally { if (mounted) state.actionBusy = false; }
  }
  async function removeSession() {
    const sessionId = state.deleteSessionId;
    if (!state.deleteOpen || !sessionId || listedSession({ id: sessionId })?.closed_at || state.actionBusy) return false;
    const action = sessionActionEpoch;
    const navigation = navigationEpoch;
    state.actionBusy = true;
    state.sessionActionError = '';
    try {
      await call(signal => api.deleteSession(sessionId, signal));
      if (!mounted) return false;
      sessionsEpoch += 1;
      state.sessions = state.sessions.filter(item => String(item.id) !== String(sessionId));
      if (navigationTargetSessionId === String(sessionId) || (navigation === navigationEpoch && String(state.session?.id) === String(sessionId))) newExplore('replace');
      else if (action === sessionActionEpoch && state.deleteSessionId === sessionId) resetSessionAction();
      void refreshSessions();
      return true;
    } catch (error) {
      if (mounted && action === sessionActionEpoch && state.deleteOpen && state.deleteSessionId === sessionId && error.name !== 'AbortError') state.sessionActionError = error.message;
      return false;
    }
    finally { if (mounted) state.actionBusy = false; }
  }
  async function download() {
    if (!selectedReport.value?.id || state.actionBusy) return;
    const reportId = selectedReport.value.id;
    const navigation = navigationEpoch;
    const selection = state.selectedRunId;
    state.actionBusy = true;
    state.actionError = '';
    try { await call(signal => api.downloadReport(reportId, signal)); }
    catch (error) { if (mounted && navigation === navigationEpoch && String(state.selectedRunId) === String(selection) && error.name !== 'AbortError') state.actionError = error.message; }
    finally { if (mounted) state.actionBusy = false; }
  }
  function continueFromSelected() {
    if (sessionClosed.value || isLiteChoiceReport(selectedReport.value) || !selectedReport.value?.id) return;
    state.sourceReportId = selectedReport.value.id;
    state.draft = '';
  }

  onMounted(() => {
    mounted = true;
    window.addEventListener('popstate', onLocationChange);
    window.addEventListener('hashchange', onLocationChange);
    initialize();
  });
  onUnmounted(() => {
    mounted = false;
    resetSessionAction();
    stopPolling();
    stopBackgroundWatch();
    navigationEpoch += 1;
    previewEpoch += 1;
    fileRequestEpoch += 1;
    for (const controller of requestControllers) controller.abort();
    requestControllers.clear();
    window.removeEventListener('popstate', onLocationChange);
    window.removeEventListener('hashchange', onLocationChange);
  });
  watch(() => [state.session?.id, state.module, state.sourceReportId, currentContext.value.mode], refreshContextPreview);
  return { state, selectedRun, selectedReport, sourceReport, tree, isActive, sessionClosed, canStartRun, canChooseLiteRecommendation,
    canChooseEventRecommendation: canChooseLiteRecommendation, showComposer, canSubmit, currentContext, contextDirty, selectedContextFiles,
    newExplore, newFlashFromLegacy, openSession, selectRun, setHistoryOpen, setModel, useSuggestion, submit, runRecommendation, confirmJudgment, chooseFeedback,
    finishAnalysis, finishEvent: finishAnalysis, clarify, confirmMaterials, toggleMaterial, cancel, retry, regenerate,
    startRename, startDelete, closeSessionAction, rename, removeSession, download, continueFromSelected, refreshSessions,
    refreshFiles, loadContexts, setContextMode, toggleContextFile, saveContext, openFile, clearFileDetail, renameFile, removeFile, downloadFile };
}
