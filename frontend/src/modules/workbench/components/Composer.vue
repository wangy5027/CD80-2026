<script setup>
import { computed, nextTick, ref } from 'vue';
import Icon from './Icon.vue';
import { fileLabel, fileIdentityHint } from '../fileLabels.js';

const props = defineProps({
  variant: { type: String, default: 'compact' },
  draft: { type: String, default: '' },
  agent: { type: Object, required: true },
  model: { type: Object, default: null },
  project: { type: Object, default: null },
  attachments: { type: Array, default: () => [] },
  skillNames: { type: Array, default: () => [] },
  streaming: Boolean,
  uploading: { type: Number, default: 0 },
  modelUnavailable: Boolean,
  hasModels: Boolean,
  searchMode: { type: String, default: 'auto' },
  searchAvailable: { type: Boolean, default: true },
});
const emit = defineEmits(['update:draft', 'update:search-mode', 'send', 'attach', 'project', 'model', 'agent', 'remove-attachment', 'remove-skill']);
const input = ref(null);
const canSend = computed(() => Boolean(props.draft.trim()) && !props.streaming && props.uploading === 0 && props.hasModels && !props.modelUnavailable);
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
      <div v-if="skillNames.length" class="composer-skills" aria-label="本次对话技能">
        <span>{{ streaming ? '本次消息技能：' : '下一条消息使用：' }}</span>
        <span v-for="name in skillNames" :key="name" class="attach-chip">{{ name }}<button type="button" :disabled="streaming" :aria-label="'移除技能 ' + name" @click="emit('remove-skill', name)"><Icon name="close" small /></button></span>
      </div>
      <label class="sr-only" for="composer-input">输入消息</label>
      <textarea id="composer-input" ref="input" :value="draft" :placeholder="variant === 'home' ? '描述你的想法、研究问题或想构建的应用…' : `告诉${agent.name}，你想从哪件事开始…`" rows="2" :disabled="streaming"
        @input="onInput" @keydown.enter="onEnter" @compositionstart="composing = true"
        @compositionend="composing = false; compositionEndedAt = Date.now()" />
      <div v-if="attachments.length" class="attach-chips">
        <div v-for="file in attachments" :key="file.id" class="attach-chip">
          <span :title="fileIdentityHint(file)">{{ fileLabel(file) }}</span>
          <button type="button" :aria-label="`移除${fileLabel(file)}`" @click="emit('remove-attachment', file.id)"><Icon name="close" small /></button>
        </div>
      </div>
      <div class="composer-tools">
        <button class="icon-button" type="button" title="添加附件" aria-label="添加附件" :disabled="streaming" @click="emit('attach')"><Icon name="add" /></button>
        <button class="composer-pill composer-project-pill" type="button" title="当前项目" :disabled="streaming" @click="emit('project')"><Icon name="folder" small /><span>{{ project?.name || '无项目' }}</span><Icon name="chevron" small /></button>
        <label class="composer-search-mode" title="网页搜索模式"><Icon name="search" small /><span class="sr-only">网页搜索模式</span><select :value="searchMode" :disabled="streaming" aria-label="网页搜索模式" @change="emit('update:search-mode', $event.target.value)"><option value="auto">自动网页搜索</option><option value="on">始终网页搜索</option><option value="off">关闭网页搜索</option></select></label>
        <div class="spacer" />
        <button class="composer-pill composer-model-pill" type="button" title="选择模型" :disabled="streaming" @click="emit('model')"><Icon name="spark" small /><span>{{ modelUnavailable ? '原模型不可用' : model?.name || '选择模型' }}</span><Icon name="chevron" small /></button>
        <button class="send-button" type="button" :title="uploading ? '请等待文件上传完成' : draft.trim() ? '发送消息' : '输入内容后发送'" aria-label="发送消息" :disabled="!canSend" @click="emit('send')"><Icon name="arrowUp" /></button>
      </div>
    </div>
    <div class="composer-meta">
      <button type="button" :disabled="streaming" @click="emit('agent')"><span class="small-avatar">{{ agent.glyph || agent.name?.slice(0, 1) }}</span>{{ agent.name }} <Icon name="chevron" small /></button>
      <span class="meta-note">{{ project ? `保存到「${project.name}」` : '本地工作台' }}</span>
    </div>
    <div class="composer-shortcut">Enter / ⌘Enter 发送 · Shift+Enter 换行</div>
    <div v-if="uploading" class="model-warning" role="status">正在上传 {{ uploading }} 个文件，上传完成后即可连同附件发送。</div>
    <div v-if="modelUnavailable" class="model-warning" role="alert">此任务原模型不可用，请先<button type="button" @click="emit('model')">选择模型</button>，再继续发送。</div>
    <div v-if="!searchAvailable && searchMode === 'on'" class="model-warning" role="status">当前网页搜索不可用，请切换为自动网页搜索或关闭网页搜索。</div>
  </div>
</template>

<style scoped>
.composer-wrap.composer-wrap--home { width: 100%; }
.composer-wrap--home .composer { min-height: 180px; border-radius: var(--radius-panel, 24px); }
.composer-wrap--home .composer textarea { min-height: 112px; padding: 22px 24px 10px; font-size: 16px; line-height: 1.65; }
.composer-wrap--home .composer-tools { min-height: 60px; padding: 6px 16px 13px; gap: 8px; }
.composer-wrap--home .send-button { width: 42px; height: 42px; flex: none; }
@media (max-width: 767px) {
  .composer-wrap.composer-wrap--home { width: 100%; }
  .composer-wrap--home .composer { min-height: 164px; }
  .composer-wrap--home .composer textarea { min-height: 90px; padding: 17px 17px 8px; }
  .composer-wrap--home .composer-tools { padding: 6px 11px 12px; }
}
</style>
