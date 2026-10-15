<script setup>
import AgentRunCard from './AgentRunCard.vue';
import AgentInputForm from './AgentInputForm.vue';
const props = defineProps({ platform: Object, files: Array, catalog: Boolean });
const emit = defineEmits(['upload', 'open-task']);
</script>
<template>
  <div class="agent-platform">
    <div v-if="platform.p.error" class="agent-error" role="alert">{{ platform.p.error }} <button @click="platform.refresh">重新同步</button></div>
    <template v-if="catalog">
      <p class="agent-muted">选择预置或个人应用，为本次运行填写新输入。也可以在新任务中描述需求，创建自己的应用。</p>
      <p v-if="platform.p.runtime?.executor?.available === false" class="agent-error">执行环境暂不可用：{{ platform.p.runtime.executor.reason }}</p>
      <div class="agent-catalog"><article v-for="entry in platform.p.agents" :key="entry.agentRef" class="agent-catalog-card"><span class="tag">{{ entry.personal ? '个人应用' : '预置应用' }}</span><h3>{{ entry.spec.name }}</h3><p>{{ entry.spec.description || entry.spec.goal }}</p><small class="agent-muted">版本 {{ entry.spec.version }}</small><button class="primary-button" @click="platform.selectEntry(entry)">填写参数并运行</button></article></div>
      <p v-if="!platform.p.agents.length && !platform.p.loading" class="agent-muted">还没有应用。完成一次新流程运行后，可以将它保存到这里。</p>
      <section v-if="platform.p.selected" class="agent-run-card"><header class="agent-card-head"><h3>{{ platform.p.selected.spec.name }} · 版本 {{ platform.p.selected.spec.version }}</h3><button class="secondary-button" :disabled="platform.busy('invoke')" @click="platform.selectEntry(null)">关闭</button></header><AgentInputForm :key="platform.p.ownerId + platform.p.selected.agentRef" :draft-key="platform.draftKey('invoke', platform.p.selected.agentRef)" :spec="platform.p.selected.spec" :files="files" fresh :busy="platform.busy('invoke')" submit-label="用这些输入直接运行" @submit="platform.invoke(platform.p.selected, $event)" @upload="emit('upload', $event)" /></section>
      <h2 class="agent-section-title">运行记录</h2>
    </template>
    <div v-if="platform.p.loading && !platform.p.runs.length" class="agent-muted">正在读取应用状态…</div>
    <AgentRunCard v-for="run in platform.p.runs" :key="platform.p.ownerId + run.id" :owner-id="platform.p.ownerId" :run="run" :files="files" :busy="platform.busy(run.id)" @approve="(approval, decision) => platform.approve(run, approval, decision)" @cancel="platform.cancel(run)" @supply="platform.supply(run, $event)" @feedback="platform.feedback(run, $event)" @save="platform.save(run)" @revise="platform.revise(run, $event)" @upload="emit('upload', $event)" @open-task="emit('open-task', $event)" />
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
