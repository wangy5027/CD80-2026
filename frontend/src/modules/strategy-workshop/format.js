export const ACTIVE_RUN_STATUSES = new Set(['queued', 'validating', 'running', 'cancelling', 'cancel_requested']);
const STATUS = {
  queued: '排队中', validating: '正在校验数据', running: '正在回测', cancelling: '正在停止…', cancel_requested: '正在停止…',
  completed: '回测完成', succeeded: '回测完成', failed: '运行失败', cancelled: '已取消', interrupted: '已中断',
};
export const statusLabel = (status) => STATUS[status] || status || '等待状态';
export const isActiveRun = (run) => ACTIVE_RUN_STATUSES.has(run?.status);
export const isFinishedRun = (run) => ['completed', 'succeeded'].includes(run?.status);

export function finite(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

export function number(value, digits = 2) {
  const normalized = Object.is(value, -0) ? 0 : value;
  return finite(normalized) ? new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }).format(normalized) : '无法计算';
}

export function percent(value) {
  return finite(value) ? `${number(value * 100)}%` : '无法计算';
}

export function money(value) {
  return finite(value) ? `¥${number(value)}` : '—';
}

export function dateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString('zh-CN', { hour12: false });
}

export function lines(value) {
  if (Array.isArray(value)) return value.filter(Boolean).map((item) => typeof item === 'string' ? item : (item.message || item.description || JSON.stringify(item)));
  return value ? [String(value)] : [];
}

export function interval(value) {
  if (!value) return '—';
  const start = value.startDate || value.start_date;
  const end = value.endDate || value.end_date;
  return start && end ? `${start} 至 ${end}` : '—';
}

export function configFrom(value = {}) {
  return {
    symbol: value.symbol || '510300.SH',
    startDate: value.startDate || value.start_date || '',
    endDate: value.endDate || value.end_date || '',
    initialCash: value.initialCash ?? value.initial_cash ?? 100000,
  };
}

export function validateConfig(config) {
  const errors = {};
  if (!/^[0-9]{6}\.(SH|SZ)$/.test(config.symbol.trim().toUpperCase())) errors.symbol = '请输入六位代码及市场后缀，例如 510300.SH。';
  const validDate = (value) => /^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(`${value}T00:00:00Z`)) && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;
  if (!validDate(config.startDate)) errors.startDate = '请选择有效的开始日期。';
  if (!validDate(config.endDate)) errors.endDate = '请选择有效的结束日期。';
  if (!errors.startDate && !errors.endDate && config.startDate > config.endDate) errors.endDate = '结束日期不能早于开始日期。';
  const cash = Number(config.initialCash);
  if (!Number.isFinite(cash) || cash <= 0 || Math.abs(cash * 100 - Math.round(cash * 100)) > 1e-6) errors.initialCash = '初始资金须大于 0，最多保留两位小数。';
  return errors;
}

export function storageKey(ownerKey, personalAppId = '') {
  const base = `strategy-workshop:v1:${encodeURIComponent(String(ownerKey))}`;
  return personalAppId ? `${base}:personal-app:${encodeURIComponent(String(personalAppId))}` : base;
}

export function loadDraft(storage, ownerKey, personalAppId = '') {
  try {
    const value = JSON.parse(storage.getItem(storageKey(ownerKey, personalAppId)) || 'null');
    if (!value || value.version !== 1) return null;
    return {
      idea: typeof value.idea === 'string' ? value.idea : '',
      sessionId: typeof value.sessionId === 'string' ? value.sessionId : null,
      resultRunId: typeof value.resultRunId === 'string' ? value.resultRunId : null,
      customizationInstructions: typeof value.customizationInstructions === 'string' ? value.customizationInstructions : '',
      config: value.config && typeof value.config === 'object' ? configFrom(value.config) : null,
    };
  } catch { return null; }
}

export function saveDraft(storage, ownerKey, value, personalAppId = '') {
  try { storage.setItem(storageKey(ownerKey, personalAppId), JSON.stringify({ version: 1, ...value })); } catch { /* Server session remains available when browser storage is full/disabled. */ }
}

export function metricCards(result, languageStyle = 'expert') {
  const metrics = result?.strategy_result?.metrics || {};
  const reasons = metrics.unavailable_reasons || {};
  const beginner = languageStyle !== 'expert';
  const copy = (simple, expert) => beginner ? simple : expert;
  return [
    ['total_return', copy('这段时间赚亏了多少（总收益率）', '总收益率'), percent, copy('还没卖出的部分，也按最后一天收盘价算进来。', '包含未平仓持仓收盘估值')],
    ['annualized_return', copy('折算成一年的收益率（年化收益率）', '年化收益率'), percent, copy('把这段历史表现按一年 252 个交易日折算，不代表未来一年能赚多少。', '按 252 个交易日复合年化')],
    ['max_drawdown', copy('从高点最多跌了多少（最大回撤）', '最大回撤'), percent, copy('从之前的最高点到之后低点，跌幅最大的一次。不是相对本金的最大亏损。', '从历史峰值回落的最大幅度')],
    ['sharpe_ratio', copy('收益相对波动的表现（夏普比率）', '夏普比率'), number, copy('把收益和每天涨跌的波动放在一起比较；这里把无风险利率设为 0。', '日超额收益均值 / 样本标准差 × √252；无风险利率为 0')],
    ['trade_count', copy('完成了几次买卖（交易次数）', '交易次数'), (value) => finite(value) ? `${number(value, 0)} 次` : '无法计算', copy('买入后又全部卖出，才算完成一次。', '已平仓完整往返')],
    ['win_rate', copy('完成的买卖中多少次赚钱（胜率）', '胜率'), percent, copy('只看已经买完又卖完的交易，并且扣除费用。', '扣费后盈利的已平仓往返占比')],
  ].map(([key, label, format, hint]) => ({ key, label, value: format(metrics[key]), hint, reason: ({
    no_closed_round_trips: copy('还没有买入后又全部卖出的交易。', '尚无已平仓交易。'), insufficient_observations: copy('可用的交易日太少，暂时算不出来。', '有效交易日观测不足。'),
    zero_daily_volatility: copy('每天的收益没有波动，无法用它计算夏普比率。', '日收益波动为零，夏普比率无法定义。'), numerical_overflow: copy('折算后的数字过大，超出了计算范围。', '样本年化计算超出数值范围。'),
  }[reasons[key]] || reasons[key] || (!finite(metrics[key]) ? (key === 'win_rate' && metrics.trade_count === 0 ? copy('还没有买入后又全部卖出的交易。', '尚无已平仓交易。') : '当前样本无法计算此指标。') : '')) }));
}

export function chartModel(strategyRows = [], benchmarkRows = [], field = 'net_value', width = 760, height = 240) {
  const strategy = strategyRows.filter((row) => row.date && finite(row[field]));
  const benchmark = benchmarkRows.filter((row) => row.date && finite(row[field]));
  const dates = [...new Set([...strategy, ...benchmark].map((row) => row.date))].sort();
  if (dates.length < 2 || strategy.length < 2 || benchmark.length < 2) return { empty: true, reason: '有效数据不足，无法绘制对比曲线。' };
  const all = [...strategy, ...benchmark].map((row) => row[field]);
  let min = Math.min(...all, field === 'drawdown' ? 0 : 1);
  let max = Math.max(...all, field === 'drawdown' ? 0 : 1);
  const span = max - min;
  const pad = span > 0 ? span * 0.08 : Math.max(Math.abs(min) * 0.02, 0.01);
  min -= pad;
  max += pad;
  const inset = { left: 60, right: 18, top: 16, bottom: 30 };
  const plotW = width - inset.left - inset.right;
  const plotH = height - inset.top - inset.bottom;
  const dateIndex = new Map(dates.map((date, index) => [date, index]));
  const x = (date) => inset.left + (dateIndex.get(date) / (dates.length - 1)) * plotW;
  const y = (value) => inset.top + ((max - value) / (max - min)) * plotH;
  const path = (rows) => rows.map((row, index) => `${index ? 'L' : 'M'}${x(row.date).toFixed(2)},${y(row[field]).toFixed(2)}`).join(' ');
  const ticks = Array.from({ length: 4 }, (_, i) => { const value = max - (i / 3) * (max - min); return { y: y(value), label: field === 'drawdown' ? percent(value) : number(value, 2) }; });
  return { empty: false, width, height, inset, ticks, dates, strategyPath: path(strategy), benchmarkPath: path(benchmark), firstDate: dates[0], lastDate: dates.at(-1), strategyEnd: strategy.at(-1)[field], benchmarkEnd: benchmark.at(-1)[field] };
}
