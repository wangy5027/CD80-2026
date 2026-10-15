<script setup>
import { computed, nextTick, ref, toRef, watch } from 'vue';
import { useAppWorkshop } from './useAppWorkshop.js';
import { userFacingError } from '../../shared/userFacingError.js';
const props = defineProps({ ownerKey: { type: [String, Number], required: true } });
const emit = defineEmits(['handoff', 'saved']);
const w = useAppWorkshop(toRef(props, 'ownerKey'));
const { requirement, session, step, loading, busy, error } = w;
const root = ref(null);
const analysis = computed(() => session.value?.analysis || {});
const recommendation = computed(() => session.value?.recommendation);
const preview = computed(() => session.value?.appPreview);
const itemText = item => typeof item === 'string' ? item : item?.label ? item.label + (item.description ? '：' + item.description : '') : item?.description || item?.purpose || item?.title || item?.name || '';
watch(step, async () => { await nextTick(); root.value?.querySelector('[data-step-heading]')?.focus({ preventScroll: true }); root.value?.closest('.app-workshop-host')?.scrollTo({ top: 0 }); });
async function useRecommendation() { const value = await w.handoff(); if (value) emit('handoff', value); }
async function saveApp() { const value = await w.save(); if (value) emit('saved', value); }
</script>

<template>
  <div ref="root" class="app-workshop" data-app-workshop>
    <header class="aw-header"><div><p class="aw-eyebrow">APP WORKSHOP</p><h1>应用工坊</h1><p>说说你要解决的问题，一起找到合适的应用。</p></div><button v-if="session" class="secondary-button" data-action="aw-new" :disabled="Boolean(busy)" @click="w.startNew">再说一个需求</button></header>
    <ol class="aw-steps" aria-label="创建应用进度"><li v-for="(label, index) in ['说说需求', '看看怎么做', '创建你的应用']" :key="label" :class="{ active: step === index + 1, complete: step > index + 1 }" :aria-current="step === index + 1 ? 'step' : undefined"><span>{{ index + 1 }}</span>{{ label }}</li></ol>
    <p v-if="loading" class="aw-notice" role="status">正在找回你的需求…</p>
    <div v-else>
      <div v-if="error" class="aw-error" role="alert">{{ userFacingError(error) }}<button v-if="!session" class="secondary-button" :disabled="Boolean(busy)" @click="w.reload">重新读取</button></div>
      <form v-if="step === 1" class="aw-card aw-intro" @submit.prevent="w.analyze">
        <p class="aw-eyebrow">从一个想法开始</p><h2 tabindex="-1" data-step-heading>你有什么金融需求？</h2><p>例如，整理每月的交易记录，比较几只股票，或者把财务数据做成一份报告。直接用你自己的话告诉我就好。</p>
        <label for="aw-requirement">我希望解决的问题</label><textarea id="aw-requirement" v-model="requirement" rows="6" maxlength="10000" placeholder="例如：我想上传交易记录，按月份汇总赚亏，再生成一份容易看懂的报告。" :disabled="Boolean(busy)" required />
        <div class="aw-actions"><button class="primary-button" data-action="aw-analyze" :disabled="Boolean(busy) || !requirement.trim()">{{ busy === 'analyze' ? '正在分析你的需求…' : '进行需求分析' }}</button></div>
      </form>
      <article v-else-if="session.status === 'handed_off'" class="aw-card" data-aw-completed><p class="aw-eyebrow">需求已交给推荐应用</p><h2 tabindex="-1" data-step-heading>已打开推荐应用，本次需求已完成</h2><blockquote class="aw-original">{{ session.requirement }}</blockquote><p>你的问题已经带到“{{ recommendation.name }}”。可以在那里检查内容，再开始使用。</p><div class="aw-actions"><button class="secondary-button" data-action="aw-reopen" @click="useRecommendation">再次打开推荐 APP</button><button class="primary-button" data-action="aw-new-completed" @click="w.startNew">再说一个需求</button></div></article>
      <template v-else-if="step === 2">
        <article class="aw-card"><p class="aw-eyebrow">先把需求说清楚</p><h2 tabindex="-1" data-step-heading>原来你想做的是这件事</h2><blockquote class="aw-original">{{ session.requirement }}</blockquote><p class="aw-understanding" data-aw-understanding>{{ analysis.understanding }}</p>
          <h3 v-if="analysis.workflow?.length">可以分成这几步</h3><ol class="aw-workflow"><li v-for="(item, index) in analysis.workflow || []" :key="index"><strong>{{ item.title }}</strong><p>{{ item.description }}</p></li></ol>
          <div class="aw-grid"><section v-if="analysis.tools?.length"><h3>会用到哪些工具</h3><ul><li v-for="(item, i) in analysis.tools" :key="i">{{ itemText(item) }}</li></ul></section><section v-if="analysis.skills?.length"><h3>需要哪些处理能力</h3><ul><li v-for="(item, i) in analysis.skills" :key="i">{{ itemText(item) }}</li></ul></section></div>
        </article>
        <article class="aw-card"><p class="aw-eyebrow">看看已有应用能帮多少</p><template v-if="recommendation"><h2>推荐你试试：{{ recommendation.name }}</h2><p>{{ recommendation.description }}</p><h3>为什么推荐它</h3><p data-aw-reason>{{ recommendation.reason }}</p><div class="aw-grid"><section><h3>它已经能做到</h3><ul><li v-for="(item, i) in recommendation.covered || []" :key="i">{{ itemText(item) }}</li></ul><p v-if="!recommendation.covered?.length">暂时还没有确认能直接满足的部分。</p></section><section><h3>你还需要的独立功能</h3><ul><li v-for="(item, i) in recommendation.gaps || []" :key="i">{{ itemText(item) }}</li></ul><p v-if="!recommendation.gaps?.length">目前没有发现额外的功能差异。</p></section></div></template><template v-else><h2>暂时没有足够合适的已有应用</h2><p id="aw-no-recommendation">目前没有可以直接使用的推荐应用，你可以按需求创建新的 APP。</p></template></article>
        <div class="aw-actions aw-main-actions"><button class="secondary-button" data-action="aw-handoff" :disabled="Boolean(busy) || !recommendation" :aria-describedby="recommendation ? undefined : 'aw-no-recommendation'" @click="useRecommendation">{{ busy === 'handoff' ? '正在打开应用…' : '使用推荐 APP' }}</button><button class="primary-button" data-action="aw-create" :disabled="Boolean(busy)" @click="w.create">{{ busy === 'create' ? '正在从应用池查找并组合应用…' : '创建新的 APP' }}</button></div>
      </template>
      <article v-else class="aw-card aw-preview" data-aw-preview><p class="aw-eyebrow">属于你的新应用</p><h2 tabindex="-1" data-step-heading>{{ preview.name }}</h2><p class="aw-goal">{{ preview.goal }}</p><p>{{ preview.description }}</p><span class="aw-tag">{{ session.status === 'saved' ? '已保存 · 未验证 · 未启用 · 未运行' : '保存状态待核实 · 未运行' }}</span>
        <section v-if="preview.sourceApps?.length" data-aw-sources>
          <h3>从应用池选用了这些应用</h3>
          <div class="aw-sources"><section v-for="app in preview.sourceApps" :key="app.appKey" class="aw-source">
            <h4>{{ app.name }}</h4><p>{{ app.reason }}</p>
            <ul v-if="app.reuse?.length"><li v-for="(item, i) in app.reuse" :key="i">{{ item }}</li></ul>
          </section></div>
        </section>
        <section v-if="preview.composition" data-aw-composition>
          <h3>组合与修改方案</h3><p>{{ preview.composition.summary }}</p>
          <ul v-if="preview.composition.changes?.length"><li v-for="(item, i) in preview.composition.changes" :key="i">{{ item }}</li></ul>
        </section>
        <h3>它能帮你做什么</h3><ul class="aw-features"><li v-for="(item, i) in preview.features || []" :key="i">{{ itemText(item) }}</li></ul>
        <div class="aw-grid"><section><h3>你需要提供</h3><ul><li v-for="(item, i) in preview.inputs || []" :key="i">{{ itemText(item) }}</li></ul><p v-if="!preview.inputs?.length">打开应用时，可以继续说明你的具体要求。</p></section><section><h3>你会得到</h3><ul><li v-for="(item, i) in preview.outputs || []" :key="i">{{ itemText(item) }}</li></ul></section></div>
        <p class="aw-note">{{ session.status === 'saved' ? '应用已加入个人应用池，尚未运行。请在应用详情审阅实际步骤，完成验证后再启用。' : '旧创作记录尚未显示保存结果，可核实并补存到应用池。' }}</p><div class="aw-actions"><button v-if="session.status !== 'saved'" class="secondary-button" data-action="aw-review" :disabled="Boolean(busy)" @click="w.review">返回需求分析</button><button class="primary-button" data-action="aw-save" :disabled="Boolean(busy) || (session.status === 'saved' && (!session.savedAppId || !session.savedVersionId))" @click="saveApp">{{ busy === 'save' ? '正在核实保存状态…' : session.status === 'saved' ? '打开应用详情，修改或验证' : '核实并补存旧创作' }}</button></div>
      </article>
      <p v-if="busy" class="aw-busy" role="status" aria-live="polite">{{ ({ analyze: '正在理解需求并查找已有应用，请稍候…', refine: '正在结合补充内容更新分析…', create: '正在从应用池查找并组合应用…', save: '正在保存到你的账号…', handoff: '正在带上你的问题打开应用…' })[busy] }}</p>
    </div>
  </div>
</template>

<style scoped>
.aw-sources{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:14px}.aw-source{border:1px solid var(--line);border-radius:12px;background:var(--surface-2);padding:18px}.aw-source h4{font-size:15px;margin:0 0 8px}.aw-source p{margin:0}.aw-source ul{margin-bottom:0}
.app-workshop{width:100%;box-sizing:border-box;max-width:1080px;margin:auto;padding:30px 30px 60px;color:var(--text);overflow-wrap:anywhere}.aw-header{display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:26px}.aw-header h1{font-size:27px;margin:4px 0 10px}.aw-header p{margin:0;color:var(--muted);line-height:1.7}.aw-eyebrow{font-size:11px;letter-spacing:.15em;color:var(--muted);text-transform:uppercase}.aw-steps{list-style:none;display:flex;gap:12px;margin:0 0 26px;padding:0}.aw-steps li{flex:1;display:flex;align-items:center;gap:10px;color:var(--muted);border-bottom:2px solid var(--line);padding:0 0 14px;font-size:14px}.aw-steps li.active{border-color:var(--accent);color:var(--text)}.aw-steps li.complete{color:#aad7bc}.aw-steps span{display:grid;place-items:center;width:27px;height:27px;flex-shrink:0;border:1px solid var(--line);border-radius:50%;font-size:12px}.aw-steps .active span{background:var(--accent-dark);border-color:var(--accent)}.aw-card{background:var(--surface);border:1px solid var(--line);border-radius:18px;padding:28px;margin:18px 0}.aw-card h2{font-size:21px;line-height:1.45;margin:6px 0 18px}.aw-card h3{font-size:15px;margin:24px 0 10px}.aw-card p,.aw-card li{line-height:1.85;font-size:14px}.aw-card p{color:var(--muted)}.aw-card ul{padding-left:22px}.aw-card li{padding:3px 0}.aw-intro{padding:36px}.aw-intro h2{font-size:29px;margin-top:14px}.aw-card label{display:block;font-size:14px;margin:24px 0 10px}.aw-card textarea{display:block;width:100%;max-width:100%;padding:15px;border:1px solid #59534c;border-radius:12px;background:var(--surface-2);color:var(--text);font:inherit;line-height:1.8;box-sizing:border-box;resize:vertical}.aw-card textarea::placeholder{color:var(--muted)}.aw-card textarea:focus-visible,.app-workshop button:focus-visible{outline:2px solid var(--accent);outline-offset:4px}.aw-actions{display:flex;flex-wrap:wrap;gap:12px;margin-top:24px}.aw-actions button{min-height:44px}.aw-main-actions{justify-content:flex-end}.aw-grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}.aw-original{margin:0 0 22px;padding:12px 18px;border-left:3px solid var(--accent);background:var(--surface-2);font-size:14px;line-height:1.8;white-space:pre-wrap}.aw-understanding,.aw-goal{color:var(--text)!important;white-space:pre-wrap}.aw-workflow{padding-left:24px}.aw-workflow li{padding:8px}.aw-workflow li p{margin:4px 0}.aw-resources{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(220px,100%),1fr));gap:12px}.aw-resources section{border:1px solid var(--line);border-radius:12px;padding:18px}.aw-resources h3{margin:12px 0 8px}.aw-resources p{margin:0}.aw-tag{display:inline-block;border:1px solid var(--line);border-radius:20px;background:var(--surface-2);color:var(--muted);font-size:12px;padding:5px 10px}.aw-note{color:var(--muted);font-size:13px!important;line-height:1.7}.aw-error{background:#342425;border:1px solid #653b40;color:var(--danger);border-radius:12px;padding:16px;white-space:pre-wrap}.aw-error button{margin-left:12px}.aw-busy,.aw-notice{padding:15px;color:var(--muted);font-size:14px;line-height:1.8}.aw-features li{padding:8px 0}.aw-preview>.aw-goal{font-size:17px}.aw-preview h2{font-size:28px}
@media(max-width:640px){.app-workshop{padding:20px 14px 40px}.aw-header{align-items:flex-start;flex-wrap:wrap}.aw-card,.aw-intro{padding:20px}.aw-intro h2{font-size:24px}.aw-steps{gap:7px}.aw-steps li{font-size:12px;gap:6px;align-items:flex-start}.aw-steps span{width:22px;height:22px}.aw-grid{grid-template-columns:1fr;gap:0}.aw-actions{flex-direction:column}.aw-actions button{width:100%}.aw-card h2{font-size:21px}}
</style>
