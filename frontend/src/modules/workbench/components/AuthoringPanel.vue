<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import CapabilityResult from './CapabilityResult.vue';
import AssetDefinitionView from './AssetDefinitionView.vue';
import AssetConnectionBindings from './AssetConnectionBindings.vue';
import { capGet } from '../capabilityApi.js';
import { resourceFiles, artifactUrl } from '../assetAppsModel.js';
import { accountDraftKey, readDraft, writeDraft } from '../draftStore.js';
import { authoringAllowed, authoringBudget, authoringCases, authoringPhases, authoringTitle, kindLabel, prettyAuthoring, currentEvidence, authoringConnectionRequirements } from '../authoringModel.js';
const props = defineProps({ controller: { type: Object, required: true }, ownerId: String, files: { type: Array, default: () => [] } });
const emit = defineEmits(['asset','chat','connections']);
const state = props.controller.s;
const detail = computed(() => state.details[state.selectedId]);
const session = computed(() => detail.value?.session);
const savedApp = computed(() => session.value?.kind === 'app' && session.value?.phase === 'completed' && session.value?.mode !== 'draft_only' && session.value?.result?.assetId ? session.value.result : null);
const pendingChat = computed(() => Boolean(state.pendingChat?.[state.selectedId]));
const actionBusy = computed(() => state.busy || pendingChat.value);
const candidate = computed(() => detail.value?.candidate);
const definition = computed(() => candidate.value?.content);
const editableContent = computed(() => session.value?.draftContent ?? definition.value);
const budget = computed(() => authoringBudget(detail.value));
const cases = computed(() => authoringCases(detail.value));
const evidence = computed(() => currentEvidence(detail.value));
const resources = computed(() => resourceFiles({ files: definition.value?.files || [] }));
const unknownRequests = computed(() => detail.value?.budget?.unknownRequests || []);
const acknowledgedUnknownRequests = ref([]), selectedFileIds = ref([]);
const unknownAcknowledged = computed(() => unknownRequests.value.every(item => acknowledgedUnknownRequests.value.includes(item.id)));
const connections = ref(null), connectionsLoading = ref(false), connectionsError = ref(''), runtimeBindings = ref({}), bindingBase = ref('{}');
const connectionRequirements = computed(() => authoringConnectionRequirements(detail.value,connections.value));
const bindingsChanged = computed(() => JSON.stringify(runtimeBindings.value) !== bindingBase.value);
const connectionController = new AbortController(); let connectionSequence = 0, bindingSession = '';
async function loadConnections() {
  const sequence = ++connectionSequence; connectionsLoading.value = true; connectionsError.value = '';
  try {
    const items = []; let cursor = '';
    do { const value = await capGet('/connections?limit=100' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : ''), { signal: connectionController.signal }); if (connectionController.signal.aborted || sequence !== connectionSequence) return; items.push(...(value.items || [])); cursor = value.nextCursor || ''; } while (cursor);
    connections.value = items;
  } catch (error) { if (sequence === connectionSequence && error.name !== 'AbortError') connectionsError.value = error.message; }
  finally { if (sequence === connectionSequence) connectionsLoading.value = false; }
}
watch(() => JSON.stringify(connectionRequirements.value.map(item => item.sourceIdentity)), () => { if (connectionRequirements.value.length) loadConnections(); }, { immediate: true });
watch(() => [state.selectedId, JSON.stringify(detail.value?.runtimeBindings || {})], ([id, mapping]) => { if (bindingSession !== id || !bindingsChanged.value) { runtimeBindings.value = JSON.parse(mapping); bindingBase.value = mapping; bindingSession = id; } }, { immediate: true });
const panel = ref(null), message = ref(''), answer = ref(''), advancedText = ref(''), advancedBase = ref(''), localError = ref('');
let returnFocus;
const allowed = action => authoringAllowed(session.value, action);
const inputKey = () => accountDraftKey(props.ownerId, 'authoring-input', state.selectedId);
watch(() => state.selectedId, () => { message.value = readDraft(inputKey())?.message || ''; answer.value = ''; advancedText.value = ''; advancedBase.value = ''; localError.value = ''; acknowledgedUnknownRequests.value = []; selectedFileIds.value = []; }, { immediate: true });
watch(unknownRequests, rows => { acknowledgedUnknownRequests.value = acknowledgedUnknownRequests.value.filter(id => rows.some(item => item.id === id)); });
watch(message, value => { if (state.selectedId) writeDraft(inputKey(), { message: value }); });
watch(() => [candidate.value?.id, session.value?.draftContent], () => {
  if (!advancedBase.value || advancedText.value === advancedBase.value) advancedText.value = advancedBase.value = prettyAuthoring(editableContent.value || {});
}, { immediate: true });
const selectedSources = () => selectedFileIds.value.map(fileId => ({ kind: 'upload', reference: { fileId } }));
async function supplement(action) {
  const input = action === 'answer' ? answer : message, text = input.value.trim(), sources = selectedSources();
  const bindings = bindingsChanged.value ? JSON.parse(JSON.stringify(runtimeBindings.value)) : undefined;
  if (!text && !sources.length && bindings === undefined) return;
  const fallback = sources.length ? action === 'answer' ? '已补充本次授权文件，请按原问题继续。' : '已补充本次授权文件，请沿用原需求继续整理与验证。' : '已选择本人的连接，请按当前需求继续准备并重新检查权限。';
  const args = [session.value.id, text || fallback, sources]; if (bindings !== undefined) args.push(bindings);
  const result = await props.controller[action](...args);
  if (result) {
    if (input.value.trim() === text) input.value = '';
    selectedFileIds.value = selectedFileIds.value.filter(id => !sources.some(item => item.reference.fileId === id));
    if (bindings !== undefined) bindingBase.value = JSON.stringify(bindings);
  }
}
const append = () => supplement('append');
const respond = () => supplement('answer');
function resume(mode) { if (!unknownAcknowledged.value) return; const ids = acknowledgedUnknownRequests.value.filter(id => unknownRequests.value.some(item => item.id === id)); return ids.length ? props.controller.resume(session.value.id, mode, ids) : props.controller.resume(session.value.id, mode); }
async function confirm(mode) { const result = await props.controller.confirm(session.value.id, mode); if (result?.assetId) emit('asset', { kind: session.value.kind, assetId: result.assetId, versionId: result.versionId }); }
async function advancedSave() { try { const value = JSON.parse(advancedText.value); localError.value = ''; const result = await props.controller.editContent(session.value.id, value); if (result) advancedBase.value = advancedText.value; } catch { localError.value = '完整内容的格式有误，输入已保留。'; } }
function downloadCandidate() {
  if (!editableContent.value) return;
  const blob = new Blob([prettyAuthoring(editableContent.value)], { type: 'application/json' }), url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = (definition.value?.name || 'asset-draft') + '.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 0);
}
function close() { props.controller.close(); nextTick(() => returnFocus?.isConnected && returnFocus.focus()); }
function keydown(event) {
  if (event.key === 'Escape') { event.preventDefault(); close(); return; }
  if (event.key !== 'Tab') return;
  const controls = [...(panel.value?.querySelectorAll?.('button:not([disabled]),textarea:not([disabled]),input:not([disabled]),select:not([disabled]),summary,a[href]') || [])].filter(item => item.getClientRects().length);
  const first = controls[0], last = controls.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
}
onMounted(() => { returnFocus = globalThis.document?.activeElement; nextTick(() => panel.value?.focus?.()); });
onUnmounted(() => { connectionSequence++; connectionController.abort(); });
const effectLabel = value => ({ real:'实际执行', isolated:'隔离执行', simulated:'模拟检查' })[value] || '执行范围待核对';
const statusLabel = value => ({ passed:'通过', succeeded:'完成', failed:'未通过', running:'进行中', awaiting_review:'待效果审阅', queued:'等待', cancelled:'已取消', interrupted:'已中断', unknown:'结果待核实' })[value] || value;
</script>
<template>
  <div class="author-overlay" @click.self="close">
    <aside ref="panel" class="author-panel" role="dialog" aria-modal="true" aria-labelledby="author-detail-title" :data-authoring-session-id="session?.id" :data-authoring-revision="session?.revision" :data-authoring-candidate-id="candidate?.id" tabindex="-1" @keydown="keydown">
      <header class="author-heading"><div><span>{{ kindLabel(session?.kind) }} · {{ authoringPhases[session?.phase] || '读取创作记录' }}</span><h2 id="author-detail-title">{{ authoringTitle(session,candidate) }}</h2></div><button type="button" class="author-close" aria-label="关闭创作详情" @click="close">×</button></header>
      <div class="author-body">
        <p v-if="state.loading && !session" role="status">正在读取草稿与实际进度…</p>
        <div v-if="state.error || localError" class="author-error" role="alert"><p>{{ USER_FACING_ERROR }}</p><button v-if="state.pendingRetry" type="button" :disabled="actionBusy" @click="controller.retry">核实并恢复同一次提交</button><button type="button" :disabled="state.busy || state.loading" @click="controller.read(state.selectedId,false)">刷新实际状态</button></div>
        <template v-if="session">
          <p v-if="pendingChat" class="author-retained" role="status">聊天中的修改正在同步。下面仍是上一版结果，收到新草稿前暂不能确认保存。</p>
          <p v-if="state.notice" class="author-retained" role="status">{{ state.notice }}</p>
          <section class="author-budget" aria-label="本轮处理额度"><strong>{{ authoringPhases[session.phase] }}</strong><div><span>有效时间 {{ budget.consumed.activeMilliseconds == null ? '待同步' : Math.ceil(budget.consumed.activeMilliseconds / 1000) + ' / ' + Math.round((budget.limits.activeMilliseconds || 600000) / 1000) + ' 秒' }}</span><span>模型请求 {{ budget.consumed.modelRequests ?? '待同步' }} / {{ budget.limits.modelRequests ?? 32 }}</span><span>自动修复 {{ budget.consumed.repairRounds ?? '待同步' }} / {{ budget.limits.repairRounds ?? 2 }} 轮</span></div><small>等待你的回答不计时。刷新和追加修改不会重置本轮额度。</small><details v-if="budget.cumulative"><summary>累计使用情况</summary><pre>{{ prettyAuthoring(budget.cumulative) }}</pre></details></section>
          <p class="author-retained" role="status">{{ savedApp ? 'APP 已保存到我的应用池，当前未验证、未启用。请在应用详情继续修改和验证。' : '草稿与已完成结果已保留。' }}{{ savedApp ? '' : session.phase === 'completed' && detail.commit?.mode === 'draft_only' ? '尚未启用，可继续编辑或导出。' : '关闭详情不会取消后台处理。' }}</p>
          <button v-if="session.phase === 'committing' && state.pendingRetry && !state.error" type="button" class="secondary-button" :disabled="actionBusy" @click="controller.retry">核实并恢复原保存</button>
          <section v-if="unknownRequests.length" class="author-question" aria-label="未完成请求的恢复选择"><h3>这些请求的最终回复尚未核实</h3><p>若决定继续，请逐项确认放弃等待这些未完成回复。已发出的请求仍计入额度，恢复不会重放旧请求；会按当前要求产生新的工作。</p><label v-for="item in unknownRequests" :key="item.id" class="author-choice"><input v-model="acknowledgedUnknownRequests" type="checkbox" :value="item.id" :disabled="actionBusy" />放弃等待 {{ item.model || '模型请求' }} · {{ item.sentAt || '发送时间待核实' }}<small>{{ item.id }}</small></label></section>
          <section v-if="connectionRequirements.length && (allowed('messages') || allowed('answers'))" class="author-connection-supplement"><AssetConnectionBindings v-model="runtimeBindings" :requirements="connectionRequirements" :disabled="actionBusy || connectionsLoading" @manage="emit('connections')" /><p v-if="connectionsError" class="author-error" role="alert">{{ USER_FACING_ERROR }}</p><p v-if="connectionsLoading" role="status">正在读取本人连接…</p><button type="button" class="secondary-button" :disabled="connectionsLoading || actionBusy" @click="loadConnections">刷新我的连接</button><p>选择随下一次回答或修改要求提交，只用于这份草稿。不会继承外部作者的凭据或启用状态。</p></section>
          <details v-if="files.length && (allowed('messages') || allowed('answers'))" class="author-files"><summary>补充本次授权文件</summary><p>仅将勾选的本人文件加入这份草稿，随下一次回答或修改要求提交。不会自动增加处理额度。</p><label v-for="file in files" :key="file.id" class="author-choice"><input v-model="selectedFileIds" type="checkbox" :value="file.id" :disabled="actionBusy" />{{ file.name }}</label></details>
          <section v-if="session.question" class="author-question"><h3>{{ session.question.kind === 'authorization' ? '需要你的授权' : '补充这一项即可继续' }}</h3><p>{{ session.question.prompt }}</p><form @submit.prevent="respond"><label>回答<textarea v-model="answer" rows="3" :disabled="actionBusy" /></label><button type="submit" class="primary-button" :disabled="actionBusy || !allowed('answers') || (!answer.trim() && !selectedFileIds.length && !bindingsChanged)">提交回答</button></form><small>回答不会自动增加处理额度；授权仍按实际权限核对。</small></section>
          <section v-if="definition" class="author-summary"><h3>{{ definition.name || definition.manifest?.name || '当前候选方法' }}</h3><p>{{ definition.description || definition.manifest?.description }}</p><template v-if="definition.rules?.length"><h4>固定规则</h4><ul><li v-for="(rule,index) in definition.rules" :key="index">{{ rule }}</li></ul></template><details v-if="definition.inputSchema"><summary>每次可更换的输入</summary><pre>{{ prettyAuthoring(definition.inputSchema) }}</pre></details><p v-if="(candidate.issues || definition.gaps || []).length" class="author-warning">{{ USER_FACING_ERROR }}</p></section>
          <section v-if="cases.length || detail.testPlan" class="author-results" aria-label="实际验证结果"><h3>示例与实际效果</h3><p>检查原需求和一个不同输入。真实执行与模拟结果分别标明；尚未完成的检查不会作为通过。</p><article v-for="(item,index) in cases" :key="item.id || index"><header><strong>{{ item.label || '用例 ' + (index + 1) }}</strong><span>{{ statusLabel(item.status) }} · {{ effectLabel(item.executionMode) }}</span></header><p v-if="item.error" class="author-warning">{{ USER_FACING_ERROR }}</p><CapabilityResult v-if="!item.error && item.status !== 'failed' && (item.result != null || item.output != null)" :value="item.result ?? item.output" /><div v-if="item.artifacts?.length" class="author-artifacts"><template v-for="artifact in item.artifacts" :key="artifact.id || artifact.name"><a v-if="artifactUrl(artifact.downloadUrl)" :href="artifactUrl(artifact.downloadUrl)" download>{{ artifact.name }} ↓</a><img v-if="artifactUrl(artifact.downloadUrl) && ['image/png','image/jpeg','image/webp'].includes(artifact.mediaType)" :src="artifactUrl(artifact.downloadUrl)" :alt="artifact.name" loading="lazy" /></template></div><details><summary>输入、检查标准与依据</summary><pre v-if="!item.error && item.status !== 'failed'">{{ prettyAuthoring(item) }}</pre><p v-else>{{ USER_FACING_ERROR }}</p></details></article><details v-if="detail.testPlan"><summary>完整测试计划与独立预期</summary><pre>{{ prettyAuthoring(detail.testPlan) }}</pre></details><p v-if="evidence.some(item => item.executionMode === 'simulated')">模拟检查只证明所展示范围，不代表真实上游验证或生产写入授权。</p></section>
          <section v-if="detail.commit || savedApp" class="author-commit" aria-label="保存结果"><h3>{{ savedApp ? '已保存到我的 APP · 未验证' : detail.commit.mode === 'draft_only' ? '草稿已保留' : detail.commit.stage === 'completed' ? '已保存并可使用' : '保存进度' }}</h3><p v-if="savedApp">保存不会自动运行或启用。请打开应用详情检查实际步骤，再完成验证。</p><p v-else-if="detail.commit.mode !== 'draft_only' && ['version_saved','evidence_linked','failed'].includes(detail.commit.stage)">{{ detail.commit.targetVersionId ? '版本已保存；启用状态仍需核对。' : '提交尚未全部完成，原可用版本保持不变。' }}</p><p v-if="detail.commit?.error">{{ USER_FACING_ERROR }}</p><small v-if="savedApp?.versionId || detail.commit?.targetVersionId">版本 {{ savedApp?.versionId || detail.commit?.targetVersionId }}</small><button v-if="savedApp || detail.commit?.targetAssetId" type="button" class="secondary-button" @click="emit('asset',{kind:session.kind,assetId:savedApp?.assetId || detail.commit.targetAssetId,versionId:savedApp?.versionId || detail.commit.targetVersionId})">{{ savedApp ? '打开应用详情' : '打开资产' }}</button></section>
          <div class="author-actions"><button v-if="session.kind !== 'app' && allowed('save_and_enable')" type="button" class="primary-button" :disabled="actionBusy || !detail.reviewSnapshot" @click="confirm('save_and_enable')">确认保存并使用</button><button v-if="allowed('draft_only')" type="button" class="secondary-button" :disabled="actionBusy" @click="confirm('draft_only')">只存草稿</button><button v-if="allowed('resume')" type="button" class="secondary-button" :disabled="actionBusy || !unknownAcknowledged" @click="resume('resume')">按剩余额度继续</button><button v-if="allowed('extend_budget')" type="button" class="primary-button" :disabled="actionBusy || !budget.confirmationId || !unknownAcknowledged" @click="resume('extend_budget')">确认再处理一轮（10分钟 / 32次请求 / 2轮修复）</button><button v-if="allowed('cancel')" type="button" class="secondary-button" :disabled="actionBusy" @click="controller.cancel(session.id)">停止准备，保留草稿</button></div>
          <button v-if="allowed('messages') && session.conversationId" type="button" class="secondary-button" @click="emit('chat',session)">在对话中继续修改</button>
          <form v-if="allowed('messages')" class="author-revise" @submit.prevent="append"><label>继续调整这份草稿<textarea v-model="message" rows="4" placeholder="直接说想保留或改变的内容，系统会沿用当前目标和已整理的资料。" :disabled="actionBusy" /></label><p>追加要求会停止过时检查，使用本轮剩余额度。</p><button type="submit" class="primary-button" :disabled="actionBusy || (!message.trim() && !selectedFileIds.length && !bindingsChanged)">提交修改要求</button></form>
          <details v-if="candidate" class="author-technical"><summary>方法、资源与来源</summary><AssetDefinitionView v-if="session.kind === 'app' && definition?.nodes" :definition="definition" /><template v-else><details v-for="file in resources" :key="file.path"><summary>{{ file.path }}</summary><pre v-if="file.displayText != null">{{ file.displayText }}</pre><p v-else>原始文件已保留。</p></details></template><h4>本次变化</h4><pre>{{ prettyAuthoring(candidate.diff || []) }}</pre><h4>来源与范围</h4><p v-for="source in detail.sources || []" :key="source.id">{{ ({observed:'可核实来源记录',inferred:'根据记录推断',generated:'本轮新生成'})[source.claim] || '来源待核对' }} · {{ ({sub_agent_run:'任务执行',conversation:'对话',asset_version:'资产版本',import_draft:'导入原件或已有草稿',upload:'授权文件'})[source.kind] || source.kind }}<small v-if="source.objectiveCompleted === false">原任务未全部完成，不能据此宣称原目标已交付。</small></p><pre>{{ prettyAuthoring(detail.sources || definition?.authoringProvenance || candidate.sourceIds || []) }}</pre><button type="button" class="secondary-button" @click="downloadCandidate">导出当前候选内容</button><small>候选导出不代表已验证或已启用。</small></details>
          <div v-if="session.draftContent && !candidate" class="author-technical"><p>当前保存的完整草稿尚未通过候选检查。</p><button type="button" class="secondary-button" @click="downloadCandidate">导出当前草稿内容</button></div>
          <details v-if="editableContent && allowed('messages')" class="author-technical"><summary>高级：编辑完整内容</summary><p>修改实现会形成新候选并检查受影响部分；这里不会覆盖正式资产。</p><textarea v-model="advancedText" rows="16" aria-label="创作候选完整内容" spellcheck="false" :disabled="actionBusy" /><button type="button" class="secondary-button" :disabled="actionBusy || advancedText === advancedBase" @click="advancedSave">提交完整内容修改</button></details>
        </template>
      </div>
    </aside>
  </div>
</template>
<style scoped>
.author-overlay{position:fixed;inset:0;z-index:70;background:#112d3a33;display:flex;justify-content:flex-end}.author-panel{width:min(720px,72vw);height:100%;background:var(--surface,#fff);box-shadow:-12px 0 50px #1236441a;display:flex;flex-direction:column;outline:0;color:var(--text,#17374a)}.author-heading{display:flex;gap:20px;justify-content:space-between;padding:24px 28px;border-bottom:1px solid var(--line,#d8e3e9)}.author-heading>div{min-width:0}.author-heading span{font-size:11px;color:var(--accent,#34758f)}.author-heading h2{font-size:21px;line-height:1.6;overflow-wrap:anywhere;margin:8px 0 0;max-height:110px;overflow:auto}.author-close{align-self:flex-start;background:none;border:0;color:inherit;font-size:28px;cursor:pointer;padding:0 8px}.author-body{padding:24px 28px 60px;overflow:auto;min-height:0}.author-body p,.author-body li{font-size:13px;line-height:1.85;overflow-wrap:anywhere}.author-body h3{font-size:17px;line-height:1.7}.author-body h4{font-size:13px}.author-body small{display:block;font-size:11px;line-height:1.8;color:var(--muted,#657b89)}.author-budget{padding:16px 18px;border:1px solid var(--line,#d8e3e9);border-radius:10px;background:var(--surface-2,#f5f8fa)}.author-budget>strong{font-size:13px}.author-budget>div{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0;font-size:12px}.author-retained{color:var(--muted,#657b89)}.author-question{border:1px solid #c6dfe8;background:#f3fafc;border-radius:10px;padding:18px;margin:22px 0}.author-body label{display:grid;gap:8px;font-size:13px}.author-body textarea{width:100%;box-sizing:border-box;resize:vertical;border:1px solid var(--line,#d8e3e9);border-radius:8px;background:var(--surface-2,#f8fafb);color:inherit;padding:12px;font:inherit;font-size:13px;line-height:1.8}.author-question button{margin:12px 0}.author-summary{padding:12px 0}.author-results{border-top:1px solid var(--line,#d8e3e9);padding-top:18px}.author-results>article{padding:16px;border:1px solid var(--line,#d8e3e9);border-radius:10px;margin:16px 0;overflow-wrap:anywhere}.author-results article>header{display:flex;gap:12px;flex-wrap:wrap;justify-content:space-between;font-size:12px;margin-bottom:14px}.author-results article>header span{color:var(--muted,#657b89)}.author-body details{margin:14px 0;font-size:12px}.author-body summary{cursor:pointer;line-height:1.8;color:var(--accent,#34758f)}.author-body pre{font:11px/1.75 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;max-height:350px;overflow:auto;background:var(--surface-2,#f5f8fa);padding:13px;border-radius:8px}.author-actions{display:flex;gap:9px;flex-wrap:wrap;margin:22px 0}.author-body button{font-size:12px}.author-body button:disabled{opacity:.5;cursor:not-allowed}.author-revise{padding:20px;border:1px solid var(--line,#d8e3e9);border-radius:12px;margin:24px 0}.author-revise p{font-size:11px;color:var(--muted,#657b89)}.author-technical{border-top:1px solid var(--line,#d8e3e9);padding-top:15px}.author-technical button{margin-top:14px}.author-body .author-choice{display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:12px 0;font-size:12px;overflow-wrap:anywhere}.author-choice input{flex:none}.author-files{padding:14px;border:1px solid var(--line,#d8e3e9);border-radius:8px}.author-artifacts{display:flex;flex-wrap:wrap;gap:10px;margin:12px 0}.author-artifacts a{color:var(--accent,#34758f);font-size:12px}.author-artifacts img{max-width:100%;height:auto;border-radius:8px}.author-warning,.author-error{color:var(--danger,#9c573e)}.author-error{background:#fff4ed;border-radius:8px;padding:14px;margin-bottom:18px}.author-error button{margin:6px 8px 0 0}.author-commit{background:#eef6f0;padding:15px;border-radius:9px;margin:18px 0}.author-commit button{margin-top:12px}.author-panel :focus-visible{outline:2px solid var(--accent,#34758f);outline-offset:3px}@media(max-width:767px){.author-panel{width:100vw}.author-heading,.author-body{padding-left:18px;padding-right:18px}.author-actions button{width:100%}.author-heading h2{font-size:18px}}
</style>
