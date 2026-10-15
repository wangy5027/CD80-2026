<script setup>
import Icon from './Icon.vue';
import LiquidAuroraRibbon from './LiquidAuroraRibbon.vue';

defineProps({ busy: Boolean, hasSkills: Boolean });
const emit = defineEmits(['explore', 'strategy-workshop', 'app-workshop', 'prompt']);
const entries = [
  { key: 'explore', stage: 'Explore', title: '探索与研究', description: '理解事件、发现关联、对照历史，保留研究与来源。', action: '进入 Explore', icon: 'search', tags: ['理解事件', '发现关联', '历史验证'] },
  { key: 'strategy-workshop', stage: '策略工坊', title: '把想法变成可验证规则', description: '说清交易想法，对齐规则，再用历史数据试一试。', action: '进入策略工坊', icon: 'spark', tags: ['表达想法', '对齐规则', '历史试验'] },
  { key: 'app-workshop', stage: '应用工坊', title: '组合自己的应用', description: '从可用应用中寻找合适能力，组合并保存自己的应用。', action: '进入应用工坊', icon: 'grid', tags: ['应用推荐', '能力组合', '保存应用'] },
];
const suggestions = [
  { title: '梳理一份研究资料', text: '请帮我梳理一份研究资料，先和我确认研究问题、资料范围和希望得到的结果。' },
  { title: '规划一个新任务', text: '帮我把这个想法梳理成可执行的计划，先向我确认目标、截止时间和现有资料。' },
  { title: '创建文件处理应用', text: '我想创建一个处理 CSV 的应用，请先和我确认分组列、求和列和输出要求，再展示流程供我确认。', build: true },
  { title: '整理已有流程的改进方向', text: '我想改进一个已有流程，请先了解目前的步骤、遇到的问题和期望的效果，再一起梳理改进方向。' },
];
</script>

<template>
  <div class="home-landing">
    <header class="home-hero">
      <p class="home-eyebrow">FROM IDEA TO IMPACT</p>
      <h1>让每个人都能创造自己的智能工具</h1>
      <p class="home-description">从自然语言中的想法，到可运行、可复用的应用，让研究与工作持续推进。</p>
    </header>

    <section class="home-feature-grid" aria-label="开始你的工作">
      <LiquidAuroraRibbon class="home-connector" />
      <article v-for="entry in entries" :key="entry.key" class="home-entry" :class="'home-entry-' + entry.key">
        <div class="home-entry-card">
          <div class="home-entry-art" aria-hidden="true"><Icon :name="entry.icon" /></div>
          <h2>{{ entry.stage }}</h2>
          <h3>{{ entry.title }}</h3>
          <p>{{ entry.description }}</p>
          <button type="button" class="home-entry-action" @click="emit(entry.key)">{{ entry.action }}<span aria-hidden="true">→</span></button>
        </div>
        <ul class="home-entry-tags" aria-label="适用场景"><li v-for="tag in entry.tags" :key="tag">{{ tag }}</li></ul>
      </article>
    </section>

    <section class="home-compose-region" aria-label="描述你的想法">
      <slot />
      <div class="home-prompt-suggestions" aria-label="试试这些想法">
        <span class="home-suggestion-label">试试这样开始</span>
        <button v-for="item in suggestions" :key="item.title" type="button" :disabled="busy || (item.build && hasSkills)" @click="emit('prompt', item)">{{ item.title }}<span aria-hidden="true">↗</span></button>
      </div>
    </section>
  </div>
</template>

<style scoped>
.home-landing { position: relative; isolation: isolate; flex: none; width: min(calc(100% - 112px), 1200px); margin: 0 auto; padding: 24px 0 48px; }
.home-landing::before {
  content: ''; position: absolute; z-index: -1; pointer-events: none; inset: 110px -48px 200px;
  background: linear-gradient(118deg, transparent 6%, rgb(245 187 221 / 34%) 24%, rgb(255 249 255 / 8%) 39%, rgb(199 165 245 / 32%) 56%, transparent 77%),
    radial-gradient(ellipse 72% 44% at 14% 76%, rgb(245 187 221 / 24%), transparent 80%),
    radial-gradient(ellipse 70% 48% at 86% 78%, rgb(199 165 245 / 21%), transparent 80%);
  -webkit-mask-image: linear-gradient(to bottom, transparent, #000 20% 72%, transparent);
  mask-image: linear-gradient(to bottom, transparent, #000 20% 72%, transparent);
  animation: home-aurora-mist 34s ease-in-out infinite;
}
.home-hero { text-align: center; }
.home-eyebrow { margin: 0 0 18px; color: var(--aurora-brand, #7652b7); font-size: 13px; font-weight: 600; letter-spacing: .26em; line-height: 1.5; }
.home-hero h1 { margin: 0; font-size: clamp(34px, 3.35vw, 52px); font-weight: 650; letter-spacing: -.035em; line-height: 1.25; text-wrap: balance; color: var(--aurora-text, #172033); }
.home-description { margin: 20px auto 0; max-width: 850px; font-size: 19px; line-height: 1.7; color: var(--aurora-text-secondary, #606579); text-wrap: balance; }
.home-feature-grid { position: relative; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin-top: 142px; }
.home-connector { position: absolute; width: 100%; height: 160px; top: -148px; pointer-events: none; z-index: -1; }
.home-entry { min-width: 0; --entry-wash: #fbf4fa; --entry-reflection: rgb(245 187 221 / 27%); --entry-tag: #f9eff7; --entry-border: rgb(151 121 177 / 32%); }
.home-entry-strategy-workshop { --entry-wash: #f7f3fc; --entry-reflection: rgb(199 165 245 / 25%); --entry-tag: #f5f0fb; }
.home-entry-app-workshop { --entry-wash: #fff4f8; --entry-reflection: rgb(247 184 207 / 26%); --entry-tag: #fff0f6; }
.home-entry-card {
  --entry-glass-rim: inset 0 2px 1px rgb(255 255 255 / 96%), inset 2px 0 1px rgb(255 255 255 / 68%), inset 0 -2px 1px rgb(151 121 177 / 18%), inset -1px 0 1px rgb(151 121 177 / 10%), inset 0 0 20px rgb(255 255 255 / 18%);
  position: relative; height: calc(100% - 44px); min-height: 310px; padding: 68px 28px 24px; display: flex; flex-direction: column; align-items: center; text-align: center;
  border: 1px solid rgb(255 255 255 / 80%); border-radius: 24px; background: linear-gradient(155deg, #fff, var(--entry-wash));
  box-shadow: var(--entry-glass-rim), 0 0 0 1px rgb(151 121 177 / 22%), 0 5px 12px rgb(83 55 117 / 6%), 0 22px 42px rgb(83 55 117 / 12%);
  transition: transform .2s, box-shadow .2s, border-color .2s;
}
.home-entry-card:hover { transform: translateY(-2px); border-color: #fff; box-shadow: var(--entry-glass-rim), 0 0 0 1px rgb(151 121 177 / 28%), 0 7px 14px rgb(83 55 117 / 7%), 0 24px 44px rgb(83 55 117 / 15%); }
.home-entry-card:focus-within { border-color: rgb(134 91 199 / 55%); box-shadow: var(--entry-glass-rim), 0 0 0 1px rgb(134 91 199 / 26%), 0 6px 14px rgb(83 55 117 / 7%), 0 22px 42px rgb(83 55 117 / 13%); }
.home-entry-art { position: absolute; width: 156px; height: 156px; display: grid; place-items: center; top: -112px; left: 50%; transform: translateX(-50%); pointer-events: none; border: 1px solid var(--entry-border); border-radius: 36px; background: linear-gradient(145deg, #fff, var(--entry-wash)); box-shadow: inset 0 1px 0 #fff, inset 1px 0 0 rgb(255 255 255 / 72%), inset 0 -1px 0 rgb(151 121 177 / 15%), 0 18px 32px rgb(83 55 117 / 13%); }
.home-entry-art :deep(.icon) { width: 80px; height: 80px; color: #67438f; stroke-width: 1.25; }
.home-entry h2 { color: var(--aurora-text, #172033); font-size: 36px; line-height: 1.2; letter-spacing: -.035em; font-weight: 650; margin: 0; }
.home-entry h3 { color: var(--aurora-text, #172033); font-size: 21px; line-height: 1.4; font-weight: 600; margin: 10px 0 0; }
.home-entry-card > p { font-size: 16px; color: #535a70; line-height: 1.65; max-width: 290px; margin: 14px 0 22px; text-wrap: balance; }
.home-entry-action { display: inline-flex; align-items: center; justify-content: center; gap: 24px; min-height: 50px; width: min(100%, 176px); margin-top: auto; border: 1px solid transparent; border-radius: 999px; background: var(--aurora-brand, #7652b7); color: #fff; font-size: 15px; font-weight: 550; box-shadow: 0 5px 14px rgb(83 55 117 / 13%); transition: box-shadow .2s, background-color .2s; }
.home-entry-action:hover:not(:disabled) { background: var(--aurora-brand-hover, #6341a0); box-shadow: 0 9px 20px rgb(83 55 117 / 21%); }
.home-entry-action:focus-visible { outline: 2px solid var(--aurora-focus, #865bc7); outline-offset: 3px; }
.home-entry-action > span { font-size: 22px; line-height: 1; font-weight: 400; }
.home-entry-tags { list-style: none; display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 6px; padding: 0; margin: 12px 0 0; }
.home-entry-tags li { color: #51435e; background: var(--entry-tag); border: 1px solid var(--entry-border); padding: 6px 9px; border-radius: 12px; font-size: 12px; line-height: 1.4; white-space: nowrap; }
.home-compose-region { width: 96%; margin: 56px auto 0; }
.home-prompt-suggestions { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
.home-suggestion-label { color: var(--aurora-text-secondary, #606579); font-size: 12px; margin-right: 2px; }
.home-prompt-suggestions button { display: inline-flex; align-items: center; gap: 10px; border: 1px solid var(--aurora-border, rgb(161 139 191 / 20%)); border-radius: 12px; color: var(--aurora-text-secondary, #606579); background: rgb(255 255 255 / 88%); padding: 9px 12px; min-height: 38px; font-size: 12px; }
.home-prompt-suggestions button:hover:not(:disabled) { color: var(--aurora-brand-hover, #6341a0); background: var(--aurora-brand-soft, #f0e9fa); border-color: rgb(161 139 191 / 35%); }
.home-prompt-suggestions button:focus-visible { outline: 2px solid var(--aurora-focus, #865bc7); outline-offset: 2px; }
.home-prompt-suggestions button:disabled { color: var(--aurora-text-secondary, #606579); background: #f7f5fa; border-color: #e4deeb; }
@supports ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
  .home-entry-card {
    background: linear-gradient(125deg, rgb(255 255 255 / 65%), rgb(255 255 255 / 18%) 19%, transparent 34% 57%, rgb(255 255 255 / 30%) 66%, transparent 78%),
      radial-gradient(ellipse 80% 70% at 100% 100%, var(--entry-reflection), transparent 88%),
      linear-gradient(155deg, rgb(255 255 255 / 40%), rgb(255 255 255 / 24%));
    -webkit-backdrop-filter: blur(20px) saturate(125%); backdrop-filter: blur(20px) saturate(125%);
  }
  .home-entry-art { background: linear-gradient(135deg, rgb(255 255 255 / 76%), rgb(255 255 255 / 32%) 48%, rgb(255 255 255 / 52%)), radial-gradient(ellipse at 100% 100%, var(--entry-reflection), transparent 78%); -webkit-backdrop-filter: blur(18px) saturate(120%); backdrop-filter: blur(18px) saturate(120%); }
}
@keyframes home-aurora-mist {
  0%, 100% { transform: translate3d(-6px, 3px, 0); }
  50% { transform: translate3d(6px, -7px, 0); }
}
@media (max-width: 1279px) {
  .home-landing { width: calc(100% - 56px); padding-top: 28px; }
  .home-landing::before { left: -24px; right: -24px; }
  .home-hero h1 { font-size: 38px; }
  .home-description { font-size: 17px; max-width: 680px; }
  .home-feature-grid { gap: 16px; margin-top: 128px; }
  .home-entry-card { padding: 56px 18px 24px; }
  .home-entry-art { width: 138px; height: 138px; top: -100px; }
  .home-entry-art :deep(.icon) { width: 72px; height: 72px; }
  .home-entry h2 { font-size: 32px; }
  .home-entry h3 { font-size: 20px; }
  .home-entry-tags { gap: 4px; }
  .home-entry-tags li { padding: 6px; }
}
@media (min-width: 768px) and (max-width: 1023px) {
  .home-landing { width: calc(100% - 48px); max-width: 700px; }
  .home-feature-grid { grid-template-columns: 1fr; gap: 104px; margin-top: 128px; }
  .home-entry-card { min-height: 270px; padding-top: 64px; }
  .home-entry-art { width: 138px; height: 138px; }
  .home-entry-card > p { max-width: 420px; }
  .home-connector { display: none; }
  .home-compose-region { width: 100%; margin-top: 40px; }
}
@media (max-width: 767px) {
  .home-landing { width: calc(100% - 32px); padding: 22px 0 max(32px, env(safe-area-inset-bottom)); }
  .home-landing::before { inset: 120px 0 200px; opacity: .7; }
  .home-eyebrow { font-size: 11px; margin-bottom: 16px; }
  .home-hero h1 { font-size: clamp(28px, 7.5vw, 34px); line-height: 1.4; letter-spacing: -.025em; }
  .home-description { margin-top: 16px; font-size: 15px; line-height: 1.8; }
  .home-feature-grid { grid-template-columns: 1fr; gap: 82px; margin-top: 90px; }
  .home-connector { display: none; }
  .home-entry-card { min-height: 280px; height: auto; padding: 54px 24px 24px; }
  .home-entry-art { width: 116px; height: 116px; top: -74px; border-radius: 28px; }
  .home-entry-art :deep(.icon) { width: 62px; height: 62px; }
  .home-entry h2 { font-size: 32px; }
  .home-entry h3 { font-size: 20px; margin-top: 8px; }
  .home-entry-card > p { margin: 12px 0 20px; font-size: 16px; }
  .home-entry-tags { gap: 8px; }
  .home-entry-tags li { padding: 6px 9px; }
  .home-compose-region { width: 100%; margin-top: 32px; }
  .home-prompt-suggestions { justify-content: flex-start; margin-top: 16px; }
  .home-suggestion-label { width: 100%; }
  .home-prompt-suggestions button { min-height: 44px; }
}
@media (prefers-reduced-motion: reduce) {
  .home-landing::before { animation: none; transform: none; }
  .home-entry-card, .home-entry-action { transition: none; }
  .home-entry-card:hover { transform: none; }
}
</style>
