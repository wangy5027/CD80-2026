<script setup>
import { computed, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { waitCapabilityJob } from '../capabilityApi.js';
import { createCapabilityCreateApi, forgetCapabilityImport, pendingCapabilityImport, rememberCapabilityImport } from '../capabilityCreateApi.js';
import { checkOriginalFile, knownImportFailure, normalizeOriginalPreview, originalFilename, originalReadiness, rawOriginalFile } from '../capabilityCreateModel.js';
import SkillPoolEditor from './SkillPoolEditor.vue';
import OriginalTestCaseEditor from './OriginalTestCaseEditor.vue';

const props = defineProps({ ownerId: { type: String, required: true }, kind: { type: String, required: true } });
const emit = defineEmits(['cancel', 'saved']);
const mode = ref('choose'), rawText = ref(''), previewed = ref(null), busy = ref(false), unknown = ref(false);
const originalCaseEditor = ref(null);
const error = ref(''), notice = ref(''), saved = ref(null), jobStatus = ref('');
const childLocked = ref(false), locked = computed(() => busy.value || unknown.value || childLocked.value);
let controller = new AbortController(), api = createCapabilityCreateApi(props.ownerId, controller.signal), generation = 0;
let pending = null;
const active = (owner, kind, turn) => !controller.signal.aborted && owner === props.ownerId && kind === props.kind && turn === generation;

function reset() {
  controller.abort(); controller = new AbortController(); api = createCapabilityCreateApi(props.ownerId, controller.signal); generation++;
  mode.value = 'choose'; rawText.value = ''; previewed.value = null; busy.value = false; unknown.value = false; childLocked.value = false;
  error.value = ''; notice.value = ''; saved.value = null; jobStatus.value = '';
  pending = pendingCapabilityImport(props.ownerId, props.kind);
  if (pending) {
    mode.value = pending.mode; rawText.value = pending.rawText || ''; previewed.value = pending.previewed;
    unknown.value = true; notice.value = '已找回此账号上次未确认的导入。请核实原提交，系统会沿用同一文件和请求编号。';
  }
}
reset();
watch([() => props.ownerId, () => props.kind], reset);
onUnmounted(() => { generation++; controller.abort(); });
watch(rawText, value => {
  if (!unknown.value && mode.value === 'raw' && previewed.value?.rawText !== value) previewed.value = null;
});

function choose(next) {
  if (locked.value) return;
  mode.value = next; previewed.value = null; error.value = ''; notice.value = '';
}
function forwardSaved(value) {
  if (value?.kind === props.kind && value.assetId && value.versionId) { saved.value = value; emit('saved', value); }
}
function cancelForm() { if (!childLocked.value) emit('cancel'); }
function finish(result) {
  if (!result?.assetId || !result?.versionId) throw new Error('保存结果缺少能力版本，请核实原提交。');
  forgetCapabilityImport(props.ownerId, props.kind); pending = null; unknown.value = false;
  saved.value = { kind: props.kind, assetId: result.assetId, versionId: result.versionId };
  emit('saved', saved.value);
}
async function inspect(file, sourceMode, exactText = '') {
  if (busy.value || unknown.value) return;
  const owner = props.ownerId, kind = props.kind, turn = generation;
  busy.value = true; error.value = ''; notice.value = ''; previewed.value = null;
  try {
    checkOriginalFile(kind, file);
    const result = normalizeOriginalPreview(await api.preview(file, kind), kind);
    if (!active(owner, kind, turn)) return;
    previewed.value = { file, result, sourceMode, rawText: exactText };
    notice.value = '只读检查已完成。保存会保留同一份原件，但不会执行、验证或启用。';
  } catch (cause) { if (active(owner, kind, turn) && cause.name !== 'AbortError') error.value = cause.message; }
  finally { if (active(owner, kind, turn)) busy.value = false; }
}
function inspectRaw() {
  try { inspect(rawOriginalFile(props.kind, rawText.value), 'raw', rawText.value); }
  catch (cause) { error.value = cause.message; }
}
function inspectUpload(event) {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file) return;
  inspect(file, 'upload');
}
async function resolveTerminal(owner, kind, turn, status) {
  if (!pending || !['failed', 'cancelled', 'interrupted'].includes(status)) return false;
  if (!pending.importId) {
    forgetCapabilityImport(owner, kind); pending = null; unknown.value = false;
    return false;
  }
  let record;
  try { record = await api.importRecord(pending.importId); }
  catch { if (active(owner, kind, turn)) unknown.value = true; return false; }
  if (!active(owner, kind, turn)) return false;
  if (record?.assetId && record?.versionId) { finish(record); return true; }
  if (record?.status === 'failed') { forgetCapabilityImport(owner, kind); pending = null; unknown.value = false; }
  else unknown.value = true;
  return false;
}
async function saveOriginal() {
  if (busy.value || saved.value) return;
  const owner = props.ownerId, kind = props.kind, turn = generation;
  busy.value = true; error.value = ''; notice.value = ''; jobStatus.value = '';
  let terminal = '';
  try {
    if (!pending) {
      if (!previewed.value?.result?.sha256) throw new Error('请先预览当前原件。');
      if (mode.value === 'raw' && previewed.value.rawText !== rawText.value) throw new Error('原文已改变，请重新预览。');
      const editedCase = originalCaseEditor.value?.snapshot();
      pending = { mode: mode.value, rawText: rawText.value, previewed: previewed.value,
        testCases: editedCase ? [editedCase, ...(previewed.value.result.testCases || []).slice(1)] : null,
        key: crypto.randomUUID(), jobId: '', importId: '' };
      rememberCapabilityImport(owner, kind, pending);
    }
    let job;
    if (pending.jobId) job = await api.job(pending.jobId);
    else {
      job = await api.start(pending.previewed.file, pending.previewed.result.sha256, pending.key, pending.testCases);
      if (active(owner, kind, turn)) {
        pending.jobId = job.jobId || ''; pending.importId = job.importId || '';
        rememberCapabilityImport(owner, kind, pending);
      }
    }
    if (!active(owner, kind, turn)) return;
    if (job.assetId && job.versionId) { finish(job); return; }
    if (!job.jobId) throw new Error('导入任务没有返回编号，请核实原提交。');
    const result = await waitCapabilityJob(job, controller.signal, current => {
      if (active(owner, kind, turn)) { jobStatus.value = current.status; terminal = current.status; }
    });
    if (active(owner, kind, turn)) finish(result);
  } catch (cause) {
    if (!active(owner, kind, turn) || cause.name === 'AbortError') return;
    if (await resolveTerminal(owner, kind, turn, terminal)) return;
    if (pending) {
      if (knownImportFailure(cause)) {
        forgetCapabilityImport(owner, kind); pending = null; unknown.value = false;
        if (cause.code === 'IMPORT_PREVIEW_CHANGED') previewed.value = null;
      } else { unknown.value = true; rememberCapabilityImport(owner, kind, pending); }
    }
    error.value = cause.message || '导入结果暂时无法核实，请稍后核实原提交。';
  } finally { if (active(owner, kind, turn)) busy.value = false; }
}
</script>

<template>
  <section class="cap-create" :aria-label="'创建 ' + (kind === 'skill' ? 'Skill' : 'Tool')">
    <header class="create-head"><div><span class="kicker">SKILL POOL</span><h2>创建 {{ kind === 'skill' ? 'Skill' : 'Tool' }}</h2>
      <p>先检查原件的文件清单与兼容性，再保存个人版本。保存不代表验证或启用。</p></div>
      <button type="button" class="secondary" :disabled="locked" @click="emit('cancel')">返回技能池</button></header>
    <div v-if="saved" class="success" role="status"><strong>已保存到我的 {{ kind === 'skill' ? 'Skills' : 'Tools' }}</strong><p>当前版本仍需检查、配置和验证，才能启用。</p>
      <button type="button" @click="emit('cancel')">查看我的 {{ kind === 'skill' ? 'Skills' : 'Tools' }}</button></div>
    <template v-else>
      <nav class="mode-tabs" aria-label="创建方式">
        <button type="button" :class="{active: mode === 'form'}" :disabled="locked" @click="choose('form')">分项填写</button>
        <button type="button" :class="{active: mode === 'raw'}" :disabled="locked" @click="choose('raw')">填写完整原文</button>
        <button type="button" :class="{active: mode === 'upload'}" :disabled="locked" @click="choose('upload')">上传原件 / ZIP</button>
      </nav>
      <div v-if="mode === 'choose'" class="choice-grid">
        <button type="button" @click="choose('form')"><strong>分项填写</strong><span>填写标准定义、执行实现与资源，沿用现有表单。</span></button>
        <button type="button" @click="choose('raw')"><strong>完整原文</strong><span>{{ kind === 'skill' ? '粘贴完整 SKILL.md，不改写 YAML 与正文。' : '粘贴完整 Tool JSON；执行代码可通过 ZIP 一同上传。' }}</span></button>
        <button type="button" @click="choose('upload')"><strong>上传原件</strong><span>{{ kind === 'skill' ? '上传 SKILL.md 或含资源的 ZIP。' : '上传 Tool JSON，或含 tool.json、可选 tool.py 与资源的 ZIP。' }}</span></button>
      </div>
      <div v-else-if="mode === 'form'" class="existing-editor"><SkillPoolEditor :key="ownerId + ':' + kind" :owner-id="ownerId" :kind="kind" @lock-change="childLocked = $event" @cancel="cancelForm" @saved="forwardSaved" /></div>
      <section v-else-if="mode === 'raw'" class="create-card"><h3>完整 {{ originalFilename(kind) }} 原文</h3>
        <p class="hint" v-if="kind === 'skill'">保留原文中的 YAML、注释和 Markdown。要同时加入资源文件，请改用 ZIP 上传。</p>
        <p class="hint" v-else>这里仅录入标准 Tool JSON；如需 Python 执行实现，请使用“分项填写”或上传含 tool.py 的 ZIP。没有实现时可保存为待配置，但不能直接启用。</p>
        <fieldset :disabled="busy || unknown"><label>原文<textarea v-model="rawText" :aria-label="'完整 ' + originalFilename(kind) + ' 原文'" spellcheck="false" rows="20" /></label></fieldset>
        <div class="actions"><button type="button" class="secondary" :disabled="busy || unknown" @click="inspectRaw">预览文件清单与兼容性</button>
          <button type="button" :disabled="busy || !previewed || (!unknown && previewed.rawText !== rawText)" @click="saveOriginal">{{ unknown ? '核实原提交' : '保存原件，不运行' }}</button></div>
      </section>
      <section v-else-if="mode === 'upload'" class="create-card"><h3>上传完整原件</h3>
        <p class="hint" v-if="kind === 'skill'">支持 SKILL.md 或 ZIP（根部或一层目录内含 SKILL.md，可附资源）。原件不超过 20 MiB。</p>
        <p class="hint" v-else>支持 Tool JSON 或 ZIP（根部或一层目录内含 tool.json，可附 tool.py 与资源）。tool.py 属于平台实现，不属于标准 Tool JSON。原件不超过 20 MiB。</p>
        <fieldset :disabled="busy || unknown"><label class="upload">选择文件<input type="file" :accept="kind === 'skill' ? '.md,.zip,text/markdown,application/zip' : '.json,.zip,application/json,application/zip'" aria-label="上传 Skill 或 Tool 原件" @change="inspectUpload" /></label></fieldset>
        <div v-if="previewed" class="actions"><button type="button" :disabled="busy" @click="saveOriginal">{{ unknown ? '核实原提交' : '导入并保存，不运行' }}</button></div>
      </section>
      <section v-if="previewed && mode !== 'form'" class="create-card preview"><h3>文件清单与兼容性 · {{ previewed.result.originalName }}</h3>
        <p><strong>{{ previewed.result.name }}</strong><span v-if="previewed.result.description"> · {{ previewed.result.description }}</span></p>
        <p class="hint">标准格式：{{ previewed.result.manifest?.source?.format || '待核对' }} · 原件 SHA-256：<code>{{ previewed.result.sha256 }}</code></p>
        <p class="readiness">预计状态：{{ originalReadiness(previewed.result.staticReadiness) }} · 内容检查：{{ previewed.result.contentValidity === 'valid' ? '有效' : '需处理' }}</p>
        <p v-if="kind === 'tool' && previewed.result.issues?.some(item => item.code === 'MISSING_IMPLEMENTATION')" class="issue">缺少执行实现：此 Tool 只能先保存，补齐并验证后才能启用。</p>
        <p v-if="previewed.result.issues?.length" class="issue">{{ USER_FACING_ERROR }}</p>
        <p v-if="previewed.result.warnings?.length" class="hint">{{ USER_FACING_ERROR }}</p>
        <h4>将保存的文件</h4><ul class="file-list"><li v-for="item in previewed.result.files" :key="item.path"><strong>{{ item.path }}</strong><span>{{ item.size }} 字节 · SHA-256 {{ item.sha256 }}</span></li></ul>
        <details><summary>查看平台清单与执行声明</summary><pre>{{ JSON.stringify(previewed.result.manifest, null, 2) }}</pre></details>
        <OriginalTestCaseEditor :key="previewed.result.sha256" ref="originalCaseEditor" :kind="kind" :initial-case="previewed.result.testCases?.[0] || null" :disabled="busy || unknown" />
      </section>
      <p v-if="unknown" class="unknown" role="alert">{{ USER_FACING_ERROR }}</p>
      <p v-if="error && !unknown" class="error" role="alert">{{ USER_FACING_ERROR }}</p>
      <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    </template>
  </section>
</template>

<style scoped>
.cap-create{max-width:1120px;margin:0 auto;padding:24px 28px 72px;color:#1b303a;overflow-wrap:anywhere}.create-head{display:flex;justify-content:space-between;align-items:start;gap:22px;margin-bottom:25px}.create-head h2{font-size:28px;margin:7px 0}.create-head p{max-width:730px;margin:0;line-height:1.65;color:#607681}.kicker{color:#087f84;font-size:11px;letter-spacing:.15em;font-weight:700}.cap-create button{font:inherit;font-weight:650;background:#087f84;border:1px solid #087f84;color:#fff;border-radius:9px;padding:10px 15px;cursor:pointer}.cap-create button.secondary{background:#fff;color:#087f84;border-color:#bdd7d9}.cap-create button:disabled{opacity:.55;cursor:not-allowed}.mode-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}.mode-tabs button{background:#fff;color:#526d76;border-color:#d4e3e5}.mode-tabs button.active{background:#e7f5f3;color:#076d73;border-color:#9dd0cd}.choice-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.choice-grid button{text-align:left;min-height:140px;padding:18px;background:#fff;border-color:#dce8ea;color:#1b303a;display:flex;flex-direction:column;gap:11px;box-shadow:0 2px 8px rgba(11,62,68,.03)}.choice-grid strong{font-size:17px}.choice-grid span,.hint{font-size:13px;line-height:1.7;color:#607681;font-weight:400}.create-card,.success{background:#fff;border:1px solid #dce8ea;border-radius:16px;padding:22px;margin-bottom:18px}.create-card h3{margin:0 0 12px;font-size:18px}.create-card fieldset{border:0;padding:0;margin:0;min-width:0}.create-card label{display:block;font-size:14px;font-weight:650}.create-card textarea{box-sizing:border-box;display:block;width:100%;min-height:260px;padding:14px;margin-top:9px;border:1px solid #cbdcdf;border-radius:10px;background:#fbfdfd;color:#1b303a;font:12px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;resize:vertical}.actions{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin-top:15px}.preview{margin-top:18px}.preview .readiness{padding:10px 12px;background:#e7f5f3;color:#076d73;border-radius:8px}.issue{padding:10px 12px;background:#fff0ef;color:#923838;border-radius:8px}.file-list{padding:0;list-style:none}.file-list li{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #e4edef;padding:9px 0;font-size:12px}.file-list span{color:#607681}.preview details{margin-top:15px}.preview summary{cursor:pointer;color:#087f84}.preview pre{max-height:420px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#f5f9f9;padding:15px;border-radius:8px}.upload{display:grid;gap:9px;padding:16px;border:1px dashed #a8cfd0;border-radius:10px;background:#f8fcfb}.upload input{font:inherit}.unknown,.error,.notice{padding:13px 16px;border-radius:9px;margin:14px 0;line-height:1.5}.unknown,.notice{background:#eaf7f6;color:#145e65}.error{background:#fff0ef;color:#923838}.success{background:#eaf7f6}.success p{color:#4a6970}.existing-editor{margin:0 -28px}@media(max-width:760px){.cap-create{padding:18px 14px 60px}.create-head{display:block}.create-head button{margin-top:14px}.choice-grid{grid-template-columns:1fr}.file-list li{display:block}.file-list span{display:block;margin-top:5px}.existing-editor{margin:0 -14px}}
</style>
