<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { modules } from './model.js';
import ExploreModelPicker from './ExploreModelPicker.vue';

const props = defineProps({
  state: { type: Object, required: true },
  isActive: { type: Boolean, default: false },
  canSubmit: { type: Boolean, default: false },
  currentContext: { type: Object, default: () => ({ mode: 'auto', file_ids: [] }) },
  selectedContextCount: { type: Number, default: 0 },
  motionPaused: { type: Boolean, default: false },
  scene: { type: String, default: 'entry' },
});
const emit = defineEmits(['submit', 'open-files', 'update:module', 'update:model', 'update:draft']);

const shell = ref(null);
const textarea = ref(null);
const moduleRail = ref(null);
const sliderStyle = ref({});
const sliderReady = ref(false);
const textFocused = ref(false);
const draftModel = computed({
  get: () => props.state.draft || '',
  set: value => emit('update:draft', value),
});
const inputDisabled = computed(() => props.state.loading || props.state.loadingSession || props.state.submitting);
const contextLabel = computed(() => {
  if (props.currentContext?.mode === 'manual') return `自选 · ${props.selectedContextCount} 个`;
  const count = props.state.contextPreview?.preview_files?.length;
  return Number.isInteger(count) ? `自动预计 · ${count} 个` : '自动选择';
});
let resizeObserver = null;
let resizeFrame = 0;
let lastShellWidth = 0;
let composing = false;
let compositionEndedAt = 0;

function measureSlider() {
  const rail = moduleRail.value;
  const selected = rail?.querySelector(`button[data-module="${props.state.module}"]`);
  if (!rail || !selected) return;
  const railRect = rail.getBoundingClientRect();
  const rect = selected.getBoundingClientRect();
  if (!rect.width) return;
  sliderStyle.value = {
    width: `${rect.width}px`,
    height: `${rect.height}px`,
    transform: `translate3d(${rect.left - railRect.left}px, ${rect.top - railRect.top}px, 0)`,
  };
  sliderReady.value = true;
}
function measureTextarea() {
  const element = textarea.value;
  if (!element) return;
  const previousScrollTop = element.scrollTop;
  const viewportHeight = window.visualViewport?.scale === 1 ? window.visualViewport.height : window.innerHeight;
  const shortViewport = viewportHeight <= 460;
  const minimum = shortViewport ? 44 : props.scene === 'entry' ? 80 : 58;
  const maximum = Math.max(minimum, Math.min(260, viewportHeight * (shortViewport ? .2 : .34)));
  element.style.minHeight = `${minimum}px`;
  element.style.maxHeight = `${maximum}px`;
  element.style.height = 'auto';
  const wanted = Math.max(minimum, Math.min(element.scrollHeight, maximum));
  element.style.height = `${Math.ceil(wanted)}px`;
  element.style.overflowY = element.scrollHeight > maximum + 1 ? 'auto' : 'hidden';
  element.scrollTop = previousScrollTop;
}
function measure() {
  resizeFrame = 0;
  measureSlider();
  measureTextarea();
}
function scheduleMeasure() {
  if (resizeFrame) return;
  resizeFrame = requestAnimationFrame(measure);
}
function onObservedResize() {
  const width = shell.value?.getBoundingClientRect().width || 0;
  if (Math.abs(width - lastShellWidth) > .5) {
    lastShellWidth = width;
    scheduleMeasure();
  } else {
    // Font loading and zoom can change tab widths without changing the shell.
    measureSlider();
  }
}
function onInput() { scheduleMeasure(); }
function onCompositionStart() { composing = true; }
function onCompositionEnd(event) {
  composing = false;
  compositionEndedAt = event.timeStamp;
  scheduleMeasure();
}
function snapshotRect(element) {
  if (!element) return null;
  const rect = element.getBoundingClientRect();
  return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
}
function getInteractionSnapshot() {
  const editor = textarea.value;
  return {
    shellRect: snapshotRect(shell.value),
    editorRect: snapshotRect(editor),
    textFocused: Boolean(editor && document.activeElement === editor),
    composing,
    selectionStart: editor?.selectionStart ?? null,
    selectionEnd: editor?.selectionEnd ?? null,
    editorScrollTop: editor?.scrollTop ?? 0,
  };
}
function onInputKeydown(event) {
  const justConfirmed = compositionEndedAt && event.timeStamp >= compositionEndedAt && event.timeStamp - compositionEndedAt < 60;
  if (event.key !== 'Enter' || event.shiftKey || event.ctrlKey || event.metaKey || event.altKey || event.repeat ||
      composing || event.isComposing || event.keyCode === 229 || justConfirmed || !props.canSubmit) return;
  event.preventDefault();
  emit('submit');
}
function onModuleKeydown(event) {
  const keys = ['ArrowRight', 'ArrowLeft', 'Home', 'End'];
  if (!keys.includes(event.key)) return;
  const index = modules.findIndex(item => item.id === event.target?.dataset?.module);
  if (index < 0) return;
  event.preventDefault();
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? modules.length - 1
    : (index + (event.key === 'ArrowRight' ? 1 : -1) + modules.length) % modules.length;
  const id = modules[next].id;
  emit('update:module', id);
  nextTick(() => moduleRail.value?.querySelector(`button[data-module="${id}"]`)?.focus());
}
function focus() { textarea.value?.focus(); }
defineExpose({ focus, getInteractionSnapshot });

onMounted(() => {
  nextTick(scheduleMeasure);
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(onObservedResize);
    if (shell.value) resizeObserver.observe(shell.value);
    if (moduleRail.value) {
      resizeObserver.observe(moduleRail.value);
      moduleRail.value.querySelectorAll('button[data-module]').forEach(button => resizeObserver.observe(button));
    }
  }
  window.addEventListener('resize', scheduleMeasure);
  window.visualViewport?.addEventListener('resize', scheduleMeasure);
});
onUnmounted(() => {
  resizeObserver?.disconnect();
  window.removeEventListener('resize', scheduleMeasure);
  window.visualViewport?.removeEventListener('resize', scheduleMeasure);
  if (resizeFrame) cancelAnimationFrame(resizeFrame);
});
watch(() => props.state.module, () => nextTick(scheduleMeasure));
watch(() => props.state.draft, scheduleMeasure);
watch(() => props.scene, scheduleMeasure);
</script>

<template>
  <div class="explore-glass-composer" data-testid="explore-composer" :data-scene="scene" :class="{ 'is-motion-paused': motionPaused }">
    <div ref="shell" class="ex-glass-composer" :class="{ 'is-text-focused': textFocused }">
      <div class="ex-glass-composer__shine" aria-hidden="true" />
      <label class="sr-only" for="explore-input">研究主题、问题、新闻原文或链接</label>
      <textarea
        id="explore-input"
        ref="textarea"
        v-model="draftModel"
        data-testid="explore-input"
        rows="2"
        maxlength="12000"
        :placeholder="isActive ? '研究进行中，可以先写下一个问题…' : '输入研究主题、问题、新闻原文或链接…'"
        :disabled="inputDisabled"
        @focus="textFocused = true"
        @blur="textFocused = false"
        @input="onInput"
        @compositionstart="onCompositionStart"
        @compositionend="onCompositionEnd"
        @keydown="onInputKeydown"
      />
      <div class="ex-glass-composer__tools">
        <div ref="moduleRail" class="ex-glass-composer__modules" role="group" aria-label="选择研究模块" @keydown="onModuleKeydown">
          <span class="ex-glass-composer__slider" data-testid="explore-module-slider" :class="{ ready: sliderReady }" :style="sliderStyle" aria-hidden="true" />
          <button
            v-for="item in modules"
            :key="item.id"
            :data-module="item.id"
            :data-testid="'explore-module-' + item.id"
            type="button"
            :class="{ active: state.module === item.id }"
            :aria-pressed="state.module === item.id"
            :disabled="inputDisabled || isActive"
            @click="emit('update:module', item.id)"
          >{{ item.title }}</button>
        </div>
        <div class="ex-glass-composer__actions">
          <ExploreModelPicker :model-value="state.model" :disabled="state.submitting" @update:model-value="emit('update:model', $event)" />
          <button
            class="ex-glass-composer__files"
            data-testid="explore-files-open"
            type="button"
            :disabled="!state.session || state.loadingSession"
            :title="state.session ? `参考材料：${contextLabel}` : '提交一次研究后可选择参考材料'"
            @click="emit('open-files')"
          ><span class="ex-glass-composer__files-full">参考材料</span><span class="ex-glass-composer__files-short">材料</span><Transition name="ex-files-count" mode="out-in"><small :key="contextLabel">{{ contextLabel }}</small></Transition></button>
          <button
            class="ex-glass-composer__submit"
            data-testid="explore-submit"
            type="button"
            :disabled="!canSubmit"
            :aria-label="state.submitting ? '正在提交研究' : '开始研究'"
            :title="state.submitting ? '正在提交研究' : '开始研究'"
            @click="emit('submit')"
          ><svg aria-hidden="true" viewBox="0 0 24 24" fill="none"><path d="M12 19V5m0 0-6 6m6-6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg></button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.explore-glass-composer {
  box-sizing: border-box;
  width: min(100%, 980px);
  margin: 0 auto;
}
.ex-glass-composer {
  position: relative;
  isolation: isolate;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
  width: 100%;
  min-height: 140px;
  margin: 0 auto;
  padding: 9px 12px 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius-panel);
  background: var(--panel);
  color: var(--text);
  box-shadow: var(--shadow-panel);
  transition: border-color 170ms ease, box-shadow 170ms ease;
}
.ex-glass-composer:hover {
  border-color: #b8d6da;
  box-shadow: var(--shadow-panel);
}
.ex-glass-composer.is-text-focused {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px rgb(8 123 128 / 12%);
}
.ex-glass-composer__shine {
  display: none;
  position: absolute;
  z-index: 0;
  top: 0;
  right: 0;
  left: 0;
  height: 3px;
  overflow: hidden;
  border-radius: inherit;
  pointer-events: none;
}
.ex-glass-composer__shine::before {
  content: '';
  position: absolute;
  top: 0;
  bottom: 0;
  left: -45%;
  width: 64%;
  transform: translate3d(-22%, 0, 0);
  background: linear-gradient(90deg, transparent, rgba(120, 196, 250, .43) 33%, rgba(160, 133, 239, .4) 62%, transparent);
  filter: blur(.65px);
  animation: ex-composer-flow 15s ease-in-out infinite alternate;
}
.ex-glass-composer__source-slot { position: relative; z-index: 1; flex: none; min-height: 27px; margin: 0 3px 2px; }
.ex-glass-composer__source {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  min-height: 27px;
  margin: 0;
  padding: 0 4px;
  border: 0;
  background: transparent;
  color: var(--muted);
  font-size: 12px;
}
.ex-glass-composer__source span { flex: none; }
.ex-glass-composer__source strong {
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-weight: 650;
}
.ex-glass-composer__source button {
  flex: none;
  width: 25px;
  height: 25px;
  margin-left: auto;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--accent);
  font-size: 19px;
  line-height: 1;
  cursor: pointer;
}
.ex-glass-composer__source button:hover { background: var(--explore-wash); }
.ex-glass-composer textarea {
  position: relative;
  z-index: 1;
  display: block;
  box-sizing: border-box;
  width: 100%;
  min-height: 58px;
  max-height: min(260px, 34dvh);
  margin: 2px 0 4px;
  padding: 10px 10px 7px;
  border: 0;
  border-radius: 0;
  outline: 0;
  resize: none;
  background: transparent;
  color: var(--text);
  font: inherit;
  font-size: 15px;
  line-height: 1.5;
  caret-color: var(--accent);
}
.ex-glass-composer textarea::placeholder { color: var(--dim); opacity: 1; }
.ex-glass-composer textarea:disabled { opacity: .67; cursor: not-allowed; }
.ex-glass-composer__tools {
  position: relative;
  z-index: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 9px;
  min-width: 0;
}
.ex-glass-composer__modules {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex: none;
  min-height: 38px;
  padding: 3px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
}
.ex-glass-composer__slider {
  position: absolute;
  top: 0;
  left: 0;
  z-index: 0;
  border-radius: 9px;
  opacity: 0;
  background: var(--panel);
  box-shadow: 0 1px 4px rgb(27 57 78 / 8%);
  pointer-events: none;
  transition: transform 200ms cubic-bezier(.22, 1, .36, 1), width 200ms cubic-bezier(.22, 1, .36, 1), opacity 150ms;
}
.ex-glass-composer__slider.ready { opacity: 1; }
.ex-glass-composer__modules button {
  position: relative;
  z-index: 1;
  min-height: 32px;
  padding: 0 11px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--muted);
  font: inherit;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
  transition: color 180ms;
}
.ex-glass-composer__modules button.active { color: var(--explore-ink); }
.ex-glass-composer__modules button[data-module="event"].active { color: #205fb3; }
.ex-glass-composer__modules button[data-module="relation"].active { color: var(--accent); }
.ex-glass-composer__modules button[data-module="history"].active { color: #995d28; }
.ex-glass-composer__modules button:disabled { cursor: default; }
.ex-glass-composer__actions { display: flex; align-items: center; gap: 9px; min-width: 0; }
.ex-glass-composer__files {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 37px;
  max-width: 205px;
  padding: 0 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius-small);
  background: var(--panel);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  font-weight: 650;
  white-space: nowrap;
  cursor: pointer;
  transition: background 160ms, border-color 160ms, transform 160ms;
}
.ex-glass-composer__files:hover:not(:disabled), .ex-glass-composer__files:focus-visible {
  border-color: var(--accent);
  background: var(--explore-wash);
}
.ex-glass-composer__files:disabled { opacity: .55; cursor: default; }
.ex-glass-composer__files-short { display: none; }
.ex-glass-composer__files small { overflow: hidden; text-overflow: ellipsis; color: var(--muted); font-size: 12px; font-weight: 500; }
.ex-glass-composer__submit {
  display: grid;
  place-items: center;
  flex: none;
  width: 39px;
  height: 39px;
  border: 1px solid var(--accent);
  border-radius: var(--radius-small);
  background: var(--accent);
  color: #fff;
  box-shadow: 0 4px 12px rgb(8 123 128 / 16%);
  cursor: pointer;
  transition: background 160ms, transform 130ms, box-shadow 160ms;
}
.ex-glass-composer__submit svg { width: 19px; height: 19px; transition: transform 160ms; }
.ex-glass-composer__submit:hover:not(:disabled) { background: var(--explore-ink); box-shadow: 0 5px 15px rgb(8 123 128 / 22%); }
.ex-glass-composer__submit:hover:not(:disabled) svg, .ex-glass-composer__submit:focus-visible svg { transform: translateY(-2px); }
.ex-glass-composer__submit:active:not(:disabled) { transform: scale(.94); }
.ex-glass-composer__submit:disabled { border-color: var(--line); background: #a6b7bb; color: #fff; box-shadow: none; cursor: default; }
.ex-composer-source-enter-active, .ex-composer-source-leave-active { transition: opacity 190ms, transform 190ms; }
.ex-composer-source-enter-from, .ex-composer-source-leave-to { opacity: 0; transform: translateY(-5px); }
.ex-files-count-enter-active, .ex-files-count-leave-active { transition: opacity 190ms, transform 190ms; }
.ex-files-count-enter-from { opacity: 0; transform: translateY(3px); }
.ex-files-count-leave-to { opacity: 0; transform: translateY(-3px); }
.ex-glass-composer button:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: 2px; }
.ex-glass-composer textarea:focus-visible { outline: 0; }
.is-motion-paused .ex-glass-composer__shine::before { animation-play-state: paused; }
.is-motion-paused .ex-glass-composer, .is-motion-paused .ex-glass-composer__slider, .is-motion-paused .ex-glass-composer__modules button, .is-motion-paused .ex-glass-composer__submit, .is-motion-paused .ex-glass-composer__submit svg, .is-motion-paused .ex-glass-composer__files, .is-motion-paused .ex-composer-source-enter-active, .is-motion-paused .ex-composer-source-leave-active, .is-motion-paused .ex-files-count-enter-active, .is-motion-paused .ex-files-count-leave-active { transition-duration: 0ms; }
@keyframes ex-composer-flow { to { transform: translate3d(135%, 0, 0); } }
@media (max-width: 690px) {
  .ex-glass-composer { padding: 9px 10px 10px; border-radius: 19px; }
  .ex-glass-composer__tools { flex-wrap: wrap; }
  .ex-glass-composer__modules { max-width: 100%; }
  .ex-glass-composer__modules button { min-height: 33px; padding: 0 9px; font-size: 13px; }
  .ex-glass-composer__actions { flex: 1 1 100%; justify-content: space-between; }
  .ex-glass-composer__files { flex: 1 1 auto; min-width: 0; max-width: none; }
  .ex-glass-composer textarea { font-size: 15px; }
}
@media (max-width: 390px) {
  .ex-glass-composer__modules button { padding: 0 7px; }
  .ex-glass-composer__files-full, .ex-glass-composer__files small { display: none; }
  .ex-glass-composer__files-short { display: inline; }
  .ex-glass-composer__source { font-size: 11px; }
}
@media (max-height: 460px) {
  .ex-glass-composer textarea { min-height: 44px; max-height: 20dvh; }
}
@media (prefers-reduced-motion: reduce) {
  .ex-glass-composer__shine::before { animation: none; transform: translate3d(34%, 0, 0); }
  .ex-glass-composer, .ex-glass-composer__slider, .ex-glass-composer__modules button, .ex-glass-composer__submit, .ex-glass-composer__submit svg, .ex-glass-composer__files, .ex-composer-source-enter-active, .ex-composer-source-leave-active, .ex-files-count-enter-active, .ex-files-count-leave-active { transition-duration: 0ms; }
}
</style>
