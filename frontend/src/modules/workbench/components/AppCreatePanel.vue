<script setup>
import { onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request } from '../api.js';
import { createAssetAppsApi } from '../assetAppsApi.js';
import { importPreviewValue, isKnownFailure } from '../appCreateModel.js';
import AssetDefinitionView from './AssetDefinitionView.vue';
import OriginalTestCaseEditor from './OriginalTestCaseEditor.vue';

const props = defineProps({ ownerId: { type: String, required: true }, files: { type: Array, default: () => [] } });
const emit = defineEmits(['cancel', 'saved', 'author']);
const downloads = [
  { name: '下载完整 JSON 示例', file: 'template.json', download: 'app-template.json' },
  { name: '下载 Python 示例', file: 'example.py', download: 'app-example.py' },
  { name: '下载源码 ZIP 示例', file: 'template.zip', download: 'app-template.zip' },
  { name: '下载 APP 格式规范', file: 'schema.json', download: 'app-schema.json' },
  { name: '下载 APP 接口说明', file: 'specification.json', download: 'app-api-specification.json' },
  { name: '下载数据接口资料', file: 'data-interface.zip', download: 'cd80-data-interface.zip' },
];
const mode = ref('choose');
const uploadPreview = ref(null), uploadName = ref('');
const originalCaseEditor = ref(null);
const busy = ref(false), unknown = ref(false), error = ref(''), notice = ref(''), saved = ref(null);
let controller = new AbortController(), api = createAssetAppsApi(props.ownerId, controller.signal), generation = 0, pendingSubmission = null;
const active = (owner, turn) => !controller.signal.aborted && props.ownerId === owner && generation === turn;
function reset() {
  controller.abort(); controller = new AbortController(); api = createAssetAppsApi(props.ownerId, controller.signal); generation++;
  mode.value = 'choose'; uploadPreview.value = null; uploadName.value = ''; pendingSubmission = null;
  busy.value = false; unknown.value = false; error.value = ''; notice.value = ''; saved.value = null;
}
watch(() => props.ownerId, reset);
onUnmounted(() => { generation++; controller.abort(); });
function choose(next) {
  if (busy.value || unknown.value) return;
  mode.value = next; error.value = ''; notice.value = '';
}
function startNaturalLanguage() {
  if (busy.value || unknown.value) return;
  emit('author', { kind: 'app', intent: 'create', hideDraftOnly: true });
}
async function inspectUpload(event) {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file || busy.value || unknown.value) return;
  const owner = props.ownerId, turn = generation;
  error.value = ''; notice.value = ''; uploadPreview.value = null; uploadName.value = '';
  if (!/\.(py|zip|json)$/i.test(file.name)) { error.value = '请选择兼容的 Python、JSON 或 ZIP 文件。'; return; }
  if (file.size > 9 * 1024 * 1024) { error.value = '上传文件不能超过 9 MiB。'; return; }
  busy.value = true;
  try {
    const body = new FormData(); body.append('file', file, file.name);
    const result = await request('/asset-apps/import/file-preview', { method: 'POST', body, signal: controller.signal });
    if (!active(owner, turn)) return;
    uploadPreview.value = importPreviewValue(result); uploadName.value = file.name;
    notice.value = uploadPreview.value.issues.length
      ? '格式已解析；请核对下方运行缺口。可先保存，验证与启用请到应用详情完成。'
      : '格式检查完成。请核对实现后保存；本步不会运行或验证代码。';
  } catch (cause) { if (active(owner, turn) && cause.name !== 'AbortError') error.value = cause.message; }
  finally { if (active(owner, turn)) busy.value = false; }
}
function readySubmission() {
  if (pendingSubmission) return pendingSubmission;
  if (mode.value === 'upload') {
    if (!uploadPreview.value?.package) throw new Error('请先上传并检查兼容的 APP 文件。');
    const originalCase = originalCaseEditor.value?.snapshot();
    const testCases = originalCase ? [originalCase, ...(uploadPreview.value.definition?.testCases || []).slice(1)] : null;
    return { package: JSON.parse(JSON.stringify(uploadPreview.value.package)), testCases };
  }
  throw new Error('请选择创建方式。');
}
async function saveApp() {
  if (busy.value || saved.value) return;
  const owner = props.ownerId, turn = generation;
  error.value = ''; notice.value = ''; busy.value = true;
  try {
    pendingSubmission ||= readySubmission();
    const result = await api.importApp(pendingSubmission.package, {}, pendingSubmission.testCases);
    if (!active(owner, turn)) return;
    unknown.value = false; pendingSubmission = null;
    saved.value = { kind: 'app', appId: result.id || result.appId, versionId: result.versionId };
    emit('saved', saved.value);
  } catch (cause) {
    if (active(owner, turn) && cause.name !== 'AbortError') {
      if (pendingSubmission) {
        if (isKnownFailure(cause)) { pendingSubmission = null; unknown.value = false; }
        else unknown.value = true;
      }
      error.value = cause.message || '保存失败，请保留内容后重试。';
    }
  } finally { if (active(owner, turn)) busy.value = false; }
}
</script>

<template>
  <section class="app-create" aria-label="创建 APP">
    <header class="create-head"><div><span class="kicker">APP CREATOR</span><h2>创建 APP</h2>
      <p>可以导入外部完整实现，也可以描述需求由模型创建。两种方式都会先保存为未验证 APP，之后在详情中修改、验证和启用。</p></div>
      <button class="secondary" type="button" :disabled="busy || unknown" @click="emit('cancel')">返回 Apps</button></header>
    <div v-if="saved" class="success" role="status"><strong>APP 已保存到我的 APP</strong><p>此版本尚未验证和启用。请在我的 APP 中查看完整步骤并准备验证。</p>
      <button type="button" @click="emit('cancel')">查看我的 APP</button></div>
    <template v-else>
      <div v-if="mode === 'choose'" class="choice-grid">
        <button type="button" :disabled="busy || unknown" @click="choose('upload')"><strong>从外部导入</strong><span>上传符合平台标准的 Python、JSON 或 ZIP，检查后保存完整实现。</span></button>
        <button type="button" :disabled="busy || unknown" @click="startNaturalLanguage"><strong>用自然语言创建</strong><span>说明用途、输入和输出，模型生成完整 APP 并先保存为未验证版本。</span></button>
      </div>
      <section v-else-if="mode === 'upload'" class="create-card" aria-label="上传 APP 实现">
        <div class="card-head"><div><h3>导入标准 APP 实现</h3><p class="hint">支持含静态 APP_INTERFACE 和 run(payload) 的 Python 单文件、完整 APP JSON，以及声明了源码和资源路径的 ZIP。文件不超过 9 MiB；不会在此运行代码。连接、修改、验证和启用在保存后的应用详情完成。</p></div><button type="button" class="secondary" :disabled="busy || unknown" @click="choose('choose')">返回创建方式</button></div>
        <div class="download-links" aria-label="APP 开发资料">
          <a v-for="item in downloads" :key="item.file" :href="'/desktop/api/asset-apps/import/' + item.file" :download="item.download">{{ item.name }}</a>
        </div>
        <fieldset :disabled="busy || unknown"><label class="file-label">选择文件<input type="file" accept=".py,.zip,.json,text/x-python,application/json,application/zip" aria-label="上传 APP Python、JSON 或 ZIP" @change="inspectUpload" /></label></fieldset>
        <div v-if="uploadPreview" class="preview"><h3>完整实现预览 · {{ uploadName }}</h3>
          <p class="hint">{{ uploadPreview.definition?.name || '未命名 APP' }} · 来源文件 {{ uploadName }} · 格式已解析，尚未验证运行效果</p>
          <p v-if="uploadPreview.issues?.length" class="issue">{{ USER_FACING_ERROR }}</p>
          <p v-if="uploadPreview.warnings?.length" class="hint">{{ USER_FACING_ERROR }}</p>
          <AssetDefinitionView :definition="uploadPreview.definition" />
          <OriginalTestCaseEditor :key="uploadPreview.package.digest" ref="originalCaseEditor" kind="app" :initial-case="uploadPreview.definition?.testCases?.[0] || null" />
          <div class="actions"><button type="button" :disabled="busy" @click="saveApp">{{ unknown ? '核实原提交' : '导入并保存，不运行' }}</button></div>
        </div>
      </section>
      <div v-if="unknown" class="unknown" role="alert">{{ USER_FACING_ERROR }}</div>
      <div v-if="error && !unknown" class="error" role="alert">{{ USER_FACING_ERROR }}</div>
      <div v-if="notice" class="notice" role="status">{{ notice }}</div>
    </template>
  </section>
</template>

<style scoped>
.app-create{max-width:1120px;margin:0 auto;padding:24px 28px 72px;color:#1b303a;overflow-wrap:anywhere}.create-head{display:flex;justify-content:space-between;gap:22px;align-items:start;margin-bottom:25px}.create-head h2{font-size:28px;margin:7px 0}.create-head p{color:#607681;line-height:1.65;max-width:730px;margin:0}.kicker{font-size:11px;letter-spacing:.15em;color:#087f84;font-weight:700}.app-create button{font:inherit;font-weight:650;background:#087f84;border:1px solid #087f84;color:#fff;border-radius:9px;padding:10px 15px;cursor:pointer}.app-create button.secondary{background:#fff;color:#087f84;border-color:#bdd7d9}.app-create button:disabled{opacity:.55;cursor:not-allowed}.app-create button:not(:disabled):hover{filter:brightness(.95)}.choice-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.choice-grid button{text-align:left;min-height:140px;padding:18px;background:#fff;border-color:#dce8ea;color:#1b303a;display:flex;flex-direction:column;gap:11px;box-shadow:0 2px 8px rgba(11,62,68,.03)}.choice-grid button strong{font-size:17px}.choice-grid button span{font-size:13px;color:#607681;line-height:1.6;font-weight:400}.create-card,.success{background:#fff;border:1px solid #dce8ea;border-radius:16px;padding:22px;margin-bottom:18px}.card-head{display:flex;justify-content:space-between;align-items:start;gap:20px}.create-card h3{margin:0 0 11px;font-size:18px}.create-card p,.hint{color:#607681;font-size:13px;line-height:1.7}.create-card fieldset{border:0;padding:0;margin:0;min-width:0}.create-card label{display:block;font-size:14px;font-weight:650}.create-card input:focus{outline:2px solid #3cabad;outline-offset:1px}.actions{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:10px;margin-top:15px}.preview{border-top:1px solid #e4edef;margin-top:19px;padding-top:19px}.preview h3{margin-bottom:12px}.issue{background:#fff4e9;color:#835622;padding:9px 12px;border-radius:8px;margin:8px 0}.preview .hint{background:#f5f9f9;padding:9px 12px;border-radius:8px;margin:8px 0}.file-label{display:grid;gap:10px;padding:16px;border:1px dashed #a8cfd0;border-radius:10px;background:#f8fcfb}.file-label input{font:inherit}.download-links{display:flex;flex-wrap:wrap;gap:10px 18px;margin:0 0 18px}.download-links a{color:#087f84;font-size:13px;line-height:1.5}.unknown,.error,.notice{padding:13px 16px;border-radius:9px;margin:14px 0;line-height:1.5}.unknown,.notice{background:#eaf7f6;color:#145e65}.error{background:#fff0ef;color:#923838}.success{background:#eaf7f6}.success p{color:#4a6970}@media(max-width:760px){.app-create{padding:18px 14px 60px}.create-head{display:block}.create-head button{margin-top:14px}.choice-grid{grid-template-columns:1fr}.card-head{display:block}.card-head button{margin-bottom:16px}}
</style>
