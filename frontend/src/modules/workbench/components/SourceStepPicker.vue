<script setup>
import { computed } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
const props = defineProps({ candidates: { type: Array, default: () => [] }, modelValue: { type: Array, default: () => [] },
  source: { type: Object, default: () => ({}) }, warnings: { type: Array, default: () => [] }, busy: Boolean,
  submitLabel: { type: String, default: '按所选步骤整理草稿' } });
const emit = defineEmits(['update:modelValue', 'submit', 'cancel']);
const selected = computed(() => props.candidates.filter(item => item.reconstructable && props.modelValue.includes(item.stepId)));
function toggle(item, checked) {
  const ids = new Set(props.modelValue);
  checked ? ids.add(item.stepId) : ids.delete(item.stepId);
  emit('update:modelValue', [...ids]);
}
</script>

<template>
  <section class="source-step-picker" aria-label="选择实际步骤">
    <div class="source-step-head"><div><span>从执行记录整理</span><h3>选择要保留的步骤</h3></div><strong>已选 {{ selected.length }} 项</strong></div>
    <p>可以选择一个或多个成功步骤。必要的前序依赖会一并检查；不会重新执行原任务。</p>
    <p v-if="source.objectiveCompleted === false" class="source-step-warning">原任务未全部完成。这次仅保存所选成功步骤，不代表原目标已经交付。</p>
    <p v-else-if="source.objectiveCompleted == null" class="source-step-warning">历史记录不能确认原目标已全部完成，请按实际结果选择。</p>
    <p v-if="warnings.length" class="source-step-warning">{{ USER_FACING_ERROR }}</p>
    <div v-if="candidates.length" class="source-step-options">
      <label v-for="item in candidates" :key="item.stepId" class="source-step-option" :class="{unavailable:!item.reconstructable}">
        <input type="checkbox" :checked="modelValue.includes(item.stepId)" :disabled="busy || !item.reconstructable" :aria-label="'保留步骤 ' + item.stepId" @change="toggle(item,$event.target.checked)" />
        <span><strong>{{ item.title || item.stepId }}</strong><small>{{ item.type === 'code' ? '实际代码' : '工具调用' }} · {{ item.adopted === true ? '已记录用于最终交付' : '由你明确选择保留' }}</small><small v-if="item.inputStepIds?.length">依赖 {{ item.inputStepIds.length }} 个前序步骤，会一并核对。</small><small v-if="item.issues?.length" class="source-step-warning">{{ USER_FACING_ERROR }}</small></span>
      </label>
    </div>
    <p v-else>记录中没有可恢复的成功步骤。可以保留任务结果，或另行创建新草稿。</p>
    <div class="source-step-actions"><button type="button" class="primary-button" :disabled="busy || !selected.length" @click="emit('submit',selected.map(item => item.stepId))">{{ busy ? '正在整理…' : submitLabel }}</button><button type="button" class="secondary-button" :disabled="busy" @click="emit('cancel')">暂不整理</button></div>
  </section>
</template>

<style scoped>
.source-step-picker{border:1px solid var(--line,#dce4e8);border-radius:12px;padding:22px;background:var(--surface,#fff);margin:18px 0;min-width:0}.source-step-head{display:flex;justify-content:space-between;align-items:center;gap:14px}.source-step-head span{font-size:11px;color:var(--muted)}.source-step-head h3{font-size:20px;margin:7px 0}.source-step-head>strong{font-size:12px;white-space:nowrap;color:var(--build-ink,#37627c)}.source-step-picker p{font-size:12px;line-height:1.8;color:var(--muted);margin:9px 0}.source-step-options{display:grid;gap:10px;margin:18px 0}.source-step-option{display:flex;gap:12px;align-items:flex-start;border:1px solid var(--line,#dce4e8);border-radius:9px;padding:14px;cursor:pointer}.source-step-option:has(input:checked){background:var(--build-wash,#f0f7fa);border-color:#a9c5d9}.source-step-option.unavailable{cursor:default;background:var(--surface-2,#f5f8f9)}.source-step-option input{margin:3px 0 0;flex:none}.source-step-option>span{min-width:0;overflow-wrap:anywhere}.source-step-option strong{font-size:13px;line-height:1.6}.source-step-option small{display:block;font-size:11px;line-height:1.8;color:var(--muted);margin-top:3px}.source-step-picker .source-step-warning{color:var(--warning-ink,#94623a)}.source-step-actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:18px}@media(max-width:600px){.source-step-picker{padding:16px}.source-step-head h3{font-size:18px}.source-step-option{padding:12px}}
</style>
