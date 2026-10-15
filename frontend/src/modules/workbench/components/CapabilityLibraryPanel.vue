<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { request } from '../api.js';
import CapabilityField from './CapabilityField.vue';
import CapabilityResult from './CapabilityResult.vue';
import ReleasePublisher from './ReleasePublisher.vue';
import CapabilityValidationSuite from './CapabilityValidationSuite.vue';
import SourceStepPicker from './SourceStepPicker.vue';
import { createAssetAppsApi } from '../assetAppsApi.js';
import { resourceFiles } from '../assetAppsModel.js';
import { createCapabilityDirectoryApi } from '../capabilityDirectoryApi.js';
import { emptyValue, formSupported, fieldErrors, labeledSchema } from '../capabilityForms.js';
import { capGet, capPatch, capPost, capabilityStatus, readinessLabels, waitCapabilityJob } from '../capabilityApi.js';

const props = defineProps({ ownerId: { type: String, default: '' }, files: { type: Array, default: () => [] }, task: Object, streaming: Boolean, initialAssetId: String, initialVersionId: String, initialCapture: Object, unifiedAuthoring: Boolean, embedded: Boolean, poolKind: String, connectionRequest: Number });
const emit = defineEmits(['select', 'community', 'author', 'kind']);
const publishing = ref(false), captureWarnings = ref([]);
const savedCapability = ref(null), savedCapabilitySummary = ref(null), showSavedTests = ref(false);
const capturePreview = ref(null), captureBaseline = ref(''), captureStepIds = ref([]), creatorEditInstruction = ref('');
const captureStep = computed(() => capturePreview.value?.steps?.find(step => step.stepId === capturePreview.value.selectedStepId));
const showDeleted = ref(false);
const kind = ref(props.poolKind || 'skill'), scope = ref('personal'), query = ref('');
const assets = ref([]), selected = ref(null), versions = ref([]), versionId = ref('');
const loading = ref(false), busy = ref(false), error = ref(''), notice = ref('');
const picker = ref(null), importTarget = ref(null), activeJob = ref(null), result = ref(null), verification = ref(null);
const resultFailed = computed(() => verification.value?.status === 'failed' || Boolean(verification.value?.error)
  || ['failed', 'error', 'cancelled'].includes(result.value?.status) || Boolean(result.value?.error));
const validationDiagnostics = computed(() => {
  const rows = verification.value?.cases || (verification.value ? [verification.value] : []);
  return rows.filter(row => row.modelRequests?.length || row.error).map(row => {
    const requests = row.modelRequests || [], last = requests.at(-1);
    const completed = (row.executionEvidence || []).filter(event => event.type === 'tool_completed').length;
    return { label: row.label || '本次验证', completed, requests: requests.length,
      stage: { awaiting_headers: '等待模型响应', streaming: '接收模型响应', generating: '生成回答' }[last?.stage] || '',
      failed: ['failed', 'cancelled'].includes(row.status), error: row.error?.error || '',
      partial: requests.some(request => request.partialOutputAccepted === false && request.outputChars > 0) };
  });
});
const inputText = ref('{}'), expectedText = ref('{}'), prompt = ref(''), criteria = ref('结果仅依据提供的资料，过程正确，未虚构事实。');
const fileIds = ref([]), fileContent = ref(''), editor = ref(null), editorText = ref('');
const connections = ref([]), connectionForm = ref(false), endpoint = ref(''), connectionName = ref(''), identity = ref(''), authMode = ref('none'), credential = ref(''), discovery = ref(null);
const history = ref([]), showHistory = ref(false), nextCursor = ref(null);
const drafts = ref([]), showDrafts = ref(false), generateForm = ref(false), messageIds = ref([]), instruction = ref(''), editingConnection = ref(null);
const localFiles = ref([]);
const availableFiles = computed(() => [...props.files, ...localFiles.value.filter(f => !props.files.some(p => p.id === f.id))]);
const headerName = ref('X-API-Key');
const controller = new AbortController();
const assetApps = createAssetAppsApi(props.ownerId, controller.signal);
const directoryApi = createCapabilityDirectoryApi(props.ownerId, controller.signal);
let generation = 0;
const version = computed(() => versions.value.find(item => item.versionId === versionId.value));
const trustedRelease = computed(() => version.value?.admission?.mode === 'trusted_release' && version.value.admission.validationRequired === false);
const canEnable = computed(() => ['verified', 'ready'].includes(version.value?.readiness) ||
  (trustedRelease.value && ['needs_configuration', 'unverified'].includes(version.value?.readiness) && version.value.configurationStale === true &&
    Array.isArray(version.value.issues) && version.value.issues.length === 0));
const enableHint = computed(() => {
  if (trustedRelease.value && version.value?.configurationStale) return selected.value?.kind === 'skill'
    ? '此前配置状态已过期；启用时服务端会重新检查当前条件，并同时启用所需的官方或公开 Tool。已主动禁用的依赖需先恢复。'
    : '此前配置状态已过期；启用时服务端会重新检查当前配置、权限和依赖。';
  if (version.value?.readiness === 'ready') return selected.value?.kind === 'skill'
    ? '服务端确认的官方或公开精确版本免重复验证；启用时会同时启用所需的官方或公开 Tool。已主动禁用的依赖需先恢复，请核对配置和权限后主动启用。'
    : '服务端确认的官方或公开精确版本免重复验证；请核对配置、权限和依赖后主动启用。';
  return '版本验证有效，可由你主动启用。';
});
const guide = computed(() => version.value?.usageGuide);
const examples = computed(() => version.value?.usageExamples || (guide.value ? [guide.value] : []));
const advanced = ref(false), testMode = ref('example'), checkExpected = ref(false), formErrors = ref([]);
const cases = ref([]), caseName = ref(''), loadedCase = ref(null), baseline = ref('');
const inputSchema = computed(() => labeledSchema(version.value?.manifest.tool?.inputSchema, guide.value?.fieldLabels));
const outputSchema = computed(() => labeledSchema(version.value?.manifest.tool?.outputSchema, guide.value?.fieldLabels));
const inputForm = computed({ get() { try { return JSON.parse(inputText.value); } catch { return {}; } }, set(value) { inputText.value = pretty(value); } });
const expectedForm = computed({ get() { try { return JSON.parse(expectedText.value); } catch { return {}; } }, set(value) { expectedText.value = pretty(value); } });
const activeAssertions = ref([]);
const shouldCheck = computed(() => testMode.value === 'example' || checkExpected.value);
const snapshot = () => JSON.stringify([inputText.value, expectedText.value, activeAssertions.value, prompt.value, criteria.value, fileIds.value]);
function fillExample(example = guide.value) {
  if (!example || busy.value) return false;
  if (baseline.value && snapshot() !== baseline.value && !window.confirm('这会替换当前测试输入。是否继续？')) return false;
  activeAssertions.value = [...(example.assertions || []), ...(example.assertions?.length && 'expected' in example ? [{ path: [], op: 'eq', value: example.expected }] : [])];
  inputText.value = pretty(example.input || {});
  expectedText.value = pretty('expected' in example ? example.expected : emptyValue(outputSchema.value));
  prompt.value = example.prompt || '';
  criteria.value = example.criteria || '';
  fileIds.value = [];
  loadedCase.value = null;
  formErrors.value = [];
  testMode.value = 'example';
  advanced.value = !formSupported(inputSchema.value);
  baseline.value = snapshot();
  notice.value = example.filePaths?.length ? '示例已填入，尚未执行。示例文件可从左侧下载，再上传并授权。' : '示例已填入，尚未执行。请核对后点击开始验证。';
  return true;
}
async function loadCases() {
  const id = selected.value?.assetId;
  if (!id || scope.value !== 'personal') { cases.value = []; return; }
  const all = []; let cursor = '';
  do {
    const data = await capGet(`/test-cases?assetId=${encodeURIComponent(id)}&limit=100${cursor ? '&cursor=' + encodeURIComponent(cursor) : ''}`, { signal: controller.signal });
    all.push(...data.items); cursor = data.nextCursor;
  } while (cursor && selected.value?.assetId === id);
  if (selected.value?.assetId === id) cases.value = all;
}
function useCase(item) {
  if (!fillExample(item)) return;
  loadedCase.value = item;
  testMode.value = 'free'; checkExpected.value = 'expected' in item || !!item.criteria || !!item.assertions?.length;
  fileIds.value = [];
  notice.value = '用例已填入。请核对当前版本参数，并重新选择本次授权文件；旧记录不代表当前验证通过。';
  baseline.value = snapshot();
}
async function saveCase() {
  await perform(async () => {
    const body = invocationBody();
    delete body.configurationId;
    await capPost('/test-cases', { ...body, name: caseName.value, ...(selected.value.kind === 'skill' ? { prompt: prompt.value, criteria: criteria.value } : shouldCheck.value ? (activeAssertions.value.length ? { assertions: activeAssertions.value } : { expected: JSON.parse(expectedText.value) }) : {}) });
    await loadCases(); caseName.value = ''; notice.value = '已保存我的测试用例，尚未运行。';
  });
}
async function deleteCase(item) { await perform(async () => { await capGet('/test-cases/' + item.caseId, { method: 'DELETE' }); await loadCases(); }); }
async function enableAndUse() {
  await perform(async () => {
    selected.value = await capPatch(`/assets/${selected.value.assetId}`, { enabled: true, activeVersionId: versionId.value }, selected.value.revision);
    emit('select', { assetId: selected.value.assetId, versionId: versionId.value, name: selected.value.name, kind: selected.value.kind });
  });
}
function toggleAdvanced() {
  try { JSON.parse(inputText.value); JSON.parse(expectedText.value); advanced.value = !advanced.value; error.value = ''; }
  catch { error.value = 'JSON 格式有误，请修正后切换，输入内容已保留。'; }
}
async function runTest() {
  if (trustedRelease.value) return;
  if (shouldCheck.value && version.value?.admission?.validationRequired !== false && version.value?.validationPolicy === 'original_and_new') { notice.value = '此版本请先在原任务用例区验证；不同输入可按需加入。'; return; }
  if (shouldCheck.value) return verify();
  await perform(async () => {
    verification.value = null; result.value = null;
    const outcome = await job('/trials', { ...invocationBody(), ...(selected.value.kind === 'skill' ? { prompt: prompt.value } : {}) });
    result.value = outcome.result;
    notice.value = '运行成功，尚未确认结果符合预期。自由试用不会启用能力或生成验证通过记录。';
  });
}
const filtered = computed(() => assets.value.filter(item => item.kind === kind.value && (item.name + item.description).toLowerCase().includes(query.value.toLowerCase())));
const canVerify = computed(() => version.value && !['blocked', 'needs_configuration', 'checking'].includes(version.value.readiness) && scope.value === 'personal' && !selected.value?.deletedAt && !version.value.configurationStale && selected.value?.lifecycle !== 'archived');
const pretty = value => JSON.stringify(value, null, 2);
const draftContent = computed(() => { try { return JSON.parse(editorText.value); } catch { return null; } });
const draftFiles = computed(() => resourceFiles({ files: draftContent.value?.files || [] }));
function updateDraftField(field, value) { if (draftContent.value) editorText.value = pretty({ ...draftContent.value, [field]: value }); }
function updateDraftFile(index, text) {
  if (!draftContent.value) return;
  const value = draftContent.value, files = [...(value.files || [])]; files[index] = { path: files[index].path, text };
  editorText.value = pretty({ ...value, files, authoringProvenance: { ...(value.authoringProvenance || {}), source: 'manual_edit', modifiedFiles: [...new Set([...(value.authoringProvenance?.modifiedFiles || []), files[index].path])] } });
  captureWarnings.value = [...new Set([...captureWarnings.value, '文件内容已修改，需重新验证；不再视为原任务实际执行的原件。'])];
}
const inputFilePath = file => '/input/' + file.id + (file.name?.includes('.') ? '.' + file.name.split('.').at(-1) : '');

async function load(more = false) {
  const current = ++generation;
  loading.value = true;
  error.value = '';
  try {
    const data = await capGet(`/assets?scope=${scope.value}&deleted=${showDeleted.value}&limit=100${more && nextCursor.value ? '&cursor=' + encodeURIComponent(nextCursor.value) : ''}`, { signal: controller.signal });
    if (current !== generation) return;
    assets.value = more ? [...assets.value, ...data.items] : data.items;
    nextCursor.value = data.nextCursor;
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
  finally { if (current === generation) loading.value = false; }
}
watch(scope, () => { selected.value = null; versions.value = []; load(); }, { flush: 'sync' });
watch(showDeleted, () => { selected.value = null; load(); });
watch(kind, () => { selected.value = null; versions.value = []; emit('kind', kind.value); }, { flush: 'sync' });
watch(() => props.poolKind, value => { if (['skill', 'tool'].includes(value) && value !== kind.value) kind.value = value; });
watch(() => props.connectionRequest, value => { if (value) loadConnections(); });
watch(versionId, async current => {
  result.value = null; verification.value = null; fileContent.value = ''; fileIds.value = [];
  inputText.value = pretty(emptyValue(inputSchema.value)); expectedText.value = pretty(emptyValue(outputSchema.value)); prompt.value = '';
  activeAssertions.value = [];
  criteria.value = ''; testMode.value = examples.value.length ? 'example' : 'free'; checkExpected.value = false; advanced.value = !formSupported(inputSchema.value); formErrors.value = []; loadedCase.value = null; baseline.value = snapshot();
  try { await loadCases(); } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
  if (versionId.value !== current || controller.signal.aborted) return;
  const saved = version.value?.lastVerificationId;
  if (saved && scope.value === 'personal') {
    try {
      const record = await capGet(`/verifications/${saved}`, { signal: controller.signal });
      if (versionId.value === current) { verification.value = record; result.value = record.result || null; }
    } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
  }
});
async function previewCapture(stepId) {
  await perform(async () => {
      const preview = props.initialCapture.prepared || await assetApps.creator({ sourceRunId: props.initialCapture.runId, kind: props.initialCapture.kind, ...(stepId ? { stepIds: Array.isArray(stepId) ? stepId : [stepId] } : {}) });
      if (controller.signal.aborted) return;
      kind.value = props.initialCapture.kind;
      captureWarnings.value = [...(preview.warnings || []), ...(preview.issues || []).map(item => typeof item === 'string' ? item : item.message || pretty(item))];
      capturePreview.value = preview; savedCapability.value = null;
      captureStepIds.value = preview.selectedStepIds || (preview.selectedStepId ? [preview.selectedStepId] : []);
      if (preview.selectionRequired || !preview.draft) { editor.value = null; notice.value = ''; return; }
      editor.value = { preview: true, content: preview.draft }; editorText.value = pretty(preview.draft); captureBaseline.value = editorText.value;
      notice.value = '已整理实际步骤。审阅后即可保存，测试与启用可以稍后进行。';
  });
}
async function selectSourceSteps(stepIds) {
  if (editor.value && editorText.value !== captureBaseline.value && !window.confirm('重新整理步骤会替换当前未保存草稿，是否继续？')) return;
  await previewCapture(stepIds);
}
function closeSourceSelection() { capturePreview.value = null; captureWarnings.value = []; }
function returnToLibrary() { selected.value = null; savedCapability.value = null; notice.value = ''; }
async function regenerateSelectedCapture() {
  if (editorText.value !== captureBaseline.value && !window.confirm('重新整理步骤会替换当前未保存草稿，是否继续？')) return;
  await previewCapture([...captureStepIds.value]);
}
async function reviseCreatorDraft() {
  if (!draftContent.value || !creatorEditInstruction.value.trim()) return;
  await perform(async () => {
    const preview = await assetApps.creator({ kind: draftContent.value.kind, draft: draftContent.value, instruction: creatorEditInstruction.value.trim() });
    if (controller.signal.aborted) return;
    if (!preview.draft) throw new Error('未返回可审阅草稿，当前编辑已保留。');
    editorText.value = pretty(preview.draft);
    capturePreview.value = preview;
    captureWarnings.value = [...(preview.warnings || []), ...(preview.issues || []).map(item => typeof item === 'string' ? item : item.message || pretty(item))];
    creatorEditInstruction.value = '';
    notice.value = '已生成修改建议。请核对差异与文件，新代码需实际验证后才能启用。';
  });
}
async function runValidationSuite(cases) {
  if (trustedRelease.value) return;
  await perform(async () => {
    const started = await capPost('/validation-suites', { assetId: selected.value.assetId, versionId: versionId.value, configurationId: version.value.configurationId, cases });
    activeJob.value = started;
    let outcome;
    try { outcome = await waitCapabilityJob(started, controller.signal, value => { activeJob.value = value; }); }
    catch (cause) {
      const key = started.verificationId || started.result?.verificationId;
      if (key && !controller.signal.aborted) {
        try { verification.value = await capGet(`/verifications/${key}`, { signal: controller.signal }); await refreshDetail(); }
        catch { /* Keep the original execution failure; observation can be retried. */ }
      }
      throw cause;
    }
    verification.value = await capGet(`/verifications/${outcome.verificationId}`, { signal: controller.signal });
    result.value = null; await refreshDetail();
    notice.value = verification.value.status === 'passed' ? '实际验证通过，可以主动启用。' : verification.value.status === 'awaiting_review' ? 'Skill 用例已执行，请核对实际结果。' : '验证未通过，请检查逐案证据。';
  });
}
async function changeCaptureStep(event) {
  const stepId = event.target.value;
  event.target.value = capturePreview.value.selectedStepId;
  if (busy.value || stepId === capturePreview.value.selectedStepId) return;
  if (editorText.value !== captureBaseline.value && !window.confirm('切换代码步骤会放弃当前草稿的未保存编辑，是否继续？')) return;
  await previewCapture(stepId);
}
onMounted(async () => {
  await load();
  if (props.initialCapture && !controller.signal.aborted) await previewCapture();
  if (!props.initialAssetId || controller.signal.aborted) return;
  try {
    const asset = await capGet('/assets/' + encodeURIComponent(props.initialAssetId), { signal: controller.signal });
    if (controller.signal.aborted) return;
    kind.value = asset.kind;
    await detail(asset, props.initialVersionId);
  } catch (cause) { if (cause.name !== 'AbortError') error.value = cause.message; }
});
onUnmounted(() => { generation += 1; controller.abort(); });

async function detail(asset, requestedVersionId) {
  if (!asset) throw new Error('能力详情尚未同步，请刷新后重试。');
  selected.value = asset;
  savedCapability.value = null; showSavedTests.value = false;
  notice.value = '';
  editor.value = null;
  publishing.value = false;
  error.value = '';
  try {
    const data = await capGet(`/assets/${asset.assetId}/versions?scope=${scope.value}&deleted=${showDeleted.value}&limit=100`, { signal: controller.signal });
    if (selected.value?.assetId !== asset.assetId) return;
    const requested = typeof requestedVersionId === 'string' ? requestedVersionId : '';
    const items = [...data.items];
    if (requested && !items.some(item => item.versionId === requested)) {
      items.push(await capGet(`/assets/${asset.assetId}/versions/${encodeURIComponent(requested)}?scope=${scope.value}`, { signal: controller.signal }));
    }
    if (controller.signal.aborted || selected.value?.assetId !== asset.assetId) return;
    versions.value = items;
    versionId.value = requested || asset.activeVersionId || asset.latestVersionId;

  } catch (cause) { error.value = cause.message; }
}

async function refreshDetail() {
  const id = selected.value?.assetId, oldVersion = versionId.value;
  await load();
  const asset = assets.value.find(item => item.assetId === id);
  if (asset) {
    selected.value = asset;
    const data = await capGet(`/assets/${id}/versions?scope=${scope.value}&deleted=${showDeleted.value}&limit=100`);
    versions.value = data.items;
    versionId.value = versions.value.some(item => item.versionId === oldVersion) ? oldVersion : asset.latestVersionId;
  }
}
async function perform(fn) {
  if (busy.value) return;
  busy.value = true; error.value = ''; notice.value = ''; activeJob.value = null;
  try { await fn(); }
  catch (cause) { if (cause.name !== 'AbortError') { error.value = cause.message; formErrors.value = cause.details?.fields || formErrors.value; } }
  finally { busy.value = false; }
}
async function job(path, body) {
  const started = await capPost(path, body);
  activeJob.value = started;
  return await waitCapabilityJob(started, controller.signal, value => { activeJob.value = value; });
}
function chooseImport(target = null) {
  if (props.unifiedAuthoring) { emit('author', { kind: selected.value?.kind || kind.value, action: 'import', ...(target ? { target: { assetId: target, baseVersionId: versionId.value, baseRevision: selected.value.revision } } : {}) }); return; }
  importTarget.value = target; picker.value?.click();
}
async function importFile(event) {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file) return;
  await perform(async () => {
    const body = new FormData(); body.append('file', file);
    if (importTarget.value) body.append('targetAssetId', importTarget.value);
    const outcome = await job('/imports', body);
    scope.value = 'personal'; await load();
    const asset = assets.value.find(item => item.assetId === outcome.assetId);
    if (asset) { kind.value = asset.kind; await detail(asset); }
    notice.value = outcome.connectionId ? '连接说明已导入；尚未联网，请在连接管理中主动发现工具。' : outcome.deduplicated ? '内容相同，已重新检查原版本。' : '已保存并完成检查；验证通过后才能启用。';
  });
}
async function copyPublic() {
  await perform(async () => {
    const value = await directoryApi.addOfficial(selected.value.assetId, versionId.value);
    scope.value = 'personal'; await load();
    const asset = assets.value.find(item => item.assetId === value.assetId) || await capGet(`/assets/${encodeURIComponent(value.assetId)}`, { signal: controller.signal });
    await detail(asset, value.versionId);
    notice.value = value.alreadyOwned ? '这个精确版本已在我的能力池中。请核对后启用。' : '已保存官方精确版本及依赖，尚未执行。配置、权限和依赖齐全时，可直接启用。';
  });
}
async function prepareOfficial() {
  await perform(async () => {
    await job('/official-preparations', { assetId: selected.value.assetId, versionId: versionId.value, scope: 'personal' });
    await refreshDetail();
    notice.value = '官方依赖已准备完成，无需配置MCP或数据源。现在可以测试效果。';
  });
}
async function updateOfficial() {
  if (!window.confirm('更新到当前官方最新版？旧版本和个人修改会保留。当前能力将停用，新版会重新检查准入和配置条件，不会自动启用。')) return;
  await perform(async () => {
    const started = await capPost(`/assets/${selected.value.assetId}/official-update`, { versionId: selected.value.officialUpdate.versionId }, { headers: { 'If-Match': `"${selected.value.revision}"` } });
    const outcome = await waitCapabilityJob(started, controller.signal);
    await refreshDetail(); versionId.value = outcome.versionId; verification.value = null; result.value = null; notice.value = '已保存新的官方版本。请核对精确版本的准入、配置和依赖状态，再主动启用。';
  });
}
async function recheck() {
  await perform(async () => {
    if (selected.value.provenance?.publicAssetId && version.value.manifest.dependencies.length) {
      await job('/official-preparations', { assetId: selected.value.assetId, versionId: versionId.value, scope: 'personal' });
    } else {
      await capPost(`/assets/${selected.value.assetId}/recheck`, { versionId: versionId.value });
    }
    await refreshDetail(); verification.value = null; result.value = null;
    notice.value = '检查已刷新。请按此精确版本的准入状态决定是否需要重新验证。';
  });
}
async function removePersonal() {
  if (!window.confirm('从个人能力池删除此能力？删除后立即停用，旧对话也无法继续调用；历史记录保留，可在“已删除”中恢复。')) return;
  await perform(async () => {
    await capGet(`/assets/${selected.value.assetId}`, { method: 'DELETE', headers: { 'If-Match': `"${selected.value.revision}"` } });
    selected.value = null; versions.value = []; await load(); notice.value = '已删除，可在“已删除”中恢复。';
  });
}
async function restorePersonal() {
  await perform(async () => {
    await capPost(`/assets/${selected.value.assetId}/restore`, {}, { headers: { 'If-Match': `"${selected.value.revision}"` } });
    showDeleted.value = false; selected.value = null; await load(); notice.value = '已恢复，尚未启用，请检查并测试。';
  });
}
async function changeAsset(changes) {
  await perform(async () => { selected.value = await capPatch(`/assets/${selected.value.assetId}`, changes, selected.value.revision); await refreshDetail(); });
}
function invocationBody() {
  let input;
  try { input = selected.value.kind === 'skill' ? {} : JSON.parse(inputText.value); } catch { throw new Error('输入 JSON 格式有误，请修正。'); }
  formErrors.value = selected.value.kind === 'tool' ? fieldErrors(inputSchema.value, input) : [];
  if (formErrors.value.length) throw new Error('请检查标出的输入项。');
  return { assetId: selected.value.assetId, versionId: versionId.value, configurationId: version.value.configurationId, input, fileIds: [...fileIds.value] };
}
async function verify() {
  if (trustedRelease.value) return;
  await perform(async () => {
    result.value = null; verification.value = null;
    const body = invocationBody();
    if (selected.value.kind === 'tool' && !activeAssertions.value.length) {
      const errors = fieldErrors(outputSchema.value, JSON.parse(expectedText.value));
      if (errors.length) throw new Error('请检查预期结果：' + errors.map(e => e.message).join(' '));
    }
    body.expectation = selected.value.kind === 'skill' ? { mode: 'manual_review', prompt: prompt.value, criteria: criteria.value } : activeAssertions.value.length ? { mode: 'assertions', assertions: activeAssertions.value } : { mode: 'json_equals', value: JSON.parse(expectedText.value) };
    const started = await capPost('/verifications', body);
    activeJob.value = started;
    let outcome;
    try { outcome = await waitCapabilityJob(started, controller.signal, value => { activeJob.value = value; }); }
    catch (cause) {
      if (cause.name !== 'AbortError' && started.verificationId) {
        verification.value = await capGet(`/verifications/${started.verificationId}`, { signal: controller.signal });
        await refreshDetail();
      }
      throw cause;
    }
    verification.value = await capGet(`/verifications/${outcome.verificationId}`);
    result.value = outcome.result;
    await refreshDetail();
    notice.value = outcome.status === 'passed' ? '样例验证通过。请明确启用此版本。' : outcome.status === 'awaiting_review' ? '测试对话已完成，请核对结果后确认。' : '实际结果与预期不一致，未通过验证。';
  });
}
async function review(decision) {
  await perform(async () => { verification.value = await capPost(`/verifications/${verification.value.verificationId}/review`, { decision }); await refreshDetail(); notice.value = decision === 'accept' ? '已确认验证结果，可以启用此版本。' : '已拒绝此验证结果。'; });
}
async function invoke() { await perform(async () => { result.value = await job('/invocations', invocationBody()); }); }
async function exportAsset(format) { await perform(async () => { result.value = await job('/exports', { assetId: selected.value.assetId, versionId: versionId.value, format }); }); }
async function uploadTestFile(event) {
  const file = event.target.files?.[0]; event.target.value = '';
  if (!file) return;
  await perform(async () => {
    const data = new FormData(); data.append('file', file);
    const uploaded = await request('/files', { method: 'POST', body: data });
    localFiles.value.push(uploaded); fileIds.value = [...new Set([...fileIds.value, uploaded.id])];
    notice.value = '文件已上传并授权本次测试，尚未执行。';
  });
}
async function downloadExampleFile(path) {
  await perform(async () => {
    const response = await fetch(`/desktop/api/capabilities/assets/${selected.value.assetId}/versions/${versionId.value}/files/${path.split('/').map(encodeURIComponent).join('/')}`, { credentials: 'include', signal: controller.signal });
    if (!response.ok) throw new Error('示例文件读取失败，请检查版本。');
    const url = URL.createObjectURL(await response.blob()); const a = document.createElement('a'); a.href = url; a.download = path.split('/').at(-1); a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
async function readFile(path) { await perform(async () => { fileContent.value = await capGet(`/assets/${selected.value.assetId}/versions/${versionId.value}/files/${path.split('/').map(encodeURIComponent).join('/')}`); }); }
async function edit() {
  if (props.unifiedAuthoring) { emit('author', { kind: selected.value.kind, intent: 'edit', target: { assetId: selected.value.assetId, baseVersionId: versionId.value, baseRevision: selected.value.revision } }); return; }
  await perform(async () => {
    editor.value = await capPost('/drafts', { kind: selected.value.kind, name: selected.value.name, description: selected.value.description, baseAssetId: selected.value.assetId, baseVersionId: versionId.value });
    editorText.value = pretty(editor.value.content);
  });
}
async function saveDraft() {
  await perform(async () => {
    const value = JSON.parse(editorText.value);
    const allowed = Object.fromEntries(['kind', 'name', 'description', 'manifest', 'files', 'dependencySnapshots', 'authoringProvenance'].filter(key => key in value).map(key => [key, value[key]]));
    if (editor.value.preview) editor.value = await capPost('/drafts', allowed);
    editor.value = await capPatch(`/drafts/${editor.value.draftId}`, allowed, editor.value.revision);
    const saved = await job(`/drafts/${editor.value.draftId}/save`, { draftRevision: editor.value.revision });
    editor.value = null; scope.value = 'personal'; await load();
    const asset = assets.value.find(item => item.assetId === saved.assetId);
    if (asset) { kind.value = asset.kind; await detail(asset); versionId.value = saved.versionId; }
    capturePreview.value = null; captureWarnings.value = [];
    savedCapability.value = { name: value.name, versionId: saved.versionId }; showSavedTests.value = false;
    notice.value = '新内容已保存为版本，无需现在测试，可以随时回来继续。';
    await nextTick(); savedCapabilitySummary.value?.focus?.({ preventScroll: true }); savedCapabilitySummary.value?.scrollIntoView?.({ block: 'start' });
  });
}
async function loadConnections() { await perform(async () => { connections.value = (await capGet('/connections?limit=100')).items; connectionForm.value = !connectionForm.value; }); }
async function createConnection() {
  await perform(async () => {
    const fields = { name: connectionName.value, endpoint: endpoint.value, authMode: authMode.value, ...(authMode.value === 'api_key' ? { headerName: headerName.value } : {}) };
    const connection = editingConnection.value ? await capPatch(`/connections/${editingConnection.value.connectionId}`, fields, editingConnection.value.revision) : await capPost('/connections', { ...fields, sourceIdentity: identity.value, transport: 'streamable_http' });
    if (credential.value) await capPost(`/connections/${connection.connectionId}/credentials`, { action: 'set', value: credential.value });
    credential.value = ''; editingConnection.value = null; connections.value = (await capGet('/connections?limit=100')).items; notice.value = '连接已保存，尚未访问远端。配置变更后请重新导入并验证相关能力。';
  });
}
function editConnection(connection) { editingConnection.value = connection; connectionName.value = connection.name; endpoint.value = connection.endpoint; identity.value = connection.sourceIdentity; authMode.value = connection.authMode; headerName.value = connection.headerName || 'X-API-Key'; credential.value = ''; }
async function clearCredential(connection) { await perform(async () => { await capPost(`/connections/${connection.connectionId}/credentials`, { action: 'clear' }); connections.value = (await capGet('/connections?limit=100')).items; }); }
async function loadDrafts() { await perform(async () => { drafts.value = (await capGet('/drafts?limit=100')).items; showDrafts.value = !showDrafts.value; }); }
async function openDraft(draft) { if (props.unifiedAuthoring) { emit('author', { kind: draft.kind || kind.value, intent: 'prepare_import', sourceRefs: [{ kind: 'import_draft', reference: { draftId: draft.draftId } }], instruction: '继续整理这份已有草稿，保留业务规则，准备示例并验证。' }); return; } await perform(async () => { editor.value = await capGet(`/drafts/${draft.draftId}`); editorText.value = pretty(editor.value.content); }); }
async function deleteDraft(draft) { await perform(async () => { await capGet(`/drafts/${draft.draftId}`, { method: 'DELETE' }); drafts.value = drafts.value.filter(item => item.draftId !== draft.draftId); if (editor.value?.draftId === draft.draftId) editor.value = null; }); }
async function generateDraft() { await perform(async () => { const value = await job('/drafts/generate', { kind: kind.value, taskId: props.task.id, messageIds: [...messageIds.value], instruction: instruction.value, fileIds: [] }); editor.value = value; editorText.value = pretty(value.content); generateForm.value = false; notice.value = '已生成待审阅草稿。尚未保存、验证或启用。'; }); }
async function newDraft() {
  if (props.unifiedAuthoring) { emit('author', { kind: kind.value, intent: 'create' }); return; }
  await perform(async () => {
    const isSkill = kind.value === 'skill';
    const manifest = { format: 'cd80.capability/1', kind: kind.value, name: '新能力', description: '请填写用途', source: { identity: 'personal/' + crypto.randomUUID(), format: 'platform' }, files: [], dependencies: [], permissions: { network: 'none', externalEffects: 'none', inputFiles: false, outputFiles: false }, ...(isSkill ? { skill: { entry: 'original/SKILL.md' } } : { tool: { inputSchema: { type: 'object', properties: {}, additionalProperties: false }, execution: { type: 'python_wasi', path: 'original/tool.py', callable: 'run' } } }) };
    editor.value = await capPost('/drafts', { kind: kind.value, name: '新能力', description: '请填写用途', manifest, files: [{ path: isSkill ? 'original/SKILL.md' : 'original/tool.py', text: isSkill ? '---\nname: my-skill\ndescription: 请填写用途\n---\n请填写具体的方法与规则。\n' : 'def run(payload):\n    return {}\n' }] });
    editorText.value = pretty(editor.value.content);
  });
}
function captureConversation() { if (props.unifiedAuthoring) emit('author', { kind: kind.value, intent: 'capture', sourceRefs: [{ kind: 'conversation', reference: { conversationId: props.task.id } }] }); else generateForm.value = !generateForm.value; }
async function discover(connection) { await perform(async () => { discovery.value = await job(`/connections/${connection.connectionId}/discover`, {}); }); }
async function importRemote(tool) { await perform(async () => { result.value = await job(`/connections/${discovery.value.connectionId}/tools/import`, { discoveryId: discovery.value.discoveryId, toolNames: [tool.name] }); await load(); notice.value = '定义已导入，是否可调用以检查结果为准。'; }); }
async function loadHistory() { await perform(async () => { history.value = (await capGet('/invocations?limit=100')).items; showHistory.value = !showHistory.value; }); }
async function cancelJob() { try { await capPost(`/jobs/${activeJob.value.jobId}/cancel`, {}); } catch (cause) { error.value = cause.message; } }
</script>

<template>
  <div class="cap-library">
    <header v-if="!selected" class="page-header" :class="{ 'cap-embedded-header': embedded }"><div v-if="!embedded"><span class="cap-eyebrow">能力库</span><h1 class="page-title">Skills & Tools</h1><p class="page-subtitle">保存你的方法与工具，让每次调用有据可查。</p></div><div class="cap-actions"><button class="secondary-button" @click="emit('community')">共创组件</button><button class="secondary-button" :disabled="busy" @click="loadHistory">调用记录</button><button class="secondary-button" :disabled="busy" @click="loadConnections">连接管理</button><button class="primary-button" :disabled="busy" @click="chooseImport()">导入能力</button></div></header>
    <input ref="picker" type="file" accept=".zip,.json,.md" hidden @change="importFile" />
    <div class="cap-content">
      <div v-if="!selected" class="cap-actions" style="margin-bottom:20px"><button v-if="!embedded" class="secondary-button" :disabled="busy" @click="newDraft">新建草稿</button><button class="secondary-button" :disabled="busy" @click="loadDrafts">我的草稿</button><button class="secondary-button" :disabled="busy || !props.task?.messages?.length" @click="captureConversation">从当前对话沉淀</button></div>
      <p v-if="error" class="send-error" role="alert">{{ USER_FACING_ERROR }}</p><p v-if="notice" class="cap-notice" role="status">{{ notice }}</p>
      <div v-if="busy" class="cap-progress" role="status">{{ activeJob ? '正在处理 · ' + activeJob.operation : '正在处理…' }}<button v-if="activeJob && ['queued','running'].includes(activeJob.status)" class="secondary-button" @click="cancelJob">取消任务</button></div>
      <section v-if="showDrafts" class="cap-box"><h2>我的草稿</h2><p v-if="!drafts.length">还没有草稿。</p><div v-for="draft in drafts" :key="draft.draftId" class="cap-row"><span>{{ draft.content.name }} · {{ draft.savedVersionId ? '已保存过版本' : '未保存' }}</span><div class="cap-actions"><button class="secondary-button" :disabled="busy" @click="openDraft(draft)">继续编辑</button><button v-if="!draft.savedVersionId" class="secondary-button" :disabled="busy" @click="deleteDraft(draft)">删除草稿</button></div></div></section>
      <section v-if="generateForm" class="cap-box"><h2>从对话生成 {{ kind === 'skill' ? 'Skill' : 'Tool' }} 草稿</h2><p>仅使用下面勾选的消息，不读取历史附件。生成后需要你审阅并保存。</p><label v-for="(message,index) in props.task?.messages || []" :key="index" class="cap-check"><input v-model="messageIds" type="checkbox" :value="message.id || String(index)" />{{ message.role === 'user' ? '我' : '助手' }}：{{ message.content?.slice(0,160) }}</label><textarea v-model="instruction" rows="3" placeholder="希望沉淀的方法或工具用途" aria-label="沉淀要求" /><button class="primary-button" :disabled="busy || !messageIds.length" @click="generateDraft">生成待审阅草稿</button></section>
      <SourceStepPicker v-if="capturePreview && !editor && (capturePreview.selectionRequired || !capturePreview.draft)" v-model="captureStepIds" :candidates="capturePreview.candidateSteps || []" :source="capturePreview.source || {}" :warnings="captureWarnings" :busy="busy" @submit="selectSourceSteps" @cancel="closeSourceSelection" />
      <section v-if="savedCapability" ref="savedCapabilitySummary" class="cap-saved-confirmation" role="status" tabindex="-1" aria-label="能力已保存"><span aria-hidden="true">✓</span><div><h3>{{ kind === 'tool' ? 'Tool' : 'Skill' }} 已保存</h3><p>「{{ savedCapability.name }}」已在我的库中。无需现在测试，可以随时回来继续。</p><small>保存不会执行或自动启用；启用前仍需完成验证。</small><div class="cap-actions"><button class="primary-button" @click="returnToLibrary">返回我的库</button><button class="secondary-button" @click="showSavedTests = true">现在测试</button></div></div></section>
      <section v-if="editor" class="cap-box cap-draft-editor"><span class="cap-eyebrow">BUILD → EVOLVE</span><h3>审阅{{ draftContent?.kind === 'tool' ? '执行工具' : '方法' }}草稿</h3><div class="cap-save-toolbar"><div><strong>审阅后即可保存</strong><p>保存不会测试或启用，完整源码与资源会一起保留。</p></div><button class="primary-button" :disabled="busy || !draftContent" @click="saveDraft">{{ busy ? '正在保存…' : '审阅并保存' }}</button></div><details v-if="capturePreview?.candidateSteps?.length" class="cap-capture-step"><summary>调整这次保留的步骤</summary><SourceStepPicker v-model="captureStepIds" :candidates="capturePreview.candidateSteps" :source="capturePreview.source || {}" :busy="busy" @submit="selectSourceSteps" @cancel="closeSourceSelection" /></details><p v-if="captureWarnings.length" class="cap-notice">{{ USER_FACING_ERROR }}</p><section v-if="editor.preview && captureStep && !capturePreview?.candidateSteps" class="cap-capture-step"><label>选择要沉淀的成功代码步骤<select :value="capturePreview.selectedStepId" :disabled="busy" @change="changeCaptureStep"><option v-for="step in capturePreview.steps" :key="step.stepId" :value="step.stepId">{{ step.label }} · {{ step.purpose }}</option></select></label><p>仅将选中步骤保存为 Tool。前后的取数和处理步骤不会自动包含；每次使用都需准备下列输入。</p><ul v-if="captureStep.inputs?.length"><li v-for="input in captureStep.inputs" :key="input.parameter"><strong>{{ input.label }}</strong><span>{{ input.parameter }} · {{ input.sourceLabel || input.sourceType }}</span></li></ul><p v-else>该步骤没有记录输入资源，保存前请核对代码与输入定义。</p></section><section v-if="editor.preview && capturePreview?.steps?.length && !capturePreview?.candidateSteps && !captureStep && !initialCapture?.prepared" class="cap-capture-step"><h4>要保留的成功步骤</h4><label v-for="step in capturePreview.steps" :key="step.stepId || step.id" class="cap-check"><input v-model="captureStepIds" type="checkbox" :value="step.stepId || step.id" :disabled="busy" />{{ step.label || step.title || step.purpose || step.stepId || step.id }}</label><button type="button" class="secondary-button" :disabled="busy || !captureStepIds.length" @click="regenerateSelectedCapture">重新整理所选步骤</button><p>固定多步骤可提取为 Tool；动态方法及其资源保留为 Skill。待补连接会明确提示。</p></section><template v-if="draftContent"><details v-if="draftContent.authoringProvenance" class="cap-source-review"><summary>源码与方法的来源记录</summary><p>来源声明用于追溯；新生成或修改的内容不会继承原任务的验证结论。</p><pre>{{ pretty(draftContent.authoringProvenance) }}</pre></details><label>名称<input :value="draftContent.name" maxlength="128" :disabled="busy" @input="updateDraftField('name', $event.target.value)" /></label><label>用途说明<textarea :value="draftContent.description" rows="2" :disabled="busy" @input="updateDraftField('description', $event.target.value)" /></label><details v-for="(file,index) in draftFiles" :key="file.path" :open="index === 0"><summary>{{ file.path }}</summary><textarea v-if="file.displayText !== null" :value="file.displayText" rows="12" spellcheck="false" :aria-label="file.path" :disabled="busy" @input="updateDraftFile(index, $event.target.value)" /><p v-else>此文件保留原格式。</p></details></template><section v-if="draftContent" class="cap-creator-edit"><label>希望怎样调整<textarea v-model="creatorEditInstruction" rows="3" :disabled="busy" placeholder="描述要调整的输入、约束或实现，生成后先审阅" /></label><button type="button" class="secondary-button" :disabled="busy || !creatorEditInstruction.trim()" @click="reviseCreatorDraft">生成修改建议</button><details v-if="capturePreview?.diff?.length"><summary>本次修改差异</summary><pre>{{ pretty(capturePreview.diff) }}</pre></details></section><details :open="!draftContent"><summary>高级：完整定义、依赖与权限</summary><textarea v-model="editorText" :disabled="busy" rows="18" spellcheck="false" aria-label="能力草稿内容" /></details><p v-if="!draftContent" role="alert">{{ USER_FACING_ERROR }}</p><div class="cap-actions"><button class="secondary-button" :disabled="busy || !draftContent" @click="saveDraft">保存此草稿为版本</button><button class="secondary-button" :disabled="busy" @click="editor = null; captureWarnings = []">收起草稿</button></div><small v-if="editor.draftId">草稿编号：{{ editor.draftId }}</small></section>
      <section v-if="connectionForm" class="cap-box"><h2>MCP 连接</h2><p>保存连接不会联网。发现工具后仍须通过检查、验证并启用。</p>
        <div class="cap-form-grid"><label>连接名称<input v-model="connectionName" /></label><label>来源标识<input v-model="identity" :disabled="!!editingConnection" placeholder="org.example/quotes" /></label><label>服务地址<input v-model="endpoint" placeholder="https://…/mcp" /></label><label>认证<select v-model="authMode"><option value="none">无需凭据</option><option value="bearer">访问令牌</option><option value="api_key">API Key</option></select></label><label v-if="authMode === 'api_key'">凭据请求头<input v-model="headerName" /></label><label v-if="authMode !== 'none'">私密令牌<input v-model="credential" type="password" autocomplete="off" /></label></div>
        <button class="primary-button" :disabled="busy" @click="createConnection">保存连接</button>
        <div v-for="connection in connections" :key="connection.connectionId" class="cap-row"><span>{{ connection.name }} · {{ connection.configured ? '已配置' : '待配置凭据' }}</span><div class="cap-actions"><button class="secondary-button" :disabled="busy" @click="editConnection(connection)">编辑配置</button><button v-if="connection.authMode !== 'none'" class="secondary-button" :disabled="busy" @click="clearCredential(connection)">清除凭据</button><button class="secondary-button" :disabled="busy || !connection.configured" @click="discover(connection)">发现工具</button></div></div>
        <div v-if="discovery"><h3>发现的工具</h3><div v-for="tool in discovery.tools" :key="tool.name" class="cap-row"><span>{{ tool.name }}</span><button class="secondary-button" :disabled="busy" @click="importRemote(tool)">导入定义</button></div></div>
      </section>
      <section v-if="showHistory" class="cap-box"><h2>最近调用</h2><p v-if="!history.length">还没有调用记录。</p><details v-for="item in history" :key="item.invocationId"><summary>{{ item.startedAt }} · {{ item.status }} · {{ item.invocationId.slice(-8) }}</summary><pre v-if="!['failed','error'].includes(item.status) && !item.error">{{ pretty(item) }}</pre><p v-else>{{ USER_FACING_ERROR }}</p></details></section>
      <div v-if="!selected" class="cap-toolbar"><div v-if="!embedded" class="cap-tabs" role="tablist" aria-label="能力类型"><button role="tab" :aria-selected="kind === 'skill'" :class="{ active: kind === 'skill' }" @click="kind = 'skill'">Skills <small>方法与资源</small></button><button role="tab" :aria-selected="kind === 'tool'" :class="{ active: kind === 'tool' }" @click="kind = 'tool'">Tools <small>执行能力</small></button></div><div class="cap-actions"><select v-model="scope" aria-label="能力目录"><option value="personal">我的能力</option><option value="public">公共能力</option></select><label v-if="scope === 'personal'"><input v-model="showDeleted" type="checkbox" />已删除</label><input v-model="query" type="search" placeholder="搜索能力" aria-label="搜索能力" /><button class="secondary-button" :disabled="busy || loading" @click="load()">刷新</button></div></div>
      <div class="cap-layout" :class="{ 'has-detail': selected }">
        <div v-if="!selected" class="cap-list"><p v-if="loading" role="status">正在读取能力库…</p><div v-else-if="!filtered.length" class="cap-empty"><span>{{ kind === 'skill' ? 'S' : 'T' }}</span><h2>从一个{{ kind === 'skill' ? '方法' : '工具' }}开始</h2><p>导入自己的能力包，或从公共能力保存个人副本。</p><button class="secondary-button" @click="scope = scope === 'personal' ? 'public' : 'personal'">{{ scope === 'personal' ? '浏览公共能力' : '返回我的能力' }}</button></div>
          <button v-for="asset in filtered" :key="asset.assetId" class="cap-card" :class="{ selected: selected?.assetId === asset.assetId }" @click="detail(asset)"><div class="cap-row"><span class="cap-kind">{{ asset.kind === 'skill' ? 'S' : 'T' }}</span><span class="cap-status" :class="asset.readiness">{{ capabilityStatus(asset) }}</span></div><h2>{{ asset.name }}</h2><p>{{ asset.description }}</p><small>{{ asset.sourceIdentity }}</small><strong v-if="asset.personalCopies?.length">已添加到我的库（内容一致 · {{ asset.personalCopies.length }}份）</strong><strong v-if="asset.officialUpdate?.available && !asset.deletedAt">官方有更新</strong></button>
          <button v-if="nextCursor" class="secondary-button" :disabled="loading" @click="load(true)">加载更多</button>
        </div>
        <article v-if="selected" class="cap-detail" aria-label="能力详情">
          <div class="cap-row"><button class="secondary-button" :disabled="busy" @click="selected = null">← 返回能力列表</button><span class="cap-status">{{ capabilityStatus({ ...selected, readiness: version?.readiness }) }}</span><button v-if="scope === 'personal' && !selected.deletedAt" class="secondary-button" :disabled="busy || version?.readiness !== 'verified'" :title="version?.readiness === 'verified' ? '检查并分享当前版本' : '验证通过后可以发布'" @click="publishing = !publishing">发布到共创</button></div>
          <ReleasePublisher v-if="publishing && version" :key="selected.assetId + versionId" endpoint="/community/capabilities" :payload="{ assetId: selected.assetId, versionId }" :name="selected.name" :description="selected.description" @close="publishing = false" @published="publishing = false; notice = '已发布到共创目录，其他平台账号可以保存个人副本。'" />
          <div class="cap-detail-heading"><span class="cap-eyebrow">{{ selected.kind === 'tool' ? 'TOOL · 执行工具' : 'SKILL · 方法与资源' }}</span><h2>{{ selected.displayName || version?.manifest.name || selected.name }}</h2><p>{{ version?.manifest.description || selected.description }}</p><small>{{ selected.sourceIdentity }}</small></div>
          <div v-if="version" class="cap-detail-grid">
            <section class="cap-explanation" aria-label="能力说明与示例">
              <h3>{{ trustedRelease ? '方法和启用条件' : '从一个示例开始' }}</h3><p class="cap-muted">{{ trustedRelease ? '查看方法、示例和依赖；这个精确版本无需重复验证。' : '先看看它能做什么，再到右侧亲自试一试。' }}</p>
              <details class="cap-examples" open><summary>使用示例 · {{ examples.length }}</summary>
                <section v-for="example in examples" :key="example.id || example.title" class="cap-guide" aria-label="如何使用">
                  <h3>{{ example.title }}</h3><p>{{ example.description }}</p>
                  <template v-if="selected.kind === 'tool'"><strong>输入示例</strong><CapabilityResult :value="example.input" :schema="inputSchema" /><template v-if="example.assertions?.length"><strong>{{ trustedRelease ? '结果核对规则' : '验证规则' }}</strong><p>按版本内声明的字段与业务规则核对实际返回，不固定动态数据全文。</p><details><summary>查看规则</summary><pre>{{ pretty(example.assertions) }}</pre></details></template><template v-else><strong>预期结果</strong><CapabilityResult :value="example.expected" :schema="outputSchema" /></template></template>
                  <template v-else><strong>测试问题</strong><p class="cap-example-text">{{ example.prompt }}</p></template>
                  <p v-if="example.criteria"><strong>验收标准：</strong>{{ example.criteria }}</p>
                  <p v-if="(scope === 'public' || selected.provenance?.publicAssetId) && selected.kind === 'skill' && version.manifest.dependencies.length">依赖工具、连接和数据访问由平台准备。{{ trustedRelease ? '这个精确版本无需重复验证。' : '请按示例核对实际效果。' }}</p><ul v-else-if="example.requirements?.length"><li v-for="item in example.requirements" :key="item">{{ item }}</li></ul>
                  <button v-for="path in example.filePaths || []" :key="path" class="cap-file" :disabled="busy || scope !== 'personal'" @click="downloadExampleFile(path)">下载示例文件：{{ path }}</button>
                  <small>{{ example.source }}</small><button v-if="scope === 'personal' && !trustedRelease" class="secondary-button" :disabled="busy" @click="fillExample(example)">试试这个示例</button>
                </section>
                <p v-if="!examples.length">{{ trustedRelease ? '作者尚未提供示例；请查看方法和依赖后决定是否启用。' : '作者尚未提供示例。你可以根据参数说明自由试用；运行成功不代表验证通过。' }}</p>
              </details>
              <details v-if="scope === 'personal' && !trustedRelease"><summary>我的测试用例 · {{ cases.length }}</summary><p v-if="!cases.length">保存右侧输入，方便以后再次检查。</p><div v-for="item in cases" :key="item.caseId" class="cap-saved-case"><strong>{{ item.name }}</strong><small>{{ item.versionId === versionId ? '当前版本用例' : '其他版本用例，使用前请核对参数' }}</small><div class="cap-actions"><button class="secondary-button" :disabled="busy" @click="useCase(item)">填入用例</button><button class="secondary-button" :disabled="busy" @click="deleteCase(item)">删除用例</button></div></div></details>
              <details><summary>版本与技术信息</summary><label>查看版本<select v-model="versionId" :disabled="busy"><option v-for="item in versions" :key="item.versionId" :value="item.versionId">V{{ item.number }} · {{ readinessLabels[item.readiness] }}{{ item.versionId === selected.activeVersionId ? ' · 当前启用' : '' }}</option></select></label><p class="cap-fingerprint">内容标识 {{ version.contentDigest.slice(0,16) }}</p><p v-if="version.contentValidity">标准内容：{{ version.contentValidity === 'valid' ? '符合格式' : '需修正格式' }} · 运行条件：{{ readinessLabels[version.runtimeReadiness || version.readiness] || version.runtimeReadiness }}</p><details v-if="version.authoringProvenance"><summary>源码与方法的来源记录</summary><p>来源声明与实际验证分别保存；请结合本版本的验证证据判断可用范围。</p><pre>{{ pretty(version.authoringProvenance) }}</pre></details><details><summary>完整清单</summary><pre>{{ pretty(version.manifest) }}</pre></details><details v-if="scope === 'personal'"><summary>原件文件 · {{ version.manifest.files.length }}</summary><button v-for="file in version.manifest.files" :key="file.path" class="cap-file" :disabled="busy" @click="readFile(file.path)">{{ file.path }}</button><pre v-if="fileContent">{{ typeof fileContent === 'string' ? fileContent : pretty(fileContent) }}</pre></details></details>
            </section>
            <section class="cap-operation" :aria-label="trustedRelease ? '管理与启用' : '测试与启用'">
              <template v-if="scope === 'public'"><p v-if="version.personalCopies?.length" class="cap-notice">这个精确版本已在我的能力池中（{{ version.personalCopies.length }}份）。打开个人副本可核对启用条件。</p><h3>添加到我的能力池</h3><p>只保存官方精确版本及其依赖，不执行样例、验证或启用。服务端确认的可信版本在配置与权限齐全时免重复验证，可由你主动启用。</p><button class="primary-button" :disabled="busy" @click="copyPublic">{{ version.owned ? '打开我的副本' : '添加官方版本' }}</button></template>
              <template v-else>
                <template v-if="!savedCapability || showSavedTests"><section class="cap-preparation"><div v-if="selected.officialUpdate?.available && !selected.deletedAt" class="cap-notice"><strong>官方已有新版本</strong><p>确认后更新个人副本，保留旧版本和修改记录；新版按服务端准入与配置状态决定是否需要验证。</p><button :disabled="busy" class="secondary-button" @click="updateOfficial">更新到官方最新版</button></div><div v-if="version.configurationStale && !selected.deletedAt" class="cap-notice"><p>平台运行规则或连接已更新，原验证已失效。这不一定表示Skill内容有新版。</p><button :disabled="busy" class="secondary-button" @click="recheck">重新检查当前条件</button></div><span class="cap-eyebrow">01 · 准备</span><h3>{{ trustedRelease ? version.readiness === 'ready' ? '可直接启用' : '先完成启用条件' : canVerify ? '可以开始测试' : '先完成准备条件' }}</h3><p v-if="trustedRelease" class="cap-muted">这个官方或公开精确版本无需重复验证；启用前仍需满足配置、权限和依赖条件。</p><p v-else-if="canVerify" class="cap-muted">填写输入并运行。测试不会自动启用能力。</p><div v-if="version.issues.length && !selected.deletedAt" class="cap-issues"><p>{{ USER_FACING_ERROR }}</p><template v-if="selected.provenance?.publicAssetId && !selected.deletedAt"><p>官方连接和数据访问由平台提供，无需填写MCP、服务地址或密钥。</p><button class="secondary-button" :disabled="busy" @click="prepareOfficial">准备官方依赖</button></template><template v-else><button v-if="version.issues.some(item => ['MISSING_CONNECTION', 'MISSING_CREDENTIAL'].includes(item.code))" class="secondary-button" :disabled="busy" @click="loadConnections">管理个人连接与凭据</button><button class="secondary-button" :disabled="busy" @click="chooseImport(selected.assetId)">处理后重新导入</button></template></div><p v-if="!selected.deletedAt && !version.issues.length && selected.provenance?.publicAssetId && version.manifest.dependencies.length">官方连接与数据源由平台提供。<button class="secondary-button" :disabled="busy" @click="prepareOfficial">检查官方依赖</button></p><p v-if="selected.lifecycle === 'archived'">已归档，请在更多操作中恢复后{{ trustedRelease ? '启用' : '测试' }}。</p></section>
                <CapabilityValidationSuite v-if="!trustedRelease && version.validationPolicy === 'original_and_new'" :key="ownerId + ':' + selected.assetId + ':' + versionId" :kind="selected.kind" :test-cases="version.testCases || []" :input-schema="inputSchema" :output-schema="outputSchema" :files="availableFiles" :input-files="version.manifest.permissions.inputFiles" :busy="busy" :allowed="canVerify" :verification="verification" @verify="runValidationSuite" @review="review" /><section v-if="!trustedRelease" class="cap-test"><span class="cap-eyebrow">02 · 测试</span><h3>亲自试一试</h3>
                  <fieldset :disabled="busy" class="cap-test-inputs">
                    <div class="cap-test-modes"><label><input v-model="testMode" type="radio" value="example" :disabled="!examples.length" />示例测试</label><label><input v-model="testMode" type="radio" value="free" />自由试用</label></div>
                    <p v-if="testMode === 'example'" class="cap-muted">点击左侧“试试这个示例”填入完整内容，也可修改后验证。</p>
                    <label v-if="testMode === 'free'" class="cap-check"><input v-model="checkExpected" type="checkbox" />{{ selected.kind === 'tool' ? '设定预期结果，重新运行并验证' : '填写验收标准，运行后确认结果' }}</label>
                    <p v-if="loadedCase" class="cap-muted">用例：{{ loadedCase.name }}。请核对版本，并重新授权所需文件。</p>
                    <template v-if="selected.kind === 'skill'"><label>测试问题<textarea v-model="prompt" rows="6" placeholder="粘贴资料并说明任务要求" /></label><label v-if="shouldCheck">验收标准<textarea v-model="criteria" rows="3" placeholder="例如：只依据原文、保留关键事实、不编造缺失信息" /></label></template>
                    <template v-else>
                      <button v-if="formSupported(inputSchema)" type="button" class="cap-file" @click="toggleAdvanced">{{ advanced ? '返回普通表单' : '高级 JSON 模式' }}</button>
                      <p v-if="!formSupported(inputSchema)" class="cap-muted">此工具包含暂不支持普通表单的参数结构，请使用高级 JSON 模式并参考作者说明。</p>
                      <label v-if="advanced">输入参数（JSON）<textarea v-model="inputText" rows="6" spellcheck="false" /></label><CapabilityField v-else v-model="inputForm" :schema="inputSchema" :errors="formErrors" :files="availableFiles.filter(f => fileIds.includes(f.id))" />
                      <p v-if="shouldCheck && activeAssertions.length">将按已加载示例的 {{ activeAssertions.length }} 项规则验证实际结果。修改输入后请同时核对规则。<button type="button" @click="activeAssertions = []">改用精确预期结果</button></p><template v-if="shouldCheck && !activeAssertions.length"><h4>预期结果</h4><CapabilityField v-if="!advanced && formSupported(outputSchema)" v-model="expectedForm" :schema="outputSchema" /><label v-else>预期结果（JSON）<small v-if="!outputSchema">作者未提供输出结构，请参考示例填写。</small><textarea v-model="expectedText" rows="3" /></label></template>
                    </template>
                    <fieldset v-if="version.manifest.permissions.inputFiles"><legend>仅本次授权的文件</legend><label v-for="file in availableFiles" :key="file.id" class="cap-check"><input v-model="fileIds" type="checkbox" :value="file.id" /><span>{{ file.name }}<small>{{ inputFilePath(file) }}</small></span></label><label>上传测试文件<input type="file" @change="uploadTestFile" /></label><p>上传完成后仅本次授权，不自动执行。</p></fieldset>
                  </fieldset>
                  <button class="primary-button" :disabled="busy || !canVerify" @click="runTest">{{ shouldCheck ? '开始验证' : '运行试用' }}</button>
                  <details><summary>保存为我的测试用例</summary><label>用例名称<input v-model="caseName" maxlength="128" placeholder="例如：两项小数求和" /></label><button class="secondary-button" :disabled="busy || !caseName.trim()" @click="saveCase">保存测试用例</button><p>不保存连接密钥；保存不会运行，也不代表通过验证。</p></details>
                </section>
                <section v-if="!trustedRelease && validationDiagnostics.length" class="cap-result" aria-label="验证执行情况" aria-live="polite"><h3>验证执行情况</h3><div v-for="item in validationDiagnostics" :key="item.label"><p>{{ item.label }}：{{ item.completed ? `已完成 ${item.completed} 次工具调用，记录已保留。` : '本次尚未完成工具调用。' }}模型请求 {{ item.requests }} 次<span v-if="item.failed">，未完成</span>。</p><p v-if="item.error">{{ USER_FACING_ERROR }}</p><p v-if="item.partial">未完成的部分回答未作为验证结果，本次需要核对最终状态。</p></div></section>
                <section v-if="result" class="cap-result" aria-live="polite"><span class="cap-eyebrow">03 · 结果</span><h3>{{ trustedRelease ? '操作结果' : verification?.status === 'passed' ? '本次测试通过' : verification?.status === 'failed' ? '与预期不一致' : verification?.status === 'awaiting_review' ? '请检查回答是否符合预期' : '实际运行结果' }}</h3><p v-if="resultFailed" class="send-error" role="alert">{{ USER_FACING_ERROR }}</p><CapabilityResult v-else :value="Object.hasOwn(result, 'content') ? result.content : result" :schema="outputSchema" /><p v-if="!trustedRelease && verification?.status === 'awaiting_review'">验收标准：{{ verification.sample?.expectation?.criteria || criteria }}</p><div v-if="!trustedRelease && verification?.status === 'awaiting_review'" class="cap-actions"><button class="primary-button" :disabled="busy" @click="review('accept')">确认符合预期</button><button class="secondary-button" :disabled="busy" @click="review('reject')">不符合预期</button></div><a v-if="result.downloadUrl" :href="result.downloadUrl" download>下载 {{ result.name }}</a><a v-for="artifact in result.artifacts || []" :key="artifact.id" :href="artifact.downloadUrl" download>{{ artifact.name }}</a><details><summary>技术详情</summary><p v-if="resultFailed">{{ USER_FACING_ERROR }}</p><pre v-else>{{ pretty({ result, verification }) }}</pre></details></section>
                </template><div v-if="canEnable && !selected.deletedAt && selected.lifecycle !== 'archived'" class="cap-ready"><p>{{ enableHint }}</p><button class="primary-button" :disabled="busy || streaming" @click="enableAndUse">启用并用于对话</button><button v-if="selected.enabled && selected.activeVersionId === versionId" class="secondary-button" :disabled="busy || streaming" @click="emit('select', { assetId: selected.assetId, versionId, name: selected.name, kind: selected.kind })">用于对话</button></div>
                <div v-if="scope === 'personal'" class="cap-actions"><button v-if="!selected.deletedAt" class="secondary-button" :disabled="busy" @click="removePersonal">删除此{{ selected.kind === 'skill' ? 'Skill' : 'Tool' }}</button><button v-else class="secondary-button" :disabled="busy" @click="restorePersonal">恢复此能力</button></div><details v-if="!selected.deletedAt"><summary>更多操作</summary><div class="cap-actions"><button class="secondary-button" :disabled="busy || !canEnable || selected.lifecycle === 'archived'" @click="changeAsset({ enabled: true, activeVersionId: versionId })">启用此版本</button><button class="secondary-button" :disabled="busy || !selected.enabled" @click="changeAsset({ enabled: false })">禁用</button><button class="secondary-button" :disabled="busy" @click="chooseImport(selected.assetId)">重新导入</button><button class="secondary-button" :disabled="busy" @click="edit">编辑草稿</button><button class="secondary-button" :disabled="busy" @click="changeAsset({ lifecycle: selected.lifecycle === 'archived' ? 'active' : 'archived' })">{{ selected.lifecycle === 'archived' ? '恢复' : '归档' }}</button><button v-if="selected.kind === 'skill'" class="secondary-button" :disabled="busy" @click="exportAsset('agent_skills')">导出标准 Skill 包</button><button class="secondary-button" :disabled="busy" @click="exportAsset('original')">导出原件</button><button class="secondary-button" :disabled="busy" @click="exportAsset('platform')">导出完整包</button></div><details v-if="selected.kind === 'tool'"><summary>导出平台接口定义</summary><button v-for="format in ['openai_chat','openai_responses','claude_tool']" :key="format" class="secondary-button" :disabled="busy" @click="exportAsset(format)">{{ format }}</button></details></details>
              </template>
            </section>
          </div>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
.cap-embedded-header{min-height:0;padding:0 32px 20px;justify-content:flex-end}
@media(max-width:640px){.cap-embedded-header{padding:0 14px 20px}}
.cap-guide{margin:20px 0;padding:18px;border:1px solid var(--cap-line);border-radius:10px;background:#f8faf5;font-size:13px;line-height:1.7}.cap-guide h3{margin:8px 0}.cap-guide small{display:block;color:#657168}.cap-guide button{margin-top:12px}.cap-guide ol{padding-left:20px}
.cap-library{--cap-ink:#243b32;--cap-soft:#edf3ed;--cap-line:#dce3dc;color:var(--cap-ink)}
.cap-eyebrow{font-size:11px;letter-spacing:.16em;color:#758578}.cap-content{padding:0 32px 36px}.cap-actions,.cap-row,.cap-toolbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.cap-row,.cap-toolbar{justify-content:space-between}.cap-toolbar{border-bottom:1px solid var(--cap-line);padding-bottom:18px;margin-bottom:24px}.cap-tabs{display:flex;gap:8px}.cap-tabs button{background:transparent;border:0;padding:10px 14px;color:#738177;cursor:pointer;font-size:17px;border-radius:8px}.cap-tabs button.active{background:var(--cap-ink);color:#fff}.cap-tabs small{display:block;font-size:11px;margin-top:3px}.cap-layout{display:grid;grid-template-columns:1fr;gap:24px}.cap-layout.has-detail{grid-template-columns:1fr;padding-top:24px}.cap-list{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;align-content:start}.has-detail .cap-list{grid-template-columns:1fr}.cap-card{border:1px solid var(--cap-line);background:#fff;text-align:left;padding:22px;border-radius:12px;cursor:pointer;color:inherit;transition:border-color .15s,transform .15s}.cap-card:hover{border-color:#809789;transform:translateY(-2px)}.cap-card.selected{border:2px solid #416b52;padding:21px;background:#fafcf8}.cap-card h2{font-size:17px;margin:16px 0 8px}.cap-card p,.cap-detail>p{font-size:13px;color:#657168;line-height:1.7}.cap-card small{display:block;margin-top:18px;font-size:10px;color:#8a948d;overflow-wrap:anywhere}.cap-kind{display:grid;place-items:center;width:32px;height:32px;background:var(--cap-soft);border-radius:7px;font:italic 21px Georgia,serif}.cap-status{font-size:11px;background:#f3f3ee;padding:4px 8px;border-radius:5px}.cap-status.blocked{background:#faece7;color:#8f4a30}.cap-status.verified{background:#e3eee2;color:#335e3c}.cap-detail{padding:25px;background:#fff;border:1px solid var(--cap-line);border-radius:12px;min-width:0}.cap-detail h2{font-size:20px;margin:0}.cap-detail h3,.cap-box h2{font-size:15px}.cap-detail .cap-actions{margin:14px 0}.cap-detail label,.cap-form-grid label{display:flex;flex-direction:column;gap:7px;font-size:12px;margin:12px 0}.cap-library input:not([type=checkbox]),.cap-library select,.cap-library textarea{border:1px solid var(--cap-line);border-radius:6px;background:#fbfcf9;color:#273b30;padding:9px;font:inherit;max-width:100%;min-width:0}.cap-library textarea{width:100%;box-sizing:border-box;line-height:1.5;font-family:Consolas,monospace;font-size:12px;resize:vertical}.cap-detail details{border-top:1px solid var(--cap-line);padding:14px 0;font-size:12px}.cap-detail summary{cursor:pointer}.cap-library pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f4f6f2;padding:14px;border-radius:6px;font-size:12px;max-height:440px;overflow:auto}.cap-fingerprint{font:11px Consolas,monospace;color:#7f8b81}.cap-issues{background:#fbf0e5;border-left:3px solid #c58a56;padding:14px;font-size:12px;line-height:1.7;margin:16px 0}.cap-issues small{display:block;overflow-wrap:anywhere}.cap-test{border-top:1px solid var(--cap-line);margin-top:18px;padding-top:10px}.cap-test>p,.cap-box>p{font-size:12px;color:#778076;line-height:1.7}.cap-file{display:block;border:0;background:transparent;text-align:left;color:#416b52;padding:8px 0;cursor:pointer;overflow-wrap:anywhere}.cap-empty{padding:50px 25px;text-align:center;grid-column:1/-1;background:#f7f8f3;border:1px dashed #ccd6c9;border-radius:12px}.cap-empty>span{font:italic 44px Georgia,serif;color:#b0bfae}.cap-empty h2{font-size:19px;font-weight:500}.cap-empty p{font-size:13px;color:#7b887a}.cap-notice{background:var(--cap-soft);padding:12px 16px;border-radius:8px;font-size:13px}.cap-progress{display:flex;align-items:center;justify-content:space-between;padding:12px;background:#f7f6e8;font-size:13px;margin-bottom:16px}.cap-box{border:1px solid var(--cap-line);padding:20px;margin-bottom:24px;border-radius:10px;background:#fff}.cap-form-grid{display:grid;grid-template-columns:1fr 1fr;gap:0 16px}.cap-check{flex-direction:row!important;align-items:center}.cap-result a{display:block;color:#3f7050}.cap-library button:focus-visible,.cap-library input:focus-visible,.cap-library textarea:focus-visible,.cap-library select:focus-visible{outline:2px solid #5b8667;outline-offset:3px}.cap-library button:disabled{opacity:.45;cursor:not-allowed}
@media(max-width:1050px){.cap-layout.has-detail{grid-template-columns:1fr;padding-top:24px}.cap-content{padding:0 20px 24px}}@media(max-width:640px){.cap-content{padding:0 14px 20px}.cap-form-grid{grid-template-columns:1fr}.cap-toolbar{gap:18px}.cap-detail{padding:18px}.cap-actions{gap:7px}}
.cap-detail-heading{padding:28px 0;border-bottom:1px solid var(--cap-line);margin-bottom:24px}.cap-detail-heading h2{font-size:28px;margin:10px 0}.cap-detail-heading p{font-size:15px;line-height:1.7}.cap-detail-heading small{color:#718076}.cap-detail-grid{display:grid;grid-template-columns:minmax(0,.85fr) minmax(0,1.15fr);gap:40px}.cap-explanation{min-width:0}.cap-operation{min-width:0;border-left:1px solid var(--cap-line);padding-left:32px}.cap-explanation h3,.cap-operation h3{font-size:19px}.cap-muted{font-size:13px;line-height:1.8;color:#607367}.cap-example-text{white-space:pre-wrap}.cap-test-inputs{border:0;padding:0;margin:12px 0}.cap-test-modes{display:flex;gap:20px}.cap-test-modes label{flex-direction:row;align-items:center;font-size:14px}.cap-test-modes input{width:auto}.cap-test textarea{font-family:inherit;font-size:14px}.cap-ready{background:#edf5ef;border:1px solid #bfd3c5;border-radius:10px;padding:18px;margin:20px 0}.cap-ready button{margin-right:10px}.cap-saved-case{padding:12px 0}.cap-saved-case small{display:block;margin-top:6px;color:#657168}.cap-result{margin-top:28px;padding:20px;background:#f6f9f4;border-radius:12px;max-height:650px;overflow:auto}.cap-operation>details{margin-top:20px}.cap-test{margin-top:24px}.cap-test legend{font-size:13px}.cap-detail-grid input[type=radio]{width:auto}
@media(max-width:800px){.cap-detail-grid{grid-template-columns:1fr;gap:20px}.cap-operation{border-left:0;padding-left:0}.cap-explanation{display:contents}.cap-explanation>h3,.cap-explanation>.cap-muted{display:none}.cap-operation{grid-row:1}.cap-detail-heading{padding:16px 0}.cap-detail-heading h2{font-size:24px}}
.cap-save-toolbar{position:sticky;top:0;z-index:4;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 0;background:#fff;border-bottom:1px solid var(--cap-line);margin-bottom:18px}.cap-save-toolbar p{margin:6px 0 0;font-size:12px;color:#657168;line-height:1.7}.cap-save-toolbar button{flex-shrink:0}.cap-saved-confirmation{display:flex;gap:16px;background:#edf5ef;border:1px solid #bfd3c5;padding:24px;border-radius:12px;margin-bottom:24px;scroll-margin-top:20px}.cap-saved-confirmation>span{display:grid;place-items:center;flex-shrink:0;width:32px;height:32px;border-radius:50%;background:#416b52;color:white}.cap-saved-confirmation h3{margin:3px 0 10px}.cap-saved-confirmation p{font-size:14px;line-height:1.7}.cap-saved-confirmation small{color:#657168}.cap-saved-confirmation .cap-actions{margin-top:18px}@media(max-width:640px){.cap-save-toolbar{align-items:flex-start;gap:10px}.cap-save-toolbar strong{font-size:13px}.cap-save-toolbar button{padding:10px}.cap-saved-confirmation{padding:18px;gap:10px}}
</style>

<style scoped>
.cap-draft-editor>label{display:grid;gap:8px;margin:16px 0;font-size:13px}.cap-draft-editor>details{border-top:1px solid var(--cap-line);padding-top:15px;margin:18px 0;font-size:12px}.cap-draft-editor>details>textarea{margin-top:12px}.cap-draft-editor summary{cursor:pointer;overflow-wrap:anywhere}
</style>

<style scoped>
.cap-capture-step{margin:18px 0;padding:18px;border:1px solid var(--cap-line);border-radius:10px;background:var(--surface-2)}.cap-capture-step label{display:grid;gap:9px;font-size:13px}.cap-capture-step select{max-width:100%;width:100%;padding:10px;border:1px solid var(--cap-line);border-radius:7px;background:var(--surface);font:inherit}.cap-capture-step p{font-size:12px;line-height:1.8;color:var(--muted)}.cap-capture-step ul{list-style:none;margin:0;padding:0}.cap-capture-step li{padding:10px 0;border-top:1px solid var(--cap-line);font-size:12px;overflow-wrap:anywhere}.cap-capture-step li span{display:block;margin-top:5px;color:var(--muted)}
</style>
