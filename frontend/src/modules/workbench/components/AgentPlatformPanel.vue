<script setup>
import AgentRunCard from './AgentRunCard.vue';
import AgentInputForm from './AgentInputForm.vue';
const props = defineProps({ platform: Object, files: Array, catalog: Boolean });
const emit = defineEmits(['upload', 'open-task', 'open-strategy-app']);
</script>
<template>
  <div class="agent-platform">
    <div v-if="platform.p.error" class="agent-error" role="alert">{{ platform.p.error }} <button @click="platform.refresh">重新同步</button></div>
    <template v-if="catalog">
      <p class="agent-muted">选择预置或个人应用，也可以打开已保存的策略应用，查看策略规则、代码和回测结果。</p>
      <p v-if="platform.p.runtime?.executor?.available === false" class="agent-error">执行环境暂不可用：{{ platform.p.runtime.executor.reason }}</p>
      <div class="agent-catalog"><article v-for="entry in platform.p.agents" :key="entry.agentRef" class="agent-catalog-card"><span class="tag">{{ entry.personal ? '个人应用' : '预置应用' }}</span><h3>{{ entry.spec.name }}</h3><p>{{ entry.spec.description || entry.spec.goal }}</p><small class="agent-muted">版本 {{ entry.spec.version }}</small><p v-if="entry.workshop" class="agent-muted" data-workshop-status>应用工坊创建 · 保存时尚未运行<span v-if="entry.workshop.extraRequirements?.length"> · 有资源需要准备</span></p><button class="primary-button" @click="platform.selectEntry(entry)">填写参数并运行</button></article><article v-for="app in platform.p.strategyApps" :key="app.id" class="agent-catalog-card" :data-strategy-app-id="app.id"><span class="tag">个人策略应用</span><h3>{{ app.name }}</h3><p>{{ app.description }}</p><small class="agent-muted">{{ app.strategyKind === 'custom' ? '自定义策略' : '模板策略' }} · {{ app.symbol }}</small><p class="agent-muted">保留策略规则和代码，可重新设置参数回测</p><button type="button" class="primary-button" data-action="open-strategy-app" @click="emit('open-strategy-app', app)">打开策略应用</button></article></div>
      <p v-if="!platform.p.agents.length && !platform.p.strategyApps?.length && !platform.p.loading" class="agent-muted">还没有应用。可以到应用工坊描述需求，创建并保存自己的应用。</p>
      <section v-if="platform.p.selected" class="agent-run-card"><header class="agent-card-head"><h3>{{ platform.p.selected.spec.name }} · 版本 {{ platform.p.selected.spec.version }}</h3><button class="secondary-button" :disabled="platform.busy('invoke')" @click="platform.selectEntry(null)">关闭</button></header><p v-if="platform.p.selected.workshop" class="agent-preview-note" data-workshop-saved>已保存到我的应用池 · 保存时尚未运行</p><div v-if="platform.p.selected.workshop?.extraRequirements?.length" class="agent-preview-note"><strong>使用前还需要准备</strong><ul><li v-for="(item, i) in platform.p.selected.workshop.extraRequirements" :key="i">{{ item.name }}：{{ item.purpose }}</li></ul></div><button v-if="platform.hasPreviousDraft(platform.p.selected)" class="secondary-button" data-action="restore-agent-draft" :disabled="platform.busy('invoke')" @click="platform.restorePreviousDraft(platform.p.selected)">恢复上一次填写的内容</button><AgentInputForm :key="platform.p.ownerId + platform.p.selected.agentRef" :draft-key="platform.draftKey('invoke', platform.p.selected.agentRef)" :spec="platform.p.selected.spec" :context-draft-key="platform.draftKey('invoke-goal', platform.p.selected.agentRef)" :show-context="Boolean(platform.p.selected.workshop) || platform.contextFor(platform.p.selected) !== undefined" :files="files" fresh :busy="platform.busy('invoke')" submit-label="用这些输入直接运行" @submit="platform.invoke(platform.p.selected, $event)" @upload="emit('upload', $event)" /></section>
      <h2 class="agent-section-title">运行记录</h2>
    </template>
    <div v-if="platform.p.loading && !platform.p.runs.length" class="agent-muted">正在读取应用状态…</div>
    <AgentRunCard v-for="run in platform.p.runs" :key="platform.p.ownerId + run.id" :owner-id="platform.p.ownerId" :run="run" :files="files" :busy="platform.busy(run.id)" @approve="(approval, decision) => platform.approve(run, approval, decision)" @cancel="platform.cancel(run)" @supply="platform.supply(run, $event)" @feedback="platform.feedback(run, $event)" @save="platform.save(run)" @revise="platform.revise(run, $event)" @upload="emit('upload', $event)" @open-task="emit('open-task', $event)" />
    <p v-if="catalog && !platform.p.loading && !platform.p.runs.length" class="agent-muted">暂无运行记录。</p>
  </div>
</template>
<style>
.agent-platform { width:100%; max-width:960px; min-width:0; margin:20px auto; color:var(--text); }
.agent-run-card,.agent-catalog-card { background:var(--surface); color:var(--text); border:1px solid var(--line); border-radius:16px; padding:22px; margin:18px 0; overflow-wrap:anywhere; }
.agent-card-head { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; }
.agent-card-head h3 { margin:0 0 8px; font-size:18px; }
.agent-status { border-radius:20px; background:var(--surface-2); color:var(--text); padding:5px 10px; white-space:nowrap; font-size:12px; }
.agent-status.succeeded,.agent-saved { color:#a4dfb5; }
.agent-status.failed,.agent-status.interrupted { color:var(--danger); background:#3d2729; }
.agent-status.awaiting_confirmation,.agent-status.awaiting_authorization { color:#f2d18e; background:#3c3220; }
.agent-preview-note { background:#332c20; color:#f2d9a8; border-left:3px solid #d2a859; padding:12px; margin:14px 0; font-size:14px; }
.agent-muted { color:var(--muted); font-size:13px; line-height:1.6; }
.agent-error { padding:12px; color:var(--danger); background:#342425; border:1px solid #653b40; border-radius:8px; white-space:pre-wrap; }
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
.agent-platform textarea,.agent-platform select,.agent-platform input:not([type=checkbox]) { width:100%; min-width:0; padding:10px; border:1px solid #59534c; border-radius:8px; background:var(--surface-2); color:var(--text); font:inherit; box-sizing:border-box; }
.agent-platform textarea::placeholder,.agent-platform input::placeholder { color:var(--muted); }
.agent-platform textarea { margin-top:10px; resize:vertical; }
.agent-platform input[type=checkbox] { width:20px; height:20px; accent-color:var(--accent); }
.agent-input-form { margin-top:18px; }
.agent-input-form .form-field { margin-bottom:16px; }
.agent-input-form .form-field>span { color:var(--text); }
.agent-input-form b { color:#f2d18e; }
.agent-approval { padding:16px; background:var(--accent-dark); color:var(--text); border:1px solid #615473; border-radius:12px; margin:16px 0; }
.agent-result p { white-space:pre-wrap; line-height:1.8; }
.agent-artifacts a { display:inline-block; margin:6px 12px 6px 0; color:var(--accent-cyan,#8bd3d7); text-decoration:underline; }
.agent-file-list { display:flex; gap:8px; flex-wrap:wrap; font-size:12px; }
.agent-file-list span { background:var(--surface-2); color:var(--text); padding:6px 10px; border-radius:6px; }
.agent-parameters { display:grid; grid-template-columns:minmax(80px,1fr) 3fr; gap:8px; font-size:13px; }
.agent-parameters dd { margin:0; min-width:0; white-space:pre-wrap; }
.agent-catalog { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(230px,100%),1fr)); gap:16px; margin:20px 0; }
.agent-catalog-card { margin:0; padding:20px; }
.agent-catalog-card h3 { margin:12px 0; color:var(--text); }
.agent-catalog-card p { font-size:14px; line-height:1.7; color:var(--muted); }
.agent-catalog-card button { display:block; margin-top:16px; }
.agent-section-title { font-size:20px; margin-top:30px; }
.agent-mode-toggle { display:flex; gap:12px; align-items:center; margin:12px auto; max-width:900px; font-size:13px; color:var(--text); }
.agent-mode-toggle select { padding:7px 10px; border:1px solid #59534c; border-radius:8px; background:var(--surface-2); color:var(--text); }
.agent-mode-toggle small { color:var(--muted); }
@media(max-width:600px) { .agent-run-card{padding:15px}.agent-card-head{flex-wrap:wrap}.agent-mode-toggle{flex-wrap:wrap;padding:0 12px}.agent-platform{margin:12px auto}.agent-actions>button{min-height:40px}.agent-parameters{grid-template-columns:1fr}.agent-parameters dt{color:var(--muted)} }
</style>
