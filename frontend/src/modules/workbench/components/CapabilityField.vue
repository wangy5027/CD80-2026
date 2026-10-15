<script setup>
import { computed } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { emptyValue } from '../capabilityForms.js';
const props = defineProps({ schema: Object, modelValue: null, label: String, path: { type: Array, default: () => [] }, errors: { type: Array, default: () => [] }, files: { type: Array, default: () => [] } });
const emit = defineEmits(['update:modelValue']);
const value = computed(() => props.modelValue);
const messages = computed(() => props.errors.filter(e => JSON.stringify(e.path) === JSON.stringify(props.path)));
function put(key, val) { emit('update:modelValue', Array.isArray(value.value) ? value.value.map((v, i) => i === key ? val : v) : { ...value.value, [key]: val }); }
function omit(key) { const next = { ...value.value }; delete next[key]; emit('update:modelValue', next); }
function number(event) { emit('update:modelValue', event.target.value === '' ? '' : Number(event.target.value)); }
</script>

<template>
  <div class="cap-field">
    <template v-if="schema.type === 'object'">
      <strong v-if="label">{{ label }}</strong>
      <div v-for="(child, key) in schema.properties" :key="key" class="cap-field-child">
        <label v-if="!schema.required?.includes(key)" class="optional-field"><input type="checkbox" :checked="key in (value || {})" @change="$event.target.checked ? put(key, emptyValue(child)) : omit(key)" />填写{{ child.title || key }}（可选）</label>
        <CapabilityField v-if="key in (value || {})" :schema="child" :model-value="value[key]" :label="child.title || key" :path="[...path, key]" :errors="errors" :files="files" @update:model-value="put(key, $event)" />
        <p v-else-if="schema.required?.includes(key)"><button type="button" @click="put(key, emptyValue(child))">填写{{ child.title || key }}</button></p>
      </div>
    </template>
    <fieldset v-else-if="schema.type === 'array'"><legend>{{ label }}</legend><p v-if="schema.description" class="field-hint">{{ schema.description }}</p>
      <div v-for="(item, index) in value || []" :key="index" class="array-item">
        <CapabilityField :schema="schema.items" :model-value="item" :label="`${schema.items.title || '项目'} ${index + 1}`" :path="[...path, index]" :errors="errors" :files="files" @update:model-value="put(index, $event)" />
        <button type="button" class="secondary-button" :aria-label="`移除${label}第${index + 1}项`" @click="emit('update:modelValue', value.filter((_, i) => i !== index))">移除</button>
      </div>
      <button type="button" class="secondary-button" :disabled="schema.maxItems != null && (value?.length || 0) >= schema.maxItems" @click="emit('update:modelValue', [...(value || []), emptyValue(schema.items)])">添加{{ schema.items.title || '项目' }}</button>
    </fieldset>
    <label v-else>{{ label }}
      <select :aria-label="label" v-if="schema.enum" :value="JSON.stringify(value)" @change="emit('update:modelValue', JSON.parse($event.target.value))"><option disabled value="">请选择</option><option v-for="option in schema.enum" :key="JSON.stringify(option)" :value="JSON.stringify(option)">{{ option }}</option></select>
      <select :aria-label="label" v-else-if="schema.type === 'boolean'" :value="String(value)" @change="emit('update:modelValue', $event.target.value === 'true')"><option value="true">是</option><option value="false">否</option></select>
      <span v-else-if="schema.type === 'null'">空值</span>
      <input :aria-label="label" v-else-if="['number', 'integer'].includes(schema.type)" type="number" :step="schema.type === 'integer' ? 1 : 'any'" :value="value" :aria-invalid="!!messages.length" @input="number" />
      <input :aria-label="label" v-else type="text" :value="value" :aria-invalid="!!messages.length" :placeholder="schema.examples?.[0] || schema.description" @input="emit('update:modelValue', $event.target.value)" />

      <small v-if="schema.description" class="field-hint">{{ schema.description }}</small>
    </label>
      <select v-if="schema.type === 'string' && !schema.enum && files.length" aria-label="从授权文件填入路径" value="" @change="emit('update:modelValue', $event.target.value)"><option value="">从本次授权文件填入路径（如需要）</option><option v-for="file in files" :key="file.id" :value="'/input/' + file.id + (file.name?.includes('.') ? '.' + file.name.split('.').at(-1) : '')">{{ file.name }}</option></select>
    <p v-if="messages.length" class="field-error" role="alert">{{ USER_FACING_ERROR }}</p>
  </div>
</template>

<style scoped>
.cap-field{min-width:0}.cap-field label{display:flex;flex-direction:column;gap:8px;margin:12px 0;font-size:13px}.cap-field textarea,.cap-field input,.cap-field select{box-sizing:border-box;width:100%;border:1px solid #cfdcd3;background:#fff;border-radius:8px;padding:10px;font:inherit;color:#243b32}.cap-field textarea{resize:vertical;min-height:42px}.cap-field fieldset{border:1px solid #dce3dc;border-radius:10px;padding:16px;margin:16px 0}.array-item{display:flex;gap:12px;align-items:center}.array-item>.cap-field{flex:1}.field-hint{color:#607266;line-height:1.6}.field-error{color:#a03232;font-size:13px}.optional-field{flex-direction:row!important;align-items:center}.optional-field input{width:auto}.cap-field :focus-visible{outline:2px solid #4b7b64;outline-offset:3px}
</style>
