<script setup>
import { computed } from 'vue';
import { moduleTitle } from './model.js';
import { AI_FAILURE_MESSAGE } from './aiFailurePresentation.js';

const props = defineProps({
  recommendations: { type: Array, required: true },
  reportId: { type: [String, Number], required: true },
  canStartRun: { type: Boolean, required: true },
  finalChoice: { type: Boolean, default: false },
  eventChoice: { type: Boolean, default: false },
  failurePresentation: { type: Boolean, default: false },
  canFinish: { type: Boolean, default: false },
});
const isFinalChoice = computed(() => props.finalChoice || props.eventChoice);
const emit = defineEmits(['run', 'finish']);
</script>

<template>
  <section class="explore-report-section explore-next" :class="{ 'explore-event-next': isFinalChoice }"><span class="explore-section-kicker">NEXT QUESTIONS</span><h3>{{ isFinalChoice ? '接下来，你可以选择' : '下一步可以研究' }}</h3><p v-if="isFinalChoice && (!failurePresentation || !recommendations.length)">{{ recommendations.length ? '本轮分析已完成。可以结束这次探索，也可以从一个角度继续追问。' : AI_FAILURE_MESSAGE }}</p><button v-if="isFinalChoice" class="explore-event-finish" data-testid="explore-event-finish" type="button" :disabled="!canFinish" @click="emit('finish')">OK，结束</button><div v-if="recommendations.length" class="explore-recommendations"><button v-for="(item, index) in recommendations.slice(0, 3)" :key="index" :data-module="item.module" data-testid="explore-recommendation" type="button" :disabled="!canStartRun" @click="emit('run', item, reportId)"><template v-if="isFinalChoice"><strong>{{ moduleTitle(item.module) }}：{{ item.question }}</strong></template><template v-else><span>{{ moduleTitle(item.module) }}</span><strong>{{ item.question }}</strong></template><small>{{ item.reason }}</small><em>直接开始研究 →</em></button></div></section>
</template>
