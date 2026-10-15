<script setup>
import { computed, defineAsyncComponent, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import Icon from './components/Icon.vue';
import SidebarIcon from './components/SidebarIcon.vue';
import './pages/sidebar-fluent-icons.css';
import { USER_FACING_ERROR, userFacingError } from '../../shared/userFacingError.js';
import './auroraRose.css';
import Composer from './components/Composer.vue';
import SubAgentPanel from './components/SubAgentPanel.vue';
import { conversationMessages } from './subAgentView.js';
import HomeLanding from './components/HomeLanding.vue';
import CapabilityDialog from './components/CapabilityDialog.vue';
import SkillPoolPanel from './components/SkillPoolPanel.vue';
import AgentPlatformPanel from './components/AgentPlatformPanel.vue';
import RecipeLibraryPanel from './components/RecipeLibraryPanel.vue';
import AssetAuthoringPanel from './components/AssetAuthoringPanel.vue';
import AuthoringCard from './components/AuthoringCard.vue';
import AuthoringPanel from './components/AuthoringPanel.vue';
import AuthoringWorkspace from './components/AuthoringWorkspace.vue';
import AuthoringShelf from './components/AuthoringShelf.vue';
import AssetChatRun from './components/AssetChatRun.vue';
import { authoringAllowed, authoringTitle, firstAssetAppRun, sessionFromResponse } from './authoringModel.js';
import { authoringCreateRequest } from './authoringCreateRequest.js';
import CommunityPanel from './components/CommunityPanel.vue';
import AgentInputForm from './components/AgentInputForm.vue';
import SourcesPanel from './components/SourcesPanel.vue';
import FinancialDataPanel from './components/FinancialDataPanel.vue';
import { isDataManagementFile } from './dataFilesModel.js';
import { useWorkbench } from './useWorkbench.js';
import { fileLabel, fileIdentityHint } from './fileLabels.js';
import { accountDraftKey, clearDraft, readDraft, writeDraft } from './draftStore.js';
import { request } from './api.js';
import { prepareAssetHandoff, prepareStrategyHandoff, workshopRequirement } from '../app-workshop/handoff.js';
import { useCreatedAppNavigation } from './useCreatedAppNavigation.js';
const ExplorePanel = defineAsyncComponent(() => import('../explore/ExplorePanel.vue'));
const StrategyWorkshop = defineAsyncComponent(() => import('../strategy-workshop/StrategyWorkshop.vue'));
const AppWorkshop = defineAsyncComponent(() => import('../app-workshop/AppWorkshop.vue'));

const {
  subAgents,
  authoring,
  platform,
  s, notices, filePicker, composer, conversation, dialog, projectName, scheduleTitle, scheduleAt,
  agents, agent, model, project, recentTasks, searchResults, visibleFiles, sortedSchedules, attachedFiles,
  uploadCount, dataUploading, activePreviewFile, previewExtension, generationLabel, formatDate, relativeTime, fileSize,
  updateDraft, updateSearchMode, selectSkill, removeSkill, manualRefresh, setView, toggleSidebar, newTask, openTask, selectAgent, selectProject, selectModel,
  submitAuth, logout, showModal, closeModal, submitModal, toggleSchedule, confirmDelete,
  toggleAttachment, removeAttachment, attachDataFileInChat, chooseFiles, uploadFiles, downloadFile, previewFile, scrollConversation,
  restoreLastPrompt, copyText, messageContent, renderAssistant, sendMessage, stopMessage, setPrompt,
  snapshotCreationDraft, recoverCreationDraft, acceptCreationDraft, toast,
  onConversationScroll, publicSearchSources, publicSkillsUsed, publicToolsUsed,
} = useWorkbench();
const conversationItems = computed(() => conversationMessages(s.currentTask?.messages, subAgents.state.queue?.items));
const authoringStart = ref(null), portableRevision = ref(0), assetRevision = ref(0);
const createMode = ref(false), creationBusy = ref(false), creationFailed = ref(false), creationSessionId = ref(''), creationContextId = ref(null), creationPending = ref(null);
const creationStateKey = owner => owner ? 'cd80-home-create:v1:' + encodeURIComponent(String(owner)) : '';
let creationOwner = '', creationSequence = 0;
function persistCreationState() {
  const key = creationStateKey(s.user?.id);
  if (!key) return;
  try { sessionStorage.setItem(key, JSON.stringify({ enabled: createMode.value, sessionId: creationSessionId.value,
    contextId: creationContextId.value, pending: creationPending.value })); } catch { /* current tab still works */ }
}
watch(() => s.user?.id, ownerValue => {
  const owner = String(ownerValue || '');
  creationSequence++;
  if (creationOwner && creationOwner !== owner) {
    try { sessionStorage.removeItem(creationStateKey(creationOwner)); } catch { /* storage unavailable */ }
  }
  creationOwner = owner;
  createMode.value = false; creationSessionId.value = ''; creationContextId.value = null; creationPending.value = null; creationBusy.value = false; creationFailed.value = false;
  if (!owner) return;
  try {
    const saved = JSON.parse(sessionStorage.getItem(creationStateKey(owner)) || 'null');
    createMode.value = saved?.enabled === true;
    creationSessionId.value = typeof saved?.sessionId === 'string' ? saved.sessionId : '';
    creationContextId.value = saved?.contextId || null;
    creationPending.value = saved?.pending?.body && saved?.pending?.sent ? saved.pending : null;
  } catch { /* a damaged browser draft never blocks the page */ }
}, { immediate: true });
function updateCreateMode(value) { createMode.value = value === true; creationFailed.value = false; persistCreationState(); }
function clearCreationSession() { creationSessionId.value = ''; creationContextId.value = null; persistCreationState(); }
function startNewTask() {
  if (creationBusy.value || authoring.s.busy) { toast('请先等待当前创建提交完成。'); return; }
  newTask(); if (!s.currentTaskId) clearCreationSession();
}
const creationSession = computed(() => {
  if (!creationSessionId.value || creationContextId.value !== (s.currentTaskId || null)) return null;
  return authoring.s.details[creationSessionId.value]?.session
    || authoring.s.items.find(item => item.id === creationSessionId.value) || null;
});
const creationCard = computed(() => creationSession.value && creationSession.value.phase !== 'completed' ? creationSession.value : null);
async function acceptCreation(result, owner, sent) {
  if (!result || owner !== String(s.user?.id || '')) return;
  const session = sessionFromResponse(result);
  creationPending.value = null;
  if (session?.id) { creationSessionId.value = session.id; creationContextId.value = session.conversationId || null; }
  if (sent) acceptCreationDraft(recoverCreationDraft(sent));
  persistCreationState();
  trackStarted(result, owner);
}
async function retryCreation() {
  if (creationBusy.value || authoring.s.busy || !creationPending.value) return;
  const owner = String(s.user?.id || ''), pending = creationPending.value;
  const sequence = ++creationSequence;
  creationBusy.value = true;
  creationFailed.value = false;
  try {
    const result = await authoring.create(pending.body);
    if (owner !== String(s.user?.id || '') || sequence !== creationSequence) return;
    if (!result && !authoring.s.pendingRetry) {
      creationPending.value = null; creationFailed.value = true; persistCreationState();
      return;
    }
    await acceptCreation(result, owner, pending.sent);
  } finally { if (owner === String(s.user?.id || '') && sequence === creationSequence) creationBusy.value = false; }
}
async function sendCreation() {
  if (creationBusy.value || authoring.s.busy || creationPending.value) return;
  const instruction = s.draft;
  if (!instruction.trim()) { composer.value?.focus?.(); return; }
  if (uploadCount.value) { toast('文件仍在上传，请等待完成后发送，以免遗漏附件。', true); return; }
  const owner = String(s.user?.id || ''), sent = snapshotCreationDraft();
  if (!owner || !sent) return;
  const sourceRefs = s.attachedFileIds.map(fileId => ({ kind: 'upload', reference: { fileId } }));
  const sequence = ++creationSequence;
  creationBusy.value = true;
  creationFailed.value = false;
  try {
    let session = creationSession.value;
    if (!session && creationSessionId.value && creationContextId.value === (s.currentTaskId || null)) {
      session = await authoring.read(creationSessionId.value, false);
      if (owner !== String(s.user?.id || '')) return;
      if (!session) { toast('请在创作记录中核实上一次结果后继续。', true); return; }
    }
    if (session && session.phase !== 'completed' && session.phase !== 'cancelled') {
      if (session.question && authoringAllowed(session, 'answers')) {
        const result = await authoring.answer(session.id, instruction, sourceRefs);
        if (owner === String(s.user?.id || '') && sequence === creationSequence) {
          if (result) acceptCreationDraft(sent); else creationFailed.value = true;
        }
        return;
      }
      if (authoringAllowed(session, 'messages')) {
        const result = await authoring.append(session.id, instruction, sourceRefs);
        if (owner === String(s.user?.id || '') && sequence === creationSequence) {
          if (result) acceptCreationDraft(sent); else creationFailed.value = true;
        }
        return;
      }
      authoring.read(session.id);
      toast('请先查看当前 APP 的创作进度，再提交下一条需求。');
      return;
    }
    const body = authoringCreateRequest({ kind: 'app', intent: 'create', mode: 'prepare', instruction,
      sourceRefs, conversationId: s.currentTaskId || null });
    creationPending.value = { body, sent };
    persistCreationState();
    const result = await authoring.create(body);
    if (owner !== String(s.user?.id || '') || sequence !== creationSequence) return;
    if (!result && !authoring.s.pendingRetry) {
      creationPending.value = null; creationFailed.value = true; persistCreationState();
      return;
    }
    await acceptCreation(result, owner, sent);
  } finally { if (owner === String(s.user?.id || '') && sequence === creationSequence) creationBusy.value = false; }
}
function openAuthoring(context = {}) { authoring.s.error = ''; authoringStart.value = { ...context }; }
const messageSessionId = message => message.authoringSessionId || message.authoringSession?.id || message.subAgentRun?.authoringSessionId;
const appRunCards = computed(() => conversationItems.value.map((_,index) => firstAssetAppRun(conversationItems.value,index)));
function messageSession(message, index) {
  const id = messageSessionId(message);
  if (!id || conversationItems.value.slice(0,index).some(item => messageSessionId(item) === id)) return null;
  return authoring.s.details[id]?.session || message.authoringSession;
}
const remainingSessions = computed(() => authoring.s.items.filter(item => item.conversationId === s.currentTaskId && item.id !== creationSessionId.value && !conversationItems.value.some(message => messageSessionId(message) === item.id)));
function continueAuthoringChat(session) { s.authoringContextId = session.id; authoring.close(); setView('home'); nextTick(() => composer.value?.focus?.()); }
function openCreatedAsset(item) { authoring.close(); if (item.kind === 'app') openUnifiedApp(item.assetId || item.appId, item.versionId); else manageExtension(item); }
watch(() => s.user?.id, () => { authoringStart.value = null; portableRevision.value = 0; assetRevision.value = 0; });
const appDetailOpen = ref(false), appListRequest = ref(null), legacyApplicationsPanel = ref(null);
const { trackStarted } = useCreatedAppNavigation(authoring, () => s.user?.id, ({ appId, versionId }) => {
  authoring.close(); authoringStart.value = null;
  if (creationSession.value?.result?.assetId === appId && creationSession.value?.result?.versionId === versionId) clearCreationSession();
  showMyApps({ appId, versionId });
});
watch(() => [creationSession.value, authoring.s.owner], ([session, owner]) => {
  if (session && owner === String(s.user?.id || '')) trackStarted(session, owner);
});
const personalStrategyAppId = ref(''), selectedAssetAppId = ref(''), selectedAssetVersionId = ref(''), selectedStrategyAppId = ref(''), workshopHandoffError = ref('');
const personalStrategyKey = () => accountDraftKey(s.user?.id, 'personal-strategy-app', 'current');
watch(() => s.user?.id, () => {
  personalStrategyAppId.value = readDraft(personalStrategyKey()) || '';
  appDetailOpen.value = false; appListRequest.value = null;
  selectedAssetAppId.value = ''; selectedAssetVersionId.value = ''; selectedStrategyAppId.value = ''; workshopHandoffError.value = '';
}, { immediate: true });
function openStrategyWorkshop() {
  clearDraft(personalStrategyKey()); personalStrategyAppId.value = ''; setView('strategy-workshop');
}
function openWorkspace(view) { if (view === 'strategy-workshop') openStrategyWorkshop(); else setView(view); }
function openPersonalStrategyApp(app) {
  if (!app?.id) return;
  personalStrategyAppId.value = app.id; writeDraft(personalStrategyKey(), app.id); setView('strategy-workshop');
}
async function onStrategyAppSaved(app) {
  const owner = String(s.user?.id || ''); showMyApps(); selectedStrategyAppId.value = app?.id || '';
  await nextTick();
  if (owner === String(s.user?.id || '')) await platform.refresh();
}
async function onStrategyRuleSaved(app) {
  const owner = String(s.user?.id || '');
  if (app?.id && owner) await platform.refresh();
}
function openSavedStrategyApp(app) { if (app?.id) openPersonalStrategyApp(app); }
function onStrategyNewSession() { clearDraft(personalStrategyKey()); personalStrategyAppId.value = ''; }
function showMyApps(saved = {}) {
  appDetailOpen.value = false; selectedAssetAppId.value = ''; selectedAssetVersionId.value = '';
  selectedStrategyAppId.value = ''; managedAssetId.value = ''; captureContext.value = null;
  appListRequest.value = saved.appId ? { appId: saved.appId, versionId: saved.versionId || '', token: crypto.randomUUID() } : null;
  assetRevision.value++; setView('extensions');
}
function openUnifiedApp(appId, versionId = '') {
  if (!appId) return;
  selectedAssetVersionId.value = versionId; selectedAssetAppId.value = appId;
  managedAssetId.value = ''; captureContext.value = null; selectedStrategyAppId.value = '';
  appListRequest.value = null; appDetailOpen.value = true; assetRevision.value++; setView('extensions');
}
async function openLegacyApps() {
  showMyApps(); legacyApplicationsOpen.value = true; await nextTick();
  legacyApplicationsPanel.value?.scrollIntoView?.({ block: 'start' });
}
watch(() => s.view, view => { if (view !== 'extensions') appDetailOpen.value = false; });
async function onWorkshopHandoff(value) {
  workshopHandoffError.value = '';
  const requirement = workshopRequirement(value.session, value.requirement);
  if (value.target.kind === 'strategy-workshop') {
    prepareStrategyHandoff(s.user?.id, requirement); openStrategyWorkshop();
  } else if (value.target.kind === 'asset-app') {
    const owner = String(s.user?.id || '');
    try {
      const detail = await request('/asset-apps/' + encodeURIComponent(value.target.appId) + '?versionId=' + encodeURIComponent(value.target.versionId));
      if (owner !== String(s.user?.id || '')) return;
      if (detail.versionId !== value.target.versionId || detail.enabledVersionId !== value.target.versionId || detail.verification?.status !== 'passed') throw new Error('推荐版本当前不可运行，请重新分析需求或到我的应用完成验证与启用。');
      prepareAssetHandoff(owner, detail, requirement, value.inputs);
      openUnifiedApp(value.target.appId, value.target.versionId);
    } catch (error) { if (owner === String(s.user?.id || '')) workshopHandoffError.value = error.message; }
  } else {
    const owner = String(s.user?.id || ''); await openLegacyApps();
    if (owner === String(s.user?.id || '')) await platform.openFromWorkshop(value.target.agentRef, requirement, value.inputs);
  }
}
function onWorkshopSaved(value) { if (value.appId) showMyApps(value); }
const knownFile = file => s.files.find(item => String(item.id) === String(file.id)) || file;
const personalDataFiles = computed(() => s.files.filter(isDataManagementFile));
const otherFiles = computed(() => s.files.filter(file => !isDataManagementFile(file)));

const isMobile = ref(false);
const mobileNavOpen = ref(false);
const specialAppsOpen = ref(false);
watch(() => s.user?.id, () => { specialAppsOpen.value = false; });
const mobileNavToggle = ref(null);
const sidebar = ref(null);
const searchShortcut = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K';
let mobileMedia;
function syncMobileLayout() {
  isMobile.value = mobileMedia.matches;
  if (!isMobile.value) mobileNavOpen.value = false;
}
onMounted(() => {
  mobileMedia = window.matchMedia('(max-width: 767px)');
  syncMobileLayout();
  mobileMedia.addEventListener('change', syncMobileLayout);
});
onUnmounted(() => mobileMedia?.removeEventListener('change', syncMobileLayout));
async function openMobileNav() {
  mobileNavOpen.value = true;
  await nextTick();
  await new Promise(resolve => requestAnimationFrame(resolve));
  sidebar.value?.querySelector('button')?.focus();
}
function closeMobileNav(restoreFocus = false) {
  mobileNavOpen.value = false;
  if (restoreFocus) nextTick(() => mobileNavToggle.value?.focus());
}
function onSidebarAction(event) {
  if (event.target.closest('.special-apps-toggle')) return;
  if (!isMobile.value || !event.target.closest('.nav-row, .side-row, .side-heading button, .project-cta button, .user-row, .search-trigger')) return;
  closeMobileNav();
  nextTick(() => { if (!s.modal) mobileNavToggle.value?.focus(); });
}
function onSidebarKeydown(event) {
  if (!isMobile.value || !mobileNavOpen.value) return;
  if (event.key === 'Escape') { event.preventDefault(); closeMobileNav(true); return; }
  if (event.key !== 'Tab') return;
  const controls = [...sidebar.value.querySelectorAll('button:not([disabled])')].filter(item => item.getClientRects().length);
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
watch(() => [s.modal, s.authenticated], () => closeMobileNav());
function useHomePrompt(item) {
  if (s.streaming || (item.build && s.selectedSkillNames.length)) return;
  if (item.build) s.interactionMode = 'platform';
  setPrompt(item.text);
}
function beginExplore() {
  setView('explore');
}
const taskFailure = computed(() => s.streaming ? '' : s.streamError || s.currentTask?.error || '');
const sourcePanel = ref(null);
const capabilityDialog = ref(null);
watch(() => s.user?.id, () => { capabilityDialog.value = null; sourcePanel.value = null; sourceReturnFocus = null; });
function openCapability(kind, item) { capabilityDialog.value = { kind, name: item.name, description: item.description }; }
function useSelectedSkill(name) { if (selectSkill(name)) capabilityDialog.value = null; }
function useLibraryCapability(item) {
  if (s.streaming) return;
  if (s.selectedApplication) { s.streamError = '请先移除已选应用，再选择 Skill 或 Tool。'; return; }
  if (s.selectedCapabilityRefs.length >= 8 && !s.selectedCapabilityRefs.some(ref => ref.assetId === item.assetId)) { s.streamError = '本轮最多选择 8 项能力，请先移除一项。'; return; }
  const old = s.currentTask?.capabilityLocks?.[item.assetId];
  if (old && old.versionId !== item.versionId) {
    if (!window.confirm('此对话已锁定另一版本。是否明确切换到当前选择的版本？')) return;
    s.replaceCapabilityLocks = true;
  }
  s.selectedCapabilityRefs = [...s.selectedCapabilityRefs.filter(value => value.assetId !== item.assetId), item];
  s.selectedSkillNames = [];
  s.interactionMode = 'chat';
  setView('home');
}
const managedAssetId = ref('');
const captureContext = ref(null), recipeCaptureRun = ref(''), assetCaptureRun = ref(''), legacyApplicationsOpen = ref(false);
watch(() => s.user?.id, () => { managedAssetId.value = ''; captureContext.value = null; recipeCaptureRun.value = ''; assetCaptureRun.value = ''; legacyApplicationsOpen.value = false; });
function manageExtension(item) { appDetailOpen.value = false; appListRequest.value = null; captureContext.value = null; managedAssetId.value = item.assetId; setView('extensions'); }
function captureResult(kind, run) {
  openAuthoring({ kind: kind === 'application' ? 'app' : kind, intent: 'capture', sourceRefs: [{ kind: 'sub_agent_run', reference: { runId: run.id } }], instruction: '根据这次任务整理可复用方法，保留既定业务规则，并准备适用的新输入验证。' });
}
function openCapabilityDraft(prepared) {
  appDetailOpen.value = false; appListRequest.value = null;
  managedAssetId.value = '';
  captureContext.value = { kind: prepared.kind, prepared, token: crypto.randomUUID() };
  setView('extensions');
}
function chooseExtension(item) {
  if (item.kind !== 'application') { useLibraryCapability(item); return; }
  if (s.selectedCapabilityRefs.length || s.selectedSkillNames.length) { s.streamError = '请先移除已选 Skill/Tool，再选择应用。'; return; }
  s.selectedApplication = item;
}
function sendComposer() {
  if (createMode.value) { sendCreation(); return; }
  if (s.selectedApplication) showModal('application-input', { title: '确认应用参数' });
  else sendMessage();
}
async function runSelectedApplication(values) {
  const entry = s.selectedApplication, owner = s.user?.id, draft = s.draft;
  if (!entry) return;
  s.modalBusy = true;
  try {
    const result = await platform.invoke(entry, { ...values, taskId: s.currentTaskId || undefined, goal: draft || entry.spec.goal });
    if (result && s.user?.id === owner) {
      if (s.draft === draft) updateDraft('');
      s.selectedApplication = null;
      s.modalBusy = false; closeModal();
    }
  } finally { s.modalBusy = false; }
}
let sourceReturnFocus = null;

function openSources(message, trigger, sourceUrl = '') {
  const sources = publicSearchSources(message?.sources);
  const skills = publicSkillsUsed(message?.skillsUsed);
  const tools = publicToolsUsed(message?.toolsUsed);
  if (!sources.length && !skills.length && !tools.length) return;
  sourceReturnFocus = trigger instanceof HTMLElement ? trigger : document.activeElement;
  sourcePanel.value = { sources, skills, tools, focusUrl: sources.some((item) => item.url === sourceUrl) ? sourceUrl : '' };
}

async function closeSources() {
  sourcePanel.value = null;
  await nextTick();
  if (sourceReturnFocus?.isConnected) sourceReturnFocus.focus();
  sourceReturnFocus = null;
}

function onRenderedMessageClick(event, message) {
  const citation = event.target.closest('[data-action="open-source"]');
  if (citation) { openSources(message, citation, citation.dataset.sourceUrl); return; }
  const button = event.target.closest('[data-action="copy-code"]');
  if (!button) return;
  const index = Number(button.dataset.codeIndex);
  const code = window.FicagentFormat.codeBlockAt(messageContent(message), index);
  if (code !== null) copyText(code);
}
function onDialogKeydown(event) {
  if (event.key !== 'Tab') return;
  const card = dialog.value;
  const controls = [...(card?.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href]') || [])];
  if (!controls.length) { event.preventDefault(); card?.focus(); return; }
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && (document.activeElement === first || !card.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !card.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}
</script>

<template>
  <div v-if="s.booting" class="auth-shell">
    <aside class="auth-side"><div class="auth-brand"><span class="brand-mark">F</span>Ficagent</div></aside>
    <main class="auth-main"><div class="auth-loading">正在连接 Ficagent…</div></main>
  </div>

  <div v-else-if="!s.authenticated" class="auth-shell">
    <aside class="auth-side">
      <div class="auth-brand"><span class="brand-mark">F</span><span>Ficagent</span></div>
      <div class="auth-copy"><h1>打开工作台，<br /><em>继续你的想法。</em></h1><p>在一个地方整理任务、文件与日程，连接你配置的模型和智能体。</p></div>
    </aside>
    <main class="auth-main">
      <div class="auth-form-wrap">
        <div class="auth-eyebrow">WELCOME TO FICAGENT</div>
        <h2>{{ s.authMode === 'register' ? '创建账号' : '欢迎回来' }}</h2>
        <p>{{ s.authMode === 'register' ? '注册后即可开始使用 Ficagent。' : '登录你的本地账号，继续使用 Ficagent。' }}</p>
        <form id="auth-form" @submit.prevent="submitAuth">
          <div v-if="s.authError" class="auth-error" role="alert">{{ userFacingError(s.authError) }}</div>
          <label v-if="s.authMode === 'register'" class="form-field"><span>你的称呼</span><input v-model="s.authName" name="name" type="text" autocomplete="name" placeholder="例如：小八" required maxlength="50" /></label>
          <label class="form-field"><span>邮箱</span><input v-model="s.authEmail" name="email" type="email" autocomplete="email" placeholder="name@example.com" required /></label>
          <label class="form-field"><span>密码</span><span class="password-wrap"><input v-model="s.authPassword" name="password" :type="s.showPassword ? 'text' : 'password'" :autocomplete="s.authMode === 'register' ? 'new-password' : 'current-password'" placeholder="请输入密码" required minlength="6" /><button type="button" :aria-label="s.showPassword ? '隐藏密码' : '显示密码'" @click="s.showPassword = !s.showPassword"><Icon :name="s.showPassword ? 'eyeOff' : 'eye'" /></button></span></label>
          <button class="primary-button auth-submit" type="submit" :disabled="s.authBusy">{{ s.authBusy ? '请稍候…' : s.authMode === 'register' ? '创建账号' : '登录' }}</button>
        </form>
        <div class="auth-switch">{{ s.authMode === 'register' ? '已有账号？' : '还没有账号？' }} <button type="button" @click="s.authMode = s.authMode === 'login' ? 'register' : 'login'; s.authError = ''">{{ s.authMode === 'register' ? '直接登录' : '创建账号' }}</button></div>
      </div>
    </main>
  </div>

  <div v-else class="app-shell theme-aurora-rose" :class="{ collapsed: s.sidebarCollapsed, 'mobile-nav-open': mobileNavOpen }" :inert="Boolean(s.modal || sourcePanel || capabilityDialog)">
    <button ref="mobileNavToggle" class="mobile-nav-toggle icon-button" type="button" aria-label="打开导航" aria-controls="workspace-sidebar" :aria-expanded="mobileNavOpen" :inert="mobileNavOpen" @click="openMobileNav"><SidebarIcon name="menu" :size="18" /></button>
    <button v-if="isMobile && mobileNavOpen" class="mobile-sidebar-backdrop" type="button" tabindex="-1" aria-label="关闭导航" @click="closeMobileNav(true)" />
    <aside id="workspace-sidebar" ref="sidebar" class="sidebar" :inert="isMobile && !mobileNavOpen" :aria-label="isMobile ? '工作台导航' : undefined" :role="isMobile ? 'dialog' : undefined" :aria-modal="isMobile && mobileNavOpen ? true : undefined" @click="onSidebarAction" @keydown="onSidebarKeydown">
      <div class="sidebar-top">
        <button class="icon-button" type="button" :title="isMobile ? '关闭导航' : s.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" :aria-label="isMobile ? '关闭导航' : s.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" :aria-expanded="isMobile ? mobileNavOpen : !s.sidebarCollapsed" @click="isMobile ? closeMobileNav(true) : toggleSidebar()"><SidebarIcon :name="isMobile ? 'close' : s.sidebarCollapsed ? 'panelExpand' : 'panelCollapse'" :size="18" /></button>
        <button class="icon-button search-trigger" type="button" :title="'搜索任务 ' + searchShortcut" aria-label="搜索任务" @click="showModal('search')"><SidebarIcon name="search" :size="18" /></button>
      </div>
      <div class="brand"><span class="brand-mark">F</span><span class="brand-name">Ficagent<span class="brand-sub">WEB WORKSPACE</span></span></div>
      <div class="side-scroll">
        <nav class="nav-group" aria-label="主导航">
          <button class="nav-row" :class="{ active: s.view === 'home' && !s.currentTask }" aria-label="新任务" title="新任务" type="button" @click="startNewTask"><SidebarIcon name="newTask" :active="s.view === 'home' && !s.currentTask" /><span>新任务</span></button>
          <div class="special-apps-group">
            <button class="nav-row special-apps-toggle" :class="{ active: ['explore', 'strategy-workshop', 'app-workshop'].includes(s.view) }" aria-label="专有应用" title="专有应用" type="button" aria-controls="special-apps-list" :aria-expanded="specialAppsOpen" @click="specialAppsOpen = !specialAppsOpen"><SidebarIcon name="specialApps" :active="['explore', 'strategy-workshop', 'app-workshop'].includes(s.view)" /><span>专有应用</span><SidebarIcon :name="specialAppsOpen ? 'chevronUp' : 'chevronDown'" class="special-apps-chevron" :size="16" /></button>
            <div v-show="specialAppsOpen" id="special-apps-list" class="special-apps-list" role="group" aria-label="专有应用">
              <button class="nav-row" :class="{ active: s.view === 'explore' }" :aria-current="s.view === 'explore' ? 'page' : undefined" aria-label="Explore" title="Explore" type="button" @click="setView('explore')"><SidebarIcon name="explore" :active="s.view === 'explore'" :size="18" /><span>Explore</span></button>
              <button class="nav-row" :class="{ active: s.view === 'strategy-workshop' }" :aria-current="s.view === 'strategy-workshop' ? 'page' : undefined" aria-label="策略工坊" title="策略工坊" type="button" @click="openStrategyWorkshop"><SidebarIcon name="strategyWorkshop" :active="s.view === 'strategy-workshop'" :size="18" /><span>策略工坊</span></button>
              <button class="nav-row" :class="{ active: s.view === 'app-workshop' }" :aria-current="s.view === 'app-workshop' ? 'page' : undefined" aria-label="应用工坊" title="应用工坊" type="button" @click="setView('app-workshop')"><SidebarIcon name="appWorkshop" :active="s.view === 'app-workshop'" :size="18" /><span>应用工坊</span></button>
            </div>
          </div>
          <button class="nav-row" :class="{ active: s.view === 'schedule' }" aria-label="日程" title="日程" type="button" @click="setView('schedule')"><SidebarIcon name="schedule" :active="s.view === 'schedule'" /><span>日程</span></button>
          <button class="nav-row" :class="{ active: s.view === 'community' }" aria-label="共创" title="共创" type="button" @click="setView('community')"><SidebarIcon name="community" :active="s.view === 'community'" /><span>共创</span></button>
          <button class="nav-row" :class="{ active: s.view === 'extensions' }" aria-label="技能池" title="技能池" type="button" @click="showMyApps()"><SidebarIcon name="skills" :active="s.view === 'extensions'" /><span>技能池</span></button>
          <button class="nav-row" :class="{ active: s.view === 'files' }" aria-label="文件" title="文件" type="button" @click="setView('files')"><SidebarIcon name="files" :active="s.view === 'files'" /><span>文件</span></button>
          <button class="nav-row" :class="{ active: s.view === 'data' }" :aria-current="s.view === 'data' ? 'page' : undefined" aria-label="数据管理" title="数据管理" type="button" @click="setView('data')"><SidebarIcon name="data" :active="s.view === 'data'" /><span>数据管理</span></button>
        </nav>
        <section class="side-section">
          <div class="side-heading"><span>项目</span><button class="icon-button" type="button" title="新建项目" aria-label="新建项目" @click="showModal('project')"><SidebarIcon name="add" :size="16" /></button></div>
          <template v-if="s.projects.length">
            <div v-for="item in s.projects" :key="item.id" class="side-row" :class="{ active: String(item.id) === String(s.selectedProjectId) }">
              <button class="side-row-button" type="button" :title="item.name" :aria-pressed="String(item.id) === String(s.selectedProjectId)" @click="selectProject(item.id)"><span class="small-avatar project-symbol"><SidebarIcon name="files" :size="16" /></span><span class="row-title">{{ item.name }}</span></button>
              <button class="icon-button row-more" type="button" :title="'删除' + item.name" :aria-label="'删除' + item.name" @click="confirmDelete('project', item.id)"><SidebarIcon name="more" :size="16" /></button>
            </div>
          </template>
          <div v-else class="project-cta"><p>按项目整理任务和文件，<br />把持续的工作放在一起。</p><button type="button" @click="showModal('project')">创建项目</button></div>
        </section>
        <section v-if="recentTasks.length" class="side-section tasks-group">
          <div class="side-heading"><span>最近任务</span><button class="icon-button" type="button" title="搜索任务" aria-label="搜索任务" @click="showModal('search')"><SidebarIcon name="search" :size="16" /></button></div>
          <div v-for="item in recentTasks" :key="item.id" class="side-row" :class="{ active: s.view === 'home' && String(item.id) === String(s.currentTaskId) }">
            <button class="side-row-button" type="button" :title="item.title || '未命名任务'" :aria-label="item.title || '未命名任务'" :aria-current="s.view === 'home' && String(item.id) === String(s.currentTaskId) ? 'page' : undefined" :aria-busy="String(item.id) === String(s.openingTaskId)" @click="openTask(item.id)"><span class="task-indicator"><SidebarIcon name="history" :size="16" /></span><span class="row-title">{{ item.title || '未命名任务' }}</span><span class="task-time">{{ relativeTime(item.updatedAt) }}</span></button>
            <button class="icon-button row-more" type="button" title="删除任务" :aria-label="'删除' + (item.title || '任务')" @click="confirmDelete('task', item.id)"><SidebarIcon name="more" :size="16" /></button>
          </div>
        </section>
      </div>
      <div class="sidebar-bottom"><button class="user-row" type="button" title="账号与设置" aria-label="账号与设置" :aria-current="s.view === 'settings' ? 'page' : undefined" @click="setView('settings')"><span class="user-avatar">{{ String(s.user?.name || s.user?.email || 'F').slice(0, 1).toUpperCase() }}</span><span class="user-name">{{ s.user?.name || s.user?.email || '本地用户' }}</span><SidebarIcon name="settings" :active="s.view === 'settings'" :size="18" /></button></div>
    </aside>

    <main class="main-panel" :inert="isMobile && mobileNavOpen">
      <template v-if="s.view === 'home'">
        <div v-if="s.selectedCapabilityRefs.length && !createMode" class="cap-selected-strip"><span>本轮能力</span><button v-for="item in s.selectedCapabilityRefs" :key="item.assetId" type="button" :disabled="s.streaming" @click="s.selectedCapabilityRefs = s.selectedCapabilityRefs.filter(value => value.assetId !== item.assetId)">{{ item.name }} · {{ item.versionId.slice(-6) }} ×</button><small>仅本轮所选文件会授权给工具。</small></div>
        <div class="home-utility-row" :class="{ 'conversation-utility-row': s.currentTask }">
        <div v-if="!createMode" class="agent-mode-toggle"><label for="interaction-mode">对话方式</label><select id="interaction-mode" v-model="s.interactionMode" :disabled="s.streaming || s.selectedSkillNames.length > 0"><option value="platform">主助手 · 对话与应用</option><option value="chat">普通对话 · 当前文字角色</option></select><small>{{ s.interactionMode === 'platform' ? '描述需求，选择已有应用或设计新流程。' : '使用当前角色进行文字交流。' }}</small></div>
          <button v-if="!s.currentTask" class="home-search" type="button" aria-label="搜索任务" @click="showModal('search')"><Icon name="search" /><span>搜索任务…</span><kbd>{{ searchShortcut }}</kbd></button>
        </div>
        <div v-if="!s.currentTask" class="page home-scroll">
          <HomeLanding :busy="s.streaming" :has-skills="s.selectedSkillNames.length > 0" @explore="beginExplore" @strategy-workshop="openStrategyWorkshop" @app-workshop="setView('app-workshop')" @prompt="useHomePrompt">
            <Composer variant="home" ref="composer" :draft="s.draft" :agent="agent" :model="model" :project="project" :attachments="attachedFiles" :streaming="s.streaming" :uploading="uploadCount" :model-unavailable="s.taskModelUnavailable" :has-models="s.models.length > 0" :skill-names="s.selectedSkillNames" :create-mode="createMode" :creation-busy="creationBusy || authoring.s.busy" :creation-pending="!!creationPending" @update:create-mode="updateCreateMode" @remove-skill="removeSkill" :search-mode="s.searchMode" :search-available="s.runtime?.searchAvailable !== false" @update:draft="updateDraft" @update:search-mode="updateSearchMode" :capability-refs="s.selectedCapabilityRefs" :application="s.selectedApplication" @choose-extension="chooseExtension" @manage-extension="manageExtension" @remove-capability="s.selectedCapabilityRefs = s.selectedCapabilityRefs.filter(item => item.assetId !== $event)" @remove-application="s.selectedApplication = null" @send="sendComposer" @attach="showModal('attachments')" @project="showModal('projects')" @model="showModal('models')" @agent="showModal('agents')" @remove-attachment="removeAttachment" />
            <div v-if="creationPending" class="creation-recovery" role="status"><span>上一次创建结果待核实，需求已保留。</span><button type="button" :disabled="creationBusy || authoring.s.busy" @click="retryCreation">核实并恢复同一次创建</button></div>
            <p v-if="createMode && creationFailed" class="send-error" role="alert">{{ USER_FACING_ERROR }}</p>
            <AuthoringCard v-if="creationCard" :session="creationCard" :detail="authoring.s.details[creationCard.id]" @open="authoring.read($event)" />
            <div v-else-if="creationSessionId && !creationSession" class="creation-recovery" role="status"><span>正在读取上次 APP 创作进度…</span><button type="button" @click="authoring.read(creationSessionId)">查看创作记录</button></div>
            <div v-if="s.streamError" class="send-error" role="alert"><Icon name="info" small /><span>{{ userFacingError(s.streamError) }}</span></div>
            <div v-if="s.runtime?.configured === false" class="config-hint"><Icon name="info" small />当前模型尚未配置。<button type="button" @click="setView('settings')">查看配置说明</button>。</div>
            <div v-else-if="!s.models.length" class="config-hint"><Icon name="info" small />当前没有可用模型。<button type="button" @click="setView('settings')">查看运行状态</button>。</div>
          </HomeLanding>
        </div>
        <div v-else class="page conversation-page">
          <div ref="conversation" class="conversation" id="conversation-scroll" @scroll="onConversationScroll">
            <div class="conversation-head"><span class="small-avatar">{{ agent?.glyph || 'F' }}</span><h1>{{ s.currentTask.title || '新任务' }}</h1><button class="icon-button" type="button" title="新任务" aria-label="新任务" @click="startNewTask"><Icon name="addCircle" small /></button></div>
            <div v-for="(message, index) in conversationItems" :key="message.rowKey" class="message" :class="message.role === 'user' ? 'user' : 'assistant'">
              <div class="message-avatar">{{ message.role === 'user' ? String(s.user?.name || s.user?.email || '我').slice(0, 1) : agent?.glyph || 'F' }}</div>
              <div class="message-body">
                <div class="message-label">{{ message.role === 'user' ? s.user?.name || '我' : agent?.name }}</div>
                <SubAgentPanel v-if="message.subAgentRun" :run="message.subAgentRun" :controller="subAgents" unified-authoring @capture="captureResult" />
                <div v-else-if="message.role === 'user'" class="message-content">{{ messageContent(message) }}</div>
                <div v-else-if="messageContent(message)" class="message-content" v-html="renderAssistant(message, index)" @click="onRenderedMessageClick($event, message)" />
                <div v-else-if="message.streaming" class="message-content"><span class="message-typing"><i /><i /><i /></span></div>
                <AssetChatRun v-if="appRunCards[index]" :key="String(s.user?.id) + appRunCards[index].assetAppRunId" :owner-id="String(s.user?.id || '')" :run-id="appRunCards[index].assetAppRunId" :app-id="appRunCards[index].assetAppId" :version-id="appRunCards[index].assetAppVersionId" :files="s.files" />
                <AuthoringCard v-if="messageSession(message,index)" :session="messageSession(message,index)" :detail="authoring.s.details[messageSessionId(message)]" @open="authoring.read($event)" />
                <details v-if="message.capabilityUsages?.length" class="cap-message-trace"><summary>本次能力记录 · {{ message.capabilityUsages.length }}</summary><div v-for="(usage, traceIndex) in message.capabilityUsages" :key="traceIndex"><span>{{ ({capability_selected:'已选用',skill_loaded:'已加载',tool_started:'开始调用',tool_completed:'调用完成',tool_failed:'调用失败',capability_blocked:'调用被阻止'})[usage.type] || usage.type }} · {{ s.currentTask?.capabilityLocks?.[usage.assetId]?.name || usage.name || '能力' }} · {{ usage.versionId?.slice(-6) }}</span><small v-if="usage.invocationId">记录 {{ usage.invocationId }}</small><span v-if="usage.message">{{ ['tool_failed', 'capability_blocked'].includes(usage.type) ? USER_FACING_ERROR : usage.message }}</span></div><button class="secondary-button" @click="setView('extensions')">查看能力与调用详情</button></details>
                <div v-if="message.role === 'assistant' && (publicSearchSources(message.sources).length || publicSkillsUsed(message.skillsUsed).length || publicToolsUsed(message.toolsUsed).length)" class="message-source-actions"><button type="button" class="message-source-trigger" @click="openSources(message, $event.currentTarget)"><Icon name="search" small />{{ publicSearchSources(message.sources).length ? '来源 · ' + publicSearchSources(message.sources).length : '本次详情' }}</button></div>
                <div v-if="message.attachments?.length" class="message-attachments" aria-label="引用文件"><span v-for="file in message.attachments" :key="file.id" class="message-attachment" :title="fileIdentityHint(knownFile(file))"><Icon name="file" small /><span>{{ fileLabel(knownFile(file)) }}</span></span></div>
                <div v-if="messageContent(message)" class="message-actions"><button type="button" @click="copyText(messageContent(message))"><Icon name="copy" small /> 复制消息</button></div>
              </div>
            </div>
            <AuthoringCard v-for="session in remainingSessions" :key="session.id" :session="session" :detail="authoring.s.details[session.id]" @open="authoring.read($event)" />
            <button v-if="s.currentTask?.messages?.length" type="button" class="secondary-button conversation-capture" @click="openAuthoring({intent:'capture',sourceRefs:[{kind:'conversation',reference:{conversationId:s.currentTaskId}}]})">把这段对话整理成可复用资产</button>
            <div v-if="taskFailure" class="send-error" role="alert"><Icon name="info" small /><span>{{ userFacingError(taskFailure) }}</span><button type="button" @click="restoreLastPrompt">放回输入框</button></div>
            <div v-if="subAgents.state.queue?.paused" class="sub-queue-notice" role="status"><span>{{ subAgents.state.queue.pauseReason ? USER_FACING_ERROR : '队列已暂停，后续消息尚未执行。' }}</span><button class="secondary-button" @click="subAgents.resume()">继续排队消息</button></div>
            <p v-if="subAgents.state.error" class="send-error" role="alert">{{ userFacingError(subAgents.state.error) }}</p>
            <AgentPlatformPanel unified-authoring @author="openAuthoring" @open-asset="payload => openUnifiedApp(payload.appId, payload.versionId)" v-if="platform.p.runs.length || s.currentTask.pendingAgentInput || subAgents.state.queue?.items?.some(run => run.kind === 'application')" :platform="platform" :files="s.files" @upload="payload => chooseFiles('agent', payload)" @open-task="openTask" />
          </div>
          <div class="conversation-jump-wrap"><button v-if="s.showJump" class="conversation-jump" type="button" @click="scrollConversation">↓ 回到底部</button></div>
          <div class="chat-footer"><div v-if="s.authoringContextId && !createMode" class="author-chat-context">正在调整：{{ authoringTitle(authoring.s.details[s.authoringContextId]?.session,authoring.s.details[s.authoringContextId]?.candidate) }}<button type="button" @click="s.authoringContextId = ''">退出草稿修改</button></div>
            <div v-if="creationPending" class="creation-recovery" role="status"><span>上一次创建结果待核实，需求已保留。</span><button type="button" :disabled="creationBusy || authoring.s.busy" @click="retryCreation">核实并恢复同一次创建</button></div>
            <p v-if="createMode && creationFailed" class="send-error" role="alert">{{ USER_FACING_ERROR }}</p>
            <AuthoringCard v-if="creationCard" :session="creationCard" :detail="authoring.s.details[creationCard.id]" @open="authoring.read($event)" />
            <div v-if="s.streaming" class="stop-bar" role="status"><span class="run-orb" aria-hidden="true" /><span class="generation-label">{{ generationLabel }}</span><button type="button" @click="stopMessage"><Icon name="stop" small /> 停止生成</button></div>
            <Composer ref="composer" :draft="s.draft" :agent="agent" :model="model" :project="project" :attachments="attachedFiles" :streaming="s.streaming" :uploading="uploadCount" :model-unavailable="s.taskModelUnavailable" :has-models="s.models.length > 0" :skill-names="s.selectedSkillNames" :create-mode="createMode" :creation-busy="creationBusy || authoring.s.busy" :creation-pending="!!creationPending" @update:create-mode="updateCreateMode" @remove-skill="removeSkill" :search-mode="s.searchMode" :search-available="s.runtime?.searchAvailable !== false" @update:draft="updateDraft" @update:search-mode="updateSearchMode" :capability-refs="s.selectedCapabilityRefs" :application="s.selectedApplication" @choose-extension="chooseExtension" @manage-extension="manageExtension" @remove-capability="s.selectedCapabilityRefs = s.selectedCapabilityRefs.filter(item => item.assetId !== $event)" @remove-application="s.selectedApplication = null" @send="sendComposer" @attach="showModal('attachments')" @project="showModal('projects')" @model="showModal('models')" @agent="showModal('agents')" @remove-attachment="removeAttachment" />
          </div>
        </div>
      </template>

      <section v-else-if="s.view === 'community'" class="page community-host"><CommunityPanel :key="s.user?.id" @applications="showMyApps()" @app="payload => openUnifiedApp(payload.appId, payload.versionId)" @capability="manageExtension" /></section>

      <section v-else-if="s.view === 'explore'" class="page explore-host"><ExplorePanel :key="String(s.user?.id || '')" :owner-id="s.user?.id" @back="setView('home')" /></section>
      <section v-else-if="s.view === 'app-workshop'" class="page app-workshop-host"><p v-if="workshopHandoffError" role="alert" class="error-message">{{ userFacingError(workshopHandoffError) }}</p><AppWorkshop :key="String(s.user?.id || '')" :owner-key="s.user?.id" @handoff="onWorkshopHandoff" @saved="onWorkshopSaved" /></section>
      <section v-else-if="s.view === 'strategy-workshop'" class="page"><div class="strategy-workshop-host"><StrategyWorkshop :key="String(s.user?.id || '') + ':' + personalStrategyAppId" :owner-key="s.user?.id" :personal-app-id="personalStrategyAppId" @saved="onStrategyAppSaved" @app-saved="onStrategyRuleSaved" @open-saved="openSavedStrategyApp" @new-session="onStrategyNewSession" /></div></section>

      <section v-else-if="s.view === 'schedule'" class="page">
        <div class="page-header"><div><h1 class="page-title">日程</h1><p class="page-subtitle">把要做的事放在这里。工作台运行时会提醒到期日程。</p></div><div class="toolbar-actions"><button class="primary-button" type="button" @click="showModal('schedule')"><Icon name="add" small /> 新建日程</button></div></div>
        <div class="page-body page-body-narrow">
          <div v-if="sortedSchedules.length" class="item-list">
            <div v-for="item in sortedSchedules" :key="item.id" class="item-card">
              <button class="schedule-check" :class="{ done: item.done }" type="button" :aria-label="item.done ? '标记未完成' : '标记完成'" @click="toggleSchedule(item)"><Icon v-if="item.done" name="check" small /></button>
              <div class="item-card-main"><div class="item-card-title" :class="{ strike: item.done }">{{ item.title }}</div><div class="item-card-detail">{{ formatDate(item.at) }}{{ new Date(item.at) < new Date() && !item.done ? ' · 已到期' : '' }}</div></div>
              <div class="item-card-actions"><button class="icon-button" type="button" title="删除" :aria-label="'删除' + item.title" @click="confirmDelete('schedule', item.id)"><Icon name="trash" small /></button></div>
            </div>
          </div>
          <div v-else class="empty-state"><Icon name="calendar" /><h2>还没有日程</h2><p>添加一条待办或提醒，到时间时会在工作台中提示你。</p><button class="secondary-button" type="button" @click="showModal('schedule')">添加第一条日程</button></div>
        </div>
      </section>

      <section v-else-if="s.view === 'files'" class="page">
        <div class="page-header"><div><h1 class="page-title">文件</h1><p class="page-subtitle">{{ project ? '当前项目：' + project.name : '上传并管理保存在本机工作台的文件。' }}</p></div><div class="toolbar-actions"><button class="primary-button" type="button" @click="chooseFiles('files')"><Icon name="upload" small /> 上传文件</button></div></div>
        <div class="page-body page-body-narrow">
          <div v-if="visibleFiles.length" class="item-list">
            <div v-for="item in visibleFiles" :key="item.id" class="item-card">
              <div class="item-card-icon"><Icon name="file" /></div>
              <div class="item-card-main"><div class="item-card-title" :title="fileIdentityHint(item)">{{ fileLabel(item) }}</div><div class="item-card-detail">{{ fileSize(item.size) }}{{ item.projectId ? ' · ' + (s.projects.find((candidate) => String(candidate.id) === String(item.projectId))?.name || '项目文件') : '' }}</div></div>
              <div class="item-card-actions">
                <button class="icon-button" type="button" title="预览" :aria-label="'预览' + fileLabel(item)" @click="previewFile(item.id)"><Icon name="eye" small /></button>
                <button class="icon-button" type="button" title="下载" :aria-label="'下载' + fileLabel(item)" @click="downloadFile(item.id)"><Icon name="download" small /></button>
                <button class="icon-button" type="button" title="删除" :aria-label="'删除' + fileLabel(item)" @click="confirmDelete('file', item.id)"><Icon name="trash" small /></button>
              </div>
            </div>
          </div>
          <div v-else class="empty-state"><Icon name="folder" /><h2>这里还没有文件</h2><p>上传资料后，可以预览、下载，或在聊天里引用支持的文本文件。</p><button class="secondary-button" type="button" @click="chooseFiles('files')">上传文件</button></div>
        </div>
      </section>

      <FinancialDataPanel v-else-if="s.view === 'data'" :key="s.user?.id" :personal-files="personalDataFiles" :upload-busy="dataUploading" @upload="chooseFiles('data')" @download="downloadFile" @delete="id => confirmDelete('file', id)" @attach="attachDataFileInChat" />

      <section v-else-if="s.view === 'extensions'" class="page skill-pool-host">
        <template v-if="appDetailOpen">
          <div class="page-header"><div><span class="pool-eyebrow">技能池 / 我的 APP</span><h1 class="page-title">APP 详情</h1></div></div>
          <div class="page-body"><AssetAuthoringPanel :key="s.user?.id + ':' + assetRevision" unified-authoring pool-detail @author="openAuthoring" :owner-id="String(s.user?.id || '')" :files="s.files" :initial-app-id="selectedAssetAppId" :initial-version-id="selectedAssetVersionId" @initial-opened="selectedAssetAppId = ''; selectedAssetVersionId = ''" :capture-run-id="assetCaptureRun" @captured="assetCaptureRun = ''" @back="showMyApps()" @capability-draft="openCapabilityDraft" @connections="manageExtension({ assetId: '' })" @legacy="openLegacyApps" @community="setView('community')" /></div>
        </template>
        <template v-else>
        <AuthoringShelf :key="s.user?.id" :controller="authoring" :owner-id="String(s.user?.id || '')" :kinds="['app','skill','tool']" :refresh-token="portableRevision" @author="openAuthoring" />
        <SkillPoolPanel :app-list-request="appListRequest" unified-authoring @open-workspace="openWorkspace" @open-strategy="openPersonalStrategyApp" @open-asset="payload => openUnifiedApp(payload.appId, payload.versionId)" @legacy="openLegacyApps" @capability-draft="openCapabilityDraft" @author="openAuthoring" :key="s.user?.id + assetRevision + managedAssetId + (captureContext?.runId || '') + (captureContext?.kind || '') + (captureContext?.token || '')" :owner-id="String(s.user?.id || '')" :initial-asset-id="managedAssetId" :initial-strategy-app-id="selectedStrategyAppId" @initial-strategy-opened="selectedStrategyAppId = ''" :initial-capture="captureContext" :files="s.files" :task="s.currentTask" :streaming="s.streaming" @select="useLibraryCapability" @community="setView('community')" />
        <details ref="legacyApplicationsPanel" class="legacy-applications" :open="legacyApplicationsOpen" @toggle="legacyApplicationsOpen = $event.target.open"><summary>旧版研究应用与固定流程 · 保留原行为</summary><p>这些应用继续按原模式运行。可以检查历史记录并生成升级草稿，完整 APP 在我的 APP 中管理。</p><RecipeLibraryPanel unified-authoring @author="openAuthoring" :key="s.user?.id" :files="s.files" :capture-run-id="recipeCaptureRun" @captured="recipeCaptureRun = ''" @open-task="openTask" @imported="platform.refresh" @community="setView('community')" /><AgentPlatformPanel unified-authoring @author="openAuthoring" @open-asset="payload => openUnifiedApp(payload.appId, payload.versionId)" :platform="platform" :files="s.files" catalog @upload="payload => chooseFiles('agent', payload)" @open-task="openTask" /></details>
        <details class="legacy-capabilities"><summary>现有内置工具与技能</summary>
        <div class="page-header"><div><h2 class="page-title">内置工具与技能</h2><p class="page-subtitle">直接调用工具，或选择技能用于下一条对话。</p></div><button class="secondary-button" :disabled="s.manualRefreshing" @click="manualRefresh">刷新目录</button></div>
        <div class="page-body page-body-narrow">
          <p v-if="s.runtimeLoading" role="status">正在读取工具与技能…</p>
          <p v-if="s.runtimeError" class="send-error" role="alert">{{ userFacingError(s.runtimeError) }}</p>
          <div class="section-label">独立工具 · {{ s.runtime?.tools?.length || 0 }}</div>
          <p class="extension-intro">填写参数后直接执行，无需加载 Skill 或让模型选择。</p>
          <div class="extension-grid">
            <article v-for="item in s.runtime?.tools || []" :key="item.name" class="surface-card extension-card" :data-tool-name="item.name">
              <div class="extension-card-heading"><h2>{{ item.name }}</h2><span class="tag">{{ item.available ? '可用' : '未配置' }}</span></div>
              <p>{{ item.description }}</p>
              <button class="secondary-button" type="button" :disabled="!item.available" :aria-label="'调用工具 ' + item.name" @click="openCapability('tool', item)">调用</button>
              <small v-if="!item.available">请在设置中检查服务端配置。</small>
            </article>
          </div>
          <p v-if="!s.runtimeLoading && !s.runtime?.tools?.length">暂无已注册工具。</p>
          <div class="section-label">Skill · {{ s.runtime?.skills?.length || 0 }}</div>
          <p class="extension-intro">Skill 提供任务规则。选择后输入问题，网站会在这一轮对话中加载它。</p>
          <div class="extension-grid">
            <article v-for="item in s.runtime?.skills || []" :key="item.name" class="surface-card extension-card" :data-skill-name="item.name">
              <h2>{{ item.name }}</h2><p>{{ item.description }}</p>
              <div class="extension-card-actions"><button class="secondary-button" type="button" :aria-label="'查看技能 ' + item.name" @click="openCapability('skill', item)">查看说明</button><button class="primary-button" type="button" :disabled="s.streaming" :aria-label="'使用技能 ' + item.name" @click="useSelectedSkill(item.name)">用于对话</button></div>
            </article>
          </div>
          <p v-if="!s.runtimeLoading && !s.runtime?.skills?.length">暂无可用技能，独立工具仍可使用。</p>
          <p v-if="s.runtime?.skillWarnings?.length" class="config-hint">{{ USER_FACING_ERROR }}</p>
        </div>
        </details>
        </template>
      </section>

      <section v-else-if="s.view === 'settings'" class="page">
        <div class="page-header"><div><h1 class="page-title">设置</h1><p class="page-subtitle">账号、模型与智能体配置。</p></div><div class="toolbar-actions"><button class="secondary-button" type="button" :disabled="s.manualRefreshing" @click="manualRefresh"><Icon name="history" small /> 刷新</button></div></div>
        <div class="page-body page-body-narrow">
          <div class="section-label">账号</div><div class="surface-card"><div class="setting-row"><span class="user-avatar">{{ String(s.user?.name || s.user?.email || 'F').slice(0, 1) }}</span><div class="grow"><strong>{{ s.user?.name || '本地用户' }}</strong><small>{{ s.user?.email || '本地账号' }}</small></div><button class="subtle-button danger-button" type="button" @click="logout"><Icon name="signout" small /> 退出登录</button></div></div>
          <div class="section-label">模型</div><div class="surface-card"><div class="setting-row"><div class="grow"><strong>{{ s.runtime?.model || s.models[0]?.name || 'GLM-5.3-Flash' }}</strong><small>{{ userFacingError(s.runtimeError) || (s.runtimeLoading ? '正在读取服务端配置状态…' : s.runtime?.configured ? 'Gitee AI 配置已填写，尚未验证上游连接。' : '尚未配置 Gitee AI 模型。') }}</small></div><button class="secondary-button" type="button" @click="showModal('models')">选择模型</button></div><div class="setting-row"><div class="grow"><strong>服务端配置</strong><small>如需启用对话，请在网站根目录的 .env 中设置 GITEE_AI_API_KEY，再重启 python main.py。密钥仅由后端读取。</small></div></div><div v-for="item in s.models" :key="item.id" class="setting-row"><div class="grow"><strong>{{ item.name }}</strong></div><span v-if="String(item.id) === String(s.selectedModelId)" class="tag">当前模型</span><button v-else class="subtle-button" type="button" @click="selectModel(item.id)">选择</button></div></div>
          <div class="section-label">网页搜索</div><div class="surface-card"><div class="setting-row"><div class="grow"><strong>搜索工具</strong><small>{{ s.runtimeLoading ? '正在读取服务端状态…' : s.runtime?.searchAvailable ? '可用；可在输入框选择自动、始终或关闭网页搜索。' : '当前不可用，请检查服务端配置。' }}</small><small>关闭网页搜索不会关闭金融查询等其他独立工具。</small></div><span class="tag">{{ s.runtime?.searchAvailable ? '可用' : '不可用' }}</span></div></div>
          <div class="section-label">已注册工具</div><div class="surface-card"><div v-for="item in s.runtime?.tools || []" :key="item.name" class="setting-row"><div class="grow"><strong>{{ item.name }}</strong><small>{{ item.description || '独立工具' }}</small><small v-if="item.capabilities.length">能力：{{ item.capabilities.join('、') }}</small></div><span class="tag">{{ item.available ? '可用' : '不可用' }}</span><button class="secondary-button" type="button" :disabled="!item.available" :aria-label="'调用工具 ' + item.name" @click="openCapability('tool', item)">调用</button></div><div v-if="!s.runtime?.tools?.length" class="setting-row"><div class="grow"><strong>暂无已注册工具</strong><small>在后端工具注册入口添加工具后会显示在这里。</small></div></div></div>
          <div class="section-label">技能</div><div class="surface-card"><div class="setting-row"><div class="grow"><strong>技能目录</strong><small>由后端技能目录中的 SKILL.md 文件提供使用说明；技能和工具分别加载。</small></div></div><div v-for="item in s.runtime?.skills || []" :key="item.name" class="setting-row"><div class="grow"><strong>{{ item.name }}</strong><small>{{ item.description || '技能指令' }}</small></div><button class="secondary-button" type="button" @click="openCapability('skill', item)">查看说明</button><button class="secondary-button" type="button" :disabled="s.streaming" @click="useSelectedSkill(item.name)">用于对话</button></div><div v-if="!s.runtime?.skills?.length" class="setting-row"><div class="grow"><strong>暂无可用技能</strong><small>独立工具仍可以正常使用。</small></div></div><div v-if="s.runtime?.skillWarnings?.length" class="setting-row"><div class="grow"><strong>{{ USER_FACING_ERROR }}</strong></div></div></div>
          <div class="section-label">智能体</div><div class="surface-card"><div class="setting-row"><div class="grow"><strong>内置文字助手</strong><small>可选择通用、编程和视频脚本助手；视频导演只提供策划文字，不会生成视频。</small></div><button class="secondary-button" type="button" @click="showModal('agents')">选择智能体</button></div><div v-for="item in agents" :key="item.id" class="setting-row"><span class="small-avatar">{{ item.glyph || item.name?.slice(0, 1) }}</span><div class="grow"><strong>{{ item.name }}</strong><small>{{ item.description || '' }}</small></div><span v-if="String(item.id) === String(s.selectedAgentId)" class="tag">当前智能体</span><button v-else class="subtle-button" type="button" @click="selectAgent(item.id)">选择</button></div></div>
        </div>
      </section>

      <div v-if="s.streaming && s.view !== 'home'" class="active-run-banner" role="status"><span class="run-orb" aria-hidden="true" /><span class="active-run-copy">Ficagent {{ generationLabel }}…</span><button type="button" @click="setView('home')">查看回复</button><button type="button" @click="stopMessage">停止</button></div>
      <div v-if="s.openingTaskId || s.openTaskError" class="task-open-status" :class="{ failed: Boolean(s.openTaskError) }" role="status" aria-live="polite"><span v-if="s.openingTaskId" class="task-open-spinner" aria-hidden="true" /><span class="task-open-message">{{ s.openTaskError ? USER_FACING_ERROR : '正在打开任务…' }}</span><button v-if="s.openTaskError" type="button" @click="openTask(s.openTaskError.id)">重试</button><button v-if="s.openTaskError" type="button" aria-label="关闭提示" @click="s.openTaskError = null">×</button></div>
    </main>
  </div>

  <CapabilityDialog v-if="capabilityDialog && s.authenticated" :target="capabilityDialog" @close="capabilityDialog = null" @use-skill="useSelectedSkill" />
  <SourcesPanel v-if="sourcePanel" :sources="sourcePanel.sources" :skills="sourcePanel.skills" :tools="sourcePanel.tools" :focus-url="sourcePanel.focusUrl" @close="closeSources" />

  <input ref="filePicker" id="file-picker" type="file" hidden multiple @change="uploadFiles" />

  <div v-if="s.modal" id="modal-root">
    <div class="modal-overlay" @click.self="closeModal()">
      <div ref="dialog" class="modal-card" :class="{ wide: ['preview', 'search'].includes(s.modal.kind) }" role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex="-1" :aria-busy="s.modalBusy" @keydown="onDialogKeydown">
        <div class="modal-head"><h2 id="modal-title">{{ s.modal.kind === 'project' ? '创建项目' : s.modal.kind === 'schedule' ? '新建日程' : s.modal.kind === 'agents' ? '选择智能体' : s.modal.kind === 'models' ? '选择模型' : s.modal.kind === 'projects' ? '选择项目' : s.modal.kind === 'attachments' ? '添加文件' : s.modal.kind === 'search' ? '搜索任务' : s.modal.kind === 'preview' ? activePreviewFile?.name || '文件预览' : s.modal.title }}</h2><button class="icon-button" type="button" title="关闭" aria-label="关闭" @click="closeModal()"><Icon name="close" small /></button></div>
        <template v-if="s.modal.kind === 'project'">
          <div class="modal-body"><p>把相关任务和文件放在一起，便于持续处理。</p><label for="modal-project-name">项目名称</label><input id="modal-project-name" v-model="projectName" maxlength="80" placeholder="例如：产品方案" @keydown.enter.prevent="submitModal" /></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="closeModal()">取消</button><button class="primary-button" type="button" :disabled="s.modalBusy" @click="submitModal">{{ s.modalBusy ? '请稍候…' : '创建项目' }}</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'schedule'">
          <div class="modal-body"><label for="modal-schedule-title">标题</label><input id="modal-schedule-title" v-model="scheduleTitle" maxlength="100" placeholder="例如：检查项目进度" @keydown.enter.prevent="submitModal" /><label for="modal-schedule-at">提醒时间</label><input id="modal-schedule-at" v-model="scheduleAt" type="datetime-local" /><p>工作台保持运行时，会在提醒时间显示通知。</p></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="closeModal()">取消</button><button class="primary-button" type="button" :disabled="s.modalBusy" @click="submitModal">{{ s.modalBusy ? '请稍候…' : '添加日程' }}</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'confirm'">
          <div class="modal-body"><p>{{ s.modal.message }}</p></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="closeModal()">取消</button><button class="secondary-button danger-button" type="button" :disabled="s.modalBusy" @click="submitModal">{{ s.modalBusy ? '请稍候…' : '删除' }}</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'agents'">
          <div class="modal-body"><p>选择一个文字助手。视频导演可提供脚本和分镜建议，不会生成视频。</p><div class="modal-list"><button v-for="item in agents" :key="item.id" class="modal-option" :class="{ selected: String(item.id) === String(s.selectedAgentId) }" type="button" @click="selectAgent(item.id)"><span class="small-avatar">{{ item.glyph || item.name?.slice(0, 1) }}</span><span class="grow">{{ item.name }}<small>{{ item.description || '' }}</small></span><Icon v-if="String(item.id) === String(s.selectedAgentId)" name="check" small /></button></div></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="closeModal()">关闭</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'models'">
          <div class="modal-body"><div v-if="s.models.length" class="modal-list"><button v-for="item in s.models" :key="item.id" class="modal-option" :class="{ selected: String(item.id) === String(s.selectedModelId) }" type="button" @click="selectModel(item.id)"><Icon name="spark" small /><span class="grow">{{ item.name }}</span><Icon v-if="String(item.id) === String(s.selectedModelId)" name="check" small /></button></div><p v-else>当前没有可用模型。请检查网站后端的模型配置。</p></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="closeModal(); setView('settings')">查看设置</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'projects'">
          <div class="modal-body"><div class="modal-list"><button class="modal-option" :class="{ selected: !s.selectedProjectId }" type="button" @click="selectProject('')"><Icon name="folder" small /><span class="grow">无项目<small>作为独立任务保存</small></span></button><button v-for="item in s.projects" :key="item.id" class="modal-option" :class="{ selected: String(item.id) === String(s.selectedProjectId) }" type="button" @click="selectProject(item.id)"><Icon name="folder" small /><span class="grow">{{ item.name }}</span></button></div></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="showModal('project')">创建项目</button></div>
        </template>
          <template v-else-if="s.modal.kind === 'application-input' && s.selectedApplication">
            <div class="modal-body"><h3>{{ s.selectedApplication.spec.name }}</h3><p>{{ s.selectedApplication.spec.description || s.selectedApplication.spec.goal }}</p><p>本轮将运行此应用。请确认输入参数；后续沿用应用已有的执行和确认规则。</p><AgentInputForm :key="s.user.id + s.selectedApplication.agentRef" :draft-key="platform.draftKey('invoke', s.selectedApplication.agentRef)" :spec="s.selectedApplication.spec" :files="s.files" fresh :busy="s.modalBusy" submit-label="确认参数并运行应用" @submit="runSelectedApplication" @upload="payload => chooseFiles('agent', payload)" /></div>
          </template>
          <template v-else-if="s.modal.kind === 'attachments'">
          <div class="modal-body">
            <button class="secondary-button attachment-upload" type="button" @click="chooseFiles('composer')"><Icon name="upload" small /> 上传新文件</button>
            <div class="section-label">数据管理中的文件 · {{ personalDataFiles.length }}</div>
            <div v-if="personalDataFiles.length" class="modal-list" aria-label="数据管理中的文件">
              <button v-for="item in personalDataFiles" :key="item.id" class="modal-option" :title="fileIdentityHint(item)" :class="{ selected: s.attachedFileIds.some((id) => String(id) === String(item.id)) }" type="button" @click="toggleAttachment(item.id)"><Icon name="file" small /><span class="grow">{{ fileLabel(item) }}<small>{{ fileSize(item.size) }} · 已保存在数据管理</small></span><Icon v-if="s.attachedFileIds.some((id) => String(id) === String(item.id))" name="check" small /></button>
            </div>
            <p v-else>还没有数据文件，可先到数据管理上传。</p>
            <div class="section-label">其他已有文件 · {{ otherFiles.length }}</div>
            <div v-if="otherFiles.length" class="modal-list" aria-label="其他已有文件">
              <button v-for="item in otherFiles" :key="item.id" class="modal-option" :title="fileIdentityHint(item)" :class="{ selected: s.attachedFileIds.some((id) => String(id) === String(item.id)) }" type="button" @click="toggleAttachment(item.id)"><Icon name="file" small /><span class="grow">{{ fileLabel(item) }}<small>{{ fileSize(item.size) }}</small></span><Icon v-if="s.attachedFileIds.some((id) => String(id) === String(item.id))" name="check" small /></button>
            </div>
          </div>
          <div class="modal-actions"><button class="primary-button" type="button" @click="closeModal()">完成</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'search'">
          <div class="modal-body"><input id="task-search" v-model="s.searchQuery" class="search-input" placeholder="输入任务标题…" @keydown.enter.prevent="searchResults[0] && openTask(searchResults[0].id)" /><div id="search-results" class="modal-list"><button v-for="item in searchResults" :key="item.id" class="modal-option" type="button" @click="openTask(item.id)"><Icon name="history" small /><span class="grow">{{ item.title || '未命名任务' }}<small>{{ formatDate(item.updatedAt) }}</small></span></button><div v-if="!searchResults.length" class="preview-fallback">没有找到任务。</div></div></div>
        </template>
        <template v-else-if="s.modal.kind === 'preview'">
          <div class="modal-body"><div class="preview-body" id="preview-body">
            <div v-if="!activePreviewFile" class="preview-fallback">文件不存在。</div>
            <pre v-else-if="s.previewText !== null">{{ s.previewText }}</pre>
            <img v-else-if="s.previewUrl && ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp'].includes(previewExtension)" :src="s.previewUrl" :alt="activePreviewFile.name" />
            <iframe v-else-if="s.previewUrl && previewExtension === 'pdf'" :src="s.previewUrl" :title="activePreviewFile.name" />
            <div v-else-if="!s.previewLoaded" class="preview-fallback">正在加载预览…</div>
            <div v-else class="preview-fallback">此格式暂无内置预览，请下载后打开。</div>
          </div></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="downloadFile(s.modal.id)"><Icon name="download" small /> 下载</button><button class="primary-button" type="button" @click="closeModal()">关闭</button></div>
        </template>
      </div>
    </div>
  </div>

  <AuthoringWorkspace v-if="s.authenticated && authoringStart" :key="String(s.user?.id)" :owner-id="String(s.user?.id || '')" :controller="authoring" :context="authoringStart" :files="s.files" @close="authoringStart = null" @imported="portableRevision++" @started="trackStarted" />
  <AuthoringPanel v-if="s.authenticated && authoring.s.open && !authoringStart" :key="String(s.user?.id)" :owner-id="String(s.user?.id || '')" :controller="authoring" :files="s.files" @asset="openCreatedAsset" @chat="continueAuthoringChat" @connections="authoring.close(); manageExtension({assetId:''})" />
  <div id="toast-root" role="status" aria-live="polite"><div v-for="item in notices" :key="item.id" class="toast" :class="{ error: item.error }">{{ item.error ? USER_FACING_ERROR : item.message }}</div></div>
</template>

<style scoped>
.creation-recovery{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;padding:12px 16px;margin:12px 0;border:1px solid var(--line);border-radius:12px;color:var(--text);font-size:12px}.creation-recovery button{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--surface,#fff);color:var(--accent);cursor:pointer}.creation-recovery button:disabled{opacity:.5;cursor:not-allowed}
.author-chat-context{display:flex;align-items:center;gap:12px;font-size:12px;line-height:1.8;margin:0 auto 10px;max-width:760px;color:var(--muted);overflow-wrap:anywhere}.author-chat-context button{flex:none;border:0;background:none;color:var(--accent);text-decoration:underline;cursor:pointer}.conversation-capture{margin:18px 0}.legacy-applications{max-width:1060px;margin:32px auto;padding:24px 0;border-top:1px solid var(--line)}.legacy-applications>summary{font-size:13px;color:var(--muted);cursor:pointer}.legacy-applications>p{font-size:12px;line-height:1.8;color:var(--muted);margin:14px 0 25px}
</style>
