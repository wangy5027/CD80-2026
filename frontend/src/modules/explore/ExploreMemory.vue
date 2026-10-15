<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { downloadResearchMemory, getResearchMemory } from './api.js';
import { userFacingError } from '../../shared/userFacingError.js';

const props = defineProps({ sessionId: String, run: Object });
const opened = ref(false);
const versions = ref([]);
const selectedId = ref('');
const error = ref('');
const busy = ref(false);
let controller;
const latest = computed(() => props.run?.memory_output || props.run?.memory_input || null);
const selected = computed(() => versions.value.find(item => item.id === selectedId.value) || latest.value);
async function load() {
  controller?.abort();
  if (!opened.value || !props.sessionId || !props.run?.id) return;
  const current = new AbortController();
  controller = current;
  busy.value = true;
  error.value = '';
  try {
    const result = await getResearchMemory(props.sessionId, props.run.id, current.signal);
    if (controller !== current) return;
    versions.value = result.memories || [];
    selectedId.value = result.latest?.id || '';
  } catch (cause) {
    if (cause.name !== 'AbortError' && controller === current) error.value = cause.message;
  } finally { if (controller === current) busy.value = false; }
}
watch(() => [props.sessionId, props.run?.id, latest.value?.id], () => {
  versions.value = [];
  selectedId.value = '';
  load();
});
function toggle(event) { opened.value = event.target.open; if (opened.value) load(); }
async function download() {
  error.value = '';
  try { await downloadResearchMemory(props.sessionId, selected.value); }
  catch (cause) { error.value = cause.message; }
}
onUnmounted(() => controller?.abort());
</script>

<template>
  <details class="explore-report-section explore-chain-memory" data-testid="explore-memory" @toggle="toggle">
    <summary>研究记忆<span v-if="latest"> · v{{ latest.version }}</span></summary>
    <p v-if="!latest">完成本轮回答后生成记忆，后续研究自动接续。</p>
    <template v-else>
      <p>本轮{{ run.memory_input ? `接续 v${run.memory_input.version}` : '从新问题开始' }}<span v-if="run.memory_output">，回答后已更新至 v{{ run.memory_output.version }}</span>。切换历史记录可查看当时的记忆。</p>
      <div class="explore-memory-actions">
        <label>记忆版本 <select v-model="selectedId" aria-label="记忆版本" :disabled="busy"><option v-if="!versions.length" :value="latest.id">v{{ latest.version }} · 当前版本</option><option v-for="item in versions" :key="item.id" :value="item.id">v{{ item.version }}{{ item.id === latest.id ? ' · 当前版本' : '' }}</option></select></label>
        <button class="secondary-button" type="button" :disabled="!selected || busy" @click="download">下载记忆</button>
      </div>
      <p v-if="busy" role="status">正在读取记忆版本…</p>
      <pre v-if="selected" class="explore-memory-content" tabindex="0">{{ selected.content }}</pre>
    </template>
    <p v-if="error" role="alert">{{ userFacingError(error) }}</p>
  </details>
</template>

<style scoped>
.explore-memory-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; margin: 12px 0; }
.explore-memory-actions label { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.explore-memory-actions select { max-width: 100%; font: inherit; color: inherit; background: transparent; border: 1px solid currentColor; border-radius: 8px; padding: 6px 10px; }
.explore-memory-content { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.7; }
</style>
