<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useExplore } from './useExplore.js';
import ExploreAmbientBackground from './ExploreAmbientBackground.vue';
import ExploreComposer from './ExploreComposer.vue';
import ExploreRecommendations from './ExploreRecommendations.vue';
import ExploreSourceChip from './ExploreSourceChip.vue';
import { useExploreMotion } from './useExploreMotion.js';
import { formatEvidenceTime, sourceSupports, publicRunActivity } from './presentation.js';
import { eventBriefCoverageNote, isEventBrief } from './reportPresentation.js';
import { renderReportMarkdown } from './reportMarkdown.js';
import { moduleTitle, safeSourceUrl, sourceLabel, sourceKindLabel, stageLabel, reportSources, runMaterials, confirmedMaterialIds, materialContentLabel, retrievalSearches, searchStatusLabel, searchFreshnessLabel, fileKindLabel, fileOriginLabel, contextFiles, maxContextFiles } from './model.js';
import './explore.css';

const props = defineProps({ ownerId: { type: [String, Number], required: true } });
const emit = defineEmits(['back']);
const { effectivePaused } = useExploreMotion();
const {
  state, selectedRun, selectedReport, tree, isActive, canStartRun, canSubmit, currentContext, contextDirty, selectedContextFiles,
  newExplore, openSession, selectRun, setHistoryOpen, useSuggestion, runRecommendation, submit, confirmJudgment, clarify, confirmMaterials, toggleMaterial, cancel, retry,
  regenerate, startRename, startDelete, closeSessionAction, rename, removeSession, download, continueFromSelected,
  refreshFiles, loadContexts, setContextMode, toggleContextFile, saveContext, openFile, clearFileDetail, renameFile, removeFile, downloadFile,
} = useExplore(props.ownerId);
const input = ref(null);
const reader = ref(null);
const viewportHeight = ref(null);
function updateViewportHeight() {
  const viewport = window.visualViewport;
  viewportHeight.value = viewport && viewport.scale === 1 ? Math.round(viewport.height) : null;
}
const reportHead = ref(null);
const headVisible = ref(true);
const reportArriving = ref(false);
const reportSwitching = ref(false);
const inlineTitle = ref(false);
const titleInput = ref(null);
const titleButton = ref(null);
const highlightedSource = ref('');
const backgroundCompletion = ref(null);
const saveNoticeVisible = ref(false);
const submitPending = ref(false);
let saveNoticeTimer;
const noticeHolds = new Set();
function scheduleNoticeDismiss() {
  clearTimeout(saveNoticeTimer);
  if (noticeHolds.size || newFilesExpanded.value) return;
  saveNoticeTimer = setTimeout(() => { saveNoticeVisible.value = false; }, 2800);
}
function holdNotice(reason) { noticeHolds.add(reason); clearTimeout(saveNoticeTimer); }
function releaseNotice(reason, event) {
  if (reason === 'focus' && event.currentTarget.contains(event.relatedTarget)) return;
  noticeHolds.delete(reason); scheduleNoticeDismiss();
}
let shellAnimation;
const readPositions = new Map();
const observedRunStates = new Map();
const liveRuns = new Set();
let headObserver;
let reportMotionTimer;
let sourceHighlightTimer;
let pendingOverlayFocus = false;
function restoreOverlayFocus(target) {
  nextTick(() => {
    if (materialsOpen.value || filesOpen.value) return;
    if (target?.isConnected && !target.disabled && !target.closest('[inert]')) target.focus({ preventScroll: true });
    else if (state.loadingSession) pendingOverlayFocus = true;
    else (titleButton.value || historyPanel.value?.querySelector('[data-testid="explore-new"]'))?.focus({ preventScroll: true });
  });
}
watch(reportHead, (node) => {
  headObserver?.disconnect();
  headVisible.value = true;
  if (node && typeof IntersectionObserver !== 'undefined') {
    headObserver = new IntersectionObserver(([entry]) => { headVisible.value = entry.isIntersecting; }, { root: reader.value });
    headObserver.observe(node);
  }
});
watch(() => state.runs.map(run => [run.id, run.status]), () => {
  for (const run of state.runs) {
    const previous = observedRunStates.get(run.id);
    if (['queued', 'running'].includes(run.status)) liveRuns.add(run.id);
    if (['queued', 'running'].includes(previous) && ['succeeded', 'partial', 'failed', 'cancelled', 'interrupted'].includes(run.status) && String(run.id) !== String(state.selectedRunId)) backgroundCompletion.value = run;
    observedRunStates.set(run.id, run.status);
  }
});
watch(() => selectedReport.value?.id, (id, previous) => {
  clearTimeout(reportMotionTimer);
  reportArriving.value = Boolean(id && liveRuns.has(selectedRun.value?.id));
  reportSwitching.value = Boolean(id && previous && id !== previous && !reportArriving.value);
  if (id) liveRuns.delete(selectedRun.value?.id);
  reportMotionTimer = setTimeout(() => { reportArriving.value = false; reportSwitching.value = false; }, 340);
});
const historyClose = ref(null);
const historyPanel = ref(null);
const historyToggle = ref(null);
const sessionMenuId = ref('');
const sessionMenu = ref(null);
const sessionMenuStyle = ref({});
const menuSession = computed(() => state.sessions.find(item => String(item.id) === sessionMenuId.value) || null);
const sessionActionOpen = computed(() => state.renameOpen || state.deleteOpen);
const sessionActionModalOpen = computed(() => sessionActionOpen.value && !inlineTitle.value);
const sessionDialog = ref(null);
const renameInput = ref(null);
const deleteCancel = ref(null);
let menuTrigger = null;
let sessionActionReturnFocus = null;
let renameComposing = false;
let renameCompositionEndedAt = 0;
const compactHistory = ref(typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches);
const narrowFileDock = ref(typeof window !== 'undefined' && window.matchMedia('(max-width: 1100px)').matches);
const historyModal = computed(() => compactHistory.value && state.historyOpen);
let compactMedia = null;
let narrowFileMedia = null;
function onHistoryBreakpointChange(event) { compactHistory.value = event.matches; }
function onFileBreakpointChange(event) { narrowFileDock.value = event.matches; if (event.matches) newFilesExpanded.value = false; }
onMounted(() => {
  updateViewportHeight();
  window.visualViewport?.addEventListener('resize', updateViewportHeight);
  compactMedia = window.matchMedia('(max-width: 767px)');
  compactHistory.value = compactMedia.matches;
  compactMedia.addEventListener('change', onHistoryBreakpointChange);
  narrowFileMedia = window.matchMedia('(max-width: 1100px)');
  narrowFileDock.value = narrowFileMedia.matches;
  narrowFileMedia.addEventListener('change', onFileBreakpointChange);
  document.addEventListener('pointerdown', onSessionMenuPointerDown, true);
  document.addEventListener('scroll', onSessionMenuScroll, true);
  window.addEventListener('resize', onSessionMenuScroll);
});
onUnmounted(() => {
  window.visualViewport?.removeEventListener('resize', updateViewportHeight);
  headObserver?.disconnect();
  clearTimeout(reportMotionTimer);
  clearTimeout(sourceHighlightTimer);
  clearTimeout(saveNoticeTimer);
  shellAnimation?.cancel();
  compactMedia?.removeEventListener('change', onHistoryBreakpointChange);
  narrowFileMedia?.removeEventListener('change', onFileBreakpointChange);
  document.removeEventListener('pointerdown', onSessionMenuPointerDown, true);
  document.removeEventListener('scroll', onSessionMenuScroll, true);
  window.removeEventListener('resize', onSessionMenuScroll);
});
const selectedRelationIndex = ref(0);
const materialsOpen = ref(false);
const citationSourceIds = ref([]);
const materialsDialog = ref(null);
const materialsClose = ref(null);
let materialsReturnFocus = null;
const filesOpen = ref(false);
const filesDialog = ref(null);
const filesClose = ref(null);
let filesReturnFocus = null;
const sessionFiles = computed(() => state.files.filter(file => String(file.session_id) === String(state.session?.id)));
const recentAddedFileIds = ref([]);
const newFilesExpanded = ref(false);
watch(newFilesExpanded, scheduleNoticeDismiss);
const newFiles = computed(() => recentAddedFileIds.value.map(id => sessionFiles.value.find(file => String(file.id) === id)).filter(Boolean));
let observedFileSession = '';
let observedFileIds = new Set();
let fileBaselineReady = false;
watch(() => [state.session?.id, state.loadingSession, state.filesLoading, sessionFiles.value.map(file => String(file.id)).join('|')], () => {
  const sessionId = String(state.session?.id || '');
  if (sessionId !== observedFileSession) {
    observedFileSession = sessionId;
    observedFileIds = new Set();
    recentAddedFileIds.value = [];
    newFilesExpanded.value = false;
    saveNoticeVisible.value = false;
    noticeHolds.clear();
    clearTimeout(saveNoticeTimer);
    fileBaselineReady = false;
  }
  if (!sessionId || state.loadingSession || state.filesLoading) return;
  const currentIds = new Set(sessionFiles.value.map(file => String(file.id)));
  if (!fileBaselineReady) fileBaselineReady = true;
  else {
    const added = [...currentIds].filter(id => !observedFileIds.has(id));
    if (added.length) {
      recentAddedFileIds.value = [...added, ...recentAddedFileIds.value];
      saveNoticeVisible.value = true;
      scheduleNoticeDismiss();
    }
  }
  observedFileIds = currentIds;
}, { immediate: true });
const selectedRelation = computed(() => selectedReport.value?.relations?.[selectedRelationIndex.value] || null);
const briefReport = computed(() => isEventBrief(selectedReport.value));
const briefCoverageNote = computed(() => eventBriefCoverageNote(selectedReport.value));
const materials = computed(() => runMaterials(selectedRun.value));
const drawerMaterials = computed(() => {
  const records = materials.value.map(material => {
    const used = reportSources(selectedReport.value).find(source => String(source.id) === String(material.id));
    return { ...material, ...Object.fromEntries(Object.entries(used || {}).filter(([, value]) => value != null && value !== '')), text_used: used?.text_used || material.text_used, report_used_text: used?.evidence_usage !== 'retrieved_only' ? used?.text_used || '' : '', retrieved_text: material.text_used };
  });
  for (const source of reportSources(selectedReport.value)) if (!records.some(item => String(item.id) === String(source.id))) records.push({ ...source, report_used_text: source.evidence_usage !== 'retrieved_only' ? source.text_used || '' : '', text_used: source.text_used || source.summary, content_kind: source.content_kind || 'search_snippet' });
  return records;
});
const visibleDrawerMaterials = computed(() => citationSourceIds.value.length
  ? citationSourceIds.value.map(id => drawerMaterials.value.find(item => String(item.id) === id)).filter(Boolean)
  : drawerMaterials.value);
const runActivity = computed(() => publicRunActivity(selectedRun.value));
const recentActivity = computed(() => runActivity.value.slice(-3));
const exampleQuestions = [
  { id: 'event', label: '梳理这篇报道的时间线', example: '请梳理以下报道的事件时间线、背景与不确定性：\n[在这里粘贴报道原文或链接]' },
  { id: 'relation', label: '找出这家公司的关键联系', example: '请分析[公司名称]与上下游主体的关系方向、机制和成立条件。\n[补充你关注的事件或材料]' },
  { id: 'history', label: '寻找判断的历史支持与反例', example: '请查找历史材料验证以下判断，并对照支持与反例：\n[填写待验证判断及适用条件]' },
];
const confirmedMaterials = computed(() => confirmedMaterialIds(selectedRun.value));
const progressFacts = computed(() => {
  const facts = [];
  if (materials.value.length) facts.push(`已保存 ${materials.value.length} 条检索材料`);
  if (selectedRun.value?.resolved_question || (selectedRun.value?.review && selectedRun.value.review.needs_clarification === false)) facts.push('研究问题已明确');
  return facts;
});
const progressQuestion = computed(() => selectedRun.value?.resolved_question || (selectedRun.value?.review?.needs_clarification === false ? selectedRun.value.review.interpreted_question : '') || '');
const modelOutputChars = computed(() => Number.isInteger(selectedRun.value?.model_progress?.output_chars) && selectedRun.value.model_progress.output_chars > 0 ? selectedRun.value.model_progress.output_chars : 0);
const progressHeading = computed(() => {
  const run = selectedRun.value;
  const phase = { claim_review: '正在核对证据与主张', report: '正在撰写研究报告', review: '正在复核报告', repair: '正在修订报告' };
  return run?.stage === 'analyzing' && phase[run.model_progress?.task] ? phase[run.model_progress.task] : stageLabel(run?.stage);
});
const reviewPending = computed(() => ['awaiting_clarification', 'awaiting_review'].includes(selectedRun.value?.status));
const canConfirmMaterials = computed(() => selectedRun.value?.status === 'awaiting_review' && state.reviewQuestion.trim() === initialReviewQuestion.value && materials.value.some(item => state.selectedSourceIds.includes(String(item.id))) && !state.actionBusy);
const initialReviewQuestion = computed(() => String(selectedRun.value?.review?.interpreted_question || selectedRun.value?.question || '').trim());
const statusTitle = computed(() => ({
  awaiting_input: '等待确认判断', awaiting_clarification: '等待明确问题', awaiting_review: '等待确认材料', queued: '已受理', running: '分析中', succeeded: '已完成',
  partial: '部分完成', failed: '分析失败', cancelled: '已取消', interrupted: '运行中断',
})[selectedRun.value?.status] || '尚未开始');
const scene = computed(() => selectedReport.value ? 'report' : selectedRun.value ? 'research' : 'entry');
watch(scene, async (value, previous) => {
  shellAnimation?.cancel();
  if (previous !== 'entry' || value !== 'research' || effectivePaused.value) return;
  const before = input.value?.getInteractionSnapshot();
  await nextTick();
  const after = input.value?.getInteractionSnapshot();
  // Only move the newly emptied submit surface, never an editor holding the next draft.
  if (!before?.shellRect || !after?.shellRect || state.draft || after.composing) return;
  const delta = before.shellRect.y - after.shellRect.y;
  if (Math.abs(delta) > 2) shellAnimation = input.value?.$el?.animate?.([{ transform: `translateY(${delta}px)` }, { transform: 'translateY(0)' }], { duration: 280, easing: 'cubic-bezier(.22,1,.36,1)' });
});
watch(effectivePaused, paused => { if (paused) shellAnimation?.cancel(); });
function stopComposerMotion() { shellAnimation?.cancel(); }
async function submitWithFeedback() {
  submitPending.value = true;
  try { return await submit(); } finally { submitPending.value = false; }
}
watch(() => state.selectedRunId, async (runId, previous) => {
  if (previous && reader.value && !state.loadingSession) readPositions.set(String(previous), reader.value.scrollTop);
  selectedRelationIndex.value = 0;
  closeMaterials();
  await nextTick();
  // Restore a revisited record, while same-record polling leaves reading untouched.
  if (!state.loadingSession && state.selectedRunId === runId) reader.value?.scrollTo({ top: readPositions.get(String(runId)) || 0, behavior: 'instant' });
});
watch(() => state.loadingSession, async (loading) => {
  if (!loading && pendingOverlayFocus) { pendingOverlayFocus = false; restoreOverlayFocus(null); }
  if (!loading) {
    const runId = state.selectedRunId;
    await nextTick();
    // Cross-session loads render the report only after context/files are ready.
    if (!state.loadingSession && state.selectedRunId === runId) reader.value?.scrollTo({ top: readPositions.get(String(runId)) || 0, behavior: 'instant' });
  }
});
watch(() => state.session?.id, () => {
  closeFiles();
  backgroundCompletion.value = null;
  observedRunStates.clear();
  liveRuns.clear();
  // Only a run observed active in this newly selected session may animate on completion.
  for (const run of state.runs) {
    observedRunStates.set(run.id, run.status);
    if (['queued', 'running'].includes(run.status)) liveRuns.add(run.id);
  }
  reportArriving.value = false;
});
watch(historyModal, (open) => { if (open) nextTick(() => historyClose.value?.focus()); }, { immediate: true });
watch(() => state.renameOpen, (open) => {
  renameComposing = false;
  renameCompositionEndedAt = 0;
  if (open) nextTick(() => { const node = inlineTitle.value ? titleInput.value : renameInput.value; node?.focus(); node?.select(); });
  else inlineTitle.value = false;
});
watch(() => state.deleteOpen, (open) => { if (open) nextTick(() => deleteCancel.value?.focus()); });
watch(sessionActionOpen, (open, wasOpen) => {
  if (!open && wasOpen) {
    const target = sessionActionReturnFocus;
    sessionActionReturnFocus = null;
    nextTick(() => {
      if (target?.isConnected) target.focus();
      else historyPanel.value?.querySelector('[data-testid="explore-new"]')?.focus();
    });
  }
});
watch(() => state.sessions.map(item => String(item.id)).join('|'), () => { if (sessionMenuId.value && !menuSession.value) closeSessionMenu(false); });

const displayTime = formatEvidenceTime;
function rememberReadingPosition() {
  if (state.selectedRunId && !state.loadingSession && reader.value) readPositions.set(String(state.selectedRunId), reader.value.scrollTop);
}
function historyStageLabel(stage, current) {
  if (current) return stageLabel(stage);
  if (stage === 'preparing_report') return '报告准备';
  return stageLabel(stage).replace(/^正在/, '');
}
function plain(value) {
  if (Array.isArray(value)) return value.map(plain).filter(Boolean).join('；');
  return value == null ? '' : String(value);
}
function safeMarkdown(value) {
  const formatter = window.FicagentFormat;
  return renderReportMarkdown(plain(value), formatter);
}
function jumpToAnalysis() { reader.value?.querySelector('.explore-report-body > .explore-report-section:not(.explore-summary):not(.explore-boundary)')?.scrollIntoView({ behavior: effectivePaused.value ? 'instant' : 'smooth', block: 'start' }); }
function claimSupportLabel(value) { return ({ traceable: '有可追溯片段', unsupported: '仅背景或证据不足' })[value] || '支持情况未标明'; }
function claimTypeLabel(value) { return ({ fact: '事实观察', sourced_statement: '来源陈述', inference: '条件推断', hypothesis: '待验证假设' })[value] || '类型未标明'; }
function sourceDomId(id) { return `explore-source-${String(selectedReport.value?.id || 'none').replace(/[^a-zA-Z0-9_-]/g, '')}-${String(id).replace(/[^a-zA-Z0-9_-]/g, '')}`; }
function focusSource(id) { openMaterials(String(id)); }
function openCitationSources(ids) { openMaterials(ids[0], ids); }
function showAllMaterials() {
  citationSourceIds.value = [];
  nextTick(() => materialsClose.value?.focus());
}
function chooseExample(item) { useSuggestion({ question: item.example, module: item.id }); nextTick(() => input.value?.focus()); }
function useReportAsSource() { continueFromSelected(); nextTick(() => input.value?.focus()); }
function openHistory() { setHistoryOpen(true); }
function closeHistory(restoreFocus = false) {
  const wasOpen = state.historyOpen;
  setHistoryOpen(false);
  if (restoreFocus || wasOpen) nextTick(() => historyToggle.value?.focus());
}
function toggleHistory() { if (state.historyOpen) closeHistory(true); else openHistory(); }
function startNewExplore() { newExplore(); }
function selectHistoryRun(runId) { selectRun(runId); }
function positionSessionMenu() {
  if (!menuTrigger?.isConnected) { closeSessionMenu(false); return; }
  const rect = menuTrigger.getBoundingClientRect();
  const scrollRect = menuTrigger.closest('.explore-history-scroll')?.getBoundingClientRect();
  if (scrollRect && (rect.bottom < scrollRect.top || rect.top > scrollRect.bottom)) { closeSessionMenu(false); return; }
  const width = 172;
  const top = rect.bottom + 6 + 92 <= window.innerHeight ? rect.bottom + 6 : Math.max(8, rect.top - 98);
  sessionMenuStyle.value = { left: `${Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8))}px`, top: `${top}px` };
}
function openSessionMenu(session, event) {
  event.stopPropagation();
  if (sessionMenuId.value === String(session.id)) { closeSessionMenu(true); return; }
  menuTrigger = event.currentTarget;
  sessionMenuId.value = String(session.id);
  positionSessionMenu();
  nextTick(() => sessionMenu.value?.querySelector('button')?.focus());
}
function closeSessionMenu(restoreFocus = false) {
  const target = menuTrigger;
  sessionMenuId.value = '';
  menuTrigger = null;
  if (restoreFocus) nextTick(() => { if (target?.isConnected) target.focus(); });
}
function onSessionMenuPointerDown(event) {
  if (!sessionMenuId.value || sessionMenu.value?.contains(event.target) || menuTrigger?.contains(event.target)) return;
  closeSessionMenu(false);
}
function onSessionMenuScroll() { if (sessionMenuId.value) positionSessionMenu(); }
function onSessionMenuKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); closeSessionMenu(true); return; }
  const options = [...(sessionMenu.value?.querySelectorAll('button:not([disabled])') || [])];
  if (!options.length) return;
  const index = options.indexOf(document.activeElement);
  if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    options[next].focus();
  } else if (event.key === 'Tab') {
    event.preventDefault();
    closeSessionMenu(true);
  }
}
function openSessionAction(kind) {
  const session = menuSession.value;
  const target = menuTrigger;
  closeSessionMenu(false);
  if (!session || state.actionBusy) return;
  inlineTitle.value = false;
  const opened = kind === 'rename' ? startRename(session) : startDelete(session);
  if (opened) sessionActionReturnFocus = target;
}
function cancelSessionDialog() { if (!state.actionBusy) closeSessionAction(); }
function onSessionDialogKeydown(event) {
  if (event.key === 'Escape') {
    if (renameComposing || event.isComposing || event.keyCode === 229 || state.actionBusy) return;
    event.preventDefault();
    cancelSessionDialog();
    return;
  }
  if (event.key !== 'Tab') return;
  const controls = [...(sessionDialog.value?.querySelectorAll('button:not([disabled]), input:not([disabled])') || [])].filter(item => item.getClientRects().length);
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
function onRenameCompositionStart() { renameComposing = true; }
function onRenameCompositionEnd(event) { renameComposing = false; renameCompositionEndedAt = event.timeStamp; }
function onRenameKeydown(event) {
  const justConfirmedComposition = renameCompositionEndedAt && event.timeStamp >= renameCompositionEndedAt && event.timeStamp - renameCompositionEndedAt < 60;
  if (event.key === 'Enter' && !renameComposing && !event.isComposing && event.keyCode !== 229 && !justConfirmedComposition && !event.repeat && !state.actionBusy && state.renameDraft.trim()) {
    event.preventDefault();
    rename();
  }
}
function onHistoryKeydown(event) {
  if (!historyModal.value) return;
  if (event.key === 'Escape') { event.preventDefault(); closeHistory(true); return; }
  if (event.key !== 'Tab') return;
  const controls = [...(historyPanel.value?.querySelectorAll('button:not([disabled])') || [])].filter(item => item.getClientRects().length);
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
function openMaterials(sourceId = '', sourceIds = []) {
  if (!drawerMaterials.value.length) return;
  if (filesOpen.value) closeFiles();
  if (!materialsOpen.value) materialsReturnFocus = document.activeElement;
  citationSourceIds.value = Array.isArray(sourceIds)
    ? [...new Set(sourceIds.map(String))].filter(id => drawerMaterials.value.some(item => String(item.id) === id))
    : [];
  materialsOpen.value = true;
  nextTick(() => {
    materialsClose.value?.focus();
    if (typeof sourceId === 'string' && sourceId) {
      const node = document.getElementById(`drawer-${sourceDomId(sourceId)}`);
      node?.scrollIntoView({ behavior: effectivePaused.value ? 'instant' : 'smooth', block: 'nearest' });
      highlightedSource.value = sourceId;
      clearTimeout(sourceHighlightTimer);
      sourceHighlightTimer = setTimeout(() => { highlightedSource.value = ''; }, 850);
    }
  });
}
function closeMaterials() {
  if (!materialsOpen.value) return;
  materialsOpen.value = false;
  highlightedSource.value = '';
  const target = materialsReturnFocus;
  materialsReturnFocus = null;
  restoreOverlayFocus(target);
}
function editTitle() {
  if (startRename()) {
    inlineTitle.value = true;
    sessionActionReturnFocus = titleButton.value;
  }
}
function onMaterialsKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); closeMaterials(); return; }
  if (event.key !== 'Tab') return;
  const controls = [...(materialsDialog.value?.querySelectorAll('button:not([disabled]), input:not([disabled]), a[href], [tabindex="0"]') || [])].filter(item => item.getClientRects().length);
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
function openFiles() {
  if (!state.session?.id || state.loadingSession) return;
  if (materialsOpen.value) closeMaterials();
  filesReturnFocus = document.activeElement;
  filesOpen.value = true;
  refreshFiles();
  nextTick(() => filesClose.value?.focus());
}
function openFilesAt(fileId) { openFiles(); if (filesOpen.value && fileId) openFile(fileId); }
function closeFiles() {
  if (!filesOpen.value) return;
  filesOpen.value = false;
  clearFileDetail();
  const target = filesReturnFocus;
  filesReturnFocus = null;
  restoreOverlayFocus(target);
}
async function finishFiles() {
  if (contextDirty.value && !(await saveContext())) return;
  closeFiles();
}
function onFilesKeydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); closeFiles(); return; }
  if (event.key !== 'Tab') return;
  const controls = [...(filesDialog.value?.querySelectorAll('button:not([disabled]), input:not([disabled]), a[href], [tabindex="0"]') || [])].filter(item => item.getClientRects().length);
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
</script>

<template>
  <section class="explore-theme explore-workspace" :style="viewportHeight ? { height: viewportHeight + 'px' } : undefined" :class="{ 'history-pinned': state.historyOpen && !compactHistory, 'motion-paused': effectivePaused, 'compact-height': viewportHeight > 0 && viewportHeight <= 460 }" :data-scene="scene" :data-module="selectedRun?.module || state.module" :inert="materialsOpen || filesOpen || sessionActionModalOpen || undefined" data-testid="explore-workspace" aria-label="Explore 独立研究工作区">
    <ExploreAmbientBackground :scene="scene" :paused="effectivePaused" />
    <button v-if="historyModal" class="explore-history-backdrop" type="button" tabindex="-1" aria-hidden="true" aria-label="关闭研究历史" @click="closeHistory" />
    <aside ref="historyPanel" class="explore-history" :class="{ open: state.historyOpen }" data-testid="explore-history-drawer" aria-label="研究历史" :role="historyModal && !sessionActionOpen ? 'dialog' : undefined" :aria-modal="historyModal && !sessionActionOpen ? 'true' : undefined" :aria-hidden="!state.historyOpen ? 'true' : undefined" :inert="!state.historyOpen || undefined" @keydown="onHistoryKeydown">
      <div class="explore-history-head"><div><span class="explore-overline">RESEARCH SPACE</span><h2>我的探索</h2></div><button ref="historyClose" class="explore-history-close" type="button" aria-label="关闭研究历史" @click="closeHistory">×</button></div>
      <button class="explore-new" data-testid="explore-new" type="button" @click="startNewExplore"><span aria-hidden="true">＋</span> 新建探索</button>
      <div class="explore-history-scroll">
        <p class="explore-list-label">探索会话</p>
        <p v-if="state.loading" class="explore-nav-muted" role="status">正在读取研究历史…</p>
        <p v-else-if="!state.sessions.length" class="explore-nav-muted">还没有保存的探索。</p>
        <ul v-else class="explore-session-list" data-testid="explore-session-list">
          <li v-for="session in state.sessions" :key="session.id" class="explore-session-row" :class="{ selected: String(state.session?.id) === String(session.id) }" :data-session-id="session.id"><button class="explore-session-item" :class="{ selected: String(state.session?.id) === String(session.id) }" type="button" :aria-current="String(state.session?.id) === String(session.id) ? 'page' : undefined" @click="openSession(session.id)"><span>{{ session.title || '未命名探索' }}</span><small>{{ displayTime(session.updated_at) }}</small></button><button class="explore-session-more" data-testid="explore-session-more" type="button" :aria-label="'更多操作：' + (session.title || '未命名探索')" aria-haspopup="menu" :aria-expanded="sessionMenuId === String(session.id)" @click="openSessionMenu(session, $event)">⋯</button></li>
        </ul>
        <template v-if="state.session && !state.loadingSession">
          <div class="explore-history-rule" />
          <p class="explore-list-label">当前会话 · 分析记录</p>
          <p v-if="!tree.length" class="explore-nav-muted">提交问题后，记录会保存在这里。</p>
          <TransitionGroup v-else name="explore-branch" tag="ul" class="explore-run-list" data-testid="explore-run-list">
            <li v-for="run in tree" :key="run.id"><button class="explore-run-item" :class="{ selected: String(state.selectedRunId) === String(run.id) }" :style="{ '--branch-depth': run.depth }" type="button" :aria-current="String(state.selectedRunId) === String(run.id) ? 'page' : undefined" @click="selectHistoryRun(run.id)"><span class="explore-branch-glyph" aria-hidden="true">{{ run.depth ? '↳' : '·' }}</span><span class="explore-run-copy"><strong>{{ moduleTitle(run.module) }}</strong><small>{{ run.question }}</small></span><span class="explore-run-state" :class="'state-' + run.status" :title="run.status" /></button></li>
          </TransitionGroup>
        </template>
      </div>
      <div class="explore-history-foot">研究记录保存在当前账号下</div>
    </aside>

    <div class="explore-main" :inert="historyModal || undefined">
      <header class="explore-topbar" :class="{ 'editing-title': inlineTitle && state.renameOpen }">
        <div class="explore-topbar-leading"><div class="explore-brand" aria-label="Ficagent Explore"><span class="brand-mark" aria-hidden="true">F</span><strong>Ficagent</strong><span class="explore-brand-divider" aria-hidden="true" /><span>Explore</span></div><button class="explore-back" type="button" @click="emit('back')"><span aria-hidden="true">←</span> 返回工作台</button></div>
        <div class="explore-topbar-title">
          <div v-if="inlineTitle && state.renameOpen" class="explore-title-editor" data-testid="explore-title-editor" @keydown="onSessionDialogKeydown">
            <input ref="titleInput" v-model="state.renameDraft" aria-label="探索名称" maxlength="120" :disabled="state.actionBusy" @compositionstart="onRenameCompositionStart" @compositionend="onRenameCompositionEnd" @keydown="onRenameKeydown" />
            <button type="button" :disabled="state.actionBusy || !state.renameDraft.trim()" @click="rename">保存</button><button type="button" :disabled="state.actionBusy" @click="cancelSessionDialog">取消</button>
            <span v-if="state.sessionActionError" role="alert">{{ state.sessionActionError }}</span>
          </div>
          <h1 v-else><button ref="titleButton" type="button" :disabled="!state.session || state.loadingSession" :title="state.session?.title || '新的探索'" aria-label="编辑探索标题" @click="editTitle">{{ state.session?.title || '新的探索' }} <span v-if="state.session" aria-hidden="true">✎</span></button></h1>
        </div>
        <div class="explore-topbar-actions">
          <div class="explore-file-dock" data-testid="explore-file-dock">
            <button class="explore-file-dock-open" data-testid="explore-files-float" type="button" :disabled="!state.session || state.loadingSession" @click="openFiles"><span>文件池 <Transition name="explore-count" mode="out-in"><strong :key="sessionFiles.length">{{ sessionFiles.length }}</strong></Transition></span></button>
          </div>
          <button v-show="!state.historyOpen" ref="historyToggle" class="explore-history-toggle" data-testid="explore-history-toggle" type="button" :aria-expanded="state.historyOpen" @click="toggleHistory">研究历史</button>
        </div>
      </header>

      <Transition name="explore-save-notice"><div v-if="saveNoticeVisible && newFiles.length && state.session && !state.loadingSession" class="explore-file-arrivals" data-testid="explore-file-arrivals" aria-live="polite" @pointerenter="holdNotice('pointer')" @pointerleave="releaseNotice('pointer', $event)" @focusin="holdNotice('focus')" @focusout="releaseNotice('focus', $event)">
        <button class="explore-file-arrivals-toggle" data-testid="explore-file-arrivals-toggle" type="button" :aria-expanded="newFilesExpanded" @click="newFilesExpanded = !newFilesExpanded"><span>已保存 {{ newFiles.length }} 个新文件</span><span aria-hidden="true">{{ newFilesExpanded ? '−' : '+' }}</span></button>
        <TransitionGroup v-if="newFilesExpanded" name="explore-file-arrival" tag="ul" class="explore-file-arrivals-list" tabindex="0" aria-label="本次新增文件"><li v-for="file in newFiles" :key="file.id" :data-file-id="file.id" data-testid="explore-file-arrival"><button type="button" @click="openFilesAt(file.id)"><strong>{{ file.name }}</strong><small>{{ fileKindLabel(file.kind) }}</small><span aria-hidden="true">查看 ↗</span></button></li></TransitionGroup>
      </div>
      </Transition>
      <div v-if="backgroundCompletion" class="explore-background-result" role="status">另一条研究{{ backgroundCompletion.status === 'succeeded' ? '已完成' : backgroundCompletion.status === 'partial' ? '部分完成' : '已结束' }}<button type="button" @click="selectRun(backgroundCompletion.id); backgroundCompletion = null">查看记录</button><button type="button" aria-label="关闭提醒" @click="backgroundCompletion = null">×</button></div>
      <div v-if="state.error" class="explore-error" role="alert">{{ state.error }}</div>
      <div v-if="state.actionError" class="explore-error" role="alert">{{ state.actionError }}</div>

      <main ref="reader" class="explore-scroll" id="explore-scroll" @scroll.passive="rememberReadingPosition">
        <div v-if="state.loading || state.loadingSession" class="explore-loading" role="status">正在读取已保存的探索…</div>
        <div v-else-if="!selectedRun" class="explore-welcome" :class="{ 'submit-leaving': submitPending }">
          <p class="explore-overline">EXPLORE · RESEARCH WORKSPACE</p>
          <h2>看懂事件，<br />发现下一步。</h2>
          <p class="explore-welcome-copy">从一个问题出发，理解变化、连接线索，让每一次探索都有据可循。</p>
        </div>
        <article v-else class="explore-document" :class="{ 'report-arriving': reportArriving, 'report-switching': reportSwitching }">
          <header ref="reportHead" class="explore-report-head" :class="{ 'head-visible': headVisible }">
            <div class="explore-document-meta"><span>{{ moduleTitle(selectedRun.module) }}</span><span :class="'state-' + selectedRun.status">{{ statusTitle }}</span></div>
            <h2>{{ selectedReport?.title || selectedRun.question }}</h2>
            <div class="explore-report-tools"><button v-if="drawerMaterials.length" class="explore-materials-link" data-testid="explore-open-materials" type="button" @click="openMaterials">材料与来源 · {{ materials.length || drawerMaterials.length }} ↗</button>
            </div>
          </header>
          <section v-if="isActive" class="explore-progress" data-testid="explore-progress" role="status" aria-live="polite">
            <div v-if="isActive" class="explore-progress-current">
              <span class="explore-section-kicker">正在研究</span>
              <div class="explore-stage-heading"><h3 :key="progressHeading"><span class="explore-progress-pulse" aria-hidden="true" />{{ progressHeading }}</h3><button class="secondary-button" data-testid="explore-cancel" type="button" :disabled="state.actionBusy" @click="cancel">取消分析</button></div>
              <div v-if="progressFacts.length" class="explore-progress-facts"><span v-for="fact in progressFacts" :key="fact">{{ fact }}</span></div>
              <p v-if="!materials.length">等待本次研究的材料与阶段更新。</p>
              <TransitionGroup v-else name="explore-event" tag="ul" class="explore-progress-material-list" aria-label="当前研究已取得的材料"><li v-for="item in materials.slice(-3)" :key="item.id"><button type="button" @click="focusSource(item.id)"><span>{{ item.id }}</span><strong>{{ sourceLabel(item) }}</strong><small>{{ materialContentLabel(item.content_kind) }} ↗</small></button></li></TransitionGroup>
              <TransitionGroup name="explore-event" tag="ol" class="explore-recent-events"><li v-for="item in recentActivity" :key="item.key"><span class="explore-stage-mark" aria-hidden="true">·</span><span>{{ item.title }}<small v-if="item.statusLabel"> · {{ item.statusLabel }}</small></span><small>{{ item.durationLabel || displayTime(item.at) }}</small></li></TransitionGroup>
              <div class="explore-progress-actions"><button v-if="materials.length" type="button" data-testid="explore-progress-materials" @click="openMaterials">查看全部 {{ materials.length }} 条材料 ↗</button></div>
            </div>
            <details class="explore-process-details"><summary>{{ isActive ? '查看完整过程' : '研究过程 · 已结束' }}</summary><p v-if="progressQuestion">已理解的问题：{{ progressQuestion }}</p><p v-if="selectedRun.model_progress">模型输出：{{ modelOutputChars ? modelOutputChars + ' 字符' : '尚未取得输出计数' }}（字符数不是完成比例）</p><ol><li v-for="item in runActivity" :key="item.key"><span>{{ item.title }} {{ item.statusLabel }}</span><small>{{ item.durationLabel }} {{ displayTime(item.at) }}</small></li></ol><details v-if="selectedRun.retrieval?.tool_trace?.length || selectedRun.retrieval?.model_trace?.length"><summary>阶段记录</summary><ol><li v-for="item in selectedRun.stage_history || []" :key="item.stage + item.at"><span>{{ historyStageLabel(item.stage, false) }}</span><small>{{ displayTime(item.at) }}</small></li></ol></details></details>
          </section>
          <section v-if="reviewPending" class="explore-awaiting explore-review-card" :data-testid="selectedRun.status === 'awaiting_clarification' ? 'explore-clarification' : 'explore-review'">
            <span class="explore-section-kicker">{{ selectedRun.status === 'awaiting_clarification' ? '资料已找到 · 请明确研究方向' : '资料已找到 · 等待材料确认' }}</span>
            <h3>{{ selectedRun.status === 'awaiting_clarification' ? '我们找到这些材料，但还需确认你的问题' : '请确认研究问题与材料' }}</h3>
            <p v-if="selectedRun.review?.summary">{{ selectedRun.review.summary }}</p>
            <p v-if="selectedRun.review?.interpreted_question"><strong>当前理解：</strong>{{ selectedRun.review.interpreted_question }}</p>
            <p v-if="selectedRun.review?.question && selectedRun.status === 'awaiting_clarification'"><strong>需要明确：</strong>{{ selectedRun.review.question }}</p>
            <ul v-if="selectedRun.review?.limitations?.length" class="explore-review-limitations"><li v-for="(item, index) in selectedRun.review.limitations" :key="index">{{ item }}</li></ul>
            <div class="explore-review-evidence">
              <div class="explore-review-evidence-head"><strong>本次找到的材料</strong><span>{{ materials.length }} 条<span v-if="selectedRun.status === 'awaiting_review'"> · {{ state.selectedSourceIds.length }} 条拟用于报告</span></span></div>
              <p v-if="!materials.length">本次没有可供选择的材料，无法生成报告。可编辑问题重新检索。</p>
              <ul v-else><li v-for="item in materials.slice(0, 4)" :key="item.id"><span>{{ item.id }}</span><strong>{{ sourceLabel(item) }}</strong><small>{{ sourceKindLabel(item.kind) }}</small></li></ul>
              <p v-if="materials.length > 4">另有 {{ materials.length - 4 }} 条，请打开材料窗口查看。</p>
            </div>
            <div v-if="selectedRun.status === 'awaiting_clarification' && selectedRun.review?.options?.length" class="explore-review-options">
              <p>选择一个研究方向后会直接检索并生成报告，无需再次确认材料。</p>
              <button v-for="option in selectedRun.review.options.slice(0, 3)" :key="option.id" data-testid="explore-review-option" type="button" :disabled="state.actionBusy" @click="clarify(option.id)"><strong>{{ option.label || option.question }}</strong><span>{{ option.question }}</span><small>{{ option.reason }}</small><em v-if="option.source_refs?.length">依据 {{ option.source_refs.join('、') }}</em></button>
            </div>
            <label for="explore-review-question">{{ selectedRun.status === 'awaiting_clarification' ? '以上都不是 / 补充你的需求' : '修改研究问题' }}</label>
            <textarea id="explore-review-question" v-model="state.reviewQuestion" data-testid="explore-clarification-input" rows="3" maxlength="1000" :placeholder="selectedRun.status === 'awaiting_clarification' ? '自行输入具体要研究的事件、关系或待验证判断…' : '修改后重新检索…'" />
            <div class="explore-action-row">
              <button v-if="selectedRun.status === 'awaiting_clarification'" class="secondary-button" data-testid="explore-clarify" type="button" :disabled="!state.reviewQuestion.trim() || state.actionBusy" @click="clarify()">按自定义问题继续</button>
              <template v-else>
                <button class="primary-button" data-testid="explore-confirm-materials" type="button" :disabled="!canConfirmMaterials" @click="confirmMaterials">确认材料并生成报告</button>
                <button class="secondary-button" data-testid="explore-clarify" type="button" :disabled="!state.reviewQuestion.trim() || state.reviewQuestion.trim() === initialReviewQuestion || state.actionBusy" @click="clarify()">修改问题并重新检索</button>
              </template>
              <button class="secondary-button" data-testid="explore-cancel" type="button" :disabled="state.actionBusy" @click="cancel">取消本次分析</button>
            </div>
            <p v-if="selectedRun.status === 'awaiting_review'" class="explore-review-hint">请先打开上方材料窗口核对摘录与来源，可取消勾选不适合用于报告的材料。全部取消后不能生成报告；修改问题后请先重新检索。</p>
          </section>

          <section v-if="!selectedReport && !isActive && selectedRun.retrieval" class="explore-report-section" data-testid="explore-retrieval"><span class="explore-section-kicker">RETRIEVAL</span><h3>本次检索</h3><p class="explore-source-note">记录本次实际搜索的关键词、时间范围和结果；搜索摘要与快讯摘录不等于全文，检索完成不代表内容已证实。</p><p v-if="!retrievalSearches(selectedRun).length">本次没有保存联网搜索记录。</p><ul v-else class="explore-searches"><li v-for="(search, index) in retrievalSearches(selectedRun)" :key="index"><div class="explore-search-heading"><strong>{{ search.query || '搜索词未知' }}</strong><span :class="'state-' + search.status">{{ searchStatusLabel(search.status) }}</span></div><small>范围：{{ searchFreshnessLabel(search.freshness) }} · 搜索：{{ displayTime(search.searched_at) }} · 返回：{{ Number.isInteger(search.count) && search.count >= 0 ? search.count + ' 条' : '未知' }}</small><p v-if="['failed', 'error'].includes(search.status)">本次检索未成功，可查看已取得的其他材料。</p></li></ul></section>

          <section v-if="selectedRun.status === 'awaiting_input'" class="explore-awaiting"><span class="explore-section-kicker">判断确认</span><h3>先确认要验证的判断</h3><p>当前输入只描述了事件。请修改下方判断，确认后才会检索历史材料并调用模型。</p><label for="explore-judgment">待验证判断</label><textarea id="explore-judgment" v-model="state.judgment" data-testid="explore-judgment" rows="3" maxlength="5000" /><div class="explore-action-row"><button class="primary-button" data-testid="explore-confirm-judgment" type="button" :disabled="!state.judgment.trim() || state.actionBusy" @click="confirmJudgment">确认并开始验证</button><button class="secondary-button" type="button" :disabled="state.actionBusy" @click="cancel">取消本次分析</button></div></section>

          <section v-if="['failed', 'cancelled', 'interrupted'].includes(selectedRun.status)" class="explore-result-error"><h3>{{ statusTitle }}</h3><p>{{ selectedRun.error || (selectedRun.status === 'interrupted' ? '服务进程中断。此记录不会自动重跑。' : '本次没有生成可交付报告。') }}</p><button class="secondary-button" data-testid="explore-retry" type="button" :disabled="state.actionBusy" @click="retry">新建记录并重试</button></section>

          <div v-if="selectedReport" class="explore-report-body" data-testid="explore-report">
            <section class="explore-report-section explore-summary" data-testid="explore-summary"><span class="explore-section-kicker">研究发现</span><button v-if="!briefReport" class="explore-analysis-jump" type="button" @click="jumpToAnalysis">阅读正文 ↓</button><h3>核心发现</h3><div v-if="!briefReport" class="explore-markdown" v-html="safeMarkdown(selectedReport.summary)" /><ul v-if="selectedReport.key_findings?.length" class="explore-finding-list"><li v-for="(finding, index) in selectedReport.key_findings" :key="index"><span>0{{ index + 1 }}</span><div v-html="safeMarkdown(finding)" /></li></ul><p v-if="briefCoverageNote" class="explore-source-note" data-testid="explore-event-gap">{{ briefCoverageNote }}</p></section>
            <section v-if="!briefReport" class="explore-report-section explore-boundary" data-testid="explore-uncertainties"><h3>重要不确定性</h3><ul v-if="selectedReport.uncertainties?.length" class="explore-simple-list"><li v-for="(item, index) in selectedReport.uncertainties" :key="index" v-html="safeMarkdown(item)" /></ul><p v-else>报告未单列重要不确定性；请结合信息缺口阅读。</p></section>
            <section v-if="!briefReport" class="explore-report-section explore-boundary explore-limitations" data-testid="explore-limitations"><h3>信息缺口与限制</h3><ul v-if="selectedReport.limitations?.length" class="explore-simple-list"><li v-for="(item, index) in selectedReport.limitations" :key="index" v-html="safeMarkdown(item)" /></ul><p v-else>报告未提供额外限制说明。</p></section>
            <section v-if="!briefReport && (selectedReport.event_overview || selectedReport.background || selectedReport.actors?.length || selectedReport.drivers?.length || selectedReport.impact_paths?.length)" class="explore-report-section"><span class="explore-section-kicker">事件解读</span><h3>事件、背景与影响路径</h3><div v-if="selectedReport.event_overview" class="explore-detail-block"><h4>事件概述</h4><div class="explore-markdown" v-html="safeMarkdown(selectedReport.event_overview)" /></div><div v-if="selectedReport.background" class="explore-detail-block"><h4>背景</h4><div class="explore-markdown" v-html="safeMarkdown(selectedReport.background)" /></div><div v-if="selectedReport.actors?.length" class="explore-detail-block"><h4>重要主体</h4><ul class="explore-simple-list"><li v-for="(actor, index) in selectedReport.actors" :key="index" v-html="safeMarkdown(actor)" /></ul></div><div v-if="selectedReport.drivers?.length" class="explore-detail-block"><h4>动因与解释</h4><ul class="explore-simple-list"><li v-for="(driver, index) in selectedReport.drivers" :key="index" v-html="safeMarkdown(driver)" /></ul></div><div v-if="selectedReport.impact_paths?.length" class="explore-detail-block"><h4>可能的影响路径</h4><ul class="explore-simple-list"><li v-for="(path, index) in selectedReport.impact_paths" :key="index" v-html="safeMarkdown(path)" /></ul></div></section>
            <section v-if="!briefReport && selectedReport.entities?.length" class="explore-report-section"><span class="explore-section-kicker">研究主体</span><h3>涉及的主体</h3><ul class="explore-simple-list"><li v-for="(entity, index) in selectedReport.entities" :key="index" v-html="safeMarkdown(entity)" /></ul></section>
            <section v-if="!briefReport && selectedReport.timeline?.length" class="explore-report-section"><span class="explore-section-kicker">事件脉络</span><h3>时间线</h3><ol class="explore-timeline"><li v-for="(item, index) in selectedReport.timeline" :key="index"><time :title="item.time || ''">{{ item.time ? displayTime(item.time) : '事件时间未知' }}</time><div><p>{{ item.event }} <ExploreSourceChip :ids="item.source_refs" :sources="reportSources(selectedReport)" @open="openCitationSources" /></p></div></li></ol></section>
            <section v-if="!briefReport && selectedReport.relations?.length" class="explore-report-section"><span class="explore-section-kicker">关系路径</span><h3>重要关联</h3><div class="explore-relation-layout"><div class="explore-relation-list"><button v-for="(item, index) in selectedReport.relations" :key="index" type="button" :class="{ selected: index === selectedRelationIndex }" @click="selectedRelationIndex = index"><strong>{{ item.from || '未知主体' }} → {{ item.to || '未知主体' }}</strong><small>{{ item.relation_type || item.direction || '关系待确认' }}</small></button></div><div v-if="selectedRelation" class="explore-relation-detail"><h4>{{ selectedRelation.from }} → {{ selectedRelation.to }}</h4><dl><dt>关系类型与方向</dt><dd>{{ selectedRelation.relation_type || '未知' }} · {{ selectedRelation.direction || '未知' }}</dd><dt>影响机制</dt><dd>{{ plain(selectedRelation.mechanism) || '未说明' }}</dd><dt>成立条件</dt><dd>{{ plain(selectedRelation.conditions) || '未说明' }} <ExploreSourceChip :ids="selectedRelation.source_refs" :sources="reportSources(selectedReport)" @open="openCitationSources" /></dd></dl></div></div></section>
            <section v-if="!briefReport && (selectedReport.cases?.length || selectedReport.judgment || selectedReport.conclusion)" class="explore-report-section"><span class="explore-section-kicker">历史案例</span><h3>判断与案例对照</h3><p v-if="selectedReport.judgment" class="explore-judgment-quote">待验证判断：{{ selectedReport.judgment }}</p><p v-if="selectedReport.mode === 'qualitative_no_market_validation'" class="explore-qualitative">定性对照，未进行行情验证。</p><div v-for="(item, index) in selectedReport.cases || []" :key="index" class="explore-case"><h4>{{ item.event || '历史案例' }}</h4><dl><dt>相似点</dt><dd>{{ plain(item.similarities) || '未说明' }}</dd><dt>关键差异</dt><dd>{{ plain(item.differences) || '未说明' }}</dd><dt>可能支持的条件</dt><dd>{{ plain(item.supporting_conditions) || '未说明' }}</dd><dt>可能失效的条件</dt><dd>{{ plain(item.failure_conditions) || '未说明' }} <ExploreSourceChip :ids="item.source_refs" :sources="reportSources(selectedReport)" @open="openCitationSources" /></dd></dl></div><div v-if="selectedReport.conclusion" class="explore-detail-block"><h4>对当前判断的结论</h4><div class="explore-markdown" v-html="safeMarkdown(selectedReport.conclusion)" /></div></section>
            <section v-if="!briefReport && selectedReport.method_notes" class="explore-report-section"><h3>方法与适用范围</h3><div class="explore-markdown" v-html="safeMarkdown(selectedReport.method_notes)" /></section>
            <section v-for="(section, index) in selectedReport.sections || []" :key="index" class="explore-report-section"><span class="explore-section-kicker">{{ briefReport ? '本轮核实' : `${String(index + 3).padStart(2, '0')} · 模块分析` }}</span><h3>{{ section.heading }}</h3><div class="explore-markdown explore-cited-prose"><div class="explore-cited-prose-body" v-html="safeMarkdown(section.body)" /><ExploreSourceChip :ids="section.source_refs" :sources="reportSources(selectedReport)" @open="openCitationSources" /></div></section>
            <ExploreRecommendations v-if="briefReport && selectedReport.recommendations?.length" :recommendations="selectedReport.recommendations" :report-id="selectedReport.id" :can-start-run="canStartRun" @run="runRecommendation" />

            <details class="explore-report-section explore-retrieval-details" data-testid="explore-retrieval"><summary>检索详情</summary><p class="explore-source-note">记录本次实际使用的搜索词、范围与返回数量；检索完成不代表资料内容已经证实。</p><p v-if="!selectedReport.retrieval && !selectedRun.retrieval">这份报告未保存联网搜索过程记录。</p><p v-else-if="!retrievalSearches(selectedReport.retrieval ? selectedReport : selectedRun).length">本次没有保存联网搜索记录。</p><ul v-else class="explore-searches"><li v-for="(search, index) in retrievalSearches(selectedReport.retrieval ? selectedReport : selectedRun)" :key="index"><div class="explore-search-heading"><strong>{{ search.query || '搜索词未知' }}</strong><span :class="'state-' + search.status">{{ searchStatusLabel(search.status) }}</span></div><small>范围：{{ searchFreshnessLabel(search.freshness) }} · 搜索：{{ displayTime(search.searched_at) }} · 返回：{{ Number.isInteger(search.count) && search.count >= 0 ? search.count + ' 条' : '未知' }}</small><p v-if="['failed', 'error'].includes(search.status)">本次检索未成功，可查看已取得的其他材料。</p></li></ul></details>
            <section class="explore-report-section" data-testid="explore-sources"><span class="explore-section-kicker">EVIDENCE</span><h3>本次来源</h3><p class="explore-source-note">以下是本次运行实际保存的材料；发布时间未知时会明确标注，不能由检索时间推断资料已验证。</p><p v-if="!reportSources(selectedReport).length">本报告没有可展示的外部来源。</p><ol v-else class="explore-sources"><li v-for="source in reportSources(selectedReport)" :id="sourceDomId(source.id)" :key="source.id"><span class="explore-source-number">{{ source.id }}</span><div><strong>{{ sourceLabel(source) }}</strong><p>{{ source.summary || '未提供摘录' }}</p><small>类型：{{ sourceKindLabel(source.kind) }} · 提供方：{{ source.source_name || source.source || '未知' }} · 发布时间：{{ displayTime(source.published_at) }} · 检索时间：{{ displayTime(source.retrieved_at) }}</small><a v-if="safeSourceUrl(source.url)" :href="safeSourceUrl(source.url)" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a><span v-else class="explore-source-unavailable">无可安全打开的原始链接</span></div></li></ol></section>
            <ExploreRecommendations v-if="!briefReport && selectedReport.recommendations?.length" :recommendations="selectedReport.recommendations" :report-id="selectedReport.id" :can-start-run="canStartRun" @run="runRecommendation" />
            <div class="explore-report-actions"><button class="secondary-button" type="button" @click="useReportAsSource">基于这份报告继续</button><button class="secondary-button" data-testid="explore-regenerate" type="button" :disabled="state.actionBusy" @click="regenerate">重新生成</button><button class="primary-button" data-testid="explore-download" type="button" :disabled="state.actionBusy" @click="download">下载 Markdown</button></div>
          </div>
          <details v-if="selectedRun.context_selection || contextFiles(selectedRun).length || selectedRun.context_limitations?.length" class="explore-report-section explore-used-context" data-testid="explore-context-used"><summary>本次实际使用的文件 · {{ contextFiles(selectedRun).length }}</summary>
            <span class="explore-section-kicker">RESEARCH CONTEXT · 创建时快照</span><h3>本次实际使用的文件</h3>
            <p>方式：{{ selectedRun.context_selection?.mode === 'manual' ? '自选文件' : '自动沿当前报告分支' }} · {{ contextFiles(selectedRun).length }} 个文件</p>
            <p v-if="!contextFiles(selectedRun).length">本次未加入文件池中的材料。联网检索与本次材料另见下方记录。</p>
            <ul v-else class="explore-context-used-list"><li v-for="item in contextFiles(selectedRun)" :key="item.file_id"><strong>{{ item.name || item.file_id }}</strong><small>{{ fileKindLabel(item.kind) }} · {{ fileOriginLabel(item.origin) }} · 实际使用 {{ item.used_chars ?? '未知' }} / {{ item.original_chars ?? '未知' }} 字<span v-if="item.truncated"> · 已截断</span></small><p v-if="item.reason">{{ item.reason }}</p><pre v-if="item.content_excerpt" tabindex="0">{{ item.content_excerpt }}</pre></li></ul>
            <ul v-if="selectedRun.context_limitations?.length" class="explore-review-limitations"><li v-for="(item, index) in selectedRun.context_limitations" :key="index">{{ item }}</li></ul>
          </details>
        </article>
      </main>

      <div class="explore-compose-zone" @beforeinput="stopComposerMotion" @compositionstart="stopComposerMotion">
        <ExploreComposer ref="input" :state="state" :is-active="isActive" :can-submit="canSubmit" :current-context="currentContext" :selected-context-count="selectedContextFiles.length" :motion-paused="effectivePaused" :scene="scene" @submit="submitWithFeedback" @open-files="openFiles" @update:module="state.module = $event" @update:draft="state.draft = $event" />
        <div v-if="state.contextError" class="explore-context-error" role="alert">{{ state.contextError }} <button v-if="!state.contextLoaded && state.session" type="button" @click="loadContexts()">重试读取文件选择</button></div>
      </div>
      <div v-if="!selectedRun && !state.loading && !state.loadingSession" class="explore-entry-suggestions" :class="{ 'submit-leaving': submitPending }" data-testid="explore-entry-suggestions"><div class="explore-example-pills"><button v-for="item in exampleQuestions" :key="item.id" :data-module="item.id" type="button" @click="chooseExample(item)">{{ item.label }} <span aria-hidden="true">↗</span></button></div><p>选择模板，补充你想研究的内容。</p></div>
    </div>
    <Teleport to="body">
      <div v-if="menuSession && !sessionActionOpen" ref="sessionMenu" class="explore-theme explore-session-menu" :style="sessionMenuStyle" data-testid="explore-session-menu" role="menu" :aria-label="'探索操作：' + (menuSession.title || '未命名探索')" @keydown="onSessionMenuKeydown">
        <button data-testid="explore-session-rename" role="menuitem" type="button" @click="openSessionAction('rename')">重命名</button>
        <button data-testid="explore-session-delete" role="menuitem" type="button" @click="openSessionAction('delete')">删除</button>
      </div>
      <div v-if="sessionActionModalOpen" class="explore-theme explore-session-overlay" @click.self="cancelSessionDialog">
        <section ref="sessionDialog" class="explore-session-dialog" :data-testid="state.renameOpen ? 'explore-rename-dialog' : 'explore-delete-dialog'" :role="state.deleteOpen ? 'alertdialog' : 'dialog'" aria-modal="true" :aria-labelledby="state.renameOpen ? 'explore-rename-title' : 'explore-delete-title'" @keydown="onSessionDialogKeydown">
          <button class="explore-session-dialog-close" data-testid="explore-session-dialog-close" type="button" aria-label="关闭" :disabled="state.actionBusy" @click="cancelSessionDialog">×</button>
          <template v-if="state.renameOpen">
            <h2 id="explore-rename-title">重命名探索</h2>
            <p>为这次探索取一个容易识别的名字。</p>
            <label for="explore-rename-input">探索名称</label>
            <input id="explore-rename-input" ref="renameInput" v-model="state.renameDraft" data-testid="explore-rename-input" type="text" maxlength="120" autocomplete="off" :disabled="state.actionBusy" @compositionstart="onRenameCompositionStart" @compositionend="onRenameCompositionEnd" @keydown="onRenameKeydown" />
          </template>
          <template v-else>
            <h2 id="explore-delete-title">删除探索</h2>
            <p>确定删除“{{ state.deleteSessionTitle || '未命名探索' }}”？这会删除该会话及其中的研究记录和文件，且无法恢复。</p>
          </template>
          <p v-if="state.sessionActionError" class="explore-session-dialog-error" role="alert">{{ state.sessionActionError }}</p>
          <footer class="explore-session-dialog-actions">
            <button ref="deleteCancel" data-testid="explore-session-dialog-cancel" type="button" :disabled="state.actionBusy" @click="cancelSessionDialog">取消</button>
            <button v-if="state.renameOpen" data-testid="explore-rename-save" class="explore-session-dialog-primary" type="button" :disabled="state.actionBusy || !state.renameDraft.trim()" @click="rename">{{ state.actionBusy ? '保存中…' : '保存' }}</button>
            <button v-else data-testid="explore-delete-confirm" class="explore-session-dialog-danger" type="button" :disabled="state.actionBusy" @click="removeSession">{{ state.actionBusy ? '删除中…' : '删除探索' }}</button>
          </footer>
        </section>
      </div>
      <Transition name="explore-drawer"><div v-if="materialsOpen" class="explore-theme explore-materials-overlay" :class="{ 'motion-paused': effectivePaused }" @click.self="closeMaterials">
        <section ref="materialsDialog" class="explore-materials-dialog" data-testid="explore-materials" role="dialog" aria-modal="true" aria-labelledby="explore-materials-title" @keydown="onMaterialsKeydown">
          <header class="explore-materials-head"><div><span class="explore-overline">SOURCE REVIEW</span><h2 id="explore-materials-title">{{ citationSourceIds.length ? '本段来源' : '本次检索材料' }}</h2></div><button ref="materialsClose" class="explore-materials-close" data-testid="explore-materials-close" type="button" aria-label="关闭材料窗口" @click="closeMaterials">×</button></header>
          <p class="explore-materials-intro">以下是本次检索保存、用于问题判断的材料文本；报告实际引用的材料以已保存来源记录为准。搜索摘要和快讯摘录不是全文，发布时间未知时不会用检索时间代替。</p>
          <p v-if="selectedRun?.status === 'awaiting_review'" class="explore-materials-count">已选择 {{ state.selectedSourceIds.length }} / {{ materials.length }} 条材料。取消不适合用于报告的材料后，关闭窗口并确认。</p>
          <div v-else-if="citationSourceIds.length" class="explore-citation-scope"><span>本段引用 {{ citationSourceIds.length }} 条来源</span><button type="button" data-testid="explore-all-materials" @click="showAllMaterials">查看全部材料 ↗</button></div>
          <p v-else-if="selectedReport" class="explore-materials-count">报告记录使用 {{ confirmedMaterials.length }} 条材料；此处为只读记录。</p>
          <div class="explore-materials-scroll">
            <article v-for="item in visibleDrawerMaterials" :id="`drawer-${sourceDomId(item.id)}`" :key="item.id" class="explore-material-card" :class="{ 'source-highlight': highlightedSource === String(item.id) }">
              <div class="explore-material-title"><label v-if="selectedRun?.status === 'awaiting_review'"><input data-testid="explore-materials-select" type="checkbox" :aria-label="'用于报告：' + sourceLabel(item)" :checked="state.selectedSourceIds.includes(String(item.id))" @change="toggleMaterial(item.id, $event.target.checked)" /><span class="explore-source-number">{{ item.id }}</span></label><span v-else class="explore-source-number">{{ item.id }}<span v-if="confirmedMaterials.includes(String(item.id))"> · 已用于报告</span></span><h3>{{ sourceLabel(item) }}</h3></div>
              <p class="explore-material-kind">{{ sourceKindLabel(item.kind) }} · {{ materialContentLabel(item.content_kind) }}</p>
              <p class="explore-material-meta">提供方：{{ item.source_name || item.source || '未知' }} · 发布时间：{{ displayTime(item.published_at) }} · 检索时间：{{ displayTime(item.retrieved_at) }}</p>
              <a v-if="safeSourceUrl(item.url)" :href="safeSourceUrl(item.url)" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a><span v-else class="explore-source-unavailable">无可安全打开的原始链接</span>
              <div v-if="sourceSupports(selectedReport, item.id).length" class="explore-source-supports"><div v-for="support in sourceSupports(selectedReport, item.id)" :key="support.key"><h4>研究记录中的关联主张</h4><p>{{ support.claimText }}</p><small>{{ claimTypeLabel(support.claimType) }} · {{ claimSupportLabel(support.supportStatus) }}；关联不代表已被报告采用</small><span v-if="support.backgroundOnly">仅作为背景材料</span><blockquote v-if="support.quote">{{ support.quote }}</blockquote><small v-else>未保存精确引用片段；以下为已保存的使用文本。</small></div></div>
              <p v-else-if="selectedReport" class="explore-source-note">未保存句子级引用定位；此处展示该来源的已保存文本范围。</p>
              <div class="explore-material-text-label">{{ item.report_used_text ? '报告保存的使用文本' : '本次检索保存的文本' }}<span v-if="item.evidence_usage === 'retrieved_only'"> · 未用于本报告</span></div>
              <pre class="explore-material-text" tabindex="0" :aria-label="'材料 ' + item.id + ' 的已保存文本'">{{ item.text_used || '未保存可展示的材料文本' }}</pre>
              <details v-if="item.retrieved_text && item.retrieved_text !== item.text_used"><summary>查看检索时保存的材料</summary><pre class="explore-material-text" tabindex="0">{{ item.retrieved_text }}</pre></details>
            </article>
          </div>
          <footer class="explore-materials-foot"><button class="primary-button" type="button" @click="closeMaterials">{{ selectedRun?.status === 'awaiting_review' ? '完成材料选择' : '关闭' }}</button></footer>
        </section>
      </div>
      </Transition>
      <Transition name="explore-drawer"><div v-if="filesOpen" class="explore-theme explore-materials-overlay" :class="{ 'motion-paused': effectivePaused }" @click.self="closeFiles">
        <section ref="filesDialog" class="explore-materials-dialog explore-files-dialog" data-testid="explore-files-dialog" role="dialog" aria-modal="true" aria-labelledby="explore-files-title" @keydown="onFilesKeydown">
          <header class="explore-materials-head"><div><span class="explore-overline">SESSION FILES</span><h2 id="explore-files-title">当前探索的文件池</h2></div><button ref="filesClose" class="explore-materials-close" data-testid="explore-files-close" type="button" aria-label="关闭文件池" @click="closeFiles">×</button></header>
          <p v-if="newFiles.length" class="explore-materials-count">本次访问已新增 {{ newFiles.length }} 个文件，最新：{{ newFiles[0]?.name }}</p>
          <div class="explore-context-bar" data-testid="explore-context-control">
            <strong>{{ moduleTitle(state.module) }} · 参考材料</strong>
            <div class="explore-context-mode" role="group" aria-label="选择文件上下文方式"><button type="button" data-testid="explore-context-auto" :aria-pressed="currentContext.mode === 'auto'" :class="{ active: currentContext.mode === 'auto' }" :disabled="state.contextBusy || !state.contextLoaded || state.submitting" @click="setContextMode('auto')">自动</button><button type="button" data-testid="explore-context-manual" :aria-pressed="currentContext.mode === 'manual'" :class="{ active: currentContext.mode === 'manual' }" :disabled="state.contextBusy || !state.contextLoaded || state.submitting" @click="setContextMode('manual')">自选</button></div>
            <span v-if="currentContext.mode === 'manual'">已选 {{ selectedContextFiles.length }} / {{ maxContextFiles }} 个</span><span v-else>沿当前报告分支 · 预计 {{ Number.isInteger(state.contextPreview?.preview_files?.length) ? state.contextPreview.preview_files.length + ' 个' : '数量待确认' }}</span>
            <button v-if="contextDirty" data-testid="explore-context-save" type="button" :disabled="state.contextBusy || state.submitting || !state.contextLoaded" @click="saveContext">保存选择</button>
          </div>
          <p class="explore-materials-intro">三个模块共享当前会话的材料与报告。搜索摘要、快讯摘录和模型报告各有明确标注；“自选”最多使用 {{ maxContextFiles }} 个文件。重命名和删除只影响今后的选择，旧报告的使用快照会保留。</p>
          <div v-if="currentContext.mode === 'auto'" class="explore-auto-preview" data-testid="explore-auto-preview"><details v-if="state.contextPreview?.preview_files?.length"><summary>自动预计使用 {{ state.contextPreview.preview_files.length }} 个文件 · 查看清单</summary><ul><li v-for="file in state.contextPreview.preview_files" :key="file.id || file.file_id"><strong>{{ file.name || file.id || file.file_id }}</strong><small v-if="file.reason">{{ file.reason }}</small></li></ul></details><p v-else>自动模式：当前分支暂无可预览文件。</p></div>
          <div v-if="state.fileError" class="explore-files-error" role="alert">{{ state.fileError }}</div>
          <div class="explore-files-body">
            <div class="explore-files-list" data-testid="explore-file-list">
              <p v-if="state.filesLoading" role="status">正在读取文件…</p><p v-else-if="!state.files.length">当前会话还没有保存材料或报告。</p>
              <article v-for="file in state.files" :key="file.id" class="explore-file-item" :data-file-id="file.id" :class="{ selected: String(state.fileDetail?.id) === String(file.id) }">
                <label v-if="currentContext.mode === 'manual'" class="explore-file-checkbox"><input type="checkbox" data-testid="explore-file-select" :aria-label="'加入' + moduleTitle(state.module) + '：' + file.name" :checked="currentContext.file_ids.includes(String(file.id))" :disabled="state.contextBusy || state.contextLoading || !state.contextLoaded || state.fileBusy || state.submitting" @change="toggleContextFile(file.id, $event.target.checked)" /></label>
                <div><strong>{{ file.name || '未命名文件' }}</strong><small>{{ fileKindLabel(file.kind) }} · {{ fileOriginLabel(file.origin) }} · {{ file.size_chars ?? '未知' }} 字</small></div>
                <button type="button" data-testid="explore-file-view" :disabled="state.fileLoading" @click="openFile(file.id)">查看</button>
              </article>
            </div>
            <div class="explore-files-detail" data-testid="explore-file-detail"><p v-if="state.fileLoading" role="status">正在读取文件正文…</p><p v-else-if="!state.fileDetail">选择文件查看正文、原始链接或管理文件。</p><template v-else>
              <h3>{{ state.fileDetail.name }}</h3><p class="explore-material-kind">{{ fileKindLabel(state.fileDetail.kind) }} · {{ fileOriginLabel(state.fileDetail.origin) }} · {{ state.fileDetail.kind === 'report' ? 'AI 生成报告，并非原始来源' : materialContentLabel(state.fileDetail.content_kind) }}</p>
              <p class="explore-material-meta">发布时间：{{ displayTime(state.fileDetail.published_at) }} · 检索时间：{{ displayTime(state.fileDetail.retrieved_at) }}</p>
              <a v-if="safeSourceUrl(state.fileDetail.url)" :href="safeSourceUrl(state.fileDetail.url)" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a><span v-else class="explore-source-unavailable">无可安全打开的原始链接</span>
              <div class="explore-material-text-label">保存的文件正文</div><pre class="explore-material-text explore-file-content" tabindex="0">{{ state.fileDetail.content || '文件正文为空' }}</pre>
              <div class="explore-file-operations"><label for="explore-file-name">文件名称</label><input id="explore-file-name" v-model="state.fileNameDraft" maxlength="120" /><button data-testid="explore-file-rename" type="button" :disabled="state.fileBusy || !state.fileNameDraft.trim() || state.fileNameDraft.trim() === state.fileDetail.name" @click="renameFile">保存名称</button><button data-testid="explore-file-download" type="button" @click="downloadFile()">下载</button><button class="explore-file-danger" type="button" :disabled="state.fileBusy" @click="state.fileDeleteOpen = true">移除</button></div>
              <div v-if="state.fileDeleteOpen" class="explore-file-delete" role="alertdialog" aria-label="确认移除文件"><p>移除“{{ state.fileDetail.name }}”？它将从未来的模块自选中移除，旧分析的使用快照仍保留。</p><button data-testid="explore-file-remove" type="button" :disabled="state.fileBusy" @click="removeFile">确认移除</button><button type="button" @click="state.fileDeleteOpen = false">保留文件</button></div>
            </template></div>
          </div>
          <footer class="explore-materials-foot explore-files-foot"><span v-if="currentContext.mode === 'manual'">{{ moduleTitle(state.module) }}已选 {{ selectedContextFiles.length }} 个文件</span><span v-else>自动模式沿所选报告与必要祖先提取材料</span><button v-if="contextDirty" data-testid="explore-files-save-context" class="secondary-button" type="button" :disabled="state.contextBusy || state.fileBusy || !state.contextLoaded" @click="saveContext">保存本模块选择</button><button class="primary-button" data-testid="explore-files-done" type="button" :disabled="state.contextBusy || state.fileBusy" @click="finishFiles">完成</button></footer>
        </section>
      </div>
      </Transition>
    </Teleport>
  </section>
</template>
