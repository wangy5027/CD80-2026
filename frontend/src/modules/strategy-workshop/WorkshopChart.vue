<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useWorkshopLanguage } from './language.js';
import { chartModel, number, percent } from './format.js';

const props = defineProps({
  strategy: { type: Array, default: () => [] },
  benchmark: { type: Array, default: () => [] },
  field: { type: String, default: 'net_value' },
  diagnostic: Boolean,
});
const { say } = useWorkshopLanguage();
const page = ref(0);
const pageSize = 30;
const chartContainer = ref(null);
const chartWidth = ref(760);
let resizeObserver;
const model = computed(() => chartModel(props.strategy, props.benchmark, props.field, chartWidth.value, 240));
const title = computed(() => props.field === 'drawdown' ? say('从之前高点跌了多少（回撤）', '回撤曲线对比') : say('投入 1 元后的变化（净值）', '策略与基准净值对比'));
const display = (value) => props.field === 'drawdown' ? percent(value) : number(value, 4);
const rows = computed(() => {
  const benchmarkByDate = new Map(props.benchmark.map((row) => [row.date, row]));
  return props.strategy.map((row) => ({ date: row.date, strategy: row[props.field], benchmark: benchmarkByDate.get(row.date)?.[props.field] }));
});
const pageCount = computed(() => Math.max(1, Math.ceil(rows.value.length / pageSize)));
const pageRows = computed(() => rows.value.slice(page.value * pageSize, (page.value + 1) * pageSize));

onMounted(() => {
  if (typeof ResizeObserver === 'undefined' || !chartContainer.value) return;
  resizeObserver = new ResizeObserver((entries) => {
    const width = entries[0]?.contentRect.width;
    if (Number.isFinite(width) && width > 0) chartWidth.value = Math.max(300, Math.min(760, Math.round(width)));
  });
  resizeObserver.observe(chartContainer.value);
});
onBeforeUnmount(() => resizeObserver?.disconnect());
</script>

<template>
  <section ref="chartContainer" class="sw-chart" :aria-label="title">
    <div class="sw-chart-heading">
      <h3>{{ title }} <span>{{ field === 'drawdown' ? '单位：%' : say('都从 1 开始比较', '初始净值：1') }}</span></h3>
      <div class="sw-chart-legend"><span><i class="strategy-line" />{{ say('按你的规则买卖', '策略') }}</span><span><i class="benchmark-line" />{{ say('买入后一直拿着作对照', '同标的买入并持有基准') }}</span></div>
    </div>
    <p v-if="diagnostic" class="sw-chart-warning">{{ say('这是试跑结果，不是正式收益报告', '工程诊断结果，非正式收益报告') }}</p>
    <p v-if="model.empty" class="sw-chart-empty">{{ model.reason }}</p>
    <template v-else>
      <svg :viewBox="`0 0 ${model.width} ${model.height}`" role="img" :aria-label="`${title}：${model.firstDate} 至 ${model.lastDate}，${say('最后按策略买卖', '期末策略')} ${display(model.strategyEnd)}，${say('买入后一直持有', '基准')} ${display(model.benchmarkEnd)}。完整数值见下方表格。`">
        <g v-for="(tick, index) in model.ticks" :key="index">
          <line :x1="model.inset.left" :x2="model.width - model.inset.right" :y1="tick.y" :y2="tick.y" class="grid-line" />
          <text :x="model.inset.left - 9" :y="tick.y + 4" text-anchor="end">{{ tick.label }}</text>
        </g>
        <path :d="model.benchmarkPath" class="benchmark-path" />
        <path :d="model.strategyPath" class="strategy-path" />
        <text :x="model.inset.left" :y="model.height - 8">{{ model.firstDate }}</text>
        <text :x="model.width - model.inset.right" :y="model.height - 8" text-anchor="end">{{ model.lastDate }}</text>
      </svg>
      <p class="sw-chart-summary">{{ say('最后按策略买卖', '期末策略') }} {{ display(model.strategyEnd) }} · {{ say('买入后一直持有', '基准') }} {{ display(model.benchmarkEnd) }}。{{ field === 'drawdown' ? say('看的是从之前最高点跌了多少。数字越接近 0，说明离高点越近。', '回撤序列非正；越接近 0，净值距历史峰值的回落越小。') : say('两种方法用的是同一只股票或 ETF、同样的日期、投入和费用。图里从 1 元起算，方便比较，不是账户的实际金额。', '策略与基准采用相同标的、评估区间、初始资本及费用模型，净值初始归一化为 1。') }}</p>
      <details>
        <summary>查看每日数值（{{ rows.length }} 个交易日）</summary>
        <div class="sw-chart-table">
          <table><caption>{{ title }}每日数值</caption><thead><tr><th scope="col">日期</th><th scope="col">{{ say('按规则买卖', '策略') }}</th><th scope="col">{{ say('买入后一直持有', '买入并持有基准') }}</th></tr></thead><tbody><tr v-for="row in pageRows" :key="row.date"><th scope="row">{{ row.date }}</th><td>{{ display(row.strategy) }}</td><td>{{ display(row.benchmark) }}</td></tr></tbody></table>
        </div>
        <div class="sw-chart-pagination"><button type="button" :disabled="page === 0" @click="page--">上一页</button><span>{{ page + 1 }} / {{ pageCount }}</span><button type="button" :disabled="page + 1 >= pageCount" @click="page++">下一页</button></div>
      </details>
    </template>
  </section>
</template>

<style scoped>
.sw-chart{min-width:0;border:1px solid #e3e9ef;border-radius:14px;background:#f8fbfc;padding:18px}.sw-chart-heading{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px}.sw-chart h3{font-size:15px;margin:0}.sw-chart h3 span{font-size:11px;font-weight:400;color:#a6a39e;margin-left:7px}.sw-chart-legend{display:flex;gap:15px;flex-wrap:wrap;font-size:11px;color:#41556a}.sw-chart-legend span{display:inline-flex;align-items:center;gap:6px}.sw-chart-legend i{width:20px;border-top:2px solid #087b80}.sw-chart-legend .benchmark-line{border-top:2px dashed #205fb3}.sw-chart svg{width:100%;height:auto;display:block;margin-top:12px;overflow:visible;font-family:inherit}.sw-chart svg text{fill:#a6a39e;font-size:11px;font-variant-numeric:tabular-nums}.grid-line{stroke:#e3e9ef;stroke-width:1}.strategy-path,.benchmark-path{fill:none;stroke-width:2;vector-effect:non-scaling-stroke;stroke-linejoin:round;stroke-linecap:round}.strategy-path{stroke:#087b80}.benchmark-path{stroke:#205fb3;stroke-dasharray:6 5}.sw-chart-warning{font-size:12px;color:#865714;margin:12px 0 0}.sw-chart-summary,.sw-chart-empty{font-size:12px;color:#a6a39e;line-height:1.7}.sw-chart summary{font-size:12px;cursor:pointer;color:#087b80}.sw-chart-table{overflow:auto;margin-top:12px}.sw-chart table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}.sw-chart caption{text-align:left;color:#a6a39e;padding-bottom:8px}.sw-chart th,.sw-chart td{padding:8px;text-align:right;border-bottom:1px solid #e3e9ef;white-space:nowrap}.sw-chart th:first-child{text-align:left;font-weight:400}.sw-chart-pagination{display:flex;justify-content:flex-end;gap:12px;align-items:center;font-size:12px;margin-top:12px}.sw-chart button{border:1px solid #e3e9ef;color:#081321;background:#f2f6f8;border-radius:7px;padding:7px 10px;cursor:pointer}.sw-chart button:disabled{opacity:.4;cursor:default}.sw-chart :focus-visible{outline:2px solid #087b80;outline-offset:3px}@media(max-width:600px){.sw-chart{padding:12px}.sw-chart h3 span{display:block;margin:5px 0 0}.sw-chart-legend{gap:10px}}
</style>
