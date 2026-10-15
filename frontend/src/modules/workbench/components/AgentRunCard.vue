<script setup>
import { computed, ref, watch, onUnmounted } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import AgentInputForm from './AgentInputForm.vue';
import { statusLabels, terminalStatuses } from '../agentForms.js';
import { accountDraftKey, readDraft, writeDraft, subscribeDraft } from '../draftStore.js';
const props = defineProps({ run: Object, files: Array, busy: Boolean, ownerId: String, unifiedAuthoring: Boolean });
const emit = defineEmits(['approve', 'cancel', 'supply', 'feedback', 'save', 'revise', 'upload', 'open-task']);
const draftKey = purpose => accountDraftKey(props.ownerId, purpose, props.run.id);
function persistentText(purpose, fallback) {
  const value = ref(''); let restoring = false; let unsubscribe;
  const restore = saved => { restoring = true; value.value = typeof saved === 'string' ? saved : fallback(); restoring = false; };
  watch(() => draftKey(purpose), key => { unsubscribe?.(); restore(readDraft(key)); unsubscribe = subscribeDraft(key, restore); }, { immediate: true });
  watch(value, next => { if (!restoring) writeDraft(draftKey(purpose), next); }, { flush: 'sync' });
  onUnmounted(() => unsubscribe?.());
  return value;
}
const feedbackText = persistentText('feedback', () => '');
const revisionText = persistentText('revision-goal', () => props.run.goal || '');
const pending = computed(() => props.run.approvals?.filter(a => a.status === 'pending') || []);
const canRevise = computed(() => props.run.spec && (terminalStatuses.has(props.run.status) || ['awaiting_input','awaiting_confirmation'].includes(props.run.status)));
const fmt = value => JSON.stringify(value, null, 2);
</script>
<template>
  <article class="agent-run-card" :data-run-id="run.id">
    <header class="agent-card-head"><div><h3>{{ run.spec?.name || run.goal || '应用运行' }}</h3><small class="agent-muted">{{ run.spec ? '版本 ' + run.spec.version : '正在生成设计' }} · {{ run.id.slice(0, 8) }}</small></div><span class="agent-status" :class="run.status">{{ statusLabels[run.status] || run.status }}</span></header>
    <p>{{ run.goal }}</p>
    <p v-if="run.searchMode === 'off'" class="agent-muted">本次已关闭网页搜索；其他已注册查询工具仍可按流程使用。</p>
    <p v-else-if="run.searchMode === 'on'" class="agent-muted">本次使用始终网页搜索模式。</p>
    <div v-if="['generating','awaiting_input','awaiting_confirmation'].includes(run.status)" class="agent-preview-note">当前仅展示设计，尚未运行工具或测试数据。确认后才会实际执行。</div>
    <p v-if="run.status === 'interrupted'" class="agent-error">{{ USER_FACING_ERROR }}</p>
    <p v-if="run.error" class="agent-error" role="alert">{{ USER_FACING_ERROR }}</p>
    <div v-if="run.files?.length" class="agent-file-list"><span v-for="file in run.files" :key="file.id">输入：{{ file.name }}</span></div>
    <details v-if="run.input && Object.keys(run.input).length"><summary>本次参数</summary><dl class="agent-parameters"><template v-for="(value,name) in run.input" :key="name"><dt>{{ name }}</dt><dd>{{ typeof value === 'object' ? fmt(value) : value }}</dd></template></dl></details>
    <ol v-if="run.spec?.pipeline" class="agent-pipeline">
      <li v-for="step in run.spec.pipeline" :key="step.id"><strong>{{ step.name || (step.kind === 'model' ? '整理结果说明' : step.tool) }}</strong><span class="agent-muted">{{ statusLabels[run.steps?.find(s => s.id === step.id)?.status] || '待执行' }}</span><p v-if="step.purpose">{{ step.purpose }}</p><small>输入：{{ Array.isArray(step.inputFrom) ? step.inputFrom.join('、') : step.inputFrom }}</small>
        <details v-if="run.steps?.find(s => s.id === step.id)?.logs"><summary>步骤日志</summary><pre>{{ run.steps.find(s => s.id === step.id)?.status === 'succeeded' ? run.steps.find(s => s.id === step.id).logs : USER_FACING_ERROR }}</pre></details>
        <details v-if="run.steps?.find(s => s.id === step.id)?.output"><summary>步骤输出</summary><pre>{{ run.steps.find(s => s.id === step.id)?.status === 'succeeded' ? fmt(run.steps.find(s => s.id === step.id).output) : USER_FACING_ERROR }}</pre></details>
      </li>
    </ol>
    <details v-if="run.spec" class="agent-advanced"><summary>高级详情：指令、技能与工具</summary><h4>应用指令</h4><pre>{{ run.spec.prompt }}</pre><h4>输入 / 输出约定</h4><pre>{{ fmt({ input: run.spec.inputSchema, output: run.spec.outputSchema }) }}</pre><h4>技能</h4><div v-for="skill in run.spec.skills" :key="skill.id"><strong>{{ skill.id }} · {{ skill.version }}</strong><p v-if="skill.registeredSkill" class="agent-muted">技能目录：{{ skill.registeredSkill }}</p><pre v-if="skill.content">{{ skill.content }}</pre></div><h4>工具</h4><details v-for="tool in run.spec.tools" :key="tool.id"><summary>{{ tool.id }} · {{ tool.version }}</summary><template v-if="tool.registeredTool"><p>平台工具：{{ tool.registeredTool }}</p><p class="agent-muted">由服务端已注册工具执行。</p></template><template v-else><p>文件权限：{{ tool.permissions?.files?.join('、') || '无' }}；生成 Python 工具网络关闭</p><pre v-if="tool.code">{{ tool.code }}</pre></template></details></details>
    <AgentInputForm v-if="run.status === 'awaiting_input'" :key="run.id" :draft-key="draftKey('supply')" :spec="run.spec" :initial="run.input" :files="files" :busy="busy" submit-label="补充参数并查看流程" @submit="emit('supply', $event)" @upload="emit('upload', $event)" />
    <section v-for="approval in pending" :key="approval.id" class="agent-approval">
      <strong>{{ approval.kind === 'execute' ? '确认这个流程后开始运行？' : approval.kind === 'access' ? '允许访问这些额外文件？' : '保存为个人应用？' }}</strong><p>{{ approval.reason }}</p>
      <ul v-if="approval.files"><li v-for="file in approval.files" :key="file.id">{{ file.name || file.id }}</li></ul>
      <div class="agent-actions"><button class="primary-button" :disabled="busy" @click="approval.kind === 'save' ? emit('save') : emit('approve', approval, 'approve')">{{ approval.kind === 'execute' ? '确认并运行' : approval.kind === 'access' ? '允许访问并继续' : '保存应用' }}</button><button class="secondary-button" :disabled="busy" @click="emit('approve', approval, 'reject')">{{ approval.kind === 'save' ? '暂不保存' : '拒绝' }}</button></div>
    </section>
    <div v-if="run.status === 'succeeded' && run.output?.explanation" class="agent-result"><h4>结果说明</h4><p>{{ run.output.explanation }}</p></div>
    <div v-if="run.artifacts?.length" class="agent-artifacts"><h4>{{ run.status === 'succeeded' ? '输出文件' : '已产生的文件（流程未完成）' }}</h4><a v-for="artifact in run.artifacts" :key="artifact.id" :href="'/desktop/api/agent-artifacts/' + encodeURIComponent(artifact.id)" download>{{ artifact.name }} ↓</a></div>
    <p v-if="run.savedAgentRef" class="agent-saved">已保存：{{ run.savedAgentRef }}</p>
    <div class="agent-actions"><button v-if="!terminalStatuses.has(run.status)" class="secondary-button" :disabled="busy || run.status === 'cancelling'" @click="emit('cancel')">{{ run.status === 'cancelling' ? '正在停止…' : '停止本次运行' }}</button><button v-if="run.taskId" class="subtle-button" @click="emit('open-task', run.taskId)">打开所属任务</button></div>
    <details v-if="terminalStatuses.has(run.status)"><summary>反馈结果</summary><form @submit.prevent="feedbackText.trim() && emit('feedback', feedbackText.trim())"><textarea v-model="feedbackText" rows="2" placeholder="结果是否符合预期？" :disabled="busy" /><div class="agent-actions"><button class="secondary-button" :disabled="busy || !feedbackText.trim()">提交反馈</button><button v-if="run.status === 'succeeded' && !run.savedAgentRef" type="button" class="primary-button" :disabled="busy" @click="emit('save')">接受结果并保存应用</button></div></form></details>
    <div v-if="canRevise && unifiedAuthoring" class="agent-actions"><button type="button" class="secondary-button" :disabled="busy" @click="emit('revise',{})">在统一草稿中调整这份方法</button></div>
    <details v-if="canRevise && !unifiedAuthoring"><summary>修改需求或参数并生成新版本</summary><p class="agent-muted">保留旧运行记录。下方显示原参数，可修改列名或重新选择文件；只有本次选择的文件会获得授权。新版本会重新展示流程，确认后运行。</p><label class="form-field"><span>修改后的目标与规则</span><textarea v-model="revisionText" rows="3" placeholder="说明希望保留的目标和要修改的规则" :disabled="busy" /></label><AgentInputForm :key="'revision-' + run.id" :draft-key="draftKey('revision-input')" :spec="run.spec" :initial="run.input" :files="files" :busy="busy || !revisionText.trim()" submit-label="生成修改后的流程" @submit="values => emit('revise', { goal: revisionText.trim(), ...values })" @upload="emit('upload', $event)" /></details>
  </article>
</template>
