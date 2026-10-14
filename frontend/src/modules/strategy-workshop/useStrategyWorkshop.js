import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { workshopRequest } from './api.js';
import { configFrom, isActiveRun, isFinishedRun, loadDraft, saveDraft, validateConfig } from './format.js';
import { loadLanguageStyle, normalizeLanguageStyle, saveLanguageStyle } from './language.js';

const id = () => globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;

export function useStrategyWorkshop(ownerKey, personalAppKey = ref('')) {
  const languageStyle = ref('beginner');
  const idea = ref('');
  const session = ref(null);
  const capabilities = ref(null);
  const catalog = ref([]);
  const symbols = ref([]);
  const form = reactive(configFrom());
  const fieldErrors = ref({});
  const error = ref('');
  const syncError = ref('');
  const busy = ref('');
  const loading = ref(true);
  const step = ref('idea');
  const preflight = ref(null);
  const coverageAccepted = ref(false);
  const diagnosticAccepted = ref(false);
  const run = ref(null);
  const openedPersonalApp = ref(null);
  const personalAppRunId = ref('');
  const personalApp = computed(() => personalAppView.value ? openedPersonalApp.value : run.value?.personalApp || null);
  const personalAppView = ref(false);
  const personalAppSaveError = ref('');
  const canSavePersonalApp = computed(() => isFinishedRun(run.value) && Boolean(run.value?.result || run.value?.report));
  const history = ref([]);
  const nextCursor = ref(null);
  const historyOpen = ref(false);
  const historyReadOnly = ref(false);
  const conversation = ref(null);
  const conversationOpen = ref(false);
  const historicalCode = ref('');
  const sourceRunId = ref(null);
  const customizationInstructions = ref('');
  let generation = 0;
  let disposed = false;
  let owner = '';
  let personalAppId = '';
  let timer = null;
  let searchTimer = null;
  let searchGeneration = 0;
  const controllers = new Set();
  const actionKeys = new Map();
  let suppressDraft = false;
  let activeView = null;
  let historyGeneration = 0;

  const candidate = computed(() => historyReadOnly.value ? run.value?.candidate : session.value?.candidate);
  const customization = computed(() => historyReadOnly.value ? null : session.value?.customization || null);
  const report = computed(() => run.value?.result || run.value?.report || null);
  const active = computed(() => isActiveRun(run.value));
  const formalAvailable = computed(() => preflight.value?.gate?.eligible === true || preflight.value?.gate?.reportEligible === true || preflight.value?.gate?.report_eligible === true);
  const coverageRequired = computed(() => Boolean(preflight.value?.coverage?.required || preflight.value?.requiresCoverageConfirmation));
  const canRun = computed(() => Boolean(preflight.value && !busy.value && (!coverageRequired.value || coverageAccepted.value) && (formalAvailable.value || diagnosticAccepted.value)));
  const selectedSymbol = computed(() => symbols.value.find((value) => (value.symbol || value.code) === form.symbol));
  const canReconfigurePersonalApp = computed(() => Boolean(personalAppView.value && openedPersonalApp.value && !active.value && !historyOpen.value && !conversationOpen.value && run.value?.runId === personalAppRunId.value));

  function stopObservation() {
    clearTimeout(timer);
    clearTimeout(searchTimer);
    timer = null;
    for (const controller of controllers) controller.abort();
    controllers.clear();
  }

  async function request(path, options = {}) {
    const at = generation;
    const controller = new AbortController();
    controllers.add(controller);
    try {
      const value = await workshopRequest(path, { ...options, signal: controller.signal });
      if (disposed || at !== generation) throw new DOMException('Stale owner response', 'AbortError');
      return value;
    } finally { controllers.delete(controller); }
  }

  async function action(kind, path, payload = {}) {
    const signature = `${kind}:${path}:${JSON.stringify(payload)}`;
    const key = actionKeys.get(signature) || id();
    actionKeys.set(signature, key);
    const value = await request(path, { method: 'POST', body: { ...payload, idempotencyKey: key } });
    actionKeys.delete(signature);
    return value;
  }

  async function operation(kind, fn) {
    if (busy.value || !owner) return;
    const at = generation;
    busy.value = kind;
    error.value = '';
    try { return await fn(); }
    catch (failure) {
      if (failure.name !== 'AbortError' && at === generation) error.value = failure.message;
    } finally { if (at === generation) busy.value = ''; }
  }

  function persist() {
    if (!owner || suppressDraft || disposed || (personalAppView.value && !session.value)) return;
    saveDraft(globalThis.localStorage, owner, { idea: idea.value, sessionId: session.value?.sessionId || null, customizationInstructions: customizationInstructions.value, config: { ...form }, ...(personalAppView.value ? { resultRunId: personalAppRunId.value } : {}) }, personalAppView.value ? personalAppId : '');
  }

  function resetPreflight() {
    preflight.value = null;
    coverageAccepted.value = false;
    diagnosticAccepted.value = false;
    fieldErrors.value = {};
    persist();
  }

  function applySession(value, { restore = false } = {}) {
    session.value = value;
    idea.value = value.idea || idea.value;
    if (value.preflight && restore) preflight.value = value.preflight;
    if (value.selectionApproval?.decision === 'accept' || value.status === 'configuring' || value.status === 'ready' || value.preflight) step.value = 'config';
    else step.value = value.customization ? 'customization' : value.candidate ? 'recommendation' : 'idea';
    persist();
  }

  function restorePreflight() {
    if (!preflight.value) return;
    const frozen = configFrom(preflight.value.requested);
    if (frozen.symbol !== form.symbol || frozen.startDate !== form.startDate || frozen.endDate !== form.endDate || Number(frozen.initialCash) !== Number(form.initialCash)) preflight.value = null;
  }

  async function loadHistory(append = false) {
    const query = append && nextCursor.value ? `?cursor=${encodeURIComponent(nextCursor.value)}` : '';
    const value = await request(`/sessions${query}`);
    const records = value.sessions || value.items || [];
    history.value = append ? [...history.value, ...records] : records;
    nextCursor.value = value.nextCursor || null;
    return records;
  }

  async function initialize(key, appId = personalAppKey.value) {
    generation += 1;
    stopObservation();
    owner = key == null ? '' : String(key);
    personalAppId = appId ? String(appId) : '';
    personalAppView.value = Boolean(appId);
    openedPersonalApp.value = null;
    personalAppRunId.value = '';
    languageStyle.value = owner ? loadLanguageStyle(owner) : 'beginner';
    actionKeys.clear();
    suppressDraft = true;
    session.value = null; run.value = null; preflight.value = null; capabilities.value = null; catalog.value = [];
    symbols.value = []; history.value = []; historyOpen.value = false; historyReadOnly.value = false;
    conversation.value = null; conversationOpen.value = false; activeView = null; historyGeneration += 1;
    historicalCode.value = ''; error.value = ''; syncError.value = ''; personalAppSaveError.value = ''; busy.value = ''; step.value = 'idea';
    coverageAccepted.value = false; diagnosticAccepted.value = false; sourceRunId.value = null; fieldErrors.value = {};
    customizationInstructions.value = '';
    Object.assign(form, configFrom());
    idea.value = '';
    if (!owner) { loading.value = false; suppressDraft = false; return; }
    const draft = loadDraft(globalThis.localStorage, owner, personalAppId);
    idea.value = draft?.idea || '';
    customizationInstructions.value = draft?.customizationInstructions || '';
    const at = generation;
    loading.value = true;
    const results = await Promise.allSettled([
      request('/capabilities'), request('/symbols'), loadHistory(),
      appId ? request(`/personal-apps/${encodeURIComponent(appId)}`) : draft?.sessionId ? request(`/sessions/${encodeURIComponent(draft.sessionId)}`) : draft ? Promise.resolve(null) : request('/sessions/current').then((value) => value.session),
      request('/strategies'),
    ]);
    if (at !== generation || disposed) return;
    const [caps, symbolResult, , sessionResult, catalogResult] = results;
    if (caps.status === 'fulfilled') {
      capabilities.value = caps.value;
      Object.assign(form, configFrom(caps.value.defaults || {}));
    }
    if (symbolResult.status === 'fulfilled') symbols.value = symbolResult.value.symbols || symbolResult.value.items || symbolResult.value || [];
    if (catalogResult.status === 'fulfilled') catalog.value = catalogResult.value.strategies || [];
    if (draft?.config) Object.assign(form, draft.config);
    if (appId && sessionResult.status === 'fulfilled' && sessionResult.value) {
      openedPersonalApp.value = sessionResult.value.app;
      run.value = { ...sessionResult.value.run, personalApp: sessionResult.value.app };
      personalAppRunId.value = run.value.runId;
      Object.assign(form, configFrom(run.value.requested || run.value.config || capabilities.value?.defaults));
      historyReadOnly.value = true;
      step.value = 'result';
      if (draft?.sessionId) {
        try {
          const restoredSession = await request(`/sessions/${encodeURIComponent(draft.sessionId)}`);
          if (restoredSession.sessionId !== draft.sessionId || restoredSession.sourcePersonalAppId !== personalAppId || !restoredSession.candidate) throw new Error('这份回测设置不属于当前个人应用，请重新设置。');
          let restoredRun = null;
          if (restoredSession.runId) {
            restoredRun = await request(`/runs/${encodeURIComponent(restoredSession.runId)}`);
            if (restoredRun.runId !== restoredSession.runId || restoredRun.sessionId !== restoredSession.sessionId || restoredRun.sourcePersonalAppId !== personalAppId) throw new Error('这份回测结果不属于当前个人应用，请重新设置。');
          } else if (draft.resultRunId && draft.resultRunId !== run.value.runId) {
            restoredRun = await request(`/runs/${encodeURIComponent(draft.resultRunId)}`);
            if (restoredRun.runId !== draft.resultRunId || restoredRun.sourcePersonalAppId !== personalAppId) throw new Error('上次回测结果不属于当前个人应用，请重新设置。');
          }
          Object.assign(form, configFrom(draft.config || restoredSession.rerunDefaults || run.value.requested || capabilities.value?.defaults));
          applySession(restoredSession, { restore: true });
          restorePreflight();
          historyReadOnly.value = false;
          if (restoredRun) {
            run.value = restoredRun;
            personalAppRunId.value = restoredRun.runId;
            if (restoredSession.runId) step.value = 'result';
            if (isActiveRun(restoredRun)) schedulePoll();
          }
        } catch (failure) {
          if (failure.name !== 'AbortError' && at === generation) syncError.value = `未能恢复这次回测设置：${failure.message} 已保留应用原来的结果，可以重试刷新或重新设置。`;
        }
      }
    } else if (!appId && sessionResult.status === 'fulfilled' && sessionResult.value) {
      applySession(sessionResult.value, { restore: true });
      restorePreflight();
      // An unsent draft remains editable without altering the server-confirmed selection.
      if (draft?.idea && draft.idea !== sessionResult.value.idea) idea.value = draft.idea;
      if (sessionResult.value.runId) {
        try {
          run.value = await request(`/runs/${encodeURIComponent(sessionResult.value.runId)}`);
          step.value = 'result';
          if (isActiveRun(run.value)) schedulePoll();
        } catch (failure) { if (failure.name !== 'AbortError' && at === generation) syncError.value = failure.message; }
      }
    }
    if (at !== generation || disposed) return;
    const failures = results.filter((result) => result.status === 'rejected' && result.reason?.name !== 'AbortError' && (appId || result.reason?.status !== 404));
    if (failures.length) error.value = failures[0].reason.message;
    loading.value = false;
    suppressDraft = false;
    persist();
  }

  function schedulePoll() {
    clearTimeout(timer);
    if (!disposed && isActiveRun(run.value)) timer = setTimeout(() => syncRun(), 1400);
  }

  async function syncRun() {
    if (!run.value?.runId) return;
    const runId = run.value.runId;
    const at = generation;
    try {
      const value = await request(`/runs/${encodeURIComponent(runId)}`);
      if (at !== generation || run.value?.runId !== runId) return;
      run.value = value;
      syncError.value = '';
      if (isActiveRun(value)) schedulePoll();
      else { await loadHistory(); }
    } catch (failure) {
      if (failure.name !== 'AbortError' && at === generation) syncError.value = `状态同步失败：${failure.message}`;
    }
  }

  async function match() {
    if (!idea.value.trim()) { fieldErrors.value = { idea: '请先描述你的投资想法。' }; return; }
    return operation('match', async () => {
      let value;
      if (session.value) value = await action('rematch', `/sessions/${encodeURIComponent(session.value.sessionId)}/rematch`, { idea: idea.value.trim(), expectedRevision: session.value.matchRevision, languageStyle: languageStyle.value });
      else value = await action('session', '/sessions', { idea: idea.value.trim(), languageStyle: languageStyle.value, ...(sourceRunId.value ? { sourceRunId: sourceRunId.value } : {}) });
      applySession(value);
      customizationInstructions.value = '';
      resetPreflight();
      step.value = 'recommendation';
      fieldErrors.value = {};
    });
  }

  async function select(decision) {
    if (!session.value?.candidate) return;
    if (session.value.selectionApproval?.decision === 'accept') {
      step.value = decision === 'accept' ? 'config' : 'idea';
      if (decision === 'reject') resetPreflight();
      return;
    }
    return operation('selection', async () => {
      const value = await action('selection', `/sessions/${encodeURIComponent(session.value.sessionId)}/selection`, {
        matchRevision: session.value.matchRevision,
        selectionDigest: session.value.candidate.selectionDigest, decision,
      });
      applySession(value);
      resetPreflight();
      step.value = decision === 'accept' ? 'config' : 'idea';
    });
  }

  async function considerCustomization() {
    if (busy.value || !owner || !session.value?.candidate) return;
    step.value = 'customization';
    if (!customization.value) return rewriteCustomization();
  }

  async function rewriteCustomization() {
    if (!session.value?.candidate) return;
    return operation('customization', async () => {
      const value = await action('customization', `/sessions/${encodeURIComponent(session.value.sessionId)}/customization`, {
        languageStyle: languageStyle.value,
        matchRevision: session.value.matchRevision,
        selectionDigest: session.value.candidate.selectionDigest,
        ...(customizationInstructions.value.trim() ? { instructions: customizationInstructions.value.trim() } : {}),
      });
      applySession(value);
      customizationInstructions.value = '';
      resetPreflight();
      step.value = 'customization';
    });
  }

  async function confirmCustomization() {
    if (customization.value?.status !== 'rules_ready' || !customization.value.rulesDigest) return;
    return operation('customization-confirm', async () => {
      const value = await action('customization-confirm', `/sessions/${encodeURIComponent(session.value.sessionId)}/customization/confirm`, {
        customizationId: customization.value.id,
        rulesDigest: customization.value.rulesDigest,
      });
      applySession(value);
      resetPreflight();
      step.value = 'customization';
    });
  }

  async function returnToBaseStrategy() {
    if (!session.value?.candidate) return;
    if (!customization.value) { step.value = 'recommendation'; return; }
    return operation('customization-reset', async () => {
      const value = await action('customization-reset', `/sessions/${encodeURIComponent(session.value.sessionId)}/customization/reset`, {
        matchRevision: session.value.matchRevision,
        selectionDigest: session.value.candidate.selectionDigest,
      });
      applySession(value);
      customizationInstructions.value = '';
      resetPreflight();
      step.value = 'recommendation';
    });
  }

  async function checkConfig() {
    fieldErrors.value = validateConfig(form);
    if (Object.keys(fieldErrors.value).length) return;
    return operation('preflight', async () => {
      const value = await action('preflight', `/sessions/${encodeURIComponent(session.value.sessionId)}/preflight`, {
        selectionRevision: session.value.selectionRevision || session.value.selectionApproval?.revision || session.value.selectionApproval?.selectionRevision || session.value.matchRevision,
        symbol: form.symbol.trim().toUpperCase(), startDate: form.startDate, endDate: form.endDate, initialCash: Number(form.initialCash),
      });
      preflight.value = value;
      coverageAccepted.value = false;
      diagnosticAccepted.value = false;
      persist();
    });
  }

  async function reconfigurePersonalApp() {
    if (!canReconfigurePersonalApp.value || busy.value || activeView) return;
    if (session.value?.sourcePersonalAppId === personalAppId && !session.value.runId) {
      historyReadOnly.value = false;
      conversation.value = null;
      conversationOpen.value = false;
      step.value = 'config';
      return;
    }
    return operation('personal-app-session', async () => {
      const value = await action('personal-app-session', `/personal-apps/${encodeURIComponent(personalAppId)}/sessions`, { languageStyle: languageStyle.value });
      if (!value.sessionId || value.sourcePersonalAppId !== personalAppId || !value.candidate) throw new Error('未能载入这个应用的策略，请重试。');
      // Keep the current result visible until the new configuration session is confirmed.
      const previousConfig = run.value?.requested || value.rerunDefaults || capabilities.value?.defaults;
      historicalCode.value = '';
      historyReadOnly.value = false;
      conversation.value = null;
      conversationOpen.value = false;
      activeView = null;
      historyGeneration += 1;
      customizationInstructions.value = '';
      applySession(value);
      Object.assign(form, configFrom(previousConfig));
      resetPreflight();
      syncError.value = '';
      step.value = 'config';
      persist();
    });
  }

  function viewPersonalAppResult() {
    if (!personalAppView.value || !run.value || busy.value || activeView) return;
    historyReadOnly.value = !session.value?.runId;
    step.value = 'result';
  }

  async function startRun() {
    if (!canRun.value) return;
    return operation('run', async () => {
      const value = await action('run', '/runs', {
        sessionId: session.value.sessionId, preflightId: preflight.value.preflightId,
        configDigest: preflight.value.configDigest,
        ...(coverageRequired.value ? { coverageDecision: { decision: 'accept_actual_interval', coverageDigest: preflight.value.coverage?.digest } } : {}),
        mode: formalAvailable.value ? 'formal' : 'diagnostic',
        ...(!formalAvailable.value ? { diagnosticAcknowledged: true } : {}),
      });
      run.value = value;
      if (personalAppView.value) personalAppRunId.value = value.runId;
      historicalCode.value = '';
      historyReadOnly.value = false;
      step.value = 'result';
      session.value = { ...session.value, runId: value.runId };
      persist();
      schedulePoll();
      await loadHistory();
    });
  }

  async function cancelRun() {
    if (historyReadOnly.value || !active.value || ['cancelling', 'cancel_requested'].includes(run.value.status)) return;
    return operation('cancel', async () => {
      run.value = await action('cancel', `/runs/${encodeURIComponent(run.value.runId)}/cancel`);
      schedulePoll();
    });
  }

  async function savePersonalApp() {
    if (personalApp.value) return personalApp.value;
    if (!canSavePersonalApp.value) return;
    const runId = run.value.runId;
    const viewAt = historyGeneration;
    const at = generation;
    personalAppSaveError.value = '';
    const app = await operation('save-personal-app', async () => {
      const value = await action('save-personal-app', `/runs/${encodeURIComponent(runId)}/personal-app`);
      if (viewAt !== historyGeneration || run.value?.runId !== runId) return;
      run.value = { ...run.value, personalApp: value.app };
      return value.app;
    });
    if (!app && at === generation && run.value?.runId === runId) personalAppSaveError.value = error.value;
    return app;
  }

  async function openHistory(record) {
    const sessionId = record.sessionId || record.id;
    if (!sessionId) return;
    return operation('history', async () => {
      const viewAt = ++historyGeneration;
      const value = await request(`/sessions/${encodeURIComponent(sessionId)}/conversation`);
      if (viewAt !== historyGeneration) return;
      rememberActiveView();
      clearTimeout(timer);
      run.value = null;
      conversation.value = value;
      conversationOpen.value = true;
      historyReadOnly.value = true;
      historyOpen.value = false;
      historicalCode.value = '';
      syncError.value = '';
    });
  }

  function rememberActiveView() {
    if (!activeView) activeView = { run: run.value, step: step.value, historicalCode: historicalCode.value, syncError: syncError.value, historyReadOnly: historyReadOnly.value };
  }

  async function openHistoricalRun(record) {
    const runId = record.runId || record.id;
    if (!runId) return;
    return operation('history', async () => {
      const viewAt = ++historyGeneration;
      const [value, transcript] = await Promise.all([
        request(`/runs/${encodeURIComponent(runId)}`),
        request(`/runs/${encodeURIComponent(runId)}/conversation`),
      ]);
      if (viewAt !== historyGeneration) return;
      rememberActiveView();
      clearTimeout(timer);
      run.value = value;
      conversation.value = transcript;
      conversationOpen.value = false;
      historyReadOnly.value = true;
      historyOpen.value = false;
      historicalCode.value = '';
      step.value = 'result';
      syncError.value = '';
      if (isActiveRun(value)) schedulePoll();
    });
  }

  async function viewRunConversation() {
    if (!run.value?.runId) return;
    const runId = run.value.runId;
    return operation('history', async () => {
      const viewAt = ++historyGeneration;
      const value = await request(`/runs/${encodeURIComponent(runId)}/conversation`);
      if (viewAt !== historyGeneration || run.value?.runId !== runId) return;
      rememberActiveView();
      clearTimeout(timer);
      conversation.value = value;
      conversationOpen.value = true;
      historyOpen.value = false;
      historyReadOnly.value = true;
    });
  }

  function leaveHistory() {
    historyGeneration += 1;
    clearTimeout(timer);
    const readOnly = activeView ? activeView.historyReadOnly : personalAppView.value;
    if (activeView) {
      run.value = activeView.run;
      step.value = activeView.step;
      historicalCode.value = activeView.historicalCode;
      syncError.value = activeView.syncError;
    }
    activeView = null;
    conversation.value = null;
    conversationOpen.value = false;
    historyReadOnly.value = readOnly;
    historyOpen.value = false;
    if (isActiveRun(run.value)) schedulePoll();
  }

  async function showHistory() {
    historyOpen.value = true;
    conversationOpen.value = false;
    return operation('history', () => loadHistory());
  }

  async function getHistoricalCode() {
    if (!run.value?.runId || historicalCode.value) return;
    const runId = run.value.runId;
    const viewAt = historyGeneration;
    return operation('code', async () => {
      const value = await request(`/runs/${encodeURIComponent(runId)}/code`);
      if (viewAt !== historyGeneration || run.value?.runId !== runId) return;
      historicalCode.value = value.code || value.sourceCode || '';
    });
  }

  function newSession({ fromRun = false } = {}) {
    clearTimeout(timer);
    const oldRun = run.value;
    const previousIdea = oldRun?.idea || oldRun?.candidate?.idea || session.value?.idea || '';
    personalAppView.value = false;
    openedPersonalApp.value = null;
    personalAppRunId.value = '';
    session.value = null; run.value = null; historicalCode.value = '';
    historyReadOnly.value = false; historyOpen.value = false; error.value = ''; syncError.value = '';
    conversation.value = null; conversationOpen.value = false; activeView = null; historyGeneration += 1;
    customizationInstructions.value = '';
    resetPreflight();
    step.value = 'idea';
    sourceRunId.value = fromRun ? oldRun?.runId : null;
    idea.value = fromRun ? previousIdea : '';
    Object.assign(form, configFrom(fromRun ? oldRun?.requested || oldRun?.config || capabilities.value?.defaults : capabilities.value?.defaults));
    persist();
  }

  function searchSymbols(query) {
    clearTimeout(searchTimer);
    const searchAt = ++searchGeneration;
    const at = generation;
    searchTimer = setTimeout(async () => {
      try {
        const value = await request(`/symbols?q=${encodeURIComponent(query)}`);
        if (at === generation && searchAt === searchGeneration) symbols.value = value.symbols || value.items || value || [];
      } catch (failure) { if (failure.name !== 'AbortError' && at === generation) error.value = failure.message; }
    }, 220);
  }

  function useSymbolDefaults() {
    form.symbol = form.symbol.trim().toUpperCase();
    const value = selectedSymbol.value;
    if (value) {
      const defaults = value.defaults || value;
      if (defaults.startDate || defaults.defaultStartDate) form.startDate = defaults.startDate || defaults.defaultStartDate;
      if (defaults.endDate || defaults.asof) form.endDate = defaults.endDate || defaults.asof;
    }
    resetPreflight();
  }

  function setLanguageStyle(value) {
    languageStyle.value = normalizeLanguageStyle(value);
    if (owner) saveLanguageStyle(owner, languageStyle.value);
  }

  watch(idea, persist);
  watch(() => run.value?.runId, () => { personalAppSaveError.value = ''; });
  watch(customizationInstructions, persist);
  watch(() => ({ ...form }), () => { if (!suppressDraft) resetPreflight(); }, { flush: 'sync' });
  watch(() => [ownerKey.value, personalAppKey.value], ([key, appId]) => initialize(key, appId), { immediate: true });
  onBeforeUnmount(() => { disposed = true; generation += 1; stopObservation(); });

  return { languageStyle, setLanguageStyle, idea, session, capabilities, catalog, symbols, form, fieldErrors, error, syncError, busy, loading, step,
    preflight, coverageAccepted, diagnosticAccepted, run, history, nextCursor, historyOpen, historyReadOnly, conversation, conversationOpen,
    historicalCode, candidate, customization, customizationInstructions, report, active, formalAvailable, coverageRequired, canRun, selectedSymbol, sourceRunId,
    personalApp, personalAppView, personalAppSaveError, canSavePersonalApp, savePersonalApp, canReconfigurePersonalApp, reconfigurePersonalApp, viewPersonalAppResult,
    match, select, considerCustomization, rewriteCustomization, confirmCustomization, returnToBaseStrategy, checkConfig, startRun, cancelRun, openHistory, openHistoricalRun, viewRunConversation, leaveHistory, showHistory, getHistoricalCode, newSession,
    searchSymbols, useSymbolDefaults, resetPreflight, syncRun, loadHistory,
    refreshHistory: () => operation('history', () => loadHistory()),
    loadMoreHistory: () => operation('history', () => loadHistory(true)),
    reload: () => initialize(owner, personalAppView.value ? personalAppKey.value : ''),
  };
}
