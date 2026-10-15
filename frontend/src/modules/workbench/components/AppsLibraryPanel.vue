<script setup>
import AppReviewNotice from './AppReviewNotice.vue';
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import AssetDefinitionView from './AssetDefinitionView.vue';
import { createAppsLibraryApi } from '../appsLibraryApi.js';
import { authoredReleases, filterAppCards, personalAppCards, platformAppCards, targetForOwned } from '../appsLibraryModel.js';
import { downloadStandardZip, standardExportSelection } from '../standardExport.js';

const props = defineProps({ ownerId: { type: String, required: true }, files: Array, strategies: Array,
  refreshToken: { type: [String, Number], default: 0 }, initialAppId: String, initialVersionId: String, initialStrategyAppId: String, focusAppRequest: Object });
const emit = defineEmits(['create', 'open-workspace', 'open-strategy', 'open-asset', 'open-legacy', 'open-community', 'initial-opened']);
const mineQuery = ref(''), platformQuery = ref(''), loading = ref(false), sourceErrors = ref([]), notice = ref('');
const catalog = ref({ assets: [], agents: [], strategies: [], recipes: [], modern: [], legacy: [] });
const selected = ref(null), detail = ref(null), detailLoading = ref(false), detailError = ref(''), actionError = ref('');
const sharePreview = ref(null), confirmSharing = ref(false), busy = ref('');
const exporting = ref(false);
const dialog = ref(null);
const highlightedAppId = ref(''), personalCardNodes = new Map();
let returnFocus = null, loadSequence = 0, detailSequence = 0, initialHandled = '', lastLocatedKey = '';
let exportController = null;
let live = { owner: props.ownerId, controller: new AbortController() };
const apiFor = context => createAppsLibraryApi(context.owner, context.controller.signal);
const current = context => context === live && !context.controller.signal.aborted;
const focusKey = (request = props.focusAppRequest, owner = live.owner) => request?.appId
  ? [owner, request.appId, request.versionId || '', request.token || ''].join(':') : '';
function rememberPersonalCard(item, element) {
  if (item.source !== 'asset') return;
  if (element) personalCardNodes.set(item.id, element);
  else personalCardNodes.delete(item.id);
}
// The account-scoped /agents response is authoritative; a caller's cached
// strategies may still belong to the previous account during sign-out.
const myCards = computed(() => personalAppCards(catalog.value));
const platformCards = computed(() => platformAppCards({ agents: catalog.value.agents, modernReleases: catalog.value.modern,
  legacyReleases: catalog.value.legacy, personalAssets: catalog.value.assets, personalAgents: catalog.value.agents.filter(item => item.personal === true),
  personalRecipes: catalog.value.recipes }));
const filteredMine = computed(() => filterAppCards(myCards.value, mineQuery.value));
const filteredPlatform = computed(() => filterAppCards(platformCards.value, platformQuery.value));
const definition = computed(() => ['asset', 'community-app'].includes(selected.value?.source)
  ? detail.value?.definition || detail.value?.package?.definition || null : null);
const myPublished = computed(() => selected.value?.source === 'asset' ? authoredReleases(catalog.value.modern, selected.value.id) : []);
const canPublish = computed(() => selected.value?.source === 'asset' && detail.value?.verification?.status === 'passed');
const ownedTarget = computed(() => targetForOwned(selected.value));

async function load() {
  const context = live, at = ++loadSequence, api = apiFor(context);
  loading.value = true; sourceErrors.value = [];
  const jobs = [
    ['我的现代 APP', 'assets', api.personalAssets, value => value.items || []],
    ['个人策略和旧版流程', 'agents', api.agents, value => value.agents || []],
    ['我的旧版研究应用', 'recipes', api.personalRecipes, value => value.items || []],
    ['公开现代 APP', 'modern', api.modernPublic, value => value.items || []],
    ['公开旧版应用', 'legacy', api.legacyPublic, value => value.items || []],
  ];
  const results = await Promise.allSettled(jobs.map(([, , run]) => run()));
  if (!current(context) || at !== loadSequence) return;
  const next = { ...catalog.value }, errors = [];
  results.forEach((result, index) => {
    const [label, key, , unpack] = jobs[index];
    if (result.status === 'fulfilled') {
      next[key] = unpack(result.value);
      if (key === 'agents') next.strategies = result.value.strategyApps || [];
    } else if (result.reason?.name !== 'AbortError') errors.push(label + '读取失败：' + result.reason.message);
  });
  catalog.value = next; sourceErrors.value = errors; loading.value = false;
  if (selected.value) selected.value = [...myCards.value, ...platformCards.value].find(item => item.key === selected.value.key) || selected.value;
  await focusRequested(context, at);
  await openInitial(context);
}

async function focusRequested(context, at) {
  const request = props.focusAppRequest, key = focusKey(request, context.owner);
  if (!key || key === lastLocatedKey) return;
  mineQuery.value = '';
  await nextTick();
  if (!current(context) || at !== loadSequence || key !== focusKey()) return;
  if (!myCards.value.some(item => item.source === 'asset' && item.id === request.appId)) {
    notice.value = 'APP 已保存，但我的 APP 列表尚未同步。请刷新目录。';
    return;
  }
  const card = personalCardNodes.get(request.appId);
  if (!card) return;
  highlightedAppId.value = request.appId;
  lastLocatedKey = key;
  if (notice.value === 'APP 已保存，但我的 APP 列表尚未同步。请刷新目录。') notice.value = '';
  card.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
  card.focus?.({ preventScroll: true });
}

async function openInitial(context) {
  const appId = props.initialAppId, versionId = props.initialVersionId || '';
  const strategyId = props.initialStrategyAppId || '';
  const key = appId ? `${context.owner}:asset:${appId}:${versionId}` : strategyId ? `${context.owner}:strategy:${strategyId}` : '';
  if (!key || key === initialHandled || !current(context)) return;
  initialHandled = key;
  const found = myCards.value.find(item => item.source === (appId ? 'asset' : 'strategy') && item.id === (appId || strategyId));
  const card = found ? appId ? { ...found, versionId: versionId || found.versionId } : found
    : appId ? personalAppCards({ assets: [{ id: appId, versionId, name: '已保存的 APP' }] }).find(item => item.source === 'asset')
      : personalAppCards({ strategies: [{ id: strategyId, name: '已保存的策略' }] }).find(item => item.source === 'strategy');
  await inspect(card);
  if (current(context)) emit('initial-opened');
}

function resetOwner() {
  cancelExport();
  live.controller.abort(); dialog.value?.close();
  live = { owner: props.ownerId, controller: new AbortController() };
  loadSequence++; detailSequence++; initialHandled = ''; returnFocus = null;
  lastLocatedKey = ''; highlightedAppId.value = ''; personalCardNodes.clear();
  catalog.value = { assets: [], agents: [], strategies: [], recipes: [], modern: [], legacy: [] };
  mineQuery.value = ''; platformQuery.value = ''; selected.value = null; detail.value = null;
  sharePreview.value = null; confirmSharing.value = false; notice.value = ''; actionError.value = ''; detailError.value = '';
  sourceErrors.value = []; busy.value = ''; loading.value = false; detailLoading.value = false;
  load();
}
onMounted(load);
onUnmounted(() => { cancelExport(); live.controller.abort(); dialog.value?.close(); });
watch(() => props.ownerId, resetOwner);
watch(() => [props.refreshToken, focusKey()], load);
watch(() => [props.initialAppId, props.initialVersionId, props.initialStrategyAppId], () => { if (!loading.value) openInitial(live); });

async function inspect(item) {
  if (!item || busy.value) return;
  cancelExport();
  const context = live, at = ++detailSequence, api = apiFor(context);
  returnFocus = document.activeElement; selected.value = item; detail.value = null; notice.value = '';
  detailError.value = ''; actionError.value = ''; sharePreview.value = null; confirmSharing.value = false;
  detailLoading.value = true;
  await nextTick();
  if (!current(context) || at !== detailSequence) return;
  if (!dialog.value?.open) dialog.value?.showModal();
  try {
    let value;
    switch (item.source) {
      case 'asset': value = await api.asset(item.id, item.versionId); break;
      case 'strategy': value = await api.personalStrategy(item.id); break;
      case 'community-app': value = await api.modernRelease(item.id); break;
      case 'legacy-publication': value = await api.legacyRelease(item.id); break;
      default: value = item.raw;
    }
    if (current(context) && at === detailSequence) detail.value = value;
  } catch (error) {
    if (current(context) && at === detailSequence && error.name !== 'AbortError') detailError.value = error.message;
  } finally { if (current(context) && at === detailSequence) detailLoading.value = false; }
}
function cancelExport() { exportController?.abort(); exportController = null; exporting.value = false; }
async function exportSelected() {
  if (!selected.value || !detail.value || detailLoading.value || exporting.value || busy.value) return;
  const context = live, at = detailSequence;
  const selection = standardExportSelection('app', selected.value, detail.value);
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
  detailSequence++; selected.value = null; detail.value = null; sharePreview.value = null;
  confirmSharing.value = false; detailError.value = ''; actionError.value = '';
  if (returnFocus?.isConnected) returnFocus.focus();
  returnFocus = null;
}
function openOwned() {
  const target = ownedTarget.value;
  if (!target) return;
  closeDetail();
  if (target.type === 'workspace') emit('open-workspace', target.view);
  else if (target.type === 'strategy') emit('open-strategy', target.app);
  else if (target.type === 'asset') emit('open-asset', { appId: target.appId, versionId: target.versionId });
  else emit('open-legacy');
}
async function copySelected() {
  const item = selected.value;
  if (!item || item.owned || busy.value) return;
  const context = live, api = apiFor(context);
  busy.value = 'copy'; actionError.value = '';
  try {
    let result;
    if (item.source === 'community-app') result = await api.copyModern(item.id);
    else if (item.source === 'preset') {
      const duplicate = Boolean(item.raw?.personalCopies?.length);
      if (duplicate && !window.confirm('旧版流程库中已有相同内容。仍要另存一份未验证的现代 APP 吗？')) return;
      result = await api.copyPreset(item.id, duplicate);
    } else if (item.source === 'legacy-publication') {
      const duplicate = Boolean(item.raw?.legacyCopyExists || item.raw?.personalCopies?.length);
      if (duplicate && !window.confirm('旧版应用库中已有相同内容。仍要另存一份未验证的现代 APP 吗？')) return;
      result = await api.copyLegacy(item.id, {}, duplicate);
    }
    else return;
    if (!current(context)) return;
    const appId = result?.assetId || result?.appId;
    if (appId && result?.versionId) {
      notice.value = '已保存到我的 APP，当前未验证、未启用。请在详情检查并验证。';
      closeDetail(); emit('open-asset', { appId, versionId: result.versionId });
      return;
    }
    selected.value = { ...item, owned: true, raw: item.source === 'community-app' && result?.appId
      ? { ...item.raw, personalCopies: [{ appId: result.appId, versionId: result.versionId }] } : item.raw };
    notice.value = result?.alreadyOwned ? '这份公开版本已经在我的 APP 中。' : '已保存独立个人副本，请检查配置并重新验证后使用。';
    await load();
  } catch (error) { if (current(context) && error.name !== 'AbortError') actionError.value = error.message; }
  finally { if (current(context)) busy.value = ''; }
}
async function previewSharing() {
  if (!canPublish.value || busy.value) return;
  const context = live, item = selected.value, api = apiFor(context);
  busy.value = 'preview'; actionError.value = ''; sharePreview.value = null; confirmSharing.value = false;
  try {
    const value = await api.sharingPreview(item.id, detail.value.versionId);
    if (current(context) && selected.value?.key === item.key) sharePreview.value = value;
  } catch (error) { if (current(context) && error.name !== 'AbortError') actionError.value = error.message; }
  finally { if (current(context)) busy.value = ''; }
}
async function publish() {
  if (!sharePreview.value || !confirmSharing.value || busy.value) return;
  const context = live, api = apiFor(context), preview = sharePreview.value;
  busy.value = 'publish'; actionError.value = '';
  try {
    await api.publish(preview);
    if (!current(context)) return;
    sharePreview.value = null; confirmSharing.value = false;
    notice.value = '此精确版本已发布到平台 APP。公开快照不包含私人文件授权、凭据或运行历史。';
    await load();
  } catch (error) { if (current(context) && error.name !== 'AbortError') actionError.value = error.message; }
  finally { if (current(context)) busy.value = ''; }
}
async function withdraw(release) {
  if (!release || busy.value || !window.confirm('撤回此公开版本？已有个人副本会保留。')) return;
  const context = live, api = apiFor(context);
  busy.value = 'withdraw'; actionError.value = '';
  try {
    await api.withdraw(release.releaseId, release.revision);
    if (!current(context)) return;
    notice.value = '公开版本已撤回；已有个人副本保留。';
    await load();
    if (selected.value?.source === 'community-app') closeDetail();
  } catch (error) { if (current(context) && error.name !== 'AbortError') actionError.value = error.message; }
  finally { if (current(context)) busy.value = ''; }
}
const canCopy = computed(() => selected.value && !selected.value.owned && ['community-app', 'preset', 'legacy-publication'].includes(selected.value.source));
const json = value => JSON.stringify(value, null, 2);
</script>

<template>
  <div class="apps-library">
    <p v-if="notice" class="apps-library-notice" role="status">{{ notice }}</p>
    <p v-if="sourceErrors.length" class="apps-library-error" role="alert">{{ USER_FACING_ERROR }}</p>
    <section class="apps-library-section" aria-labelledby="my-apps-title">
      <div class="apps-library-heading"><div><span>MY APPS</span><h2 id="my-apps-title">我的 APP</h2><p>包括默认工作区、已保存的完整应用与个人策略。</p></div><button type="button" class="primary-button" @click="emit('create')">创建 APP</button></div>
      <label class="apps-library-search"><span class="sr-only">搜索我的 APP</span><input v-model="mineQuery" type="search" placeholder="搜索我的 APP" /></label>
      <div v-if="filteredMine.length" class="apps-library-grid">
        <button v-for="item in filteredMine" :key="item.key" :ref="element => rememberPersonalCard(item, element)" type="button" class="apps-library-card" :class="{ 'is-recent': item.source === 'asset' && item.id === highlightedAppId }" :data-app-id="item.source === 'asset' ? item.id : undefined" @click="inspect(item)">
          <span class="apps-library-card-top"><span class="apps-library-tag">{{ item.label }}</span><span :class="item.source === 'asset' && item.id === highlightedAppId ? 'apps-library-new' : 'apps-library-owned'">{{ item.source === 'asset' && item.id === highlightedAppId ? '刚保存' : '已拥有' }}</span></span>
          <strong>{{ item.name }}</strong><span class="apps-library-card-description">{{ item.description || '打开详情查看实际方法与使用条件。' }}</span>
          <span class="apps-library-card-bottom"><span>{{ item.status }}</span><span aria-hidden="true">↗</span></span>
        </button>
      </div>
      <p v-else class="apps-library-empty">{{ mineQuery ? '没有匹配的个人 APP。' : '当前账号暂无个人应用。' }}</p>
    </section>
    <section class="apps-library-section platform" aria-labelledby="platform-apps-title">
      <div class="apps-library-heading"><div><span>PLATFORM APPS</span><h2 id="platform-apps-title">平台 APP</h2><p>平台内置与主动公开的应用；私人草稿不会出现在这里。</p></div><button type="button" class="secondary-button" :disabled="loading" @click="load">{{ loading ? '同步中…' : '刷新目录' }}</button></div>
      <label class="apps-library-search"><span class="sr-only">搜索平台 APP</span><input v-model="platformQuery" type="search" placeholder="搜索平台 APP" /></label>
      <div v-if="filteredPlatform.length" class="apps-library-grid">
        <button v-for="item in filteredPlatform" :key="item.key" type="button" class="apps-library-card" @click="inspect(item)">
          <span class="apps-library-card-top"><span class="apps-library-tag">{{ item.label }}</span><span :class="item.owned ? 'apps-library-owned' : 'apps-library-addable'">{{ item.owned ? '已拥有' : '可添加' }}</span></span>
          <strong>{{ item.name }}</strong><span class="apps-library-card-description">{{ item.description || '打开详情查看工作流程与使用条件。' }}</span>
          <span class="apps-library-card-bottom"><span>{{ item.status }}</span><span aria-hidden="true">↗</span></span>
        </button>
      </div>
      <p v-else class="apps-library-empty">{{ platformQuery ? '没有匹配的平台 APP。' : '暂无可添加的公开应用；内置工作区仍可使用。' }}</p>
    </section>
    <dialog ref="dialog" class="apps-library-dialog" aria-labelledby="apps-library-detail-title" @close="afterClose" @click="event => { if (event.target === dialog) closeDetail(); }">
      <template v-if="selected">
        <header class="apps-library-dialog-header"><div class="apps-library-dialog-actions"><button type="button" class="primary-button" :disabled="selected.owned || !canCopy || !!busy || detailLoading" @click="copySelected">{{ selected.owned ? '已拥有' : busy === 'copy' ? '正在添加…' : '添加为我的 APP' }}</button><span>{{ selected.label }}</span></div><div class="apps-library-dialog-actions apps-library-header-controls"><button type="button" class="secondary-button" data-standard-export="app" :disabled="!detail || detailLoading || !!busy || exporting" @click="exportSelected">{{ exporting ? '正在导出…' : '标准格式导出' }}</button><button type="button" class="secondary-button" aria-label="关闭 APP 详情" @click="closeDetail">关闭</button></div></header>
        <div class="apps-library-dialog-content">
          <div class="apps-library-detail-intro"><span>{{ selected.status }}</span><h2 id="apps-library-detail-title">{{ selected.name }}</h2><p>{{ selected.description || '作者尚未填写用途说明。' }}</p><small v-if="selected.versionId">版本：{{ selected.versionId }}</small></div>
          <p v-if="detailLoading" role="status">正在读取完整应用详情…</p>
          <p v-if="detailError" class="apps-library-error" role="alert">{{ USER_FACING_ERROR }} <button type="button" class="secondary-button" @click="inspect(selected)">重试读取</button></p>
          <p v-if="actionError" class="apps-library-error" role="alert">{{ USER_FACING_ERROR }}</p>
          <p v-if="notice" class="apps-library-notice" role="status">{{ notice }}</p>
          <template v-if="detail">
            <AppReviewNotice v-if="selected.source === 'asset' || selected.source === 'strategy'" :status="selected.source === 'strategy' ? detail.app?.verification?.status : detail.verification?.status" />
            <template v-if="selected.source === 'builtin'"><dl class="apps-library-facts"><dt>工作流程</dt><dd>{{ detail.workflow }}</dd><dt>输入</dt><dd>{{ detail.inputs }}</dd><dt>输出</dt><dd>{{ detail.outputs }}</dd><dt>运行条件</dt><dd>登录后进入此工作区；业务流程独立使用。</dd></dl></template>
            <template v-else-if="definition"><p class="apps-library-explainer">以下为此版本完整方法；复制后需要使用自己的连接与授权，并重新验证才能启用。</p><AssetDefinitionView :definition="definition" /><details class="apps-library-raw"><summary>查看完整定义与资源包 JSON</summary><pre>{{ json(detail.package || definition) }}</pre></details></template>
            <template v-else-if="selected.source === 'strategy'"><dl class="apps-library-facts"><dt>原始想法</dt><dd>{{ detail.definition?.idea || detail.run?.idea || selected.description || '查看策略工坊中的保存记录。' }}</dd><dt>版本状态</dt><dd>{{ detail.app?.verification?.status === 'passed' ? '工程验证与诊断回测通过' : '未验证 · 尚未运行诊断回测' }}</dd><dt>使用方式</dt><dd>进入策略详情，审阅冻结规则，再主动修改或验证。</dd><dt>运行条件</dt><dd>验证时需重新确认行情、日期、资金和实际执行条件。</dd></dl><details class="apps-library-raw"><summary>查看已保存的策略规则{{ detail.run ? '与回测记录' : '' }}</summary><pre>{{ json(detail.definition || detail.app || selected.raw) }}</pre></details></template>
            <template v-else-if="selected.source === 'legacy-recipe'"><dl class="apps-library-facts"><dt>工作流程</dt><dd>{{ detail.goalTemplate }}</dd><dt>固定约束</dt><dd>{{ (detail.constraints || []).join('；') || '无' }}</dd><dt>运行条件</dt><dd>按旧版研究应用流程运行；依赖能力需按原规则核对。</dd></dl></template>
            <template v-else><p class="apps-library-explainer">添加后会保存一份个人现代 APP，状态为未验证、未启用；请在详情检查依赖与输入，不会自动执行。</p><details class="apps-library-raw" open><summary>工作流程、输入输出与完整定义</summary><pre>{{ json(detail.spec || detail.definition || detail) }}</pre></details></template>
            <div v-if="selected.source === 'asset'" class="apps-library-share"><h3>发布这个版本</h3><p>仅当前精确版本通过验证且运行配置仍一致时可发布。先预览完整公开内容，再确认分享。</p><button type="button" class="secondary-button" :disabled="!canPublish || !!busy" @click="previewSharing">{{ busy === 'preview' ? '正在检查…' : '预览公开内容' }}</button><p v-if="!canPublish">此版本尚未验证通过，不能发布。</p>
              <section v-if="sharePreview" class="apps-library-share-preview" aria-label="公开内容预览"><h4>公开快照预览</h4><p>下方完整定义与资源将公开；私人文件授权、凭据、聊天和运行历史不会公开。</p><AssetDefinitionView v-if="sharePreview.definition" :definition="sharePreview.definition" /><details class="apps-library-raw" open><summary>完整源码与资源 JSON · 摘要 {{ sharePreview.snapshotDigest }}</summary><pre>{{ json(sharePreview.package) }}</pre></details><p v-if="sharePreview.warnings?.length">{{ USER_FACING_ERROR }}</p><label class="apps-library-confirm"><input v-model="confirmSharing" type="checkbox" :disabled="!!busy" />我已核对上述完整公开内容，确认发布此版本</label><button type="button" class="primary-button" :disabled="!confirmSharing || !!busy" @click="publish">{{ busy === 'publish' ? '正在发布…' : '确认发布' }}</button></section>
              <div v-if="myPublished.length" class="apps-library-published"><h4>我发布的版本</h4><div v-for="release in myPublished" :key="release.releaseId"><span>{{ release.title }} · {{ release.sourceVersionId }}</span><button type="button" class="secondary-button" :disabled="!!busy" @click="withdraw(release)">撤回公开版本</button></div></div>
            </div>
            <div v-if="selected.source === 'community-app' && detail.isOwner && detail.status === 'published'" class="apps-library-published"><button type="button" class="secondary-button" :disabled="!!busy" @click="withdraw(detail)">撤回此公开版本</button></div>
          </template>
          <div class="apps-library-detail-footer"><button v-if="ownedTarget" type="button" class="primary-button" @click="openOwned">{{ selected.source === 'builtin' ? '打开工作区' : selected.source === 'strategy' ? '打开策略详情，修改或验证' : '打开我的 APP' }}</button><button v-if="selected.source === 'legacy-publication' && actionError" type="button" class="secondary-button" @click="emit('open-community')">到共创处理依赖</button></div>
        </div>
      </template>
    </dialog>
  </div>
</template>

<style scoped src="../poolCatalog.css" />
<style scoped>
.apps-library-card.is-recent{border-color:#087f84;box-shadow:0 0 0 2px #087f8433,0 10px 24px #15394312}
.apps-library-new{font-size:11px;color:#087f84;font-weight:700}
</style>
