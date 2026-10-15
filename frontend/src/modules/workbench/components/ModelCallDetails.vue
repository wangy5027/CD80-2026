<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { createModelCallTraceLoader, modelCallTracePath } from '../subAgentView.js';
import { presentModelCall } from '../modelCallPresentation.js';

const props = defineProps({ runId: { type: String, required: true }, call: { type: Object, required: true } });
const opened = ref(false), record = ref({ status: 'idle', text: '', error: '' });
const tracePath = computed(() => modelCallTracePath(props.runId, props.call));
const loader = createModelCallTraceLoader(undefined, value => { record.value = value; });
const diagnosticSummary = computed(() => {
  const fields = ['errorCode', 'retryOf', 'validationErrors', 'finalDecision'];
  return Object.fromEntries(fields.filter(key => props.call[key] != null).map(key => [key, props.call[key]]));
});
const unavailable = computed(() => !props.call.traceUrl ? '历史记录未保存请求详情。'
  : props.call.traceAvailable === false ? props.call.status === 'running' ? '请求仍在处理，详情尚未保存。' : '本次请求详情不可用。'
    : '请求记录地址无效，请刷新后重试。');
const presentation = computed(() => presentModelCall(record.value.text, props.call));
function load() { return loader.load(tracePath.value); }
function toggle(event) {
  opened.value = event.target.open;
  if (opened.value) load();
  else if (record.value.status === 'loading') loader.reset();
}
watch([tracePath, () => props.runId, () => props.call.id, () => props.call.status, () => props.call.completedAt,
  () => JSON.stringify(diagnosticSummary.value)], () => {
  loader.reset();
  if (opened.value) load();
});
onUnmounted(() => loader.dispose());
</script>

<template>
  <details class="model-call-details" @toggle="toggle">
    <summary>模型请求记录</summary>
    <div v-if="opened" class="model-call-record">
      <p class="model-call-note">这里只显示请求摘要、业务输入和输出节选，不展示系统或开发者提示词。</p>
      <dl class="model-call-summary" aria-label="本次模型请求摘要"><div v-for="item in presentation.metadata" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
      <div v-if="presentation.errors.length && record.status !== 'error'" class="model-call-diagnostics" aria-label="本次模型请求诊断摘要"><p>{{ USER_FACING_ERROR }}</p></div>
      <template v-if="tracePath">
        <p v-if="record.status === 'loading'" role="status">正在读取请求记录…</p>
        <div v-else-if="record.status === 'error'" class="model-call-error"><p role="alert">{{ USER_FACING_ERROR }}</p><button type="button" @click="load">重试读取</button></div>
        <template v-else-if="record.status === 'loaded'">
          <p v-if="presentation.omitted" class="model-call-note">原记录已截断，无法安全区分业务内容的片段已省略。</p>
          <section v-if="presentation.input" class="model-call-section"><h4>本次业务输入</h4><pre aria-label="本次业务输入" tabindex="0">{{ presentation.input }}</pre></section>
          <section v-if="presentation.output && !['failed','error'].includes(call.status)" class="model-call-section"><h4>输出节选<span v-if="presentation.partial"> · 尚未完整返回</span></h4><pre aria-label="模型输出节选" tabindex="0">{{ presentation.output }}</pre></section>
          <p v-else-if="!presentation.omitted" class="model-call-note">本次请求尚无可展示的输出。</p>
        </template>
      </template>
      <p v-else class="model-call-note">{{ unavailable }}</p>
    </div>
  </details>
</template>

<style scoped>
.model-call-details{margin-top:8px;font-size:12px}.model-call-details>summary{cursor:pointer;color:var(--muted,#657b89)}.model-call-record{margin-top:9px;padding:12px;border:1px solid var(--line,#dce4e8);border-radius:8px;background:var(--panel,#f5f8f9);min-width:0}.model-call-record p{margin:0 0 9px;font-size:12px;line-height:1.7}.model-call-note{color:var(--muted,#657b89)}.model-call-record pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:280px;overflow:auto;margin:6px 0 0;padding:10px;background:var(--surface,#fff);border-radius:5px;font-size:11px;line-height:1.7;color:var(--text,#17374a)}.model-call-summary{display:flex;flex-wrap:wrap;gap:8px 20px;margin:0 0 12px}.model-call-summary>div{display:flex;gap:6px;min-width:0;overflow-wrap:anywhere}.model-call-summary dt{color:var(--muted,#657b89)}.model-call-summary dd{margin:0}.model-call-section{margin-top:12px;min-width:0}.model-call-section h4{font-size:12px;margin:0;color:var(--text,#17374a)}.model-call-section h4 span{font-weight:400;color:var(--muted,#657b89)}.model-call-diagnostics{overflow-wrap:anywhere;white-space:pre-wrap}.model-call-error{color:#a05e43}.model-call-error button{border:1px solid var(--line,#dce4e8);border-radius:6px;background:transparent;color:inherit;padding:5px 10px;font:inherit;cursor:pointer}.model-call-details summary:focus-visible,.model-call-details button:focus-visible,.model-call-record pre:focus-visible{outline:2px solid #4d809c;outline-offset:3px}@media(max-width:600px){.model-call-record{padding:10px}.model-call-record pre{padding:8px}}
</style>
