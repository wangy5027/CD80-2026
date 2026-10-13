<script setup>
import { nextTick, ref } from 'vue';
import Icon from './Icon.vue';
import { fileLabel, fileIdentityHint } from '../fileLabels.js';

const props = defineProps({
  draft: { type: String, default: '' },
  agent: { type: Object, required: true },
  model: { type: Object, default: null },
  project: { type: Object, default: null },
  attachments: { type: Array, default: () => [] },
  streaming: Boolean,
  uploading: { type: Number, default: 0 },
  modelUnavailable: Boolean,
  hasModels: Boolean,
});
const emit = defineEmits(['update:draft', 'send', 'attach', 'project', 'model', 'agent', 'remove-attachment']);
const input = ref(null);
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
  emit('send');
}

function focus() { nextTick(() => input.value?.focus()); }
defineExpose({ focus });
</script>

<template>
  <div class="composer-wrap">
    <div class="composer">
      <label class="sr-only" for="composer-input">输入消息</label>
      <textarea id="composer-input" ref="input" :value="draft" :placeholder="`告诉${agent.name}，你想从哪件事开始…`" rows="2" :disabled="streaming"
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
        <button class="composer-pill" type="button" title="当前项目" :disabled="streaming" @click="emit('project')"><Icon name="folder" small /><span>{{ project?.name || '无项目' }}</span><Icon name="chevron" small /></button>
        <div class="spacer" />
        <button class="composer-pill" type="button" title="选择模型" :disabled="streaming" @click="emit('model')"><Icon name="spark" small /><span>{{ modelUnavailable ? '原模型不可用' : model?.name || '选择模型' }}</span><Icon name="chevron" small /></button>
        <button class="send-button" type="button" :title="uploading ? '请等待文件上传完成' : '发送消息'" aria-label="发送消息" :disabled="streaming || uploading > 0 || !hasModels || modelUnavailable" @click="emit('send')"><Icon name="arrowUp" /></button>
      </div>
    </div>
    <div class="composer-meta">
      <button type="button" :disabled="streaming" @click="emit('agent')"><span class="small-avatar">{{ agent.glyph || agent.name?.slice(0, 1) }}</span>{{ agent.name }} <Icon name="chevron" small /></button>
      <span class="meta-note">{{ project ? `保存到「${project.name}」` : '本地工作台' }}</span>
    </div>
    <div class="composer-shortcut">Enter / ⌘Enter 发送 · Shift+Enter 换行</div>
    <div v-if="uploading" class="model-warning" role="status">正在上传 {{ uploading }} 个文件，上传完成后即可连同附件发送。</div>
    <div v-if="modelUnavailable" class="model-warning" role="alert">此任务原模型不可用，请先<button type="button" @click="emit('model')">选择模型</button>，再继续发送。</div>
  </div>
</template>
