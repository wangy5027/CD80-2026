<script setup>
defineProps({ strategies: { type: Array, default: () => [] } });
const emit = defineEmits(['open', 'strategy']);
const entries = [
  { view: 'explore', name: 'Explore', description: '理解事件、发现关联、对照历史，保留完整研究过程与来源。' },
  { view: 'strategy-workshop', name: '策略工坊', description: '把投资想法整理成策略，完成诊断回测并保存复用。' },
  { view: 'app-workshop', name: '应用工坊', description: '分析金融需求，使用推荐应用或组合创建个人应用。' },
];
</script>

<template>
  <section class="integrated-apps" aria-label="研究与创作工作区">
    <h2>研究与创作工作区</h2>
    <div class="integrated-app-grid">
      <article v-for="entry in entries" :key="entry.view" class="surface-card">
        <h3>{{ entry.name }}</h3><p>{{ entry.description }}</p>
        <button type="button" class="secondary-button" @click="emit('open', entry.view)">打开{{ entry.name }}</button>
      </article>
    </div>
  </section>
  <section class="integrated-apps" aria-label="我的策略应用">
    <h2>我的策略应用</h2>
    <p v-if="!strategies.length" class="muted">在策略工坊生成规则后，策略会先以未验证状态保存到我的 APP。</p>
    <div v-else class="integrated-app-grid">
      <article v-for="app in strategies" :key="app.id" class="surface-card">
        <span class="tag">{{ app.verification?.status === 'passed' ? '个人策略 · 工程诊断通过' : '个人策略 · 未验证' }}</span><h3>{{ app.name }}</h3>
        <p>{{ app.description || '打开已保存规则，可继续修改或主动验证。' }}</p>
        <button type="button" class="secondary-button" @click="emit('strategy', app)">打开策略详情</button>
      </article>
    </div>
  </section>
</template>

<style scoped>
.integrated-apps { margin-bottom: 28px; }
.integrated-apps h2 { font-size: 18px; margin: 0 0 14px; }
.integrated-app-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); gap: 14px; }
.integrated-app-grid h3 { margin: 0 0 8px; font-size: 16px; }
.integrated-app-grid p,.muted { color: var(--muted); line-height: 1.7; font-size: 14px; }
.integrated-app-grid .surface-card { display: flex; flex-direction: column; align-items: flex-start; }
.integrated-app-grid p { flex: 1; }
</style>
