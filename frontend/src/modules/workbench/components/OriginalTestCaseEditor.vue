<script setup>
import { ref, watch } from 'vue';

const props = defineProps({ kind: { type: String, required: true }, initialCase: { type: Object, default: null }, disabled: Boolean });
const row = ref({ label: '', inputText: '{}', expectationText: '', prompt: '', criteria: '' });
let initialSignature = '';
function reset() {
  const source = props.initialCase || {};
  row.value = {
    label: source.label || '原任务用例',
    inputText: JSON.stringify(source.input || {}, null, 2),
    expectationText: source.expectation && props.kind !== 'skill' ? JSON.stringify(source.expectation, null, 2) : '',
    prompt: source.expectation?.prompt || '',
    criteria: source.expectation?.criteria || '',
  };
  initialSignature = JSON.stringify(row.value);
}
watch([() => props.initialCase, () => props.kind], reset, { immediate: true });
function snapshot() {
  if (JSON.stringify(row.value) === initialSignature) return null;
  const input = JSON.parse(row.value.inputText);
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('原任务用例的输入必须是 JSON 对象。');
  let expectation = null;
  if (props.kind === 'skill') expectation = { mode: 'manual_review', prompt: row.value.prompt.trim(), criteria: row.value.criteria.trim() };
  else if (row.value.expectationText.trim()) {
    expectation = JSON.parse(row.value.expectationText);
    if (!expectation || typeof expectation !== 'object' || Array.isArray(expectation)) throw new Error('独立预期必须是 JSON 对象。');
    if (props.kind === 'app' && !['json_equals', 'fields'].includes(expectation.mode)) throw new Error('APP 独立预期需使用 json_equals 或 fields。');
    if (props.kind === 'tool' && !['json_equals', 'assertions'].includes(expectation.mode)) throw new Error('Tool 独立预期需使用 json_equals 或 assertions。');
  }
  return {
    label: row.value.label.trim() || '原任务用例', input,
    ...(props.kind === 'app' ? { fileBindings: { ...(props.initialCase?.fileBindings || {}) } } : { fileIds: [...(props.initialCase?.fileIds || [])] }),
    expectation,
  };
}
defineExpose({ snapshot });
</script>

<template>
  <section class="original-case-editor" aria-label="保存原任务用例">
    <h4>保存原任务用例</h4>
    <p>这里只记录原任务的输入和独立预期，不会运行。预期尚不明确时可以留空，保存后须补全才能验证。</p>
    <fieldset :disabled="disabled"><label>用例名称<input v-model="row.label" aria-label="保存用例名称" /></label>
    <label>原任务输入（JSON 对象）<textarea v-model="row.inputText" rows="5" spellcheck="false" aria-label="保存原任务输入" /></label>
    <template v-if="kind === 'skill'">
      <label>原任务要求<textarea v-model="row.prompt" rows="3" aria-label="保存原任务要求" /></label>
      <label>独立验收标准<textarea v-model="row.criteria" rows="3" aria-label="保存独立验收标准" placeholder="未确定时可留空，保存后补全" /></label>
    </template>
    <label v-else>独立预期（JSON，可留空）
      <textarea v-model="row.expectationText" rows="5" spellcheck="false" aria-label="保存独立预期" placeholder='{"mode":"json_equals","value":{}}' />
    </label></fieldset>
  </section>
</template>

<style scoped>
.original-case-editor{border:1px solid #dce8ea;border-radius:12px;padding:16px;margin:18px 0;background:#f8fcfb}.original-case-editor h4{font-size:15px;margin:0 0 8px}.original-case-editor p{font-size:12px;line-height:1.6;color:#607681;margin:0 0 12px}.original-case-editor fieldset{border:0;padding:0;margin:0;min-width:0}.original-case-editor label{display:block;margin:12px 0;font-size:13px;font-weight:650;color:#29414d}.original-case-editor input,.original-case-editor textarea{box-sizing:border-box;display:block;width:100%;margin-top:7px;padding:10px;border:1px solid #cbdcdf;border-radius:8px;color:#1b303a;background:white;font:inherit;font-weight:400}.original-case-editor textarea{font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;resize:vertical}
</style>
