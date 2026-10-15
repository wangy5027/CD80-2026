<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, useId, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request } from '../api.js';
import { initialToolValues, parseToolArguments } from '../toolForm.js';

const props = defineProps({
  target: { type: Object, required: true },
});
const emit = defineEmits(['close', 'use-skill']);
const dialog = ref(null);
const closeButton = ref(null);
const executeButton = ref(null);
const resultPre = ref(null);
const titleId = useId();
const tool = ref(null);
const instructions = ref('');
const values = ref({});
const result = ref(null);
const loading = ref(false);
const running = ref(false);
const error = ref('');
const completed = ref(false);
const isTool = computed(() => props.target?.kind === 'tool');
const heading = computed(() => `${isTool.value ? '工具' : 'Skill'} · ${props.target?.name || ''}`);
const fields = computed(() => Object.entries(tool.value?.parameters?.properties || {}));
const required = computed(() => new Set(tool.value?.parameters?.required || []));
const output = computed(() => {
  if (!completed.value) return '';
  try { return JSON.stringify(result.value, null, 2) ?? 'null'; }
  catch { return String(result.value); }
});

let epoch = 0;
let controller = null;
let previousFocus = null;
let disposed = false;
let closing = false;
let skipFocusRestore = false;

function cancelPending() {
  epoch += 1;
  controller?.abort();
  controller = null;
}

watch(() => `${props.target?.kind || ''}:${props.target?.name || ''}`, async () => {
  cancelPending();
  const version = epoch;
  tool.value = null;
  instructions.value = '';
  values.value = {};
  result.value = null;
  completed.value = false;
  running.value = false;
  error.value = '';
  loading.value = true;
  const kind = props.target?.kind;
  const name = props.target?.name;
  if (!['tool', 'skill'].includes(kind) || typeof name !== 'string' || !name) {
    loading.value = false;
    error.value = '技能池项目无效。';
    return;
  }
  const current = new AbortController();
  controller = current;
  try {
    if (kind === 'tool') {
      const data = await request('/tools', { signal: current.signal });
      if (disposed || closing || version !== epoch) return;
      const entry = Array.isArray(data?.tools) ? data.tools.find(item => item?.name === name) : null;
      if (!entry) throw new Error('工具不存在或尚未注册，请刷新技能池列表。');
      tool.value = entry;
      values.value = initialToolValues(entry.parameters);
    } else {
      const data = await request(`/skills/${encodeURIComponent(name)}`, { signal: current.signal });
      if (disposed || closing || version !== epoch) return;
      if (typeof data?.instructions !== 'string') throw new Error('Skill 内容无法读取。');
      instructions.value = data.instructions;
    }
  } catch (cause) {
    if (disposed || closing || version !== epoch || cause?.name === 'AbortError') return;
    error.value = cause.message || '技能池项目加载失败。';
  } finally {
    if (!disposed && !closing && version === epoch) {
      loading.value = false;
      controller = null;
    }
  }
}, { immediate: true });

onMounted(async () => {
  previousFocus = document.activeElement;
  document.addEventListener('keydown', onDocumentKeydown, true);
  await nextTick();
  if (!closing) closeButton.value?.focus();
});

onUnmounted(() => {
  disposed = true;
  document.removeEventListener('keydown', onDocumentKeydown, true);
  cancelPending();
  const label = `${isTool.value ? '调用工具 ' : '查看技能 '}${props.target?.name || ''}`;
  const fallback = [...document.querySelectorAll('button[aria-label]')].find(button => button.getAttribute('aria-label') === label);
  const focus = skipFocusRestore ? null : previousFocus?.isConnected && previousFocus !== document.body ? previousFocus : fallback;
  queueMicrotask(() => { if (focus?.isConnected && !focus.closest('[inert]')) focus.focus(); });
});

function close() {
  if (closing) return;
  closing = true;
  skipFocusRestore = false;
  cancelPending();
  emit('close');
}

function onDocumentKeydown(event) {
  // A disabled submit button may move focus to document.body while the request
  // is pending. Keep Escape and Tab bound to the topmost active dialog.
  if (disposed || closing || !dialog.value?.isConnected) return;
  const dialogs = [...document.querySelectorAll('[role="dialog"][aria-modal="true"]')];
  if (dialogs.at(-1) !== dialog.value) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    event.stopPropagation();
    close();
    return;
  }
  if (event.key !== 'Tab') return;
  const controls = [...(dialog.value?.querySelectorAll('button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex="0"]') || [])];
  if (!controls.length) {
    event.preventDefault();
    dialog.value?.focus();
    return;
  }
  const first = controls[0];
  const last = controls.at(-1);
  if (event.shiftKey && (document.activeElement === first || !dialog.value.contains(document.activeElement))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (document.activeElement === last || !dialog.value.contains(document.activeElement))) {
    event.preventDefault();
    first.focus();
  }
}

async function executeTool() {
  if (loading.value || running.value || !tool.value?.available || closing) return;
  let args;
  try { args = parseToolArguments(tool.value.parameters, values.value); }
  catch (cause) { error.value = cause.message; return; }
  error.value = '';
  completed.value = false;
  result.value = null;
  running.value = true;
  const version = epoch;
  const current = new AbortController();
  controller = current;
  try {
    const data = await request(`/tools/${encodeURIComponent(tool.value.name)}/execute`, {
      method: 'POST', body: { arguments: args }, signal: current.signal,
    });
    if (disposed || closing || version !== epoch) return;
    result.value = data;
    completed.value = true;
  } catch (cause) {
    if (disposed || closing || version !== epoch || cause?.name === 'AbortError') return;
    error.value = cause.message || '工具执行失败，请检查参数后重试。';
  } finally {
    if (!disposed && !closing && version === epoch) {
      running.value = false;
      controller = null;
      await nextTick();
      if (!disposed && !closing && version === epoch && dialog.value && !dialog.value.contains(document.activeElement)) {
        (completed.value ? resultPre.value : executeButton.value || closeButton.value)?.focus();
      }
    }
  }
}

function useSkill() {
  if (loading.value || error.value || !instructions.value || closing) return;
  // The parent moves focus to the conversation composer after selection.
  skipFocusRestore = true;
  emit('use-skill', props.target.name);
}
</script>

<template>
  <Teleport to="body">
    <div class="capability-overlay" @click.self="close">
      <section ref="dialog" class="capability-dialog" role="dialog" aria-modal="true" :aria-labelledby="titleId" tabindex="-1">
        <header class="capability-header">
          <div><small>{{ isTool ? '独立工具' : '技能指令' }}</small><h2 :id="titleId">{{ heading }}</h2></div>
          <button ref="closeButton" type="button" class="capability-close" aria-label="关闭技能池详情" @click="close">×</button>
        </header>
        <div class="capability-content" :aria-busy="loading || running">
          <p v-if="props.target?.description" class="capability-description">{{ props.target.description }}</p>
          <p v-if="loading" class="capability-muted" role="status">正在读取{{ isTool ? '工具参数' : 'Skill 内容' }}…</p>
          <template v-else-if="isTool && tool">
            <p v-if="tool.description && tool.description !== props.target?.description" class="capability-description">{{ tool.description }}</p>
            <p v-if="!tool.available" class="capability-warning" role="status">此工具尚未配置，当前无法执行。请在服务端完成配置后刷新页面。</p>
            <form class="capability-form" @submit.prevent="executeTool">
              <p v-if="!fields.length" class="capability-muted">此工具不需要填写参数。</p>
              <div v-for="([name, field], index) in fields" :key="name" class="capability-field">
                <label :for="`${titleId}-${index}`">{{ field.title || name }}<b v-if="required.has(name)"> *</b><span v-else class="capability-optional">可选</span></label>
                <p v-if="field.description" :id="`${titleId}-${index}-hint`" class="capability-hint">{{ field.description }}</p>
                <input v-if="Object.hasOwn(field, 'const')" :id="`${titleId}-${index}`" :value="typeof field.const === 'string' ? field.const : JSON.stringify(field.const)" readonly :aria-describedby="field.description ? `${titleId}-${index}-hint` : undefined" />
                <select v-else-if="Array.isArray(field.enum)" :id="`${titleId}-${index}`" v-model="values[name]" :disabled="running || !tool.available" :aria-describedby="field.description ? `${titleId}-${index}-hint` : undefined">
                  <option value="">请选择</option><option v-for="(option, optionIndex) in field.enum" :key="optionIndex" :value="JSON.stringify(option)">{{ String(option) }}</option>
                </select>
                <select v-else-if="field.type === 'boolean'" :id="`${titleId}-${index}`" v-model="values[name]" :disabled="running || !tool.available" :aria-describedby="field.description ? `${titleId}-${index}-hint` : undefined"><option value="">请选择</option><option value="true">是</option><option value="false">否</option></select>
                <textarea v-else-if="field.type === 'object' || field.type === 'array'" :id="`${titleId}-${index}`" v-model="values[name]" rows="4" :placeholder="field.type === 'array' ? '[ ]' : '{ }'" :disabled="running || !tool.available" :aria-describedby="field.description ? `${titleId}-${index}-hint` : undefined" />
                <input v-else :id="`${titleId}-${index}`" v-model="values[name]" :type="field.type === 'number' || field.type === 'integer' ? 'number' : 'text'" :step="field.type === 'integer' ? '1' : field.type === 'number' ? 'any' : undefined" :min="field.minimum" :max="field.maximum" :maxlength="field.maxLength" :disabled="running || !tool.available" :aria-describedby="field.description ? `${titleId}-${index}-hint` : undefined" />
              </div>
              <p v-if="error" class="capability-error" role="alert">{{ USER_FACING_ERROR }}</p>
              <div class="capability-actions"><button ref="executeButton" type="submit" class="primary-button" :disabled="running || !tool.available">{{ running ? '执行中…' : '执行工具' }}</button></div>
            </form>
            <section v-if="completed" class="capability-result" aria-label="工具执行结果"><h3>执行结果</h3><pre ref="resultPre" tabindex="0">{{ output }}</pre><p class="capability-muted">可修改参数后再次执行。</p></section>
          </template>
          <template v-else-if="!isTool && instructions">
            <div class="capability-skill-note">Skill 提供任务规则和工具用法，加入对话后由助手按需使用。</div>
            <pre class="capability-instructions" tabindex="0">{{ instructions }}</pre>
            <div class="capability-actions"><button type="button" class="primary-button" @click="useSkill">用于对话</button></div>
          </template>
          <p v-if="error && (!isTool || !tool)" class="capability-error" role="alert">{{ USER_FACING_ERROR }}</p>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.capability-overlay { position:fixed; z-index:40; inset:0; padding:20px; display:grid; place-items:center; background:rgb(8 19 33 / 38%); }
.capability-dialog { width:min(100%,680px); max-height:min(88dvh,880px); display:flex; flex-direction:column; min-width:0; color:var(--text,#081321); background:var(--surface,#fff); border:1px solid var(--line,#e3e9ef); border-radius:var(--radius-panel,24px); box-shadow:0 24px 70px rgb(8 19 33 / 18%); outline:none; }
.capability-header { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; padding:20px 24px 16px; border-bottom:1px solid var(--line,#e3e9ef); }
.capability-header small { color:var(--muted,#596a7e); font-size:12px; }
.capability-header h2 { margin:6px 0 0; font-size:20px; font-weight:650; overflow-wrap:anywhere; }
.capability-close { flex:none; width:40px; height:40px; border:1px solid var(--line,#e3e9ef); border-radius:var(--radius-small,12px); background:var(--surface,#fff); color:inherit; font-size:25px; line-height:1; cursor:pointer; }
.capability-close:hover { background:var(--surface-2,#f2f6f8); }
.capability-content { padding:18px 22px 22px; overflow-y:auto; min-height:0; scrollbar-width:thin; }
.capability-description,.capability-muted,.capability-hint { color:var(--muted,#596a7e); font-size:14px; line-height:1.6; overflow-wrap:anywhere; }
.capability-description { margin:0 0 15px; }
.capability-muted { margin:8px 0; }
.capability-form { display:block; }
.capability-field { margin:0 0 15px; }
.capability-field label { display:block; margin:0 0 7px; font-size:14px; font-weight:600; overflow-wrap:anywhere; }
.capability-field b { color:var(--danger,#b42332); }
.capability-optional { margin-left:8px; color:var(--muted,#596a7e); font-size:12px; font-weight:400; }
.capability-hint { margin:0 0 7px; font-size:13px; }
.capability-field input,.capability-field select,.capability-field textarea { display:block; width:100%; min-width:0; box-sizing:border-box; padding:11px 12px; border:1px solid var(--line,#e3e9ef); border-radius:var(--radius-input,14px); color:var(--text,#081321); background:var(--surface-2,#f2f6f8); font:inherit; font-size:14px; }
.capability-field textarea { min-height:78px; resize:vertical; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.capability-field input:disabled,.capability-field select:disabled,.capability-field textarea:disabled { opacity:.6; }
.capability-actions { display:flex; justify-content:flex-end; margin-top:17px; }
.capability-warning,.capability-error,.capability-skill-note { padding:11px 12px; border-radius:var(--radius-small,12px); font-size:13px; line-height:1.6; overflow-wrap:anywhere; }
.capability-warning { color:var(--warning-ink,#865714); background:var(--warning-wash,#fff7e8); border:1px solid #efddb7; }
.capability-error { color:var(--danger,#b42332); background:var(--error-wash,#fff0f2); border:1px solid #efcbd0; }
.capability-skill-note { color:var(--muted,#596a7e); background:var(--surface-2,#f2f6f8); }
.capability-result { margin-top:19px; border-top:1px solid var(--line,#e3e9ef); }
.capability-result h3 { margin:16px 0 8px; font-size:14px; }
.capability-result pre,.capability-instructions { margin:0; padding:13px; max-height:340px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; font-size:13px; line-height:1.6; color:var(--text,#081321); background:var(--surface-2,#f2f6f8); border:1px solid var(--line,#e3e9ef); border-radius:var(--radius-small,12px); }
.capability-instructions { max-height:min(50vh,440px); margin:13px 0 0; }
.capability-dialog :focus-visible { outline:2px solid var(--focus-ring,#146aa3); outline-offset:2px; }
@media(max-width:600px) { .capability-overlay { padding:8px; align-items:end; } .capability-dialog { max-height:94dvh; border-radius:14px 14px 0 0; } .capability-header { padding:15px 16px 11px; } .capability-content { padding:14px 16px 18px; } }
</style>
