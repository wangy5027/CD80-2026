<script setup>
import { moduleTitle } from './model.js';

defineProps({
  recommendations: { type: Array, required: true },
  reportId: { type: [String, Number], required: true },
  canStartRun: { type: Boolean, required: true },
});
const emit = defineEmits(['run']);
</script>

<template>
  <section class="explore-report-section explore-next"><span class="explore-section-kicker">NEXT QUESTIONS</span><h3>下一步可以研究</h3><div class="explore-recommendations"><button v-for="(item, index) in recommendations.slice(0, 3)" :key="index" :data-module="item.module" data-testid="explore-recommendation" type="button" :disabled="!canStartRun" @click="emit('run', item, reportId)"><span>{{ moduleTitle(item.module) }}</span><strong>{{ item.question }}</strong><small>{{ item.reason }}</small><em>直接开始研究 →</em></button></div></section>
</template>
