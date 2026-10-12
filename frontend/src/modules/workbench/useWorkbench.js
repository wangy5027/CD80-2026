import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { chatResponse, fileResponse, readSse, request } from './api.js';

const builtinAgents = [
  { id: 'assistant', name: 'Ficagent助手', description: '通用文字助手', glyph: 'F' },
  { id: 'coder', name: '编程助手', description: '代码与技术任务', glyph: '</>' },
  { id: 'director', name: '视频导演', description: '策划脚本与分镜', glyph: '▶' },
];
const textExtensions = new Set(['txt', 'md', 'json', 'csv', 'tsv', 'js', 'ts', 'py', 'html', 'css', 'xml', 'yaml', 'yml', 'log']);
const imageExtensions = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']);

export function formatDate(value) {
  if (!value) return '时间未设置';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

export function relativeTime(value) {
  const time = new Date(value).getTime();
  if (!time) return '';
  const minutes = Math.max(0, Math.floor((Date.now() - time) / 60000));
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return String(minutes) + ' 分';
  if (minutes < 1440) return String(Math.floor(minutes / 60)) + ' 小时';
  return String(Math.floor(minutes / 1440)) + ' 天';
}

export function fileSize(value) {
  const number = Number(value) || 0;
  if (number < 1024) return String(number) + ' B';
  if (number < 1048576) return (number / 1024).toFixed(1) + ' KB';
  return (number / 1048576).toFixed(1) + ' MB';
}

export function useWorkbench() {
  const s = reactive({
    booting: true, authenticated: false, authMode: 'login', authError: '', authBusy: false,
    authEmail: '', authName: '', authPassword: '', showPassword: false,
    user: null, models: [], agents: [], projects: [], tasks: [], schedules: [], files: [],
    selectedAgentId: localStorage.getItem('cd80-agent') || 'assistant',
    selectedModelId: localStorage.getItem('cd80-model') || '',
    selectedProjectId: localStorage.getItem('cd80-project') || '',
    sidebarCollapsed: localStorage.getItem('cd80-sidebar-collapsed') === '1',
    currentTaskId: null, currentTask: null, taskModelUnavailable: false,
    openingTaskId: null, openTaskError: null, view: 'home', draft: '', attachedFileIds: [], showJump: false,
    streaming: false, streamError: '', modal: null, modalBusy: false,
    previewUrl: null, previewText: null, previewLoaded: false, searchQuery: '', studioOpening: false, manualRefreshing: false,
  });
  const notices = ref([]);
  const filePicker = ref(null);
  const composer = ref(null);
  const conversation = ref(null);
  const dialog = ref(null);
  const projectName = ref('');
  const scheduleTitle = ref('');
  const scheduleAt = ref('');
  const uploads = reactive(new Map());
  const drafts = new Map();
  const uploadControllers = new Set();
  const notifiedSchedules = new Set();
  let notifiedStorageKey = '';
  let streamAbort = null;
  let taskAbort = null;
  let previewAbort = null;
  let taskSequence = 0;
  let previewSequence = 0;
  let authEpoch = 0;
  let toastSequence = 0;
  let selectedUpload = null;
  let focusBeforeModal = null;
  let scheduleTimer = null;
  let refreshTimer = null;
  let refreshBusy = false;

  const agents = computed(() => {
    const byId = new Map(builtinAgents.map((agent) => [agent.id, agent]));
    for (const agent of s.agents) {
      const id = String(agent.id);
      byId.set(id, {
        ...agent,
        name: id === 'assistant' ? 'Ficagent助手' : agent.name,
        glyph: id === 'assistant' ? 'F' : agent.glyph || byId.get(id)?.glyph || String(agent.name || 'F').slice(0, 1),
      });
    }
    return [...byId.values()];
  });
  const agent = computed(() => agents.value.find((item) => String(item.id) === String(s.selectedAgentId)) || agents.value[0]);
  const model = computed(() => s.models.find((item) => String(item.id) === String(s.selectedModelId)) || null);
  const project = computed(() => s.projects.find((item) => String(item.id) === String(s.selectedProjectId)) || null);
  const recentTasks = computed(() => [...s.tasks].sort((a, b) => new Date(b.updatedAt) - new Date(a.updatedAt)).slice(0, 9));
  const searchResults = computed(() => s.tasks.filter((task) => !s.searchQuery.trim() || String(task.title || '').toLowerCase().includes(s.searchQuery.trim().toLowerCase())).slice(0, 30));
  const visibleFiles = computed(() => s.selectedProjectId ? s.files.filter((file) => String(file.projectId) === String(s.selectedProjectId)) : s.files);
  const sortedSchedules = computed(() => [...s.schedules].sort((a, b) => new Date(a.at) - new Date(b.at)));
  const attachedFiles = computed(() => s.attachedFileIds.map((id) => s.files.find((file) => String(file.id) === String(id)) || { id, name: '已选择文件' }));
  const uploadCount = computed(() => uploads.get(uploadKey()) || 0);
  const activePreviewFile = computed(() => s.files.find((file) => String(file.id) === String(s.modal?.id)) || null);
  const previewExtension = computed(() => String(activePreviewFile.value?.name || '').split('.').pop().toLowerCase());
  const generationLabel = computed(() => s.currentTask?.messages?.at(-1)?.content ? '正在回复' : '等待模型响应');

  function toast(message, error = false) {
    const id = ++toastSequence;
    notices.value.push({ id, message: String(message || '操作失败'), error });
    setTimeout(() => { notices.value = notices.value.filter((item) => item.id !== id); }, 4400);
  }
  function contextKey() {
    return s.currentTaskId ? 'task:' + s.currentTaskId : 'home:' + s.selectedAgentId + ':' + (s.selectedProjectId || '');
  }
  function uploadKey(epoch = authEpoch, key = contextKey()) { return String(epoch) + ':' + key; }
  function rememberDraft() {
    drafts.set(contextKey(), { draft: s.draft, fileIds: [...s.attachedFileIds] });
  }
  function restoreDraft() {
    const saved = drafts.get(contextKey());
    s.draft = saved?.draft || '';
    s.attachedFileIds = (saved?.fileIds || []).filter((id) => s.files.some((file) => String(file.id) === String(id)));
  }
  function updateDraft(value) { s.draft = value; rememberDraft(); }
  function cancelTaskOpening() {
    taskSequence++;
    taskAbort?.abort();
    taskAbort = null;
    s.openingTaskId = null;
    s.openTaskError = null;
  }
  function releasePreview() {
    previewSequence++;
    previewAbort?.abort();
    previewAbort = null;
    if (s.previewUrl) URL.revokeObjectURL(s.previewUrl);
    s.previewUrl = null;
    s.previewText = null;
    s.previewLoaded = false;
  }
  function invalidateAccountRequests() {
    authEpoch++;
    streamAbort?.abort();
    streamAbort = null;
    s.streaming = false;
    for (const controller of uploadControllers) controller.abort();
    uploadControllers.clear();
    uploads.clear();
    selectedUpload = null;
    releasePreview();
    cancelTaskOpening();
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = null;
  }
  function clearAccountData() {
    drafts.clear();
    s.draft = '';
    s.attachedFileIds = [];
    s.currentTask = null;
    s.currentTaskId = null;
    s.taskModelUnavailable = false;
    s.projects = [];
    s.tasks = [];
    s.schedules = [];
    s.files = [];
    s.selectedProjectId = '';
    localStorage.removeItem('cd80-project');
    s.modal = null;
    s.modalBusy = false;
    s.view = 'home';
  }
  function resetTaskModel() {
    s.taskModelUnavailable = false;
    if (model.value) return;
    const preferred = localStorage.getItem('cd80-model');
    s.selectedModelId = s.models.find((item) => String(item.id) === String(preferred))?.id || s.models[0]?.id || '';
  }
  function applySession(session, requestEpoch = authEpoch) {
    if (requestEpoch !== authEpoch) return false;
    const before = s.authenticated ? String(s.user?.id || s.user?.email || 'authenticated') : '';
    const after = session?.authenticated ? String(session.user?.id || session.user?.email || 'authenticated') : '';
    const changed = before !== after;
    if (changed) { invalidateAccountRequests(); clearAccountData(); }
    s.authenticated = Boolean(session?.authenticated);
    s.user = session?.user || null;
    s.models = Array.isArray(session?.models) ? session.models : [];
    s.agents = Array.isArray(session?.agents) ? session.agents : [];
    if (s.taskModelUnavailable) s.selectedModelId = '';
    else if (!s.models.some((item) => String(item.id) === String(s.selectedModelId))) {
      if (s.currentTask?.modelId) { s.taskModelUnavailable = true; s.selectedModelId = ''; }
      else { s.selectedModelId = s.models[0]?.id || ''; if (s.selectedModelId) localStorage.setItem('cd80-model', String(s.selectedModelId)); }
    }
    if (!agents.value.some((item) => String(item.id) === String(s.selectedAgentId))) s.selectedAgentId = 'assistant';
    return changed;
  }
  async function loadState(requestEpoch = authEpoch) {
    const data = await request('/state');
    if (requestEpoch !== authEpoch || !s.authenticated) return false;
    s.projects = Array.isArray(data?.projects) ? data.projects : [];
    s.tasks = Array.isArray(data?.tasks) ? data.tasks : [];
    s.schedules = Array.isArray(data?.schedules) ? data.schedules : [];
    s.files = Array.isArray(data?.files) ? data.files : [];
    if (s.selectedProjectId && !s.projects.some((item) => String(item.id) === String(s.selectedProjectId))) {
      rememberDraft();
      s.selectedProjectId = '';
      localStorage.removeItem('cd80-project');
      restoreDraft();
    }
    checkSchedules();
    return true;
  }
  async function bootstrap() {
    let epoch = authEpoch;
    try {
      const session = await request('/session');
      if (epoch !== authEpoch) return;
      applySession(session, epoch);
      epoch = authEpoch;
      if (session?.backendUnavailable) s.authError = session.error || '网站后端无法连接 Coze，请确认 Coze 服务正在运行。';
      if (s.authenticated) await loadState(epoch);
    } catch (error) {
      if (epoch === authEpoch) s.authError = error.message;
    } finally { s.booting = false; }
  }
  async function refreshState() {
    const epoch = authEpoch;
    try { return await loadState(epoch); }
    catch (error) { if (epoch === authEpoch) toast(error.message, true); return false; }
  }
  async function refreshSession(silent = false) {
    if (s.streaming) return false;
    let epoch = authEpoch;
    try {
      const session = await request('/session');
      if (epoch !== authEpoch || s.streaming || s.modalBusy) return false;
      if (session?.backendUnavailable) throw new Error(session.error || '网站后端无法连接 Coze，请确认 Coze 服务正在运行。');
      const changed = applySession(session, epoch);
      epoch = authEpoch;
      if (changed && s.authenticated) await loadState(epoch);
      return s.authenticated;
    } catch (error) { if (!silent && epoch === authEpoch) toast(error.message, true); return false; }
  }
  function requestResourceRefresh() {
    if (!s.authenticated || s.streaming || s.modalBusy || refreshTimer || refreshBusy) return;
    refreshTimer = setTimeout(async () => {
      refreshTimer = null;
      if (!s.authenticated || s.streaming || s.modalBusy) return;
      refreshBusy = true;
      try { await refreshSession(true); } finally { refreshBusy = false; }
    }, 800);
  }
  async function manualRefresh() {
    if (s.manualRefreshing) return;
    s.manualRefreshing = true;
    try { if (await refreshSession()) { await refreshState(); toast('已刷新。'); } }
    finally { s.manualRefreshing = false; }
  }
  function setView(view) {
    cancelTaskOpening();
    s.view = view;
    s.streamError = '';
  }
  function toggleSidebar() {
    s.sidebarCollapsed = !s.sidebarCollapsed;
    localStorage.setItem('cd80-sidebar-collapsed', s.sidebarCollapsed ? '1' : '0');
  }
  function newTask() {
    if (s.streaming) { toast('请先停止或等待当前回复。'); return; }
    cancelTaskOpening();
    rememberDraft();
    s.currentTaskId = null;
    s.currentTask = null;
    restoreDraft();
    resetTaskModel();
    s.streamError = '';
    s.view = 'home';
    composer.value?.focus();
  }
  async function openTask(id) {
    if (s.streaming) { toast('请先停止或等待当前回复。'); return; }
    const requestedId = String(id || '');
    if (!requestedId || s.openingTaskId === requestedId) return;
    cancelTaskOpening();
    const sequence = ++taskSequence;
    const controller = new AbortController();
    taskAbort = controller;
    s.openingTaskId = requestedId;
    try {
      const task = await request('/tasks/' + encodeURIComponent(requestedId), { signal: controller.signal });
      if (sequence !== taskSequence || s.streaming) return;
      rememberDraft();
      s.currentTaskId = task.id || requestedId;
      s.currentTask = task;
      s.streamError = '';
      if (task.agentId) s.selectedAgentId = task.agentId;
      s.selectedProjectId = task.projectId || '';
      if (task.modelId) {
        const found = s.models.find((item) => String(item.id) === String(task.modelId));
        s.selectedModelId = found?.id || '';
        s.taskModelUnavailable = !found;
      } else resetTaskModel();
      restoreDraft();
      s.view = 'home';
      closeModal(true);
      nextTick(scrollConversation);
    } catch (error) {
      if (sequence !== taskSequence || error.name === 'AbortError') return;
      s.openTaskError = { id: requestedId, message: error.message };
      toast('打开任务失败：' + error.message, true);
    } finally {
      if (sequence === taskSequence) { taskAbort = null; s.openingTaskId = null; }
    }
  }
  function selectAgent(id) {
    if (s.streaming) { toast('请先停止或等待当前回复。'); return; }
    if (!agents.value.some((item) => String(item.id) === String(id))) return;
    cancelTaskOpening();
    rememberDraft();
    s.selectedAgentId = id;
    localStorage.setItem('cd80-agent', String(id));
    s.view = 'home';
    s.currentTask = null;
    s.currentTaskId = null;
    resetTaskModel();
    restoreDraft();
    closeModal(true);
  }
  function selectProject(id) {
    if (s.streaming) { toast('请先停止或等待当前回复。'); return; }
    cancelTaskOpening();
    rememberDraft();
    if (s.currentTask && String(s.currentTask.projectId || '') !== String(id || '')) {
      s.currentTask = null;
      s.currentTaskId = null;
      resetTaskModel();
      s.view = 'home';
    }
    s.selectedProjectId = id || '';
    if (id) localStorage.setItem('cd80-project', String(id));
    else localStorage.removeItem('cd80-project');
    restoreDraft();
    closeModal(true);
  }
  function selectModel(id) {
    if (s.streaming) { toast('请等待当前回复结束后再切换模型。'); return; }
    if (!s.models.some((item) => String(item.id) === String(id))) return;
    cancelTaskOpening();
    s.selectedModelId = id;
    s.taskModelUnavailable = false;
    localStorage.setItem('cd80-model', String(id));
    closeModal(true);
  }
  async function submitAuth() {
    if (s.authBusy) return;
    invalidateAccountRequests();
    let epoch = authEpoch;
    const mode = s.authMode;
    s.authBusy = true;
    s.authError = '';
    try {
      const values = { email: s.authEmail, password: s.authPassword };
      if (mode === 'register') values.name = s.authName;
      await request(mode === 'register' ? '/register' : '/login', { method: 'POST', body: values });
      if (epoch !== authEpoch) return;
      const session = await request('/session');
      if (epoch !== authEpoch) return;
      applySession(session, epoch);
      epoch = authEpoch;
      if (!s.authenticated && mode === 'register') {
        s.authMode = 'login';
        toast('账号已创建，请登录。');
        return;
      }
      if (!s.authenticated) throw new Error('登录未完成，请重试。');
      await loadState(epoch);
      if (epoch !== authEpoch) return;
      s.authEmail = '';
      s.authName = '';
      s.authPassword = '';
      s.view = 'home';
    } catch (error) {
      if (epoch === authEpoch) {
        s.authError = error.message;
        if (s.authenticated) toast('已登录，但资料加载失败：' + error.message, true);
      }
    } finally { s.authBusy = false; }
  }
  async function logout() {
    if (s.streaming) { toast('请先停止或等待当前回复。'); return; }
    invalidateAccountRequests();
    const epoch = authEpoch;
    try {
      const result = await request('/logout', { method: 'POST' });
      if (epoch !== authEpoch) return;
      clearAccountData();
      s.authenticated = false;
      s.user = null;
      s.models = [];
      s.agents = [];
      s.authError = '';
      if (result?.warning) toast(result.warning, true);
    } catch (error) { if (epoch === authEpoch) toast(error.message, true); }
  }
  function defaultScheduleTime() {
    const date = new Date(Date.now() + 3600000);
    date.setMinutes(0, 0, 0);
    const pad = (part) => String(part).padStart(2, '0');
    return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + 'T' + pad(date.getHours()) + ':' + pad(date.getMinutes());
  }
  function showModal(kind, data = {}) {
    if (s.openingTaskId) cancelTaskOpening();
    if (s.modal?.kind === 'preview') releasePreview();
    if (!s.modal) focusBeforeModal = document.activeElement;
    s.modal = { kind, ...data };
    s.modalBusy = false;
    if (kind === 'project') projectName.value = '';
    if (kind === 'schedule') { scheduleTitle.value = ''; scheduleAt.value = defaultScheduleTime(); }
    if (kind === 'search') s.searchQuery = '';
    nextTick(() => dialog.value?.querySelector('input, button')?.focus());
  }
  function closeModal(force = false) {
    if (!s.modal || (s.modalBusy && !force)) return;
    if (s.modal.kind === 'preview') releasePreview();
    s.modal = null;
    s.modalBusy = false;
    nextTick(() => { if (focusBeforeModal?.isConnected) focusBeforeModal.focus(); focusBeforeModal = null; });
  }
  async function createProject() {
    const name = projectName.value.trim();
    if (!name) { toast('请输入项目名称。', true); return; }
    s.modalBusy = true;
    const epoch = authEpoch;
    try {
      const created = await request('/projects', { method: 'POST', body: { name } });
      if (epoch !== authEpoch) return;
      if (created?.id) s.projects = [created, ...s.projects.filter((item) => String(item.id) !== String(created.id))];
      closeModal(true);
      if (created?.id) selectProject(created.id);
      toast('项目已创建。');
      await refreshState();
    } catch (error) { if (epoch === authEpoch) toast(error.message, true); }
    finally { s.modalBusy = false; }
  }
  async function createSchedule() {
    const title = scheduleTitle.value.trim();
    const date = scheduleAt.value && new Date(scheduleAt.value);
    if (!title || !date || Number.isNaN(date.getTime())) { toast('请填写标题和有效的提醒时间。', true); return; }
    s.modalBusy = true;
    const epoch = authEpoch;
    try {
      const created = await request('/schedules', { method: 'POST', body: { title, at: date.toISOString() } });
      if (epoch !== authEpoch) return;
      if (created?.id) s.schedules = [created, ...s.schedules.filter((item) => String(item.id) !== String(created.id))];
      closeModal(true);
      toast('日程已添加。');
      if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission().catch(() => {});
      await refreshState();
    } catch (error) { if (epoch === authEpoch) toast(error.message, true); }
    finally { s.modalBusy = false; }
  }
  function confirmDelete(target, id) {
    if (s.streaming && ['project', 'task'].includes(target)) { toast('请先停止或等待当前回复。'); return; }
    const description = {
      project: ['删除项目', '删除这个项目？其中的任务和文件会保留，并移到“无项目”。'],
      task: ['删除任务', '确定删除这条任务及其对话记录？'],
      schedule: ['删除日程', '确定删除这条日程？'],
      file: ['删除文件', '确定从本地工作台删除这个文件？'],
    }[target];
    showModal('confirm', { target, id, title: description[0], message: description[1] });
  }
  async function deleteSelected() {
    const selected = s.modal;
    if (!selected || selected.kind !== 'confirm') return;
    s.modalBusy = true;
    const epoch = authEpoch;
    const endpoints = { project: 'projects', task: 'tasks', schedule: 'schedules', file: 'files' };
    try {
      await request('/' + endpoints[selected.target] + '/' + encodeURIComponent(selected.id), { method: 'DELETE' });
      if (epoch !== authEpoch) return;
      if (selected.target === 'project') {
        if (String(s.selectedProjectId) === String(selected.id)) { s.selectedProjectId = ''; localStorage.removeItem('cd80-project'); }
        for (const key of drafts.keys()) if (key.startsWith('home:') && key.endsWith(':' + selected.id)) drafts.delete(key);
      }
      if (selected.target === 'task') {
        drafts.delete('task:' + selected.id);
        if (String(s.currentTaskId) === String(selected.id)) { s.currentTaskId = null; s.currentTask = null; restoreDraft(); }
      }
      if (selected.target === 'file') {
        s.attachedFileIds = s.attachedFileIds.filter((id) => String(id) !== String(selected.id));
        for (const saved of drafts.values()) saved.fileIds = saved.fileIds.filter((id) => String(id) !== String(selected.id));
      }
      await loadState(epoch);
      closeModal(true);
      toast('已删除。');
    } catch (error) { if (epoch === authEpoch) toast(error.message, true); }
    finally { s.modalBusy = false; }
  }
  async function submitModal() {
    if (s.modalBusy) return;
    if (s.modal?.kind === 'project') return createProject();
    if (s.modal?.kind === 'schedule') return createSchedule();
    if (s.modal?.kind === 'confirm') return deleteSelected();
  }
  async function toggleSchedule(item) {
    const epoch = authEpoch;
    try {
      await request('/schedules/' + encodeURIComponent(item.id), { method: 'PATCH', body: { done: !item.done } });
      if (epoch === authEpoch) await refreshState();
    } catch (error) { if (epoch === authEpoch) toast(error.message, true); }
  }
  function toggleAttachment(id) {
    const index = s.attachedFileIds.findIndex((value) => String(value) === String(id));
    if (index >= 0) s.attachedFileIds.splice(index, 1);
    else s.attachedFileIds.push(id);
    rememberDraft();
  }
  function removeAttachment(id) {
    s.attachedFileIds = s.attachedFileIds.filter((value) => String(value) !== String(id));
    rememberDraft();
  }
  function chooseFiles(kind) {
    selectedUpload = { epoch: authEpoch, kind, context: contextKey(), projectId: s.selectedProjectId };
    filePicker.value?.click();
  }
  async function uploadFiles(event) {
    const files = [...(event.target.files || [])];
    event.target.value = '';
    const selected = selectedUpload;
    selectedUpload = null;
    if (!selected || !files.length || selected.epoch !== authEpoch || !s.authenticated) return;
    const key = uploadKey(selected.epoch, selected.context);
    if (selected.kind === 'composer') uploads.set(key, (uploads.get(key) || 0) + files.length);
    const controller = new AbortController();
    uploadControllers.add(controller);
    let succeeded = 0;
    try {
      for (const file of files) {
        if (selected.epoch !== authEpoch) break;
        const form = new FormData();
        form.append('file', file);
        if (selected.projectId) form.append('projectId', selected.projectId);
        try {
          const result = await request('/files', { method: 'POST', body: form, signal: controller.signal });
          if (selected.epoch !== authEpoch) break;
          succeeded++;
          if (selected.kind === 'composer' && result?.id) {
            const saved = drafts.get(selected.context) || { draft: '', fileIds: [] };
            if (!saved.fileIds.some((id) => String(id) === String(result.id))) saved.fileIds.push(result.id);
            drafts.set(selected.context, saved);
            if (selected.context === contextKey()) s.attachedFileIds = [...saved.fileIds];
          }
        } catch (error) { if (selected.epoch === authEpoch) toast(file.name + '：' + error.message, true); }
        finally {
          if (selected.kind === 'composer' && selected.epoch === authEpoch) {
            const count = Math.max(0, (uploads.get(key) || 0) - 1);
            if (count) uploads.set(key, count); else uploads.delete(key);
          }
        }
      }
    } finally { uploadControllers.delete(controller); }
    if (selected.epoch !== authEpoch) return;
    await refreshState();
    if (succeeded) toast('已上传 ' + succeeded + ' 个文件。');
  }
  async function downloadFile(id) {
    const file = s.files.find((item) => String(item.id) === String(id));
    const epoch = authEpoch;
    try {
      const response = await fileResponse(id);
      const blob = await response.blob();
      if (epoch !== authEpoch) return;
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = file?.name || '下载文件';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    } catch (error) { if (epoch === authEpoch) toast(error.message, true); }
  }
  async function previewFile(id) {
    showModal('preview', { id });
    const file = s.files.find((item) => String(item.id) === String(id));
    if (!file) return;
    const epoch = authEpoch;
    const sequence = ++previewSequence;
    const controller = new AbortController();
    previewAbort = controller;
    const current = () => epoch === authEpoch && sequence === previewSequence && s.modal?.kind === 'preview' && String(s.modal.id) === String(id);
    try {
      const response = await fileResponse(id, controller.signal);
      const blob = await response.blob();
      if (!current()) return;
      const ext = String(file.name).split('.').pop().toLowerCase();
      if (textExtensions.has(ext)) {
        const content = await blob.text();
        if (current()) s.previewText = content.slice(0, 200000);
      } else if (imageExtensions.has(ext) || ext === 'pdf') s.previewUrl = URL.createObjectURL(blob);
      if (current()) s.previewLoaded = true;
    } catch (error) {
      if (current()) { toast(error.message, true); closeModal(true); }
    } finally { if (previewAbort === controller) previewAbort = null; }
  }
  function scrollConversation() {
    const box = conversation.value;
    if (box) { box.scrollTop = box.scrollHeight; s.showJump = false; }
  }
  function onConversationScroll() {
    const box = conversation.value;
    s.showJump = Boolean(box && box.scrollHeight - box.scrollTop - box.clientHeight >= 96);
  }
  function restoreLastPrompt() {
    if (s.streaming) return;
    if (s.draft.trim()) { toast('输入框已有内容，请先处理当前草稿。'); return; }
    const last = [...(s.currentTask?.messages || [])].reverse().find((message) => message.role === 'user');
    if (!last) { toast('这条任务没有可恢复的提问。', true); return; }
    s.draft = typeof last.content === 'string' ? last.content : String(last.content ?? '');
    const requested = Array.isArray(last.attachments) ? last.attachments : [];
    s.attachedFileIds = [...new Set(requested.map((file) => String(file?.id || '')).filter((id) => s.files.some((file) => String(file.id) === id)))];
    rememberDraft();
    composer.value?.focus();
    if (s.attachedFileIds.length < requested.length) toast('部分原附件已不存在，请重新选择。', true);
    else toast('已放回输入框；请检查后再发送。');
  }
  async function copyText(content) {
    const value = String(content ?? '');
    if (!value) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard API unavailable');
      await navigator.clipboard.writeText(value);
      toast('已复制。');
      return;
    } catch (_) { /* try the document fallback */ }
    const input = document.createElement('textarea');
    try {
      input.value = value;
      input.readOnly = true;
      input.style.position = 'fixed';
      input.style.opacity = '0';
      document.body.appendChild(input);
      input.select();
      if (!document.execCommand('copy')) throw new Error('copy failed');
      toast('已复制。');
    } catch (_) { toast('复制失败，请手动选择文本复制。', true); }
    finally { input.remove(); }
  }
  function messageContent(message) {
    return typeof message?.content === 'string' ? message.content : JSON.stringify(message?.content ?? '');
  }
  function renderAssistant(message, index) {
    return window.FicagentFormat.renderAssistant(messageContent(message), index);
  }
  async function sendMessage() {
    if (s.streaming) return;
    const message = s.draft.trim();
    if (!message) { composer.value?.focus(); return; }
    if (uploadCount.value) { toast('文件仍在上传，请等待完成后发送，以免遗漏附件。', true); return; }
    if (s.taskModelUnavailable || !model.value) {
      toast(s.taskModelUnavailable ? '此任务原模型不可用，请先明确选择一个模型。' : '请先在模型管理中配置并选择模型。', true);
      showModal('models');
      return;
    }
    cancelTaskOpening();
    const epoch = authEpoch;
    const attached = [...s.attachedFileIds];
    const taskId = s.currentTaskId;
    s.draft = '';
    s.attachedFileIds = [];
    rememberDraft();
    s.streamError = '';
    s.currentTask ||= { id: null, title: message.slice(0, 28), modelId: s.selectedModelId, messages: [] };
    s.currentTask.messages ||= [];
    s.currentTask.messages.push({ role: 'user', content: message, attachments: attached.map((id) => ({ id, name: s.files.find((file) => String(file.id) === String(id))?.name || '文件' })) });
    s.currentTask.messages.push({ role: 'assistant', content: '', streaming: true });
    s.streaming = true;
    s.view = 'home';
    nextTick(scrollConversation);
    const controller = new AbortController();
    streamAbort = controller;
    let sawDone = false;
    let gotMeta = false;
    try {
      const response = await chatResponse({
        message, agentId: s.selectedAgentId, modelId: s.selectedModelId,
        projectId: s.selectedProjectId || null, taskId: taskId || null, fileIds: attached,
      }, controller.signal);
      for await (const item of readSse(response.body)) {
        if (epoch !== authEpoch) return;
        const data = item.data;
        if (item.event === 'meta') {
          gotMeta = true;
          if (data.taskId) { s.currentTaskId = data.taskId; s.currentTask.id = data.taskId; }
          if (data.title) s.currentTask.title = data.title;
          rememberDraft();
        } else if (item.event === 'token') {
          const last = s.currentTask?.messages?.at(-1);
          if (last?.role === 'assistant') last.content = String(last.content || '') + String(data.text || '');
        } else if (item.event === 'error') s.streamError = data.message || data.error || '模型返回错误。';
        else if (item.event === 'done') {
          sawDone = true;
          if (data.task?.messages) s.currentTask = data.task;
          if (data.task?.id) s.currentTaskId = data.task.id;
          break;
        }
      }
      if (!sawDone && !controller.signal.aborted) throw new Error(s.streamError || '模型连接提前结束，请重试。');
      if (s.streamError) toast(s.streamError, true);
    } catch (error) {
      if (epoch !== authEpoch) return;
      if (error.name !== 'AbortError') { s.streamError = error.message; toast(error.message, true); }
      if (!gotMeta) {
        s.currentTask?.messages?.splice(-2, 2);
        s.draft = message;
        s.attachedFileIds = attached;
        if (!taskId) { s.currentTask = null; s.currentTaskId = null; }
        rememberDraft();
      }
    } finally {
      if (epoch !== authEpoch) return;
      s.streaming = false;
      streamAbort = null;
      const last = s.currentTask?.messages?.at(-1);
      if (last?.streaming) { delete last.streaming; if (!last.content) s.currentTask.messages.pop(); }
      if (s.currentTaskId) await refreshState();
      nextTick(scrollConversation);
      requestResourceRefresh();
    }
  }
  async function stopMessage() {
    if (!s.streaming) return;
    const id = s.currentTaskId;
    s.streamError = '已停止生成。';
    streamAbort?.abort();
    if (id) {
      try { await request('/stop', { method: 'POST', body: { taskId: id } }); }
      catch (error) { toast(error.message, true); }
    }
    toast('已停止生成。');
  }
  function setPrompt(value) {
    s.draft = value;
    rememberDraft();
    composer.value?.focus();
  }
  async function openStudio(kind) {
    if (s.studioOpening) return;
    s.studioOpening = true;
    try {
      const backendOrigin = import.meta.env.DEV
        ? import.meta.env.VITE_BACKEND_TARGET || 'http://127.0.0.1:8893'
        : window.location.origin;
      const opened = window.open(new URL('/desktop/coze/' + kind, backendOrigin).href, '_blank');
      if (!opened) throw new Error('浏览器未能打开管理页面，请检查弹窗设置后重试。');
      opened.opener = null;
      toast('已打开' + (kind === 'models' ? '模型管理' : 'Coze Studio') + '。');
      setTimeout(requestResourceRefresh, 1800);
    } catch (error) { toast(error.message || '无法打开 Coze Studio。', true); }
    finally { s.studioOpening = false; }
  }
  function checkSchedules() {
    if (!s.authenticated || !s.user) return;
    const identity = String(s.user.id || s.user.email || '');
    if (!identity) return;
    const key = 'cd80-notified-schedules:' + identity;
    if (key !== notifiedStorageKey) {
      notifiedStorageKey = key;
      notifiedSchedules.clear();
      try {
        const saved = JSON.parse(localStorage.getItem(key) || '[]');
        if (Array.isArray(saved)) saved.forEach((id) => notifiedSchedules.add(String(id)));
      } catch (_) { /* notifications still work for this run */ }
    }
    const ids = new Set(s.schedules.map((item) => String(item.id)));
    let changed = false;
    for (const id of notifiedSchedules) if (!ids.has(id)) { notifiedSchedules.delete(id); changed = true; }
    for (const item of s.schedules) {
      const at = new Date(item.at).getTime();
      if (!item.done && Number.isFinite(at) && at <= Date.now() && !notifiedSchedules.has(String(item.id))) {
        notifiedSchedules.add(String(item.id));
        changed = true;
        toast('日程提醒：' + item.title);
        if ('Notification' in window && Notification.permission === 'granted') {
          try { new Notification('Ficagent · 日程提醒', { body: item.title }); } catch (_) { /* in-app notice is shown */ }
        }
      }
    }
    if (changed) { try { localStorage.setItem(key, JSON.stringify([...notifiedSchedules])); } catch (_) { /* in-memory deduplication */ } }
  }
  function onVisibility() { if (!document.hidden) { checkSchedules(); requestResourceRefresh(); } }
  function onFocus() { checkSchedules(); requestResourceRefresh(); }
  function onKeydown(event) {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && s.authenticated) {
      event.preventDefault();
      showModal('search');
    }
    if (event.key === 'Escape' && s.modal) closeModal();
  }
  watch(() => s.currentTask?.messages?.at(-1)?.content, () => {
    const box = conversation.value;
    const follow = !box || box.scrollHeight - box.scrollTop - box.clientHeight < 96;
    if (follow) nextTick(scrollConversation);
  });
  onMounted(() => {
    bootstrap();
    scheduleTimer = setInterval(checkSchedules, 15000);
    window.addEventListener('focus', onFocus);
    document.addEventListener('visibilitychange', onVisibility);
    document.addEventListener('keydown', onKeydown);
  });
  onUnmounted(() => {
    invalidateAccountRequests();
    if (scheduleTimer) clearInterval(scheduleTimer);
    window.removeEventListener('focus', onFocus);
    document.removeEventListener('visibilitychange', onVisibility);
    document.removeEventListener('keydown', onKeydown);
  });

  return {
    s, notices, filePicker, composer, conversation, dialog, projectName, scheduleTitle, scheduleAt,
    agents, agent, model, project, recentTasks, searchResults, visibleFiles, sortedSchedules, attachedFiles,
    uploadCount, activePreviewFile, previewExtension, generationLabel, formatDate, relativeTime, fileSize,
    toast, updateDraft, refreshState, manualRefresh, setView, toggleSidebar, newTask, openTask, selectAgent, selectProject,
    selectModel, submitAuth, logout, showModal, closeModal, submitModal, toggleSchedule, confirmDelete,
    toggleAttachment, removeAttachment, chooseFiles, uploadFiles, downloadFile, previewFile, scrollConversation,
    restoreLastPrompt, copyText, messageContent, renderAssistant, sendMessage, stopMessage, setPrompt, openStudio,
    onConversationScroll,
    imageExtensions,
  };
}
