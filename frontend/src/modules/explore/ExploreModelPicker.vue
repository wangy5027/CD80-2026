<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { defaultExploreModel, exploreModels } from './model.js';

const props = defineProps({
  modelValue: { type: String, default: defaultExploreModel },
  disabled: { type: Boolean, default: false },
});
const emit = defineEmits(['update:modelValue']);
const root = ref(null);
const trigger = ref(null);
const menu = ref(null);
const open = ref(false);
const selected = computed(() => exploreModels.find(model => model.id === props.modelValue) || exploreModels[0]);

function close(restoreFocus = false) {
  open.value = false;
  if (restoreFocus) nextTick(() => trigger.value?.focus());
}
function focusOption(index) {
  nextTick(() => menu.value?.querySelectorAll('[role="menuitemradio"]')[index]?.focus());
}
function showMenu(index = exploreModels.findIndex(model => model.id === selected.value.id)) {
  if (props.disabled) return;
  open.value = true;
  focusOption(Math.max(0, index));
}
function toggle() {
  if (open.value) close(true);
  else showMenu();
}
function choose(model) {
  if (props.disabled) return;
  emit('update:modelValue', model.id);
  close(true);
}
function onTriggerKeydown(event) {
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? exploreModels.length - 1
    : event.key === 'ArrowDown' ? (exploreModels.findIndex(model => model.id === selected.value.id) + 1) % exploreModels.length
      : (exploreModels.findIndex(model => model.id === selected.value.id) + exploreModels.length - 1) % exploreModels.length;
  showMenu(index);
}
function onMenuKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault();
    close(true);
    return;
  }
  if (event.key === 'Tab') { close(); return; }
  if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const options = [...(menu.value?.querySelectorAll('[role="menuitemradio"]') || [])];
  const current = Math.max(0, options.indexOf(document.activeElement));
  const index = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1
    : (current + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
  options[index]?.focus();
}
function onDocumentPointerDown(event) {
  if (open.value && !root.value?.contains(event.target)) close();
}
function onDocumentFocusIn(event) {
  if (open.value && !root.value?.contains(event.target)) close();
}
onMounted(() => {
  document.addEventListener('pointerdown', onDocumentPointerDown);
  document.addEventListener('focusin', onDocumentFocusIn);
});
onUnmounted(() => {
  document.removeEventListener('pointerdown', onDocumentPointerDown);
  document.removeEventListener('focusin', onDocumentFocusIn);
});
watch(() => props.disabled, disabled => { if (disabled) close(); });
</script>

<template>
  <div ref="root" class="ex-model-picker" data-testid="explore-model-picker">
    <button
      ref="trigger"
      class="ex-model-picker__trigger"
      data-testid="explore-model-trigger"
      type="button"
      aria-label="选择下一次研究使用的模型"
      aria-haspopup="menu"
      :aria-expanded="open"
      :disabled="disabled"
      title="选择下一次研究使用的模型"
      @click="toggle"
      @keydown="onTriggerKeydown"
    ><span class="ex-model-picker__spark" aria-hidden="true">✦</span><span>{{ selected.label }}</span><svg aria-hidden="true" viewBox="0 0 16 16" fill="none"><path d="m4 6 4 4 4-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
    <div v-if="open" ref="menu" class="ex-model-picker__menu" data-testid="explore-model-menu" role="menu" aria-label="下一次研究使用的模型" @keydown="onMenuKeydown">
      <button
        v-for="model in exploreModels"
        :key="model.id"
        class="ex-model-picker__option"
        :data-testid="`explore-model-${model.id}`"
        type="button"
        role="menuitemradio"
        :aria-checked="selected.id === model.id"
        @click="choose(model)"
      ><span class="ex-model-picker__option-text"><strong>{{ model.label }}</strong><small>{{ model.description }}</small></span><span class="ex-model-picker__check" aria-hidden="true">{{ selected.id === model.id ? '✓' : '' }}</span></button>
    </div>
  </div>
</template>

<style scoped>
.ex-model-picker { position: relative; flex: none; }
.ex-model-picker__trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  min-height: 37px;
  padding: 0 9px;
  border: 1px solid rgba(128, 125, 186, .14);
  border-radius: 11px;
  background: rgba(247, 249, 255, .28);
  color: #3d3b64;
  font: inherit;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
}
.ex-model-picker__trigger:hover:not(:disabled), .ex-model-picker__trigger[aria-expanded="true"] {
  border-color: rgba(113, 105, 204, .34);
  background: rgba(255, 255, 255, .62);
}
.ex-model-picker__trigger:disabled { opacity: .55; cursor: default; }
.ex-model-picker__spark { color: #675dc3; font-size: 14px; }
.ex-model-picker__trigger svg { width: 14px; height: 14px; color: #716d97; }
.ex-model-picker__menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + 8px);
  z-index: 10;
  box-sizing: border-box;
  min-width: 190px;
  padding: 5px;
  border: 1px solid rgba(126, 120, 191, .21);
  border-radius: 14px;
  background: #fff;
  box-shadow: 0 12px 28px rgba(41, 36, 98, .18);
}
.ex-model-picker__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  width: 100%;
  padding: 9px 10px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: #33304f;
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.ex-model-picker__option:hover, .ex-model-picker__option:focus-visible { background: #f0effb; }
.ex-model-picker__option-text { display: grid; gap: 2px; }
.ex-model-picker__option strong { font-size: 13px; }
.ex-model-picker__option small { color: #706d84; font-size: 11px; }
.ex-model-picker__check { min-width: 12px; color: #514ab0; font-weight: 700; }
.ex-model-picker button:focus-visible { outline: 2px solid #5753b7; outline-offset: 2px; }
@media (max-width: 690px) {
  .ex-model-picker__menu { right: auto; left: 0; }
}
@media (max-width: 390px) {
  .ex-model-picker__trigger { padding-inline: 7px; }
}
</style>
