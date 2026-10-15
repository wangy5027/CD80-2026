<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { capGet, waitCapabilityJob } from '../capabilityApi.js';
import { createAssetAppsApi } from '../assetAppsApi.js';
import { loadPersonalComponents, makeAppDefinition, makeSkillDocument, makeSkillDraft, makeToolDraft,
  parseJsonObject, recommendedBindings } from '../skillPoolModel.js';
import OriginalTestCaseEditor from './OriginalTestCaseEditor.vue';

const props = defineProps({ kind: { type: String, required: true }, ownerId: { type: String, required: true } });
const emit = defineEmits(['cancel', 'saved', 'author', 'lock-change']);
const title = computed(() => ({ app: 'APP', skill: 'Skill', tool: 'Tool' })[props.kind] || '技能');
const exampleTool = JSON.stringify({ name: '', description: '',
  input_schema: { type: 'object', properties: {}, required: [] } }, null, 2);
const skill = ref({ name: '', description: '', body: '', extraFrontmatterText: '{}', resources: [], permissions: { inputFiles: false, outputFiles: false } });
const tool = ref({ definitionText: exampleTool, pythonCode: '', resources: [], permissions: { inputFiles: false, outputFiles: false } });
const originalCaseEditor = ref(null);
const app = ref({ name: '', description: '', inputSchemaText: JSON.stringify({ type: 'object', properties: {}, required: [] }, null, 2), outputsText: '', steps: [] });
const assets = ref([]), loading = ref(false), busy = ref(false), error = ref(''), notice = ref(''), saved = ref(null), jobStatus = ref(''), outcomeUnknown = ref('');
watch([busy, outcomeUnknown], () => emit('lock-change', busy.value || !!outcomeUnknown.value), { immediate: true });
const skillPreview = computed(() => { try { return makeSkillDocument(skill.value); } catch { return ''; } });
const canonical = value => JSON.stringify(value, (_key, item) => item && typeof item === 'object' && !Array.isArray(item)
  ? Object.fromEntries(Object.entries(item).sort(([left], [right]) => left.localeCompare(right))) : item);
const freshDraftState = () => ({ identity: 'personal/' + crypto.randomUUID(), createKey: crypto.randomUUID(), createBody: null,
  draftId: '', revision: 0, currentBody: '', saveKey: crypto.randomUUID(), saveBody: '', pendingContent: null });
let controller = new AbortController(), appApi = createAssetAppsApi(props.ownerId, controller.signal), generation = 0;
let draftState = freshDraftState(), pendingApp = null;
function active(owner, turn) { return !controller.signal.aborted && owner === props.ownerId && turn === generation; }
function reset() {
  controller.abort(); controller = new AbortController(); appApi = createAssetAppsApi(props.ownerId, controller.signal); generation++;
  draftState = freshDraftState(); pendingApp = null; assets.value = []; error.value = ''; notice.value = ''; saved.value = null; jobStatus.value = ''; outcomeUnknown.value = ''; busy.value = false;
  if (props.kind === 'app') loadAssets();
}
watch([() => props.ownerId, () => props.kind], reset);
onMounted(() => { if (props.kind === 'app') loadAssets(); });
onUnmounted(() => { generation++; controller.abort(); });

async function loadAssets() {
  const owner = props.ownerId, turn = generation, signal = controller.signal;
  loading.value = true; error.value = '';
  try { const result = await loadPersonalComponents(capGet, signal); if (active(owner, turn)) assets.value = result; }
  catch (cause) { if (active(owner, turn) && cause.name !== 'AbortError') error.value = cause.message; }
  finally { if (active(owner, turn)) loading.value = false; }
}

function componentFor(step) { return assets.value.find(item => item.assetId === step.assetId)?.versions.find(v => v.versionId === step.versionId); }
function assetFor(step) { return assets.value.find(item => item.assetId === step.assetId); }
function optionStatus(asset, version) {
  if (version.configurationStale) return '配置已变化';
  if (asset.enabled && asset.activeVersionId === version.versionId && ['verified', 'ready'].includes(version.readiness)) return '已启用';
  return ({ verified: '已验证', ready: '免验证 · 可启用', unverified: '未验证', needs_configuration: '待配置', blocked: '不可调用', checking: '检查中', verifying: '验证中', failed: '验证未通过' })[version.readiness] || '待检查';
}
function chooseComponent(step, value) {
  const [assetId, versionId] = value.split('|');
  const asset = assets.value.find(item => item.assetId === assetId), version = asset?.versions.find(item => item.versionId === versionId);
  step.assetId = assetId || ''; step.versionId = versionId || ''; step.kind = asset?.kind || '';
  if (asset && !step.title) step.title = asset.name;
  if (version) {
    const previous = app.value.steps[app.value.steps.indexOf(step) - 1]?.id || '';
    let schema = {};
    try { schema = parseJsonObject(app.value.inputSchemaText, 'APP 输入 Schema'); } catch { /* User can repair schema before save. */ }
    step.bindingsText = JSON.stringify(recommendedBindings({ kind: asset.kind, manifest: version.manifest }, schema, previous), null, 2);
  }
}
function addStep() { const index = app.value.steps.length + 1; app.value.steps.push({ id: 'step' + index + '_' + crypto.randomUUID().slice(0, 4), title: '', assetId: '', versionId: '', kind: '', task: '', bindingsText: '{}' }); }
function moveStep(index, offset) { const other = index + offset; if (other < 0 || other >= app.value.steps.length) return; const steps = app.value.steps; [steps[index], steps[other]] = [steps[other], steps[index]]; }
function removeStep(index) { app.value.steps.splice(index, 1); }
function addResource(list) { list.push({ path: '', text: '' }); }
async function uploadResource(item, file) {
  if (!file) return;
  if (file.size > 1024 * 1024) { error.value = '单个资源文件不能超过 1 MiB。'; return; }
  try {
    const encoded = await new Promise((resolve, reject) => {
      const reader = new FileReader(); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.onerror = reject; reader.readAsDataURL(file);
    });
    if (!item.path) item.path = file.name;
    delete item.text; item.base64 = encoded; error.value = '';
  } catch { error.value = '资源读取失败，请重新选择。'; }
}
function useTextResource(item) { delete item.base64; item.text = ''; }

async function saveCapability() {
  const owner = props.ownerId, turn = generation, signal = controller.signal, state = draftState;
  const body = state.pendingContent || (() => {
    const draft = props.kind === 'skill' ? makeSkillDraft(skill.value, state.identity) : makeToolDraft(tool.value, state.identity);
    const originalCase = originalCaseEditor.value?.snapshot();
    return originalCase ? { ...draft, testCases: [originalCase] } : draft;
  })();
  if (!state.draftId) {
    // An unknown network outcome retries the same body and key; later edits patch that draft.
    state.createBody ||= body;
    state.pendingContent = body;
    let record;
    try {
      record = await capGet('/drafts', { method: 'POST', body: state.createBody, signal,
        headers: { 'Idempotency-Key': state.createKey } });
    } catch (cause) {
      if (active(owner, turn)) {
        if (cause.status >= 400 && cause.status < 500 && cause.code !== 'OUTCOME_UNKNOWN') {
          state.createBody = null; state.pendingContent = null; state.createKey = crypto.randomUUID();
        } else outcomeUnknown.value = 'capability';
      }
      throw cause;
    }
    if (!active(owner, turn)) return;
    state.draftId = record.draftId; state.revision = record.revision; state.currentBody = JSON.stringify(state.createBody);
    outcomeUnknown.value = '';
  }
  const signature = JSON.stringify(body);
  if (signature !== state.currentBody) {
    let record;
    try {
      record = await capGet('/drafts/' + encodeURIComponent(state.draftId), { method: 'PATCH', body, signal,
        headers: { 'If-Match': `"${state.revision}"` } });
    } catch (cause) {
      // If the PATCH response was lost, read the same owner-scoped draft before
      // considering any new write. Never create a second asset to recover.
      let current;
      try { current = await capGet('/drafts/' + encodeURIComponent(state.draftId), { signal }); }
      catch { if (active(owner, turn)) outcomeUnknown.value = 'capability'; throw cause; }
      if (!active(owner, turn)) return;
      const fields = Object.fromEntries(['kind', 'name', 'description', 'manifest', 'files', 'testCases'].map(key => [key, current.content?.[key]]));
      if (canonical(fields) !== canonical(body)) {
        if (!(cause.status >= 400 && cause.status < 500 && cause.code !== 'OUTCOME_UNKNOWN')) outcomeUnknown.value = 'capability';
        else state.pendingContent = null;
        throw cause;
      }
      record = current;
    }
    if (!active(owner, turn)) return;
    state.revision = record.revision; state.currentBody = signature; state.saveBody = ''; state.saveKey = crypto.randomUUID();
  }
  const saveBody = JSON.stringify({ draftRevision: state.revision });
  if (state.saveBody !== saveBody) { state.saveBody = saveBody; state.saveKey = crypto.randomUUID(); }
  let terminal = '';
  state.pendingContent = body;
  let job;
  try {
    job = await capGet('/drafts/' + encodeURIComponent(state.draftId) + '/save', { method: 'POST', body: { draftRevision: state.revision }, signal,
      headers: { 'Idempotency-Key': state.saveKey } });
  } catch (cause) {
    if (active(owner, turn)) {
      if (cause.status >= 400 && cause.status < 500 && cause.code !== 'OUTCOME_UNKNOWN') state.pendingContent = null;
      else outcomeUnknown.value = 'capability';
    }
    throw cause;
  }
  if (!active(owner, turn)) return;
  try {
    const result = await waitCapabilityJob(job, signal, current => { if (active(owner, turn)) { jobStatus.value = current.status; terminal = current.status; } });
    if (!active(owner, turn)) return;
    state.pendingContent = null; outcomeUnknown.value = '';
    saved.value = { kind: props.kind, assetId: result.assetId, versionId: result.versionId };
    emit('saved', saved.value);
  } catch (cause) {
    if (!active(owner, turn)) throw cause;
    if (['failed', 'cancelled', 'interrupted'].includes(terminal)) {
      let record;
      try { record = await capGet('/drafts/' + encodeURIComponent(state.draftId), { signal }); }
      catch { outcomeUnknown.value = 'capability'; throw new Error('保存任务已结束，但暂时无法核实草稿状态。请稍后点击“核实原提交”。'); }
      if (!active(owner, turn)) return;
      if (record.savedVersionId) {
        try {
          const catalog = await loadPersonalComponents(capGet, signal);
          if (!active(owner, turn)) return;
          const asset = catalog.find(item => item.versions.some(version => version.versionId === record.savedVersionId));
          if (!asset) throw new Error('saved version not in catalog');
          state.pendingContent = null; outcomeUnknown.value = '';
          saved.value = { kind: props.kind, assetId: asset.assetId, versionId: record.savedVersionId };
          emit('saved', saved.value);
          return;
        } catch { outcomeUnknown.value = 'capability'; throw new Error('服务器已保存此版本，但暂时无法定位资产。请在技能池核实，避免重复提交。'); }
      }
      state.saveKey = crypto.randomUUID(); state.saveBody = ''; state.pendingContent = null; outcomeUnknown.value = '';
    } else if (cause.name !== 'AbortError') outcomeUnknown.value = 'capability';
    throw cause;
  }
}
async function saveApp() {
  const definition = pendingApp || makeAppDefinition(app.value, assets.value);
  const owner = props.ownerId, turn = generation;
  if (!pendingApp) {
    const preview = await appApi.preview({ definition });
    if (!active(owner, turn)) return;
    if (preview.issues?.length) throw new Error(preview.issues.map(issue => issue.message || issue).join('；'));
  }
  pendingApp = definition;
  try {
    const result = await appApi.save({ definition });
    if (!active(owner, turn)) return;
    pendingApp = null; outcomeUnknown.value = '';
    saved.value = { kind: 'app', appId: result.id || result.appId, versionId: result.versionId };
    emit('saved', saved.value);
  } catch (cause) {
    if (active(owner, turn)) {
      if (cause.status >= 400 && cause.status < 500 && cause.code !== 'OUTCOME_UNKNOWN') { pendingApp = null; outcomeUnknown.value = ''; }
      else outcomeUnknown.value = 'app';
    }
    throw cause;
  }
}
async function submit() {
  if (busy.value || saved.value) return;
  const owner = props.ownerId, turn = generation;
  busy.value = true; error.value = ''; notice.value = '';
  try { if (props.kind === 'app') await saveApp(); else await saveCapability(); }
  catch (cause) { if (cause.name !== 'AbortError' && active(owner, turn)) error.value = cause.message || '保存失败，请保留内容并重试。'; }
  finally { if (active(owner, turn)) busy.value = false; }
}
</script>

<template>
  <section class="skill-pool-editor" aria-label="手动加入技能">
    <div class="pool-header">
      <div><span class="pool-eyebrow">技能池 / 手动加入</span><h2>加入 {{ title }}</h2>
        <p v-if="kind === 'app'">从个人库选择精确的 Skill / Tool 版本，按顺序连成 Pipeline。保存后单独验证，验证通过才可启用。</p>
        <p v-else-if="kind === 'skill'">填写标准 SKILL.md 的名称、用途和正文。保存原件后再到技能池检查、验证和启用。</p>
        <p v-else>填写 Anthropic Tool 的标准 JSON 定义。执行实现与标准定义分开保存；只有定义时会显示“待实现”。</p>
      </div>
      <button class="quiet" type="button" :disabled="kind !== 'app' && (busy || !!outcomeUnknown)" @click="emit('cancel')">返回技能池</button>
    </div>

    <div v-if="saved" class="pool-success" role="status">
      <strong>{{ title }} 已保存到个人库</strong><p>请在技能池查看兼容性与当前状态；完成所需配置和验证后才能启用。</p>
      <button type="button" @click="emit('cancel')">返回技能池</button>
    </div>

    <form v-else @submit.prevent="submit">
      <fieldset class="pool-fields" :disabled="busy || !!outcomeUnknown">
      <template v-if="kind === 'skill'">
        <div class="pool-card"><h3>标准 SKILL.md</h3><div class="pool-grid">
          <label>name <small>仅小写字母、数字、短横线；最多 64 字</small><input v-model="skill.name" maxlength="64" autocomplete="off" placeholder="portfolio-review" /></label>
          <label>description <small>写明做什么、什么时候使用；最多 1024 字</small><textarea v-model="skill.description" maxlength="1024" rows="3" placeholder="在需要复核持仓汇总时使用，按提供的数据核对金额和来源。" /></label>
        </div><label>SKILL.md 正文 <small>写清步骤、输入、输出与边界</small><textarea v-model="skill.body" rows="12" placeholder="# 工作流程\n\n1. 阅读本次提供的数据。\n2. 按顺序检查并给出来源。" /></label>
          <details><summary>额外标准 frontmatter（可选）</summary>
            <p class="hint">填写 JSON 对象，可保留 license、compatibility、metadata、allowed-tools 等字段；不能覆盖 name / description。字段写入 SKILL.md，不会改变下方平台权限。当前平台不支持的字段可能使此版本暂不可调用。</p>
            <label>额外 frontmatter JSON<textarea v-model="skill.extraFrontmatterText" spellcheck="false" rows="5" class="mono" aria-label="额外 Skill frontmatter JSON" /></label>
          </details>
          <details><summary>查看完整 SKILL.md 预览</summary><pre>{{ skillPreview || '请先填写合法的名称、说明和正文。' }}</pre></details>
        </div>
      </template>

      <template v-else-if="kind === 'tool'">
        <div class="pool-card"><h3>Anthropic Tool 定义</h3>
          <p class="hint">直接填写标准 JSON：name、description、input_schema；可选原生字段按原文保留。平台如不支持某字段，会在保存后的兼容性检查中提示。</p>
          <label>tool.json <textarea v-model="tool.definitionText" spellcheck="false" rows="15" class="mono" aria-label="Anthropic Tool JSON 定义" /></label>
          <label>平台执行实现（可选）<small>Python WASI；需定义 def run(payload)。它不属于 Anthropic 的 Tool JSON 定义。</small>
            <textarea v-model="tool.pythonCode" spellcheck="false" rows="10" class="mono" placeholder="def run(payload):\n    return {'result': payload['values']}" /></label>
          <p class="hint">{{ tool.pythonCode.trim() ? '保存后仍需实际验证，才能启用。' : '当前仅有接口定义；保存后会标记待实现，不能直接启用。' }}</p>
        </div>
      </template>

      <template v-else-if="kind === 'app'">
        <div class="pool-card"><h3>APP 信息</h3><div class="pool-grid">
          <label>名称<input v-model="app.name" maxlength="120" placeholder="我的研究流程" /></label>
          <label>用途<input v-model="app.description" placeholder="输入一组数据，依次分析并生成结果" /></label>
        </div><label>APP 输入 Schema <small>JSON Schema 对象；步骤可用 from: input 按字段引用。</small>
          <textarea v-model="app.inputSchemaText" class="mono" spellcheck="false" rows="7" /></label></div>
        <div class="pool-card"><div class="pool-row"><h3>Pipeline 步骤</h3><button type="button" class="quiet" @click="loadAssets" :disabled="loading">{{ loading ? '加载中…' : '刷新个人组件' }}</button></div>
          <p class="hint">选择本人 Skill 或 Tool 的精确版本，保存时系统将冻结组件内容。未验证版本可以组合，APP 仍需自己的验证。</p>
          <p v-if="!loading && !assets.length" class="hint">个人库还没有 Skill / Tool。可返回技能池先加入一个组件。</p>
          <div v-for="(step, index) in app.steps" :key="step.id" class="pool-step">
            <div class="pool-row"><strong>步骤 {{ index + 1 }}</strong><div class="pool-actions">
              <button type="button" class="quiet" :disabled="index === 0" @click="moveStep(index, -1)">上移</button>
              <button type="button" class="quiet" :disabled="index === app.steps.length - 1" @click="moveStep(index, 1)">下移</button>
              <button type="button" class="quiet danger" @click="removeStep(index)">移除</button>
            </div></div>
            <div class="pool-grid"><label>步骤标识 <small>用于引用前序结果</small><input v-model="step.id" /></label>
              <label>步骤标题<input v-model="step.title" placeholder="例如：清洗输入数据" /></label></div>
            <label>选择组件与版本
              <select :value="step.assetId && step.versionId ? step.assetId + '|' + step.versionId : ''" @change="chooseComponent(step, $event.target.value)">
                <option value="">请选择 Skill / Tool</option>
                <optgroup v-for="item in assets" :key="item.assetId" :label="`${item.kind === 'skill' ? 'Skill' : 'Tool'} · ${item.name}`">
                  <option v-for="version in item.versions" :key="version.versionId" :value="item.assetId + '|' + version.versionId">
                    {{ item.name }} · v{{ version.number || '?' }} · {{ optionStatus(item, version) }}
                  </option>
                </optgroup>
              </select>
            </label>
            <p v-if="componentFor(step)" class="hint">当前选择：{{ assetFor(step)?.name }} / {{ optionStatus(assetFor(step), componentFor(step)) }}。保存的是此精确版本的快照。</p>
            <label v-if="step.kind === 'skill'">本步目标 task <small>告诉 Skill 此步要做什么，后续可用真实输入执行。</small><textarea v-model="step.task" rows="3" placeholder="根据本次输入，完成核查并返回结构化结果。" /></label>
            <label>参数连接 inputBindings <small v-if="index">JSON 对象；使用 from: step、stepId 和 path 引用前序步骤。</small><small v-else>JSON 对象；使用 from: input 和 path 引用本次输入字段。</small>
              <textarea v-model="step.bindingsText" spellcheck="false" class="mono" rows="5" /></label>
          </div>
          <button type="button" class="add" @click="addStep">＋ 添加顺序步骤</button>
        </div>
        <div class="pool-card"><h3>最终结果</h3>
          <label>输出连接（可选）<small>留空则把最后一步完整结果作为 result；也可用 from: step / literal 自行指定。</small>
            <textarea v-model="app.outputsText" spellcheck="false" class="mono" rows="5" placeholder='{"result":{"from":"step","stepId":"step1","path":""}}' /></label>
        </div>
      </template>

      <template v-if="kind === 'skill' || kind === 'tool'">
        <div class="pool-card"><OriginalTestCaseEditor ref="originalCaseEditor" :kind="kind" /></div>
        <div class="pool-card"><h3>附加资源（可选）</h3><p class="hint">文本或单个上传文件保存在标准原件目录内；脚本文件不会自动获得执行权限。</p>
          <div v-for="(item, index) in (kind === 'skill' ? skill.resources : tool.resources)" :key="index" class="pool-resource">
            <div class="pool-row"><label>相对路径<input v-model="item.path" placeholder="references/guide.md" /></label>
              <button class="quiet danger" type="button" @click="(kind === 'skill' ? skill.resources : tool.resources).splice(index, 1)">移除</button></div>
            <label v-if="typeof item.text === 'string'">内容<textarea v-model="item.text" rows="5" class="mono" /></label>
            <p v-else class="hint">已上传二进制文件。<button class="quiet" type="button" @click="useTextResource(item)">改为文本</button></p>
            <label class="upload">上传文件（最多 1 MiB）<input type="file" @change="uploadResource(item, $event.target.files?.[0]); $event.target.value = ''" /></label>
          </div>
          <button type="button" class="add" @click="addResource(kind === 'skill' ? skill.resources : tool.resources)">＋ 添加资源</button>
        </div>
        <div class="pool-card"><h3>平台权限</h3><p class="hint">仅控制本站运行权限，不写进标准 SKILL.md 或 Tool JSON。网络与外部写入默认关闭。</p>
          <label class="check"><input v-model="(kind === 'skill' ? skill.permissions : tool.permissions).inputFiles" type="checkbox" />允许读取本次明确授权的输入文件</label>
          <label class="check"><input v-model="(kind === 'skill' ? skill.permissions : tool.permissions).outputFiles" type="checkbox" />允许生成本次输出文件</label>
        </div>
      </template>
      </fieldset>

      <div v-if="outcomeUnknown" class="pool-notice" role="alert">{{ USER_FACING_ERROR }}</div>
      <div v-if="error && !outcomeUnknown" class="pool-error" role="alert">{{ USER_FACING_ERROR }}</div>
      <div v-if="notice" class="pool-notice" role="status">{{ notice }}</div>
      <div class="pool-footer"><button type="button" class="quiet" :disabled="kind !== 'app' && (busy || !!outcomeUnknown)" @click="emit('cancel')">取消</button>
        <button type="submit" :disabled="busy || (kind === 'app' && loading)">{{ busy ? (jobStatus ? '保存中 · ' + jobStatus : '正在保存…') : outcomeUnknown ? '核实原提交' : '保存到个人库' }}</button></div>
    </form>
  </section>
</template>

<style scoped>
.skill-pool-editor { max-width: 1080px; margin: 0 auto; padding: 24px 28px 80px; color: #1b303a; overflow-wrap: anywhere; }
.pool-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 22px; }
.pool-header h2 { margin: 7px 0 8px; font-size: 27px; letter-spacing: -.02em; }
.pool-header p { color: #58707b; margin: 0; line-height: 1.65; max-width: 750px; }
.pool-eyebrow { color: #087f84; font-size: 12px; font-weight: 700; letter-spacing: .14em; }
.pool-card { background: #fff; border: 1px solid #dce7e9; border-radius: 17px; padding: 22px; margin: 0 0 18px; box-shadow: 0 2px 8px rgba(11,62,68,.03); }
.pool-fields { border: 0; margin: 0; padding: 0; min-width: 0; }
.pool-card h3 { margin: 0 0 16px; font-size: 18px; }
.pool-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.pool-card label { display: block; color: #29414d; font-size: 14px; font-weight: 650; margin-bottom: 17px; min-width: 0; }
.pool-card label small { display: block; color: #788e99; font-size: 12px; font-weight: 400; margin: 5px 0 8px; line-height: 1.4; }
.pool-card input:not([type=file]):not([type=checkbox]), .pool-card textarea, .pool-card select { box-sizing: border-box; display: block; width: 100%; margin-top: 8px; padding: 11px 12px; border: 1px solid #cbdcdf; border-radius: 9px; background: #fff; color: #1b303a; font: inherit; font-weight: 400; resize: vertical; }
.pool-card textarea:focus, .pool-card input:focus, .pool-card select:focus { outline: 2px solid #3cabad; outline-offset: 1px; }
.pool-card .mono, .pool-card pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12px; line-height: 1.55; }
.pool-card pre { white-space: pre-wrap; max-height: 420px; overflow: auto; background: #f4f8f8; padding: 13px; border-radius: 9px; }
.pool-card details { color: #087f84; cursor: pointer; }
.pool-card .hint { color: #627984; font-size: 13px; line-height: 1.6; margin: 0 0 15px; }
.pool-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.pool-row h3 { margin-bottom: 0; }.pool-actions { display: flex; gap: 5px; }
.pool-step, .pool-resource { border: 1px solid #e2eaec; border-radius: 12px; padding: 16px; margin: 17px 0; background: #fbfdfd; }
.pool-step .pool-row { margin-bottom: 13px; }.pool-resource .pool-row label { flex: 1; }
.pool-card .check { display: flex; align-items: center; gap: 9px; margin: 8px 0 15px; font-weight: 400; }.pool-card .check input { width: 17px; height: 17px; accent-color: #07888b; }
.pool-resource .upload { color: #087f84; }.pool-resource .upload input { display: block; margin-top: 8px; }
button { border: 1px solid #087f84; background: #087f84; color: #fff; border-radius: 9px; padding: 10px 15px; font: inherit; font-weight: 650; cursor: pointer; }
button:hover:not(:disabled) { filter: brightness(.94); }button:disabled { opacity: .5; cursor: not-allowed; }
button.quiet { background: #fff; color: #087f84; border-color: #c9dfe1; }button.danger { color: #a24545; }
button.add { background: #e8f5f4; color: #087f84; border-color: #bee0dc; }
.pool-footer { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
.pool-error, .pool-notice, .pool-success { padding: 14px 16px; border-radius: 10px; line-height: 1.5; margin: 14px 0; }
.pool-error { color: #923838; background: #fff0f0; border: 1px solid #ecc3c3; }.pool-notice { color: #155f64; background: #eaf7f6; }
.pool-success { background: #eaf7f6; border: 1px solid #b7ddda; }.pool-success p { margin: 8px 0 15px; }
@media (max-width: 700px) { .skill-pool-editor { padding: 18px 14px 60px; }.pool-header { display: block; }.pool-header button { margin-top: 14px; }.pool-grid { grid-template-columns: 1fr; }.pool-actions { flex-wrap: wrap; } }
</style>
