<script setup>
import { computed } from 'vue';

const props = defineProps({
  ids: { type: Array, default: () => [] },
  sources: { type: Array, default: () => [] },
});
const emit = defineEmits(['open']);

function sourceId(value) {
  return typeof value === 'string' || typeof value === 'number' ? String(value).trim() : '';
}
const uniqueIds = computed(() => {
  const seen = new Set();
  return (Array.isArray(props.ids) ? props.ids : []).map(sourceId).filter(id => {
    if (!id || seen.has(id)) return false;
    seen.add(id);
    return true;
  });
});
const byId = computed(() => {
  const found = new Map();
  for (const source of Array.isArray(props.sources) ? props.sources : []) {
    const id = sourceId(source?.id);
    if (id && !found.has(id)) found.set(id, source);
  }
  return found;
});
const knownIds = computed(() => uniqueIds.value.filter(id => byId.value.has(id)));
const unknownIds = computed(() => uniqueIds.value.filter(id => !byId.value.has(id)));
const knownTitle = computed(() => `查看来源：${knownIds.value.map(id => {
  const source = byId.value.get(id);
  return `${id} ${String(source?.title || source?.source_name || source?.source || '标题未提供')}`;
}).join('；')}`);
const unknownTitle = computed(() => `未映射来源：${unknownIds.value.join('、')}`);

function openSources() { emit('open', [...knownIds.value]); }
</script>

<template>
  <span v-if="knownIds.length || unknownIds.length" class="explore-source-chip-group">
    <button v-if="knownIds.length" class="explore-source-chip" data-testid="explore-source-chip" type="button" :aria-label="knownTitle" :title="knownTitle" @click="openSources">
      <svg viewBox="0 0 20 20" fill="none" aria-hidden="true" focusable="false"><path d="M5.5 2.75h5.3l3.7 3.7v10.8H5.5a1 1 0 0 1-1-1V3.75a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" /><path d="M10.8 2.75v3.7h3.7M7.2 10h5.6M7.2 13h4.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" /></svg>
      <span>来源<template v-if="knownIds.length > 1"> +{{ knownIds.length - 1 }}</template></span>
    </button>
    <span v-if="unknownIds.length" class="explore-source-chip-unknown" data-testid="explore-source-unmapped" :title="unknownTitle">来源待核对</span>
  </span>
</template>

<style scoped>
.explore-source-chip-group { display: inline-flex; align-items: center; gap: 5px; max-width: 100%; margin-inline-start: 5px; vertical-align: -.16em; white-space: nowrap; }
.explore-source-chip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 3px;
  box-sizing: border-box;
  height: 20px;
  padding: 0 6px 0 5px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface-2);
  color: var(--muted);
  font: inherit;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}
.explore-source-chip:hover { border-color: var(--accent); background: var(--explore-wash); color: var(--explore-ink); }
.explore-source-chip:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }
.explore-source-chip svg { flex: none; width: 12px; height: 12px; }
.explore-source-chip-unknown { color: var(--dim); font-size: 11px; line-height: 20px; white-space: nowrap; }
</style>
