<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue';
import Icon from './Icon.vue';
import CapabilityLibraryPanel from './CapabilityLibraryPanel.vue';
import AppsLibraryPanel from './AppsLibraryPanel.vue';
import AppCreatePanel from './AppCreatePanel.vue';
import CapabilityDirectoryPanel from './CapabilityDirectoryPanel.vue';
import CapabilityCreatePanel from './CapabilityCreatePanel.vue';

const props = defineProps({ ownerId: { type: String, required: true }, files: Array, task: Object, streaming: Boolean, initialAssetId: String, initialStrategyAppId: String, initialCapture: Object, unifiedAuthoring: Boolean, appListRequest: Object });
const emit = defineEmits(['select', 'community', 'author', 'legacy', 'capability-draft', 'open-workspace', 'open-strategy', 'open-asset', 'initial-strategy-opened']);
const categories = [
  { kind: 'app', name: 'Apps', icon: 'grid', description: '管理我的 APP，发现平台上已发布的应用。' },
  { kind: 'skill', name: 'Skills', icon: 'spark', description: '用 SKILL.md 描述方法、规则与参考资源。' },
  { kind: 'tool', name: 'Tools', icon: 'code', description: '定义工具的用途、输入参数与执行实现。' },
];
const kind = ref(props.initialStrategyAppId ? 'app' : props.initialCapture?.kind || (props.initialAssetId ? 'skill' : 'app'));
const dialog = ref(null), addButton = ref(null), choice = ref('app'), editing = ref('');
const openedAssetId = ref(props.initialAssetId || ''), openedAppId = ref(''), openedVersionId = ref('');
const appListTarget = ref(null);
const managing = ref(Boolean(props.initialAssetId || props.initialCapture));
const captureRequest = ref(props.initialCapture || null);
const savedNotice = ref(null);
const revision = ref(0), connectionRequest = ref(0), notice = ref('');
const choiceDescription = computed(() => categories.find(item => item.kind === choice.value)?.description);
let alive = true;
onUnmounted(() => { alive = false; dialog.value?.close(); });
function openChooser() { choice.value = kind.value; dialog.value.showModal(); }
function closeChooser() { dialog.value?.close(); }
function begin() { editing.value = choice.value; closeChooser(); nextTick(() => document.querySelector('.skill-pool-editor input, .skill-pool-editor textarea, .pool-editor-region input, .pool-editor-region textarea')?.focus()); }
function selectCategory(value) { kind.value = value; managing.value = false; captureRequest.value = null; openedAssetId.value = ''; openedVersionId.value = ''; openedAppId.value = ''; appListTarget.value = null; notice.value = ''; }
function manageCapability(target = {}) {
  openedAssetId.value = target.assetId || ''; openedVersionId.value = target.versionId || '';
  captureRequest.value = null; managing.value = true; notice.value = '';
}
function returnToDirectory() {
  managing.value = false; captureRequest.value = null; openedAssetId.value = ''; openedVersionId.value = ''; revision.value++;
}
let appListSequence = 0;
function showSavedApp(payload) {
  if (!alive || !payload?.appId) return;
  editing.value = ''; managing.value = false; captureRequest.value = null; kind.value = 'app';
  openedAssetId.value = ''; openedAppId.value = ''; openedVersionId.value = '';
  appListTarget.value = { appId: payload.appId, versionId: payload.versionId || '', token: payload.token || `pool-${++appListSequence}` };
  revision.value++;
  notice.value = 'APP 已保存到我的 APP，当前未验证、未启用。点击卡片查看详情。';
}
watch(() => [props.appListRequest?.appId, props.appListRequest?.versionId, props.appListRequest?.token], ([appId, versionId, token]) => {
  if (appId) showSavedApp({ appId, versionId, token });
}, { immediate: true });
function finish(payload) {
  if (!alive) return;
  if (payload?.kind === 'app') { showSavedApp(payload); return; }
  editing.value = '';
  managing.value = false; captureRequest.value = null;
  kind.value = payload.kind;
  openedAssetId.value = payload.assetId || '';
  openedAppId.value = payload.appId || '';
  openedVersionId.value = payload.versionId || '';
  revision.value++;
  notice.value = '已保存到技能池，尚未启用。可以在详情中继续验证。';
  nextTick(() => { savedNotice.value?.scrollIntoView({ block: 'start' }); savedNotice.value?.focus({ preventScroll: true }); });
}
async function manageConnections() {
  editing.value = ''; kind.value = 'tool'; openedAssetId.value = ''; openedVersionId.value = ''; captureRequest.value = null; managing.value = true;
  await nextTick(); connectionRequest.value++;
}
function focusCategory(event, index) {
  const next = event.key === 'ArrowRight' ? (index + 1) % 3 : event.key === 'ArrowLeft' ? (index + 2) % 3 : event.key === 'Home' ? 0 : event.key === 'End' ? 2 : -1;
  if (next < 0) return;
  event.preventDefault(); selectCategory(categories[next].kind);
  event.currentTarget.parentElement.querySelectorAll('[role=tab]')[next]?.focus();
}
</script>

<template>
  <div class="skill-pool">
    <header class="page-header pool-heading">
      <div><span class="pool-eyebrow">技能池</span><h1 class="page-title">Apps, Skills &amp; Tools</h1><p class="page-subtitle">从一个工具、一套方法，到可重复运行的完整流程。</p></div>
      <button v-if="!editing" ref="addButton" type="button" class="primary-button pool-add" @click="openChooser"><Icon name="add" small />加入技能</button>
    </header>
    <div v-if="editing" class="pool-editor-region">
      <AppCreatePanel v-if="editing === 'app'" :key="ownerId + editing" :owner-id="ownerId" :files="files" @saved="finish" @author="emit('author', { kind: 'app', intent: 'create', hideDraftOnly: true })" @cancel="editing = ''; nextTick(() => addButton?.focus())" />
      <CapabilityCreatePanel v-else :key="ownerId + editing" :owner-id="ownerId" :kind="editing" @saved="finish" @cancel="editing = ''; nextTick(() => addButton?.focus())" />
    </div>
    <template v-else>
      <p v-if="notice" ref="savedNotice" tabindex="-1" class="pool-notice" role="status">{{ notice }}</p>
      <div class="pool-categories" role="tablist" aria-label="技能池类型">
        <button v-for="(item, index) in categories" :id="'pool-tab-' + item.kind" :key="item.kind" type="button" role="tab" :aria-selected="kind === item.kind" aria-controls="pool-category-content" :tabindex="kind === item.kind ? 0 : -1" :class="{ active: kind === item.kind }" @click="selectCategory(item.kind)" @keydown="focusCategory($event, index)"><Icon :name="item.icon" /><span><strong>{{ item.name }}</strong><small>{{ item.description }}</small></span></button>
      </div>
      <section id="pool-category-content" role="tabpanel" :aria-labelledby="'pool-tab-' + kind">
        <AppsLibraryPanel v-if="kind === 'app'" :key="ownerId" :owner-id="ownerId" :refresh-token="revision" :focus-app-request="appListTarget" :initial-app-id="openedAppId" :initial-version-id="openedVersionId" :initial-strategy-app-id="appListTarget ? '' : initialStrategyAppId" @initial-opened="openedAppId = ''; openedVersionId = ''; emit('initial-strategy-opened')" @open-community="emit('community')" @create="editing = 'app'" @open-workspace="emit('open-workspace', $event)" @open-strategy="emit('open-strategy', $event)" @open-asset="emit('open-asset', $event)" @open-legacy="emit('legacy')" />
        <template v-else-if="managing">
          <div class="pool-manager-heading"><button type="button" class="secondary-button" @click="returnToDirectory">返回{{ kind === 'skill' ? ' Skills' : ' Tools' }}目录</button><span>版本、配置、验证与启用</span></div>
          <CapabilityLibraryPanel :key="ownerId + revision" embedded :pool-kind="kind" :connection-request="connectionRequest" :owner-id="ownerId" :files="files" :task="task" :streaming="streaming" :initial-asset-id="openedAssetId" :initial-version-id="openedVersionId" :initial-capture="captureRequest" :unified-authoring="unifiedAuthoring" @kind="kind = $event" @select="emit('select', $event)" @community="emit('community')" @author="emit('author', $event)" />
        </template>
        <CapabilityDirectoryPanel v-else :key="ownerId + kind" :owner-id="ownerId" :kind="kind" :refresh-token="revision" :initial-asset-id="openedAssetId" :initial-version-id="openedVersionId" @initial-opened="openedAssetId = ''; openedVersionId = ''" @create="editing = kind" @manage="manageCapability" @community="emit('community')" />
      </section>
    </template>
    <dialog ref="dialog" class="pool-join-dialog" aria-labelledby="pool-join-title" @close="addButton?.focus()" @click="event => { if (event.target === dialog) closeChooser(); }">
      <form @submit.prevent="begin">
        <div class="pool-dialog-heading"><div><span class="pool-eyebrow">技能池</span><h2 id="pool-join-title">加入技能</h2></div><button type="button" class="icon-button" aria-label="关闭加入技能窗口" @click="closeChooser"><Icon name="close" /></button></div>
        <label for="pool-join-kind">加入技能方式</label>
        <select id="pool-join-kind" v-model="choice" autofocus><option value="app">APP · 创建或导入应用</option><option value="skill">Skill · 方法与资源</option><option value="tool">Tool · 执行工具</option></select>
        <p class="pool-choice-description">{{ choiceDescription }}</p>
        <p class="pool-dialog-note">自建内容先保存到个人技能池，验证通过后可启用；可信官方或公开精确版本按详情中的准入状态处理。</p>
        <div class="pool-dialog-actions"><button type="button" class="secondary-button" @click="closeChooser">取消</button><button type="submit" class="primary-button">继续</button></div>
      </form>
    </dialog>
  </div>
</template>

<style scoped>
.pool-manager-heading{display:flex;align-items:center;gap:16px;margin:0 32px 20px;font-size:12px;color:var(--muted,#687c87)}@media(max-width:700px){.pool-manager-heading{margin:0 16px 20px;flex-wrap:wrap}}
.skill-pool{min-width:0}.pool-eyebrow{display:block;font-size:11px;letter-spacing:.12em;color:var(--accent,#0c7b82);margin-bottom:8px}.pool-heading{padding-bottom:24px}.pool-add{display:flex;gap:7px;align-items:center;white-space:nowrap}.pool-categories{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:0 32px 24px}.pool-categories button{display:flex;align-items:flex-start;gap:12px;text-align:left;padding:18px;border:1px solid var(--line,#d9e3e8);border-radius:12px;background:var(--surface,#fff);color:var(--muted,#5c7080);cursor:pointer;min-width:0}.pool-categories button.active{color:#086f76;border-color:#78afb2;background:#edf8f7;box-shadow:inset 0 0 0 1px #78afb2}.pool-categories strong{font-size:16px;display:block;line-height:1.5}.pool-categories small{display:block;font-size:12px;line-height:1.7;margin-top:6px}.pool-categories svg{flex:none;margin-top:3px}.pool-categories button:focus-visible{outline:2px solid #087d84;outline-offset:3px}.pool-notice{margin:0 32px 20px;padding:12px 16px;border-radius:8px;background:#e8f5ef;font-size:13px;color:#24644d}.pool-editor-region{margin:0 32px 32px}#pool-category-content{min-width:0}#pool-category-content>.asset-authoring{margin:0 32px 32px;max-width:none}.pool-join-dialog{width:min(480px,calc(100vw - 32px));max-height:calc(100dvh - 40px);box-sizing:border-box;overflow:auto;border:1px solid #dce6eb;border-radius:18px;padding:28px;color:var(--text,#183044);background:#fff;box-shadow:0 24px 80px #17374a30}.pool-join-dialog::backdrop{background:#18304455;backdrop-filter:blur(3px)}.pool-dialog-heading{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:26px}.pool-dialog-heading h2{font-size:23px;margin:0}.pool-join-dialog label{display:block;font-size:14px;font-weight:600;margin-bottom:9px}.pool-join-dialog select{width:100%;box-sizing:border-box;padding:12px;border:1px solid #cad8e0;border-radius:8px;background:#f8fafb;font:inherit;color:inherit}.pool-choice-description{font-size:14px;line-height:1.8;min-height:50px;color:#405c6d}.pool-dialog-note{font-size:12px;line-height:1.8;color:#738692;border-top:1px solid #e3ebef;padding-top:16px}.pool-dialog-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:24px}@media(max-width:700px){.pool-categories{grid-template-columns:repeat(3,minmax(0,1fr));gap:7px;margin:0 16px 20px}.pool-categories button{padding:12px 7px;gap:6px;justify-content:center}.pool-categories small{display:none}.pool-categories strong{font-size:13px}.pool-categories svg{width:16px;height:16px}.pool-heading{gap:14px}.pool-heading .page-title{font-size:22px}.pool-editor-region,.pool-notice{margin-left:16px;margin-right:16px}#pool-category-content>.asset-authoring{margin-left:16px;margin-right:16px}.pool-join-dialog{padding:22px}}
</style>
