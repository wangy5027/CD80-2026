<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { createCapabilityDirectoryApi } from '../capabilityDirectoryApi.js';
import { capabilityDetail, capabilityKind, capabilityName, capabilityOwnedTarget, filterCapabilityCards,
  personalCapabilityCards, platformCapabilityCards } from '../capabilityDirectoryModel.js';
import { downloadStandardZip, standardExportSelection } from '../standardExport.js';

const props = defineProps({ ownerId: { type: String, required: true }, kind: { type: String, required: true },
  refreshToken: { type: [String, Number], default: 0 }, initialAssetId: String, initialVersionId: String });
const emit = defineEmits(['create', 'manage', 'community', 'initial-opened']);
const mineQuery = ref(''), platformQuery = ref(''), loading = ref(false), sourceErrors = ref([]), notice = ref('');
const catalog = ref({ personal: [], official: [], community: [] });
const selected = ref(null), detailData = ref(null), detailLoading = ref(false), detailError = ref(''), actionError = ref(''), busy = ref(false);
const exporting = ref(false);
const dialog = ref(null);
let returnFocus = null, loadSequence = 0, detailSequence = 0, initialHandled = '';
let exportController = null;
let live = { owner: props.ownerId, kind: capabilityKind(props.kind), controller: new AbortController() };
const current = context => context === live && !context.controller.signal.aborted;
const apiFor = context => createCapabilityDirectoryApi(context.owner, context.controller.signal);
const noun = computed(() => capabilityName(props.kind));
const myCards = computed(() => personalCapabilityCards(catalog.value.personal, props.kind));
const platformCards = computed(() => platformCapabilityCards({ official: catalog.value.official,
  releases: catalog.value.community }, props.kind));
const filteredMine = computed(() => filterCapabilityCards(myCards.value, mineQuery.value));
const filteredPlatform = computed(() => filterCapabilityCards(platformCards.value, platformQuery.value));
const effectiveSelected = computed(() => selected.value ? { ...selected.value,
  owned: selected.value.owned || detailData.value?.owned === true,
  raw: detailData.value?.personalCopies?.length ? { ...selected.value.raw, personalCopies: detailData.value.personalCopies } : selected.value.raw } : null);
const detailDisplayName = computed(() => detailData.value?.asset?.displayName || detailData.value?.asset?.title ||
  detailData.value?.asset?.name || selected.value?.name || '未命名能力');
const ownedTarget = computed(() => capabilityOwnedTarget(effectiveSelected.value));
const trustedSelection = computed(() => selected.value?.source !== 'personal' ||
  detailData.value?.version?.admission?.validationRequired === false || detailData.value?.asset?.admission?.validationRequired === false);
const canAdd = computed(() => selected.value && ['official', 'community'].includes(selected.value.source) &&
  !effectiveSelected.value?.owned && !detailLoading.value && !detailError.value && !busy.value);
const pretty = value => JSON.stringify(value, null, 2);

async function load() {
  const context = live, at = ++loadSequence, api = apiFor(context);
  loading.value = true; sourceErrors.value = [];
  const jobs = [
    ['我的能力', 'personal', () => api.personal(context.kind)],
    ['官方能力', 'official', () => api.official(context.kind)],
    ['公开能力', 'community', () => api.community(context.kind)],
  ];
  const results = await Promise.allSettled(jobs.map(([, , run]) => run()));
  if (!current(context) || at !== loadSequence) return;
  const next = { ...catalog.value }, errors = [];
  results.forEach((result, index) => {
    const [label, key] = jobs[index];
    if (result.status === 'fulfilled') next[key] = result.value.items || [];
    else if (result.reason?.name !== 'AbortError') errors.push(label + '读取失败：' + result.reason.message);
  });
  catalog.value = next; sourceErrors.value = errors; loading.value = false;
  if (selected.value) selected.value = [...myCards.value, ...platformCards.value]
    .find(item => item.key === selected.value.key && item.versionId === selected.value.versionId) || selected.value;
  await openInitial(context);
}

function resetScope() {
  cancelExport();
  live.controller.abort(); if (dialog.value?.open) dialog.value.close();
  live = { owner: props.ownerId, kind: capabilityKind(props.kind), controller: new AbortController() };
  loadSequence++; detailSequence++; initialHandled = ''; returnFocus = null;
  catalog.value = { personal: [], official: [], community: [] };
  mineQuery.value = ''; platformQuery.value = ''; selected.value = null; detailData.value = null;
  notice.value = ''; sourceErrors.value = []; detailError.value = ''; actionError.value = '';
  loading.value = false; detailLoading.value = false; busy.value = false;
  load();
}

async function fetchDetail(item, api) {
  if (item.source === 'community') return capabilityDetail(item, await api.release(item.id));
  const scope = item.source === 'official' ? 'public' : 'personal';
  const asset = await api.asset(item.id, scope);
  const versionId = item.versionId || asset.activeVersionId || asset.latestVersionId;
  const version = versionId ? await api.version(item.id, versionId, scope) : null;
  return capabilityDetail(item, asset, version);
}

async function inspect(item) {
  if (!item || busy.value) return;
  cancelExport();
  const context = live, at = ++detailSequence, api = apiFor(context);
  if (!dialog.value?.open) returnFocus = document.activeElement;
  selected.value = item; detailData.value = null; detailError.value = ''; actionError.value = ''; notice.value = '';
  detailLoading.value = true;
  await nextTick();
  if (!current(context) || at !== detailSequence) return;
  if (!dialog.value?.open) dialog.value?.showModal();
  try {
    const detail = await fetchDetail(item, api);
    if (current(context) && at === detailSequence) detailData.value = detail;
  } catch (error) {
    if (current(context) && at === detailSequence && error.name !== 'AbortError') detailError.value = error.message;
  } finally { if (current(context) && at === detailSequence) detailLoading.value = false; }
}

async function openInitial(context) {
  const assetId = props.initialAssetId, versionId = props.initialVersionId || '';
  const key = assetId && `${context.owner}:${context.kind}:${assetId}:${versionId}`;
  if (!key || key === initialHandled || !current(context)) return;
  initialHandled = key;
  const found = myCards.value.find(item => item.id === assetId);
  const item = found ? { ...found, versionId: versionId || found.versionId,
    key: `personal:${found.id}:${versionId || found.versionId}` } : {
    key: `personal:${assetId}:${versionId}`, source: 'personal', id: assetId, versionId,
    name: '已保存的' + capabilityName(context.kind), description: '', label: '我的 ' + capabilityName(context.kind), owned: true, status: '待检查', raw: null,
  };
  await inspect(item);
  if (current(context)) emit('initial-opened');
}

function cancelExport() { exportController?.abort(); exportController = null; exporting.value = false; }
async function exportSelected() {
  if (!selected.value || !detailData.value || detailLoading.value || busy.value || exporting.value) return;
  const context = live, at = detailSequence;
  const selection = standardExportSelection(context.kind, { ...selected.value, name: detailDisplayName.value }, detailData.value);
  if (!selection) return;
  const controller = new AbortController();
  exportController = controller; exporting.value = true; actionError.value = '';
  try {
    await downloadStandardZip(selection, { signal: controller.signal });
  } catch (error) {
    if (current(context) && at === detailSequence && !controller.signal.aborted && error.name !== 'AbortError')
      actionError.value = error.message;
  } finally {
    if (exportController === controller) { exportController = null; exporting.value = false; }
  }
}
function closeDetail() { cancelExport(); if (dialog.value?.open) dialog.value.close(); }
function afterClose() {
  cancelExport();
  if (dialog.value?.open) return;
  detailSequence++; selected.value = null; detailData.value = null; detailError.value = ''; actionError.value = '';
  if (returnFocus?.isConnected) returnFocus.focus();
  returnFocus = null;
}

function manageOwned() {
  if (!ownedTarget.value) return;
  const target = ownedTarget.value;
  closeDetail(); emit('manage', target);
}

function markOwned(item, result) {
  const copy = { assetId: result.assetId, versionId: result.versionId || '', name: result.name || item.name };
  selected.value = { ...selected.value, owned: true, raw: { ...selected.value.raw, personalCopies: [copy] } };
  detailData.value = { ...detailData.value, owned: true, personalCopies: [copy] };
  notice.value = result.alreadyOwned ? `这个精确版本已在我的 ${noun.value} 中。` :
    `已保存个人副本。官方或公开精确版本如满足配置与权限要求，可直接在能力管理中启用；自建版本仍需验证。`;
}

async function addSelected() {
  if (!canAdd.value) return;
  const item = selected.value, context = live, api = apiFor(context);
  busy.value = true; actionError.value = '';
  try {
    const result = item.source === 'official' ? await api.addOfficial(item.id, item.versionId) : await api.addCommunity(item.id);
    if (!current(context)) return;
    if (selected.value?.key === item.key) markOwned(item, result);
    else notice.value = result.alreadyOwned ? `这个精确版本已在我的 ${noun.value} 中。` :
      `已保存个人副本。请在能力管理中核对当前版本的验证要求与启用条件。`;
    await load();
  } catch (error) {
    if (!current(context) || error.name === 'AbortError') return;
    if (selected.value?.key !== item.key) {
      actionError.value = ''; notice.value = '添加结果未确认，目录已刷新；请重新打开卡片核对是否已拥有。';
      await load();
      return;
    }
    if (!error.status || error.code === 'OUTCOME_UNKNOWN') {
      try {
        const refreshed = await fetchDetail(item, api);
        if (!current(context) || selected.value?.key !== item.key) return;
        detailData.value = refreshed;
        if (refreshed.owned && refreshed.personalCopies.length) {
          markOwned(item, { ...refreshed.personalCopies[0], alreadyOwned: true });
          await load();
          return;
        }
      } catch (refreshError) {
        if (!current(context) || refreshError.name === 'AbortError') return;
      }
      actionError.value = '添加结果尚未确认。请重试，系统会沿用本次请求编号核对结果。';
    } else actionError.value = error.message;
  } finally { if (current(context)) busy.value = false; }
}

onMounted(load);
onUnmounted(() => { cancelExport(); live.controller.abort(); if (dialog.value?.open) dialog.value.close(); });
watch(() => [props.ownerId, props.kind], resetScope);
watch(() => props.refreshToken, load);
watch(() => [props.initialAssetId, props.initialVersionId], () => { if (!loading.value) openInitial(live); });
</script>

<template>
  <div class="apps-library">
    <p v-if="notice && !selected" class="apps-library-notice" role="status">{{ notice }}</p>
    <p v-if="sourceErrors.length" class="apps-library-error" role="alert">{{ USER_FACING_ERROR }}</p>
    <section class="apps-library-section" :aria-labelledby="`my-${kind}-title`">
      <div class="apps-library-heading"><div><span>MY {{ noun.toUpperCase() }}S</span><h2 :id="`my-${kind}-title`">我的 {{ noun }}s</h2><p>当前账号保存的能力。详情中可进入管理、验证与启用。</p></div><div class="apps-library-dialog-actions"><button type="button" class="secondary-button" @click="emit('manage', { assetId: '' })">能力管理</button><button type="button" class="primary-button" @click="emit('create', { kind: capabilityKind(kind) })">创建 {{ noun }}</button></div></div>
      <label class="apps-library-search"><span class="sr-only">搜索我的 {{ noun }}</span><input v-model="mineQuery" type="search" :placeholder="`搜索我的 ${noun}`" /></label>
      <div v-if="filteredMine.length" class="apps-library-grid">
        <button v-for="item in filteredMine" :key="item.key" type="button" class="apps-library-card" @click="inspect(item)"><span class="apps-library-card-top"><span class="apps-library-tag">{{ item.label }}</span><span class="apps-library-owned">已拥有</span></span><strong>{{ item.name }}</strong><span class="apps-library-card-description">{{ item.description || '打开详情查看用途、规则与使用条件。' }}</span><span class="apps-library-card-bottom"><span>{{ item.status }}</span><span aria-hidden="true">↗</span></span></button>
      </div>
      <p v-else class="apps-library-empty">{{ mineQuery ? `没有匹配的个人 ${noun}。` : loading ? '正在读取个人能力…' : `当前账号暂无个人 ${noun}。` }}</p>
    </section>
    <section class="apps-library-section platform" :aria-labelledby="`platform-${kind}-title`">
      <div class="apps-library-heading"><div><span>PLATFORM {{ noun.toUpperCase() }}S</span><h2 :id="`platform-${kind}-title`">平台 {{ noun }}s</h2><p>官方能力与用户主动发布的公开版本；私人草稿不会出现在这里。</p></div><div class="apps-library-dialog-actions"><button type="button" class="secondary-button" @click="emit('community')">公开目录</button><button type="button" class="secondary-button" :disabled="loading" @click="load">{{ loading ? '同步中…' : '刷新目录' }}</button></div></div>
      <label class="apps-library-search"><span class="sr-only">搜索平台 {{ noun }}</span><input v-model="platformQuery" type="search" :placeholder="`搜索平台 ${noun}`" /></label>
      <div v-if="filteredPlatform.length" class="apps-library-grid">
        <button v-for="item in filteredPlatform" :key="item.key" type="button" class="apps-library-card" @click="inspect(item)"><span class="apps-library-card-top"><span class="apps-library-tag">{{ item.label }}</span><span :class="item.owned ? 'apps-library-owned' : 'apps-library-addable'">{{ item.owned ? '已拥有' : '可添加' }}</span></span><strong>{{ item.name }}</strong><span class="apps-library-card-description">{{ item.description || '打开详情查看用途、权限与依赖。' }}</span><span class="apps-library-card-bottom"><span>{{ item.status }}</span><span aria-hidden="true">↗</span></span></button>
      </div>
      <p v-else class="apps-library-empty">{{ platformQuery ? `没有匹配的平台 ${noun}。` : loading ? '正在读取平台目录…' : `暂无可添加的平台 ${noun}。` }}</p>
    </section>
    <dialog ref="dialog" class="apps-library-dialog" :aria-labelledby="`capability-directory-${kind}-detail`" @close="afterClose" @click="event => { if (event.target === dialog) closeDetail(); }">
      <template v-if="selected">
        <header class="apps-library-dialog-header"><div class="apps-library-dialog-actions"><button type="button" class="primary-button" :disabled="!canAdd" @click="addSelected">{{ effectiveSelected.owned ? '已拥有' : busy ? '正在添加…' : `添加为我的 ${noun}` }}</button><span>{{ selected.label }}</span></div><div class="apps-library-dialog-actions apps-library-header-controls"><button type="button" class="secondary-button" :data-standard-export="kind" :disabled="!detailData || detailLoading || busy || exporting" @click="exportSelected">{{ exporting ? '正在导出…' : '标准格式导出' }}</button><button type="button" class="secondary-button" :aria-label="`关闭 ${noun} 详情`" @click="closeDetail">关闭</button></div></header>
        <div class="apps-library-dialog-content">
          <div class="apps-library-detail-intro"><span>{{ selected.status }}</span><h2 :id="`capability-directory-${kind}-detail`">{{ detailDisplayName }}</h2><p>{{ detailData?.manifest?.description || selected.description || '作者尚未填写用途说明。' }}</p><small>精确版本：{{ detailData?.version?.number ? `V${detailData.version.number} · ` : '' }}{{ detailData?.version?.versionId || selected.versionId || selected.id }}</small></div>
          <p v-if="detailLoading" role="status">正在读取能力详情…</p>
          <p v-if="detailError" class="apps-library-error" role="alert">{{ USER_FACING_ERROR }} <button type="button" class="secondary-button" @click="inspect(selected)">重试读取</button></p>
          <p v-if="actionError" class="apps-library-error" role="alert">{{ USER_FACING_ERROR }}</p>
          <p v-if="notice" class="apps-library-notice" role="status">{{ notice }}</p>
          <template v-if="detailData?.manifest">
            <p class="apps-library-explainer">{{ selected.source === 'personal' ? trustedSelection ? '这是当前账号保存的可信版本；请在管理页面核对配置和启用条件。' : '这是当前账号保存的能力；验证状态以管理页面为准。' : '添加只保存个人副本，不会自动执行、验证或启用。请先核对来源、权限和依赖。' }}</p>
            <dl class="apps-library-facts"><dt>来源标识</dt><dd>{{ detailData.sourceIdentity || '未提供' }}</dd><dt>内容摘要</dt><dd>{{ detailData.contentDigest || '未提供' }}</dd><dt>输入</dt><dd><pre v-if="detailData.input" class="capability-directory-inline-json">{{ pretty(detailData.input) }}</pre><span v-else>{{ kind === 'skill' ? '任务说明与按需授权的材料' : '未声明输入结构' }}</span></dd><dt>输出</dt><dd><pre v-if="detailData.output" class="capability-directory-inline-json">{{ pretty(detailData.output) }}</pre><span v-else>{{ kind === 'skill' ? '按方法生成的回答或文件' : '未声明输出结构' }}</span></dd><template v-if="detailData.manifest.skill?.entry"><dt>方法入口</dt><dd>{{ detailData.manifest.skill.entry }}</dd></template><template v-if="detailData.manifest.kind === 'tool'"><dt>执行方式</dt><dd>{{ detailData.executionLabel }}</dd></template></dl>
            <div class="capability-directory-block"><h3>权限</h3><pre>{{ pretty(detailData.permissions || {}) }}</pre></div>
            <div class="capability-directory-block"><h3>依赖 · {{ detailData.dependencies.length }}</h3><ul v-if="detailData.dependencies.length"><li v-for="(dependency, index) in detailData.dependencies" :key="index">{{ dependency.name || dependency.sourceIdentity || dependency.assetId || `依赖 ${index + 1}` }}<small v-if="dependency.contentDigest"> · {{ dependency.contentDigest }}</small></li></ul><p v-else>此版本未声明外部能力依赖。</p></div>
            <div class="capability-directory-block"><h3>文件清单 · {{ detailData.files.length }}</h3><ul v-if="detailData.files.length"><li v-for="(file, index) in detailData.files" :key="file.path || index">{{ file.path || file.name || `文件 ${index + 1}` }}<small v-if="file.size"> · {{ file.size }} 字节</small></li></ul><p v-else>此版本未声明资源文件。</p></div>
            <details class="apps-library-raw"><summary>查看完整能力清单</summary><pre>{{ pretty(detailData.manifest) }}</pre></details>
          </template>
          <div class="apps-library-detail-footer"><button v-if="ownedTarget" type="button" class="primary-button" @click="manageOwned">{{ trustedSelection ? '管理与启用' : '管理与验证' }}</button><button v-else-if="selected.source !== 'personal'" type="button" class="secondary-button" @click="emit('community')">查看公开目录</button></div>
        </div>
      </template>
    </dialog>
  </div>
</template>

<style scoped src="../poolCatalog.css" />
<style scoped>
.capability-directory-block{padding:20px 0;border-bottom:1px solid var(--line,#d8e2e8)}
.capability-directory-block h3{font-size:15px;margin:0 0 10px}
.capability-directory-block p,.capability-directory-block li{font-size:13px;line-height:1.8;color:var(--muted,#687c87)}
.capability-directory-block ul{margin:0;padding-left:21px}
.capability-directory-block pre,.capability-directory-inline-json{margin:0;max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;line-height:1.7;color:var(--text,#1c3444)}
.capability-directory-block small{overflow-wrap:anywhere}
@media(max-width:700px){.apps-library-heading{flex-direction:column;align-items:stretch}.apps-library-heading>.apps-library-dialog-actions{flex-wrap:wrap;justify-content:flex-start}}
</style>
