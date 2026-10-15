<script setup>
import { ref, computed, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import AgentRunCard from './AgentRunCard.vue';
import AgentInputForm from './AgentInputForm.vue';
import ReleasePublisher from './ReleasePublisher.vue';
const props = defineProps({ platform: Object, files: Array, catalog: Boolean, unifiedAuthoring: Boolean });
const emit = defineEmits(['upload', 'open-task', 'author', 'open-asset']);
const catalogScope = ref('all'), query = ref(''), adapting = ref(null), goal = ref(''), components = ref([]), publishing = ref(null), notice = ref('');
const entries = computed(() => props.platform.p.agents.filter(entry => (catalogScope.value === 'all' || entry.personal === (catalogScope.value === 'personal')) && (entry.spec.name + entry.spec.description + entry.spec.goal).toLowerCase().includes(query.value.toLowerCase())));
watch(() => props.platform.p.ownerId, () => { adapting.value = null; publishing.value = null; goal.value = ''; components.value = []; notice.value = ''; });
async function beginAdapt(entry) { if (props.unifiedAuthoring) { emit('author', { kind: 'app', intent: 'upgrade', sourceRefs: [{ kind: 'asset_version', reference: { legacy: { kind: 'pipeline', agentRef: entry.agentRef } } }] }); return; } adapting.value = entry; goal.value = ''; components.value = []; publishing.value = null; props.platform.selectEntry(null); await props.platform.loadComponents(); }
function saveRun(run) { if (!props.unifiedAuthoring) return props.platform.save(run); emit('author', { kind: 'app', intent: 'upgrade', instruction: '保留这次旧流程的规则与实现，整理可复用应用并验证。', sourceRefs: [{ kind: 'asset_version', reference: { legacy: { kind: 'pipeline', runId: run.id } } }] }); }
function reviseRun(run, values = {}) { if (!props.unifiedAuthoring) return props.platform.revise(run, values); emit('author', { kind: 'app', intent: 'upgrade', instruction: values.goal || '', sourceRefs: [{ kind: 'asset_version', reference: { legacy: { kind: 'pipeline', runId: run.id } } }, ...(values.fileIds || []).map(fileId => ({ kind: 'upload', reference: { fileId } }))] }); }
async function adapt(values) { const entry = adapting.value; const result = await props.platform.adapt(entry, { ...values, goal: goal.value.trim(), capabilityRefs: props.platform.p.components.filter(item => components.value.includes(item.assetId)).map(({ assetId, versionId }) => ({ assetId, versionId })) }); if (result) adapting.value = null; }
async function copyPreset(entry) {
  const result = await props.platform.copyPreset(entry);
  if (result?.assetId && result?.versionId) emit('open-asset', { appId: result.assetId, versionId: result.versionId });
}
</script>
<template>
  <div class="agent-platform">
    <div v-if="platform.p.error" class="agent-error" role="alert">{{ USER_FACING_ERROR }} <button @click="platform.refresh">重新同步</button></div>
    <template v-if="catalog">
      <header class="agent-library-heading"><div><span class="agent-library-eyebrow">REUSABLE WORKFLOWS</span><h2>流程应用</h2><p class="agent-muted">复用已定义的执行步骤，组合自己的组件，或用对话改编一个新版本。</p></div><div class="agent-library-filter"><select v-model="catalogScope" aria-label="应用来源"><option value="all">全部流程</option><option value="personal">我的流程</option><option value="preset">平台预置</option></select><input v-model="query" type="search" placeholder="搜索流程应用" aria-label="搜索流程应用" /></div></header>
      <p v-if="notice" class="agent-saved" role="status">{{ notice }}</p>
      <p v-if="platform.p.runtime?.executor?.available === false" class="agent-error">{{ USER_FACING_ERROR }}</p>
      <div class="agent-catalog"><article v-for="entry in entries" :key="`${entry.personal}:${entry.agentRef}`" class="agent-catalog-card"><span class="tag">{{ entry.personal ? '个人流程' : '平台预置' }}</span><p v-if="!entry.personal && entry.personalCopies?.length" class="agent-saved">旧版流程副本 {{ entry.personalCopies.length }} 份</p><h3>{{ entry.spec.name }}</h3><p>{{ entry.spec.description || entry.spec.goal }}</p><small class="agent-muted">版本 {{ entry.spec.version }} · {{ entry.spec.pipeline?.length || 0 }} 个步骤</small><div class="agent-catalog-actions"><button class="primary-button" @click="platform.selectEntry(entry); adapting = null">填写参数并运行</button><button v-if="!entry.personal" class="secondary-button" :disabled="platform.busy('copy:' + entry.agentRef)" @click="copyPreset(entry)">添加为未验证 APP</button><button class="secondary-button" :disabled="platform.busy('adapt:' + entry.agentRef)" @click="beginAdapt(entry)">对话改编</button></div><div class="agent-catalog-links"><button v-if="entry.personal" type="button" :disabled="platform.busy('fork:' + entry.agentRef)" @click="unifiedAuthoring ? beginAdapt(entry) : platform.fork(entry)">复制</button><a :href="'/desktop/api/agents/' + encodeURIComponent(entry.spec.id) + '/versions/' + encodeURIComponent(entry.spec.version) + '/export'" download>导出应用包</a><button v-if="entry.personal" type="button" @click="publishing = entry; adapting = null">发布到共创</button></div></article></div>
      <p v-if="!platform.p.agents.length && !platform.p.loading" class="agent-muted">还没有应用。完成一次新流程运行后，可以将它保存到这里。</p>
      <section v-if="platform.p.selected" class="agent-run-card"><header class="agent-card-head"><h3>{{ platform.p.selected.spec.name }} · 版本 {{ platform.p.selected.spec.version }}</h3><button class="secondary-button" :disabled="platform.busy('invoke')" @click="platform.selectEntry(null)">关闭</button></header><AgentInputForm :key="platform.p.ownerId + platform.p.selected.agentRef" :draft-key="platform.draftKey('invoke', platform.p.selected.agentRef)" :spec="platform.p.selected.spec" :context-draft-key="platform.draftKey('invoke-goal', platform.p.selected.agentRef)" :show-context="platform.contextFor(platform.p.selected) !== undefined" :files="files" fresh :busy="platform.busy('invoke')" submit-label="用这些输入直接运行" @submit="platform.invoke(platform.p.selected, $event)" @upload="emit('upload', $event)" /></section>
      <section v-if="adapting" class="agent-run-card"><header class="agent-card-head"><div><span class="agent-library-eyebrow">BUILD ON EXISTING WORK</span><h3>从「{{ adapting.spec.name }}」继续构建</h3></div><button class="secondary-button" :disabled="platform.busy('adapt:' + adapting.agentRef)" @click="adapting = null">收起</button></header><p class="agent-muted">保留原应用，生成独立的新流程。新流程需要你审阅并确认后才执行。</p><label class="form-field"><span>希望保留什么，改变什么？</span><textarea v-model="goal" rows="4" placeholder="例如：保留取数步骤，增加收益率比较，并输出可下载图表。" /></label><details class="agent-component-picker"><summary>组合我的组件 · {{ components.length }} 项已选</summary><p class="agent-muted">可加入已启用且当前可用的 Skill 或 Tool；原流程的依赖仍会被重新检查。</p><p v-if="!platform.p.components.length" class="agent-muted">还没有可用的个人组件，请在技能池中核对版本、验证要求并启用。</p><label v-for="component in platform.p.components" :key="component.assetId"><input v-model="components" type="checkbox" :value="component.assetId" :disabled="!component.available" /><span>{{ component.name }} <small>{{ component.kind }} · {{ component.available ? '可用' : component.reason ? USER_FACING_ERROR : '暂不可用' }}</small></span></label></details><div class="agent-actions"><button type="button" class="primary-button" :disabled="!goal.trim() || platform.busy('adapt:' + adapting.agentRef)" @click="adapt({ input: {}, fileIds: [] })">先生成流程，再补参数</button></div><details><summary>可选：沿用原表单提供本次输入</summary><AgentInputForm :key="'adapt:' + adapting.agentRef" :draft-key="platform.draftKey('adapt', adapting.agentRef)" :spec="adapting.spec" :files="files" fresh :busy="!goal.trim() || platform.busy('adapt:' + adapting.agentRef)" submit-label="生成改编流程" @submit="adapt" @upload="emit('upload', $event)" /></details></section>
      <ReleasePublisher v-if="publishing" :key="publishing.agentRef" endpoint="/evolution/publications" :payload="{ kind: 'pipeline', agentRef: publishing.agentRef }" :name="publishing.spec.name" :description="publishing.spec.description" @close="publishing = null" @published="publishing = null; notice = '已发布到共创目录，其他平台账号可以复制改编。'" />
      <h2 class="agent-section-title">运行记录</h2>
    </template>
    <div v-if="platform.p.loading && !platform.p.runs.length" class="agent-muted">正在读取应用状态…</div>
    <AgentRunCard v-for="run in platform.p.runs" :key="platform.p.ownerId + run.id" :owner-id="platform.p.ownerId" :run="run" :files="files" :unified-authoring="unifiedAuthoring" :busy="platform.busy(run.id)" @approve="(approval, decision) => platform.approve(run, approval, decision)" @cancel="platform.cancel(run)" @supply="platform.supply(run, $event)" @feedback="platform.feedback(run, $event)" @save="saveRun(run)" @revise="reviseRun(run, $event)" @upload="emit('upload', $event)" @open-task="emit('open-task', $event)" />
    <p v-if="catalog && !platform.p.loading && !platform.p.runs.length" class="agent-muted">暂无运行记录。</p>
  </div>
</template>
<style>
.agent-platform { width:100%; max-width:960px; min-width:0; margin:20px auto; color:var(--text); }
.agent-run-card,.agent-catalog-card { min-width:0; background:var(--surface); color:var(--text); border:1px solid var(--line); border-radius:var(--radius-panel,24px); padding:22px; margin:18px 0; overflow-wrap:anywhere; box-shadow:var(--shadow-panel,0 16px 44px rgb(27 57 78 / 6%)); }
.agent-card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.agent-card-head > div { min-width:0; }
.agent-card-head h3 { margin:0 0 8px; font-size:18px; }
.agent-status { flex:none; border-radius:20px; background:var(--surface-2); color:var(--text); padding:5px 10px; white-space:nowrap; font-size:12px; }
.agent-status.succeeded { color:var(--success-ink,#236744); background:var(--success-wash,#edf8f0); }
.agent-saved { color:var(--success-ink,#236744); }
.agent-status.failed,.agent-status.interrupted { color:var(--danger,#b42332); background:var(--error-wash,#fff0f2); }
.agent-status.awaiting_input,.agent-status.awaiting_confirmation,.agent-status.awaiting_authorization { color:var(--warning-ink,#865714); background:var(--warning-wash,#fff7e8); }
.agent-status.generating,.agent-status.queued,.agent-status.running { color:var(--build-ink,#103c84); background:var(--build-wash,#edf6ff); }
.agent-status.cancelling { color:var(--warning-ink,#865714); background:var(--warning-wash,#fff7e8); }
.agent-status.cancelled { color:var(--muted,#596a7e); background:var(--surface-2,#f2f6f8); }
.agent-preview-note { background:var(--warning-wash,#fff7e8); color:var(--warning-ink,#865714); border-left:3px solid #b67a28; padding:12px; margin:14px 0; font-size:14px; }
.agent-muted { color:var(--muted); font-size:13px; line-height:1.6; }
.agent-error { padding:12px; color:var(--danger,#b42332); background:var(--error-wash,#fff0f2); border:1px solid #efcbd0; border-radius:var(--radius-small,12px); white-space:pre-wrap; }
.agent-error button { color:var(--text); border:1px solid var(--line); border-radius:6px; padding:5px 10px; background:var(--surface-2); }
.agent-pipeline { padding-left:24px; }
.agent-pipeline li { padding:10px 0; }
.agent-pipeline li>span { margin-left:12px; }
.agent-pipeline p { margin:6px 0; }
.agent-actions { display:flex; gap:10px; flex-wrap:wrap; margin:12px 0; }
.agent-platform details { margin:14px 0; min-width:0; }
.agent-platform summary { cursor:pointer; font-size:13px; font-weight:600; color:var(--text); }
.agent-platform summary:focus-visible { outline:2px solid var(--accent); outline-offset:4px; }
.agent-platform pre { white-space:pre-wrap; overflow-wrap:anywhere; background:var(--panel); color:var(--text); border:1px solid var(--line); padding:14px; border-radius:8px; max-height:340px; overflow:auto; font-size:12px; }
.agent-platform textarea,.agent-platform select,.agent-platform input:not([type=checkbox]) { width:100%; min-width:0; padding:10px; border:1px solid var(--line); border-radius:var(--radius-input,14px); background:var(--surface-2); color:var(--text); font:inherit; box-sizing:border-box; }
.agent-platform textarea:focus-visible,.agent-platform select:focus-visible,.agent-platform input:focus-visible { outline:2px solid var(--focus-ring,#146aa3); outline-offset:2px; }
.agent-platform textarea::placeholder,.agent-platform input::placeholder { color:var(--muted); }
.agent-platform textarea { margin-top:10px; resize:vertical; }
.agent-platform input[type=checkbox] { width:20px; height:20px; accent-color:var(--accent); }
.agent-input-form { margin-top:18px; }
.agent-input-form .form-field { margin-bottom:16px; }
.agent-input-form .form-field>span { color:var(--text); }
.agent-input-form b { color:var(--danger,#b42332); }
.agent-approval { padding:16px; background:var(--accent-dark,#e5f4f2); color:var(--text); border:1px solid #c7e5e2; border-radius:var(--radius-input,14px); margin:16px 0; }
.agent-result p { white-space:pre-wrap; line-height:1.8; }
.agent-artifacts a { display:inline-block; margin:6px 12px 6px 0; color:var(--accent,#087b80); text-decoration:underline; }
.agent-file-list { display:flex; gap:8px; flex-wrap:wrap; font-size:12px; }
.agent-file-list span { min-width:0; max-width:100%; overflow-wrap:anywhere; background:var(--surface-2); color:var(--text); padding:6px 10px; border-radius:var(--radius-small,12px); }
.agent-parameters { display:grid; grid-template-columns:minmax(80px,1fr) 3fr; gap:8px; font-size:13px; }
.agent-parameters dd { margin:0; min-width:0; white-space:pre-wrap; overflow-wrap:anywhere; }
.agent-catalog { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr)); gap:16px; margin:20px 0; }
.agent-catalog-card { margin:0; padding:20px; }
.agent-catalog-card h3 { margin:12px 0; color:var(--text); }
.agent-catalog-card p { font-size:14px; line-height:1.7; color:var(--muted); }
.agent-catalog-card button { display:block; margin-top:16px; }
.agent-section-title { font-size:20px; margin-top:30px; }
.agent-mode-toggle { display:flex; gap:12px; align-items:center; margin:12px auto; max-width:900px; font-size:13px; color:var(--text); }
.agent-mode-toggle select { min-width:0; max-width:100%; padding:7px 10px; border:1px solid var(--line); border-radius:var(--radius-small,12px); background:var(--surface-2); color:var(--text); }
.agent-mode-toggle small { color:var(--muted); }
@media(max-width:600px) { .agent-run-card{padding:15px}.agent-card-head{flex-wrap:wrap}.agent-mode-toggle{flex-wrap:wrap;padding:0 12px}.agent-platform{margin:12px auto}.agent-actions>button{min-height:40px}.agent-parameters{grid-template-columns:1fr}.agent-parameters dt{color:var(--muted)} }
</style>

<style scoped>
.agent-library-heading{display:flex;justify-content:space-between;gap:24px;align-items:end;border-top:1px solid var(--line);padding-top:28px}.agent-library-eyebrow{font-size:10px;letter-spacing:.14em;color:var(--build-ink)}.agent-library-heading h2{margin:8px 0;font-size:24px;font-weight:600}.agent-library-heading p{margin:0;max-width:500px}.agent-library-filter{display:flex;gap:8px;max-width:340px}.agent-library-filter select,.agent-library-filter input{font-size:12px;width:auto;min-width:0}.agent-catalog-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:18px}.agent-catalog-actions button{margin:0;font-size:12px}.agent-catalog-links{display:flex;gap:14px;flex-wrap:wrap;margin-top:18px}.agent-catalog-links button,.agent-catalog-links a{display:inline;padding:0;border:0;background:transparent;color:var(--build-ink);text-decoration:underline;font-size:11px;margin:0;cursor:pointer}.agent-component-picker{padding:16px;background:var(--surface-2);border:1px solid var(--line);border-radius:12px}.agent-component-picker label{display:flex;align-items:start;gap:10px;padding:9px 0;font-size:13px}.agent-component-picker label>input{flex:none;margin-top:2px}.agent-component-picker small{display:block;color:var(--muted);font-size:11px;line-height:1.8}.agent-component-picker label:has(input:disabled){opacity:.65}@media(max-width:700px){.agent-library-heading{flex-direction:column;align-items:start}.agent-library-filter{width:100%;max-width:none}.agent-library-filter input{flex:1}}
</style>
