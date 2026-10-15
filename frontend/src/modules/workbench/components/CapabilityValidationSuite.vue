<script setup>
import { computed, ref } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import CapabilityField from './CapabilityField.vue';
import CapabilityResult from './CapabilityResult.vue';
import { emptyValue, fieldErrors, formSupported } from '../capabilityForms.js';
const props = defineProps({ kind: String, inputSchema: Object, outputSchema: Object, files: { type: Array, default: () => [] }, inputFiles: Boolean, busy: Boolean, allowed: Boolean, verification: Object, testCases: { type: Array, default: () => [] } });
const emit = defineEmits(['verify', 'review']);
const copy = value => JSON.parse(JSON.stringify(value));
function caseRow(source, index) {
  const expectation = source?.expectation || {};
  return { label: source?.label || (index ? '适用的新输入' : '原用例'),
    input: copy(source?.input || emptyValue(props.inputSchema)), inputText: JSON.stringify(source?.input || {}, null, 2),
    expected: expectation.mode === 'json_equals' && Object.hasOwn(expectation, 'value') ? JSON.stringify(expectation.value, null, 2) : '',
    expectationMode: expectation.mode === 'assertions' ? 'assertions' : 'json_equals',
    assertions: JSON.stringify(expectation.assertions || [], null, 2),
    prompt: expectation.prompt || '', criteria: expectation.criteria || '', fileIds: [...(source?.fileIds || [])] };
}
const rows = ref([caseRow(props.testCases[0], 0), caseRow(props.testCases[1], 1)]);
const includeAdditionalCase = ref(false);
const originalCase = computed(() => props.testCases[0] || null);
const originalNeedsCompletion = computed(() => {
  const expectation = originalCase.value?.expectation;
  if (!expectation) return true;
  if (props.kind === 'skill') return !expectation.prompt?.trim() || !expectation.criteria?.trim();
  if (expectation.mode === 'assertions') return !expectation.assertions?.length;
  return expectation.mode !== 'json_equals' || !Object.hasOwn(expectation, 'value');
});
function restoreOriginalCase() { if (originalCase.value) rows.value[0] = caseRow(originalCase.value, 0); }
function copyOriginalToAdditional() { if (originalCase.value) rows.value[1] = { ...caseRow(originalCase.value, 1), label: '适用的新输入' }; }
const error = ref('');
function submit() {
  try {
    const cases = (includeAdditionalCase.value ? rows.value : rows.value.slice(0, 1)).map(row => {
      const input = props.kind === 'tool' && formSupported(props.inputSchema) ? row.input : JSON.parse(row.inputText);
      if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error(row.label + '：实际输入需要是 JSON 对象。');
      if (props.kind === 'tool') { const issues = fieldErrors(props.inputSchema, input); if (issues.length) throw new Error(row.label + '：' + issues.map(item => item.message).join(' ')); }
      if (props.kind === 'skill' && (!row.prompt.trim() || !row.criteria.trim())) throw new Error('请填写当前 Skill 用例的任务和验收标准。');
      let expectation;
      if (props.kind === 'skill') expectation = { mode: 'manual_review', prompt: row.prompt, criteria: row.criteria };
      else if (row.expectationMode === 'assertions') {
        const assertions = JSON.parse(row.assertions);
        if (!Array.isArray(assertions) || !assertions.length) throw new Error(row.label + '：至少填写一条字段或业务断言。');
        expectation = { mode: 'assertions', assertions };
      } else expectation = { mode: 'json_equals', value: JSON.parse(row.expected) };
      return { label: row.label, input, fileIds: [...row.fileIds], expectation };
    });
    if (cases.length > 1 && JSON.stringify([cases[0].input, cases[0].fileIds, cases[0].expectation.prompt]) === JSON.stringify([cases[1].input, cases[1].fileIds, cases[1].expectation.prompt])) throw new Error('新用例必须使用不同的实际输入或任务，不能仅改变名称与预期。');
    error.value = ''; emit('verify', cases);
  } catch (cause) { error.value = cause instanceof SyntaxError ? '输入或预期结果的 JSON 格式有误，请检查。' : cause.message; }
}
</script>
<template>
  <section class="cap-validation-suite" aria-label="原用例与新输入验证">
    <h3>先验证原任务用例</h3><p>保存时的原用例已填入；可选再加入一个不同输入。保存或查看不会执行验证。</p>
    <form @submit.prevent="submit">
      <p v-if="originalNeedsCompletion" role="status">保存版本只有静态候选，独立预期尚待补全；请核对后再验证。</p>
      <button type="button" class="secondary-button" :disabled="busy || !originalCase" @click="restoreOriginalCase">一键填入原用例</button>
      <fieldset :disabled="busy"><legend>01 · 原任务用例</legend><label>用例名称<input v-model="rows[0].label" /></label>
        <template v-if="kind === 'skill'"><label>本次任务<textarea v-model="rows[0].prompt" rows="4" :aria-label="rows[0].label + ' · 任务'" /></label><label>独立验收标准<textarea v-model="rows[0].criteria" rows="3" :aria-label="rows[0].label + ' · 验收标准'" placeholder="逐项说明应满足的结果、数据依据及不能发生的行为" /></label><details><summary>结构化数据与约束（可选）</summary><label>本用例实际输入（JSON）<textarea v-model="rows[0].inputText" rows="5" spellcheck="false" :aria-label="rows[0].label + ' · 结构化输入'" /></label></details></template>
        <template v-else><CapabilityField v-if="formSupported(inputSchema)" v-model="rows[0].input" :schema="inputSchema" :files="files.filter(file => rows[0].fileIds.includes(file.id))" /><label v-else>实际输入（JSON）<textarea v-model="rows[0].inputText" rows="5" spellcheck="false" /></label><label>如何核对实际结果<select v-model="rows[0].expectationMode" :aria-label="rows[0].label + ' · 检查方式'"><option value="json_equals">精确结果</option><option value="assertions">字段与业务断言</option></select></label><label v-if="rows[0].expectationMode === 'json_equals'">独立预期结果（JSON）<textarea v-model="rows[0].expected" rows="4" spellcheck="false" /></label><label v-else>逐项断言（JSON）<textarea v-model="rows[0].assertions" rows="6" spellcheck="false" :aria-label="rows[0].label + ' · 断言'" /><small>例如 [{"path":["records"],"op":"minItems","value":3}]。应核对真实业务要求，不能仅检查有输出。</small></label></template>
        <fieldset v-if="inputFiles"><legend>仅本用例授权</legend><label v-for="file in files" :key="file.id" class="cap-suite-file"><input v-model="rows[0].fileIds" type="checkbox" :value="file.id" />{{ file.name }}</label></fieldset>
      </fieldset>
      <details class="cap-additional-case"><summary>02 · 不同输入用例（可选）</summary>
        <button type="button" class="secondary-button" :disabled="busy || !originalCase" @click="copyOriginalToAdditional">从原用例填入</button>
        <label class="cap-suite-file"><input v-model="includeAdditionalCase" type="checkbox" :disabled="busy" />将这个不同输入用例加入本次验证</label>
        <fieldset :disabled="busy"><legend>不同输入用例</legend><label>用例名称<input v-model="rows[1].label" /></label>
          <template v-if="kind === 'skill'"><label>本次任务<textarea v-model="rows[1].prompt" rows="4" :aria-label="rows[1].label + ' · 任务'" /></label><label>独立验收标准<textarea v-model="rows[1].criteria" rows="3" :aria-label="rows[1].label + ' · 验收标准'" placeholder="逐项说明应满足的结果、数据依据及不能发生的行为" /></label><details><summary>结构化数据与约束（可选）</summary><label>本用例实际输入（JSON）<textarea v-model="rows[1].inputText" rows="5" spellcheck="false" :aria-label="rows[1].label + ' · 结构化输入'" /></label></details></template>
          <template v-else><CapabilityField v-if="formSupported(inputSchema)" v-model="rows[1].input" :schema="inputSchema" :files="files.filter(file => rows[1].fileIds.includes(file.id))" /><label v-else>实际输入（JSON）<textarea v-model="rows[1].inputText" rows="5" spellcheck="false" /></label><label>如何核对实际结果<select v-model="rows[1].expectationMode" :aria-label="rows[1].label + ' · 检查方式'"><option value="json_equals">精确结果</option><option value="assertions">字段与业务断言</option></select></label><label v-if="rows[1].expectationMode === 'json_equals'">独立预期结果（JSON）<textarea v-model="rows[1].expected" rows="4" spellcheck="false" /></label><label v-else>逐项断言（JSON）<textarea v-model="rows[1].assertions" rows="6" spellcheck="false" :aria-label="rows[1].label + ' · 断言'" /><small>应核对真实业务要求，不能仅检查有输出。</small></label></template>
          <fieldset v-if="inputFiles"><legend>仅本用例授权</legend><label v-for="file in files" :key="file.id" class="cap-suite-file"><input v-model="rows[1].fileIds" type="checkbox" :value="file.id" />{{ file.name }}</label></fieldset>
        </fieldset>
      </details>
      <p v-if="error" role="alert" class="cap-suite-error">{{ USER_FACING_ERROR }}</p><p>点击后实际执行所选用例，可能使用模型或外部服务。验证不会自动启用。</p>
      <button type="submit" class="primary-button" :disabled="busy || !allowed">{{ includeAdditionalCase ? '开始两组验证' : '开始原用例验证' }}</button>
    </form>
    <section v-if="verification?.cases?.length" class="cap-suite-results"><h4>逐案实际证据</h4><details v-for="(item,index) in verification.cases" :key="index"><summary>{{ item.label || '用例 ' + (index + 1) }} · {{ ({passed:'通过',failed:'未通过',awaiting_review:'待审阅'})[item.status] || item.status }}</summary><p v-if="item.error" class="cap-suite-error">{{ USER_FACING_ERROR }}</p><CapabilityResult v-if="!item.error && item.status !== 'failed'" :value="item.result" :schema="outputSchema" /><pre v-if="!item.error && item.status !== 'failed'">{{ JSON.stringify(item, null, 2) }}</pre></details><div v-if="verification.status === 'awaiting_review'"><p>请核对已执行的用例与验收标准后确认。</p><button type="button" class="primary-button" :disabled="busy" @click="emit('review','accept')">{{ verification.cases.length > 1 ? '确认全部符合预期' : '确认原用例符合预期' }}</button><button type="button" class="secondary-button" :disabled="busy" @click="emit('review','reject')">存在不符合项</button></div></section>
  </section>
</template>
<style scoped>
.cap-validation-suite{border-top:1px solid var(--line);padding-top:20px;margin-top:22px}.cap-validation-suite h3{font-size:16px;margin:0 0 12px}.cap-validation-suite p{font-size:12px;line-height:1.8;color:var(--muted)}.cap-validation-suite fieldset{margin:16px 0;border:1px solid var(--line);border-radius:9px;padding:16px;min-width:0}.cap-validation-suite legend{font-size:12px;padding:0 6px}.cap-validation-suite label{display:grid;gap:7px;font-size:12px;margin:12px 0}.cap-validation-suite input:not([type=checkbox]),.cap-validation-suite textarea,.cap-validation-suite select{width:100%;min-width:0;border:1px solid var(--line);border-radius:8px;padding:10px;background:var(--surface-2);color:var(--text);font:inherit;line-height:1.7}.cap-validation-suite .cap-suite-file{display:flex;gap:8px;align-items:center}.cap-validation-suite .cap-suite-error{color:var(--danger)}.cap-suite-results details{margin:14px 0}.cap-suite-results summary{font-size:12px;cursor:pointer;color:var(--build-ink)}.cap-suite-results pre{font-size:11px;line-height:1.8;white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;background:var(--surface-2);padding:12px}.cap-suite-results button{margin:8px 8px 0 0}
.cap-additional-case{margin:18px 0;border:1px solid var(--line);border-radius:9px;padding:16px}.cap-additional-case summary{cursor:pointer;font-size:12px;font-weight:600}.cap-additional-case>.secondary-button{margin-top:14px}.cap-additional-case>.cap-suite-file{margin:15px 0}.cap-validation-suite form>.secondary-button{margin:6px 0}
</style>
