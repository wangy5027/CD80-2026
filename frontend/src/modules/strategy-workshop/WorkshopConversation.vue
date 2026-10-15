<script setup>
import { useWorkshopLanguage } from './language.js';
import { dateTime, interval, lines, money, statusLabel } from './format.js';
import { isRecordedFailure, recordedEntryText } from './failurePresentation.js';

defineProps({ conversation: { type: Object, required: true }, busy: Boolean });
defineEmits(['open-run', 'open-session', 'back']);
const { say, languageStyleLabel } = useWorkshopLanguage();
const entryStyle = (entry) => entry.languageStyle || entry.details?.customization?.languageStyle || entry.details?.candidate?.languageStyle;

const stageLabel = (stage) => ({ matching: say('听懂想法、找方案', '策略解析与匹配'), selection: say('选好规则', '策略确认'), customization: say('补充想法、改规则', '规则定制'), validation: say('确认规则、检查代码', '规则确认与代码校验'), configuration: say('选择日期、检查数据', '回测配置与数据校验'), backtest: say('用历史数据测试、看结果', '回测执行与绩效结果') }[stage] || stage || '阶段记录');
const roleLabel = (role) => ({ user: '你', assistant: '策略工坊', system: '运行记录' }[role] || '记录');
const customLabel = (status) => ({ needs_clarification: '等待一次需求对齐', alignment_incomplete: '本次无法生成规则', unsupported_data: '需要额外数据', rules_ready: '等待确认规则', validated: '代码校验通过', validation_failed: '代码校验未通过' }[status] || status);
const ruleLabel = (key) => ({ name: '策略名称', summary: '策略说明', entry: '买入条件', exit: '退出条件', warmup_bars: say('先准备多少天数据', '指标预热（日线根数）'), required_features: '额外数据', semantics: say('具体怎么做', '执行规则') }[key] || key);
const valueText = (value) => Array.isArray(value) ? value.length ? value.join('；') : '无' : typeof value === 'object' ? JSON.stringify(value) : String(value ?? '—');
const code = (entry) => entry.details?.customization?.sourceCode || entry.details?.candidate?.sourceCode || '';
const metadata = (entry) => JSON.stringify(entry.details, (key, value) => key === 'sourceCode' ? undefined : value, 2);
</script>

<template>
  <section class="sw-conversation" aria-label="完整阶段对话">
    <div class="sw-conversation-heading">
      <div><p class="sw-conversation-kicker">{{ conversation.runId ? say('这次测试保存的完整对话', '本次回测的会话快照') : '会话历史' }}</p><h2 data-step-heading tabindex="-1">完整阶段对话</h2><p>{{ say('按顺序看看当时说了什么、怎么选、怎么改。对话保留原话，查看不会再运行一次。', '按时序回溯策略假设、分析、约束修订、确认与执行记录。原始表达保持不变，历史查看不触发计算。') }}</p></div>
      <button class="sw-conversation-button" type="button" :disabled="busy" @click="$emit('back')">返回历史列表</button>
    </div>
    <p v-if="conversation.runId" class="sw-conversation-scope">此处展示本次回测保存的对话；后续对策略的修改不会改变这份记录。<button type="button" :disabled="busy" @click="$emit('open-session', { sessionId: conversation.sessionId })">查看整个会话</button></p>
    <p v-if="conversation.complete === false || conversation.notice" class="sw-conversation-notice" role="status">{{ conversation.notice || '这条旧记录未保存完整对话，仅展示可核实的历史内容。' }}</p>
    <p v-if="!conversation.entries?.length" class="sw-conversation-empty">暂无可读取的阶段对话。</p>
    <ol v-else class="sw-conversation-entries">
      <li v-for="entry in conversation.entries" :key="entry.id" class="sw-conversation-entry" :data-stage="entry.stage" :data-role="entry.role" :data-entry-id="entry.id">
        <div class="sw-conversation-meta"><strong>{{ roleLabel(entry.role) }}</strong><span>{{ stageLabel(entry.stage) }}</span><span v-if="entry.role === 'assistant'" class="sw-conversation-style">{{ languageStyleLabel(entryStyle(entry)) }}</span><time>{{ dateTime(entry.at) }}</time></div>
        <h3>{{ isRecordedFailure(entry) ? '当前步骤未完成' : entry.title }}</h3>
        <p v-if="isRecordedFailure(entry) || (entry.text && entry.text !== entry.details?.candidate?.explanation?.understoodIdea)" class="sw-conversation-text">{{ recordedEntryText(entry) }}</p>
        <div v-if="!isRecordedFailure(entry) && entry.details?.clarifications?.length" class="sw-conversation-content"><h4>已保存的补充要求</h4><ul><li v-for="(item, index) in lines(entry.details.clarifications)" :key="index">{{ item }}</li></ul></div>
        <div v-if="!isRecordedFailure(entry) && entry.details?.candidate" class="sw-conversation-content">
          <h4>{{ entry.details.candidate.name }}</h4>
          <template v-if="entry.details.candidate.explanation">
            <template v-for="(label, key) in { understoodIdea: say('当时怎样理解你的想法', '投资假设解析'), implementationApproach: say('可以怎么做', '策略实现路径'), whyThisStrategy: say('为什么推荐它', '模板匹配依据'), coveredParts: say('哪里一样', '已覆盖条件'), uncoveredParts: say('哪里不一样、哪里没做到', '差异与未覆盖约束') }" :key="key">
              <template v-if="entry.details.candidate.explanation[key]?.length"><h4>{{ label }}</h4><p v-for="(part, index) in lines(entry.details.candidate.explanation[key])" :key="index">{{ part }}</p></template>
            </template>
            <p v-if="typeof entry.details.candidate.explanation === 'string'">{{ entry.details.candidate.explanation }}</p>
          </template>
          <template v-if="entry.details.candidate.rules?.length"><h4>当时的交易规则</h4><ul><li v-for="(rule, index) in lines(entry.details.candidate.rules)" :key="index">{{ rule }}</li></ul></template>
          <p v-else-if="entry.details.candidate.summary">{{ entry.details.candidate.summary }}</p>
        </div>
        <div v-if="!isRecordedFailure(entry) && entry.details?.customization" class="sw-conversation-content">
          <p class="sw-conversation-state">{{ customLabel(entry.details.customization.status) }}</p>
          <p v-if="entry.details.customization.summary && entry.details.customization.summary !== entry.text">{{ entry.details.customization.summary }}</p>
          <dl v-if="entry.details.customization.ruleSummary"><template v-for="(value, key) in entry.details.customization.ruleSummary" :key="key"><dt>{{ ruleLabel(key) }}</dt><dd>{{ valueText(value) }}</dd></template></dl>
          <template v-for="(label, key) in { questions: '当时需要你补充的问题', assumptions: '本版本采用的假设', dataGaps: '尚缺少的数据' }" :key="key"><template v-if="entry.details.customization[key]?.length"><h4>{{ label }}</h4><ul><li v-for="(item, index) in lines(entry.details.customization[key])" :key="index">{{ item }}</li></ul></template></template>
          <p v-if="entry.details.customization.validation?.passed === true" class="sw-conversation-success">此版本已通过规则与代码校验。</p>
        </div>
        <div v-if="!isRecordedFailure(entry) && entry.details?.configuration" class="sw-conversation-content"><dl><template v-if="entry.details.configuration.symbol"><dt>{{ say('股票或 ETF', '回测标的') }}</dt><dd>{{ entry.details.configuration.symbol }}</dd><dt>{{ say('当时选的日期', '请求区间') }}</dt><dd>{{ interval(entry.details.configuration) }}</dd><dt>{{ say('模拟投入金额', '初始资本') }}</dt><dd>{{ money(entry.details.configuration.initialCash ?? entry.details.configuration.initial_cash) }}</dd></template><template v-if="entry.details.configuration.mode"><dt>运行方式</dt><dd>{{ entry.details.configuration.mode === 'diagnostic' ? say('数据尚不完整时的试跑', '工程诊断回测') : entry.details.configuration.mode === 'formal' ? say('正式历史测试', '正式回测') : say('按当时确认的设置测试', '按已确认参数回测') }}</dd></template><template v-if="entry.details.configuration.diagnosticAcknowledged"><dt>数据说明</dt><dd>{{ say('当时已了解缺少哪些数据，同意先试跑。', '已确认数据缺口并同意运行工程诊断。') }}</dd></template><template v-if="entry.details.configuration.coverageDecision?.decision === 'accept_actual_interval'"><dt>行情覆盖</dt><dd>{{ say('当时已同意用检查后实际能测试的日期。', '已确认采用校验后的实际可运行区间。') }}</dd></template></dl></div>
        <div v-if="!isRecordedFailure(entry) && entry.details?.preflight" class="sw-conversation-content">
          <dl><dt>{{ say('股票或 ETF', '回测标的') }}</dt><dd>{{ entry.details.preflight.requested?.symbol || '—' }}</dd><dt>{{ say('当时选的日期', '请求区间') }}</dt><dd>{{ interval(entry.details.preflight.requested) }}</dd><dt>{{ say('实际能测试的日期', '实际可运行区间') }}</dt><dd>{{ interval(entry.details.preflight.actual) }}</dd><dt>{{ say('模拟投入金额', '初始资本') }}</dt><dd>{{ money(entry.details.preflight.requested?.initialCash ?? entry.details.preflight.requested?.initial_cash) }}</dd><dt>数据截至</dt><dd>{{ entry.details.preflight.asof || '—' }}</dd></dl>
          <template v-for="(reasons, index) in [entry.details.preflight.coverage?.reasons, entry.details.preflight.gate?.reasons]" :key="index"><ul v-if="reasons?.length"><li v-for="(reason, reasonIndex) in lines(reasons)" :key="reasonIndex">{{ reason }}</li></ul></template>
        </div>
        <details v-if="!isRecordedFailure(entry) && code(entry)" class="sw-conversation-details sw-conversation-code"><summary>查看这一阶段的策略代码</summary><pre tabindex="0"><code>{{ code(entry) }}</code></pre></details>
        <details v-if="entry.details && Object.keys(entry.details).length && !isRecordedFailure(entry)" class="sw-conversation-details"><summary>查看本阶段参数与记录</summary><pre tabindex="0">{{ metadata(entry) }}</pre></details>
      </li>
    </ol>
    <div class="sw-conversation-runs"><h3>这段会话的回测记录</h3><p v-if="!conversation.runs?.length" class="sw-conversation-empty">这段会话还没有发起回测，前面的对话已保存。</p><article v-for="record in conversation.runs || []" :key="record.runId || record.id"><div><strong>{{ record.strategyName || record.candidate?.name || '策略回测' }}</strong><p>{{ record.symbol || record.requested?.symbol || '—' }} · {{ statusLabel(record.status) }} · {{ dateTime(record.createdAt) }}</p></div><button class="sw-conversation-button" type="button" :disabled="busy" :data-run-id="record.runId || record.id" @click="$emit('open-run', record)">查看回测结果</button></article></div>
  </section>
</template>

<style scoped>
.sw-conversation-style{font-size:10px;padding:2px 6px;border:1px solid var(--sw-line);border-radius:12px;color:var(--sw-muted)}

.sw-conversation{background:var(--sw-surface);border:1px solid var(--sw-line);border-radius:16px;padding:24px;min-width:0}.sw-conversation-heading{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}.sw-conversation-heading h2{margin:0 0 8px;font-size:21px}.sw-conversation-heading p{color:var(--sw-muted);font-size:12px;line-height:1.8;margin:0}.sw-conversation-heading .sw-conversation-kicker{font-size:10px;letter-spacing:1px;color:var(--sw-accent);margin-bottom:8px}.sw-conversation-button{border:1px solid #cddae2;background:var(--sw-input);color:var(--sw-text);font:inherit;font-size:12px;border-radius:9px;min-height:36px;padding:8px 12px;cursor:pointer;flex-shrink:0}.sw-conversation-button:disabled{opacity:.5;cursor:default}.sw-conversation button:focus-visible,.sw-conversation summary:focus-visible{outline:2px solid var(--sw-accent);outline-offset:3px}.sw-conversation-scope,.sw-conversation-notice{font-size:12px;padding:12px 14px;margin:20px 0;border:1px solid #bedbd9;background:#eaf8f5;border-radius:9px;line-height:1.9;color:#41556a}.sw-conversation-scope button{background:none;border:0;text-decoration:underline;color:var(--sw-accent);font:inherit;cursor:pointer;padding:0 0 0 8px}.sw-conversation-notice{border-color:#ead8b3;background:#fff7e8;color:#865714}.sw-conversation-entries{list-style:none;margin:24px 0 0;padding:0}.sw-conversation-entry{border:1px solid var(--sw-line);border-radius:12px;background:var(--sw-panel);padding:18px;min-width:0;overflow-wrap:anywhere}.sw-conversation-entry+.sw-conversation-entry{margin-top:16px}.sw-conversation-entry[data-role=user]{border-left:3px solid #087b80;background:#f2f8f8}.sw-conversation-entry[data-role=system]{background:#f5f7f8}.sw-conversation-meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;font-size:10px;color:var(--sw-muted)}.sw-conversation-meta strong{font-size:12px;color:var(--sw-accent)}.sw-conversation-meta time{margin-left:auto}.sw-conversation-entry h3{font-size:15px;margin:12px 0 8px}.sw-conversation-entry h4{font-size:12px;color:#081321;margin:17px 0 5px}.sw-conversation-entry p,.sw-conversation-entry li,.sw-conversation-entry dd{font-size:13px;line-height:1.9;white-space:pre-wrap;color:#41556a}.sw-conversation-entry p{margin:8px 0}.sw-conversation-entry ul{padding-left:19px;margin:6px 0}.sw-conversation-entry .sw-conversation-text{color:#081321}.sw-conversation-entry .sw-conversation-state{color:var(--sw-accent);font-size:12px}.sw-conversation-entry .sw-conversation-success{color:#236744}.sw-conversation-entry dl{display:grid;grid-template-columns:100px minmax(0,1fr);gap:6px 12px;font-size:12px;margin:15px 0}.sw-conversation-entry dt{color:var(--sw-muted)}.sw-conversation-entry dd{margin:0}.sw-conversation-details{font-size:12px;margin:16px 0 0;padding-top:12px;border-top:1px solid var(--sw-line)}.sw-conversation-details summary{color:var(--sw-accent);cursor:pointer}.sw-conversation-details pre{max-height:300px;overflow:auto;max-width:100%;padding:12px;background:#eef3f6;border-radius:8px;white-space:pre;font:11px/1.7 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;overflow-wrap:normal;color:#344456}.sw-conversation-empty{font-size:12px;color:var(--sw-muted);padding:12px 0}.sw-conversation-runs{border-top:1px solid var(--sw-line);margin-top:25px;padding-top:18px}.sw-conversation-runs h3{font-size:15px;margin:0 0 12px}.sw-conversation-runs article{display:flex;align-items:center;justify-content:space-between;gap:14px;padding:13px 0;min-width:0}.sw-conversation-runs article+article{border-top:1px solid var(--sw-line)}.sw-conversation-runs article>div{min-width:0;overflow-wrap:anywhere}.sw-conversation-runs strong{font-size:13px}.sw-conversation-runs p{font-size:11px;color:var(--sw-muted);margin:4px 0 0}@media(max-width:600px){.sw-conversation{padding:16px;border-radius:13px}.sw-conversation-heading{flex-direction:column;gap:12px}.sw-conversation-entry{padding:13px}.sw-conversation-meta time{margin-left:0;width:100%}.sw-conversation-runs article{align-items:flex-start;flex-wrap:wrap}.sw-conversation-entry dl{grid-template-columns:80px minmax(0,1fr)}.sw-conversation-button{min-height:40px}}
</style>
