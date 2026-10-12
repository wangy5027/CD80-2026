<script setup>
import { computed } from 'vue';
import Icon from './components/Icon.vue';
import Composer from './components/Composer.vue';
import { useWorkbench } from './useWorkbench.js';

const {
  s, notices, filePicker, composer, conversation, dialog, projectName, scheduleTitle, scheduleAt,
  agents, agent, model, project, recentTasks, searchResults, visibleFiles, sortedSchedules, attachedFiles,
  uploadCount, activePreviewFile, previewExtension, generationLabel, formatDate, relativeTime, fileSize,
  updateDraft, manualRefresh, setView, toggleSidebar, newTask, openTask, selectAgent, selectProject, selectModel,
  submitAuth, logout, showModal, closeModal, submitModal, toggleSchedule, confirmDelete,
  toggleAttachment, removeAttachment, chooseFiles, uploadFiles, downloadFile, previewFile, scrollConversation,
  restoreLastPrompt, copyText, messageContent, renderAssistant, sendMessage, stopMessage, setPrompt, openStudio,
  onConversationScroll,
} = useWorkbench();

const prompts = [
  { title: '看看我能做什么', icon: 'code', text: '介绍一下你能帮我做什么，并列出适合现在开始的三个任务。' },
  { title: '规划一个新任务', icon: 'grid', text: '帮我把这个想法梳理成可执行的计划，先向我确认目标、截止时间和现有资料。' },
  { title: '构思视频脚本', icon: 'spark', text: '帮我整理一份视频创意方案。先询问产品、受众、渠道与时长，再给出脚本和分镜。' },
];
const taskFailure = computed(() => s.streaming ? '' : s.streamError || s.currentTask?.error || '');

function onRenderedMessageClick(event, message) {
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
          <div v-if="s.authError" class="auth-error" role="alert">{{ s.authError }}</div>
          <label v-if="s.authMode === 'register'" class="form-field"><span>你的称呼</span><input v-model="s.authName" name="name" type="text" autocomplete="name" placeholder="例如：小八" required maxlength="50" /></label>
          <label class="form-field"><span>邮箱</span><input v-model="s.authEmail" name="email" type="email" autocomplete="email" placeholder="name@example.com" required /></label>
          <label class="form-field"><span>密码</span><span class="password-wrap"><input v-model="s.authPassword" name="password" :type="s.showPassword ? 'text' : 'password'" :autocomplete="s.authMode === 'register' ? 'new-password' : 'current-password'" placeholder="请输入密码" required minlength="6" /><button type="button" :aria-label="s.showPassword ? '隐藏密码' : '显示密码'" @click="s.showPassword = !s.showPassword"><Icon :name="s.showPassword ? 'eyeOff' : 'eye'" /></button></span></label>
          <button class="primary-button auth-submit" type="submit" :disabled="s.authBusy">{{ s.authBusy ? '请稍候…' : s.authMode === 'register' ? '创建账号' : '登录' }}</button>
        </form>
        <div class="auth-switch">{{ s.authMode === 'register' ? '已有账号？' : '还没有账号？' }} <button type="button" @click="s.authMode = s.authMode === 'login' ? 'register' : 'login'; s.authError = ''">{{ s.authMode === 'register' ? '直接登录' : '创建账号' }}</button></div>
      </div>
    </main>
  </div>

  <div v-else class="app-shell" :class="{ collapsed: s.sidebarCollapsed }" :inert="Boolean(s.modal)">
    <aside class="sidebar">
      <div class="sidebar-top">
        <button class="icon-button" type="button" :title="s.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" :aria-label="s.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" :aria-expanded="!s.sidebarCollapsed" @click="toggleSidebar"><Icon name="panel" /></button>
        <button class="icon-button search-trigger" type="button" title="搜索任务 ⌘K" aria-label="搜索任务" @click="showModal('search')"><Icon name="search" /></button>
      </div>
      <div class="brand"><span class="brand-mark">F</span><span class="brand-name">Ficagent<span class="brand-sub">WEB WORKSPACE</span></span></div>
      <div class="side-scroll">
        <nav class="nav-group" aria-label="主导航">
          <button class="nav-row" :class="{ active: s.view === 'home' && !s.currentTask }" type="button" @click="newTask"><Icon name="addCircle" /><span>新任务</span></button>
          <button class="nav-row" :class="{ active: s.view === 'schedule' }" type="button" @click="setView('schedule')"><Icon name="calendar" /><span>日程</span></button>
          <button class="nav-row" :class="{ active: s.view === 'extensions' }" type="button" @click="setView('extensions')"><Icon name="grid" /><span>扩展</span></button>
          <button class="nav-row" :class="{ active: s.view === 'files' }" type="button" @click="setView('files')"><Icon name="folder" /><span>文件</span></button>
        </nav>
        <section class="side-section">
          <div class="side-heading"><span>智能体</span><button class="icon-button" type="button" title="配置智能体" aria-label="配置智能体" @click="setView('settings')"><Icon name="addCircle" small /></button></div>
          <button v-for="item in agents" :key="item.id" class="side-row" :class="{ active: String(item.id) === String(s.selectedAgentId) && s.view === 'home' }" type="button" :title="item.description || item.name" :aria-pressed="String(item.id) === String(s.selectedAgentId)" @click="selectAgent(item.id)"><span class="small-avatar">{{ item.glyph || item.name?.slice(0, 1) }}</span><span class="row-title">{{ item.name }}</span></button>
        </section>
        <section class="side-section">
          <div class="side-heading"><span>项目</span><button class="icon-button" type="button" title="新建项目" aria-label="新建项目" @click="showModal('project')"><Icon name="addCircle" small /></button></div>
          <template v-if="s.projects.length">
            <div v-for="item in s.projects" :key="item.id" class="side-row" :class="{ active: String(item.id) === String(s.selectedProjectId) }">
              <button class="side-row-button" type="button" :title="item.name" :aria-pressed="String(item.id) === String(s.selectedProjectId)" @click="selectProject(item.id)"><span class="small-avatar project-symbol"><Icon name="folder" small /></span><span class="row-title">{{ item.name }}</span></button>
              <button class="icon-button row-more" type="button" :title="'删除' + item.name" :aria-label="'删除' + item.name" @click="confirmDelete('project', item.id)"><Icon name="more" small /></button>
            </div>
          </template>
          <div v-else class="project-cta"><p>按项目整理任务和文件，<br />把持续的工作放在一起。</p><button type="button" @click="showModal('project')">创建项目</button></div>
        </section>
        <section v-if="recentTasks.length" class="side-section tasks-group">
          <div class="side-heading"><span>最近任务</span><button class="icon-button" type="button" title="搜索任务" aria-label="搜索任务" @click="showModal('search')"><Icon name="search" small /></button></div>
          <div v-for="item in recentTasks" :key="item.id" class="side-row" :class="{ active: s.view === 'home' && String(item.id) === String(s.currentTaskId) }">
            <button class="side-row-button" type="button" :aria-current="s.view === 'home' && String(item.id) === String(s.currentTaskId) ? 'page' : undefined" :aria-busy="String(item.id) === String(s.openingTaskId)" @click="openTask(item.id)"><span class="task-indicator"><Icon name="history" small /></span><span class="row-title">{{ item.title || '未命名任务' }}</span><span class="task-time">{{ relativeTime(item.updatedAt) }}</span></button>
            <button class="icon-button row-more" type="button" title="删除任务" :aria-label="'删除' + (item.title || '任务')" @click="confirmDelete('task', item.id)"><Icon name="more" small /></button>
          </div>
        </section>
      </div>
      <div class="sidebar-bottom"><button class="user-row" type="button" title="账号与设置" :aria-current="s.view === 'settings' ? 'page' : undefined" @click="setView('settings')"><span class="user-avatar">{{ String(s.user?.name || s.user?.email || 'F').slice(0, 1).toUpperCase() }}</span><span class="user-name">{{ s.user?.name || s.user?.email || '本地用户' }}</span><Icon name="gear" small /></button></div>
    </aside>

    <main class="main-panel">
      <template v-if="s.view === 'home'">
        <div v-if="!s.currentTask" class="page home-scroll">
          <div class="welcome">
            <h1>嗨，我是 <span class="headline-avatar">{{ agent?.glyph || 'F' }}</span> {{ agent?.name }}。<br class="mobile-break" />今天想做什么？</h1>
            <Composer ref="composer" :draft="s.draft" :agent="agent" :model="model" :project="project" :attachments="attachedFiles" :streaming="s.streaming" :uploading="uploadCount" :model-unavailable="s.taskModelUnavailable" :has-models="s.models.length > 0" @update:draft="updateDraft" @send="sendMessage" @attach="showModal('attachments')" @project="showModal('projects')" @model="showModal('models')" @agent="showModal('agents')" @remove-attachment="removeAttachment" />
            <div class="prompt-grid"><button v-for="item in prompts" :key="item.title" class="prompt-card" type="button" @click="setPrompt(item.text)"><Icon :name="item.icon" /><span>{{ item.title }}</span></button></div>
            <div v-if="s.streamError" class="send-error" role="alert"><Icon name="info" small /><span>发送未完成：{{ s.streamError }} 输入内容已保留，请检查后再发送。</span></div>
            <div v-if="!s.models.length" class="config-hint"><Icon name="info" small />还没有配置模型。<button type="button" @click="openStudio('models')">前往模型管理</button>，配置后即可开始对话。</div>
          </div>
          <div class="home-bottom-space" />
        </div>
        <div v-else class="page conversation-page">
          <div ref="conversation" class="conversation" id="conversation-scroll" @scroll="onConversationScroll">
            <div class="conversation-head"><span class="small-avatar">{{ agent?.glyph || 'F' }}</span><h2>{{ s.currentTask.title || '新任务' }}</h2><button class="icon-button" type="button" title="新任务" aria-label="新任务" @click="newTask"><Icon name="addCircle" small /></button></div>
            <div v-for="(message, index) in (s.currentTask.messages || [])" :key="index" class="message" :class="message.role === 'user' ? 'user' : 'assistant'">
              <div class="message-avatar">{{ message.role === 'user' ? String(s.user?.name || s.user?.email || '我').slice(0, 1) : agent?.glyph || 'F' }}</div>
              <div class="message-body">
                <div class="message-label">{{ message.role === 'user' ? s.user?.name || '我' : agent?.name }}</div>
                <div v-if="message.role === 'user'" class="message-content">{{ messageContent(message) }}</div>
                <div v-else-if="messageContent(message)" class="message-content" v-html="renderAssistant(message, index)" @click="onRenderedMessageClick($event, message)" />
                <div v-else-if="message.streaming" class="message-content"><span class="message-typing"><i /><i /><i /></span></div>
                <div v-if="message.attachments?.length" class="message-attachments" aria-label="引用文件"><span v-for="file in message.attachments" :key="file.id" class="message-attachment"><Icon name="file" small /><span>{{ file.name || '文件' }}</span></span></div>
                <div v-if="messageContent(message)" class="message-actions"><button type="button" @click="copyText(messageContent(message))"><Icon name="copy" small /> 复制消息</button></div>
              </div>
            </div>
            <div v-if="taskFailure" class="send-error" role="alert"><Icon name="info" small /><span>上次回复未完成：{{ taskFailure }} 如需再次提问，可以先检查内容；再次发送会新增一条消息。</span><button type="button" @click="restoreLastPrompt">放回输入框</button></div>
          </div>
          <div class="conversation-jump-wrap"><button v-if="s.showJump" class="conversation-jump" type="button" @click="scrollConversation">↓ 回到底部</button></div>
          <div class="chat-footer">
            <div v-if="s.streaming" class="stop-bar" role="status"><span class="run-orb" aria-hidden="true" /><span class="generation-label">{{ generationLabel }}</span><button type="button" @click="stopMessage"><Icon name="stop" small /> 停止生成</button></div>
            <Composer ref="composer" :draft="s.draft" :agent="agent" :model="model" :project="project" :attachments="attachedFiles" :streaming="s.streaming" :uploading="uploadCount" :model-unavailable="s.taskModelUnavailable" :has-models="s.models.length > 0" @update:draft="updateDraft" @send="sendMessage" @attach="showModal('attachments')" @project="showModal('projects')" @model="showModal('models')" @agent="showModal('agents')" @remove-attachment="removeAttachment" />
          </div>
        </div>
      </template>

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
              <div class="item-card-main"><div class="item-card-title">{{ item.name }}</div><div class="item-card-detail">{{ fileSize(item.size) }}{{ item.projectId ? ' · ' + (s.projects.find((candidate) => String(candidate.id) === String(item.projectId))?.name || '项目文件') : '' }}</div></div>
              <div class="item-card-actions">
                <button class="icon-button" type="button" title="预览" :aria-label="'预览' + item.name" @click="previewFile(item.id)"><Icon name="eye" small /></button>
                <button class="icon-button" type="button" title="下载" :aria-label="'下载' + item.name" @click="downloadFile(item.id)"><Icon name="download" small /></button>
                <button class="icon-button" type="button" title="删除" :aria-label="'删除' + item.name" @click="confirmDelete('file', item.id)"><Icon name="trash" small /></button>
              </div>
            </div>
          </div>
          <div v-else class="empty-state"><Icon name="folder" /><h2>这里还没有文件</h2><p>上传资料后，可以预览、下载，或在聊天里引用支持的文本文件。</p><button class="secondary-button" type="button" @click="chooseFiles('files')">上传文件</button></div>
        </div>
      </section>

      <section v-else-if="s.view === 'extensions'" class="page">
        <div class="page-header"><div><h1 class="page-title">扩展</h1><p class="page-subtitle">管理本机已连接的 Coze 能力。</p></div></div>
        <div class="page-body page-body-narrow"><div class="two-column">
          <div class="surface-card"><div class="item-card-icon"><Icon name="grid" /></div><h2>Coze Studio</h2><p>打开本机部署的 Coze Studio，管理工作流、知识库及智能体。</p><button class="secondary-button" type="button" :disabled="s.studioOpening" @click="openStudio('studio')">打开 Coze Studio <Icon name="link" small /></button></div>
          <div class="surface-card"><div class="item-card-icon"><Icon name="spark" /></div><h2>模型管理</h2><p>先在 Coze Studio 中配置可用模型。界面只显示已从本地服务读取到的模型。</p><button class="secondary-button" type="button" :disabled="s.studioOpening" @click="openStudio('models')">打开模型管理 <Icon name="link" small /></button></div>
        </div></div>
      </section>

      <section v-else-if="s.view === 'settings'" class="page">
        <div class="page-header"><div><h1 class="page-title">设置</h1><p class="page-subtitle">账号、模型与智能体配置。</p></div><div class="toolbar-actions"><button class="secondary-button" type="button" :disabled="s.manualRefreshing" @click="manualRefresh"><Icon name="history" small /> 刷新</button></div></div>
        <div class="page-body page-body-narrow">
          <div class="section-label">账号</div><div class="surface-card"><div class="setting-row"><span class="user-avatar">{{ String(s.user?.name || s.user?.email || 'F').slice(0, 1) }}</span><div class="grow"><strong>{{ s.user?.name || '本地用户' }}</strong><small>{{ s.user?.email || '本地账号' }}</small></div><button class="subtle-button danger-button" type="button" @click="logout"><Icon name="signout" small /> 退出登录</button></div></div>
          <div class="section-label">模型</div><div class="surface-card"><div class="setting-row"><div class="grow"><strong>{{ s.models.length ? s.models.length + ' 个可用模型' : '尚未配置可用模型' }}</strong><small>模型从本机 Coze 服务读取。配置后才能发起对话。</small></div><button class="secondary-button" type="button" @click="openStudio('models')">管理模型</button></div><div v-for="item in s.models" :key="item.id" class="setting-row"><div class="grow"><strong>{{ item.name }}</strong></div><span v-if="String(item.id) === String(s.selectedModelId)" class="tag">当前模型</span><button v-else class="subtle-button" type="button" @click="selectModel(item.id)">选择</button></div></div>
          <div class="section-label">智能体</div><div class="surface-card"><div class="setting-row"><div class="grow"><strong>内置文字助手与 Coze 智能体</strong><small>视频导演可协助写脚本与分镜；视频生成功能取决于你实际配置的工作流。</small></div><button class="secondary-button" type="button" @click="openStudio('studio')">配置 Coze</button></div><div v-for="item in agents" :key="item.id" class="setting-row"><span class="small-avatar">{{ item.glyph || item.name?.slice(0, 1) }}</span><div class="grow"><strong>{{ item.name }}</strong><small>{{ item.description || '' }}</small></div><span v-if="String(item.id) === String(s.selectedAgentId)" class="tag">当前智能体</span><button v-else class="subtle-button" type="button" @click="selectAgent(item.id)">选择</button></div></div>
        </div>
      </section>

      <div v-if="s.streaming && s.view !== 'home'" class="active-run-banner" role="status"><span class="run-orb" aria-hidden="true" /><span class="active-run-copy">Ficagent {{ generationLabel }}…</span><button type="button" @click="setView('home')">查看回复</button><button type="button" @click="stopMessage">停止</button></div>
      <div v-if="s.openingTaskId || s.openTaskError" class="task-open-status" :class="{ failed: Boolean(s.openTaskError) }" role="status" aria-live="polite"><span v-if="s.openingTaskId" class="task-open-spinner" aria-hidden="true" /><span class="task-open-message">{{ s.openTaskError ? '打开任务失败：' + s.openTaskError.message : '正在打开任务…' }}</span><button v-if="s.openTaskError" type="button" @click="openTask(s.openTaskError.id)">重试</button><button v-if="s.openTaskError" type="button" aria-label="关闭提示" @click="s.openTaskError = null">×</button></div>
    </main>
  </div>

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
          <div class="modal-body"><p>内置助手会创建文字对话；Coze 智能体需要先在本机 Coze Studio 配置。</p><div class="modal-list"><button v-for="item in agents" :key="item.id" class="modal-option" :class="{ selected: String(item.id) === String(s.selectedAgentId) }" type="button" @click="selectAgent(item.id)"><span class="small-avatar">{{ item.glyph || item.name?.slice(0, 1) }}</span><span class="grow">{{ item.name }}<small>{{ item.description || '' }}</small></span><Icon v-if="String(item.id) === String(s.selectedAgentId)" name="check" small /></button></div></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="openStudio('studio')">配置 Coze 智能体</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'models'">
          <div class="modal-body"><div v-if="s.models.length" class="modal-list"><button v-for="item in s.models" :key="item.id" class="modal-option" :class="{ selected: String(item.id) === String(s.selectedModelId) }" type="button" @click="selectModel(item.id)"><Icon name="spark" small /><span class="grow">{{ item.name }}</span><Icon v-if="String(item.id) === String(s.selectedModelId)" name="check" small /></button></div><p v-else>还没有可用模型。请先在本机 Coze Studio 添加模型。</p></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="openStudio('models')">打开模型管理</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'projects'">
          <div class="modal-body"><div class="modal-list"><button class="modal-option" :class="{ selected: !s.selectedProjectId }" type="button" @click="selectProject('')"><Icon name="folder" small /><span class="grow">无项目<small>作为独立任务保存</small></span></button><button v-for="item in s.projects" :key="item.id" class="modal-option" :class="{ selected: String(item.id) === String(s.selectedProjectId) }" type="button" @click="selectProject(item.id)"><Icon name="folder" small /><span class="grow">{{ item.name }}</span></button></div></div>
          <div class="modal-actions"><button class="secondary-button" type="button" @click="showModal('project')">创建项目</button></div>
        </template>
        <template v-else-if="s.modal.kind === 'attachments'">
          <div class="modal-body"><button class="secondary-button attachment-upload" type="button" @click="chooseFiles('composer')"><Icon name="upload" small /> 上传新文件</button><div class="section-label">已有文件</div><div v-if="s.files.length" class="modal-list"><button v-for="item in s.files" :key="item.id" class="modal-option" :class="{ selected: s.attachedFileIds.some((id) => String(id) === String(item.id)) }" type="button" @click="toggleAttachment(item.id)"><Icon name="file" small /><span class="grow">{{ item.name }}<small>{{ fileSize(item.size) }}</small></span><Icon v-if="s.attachedFileIds.some((id) => String(id) === String(item.id))" name="check" small /></button></div><p v-else>还没有上传文件。</p></div>
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

  <div id="toast-root" role="status" aria-live="polite"><div v-for="item in notices" :key="item.id" class="toast" :class="{ error: item.error }">{{ item.message }}</div></div>
</template>
