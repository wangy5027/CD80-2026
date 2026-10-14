<script setup>
import { reactive, ref, watch, onUnmounted } from 'vue';
import { fileFields, initialInputs, parseInputs, parameterLabels } from '../agentForms.js';
import { readDraft, writeDraft, subscribeDraft, uploadToDraft } from '../draftStore.js';
import { fileLabel, fileIdentityHint } from '../fileLabels.js';
const props = defineProps({ spec: Object, draftKey: String, contextDraftKey: String, showContext: Boolean, initial: { type: Object, default: () => ({}) }, files: Array, fresh: Boolean, busy: Boolean, submitLabel: { default: '确认参数' } });
const emit = defineEmits(['submit', 'upload']);
const values = reactive({}); const error = ref('');
const goal = ref(''); let unsubscribeGoal; let restoringGoal = false;
watch(() => props.contextDraftKey, key => {
  unsubscribeGoal?.(); restoringGoal = true; goal.value = readDraft(key) || ''; restoringGoal = false;
  unsubscribeGoal = subscribeDraft(key, value => { restoringGoal = true; goal.value = value || ''; restoringGoal = false; });
}, { immediate: true });
watch(goal, value => { if (!restoringGoal && props.contextDraftKey) writeDraft(props.contextDraftKey, value); }, { flush: 'sync' });
let restoring = false; let unsubscribe;
function restore(saved) {
  restoring = true;
  for (const key of Object.keys(values)) delete values[key];
  Object.assign(values, initialInputs(props.spec, props.initial, props.fresh), saved || {});
  restoring = false; error.value = '';
}
watch(() => [props.spec?.id, props.spec?.version, props.fresh, props.draftKey], () => {
  unsubscribe?.(); restore(readDraft(props.draftKey));
  unsubscribe = subscribeDraft(props.draftKey, saved => { if (!restoring) restore(saved); });
}, { immediate: true });
watch(values, () => {
  if (restoring) return;
  restoring = true; writeDraft(props.draftKey, values); restoring = false;
}, { deep: true, flush: 'sync' });
onUnmounted(() => { unsubscribe?.(); unsubscribeGoal?.(); });
function upload(field) {
  const key = props.draftKey;
  writeDraft(key, values);
  emit('upload', { field, onUploaded: uploadToDraft(key, field, { ...values }) });
}
function submit() { try { const parsed = parseInputs(props.spec, values); error.value = ''; emit('submit', { ...parsed, ...(props.showContext && goal.value.trim() ? { goal: goal.value.trim() } : {}) }); } catch (e) { error.value = e.message; } }
</script>
<template>
  <form class="agent-input-form" @submit.prevent="submit">
    <label v-if="showContext" class="form-field" for="agent-requirement"><span>这次想解决的问题</span><textarea id="agent-requirement" v-model="goal" rows="4" :disabled="busy" /><small class="agent-muted">已经带上你的需求。可以先修改问题和下面的参数，再开始运行。</small></label>
    <p v-if="fresh" class="agent-muted">请为本次运行选择输入文件，默认不会使用上次文件。</p>
    <label v-for="(field, name) in (spec?.inputSchema?.properties || {})" :key="name" class="form-field">
      <span>{{ field.title || parameterLabels[name] || name }}<b v-if="spec.inputSchema.required?.includes(name)"> *</b></span>
      <template v-if="fileFields(spec).includes(name)"><select v-model="values[name]" :disabled="busy"><option value="">请选择文件</option><option v-for="file in files" :key="file.id" :value="file.id" :title="fileIdentityHint(file)">{{ fileLabel(file) }}</option></select><button type="button" class="secondary-button" :disabled="busy" @click="upload(name)">{{ fileFields(spec).length === 1 ? '上传新文件' : '上传' + (field.title || parameterLabels[name] || name) }}</button></template>
      <select v-else-if="field.enum" v-model="values[name]" :disabled="busy"><option value="">请选择</option><option v-for="item in field.enum" :key="String(item)" :value="item">{{ item }}</option></select>
      <input v-else-if="field.type === 'boolean'" v-model="values[name]" type="checkbox" :disabled="busy" />
      <textarea v-else-if="['object','array'].includes(field.type)" v-model="values[name]" rows="4" placeholder="复杂参数，请填写 JSON" :disabled="busy" />
      <input v-else v-model="values[name]" :type="['number','integer'].includes(field.type) ? 'number' : 'text'" :step="field.type === 'integer' ? '1' : 'any'" :readonly="'const' in field" :disabled="busy" :placeholder="field.description || name" />
      <small v-if="field.description" class="agent-muted">{{ field.description }}</small>
    </label>
    <p v-if="error" class="agent-error" role="alert">{{ error }}</p>
    <div class="agent-actions"><button class="primary-button" :disabled="busy">{{ busy ? '提交中…' : submitLabel }}</button></div>
  </form>
</template>
