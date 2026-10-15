<script setup>
import { computed } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { authoringPhases, authoringTitle, kindLabel, authoringActive, authoringBudget } from '../authoringModel.js';
const props = defineProps({ session: { type: Object, required: true }, detail: Object, busy: Boolean });
const emit = defineEmits(['open']);
const title = computed(() => authoringTitle(props.session, props.detail?.candidate));
const budget = computed(() => authoringBudget(props.detail || { session: props.session }));
</script>
<template>
  <article class="author-card" :class="{working:authoringActive(session)}" :data-authoring-session-id="session.id" aria-label="资产创作">
    <div class="author-card-top"><span>{{ kindLabel(session.kind) }} · {{ authoringPhases[session.phase] || session.phase }}</span><small>草稿与进度持续保留</small></div>
    <h3>{{ title }}</h3><p v-if="session.question?.prompt">{{ session.question.prompt }}</p><p v-else-if="session.kind === 'app' && session.phase === 'completed' && session.result?.assetId">已保存到我的 APP，尚未验证或启用。打开详情检查和验证这个版本。</p><p v-else-if="session.phase === 'awaiting_review'">方法和示例已经准备好。查看实际结果后，决定保存使用或继续调整。</p><p v-else-if="session.phase === 'completed'">{{ detail?.commit?.mode === 'draft_only' || session.mode === 'draft_only' ? '草稿已保留，可以继续编辑或导出。' : '查看保存结果、版本与可用状态。' }}</p><p v-else>{{ ['failed','interrupted'].includes(session.phase) ? USER_FACING_ERROR : session.summary || session.message || '在同一份草稿中整理方法、资源与使用示例。' }}</p>
    <footer><span v-if="budget.consumed.modelRequests != null">本轮模型请求 {{ budget.consumed.modelRequests }} / {{ budget.limits.modelRequests ?? 32 }}</span><button type="button" @click="emit('open',session.id)">{{ session.phase === 'awaiting_review' ? '审阅实际效果' : '查看并继续' }} <span aria-hidden="true">↗</span></button></footer>
  </article>
</template>
<style scoped>
.author-card{margin:16px 0;padding:20px 22px;border:1px solid var(--line,#d6e3e8);border-left:3px solid var(--accent,#34758f);border-radius:12px;background:var(--panel,#f6fafb);color:var(--text,#17374a)}.author-card-top{display:flex;gap:14px;justify-content:space-between;align-items:center}.author-card-top span{font-size:12px;color:var(--accent,#34758f);font-weight:600}.author-card-top small{font-size:10px;color:var(--muted,#657b89)}.author-card h3{font-size:17px;line-height:1.7;margin:12px 0 8px;white-space:pre-wrap;overflow-wrap:anywhere}.author-card p{font-size:13px;line-height:1.8;color:var(--muted,#657b89);margin:0}.author-card footer{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:17px}.author-card footer>span{font-size:11px;color:var(--muted,#657b89)}.author-card button{margin-left:auto;background:transparent;color:var(--accent,#34758f);border:1px solid var(--line,#d6e3e8);border-radius:7px;padding:8px 12px;font:inherit;font-size:12px;cursor:pointer}.author-card button:focus-visible{outline:2px solid var(--accent,#34758f);outline-offset:3px}@media(max-width:600px){.author-card{padding:16px}.author-card-top{align-items:flex-start;flex-direction:column;gap:5px}}
</style>
