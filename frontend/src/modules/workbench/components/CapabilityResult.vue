<script setup>
defineProps({ value: null, schema: Object });
</script>
<template>
  <dl v-if="value !== null && typeof value === 'object' && !Array.isArray(value)" class="readable-result"><div v-for="(item, key) in value" :key="key"><dt>{{ schema?.properties?.[key]?.title || key }}</dt><dd><CapabilityResult :value="item" :schema="schema?.properties?.[key]" /></dd></div></dl>
  <ol v-else-if="Array.isArray(value)"><li v-for="(item, index) in value" :key="index"><CapabilityResult :value="item" :schema="schema?.items" /></li></ol>
  <p v-else class="result-text">{{ value === null ? '空值' : typeof value === 'boolean' ? (value ? '是' : '否') : value }}</p>
</template>
<style scoped>
.readable-result{margin:12px 0}.readable-result>div{padding:8px 0}.readable-result dt{font-size:12px;color:#63766b}.readable-result dd{margin:0}.result-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.8;margin:4px 0;font-size:16px;color:#243b32}
</style>
