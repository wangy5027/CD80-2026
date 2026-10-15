<script setup>
import { computed, ref } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { subTaskLabels } from '../useSubAgents.js';
import { isTerminal, timeline, duration } from '../subAgentView.js';
import { modelCallText } from '../modelCallPresentation.js';
import ModelCallDetails from './ModelCallDetails.vue';
const props = defineProps({ controller: { type: Object, required: true }, run: { type: Object, required: true }, unifiedAuthoring: Boolean });
const emit = defineEmits(['capture']);
const answer = ref('');
const finished = computed(() => isTerminal(props.run));
const entries = computed(() => timeline(props.run).map(entry => entry.entryType !== 'model' ? entry : {
  ...entry, label: modelCallText(entry.label, 100), summary: modelCallText(entry.summary, 300),
  purpose: modelCallText(entry.purpose, 300), error: modelCallText(entry.error), errorSummary: modelCallText(entry.errorSummary, 180),
}));
const taskTitle = computed(() => props.run.delegation?.goal || props.run.assessment?.reason || '理解目标与安排执行');
const labels = { succeeded: '完成', failed: '失败', cancelled: '已停止', interrupted: '已中断', running: '进行中' };
const evidence = step => (props.run.artifacts || []).filter(a => a.stepId === step.id);
const completionLabel = computed(() => props.run.completion?.status === 'partial' ? '任务未全部完成' : props.run.status === 'succeeded' && props.run.completion?.status !== 'complete' ? '处理已结束' : subTaskLabels[props.run.status] || props.run.status);
const canSelectSteps = computed(() => ['succeeded', 'partial', 'failed', 'interrupted'].includes(props.run.status) && (props.run.steps || []).some(step => step.status === 'succeeded'));
</script>
<template>
  <article class="sub-task" :class="{ 'is-finished': finished, 'has-failed': run.status === 'failed' }" :data-run-id="run.id" :data-status="run.status" aria-label="本轮任务执行">
    <header class="sub-task-header">
      <span class="sub-status-dot" :class="run.status" aria-hidden="true" />
      <strong>{{ completionLabel }}</strong>
      <span class="sub-task-kind">{{ run.kind === 'delegate' ? '子任务' : run.kind === 'application' ? '应用流程' : '主助手' }}</span>
      <button v-if="!finished && run.status !== 'queued'" type="button" :disabled="run.status === 'cancelling'" @click="controller.cancel(run)">{{ run.status === 'cancelling' ? '正在停止' : '中断' }}</button>
      <button v-if="run.status === 'queued'" type="button" @click="controller.withdraw(run)">撤回</button>
    </header>
    <p v-if="run.status === 'queued'" class="sub-task-goal">等待前一项结束后执行，不会提前调用能力。</p>
    <template v-else>
      <p class="sub-task-goal">{{ taskTitle }}</p>
      <p v-if="run.retryNotice && !finished" class="sub-retry" role="status">{{ USER_FACING_ERROR }}</p>
      <details v-if="entries.length" class="sub-process" :open="!finished">
        <summary>执行过程 <span>{{ entries.filter(e => e.status === 'succeeded').length }} 项完成<span v-if="run.correctionsUsed"> · 纠错 {{ run.correctionsUsed }} / 3</span></span></summary>
        <ol class="sub-timeline">
          <li v-for="entry in entries" :key="entry.id" :class="entry.status">
            <span class="sub-step-marker" aria-hidden="true">{{ entry.status === 'succeeded' ? '✓' : entry.status === 'failed' ? '!' : '·' }}</span>
            <div class="sub-step-body">
              <div class="sub-step-heading"><strong>{{ entry.label }}</strong><span>{{ labels[entry.status] || entry.status }}<template v-if="duration(entry)"> · {{ duration(entry) }}</template></span></div>
              <p v-if="entry.purpose">{{ entry.purpose }}</p>
              <p v-if="entry.summary && entry.status === 'succeeded'" class="sub-observation">{{ entry.summary }}</p>
              <p v-if="entry.error" class="sub-step-error">{{ USER_FACING_ERROR }}</p>
              <details v-if="entry.entryType === 'step'" class="sub-step-details"><summary>输入与结果</summary>
                <pre v-if="entry.arguments && Object.keys(entry.arguments).length">{{ JSON.stringify(entry.arguments, null, 2) }}</pre>
                <p v-if="entry.inputRefs?.length">使用 {{ entry.inputRefs.length }} 份已有数据</p>
                <pre v-if="entry.status === 'succeeded' && entry.result?.content">{{ JSON.stringify(entry.result.content, null, 2) }}</pre>
                <pre v-else-if="entry.status === 'succeeded' && entry.result?.resultPreview">{{ entry.result.resultPreview }}</pre>
                <p v-if="entry.error">{{ USER_FACING_ERROR }}</p>
                <a v-for="file in evidence(entry)" :key="file.id" :href="file.downloadUrl + '?download=1'">{{ file.name }}</a>
              </details>
              <ModelCallDetails v-if="entry.entryType === 'model'" :key="run.id + ':' + entry.id" :run-id="run.id" :call="entry" />
            </div>
          </li>
        </ol>
        <div v-if="run.approvals?.length" class="sub-approvals"><p v-for="approval in run.approvals" :key="approval.id">{{ approval.decision === 'approve' ? '主助手已批准临时代码' : '代码申请未批准' }}：{{ approval.reason }}</p></div>
      </details>
      <form v-if="run.question && run.status === 'awaiting_user'" class="sub-answer" @submit.prevent="controller.answer(run, answer)">
        <label :for="'answer-' + run.id">{{ run.question.prompt }}</label><textarea :id="'answer-' + run.id" v-model="answer" required placeholder="回答当前问题，继续此任务" /><button type="submit">提交回答</button>
      </form>
      <div v-if="run.deliveries?.length" class="sub-artifacts">
        <figure v-for="file in run.deliveries.filter(a => a.mediaType === 'image/png')" :key="file.id"><img :src="file.downloadUrl" :alt="file.name" loading="lazy" /><figcaption>{{ file.name }}</figcaption></figure>
        <div class="sub-downloads"><a v-for="file in run.deliveries" :key="file.id" :href="file.downloadUrl + '?download=1'" :data-filename="file.name"><span aria-hidden="true">↓</span> {{ file.name }}<small>{{ Math.max(1, Math.ceil(file.size / 1024)) }} KB</small></a></div>
      </div>
      <p v-if="run.status === 'failed' || run.status === 'interrupted'" class="sub-ending" role="alert">{{ USER_FACING_ERROR }}</p>
      <section v-if="run.completion?.status === 'partial'" class="sub-ending" role="status"><strong>任务未全部完成</strong><p v-if="run.completion.reasons?.length">{{ USER_FACING_ERROR }}</p><p v-if="run.completion.missingDeliverables?.length">尚未交付：{{ run.completion.missingDeliverables.join('、') }}</p></section>
      <section v-if="unifiedAuthoring ? finished : canSelectSteps && run.kind !== 'application'" class="sub-capture" aria-label="保存与复用成果">
        <div><strong>{{ unifiedAuthoring ? '把这次工作整理成可复用方法' : '保留已完成的步骤' }}</strong><p v-if="unifiedAuthoring">自动整理相关规则、实现和资源，准备示例验证，最后由你审阅。历史不足时会标明推断或新生成的部分。</p><p v-else>选择一个或多个成功步骤，整理为应用、Skill 或 Tool 草稿。平台会核对可恢复的实现与依赖，保存不会重新执行。</p></div>
        <div class="sub-capture-actions"><button type="button" @click="emit('capture', 'application', run)">保存为应用</button><button type="button" @click="emit('capture', 'skill', run)">沉淀为 Skill</button><button type="button" @click="emit('capture', 'tool', run)">沉淀为 Tool</button></div>
      </section>
    </template>
  </article>
</template>
<style scoped>
.sub-task{--task-line:var(--line,#dce4e8);width:100%;min-width:0;padding:4px 0 12px;overflow-wrap:anywhere;color:var(--text,#17374a)}
.sub-task-header{display:flex;align-items:center;gap:9px;min-height:30px;font-size:14px}.sub-task-header strong{font-weight:650}.sub-task-kind{font-size:11px;letter-spacing:.05em;color:var(--muted,#718392);padding:2px 7px;border:1px solid var(--task-line);border-radius:5px}.sub-task-header button{margin-left:auto}
.sub-status-dot{width:7px;height:7px;background:#51879d;border-radius:50%;box-shadow:0 0 0 4px #51879d12}.sub-status-dot.running,.sub-status-dot.planning,.sub-status-dot.reviewing{animation:breathe 1.8s ease-in-out infinite}.sub-status-dot.succeeded{background:#3b8b71}.sub-status-dot.failed,.sub-status-dot.cancelled,.sub-status-dot.interrupted{background:#aa6756}
.sub-task-goal{margin:9px 0 14px;font-size:14px;line-height:1.75;color:var(--muted,#657b89)}.sub-process{border-top:1px solid var(--task-line);border-bottom:1px solid var(--task-line);padding:11px 0}.sub-process>summary{cursor:pointer;font-size:13px;user-select:none}.sub-process>summary>span{margin-left:10px;color:var(--muted,#657b89);font-size:12px}
.sub-timeline{list-style:none;padding:5px 0 0;margin:16px 0 0}.sub-timeline>li{display:flex;position:relative;gap:12px;padding:0 0 20px;min-width:0}.sub-timeline>li:last-child{padding-bottom:4px}.sub-timeline>li:not(:last-child):before{content:'';position:absolute;top:20px;bottom:0;left:9px;border-left:1px solid var(--task-line)}.sub-step-marker{display:flex;align-items:center;justify-content:center;width:19px;height:19px;border:1px solid var(--task-line);border-radius:50%;font-size:12px;flex:none;color:var(--muted,#657b89);background:var(--panel,#fff)}.succeeded>.sub-step-marker{color:#3b806a;background:#3b806a0c}.failed>.sub-step-marker{color:#a25c48;background:#a25c480c}.running>.sub-step-marker{color:#236b94;border-color:#5a91ab}
.sub-step-body{flex:1;min-width:0}.sub-step-heading{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;line-height:21px}.sub-step-heading strong{font-size:13px;font-weight:600}.sub-step-heading>span{font-size:11px;color:var(--muted,#718392)}.sub-step-body p{margin:6px 0 0;font-size:12px;line-height:1.7;color:var(--muted,#657b89)}.sub-step-body .sub-observation{color:var(--text,#294f5c)}.sub-step-body .sub-step-error,.sub-ending,.sub-retry{color:#a05e43}.sub-retry,.sub-ending{font-size:13px;line-height:1.7}.sub-step-details{margin-top:7px;font-size:12px}.sub-step-details summary{cursor:pointer;color:var(--muted,#657b89)}.sub-step-details pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto;padding:10px;background:var(--panel,#f5f8f9);font-size:11px}.sub-step-details a{display:inline-block;margin:6px 12px 0 0;color:var(--accent,#2c6684)}
.sub-approvals{margin:10px 0 0;padding:8px 12px;border-left:2px solid #91afa2;background:#91afa209;font-size:12px;line-height:1.7}.sub-approvals p{margin:0}.sub-artifacts{margin-top:18px}.sub-artifacts figure{margin:0 0 12px}.sub-artifacts img{max-width:100%;height:auto;display:block;border-radius:8px}.sub-artifacts figcaption{font-size:11px;color:var(--muted,#718392);margin-top:5px}.sub-downloads{display:flex;flex-wrap:wrap;gap:8px}.sub-downloads a{display:inline-flex;align-items:center;gap:7px;text-decoration:none;border:1px solid var(--task-line);border-radius:7px;padding:7px 10px;font-size:12px;color:inherit}.sub-downloads small{color:var(--muted,#718392);font-size:10px}.sub-task button{border:1px solid var(--task-line);background:transparent;color:inherit;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer}.sub-task button:disabled{opacity:.6;cursor:default}.sub-answer{margin-top:15px;font-size:13px}.sub-answer textarea{display:block;box-sizing:border-box;width:100%;min-height:74px;border:1px solid var(--task-line);border-radius:7px;background:transparent;color:inherit;padding:10px;margin:10px 0;resize:vertical}.sub-task button:focus-visible,.sub-task summary:focus-visible,.sub-task a:focus-visible{outline:2px solid #4d809c;outline-offset:3px}@keyframes breathe{50%{opacity:.4}}@media(prefers-reduced-motion:reduce){.sub-status-dot{animation:none!important}}@media(max-width:600px){.sub-task-goal{font-size:13px}.sub-step-heading{gap:4px}.sub-process>summary>span{margin-left:5px}}
</style>

<style scoped>
.sub-capture{margin:20px 0 6px;padding:17px 18px;border:1px solid #eadbc9;border-radius:12px;background:var(--evolve-wash,#fff4e9)}.sub-capture strong{font-size:13px;color:var(--evolve-ink,#995019)}.sub-capture p{margin:7px 0 12px;font-size:12px;line-height:1.7;color:var(--muted)}.sub-capture-actions{display:flex;gap:8px;flex-wrap:wrap}.sub-capture-actions button:first-child{background:var(--evolve-action,#995d28);color:white;border-color:transparent}
</style>
