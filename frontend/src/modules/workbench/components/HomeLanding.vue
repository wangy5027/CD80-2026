<script setup>
import exploreArt from '../assets/explore-ribbons.svg';
import buildArt from '../assets/build-cubes.svg';
import evolveArt from '../assets/evolve-orbits.svg';

defineProps({ busy: Boolean, hasSkills: Boolean });
const emit = defineEmits(['explore', 'build', 'applications', 'prompt']);
const entries = [
  { key: 'explore', stage: 'Explore', title: '探索与研究', description: '从问题与资料出发，梳理信息，深入思考。', action: '开始探索', artwork: exploreArt, tags: ['资料梳理', '问题分析', '研究规划'] },
  { key: 'build', stage: 'Build', title: '构建应用', description: '描述处理目标，把重复工作整理成可运行的流程。', action: '开始构建', artwork: buildArt, tags: ['自然语言描述', '流程预览', '确认后运行'] },
  { key: 'applications', stage: 'Evolve', title: '复用与迭代', description: '使用已保存的应用，以新的输入继续完成任务。', action: '查看我的应用', artwork: evolveArt, tags: ['已保存应用', '新输入运行', '反馈与修订'] },
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
      <svg class="home-connector" viewBox="0 0 1100 160" fill="none" preserveAspectRatio="none" aria-hidden="true"><path d="M80 96C280-40 345 160 560 82S860-12 1020 96" stroke="url(#home-connection)" stroke-width="1.2"/><defs><linearGradient id="home-connection"><stop stop-color="#6dbab4"/><stop offset=".5" stop-color="#a2c6ea"/><stop offset="1" stop-color="#e4bd91"/></linearGradient></defs><circle cx="370" cy="90" r="4" fill="#a6cfd0"/><circle cx="765" cy="39" r="4" fill="#c3d4e4"/></svg>
      <article v-for="entry in entries" :key="entry.key" class="home-entry" :class="'home-entry-' + entry.key">
        <div class="home-entry-card">
          <img class="home-entry-art" :src="entry.artwork" alt="" aria-hidden="true" width="300" height="240" />
          <h2>{{ entry.stage }}</h2>
          <h3>{{ entry.title }}</h3>
          <p>{{ entry.description }}</p>
          <button type="button" class="home-entry-action" :disabled="entry.key !== 'applications' && (busy || (entry.key === 'build' && hasSkills))" :title="entry.key === 'build' && hasSkills ? '请先移除输入框中已选技能，再构建应用' : undefined" @click="emit(entry.key)">{{ entry.action }}<span aria-hidden="true">→</span></button>
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
.home-landing::before { content: ''; position: absolute; z-index: -1; pointer-events: none; inset: 110px -48px 200px; background: radial-gradient(ellipse at 15% 35%, rgb(134 222 204 / 19%), transparent 45%), radial-gradient(ellipse at 50% 40%, rgb(159 209 253 / 20%), transparent 48%), radial-gradient(ellipse at 90% 40%, rgb(255 209 164 / 24%), transparent 43%); }
.home-hero { text-align: center; }
.home-eyebrow { margin: 0 0 18px; color: var(--muted); font-size: 13px; font-weight: 600; letter-spacing: .26em; line-height: 1.5; }
.home-hero h1 { margin: 0; font-size: clamp(34px, 3.35vw, 52px); font-weight: 650; letter-spacing: -.035em; line-height: 1.25; text-wrap: balance; color: var(--text); }
.home-description { margin: 20px auto 0; max-width: 850px; font-size: 19px; line-height: 1.7; color: var(--muted); text-wrap: balance; }
.home-feature-grid { position: relative; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 24px; margin-top: 142px; }
.home-connector { position: absolute; width: 100%; height: 160px; top: -148px; opacity: .6; pointer-events: none; z-index: -1; }
.home-entry { min-width: 0; --entry-ink: var(--explore-ink); --entry-action: var(--explore-action); --entry-wash: var(--explore-wash); --entry-border: #d1e8e3; }
.home-entry-build { --entry-ink: var(--build-ink); --entry-action: var(--build-action); --entry-wash: var(--build-wash); --entry-border: #d6e5f5; }
.home-entry-applications { --entry-ink: var(--evolve-ink); --entry-action: var(--evolve-action); --entry-wash: var(--evolve-wash); --entry-border: #edddcc; }
.home-entry-card { position: relative; height: calc(100% - 44px); min-height: 310px; padding: 68px 28px 24px; display: flex; flex-direction: column; align-items: center; text-align: center; border: 1px solid var(--entry-border); border-radius: 24px; background: linear-gradient(155deg, rgb(255 255 255 / 94%), var(--entry-wash)); box-shadow: 0 12px 32px rgb(35 75 100 / 4%); transition: transform .2s, box-shadow .2s; }
.home-entry-card:hover { transform: translateY(-3px); box-shadow: 0 18px 38px rgb(35 75 100 / 8%); }
.home-entry-art { position: absolute; width: min(270px, 96%); height: 216px; object-fit: contain; top: -150px; left: 50%; transform: translateX(-50%); pointer-events: none; }
.home-entry h2 { color: var(--entry-ink); font-size: 36px; line-height: 1.2; letter-spacing: -.035em; font-weight: 650; margin: 0; }
.home-entry h3 { color: var(--entry-ink); font-size: 21px; line-height: 1.4; font-weight: 600; margin: 10px 0 0; }
.home-entry-card > p { font-size: 16px; color: var(--muted); line-height: 1.65; max-width: 290px; margin: 14px 0 22px; text-wrap: balance; }
.home-entry-action { display: inline-flex; align-items: center; justify-content: center; gap: 24px; min-height: 50px; width: min(100%, 176px); margin-top: auto; border: 1px solid transparent; border-radius: 999px; background: var(--entry-action); color: #fff; font-size: 15px; font-weight: 550; transition: box-shadow .2s, filter .2s; }
.home-entry-action:hover:not(:disabled) { box-shadow: 0 5px 14px rgb(27 57 78 / 13%); filter: brightness(1.06); }
.home-entry-action > span { font-size: 22px; line-height: 1; font-weight: 400; }
.home-entry-tags { list-style: none; display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 6px; padding: 0; margin: 12px 0 0; }
.home-entry-tags li { color: var(--entry-ink); background: var(--entry-wash); border: 1px solid var(--entry-border); padding: 6px 9px; border-radius: 12px; font-size: 12px; line-height: 1.4; white-space: nowrap; }
.home-compose-region { width: 96%; margin: 56px auto 0; }
.home-prompt-suggestions { display: flex; align-items: center; justify-content: center; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
.home-suggestion-label { color: var(--dim); font-size: 12px; margin-right: 2px; }
.home-prompt-suggestions button { display: inline-flex; align-items: center; gap: 10px; border: 1px solid var(--line); border-radius: 12px; color: var(--muted); background: rgb(255 255 255 / 80%); padding: 9px 12px; min-height: 38px; font-size: 12px; }
.home-prompt-suggestions button:hover:not(:disabled) { color: var(--accent); background: var(--accent-dark); border-color: #badbd6; }
@media (max-width: 1279px) {
  .home-landing { width: calc(100% - 56px); padding-top: 28px; }
  .home-landing::before { left: -24px; right: -24px; }
  .home-hero h1 { font-size: 38px; }
  .home-description { font-size: 17px; max-width: 680px; }
  .home-feature-grid { gap: 16px; margin-top: 128px; }
  .home-entry-card { padding: 56px 18px 24px; }
  .home-entry-art { height: 188px; top: -132px; }
  .home-entry h2 { font-size: 32px; }
  .home-entry h3 { font-size: 20px; }
  .home-entry-tags { gap: 4px; }
  .home-entry-tags li { padding: 6px; }
}
@media (min-width: 768px) and (max-width: 1023px) {
  .home-landing { width: calc(100% - 48px); max-width: 700px; }
  .home-feature-grid { grid-template-columns: 1fr; gap: 104px; margin-top: 128px; }
  .home-entry-card { min-height: 270px; padding-top: 64px; }
  .home-entry-art { height: 190px; }
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
  .home-entry-art { width: 180px; height: 144px; top: -86px; }
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
  .home-entry-card, .home-entry-action { transition: none; }
  .home-entry-card:hover { transform: none; }
}
</style>
