<script setup>
import { computed, nextTick, ref } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import Icon from './Icon.vue';
import ExtensionPicker from './ExtensionPicker.vue';
import { fileLabel, fileIdentityHint } from '../fileLabels.js';

const props = defineProps({
  variant: { type: String, default: 'compact' },
  draft: { type: String, default: '' },
  agent: { type: Object, required: true },
  model: { type: Object, default: null },
  project: { type: Object, default: null },
  attachments: { type: Array, default: () => [] },
  skillNames: { type: Array, default: () => [] },
  capabilityRefs: { type: Array, default: () => [] },
  application: { type: Object, default: null },
  streaming: Boolean,
  uploading: { type: Number, default: 0 },
  modelUnavailable: Boolean,
  hasModels: Boolean,
  searchMode: { type: String, default: 'auto' },
  searchAvailable: { type: Boolean, default: true },
  createMode: Boolean,
  creationBusy: Boolean,
  creationPending: Boolean,
});
const emit = defineEmits(['update:draft', 'update:search-mode', 'update:create-mode', 'send', 'attach', 'project', 'model', 'agent', 'remove-attachment', 'remove-skill', 'choose-extension', 'remove-capability', 'remove-application', 'manage-extension']);
const input = ref(null);
const addOpen = ref(false), pickerKind = ref('');
function pick(kind) { addOpen.value = false; pickerKind.value = kind; }
function choose(item) { emit('choose-extension', { ...item, kind: pickerKind.value }); pickerKind.value = ''; focus(); }
const canSend = computed(() => props.createMode
  ? Boolean(props.draft.trim()) && !props.creationBusy && !props.creationPending && !props.streaming && props.uploading === 0
  : Boolean(props.draft.trim() || props.application) && !props.streaming && props.uploading === 0 && props.hasModels && !props.modelUnavailable);
let composing = false;
let compositionEndedAt = 0;

function onInput(event) {
  emit('update:draft', event.target.value);
  event.target.style.height = 'auto';
  event.target.style.height = `${Math.min(event.target.scrollHeight, 260)}px`;
}

function onEnter(event) {
  if (event.shiftKey || event.isComposing || composing || event.keyCode === 229 || Date.now() - compositionEndedAt < 100) return;
  event.preventDefault();
  if (canSend.value) emit('send');
}

function focus() { nextTick(() => input.value?.focus()); }
defineExpose({ focus });
</script>

<template>
  <div class="composer-wrap" :class="{ 'composer-wrap--home': variant === 'home' }">
    <div class="composer">
      <div v-if="!createMode && (capabilityRefs.length || application)" class="composer-skills" aria-label="本轮优先能力">
        <span>本轮选择：</span><span v-for="item in capabilityRefs" :key="item.assetId" class="attach-chip">{{ item.kind === 'skill' ? 'Skill' : 'Tool' }} · {{ item.name }}<button type="button" :disabled="streaming" :aria-label="'移除 ' + item.name" @click="emit('remove-capability', item.assetId)"><Icon name="close" small /></button></span>
        <span v-if="application" class="attach-chip">Application · {{ application.spec.name }}<button type="button" :disabled="streaming" aria-label="移除应用" @click="emit('remove-application')"><Icon name="close" small /></button></span>
      </div>
      <ExtensionPicker v-if="pickerKind && !streaming && !createMode" :key="pickerKind" :kind="pickerKind" :selected="capabilityRefs" @close="pickerKind = ''; focus()" @choose="choose" @manage="pickerKind = ''; emit('manage-extension', $event)" />
      <div v-if="skillNames.length && !createMode" class="composer-skills" aria-label="本次对话技能">
        <span>{{ streaming ? '本次消息技能：' : '下一条消息使用：' }}</span>
        <span v-for="name in skillNames" :key="name" class="attach-chip">{{ name }}<button type="button" :disabled="streaming" :aria-label="'移除技能 ' + name" @click="emit('remove-skill', name)"><Icon name="close" small /></button></span>
      </div>
      <label class="sr-only" for="composer-input">输入消息</label>
      <textarea id="composer-input" ref="input" :value="draft" :placeholder="createMode ? '描述要创建的 APP、输入输出与所需能力…' : variant === 'home' ? '描述你的想法、研究问题或想构建的应用…' : `告诉${agent.name}，你想从哪件事开始…`" rows="2" :disabled="streaming || creationBusy"
        @input="onInput" @keydown.enter="onEnter" @compositionstart="composing = true"
        @compositionend="composing = false; compositionEndedAt = Date.now()" />
      <div v-if="attachments.length" class="attach-chips">
        <div v-for="file in attachments" :key="file.id" class="attach-chip">
          <span :title="fileIdentityHint(file)">{{ fileLabel(file) }}</span>
          <button type="button" :aria-label="`移除${fileLabel(file)}`" @click="emit('remove-attachment', file.id)"><Icon name="close" small /></button>
        </div>
      </div>
      <div class="composer-tools">
        <div class="composer-add" @keydown.esc.stop="addOpen = false"><button class="icon-button" type="button" :title="createMode ? '添加文件' : '添加文件或技能池'" :aria-label="createMode ? '添加文件' : '添加文件或技能池'" :aria-expanded="addOpen" :disabled="streaming || creationBusy" @click="addOpen = !addOpen"><Icon name="add" /></button>
          <div v-if="addOpen" class="composer-add-menu"><button type="button" @click="addOpen = false; emit('attach')">上传或选择文件</button><template v-if="!createMode"><button type="button" @click="pick('skill')">选择 Skill</button><button type="button" @click="pick('tool')">选择 Tool</button><button type="button" @click="pick('application')">选择 Application</button></template></div>
        </div>
        <button class="composer-pill composer-create-mode" :class="{ active: createMode }" type="button" aria-label="创建模式" :aria-pressed="createMode" :disabled="creationBusy" @click="addOpen = false; pickerKind = ''; emit('update:create-mode', !createMode)">创建模式</button>
        <button class="composer-pill composer-project-pill" type="button" title="当前项目" :disabled="streaming" @click="emit('project')"><Icon name="folder" small /><span>{{ project?.name || '无项目' }}</span><Icon name="chevron" small /></button>
        <label class="composer-search-mode" title="网页搜索模式"><Icon name="search" small /><span class="sr-only">网页搜索模式</span><select :value="searchMode" :disabled="streaming" aria-label="网页搜索模式" @change="emit('update:search-mode', $event.target.value)"><option value="auto">自动网页搜索</option><option value="on">始终网页搜索</option><option value="off">关闭网页搜索</option></select></label>
        <div class="spacer" />
        <button class="composer-pill composer-model-pill" type="button" title="选择模型" :disabled="streaming" @click="emit('model')"><Icon name="spark" small /><span>{{ modelUnavailable ? '原模型不可用' : model?.name || '选择模型' }}</span><Icon name="chevron" small /></button>
        <button class="send-button" type="button" :title="uploading ? '请等待文件上传完成' : createMode ? '创建并保存 APP' : draft.trim() ? '发送消息' : '输入内容后发送'" :aria-label="createMode ? '创建并保存 APP' : '发送消息'" :disabled="!canSend" @click="emit('send')"><Icon name="arrowUp" /></button>
      </div>
    </div>
    <div class="composer-meta">
      <button type="button" :disabled="streaming" @click="emit('agent')"><span class="small-avatar">{{ agent.glyph || agent.name?.slice(0, 1) }}</span>{{ agent.name }} <Icon name="chevron" small /></button>
      <span class="meta-note">{{ project ? `保存到「${project.name}」` : '本地工作台' }}</span>
    </div>
    <div class="composer-shortcut">Enter / ⌘Enter 发送 · Shift+Enter 换行</div>
    <div v-if="createMode" class="composer-create-note" role="status">创建模式：请在需求中说明要创建的 APP 及所需能力。生成后保存到我的 APP，初始为未验证。</div>
    <div v-if="uploading" class="model-warning" role="status">正在上传 {{ uploading }} 个文件，上传完成后即可连同附件发送。</div>
    <div v-if="modelUnavailable" class="model-warning" role="alert">{{ USER_FACING_ERROR }} <button type="button" @click="emit('model')">选择模型</button></div>
    <div v-if="!searchAvailable && searchMode === 'on'" class="model-warning" role="status">当前网页搜索不可用，请切换为自动网页搜索或关闭网页搜索。</div>
  </div>
</template>

<style scoped>
.composer-create-mode{white-space:nowrap}.composer-create-mode.active{background:var(--aurora-brand-soft,#f0e9fa);color:var(--aurora-brand-hover,#6341a0);box-shadow:inset 0 0 0 2px var(--aurora-brand,#7652b7)}.composer-create-mode:focus-visible{outline:2px solid var(--aurora-focus,#865bc7);outline-offset:2px}.composer-create-note{padding:8px 16px 0;color:var(--muted,#606579);font-size:12px;line-height:1.6}
.composer-add{position:relative}.composer-add-menu{position:absolute;bottom:100%;left:0;z-index:20;min-width:200px;padding:8px;border:1px solid var(--line);border-radius:12px;background:var(--surface,#fff);box-shadow:0 12px 36px #0002}.composer-add-menu button{display:block;width:100%;padding:12px;text-align:left;border:0;border-radius:8px;background:transparent;color:var(--text);cursor:pointer}.composer-add-menu button:hover,.composer-add-menu button:focus-visible{background:var(--surface-2)}
.composer-wrap.composer-wrap--home { width: 100%; }
.composer-wrap--home .composer {
  --composer-glass-rim: inset 0 2px 0 #fff,
    inset 0 5px 9px rgba(255, 255, 255, .42),
    inset 2px 0 1px rgba(255, 255, 255, .88),
    inset 0 -3px 1px rgba(151, 126, 181, .24),
    inset -2px 0 1px rgba(151, 126, 181, .13),
    inset 0 0 18px rgba(255, 255, 255, .16);
  min-height: 180px;
  border-radius: var(--radius-panel, 24px);
  border-color: rgba(255, 255, 255, .94);
  background: #fffaff;
  box-shadow: var(--composer-glass-rim),
    0 0 0 1px rgba(151, 126, 181, .28),
    0 2px 0 rgba(255, 255, 255, .56),
    0 8px 18px rgba(83, 55, 117, .07),
    0 24px 48px rgba(83, 55, 117, .13);
  transition: border-color .18s ease, box-shadow .18s ease;
}
.composer-wrap--home .composer:focus-within {
  border-color: var(--aurora-focus, #865bc7);
  box-shadow: 0 0 0 3px rgba(134, 91, 199, .16),
    var(--composer-glass-rim),
    0 2px 0 rgba(255, 255, 255, .56),
    0 8px 18px rgba(83, 55, 117, .07),
    0 24px 48px rgba(83, 55, 117, .14);
}
.composer-wrap--home .composer textarea {
  min-height: 112px;
  padding: 22px 24px 10px;
  font-size: 16px;
  line-height: 1.65;
  color: var(--aurora-text, #172033);
  caret-color: var(--aurora-brand, #7652b7);
}
.composer-wrap--home .composer textarea::placeholder {
  color: var(--aurora-text-secondary, #606579);
  opacity: 1;
}
.composer-wrap--home .composer-tools { min-height: 60px; padding: 6px 16px 13px; gap: 8px; }
.composer-wrap--home .composer .icon-button,
.composer-wrap--home .composer-pill,
.composer-wrap--home .composer-search-mode {
  color: var(--aurora-text-secondary, #606579);
}
.composer-wrap--home .composer-pill,
.composer-wrap--home .composer-search-mode {
  background: rgba(248, 242, 252, .64);
  box-shadow: inset 0 0 0 1px rgba(161, 139, 191, .16);
}
.composer-wrap--home .composer-create-mode.active {
  background: var(--aurora-brand-soft, #f0e9fa);
  color: var(--aurora-brand-hover, #6341a0);
  box-shadow: inset 0 0 0 2px var(--aurora-brand, #7652b7);
}
.composer-wrap--home .composer .icon-button:hover:not(:disabled),
.composer-wrap--home .composer-pill:hover:not(:disabled),
.composer-wrap--home .composer-search-mode:hover {
  background: var(--aurora-brand-soft, #f0e9fa);
  color: var(--aurora-brand-hover, #6341a0);
}
.composer-wrap--home .composer .icon-button:focus-visible,
.composer-wrap--home .composer-pill:focus-visible,
.composer-wrap--home .send-button:focus-visible {
  outline: 2px solid var(--aurora-focus, #865bc7);
  outline-offset: 2px;
}
.composer-wrap--home .composer-search-mode:focus-within {
  box-shadow: 0 0 0 2px rgba(134, 91, 199, .24);
}
.composer-wrap--home .composer-search-mode select {
  color: inherit;
}
.composer-wrap--home .composer-skills {
  color: var(--aurora-text-secondary, #606579);
}
.composer-wrap--home .attach-chip {
  border-color: var(--aurora-border, rgba(161, 139, 191, .2));
  background: rgba(248, 242, 252, .72);
  color: var(--aurora-text, #172033);
}
.composer-wrap--home .attach-chip button {
  color: var(--aurora-text-secondary, #606579);
}
.composer-wrap--home .send-button {
  width: 42px;
  height: 42px;
  flex: none;
  background: var(--aurora-brand, #7652b7);
  color: #fff;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .24),
    0 5px 13px rgba(97, 59, 151, .2);
}
.composer-wrap--home .send-button:hover:not(:disabled) {
  background: var(--aurora-brand-hover, #6341a0);
}
.composer-wrap--home .send-button:disabled {
  background: #e6dcf0;
  color: #70538f;
  box-shadow: inset 0 0 0 1px rgba(112, 83, 143, .12);
  opacity: 1;
  cursor: not-allowed;
}
.composer-wrap--home .composer-meta {
  --aurora-text-secondary: #535a70;
  --muted: #535a70;
  background: linear-gradient(90deg, #fffaff 0%, #f5effb 100%);
  color: var(--aurora-text-secondary, #606579);
  box-shadow: inset 0 0 0 1px rgba(161, 139, 191, .14);
}
.composer-wrap--home .composer-meta button:hover {
  color: var(--aurora-brand-hover, #6341a0);
}
.composer-wrap--home .composer-meta .small-avatar {
  background: var(--aurora-brand-soft, #f0e9fa);
  color: var(--aurora-brand, #7652b7);
}
.composer-wrap--home .composer-meta .meta-note,
.composer-wrap--home .composer-shortcut {
  color: var(--aurora-text-secondary, #606579);
}
@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .composer-wrap--home .composer {
    background: linear-gradient(112deg, rgba(255, 255, 255, .46), rgba(255, 255, 255, .08) 16%, transparent 32% 68%, rgba(255, 255, 255, .22) 80%, transparent 94%),
      radial-gradient(ellipse 90% 115% at 100% 100%, rgba(199, 165, 245, .2), transparent 70%),
      radial-gradient(ellipse 50% 150% at 0% 100%, rgba(245, 187, 221, .12), transparent 80%),
      linear-gradient(145deg, rgba(255, 255, 255, .38), rgba(255, 255, 255, .22));
    -webkit-backdrop-filter: blur(20px) saturate(120%);
    backdrop-filter: blur(20px) saturate(120%);
  }
  .composer-wrap--home .composer-meta {
    background: linear-gradient(90deg, rgba(255, 250, 255, .56), rgba(245, 239, 251, .32));
  }
}
@media (prefers-reduced-motion: reduce) {
  .composer-wrap--home .composer { transition: none; }
}
@media (max-width: 767px) {
  .composer-wrap.composer-wrap--home { width: 100%; }
  .composer-wrap--home .composer { min-height: 164px; }
  .composer-wrap--home .composer textarea { min-height: 90px; padding: 17px 17px 8px; }
  .composer-wrap--home .composer-tools { padding: 6px 11px 12px; }
}
</style>
