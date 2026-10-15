<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request as api } from '../api.js';
import { kindLabel } from '../authoringModel.js';
import { authoringCreateRequest } from '../authoringCreateRequest.js';
import { accountDraftKey, readDraft, writeDraft } from '../draftStore.js';
const props = defineProps({ controller: { type: Object, required: true }, ownerId: String, context: { type: Object, required: true }, files: { type: Array, default: () => [] } });
const emit = defineEmits(['close', 'imported', 'started']);
const kind = ref(props.context.kind || 'app'), instruction = ref(props.context.instruction || ''), file = ref(null), busy = ref(false), error = ref(''), imported = ref(null), fileRefs = ref([]), panel = ref(null);
const importing = computed(() => props.context.action === 'import');
let importKey = '', returnFocus, alive = true;
const observation = new AbortController();
onUnmounted(() => { alive = false; observation.abort(); });
const key = () => accountDraftKey(props.ownerId, 'authoring-start', [props.context.intent || 'create', props.context.target?.assetId || '', kind.value].join(':'));
if (!instruction.value) instruction.value = readDraft(key())?.instruction || '';
watch(instruction, value => writeDraft(key(), { instruction: value }));
const title = computed(() => importing.value ? '保存外部原件' : props.context.intent === 'edit' ? '调整这份' + kindLabel(kind.value) : props.context.intent === 'capture' ? '整理为可复用的' + kindLabel(kind.value) : '创建' + kindLabel(kind.value));
const appCreation = computed(() => kind.value === 'app');
function close() { props.controller.close(); emit('close'); nextTick(() => returnFocus?.isConnected && returnFocus.focus()); }
function chooseFile(event) { file.value = event.target.files?.[0] || null; importKey = crypto.randomUUID(); error.value = ''; }
async function importFile() {
  if (!file.value || busy.value) return;
  const owner = props.ownerId;
  busy.value = true; error.value = '';
  const form = new FormData(); form.append('file', file.value); form.append('kind', kind.value);
  try {
    const value = await api('/portable-assets/imports', { method: 'POST', signal: observation.signal, headers: { 'Idempotency-Key': importKey }, body: form });
    if (!alive || owner !== props.ownerId) return;
    imported.value = value; emit('imported', value);
  } catch (cause) { if (alive && owner === props.ownerId && cause.name !== 'AbortError') error.value = cause.message; }
  finally { if (alive && owner === props.ownerId) busy.value = false; }
}
async function start(mode = 'prepare') {
  if (busy.value || props.controller.s.busy) return;
  const owner = props.ownerId;
  const body = authoringCreateRequest({
    kind: kind.value, intent: props.context.intent || 'create', mode, instruction: instruction.value,
    fallbackInstruction: imported.value ? '准备使用这份导入原件，保留原有业务规则。' : props.context.instruction || '按关联来源整理可复用方法，保留业务规则。',
    sourceRefs: props.context.sourceRefs || [], fileIds: fileRefs.value,
    importedId: imported.value?.id || '',
    ...(props.context.conversationId !== undefined ? { conversationId: props.context.conversationId } : {}),
    target: props.context.target,
  });
  const result = await props.controller.create(body);
  if (result && alive && owner === props.ownerId) { writeDraft(key(), { instruction: '' }); emit('started', result, owner); emit('close'); }
}
async function retryStart() {
  const owner = props.ownerId;
  const result = await props.controller.retry();
  if (result && alive && owner === props.ownerId) { emit('started', result, owner); emit('close'); }
}
function keydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); close(); }
  if (event.key !== 'Tab') return;
  const controls = [...(panel.value?.querySelectorAll?.('button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),a[href]') || [])];
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
onMounted(() => { returnFocus = globalThis.document?.activeElement; nextTick(() => panel.value?.focus?.()); });
</script>
<template>
  <div class="author-start-overlay" @click.self="close"><section ref="panel" role="dialog" aria-modal="true" aria-labelledby="author-start-title" class="author-start" tabindex="-1" @keydown="keydown">
    <header><h2 id="author-start-title">{{ title }}</h2><button type="button" aria-label="关闭创作入口" @click="close">×</button></header>
    <p v-if="!importing">{{ appCreation ? '说清你想达到的效果即可。系统整理出完整应用后会先保存到我的 APP，标为未验证；你可以从应用详情修改、验证并启用。' : '说清你想达到的效果即可。系统会整理已有资料，准备示例并验证，最后由你确认保存。' }}</p>
    <p v-else>先完整保存原文件，随时可以导出。准备使用时再检查格式、权限和实际效果。</p>
    <p v-if="error || controller.s.error" role="alert" class="author-start-error">{{ USER_FACING_ERROR }}</p><button v-if="controller.s.pendingRetry" type="button" class="secondary-button" :disabled="controller.s.busy" @click="retryStart">核实并恢复同一次提交</button>
    <template v-if="!imported">
      <label>资产类型<select v-model="kind" :disabled="busy || controller.s.busy || !!context.target || !!context.kind"><option value="app">App · 完整流程</option><option value="tool">Tool · 固定执行能力</option><option value="skill">Skill · 方法与资源</option></select></label>
      <form v-if="importing" @submit.prevent="importFile"><label>原文件或完整资源包<input type="file" :disabled="busy" @change="chooseFile" /></label><small>文件或 ZIP 可保存原件；无需先通过平台运行测试。</small><button class="primary-button" type="submit" :disabled="busy || !file">{{ busy ? '保存原件中…' : '保存原件，不运行' }}</button></form>
    </template>
    <section v-if="imported" class="author-original" role="status"><h3>原件已保存 · 待准备</h3><p>{{ imported.name || imported.originalName }}</p><p>保存原件不代表已适配或可运行。</p><a class="secondary-button" :href="imported.exportUrl" download>导出原件</a><button type="button" class="secondary-button" @click="close">完成</button></section>
    <form v-if="!importing || imported" @submit.prevent="start('prepare')"><label>{{ context.intent === 'edit' ? '希望怎样调整' : '希望它完成什么' }}<textarea v-model="instruction" rows="5" :disabled="controller.s.busy" :placeholder="context.intent === 'capture' ? '可以直接整理当前对话，也可以说明要保留的规则或需要调整的部分。' : '例如：按我提供的规则整理表格，并给出可核对的结果。'" /></label>
      <details v-if="files.length"><summary>补充本次已授权文件</summary><label v-for="item in files" :key="item.id" class="author-file-choice"><input v-model="fileRefs" type="checkbox" :value="item.id" :disabled="controller.s.busy" />{{ item.name }}</label></details>
      <small v-if="context.sourceRefs?.length">已关联来源，会自动整理相关内容；必要的业务选择会在同一草稿中询问。</small>
      <div class="author-start-actions"><button class="primary-button" type="submit" :disabled="controller.s.busy || (!instruction.trim() && !context.sourceRefs?.length && !imported)">{{ appCreation ? '整理并保存未验证 APP' : imported ? '准备使用这份原件' : '开始整理与验证' }}</button><button v-if="!(appCreation && context.hideDraftOnly)" class="secondary-button" type="button" :disabled="controller.s.busy || (!instruction.trim() && !context.sourceRefs?.length && !imported)" @click="start('draft_only')">只存草稿</button></div>
      <small>本轮最多 10 分钟有效时间、32 次模型请求、2 轮技术修复。等待你回答不计时，增加一轮会另请你确认。</small>
    </form>
  </section></div>
</template>
<style scoped>
.author-start-overlay{position:fixed;inset:0;background:#112d3a44;z-index:72;display:flex;align-items:center;justify-content:center;padding:20px}.author-start{width:min(580px,100%);max-height:88vh;overflow:auto;padding:26px;border-radius:14px;background:var(--surface,#fff);color:var(--text,#17374a);box-shadow:0 20px 80px #17374a25;outline:none}.author-start header{display:flex;justify-content:space-between;gap:15px;align-items:center}.author-start h2{font-size:21px;margin:0}.author-start header>button{font-size:26px;border:0;background:none;color:inherit;cursor:pointer}.author-start p{font-size:13px;line-height:1.9;color:var(--muted,#657b89)}.author-start label{display:grid;gap:9px;font-size:13px;margin:20px 0}.author-start textarea,.author-start select,.author-start input[type=file]{font:inherit;color:inherit;border:1px solid var(--line,#d8e3e9);border-radius:8px;background:var(--surface-2,#f8fafb);padding:11px;max-width:100%;box-sizing:border-box}.author-start textarea{width:100%;resize:vertical;line-height:1.8}.author-start small{display:block;color:var(--muted,#657b89);font-size:11px;line-height:1.8;margin:10px 0}.author-start-actions{display:flex;gap:10px;flex-wrap:wrap;margin:20px 0}.author-start details{font-size:12px}.author-start .author-file-choice{display:flex;align-items:center;margin:10px 0;overflow-wrap:anywhere}.author-original{border:1px solid var(--line,#d8e3e9);padding:15px;border-radius:10px;margin:20px 0}.author-original h3{font-size:16px}.author-original a{display:inline-block;margin-right:10px}.author-start-error{color:var(--danger,#9c573e)!important}.author-start :focus-visible{outline:2px solid var(--accent,#34758f);outline-offset:3px}@media(max-width:767px){.author-start-overlay{padding:0}.author-start{width:100%;height:100%;max-height:100%;border-radius:0;box-sizing:border-box}.author-start-actions{flex-direction:column}}
</style>
