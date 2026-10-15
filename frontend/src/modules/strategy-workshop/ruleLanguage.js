// Presentation only: read the confirmed rule tree, never modify or evaluate it.
const FIELDS = { open: '开盘时的价格', high: '当天最高价格', low: '当天最低价格', close: '收盘时的价格', volume: '当天成交的数量' };
const COMPARE = { gt: '高于', gte: '高于或等于', lt: '低于', lte: '低于或等于', eq: '等于', ne: '不等于' };
const ARITHMETIC = { add: '加', sub: '减', mul: '乘以', div: '除以' };

function plainNode(node, depth = 0) {
  if (!node || depth > 16) throw new Error('Unsupported presentation rule');
  const text = (value) => plainNode(value, depth + 1);
  const shifted = (value) => node.lag ? `取${node.lag}个交易日前的值：〔${value}〕` : value;
  switch (node.type) {
    case 'const': return String(node.value);
    case 'field': {
      if (!FIELDS[node.name]) throw new Error('Unknown field');
      return shifted(FIELDS[node.name]);
    }
    case 'compare': {
      if (!COMPARE[node.op]) throw new Error('Unknown comparison');
      return `〔${text(node.left)}〕${COMPARE[node.op]}〔${text(node.right)}〕`;
    }
    case 'all': return `以下条件都满足：${node.args.map((item) => `（${text(item)}）`).join('，并且')}`;
    case 'any': return `以下条件至少满足一个：${node.args.map((item) => `（${text(item)}）`).join('，或者')}`;
    case 'not': return `以下条件不成立：（${text(node.arg)}）`;
    case 'cross_above': return `〔${text(node.left)}〕上一交易日不高于〔${text(node.right)}〕，今天变成高于它（上穿）`;
    case 'cross_below': return `〔${text(node.left)}〕上一交易日不低于〔${text(node.right)}〕，今天变成低于它（下穿）`;
    case 'position': return `上一次规则给出的目标是“${node.value === 1 ? '买入并继续持有' : '不持有'}”（指策略目标，不代表已经成交）`;
    case 'arithmetic': {
      const values = node.args.map((item) => `〔${text(item)}〕`);
      if (node.op === 'min' || node.op === 'max') return `取这些数中${node.op === 'min' ? '最小' : '最大'}的一个：${values.join('、')}`;
      if (!ARITHMETIC[node.op]) throw new Error('Unknown arithmetic');
      return values.join(ARITHMETIC[node.op]);
    }
    case 'indicator': {
      const name = node.name;
      const input = text(node.input || { type: 'field', name: 'close' });
      const period = node.period;
      let label;
      if (name === 'sma') label = `最近${period}个交易日「${input}」的平均值`;
      else if (name === 'ema') label = `${period}日指数平均值（越近的数据越重要，EMA；按「${input}」计算）`;
      else if (name === 'rsi') label = `${period}日涨跌强弱评分（RSI；按「${input}」计算）`;
      else if (name === 'roc') label = `「${input}」相对${period}个交易日前的变化百分数（ROC，数值1代表1%）`;
      else if (name === 'atr') label = `${period}日平均波动幅度（ATR，也考虑隔夜跳空）`;
      else if (name === 'std') label = `最近${period}个交易日「${input}」的分散程度（标准差）`;
      else if (name === 'highest' || name === 'lowest') label = `最近${period}个交易日「${input}」的${name === 'highest' ? '最大' : '最小'}值`;
      else if (name.startsWith('macd_')) {
        const labels = { macd_line: '快慢两条指数平均线的差值', macd_signal: '把快慢线差值再次做指数平均得到的信号线', macd_hist: '快慢线差值减去信号线得到的柱值' };
        if (!labels[name]) throw new Error('Unknown MACD');
        label = `${labels[name]}（MACD；快线${node.fast_period ?? 12}日、慢线${node.slow_period ?? 26}日、信号线${node.signal_period ?? 9}日；按「${input}」计算）`;
      } else if (name.startsWith('bollinger_')) {
        const mid = `最近${period}个交易日「${input}」的平均值`;
        if (name === 'bollinger_mid') label = `${mid}（布林带中线）`;
        else if (['bollinger_upper', 'bollinger_lower'].includes(name)) label = `${mid}${name === 'bollinger_upper' ? '加上' : '减去'}${node.multiplier ?? 2}倍的同期标准差（数据分散程度；布林带${name === 'bollinger_upper' ? '上' : '下'}边界）`;
        else throw new Error('Unknown Bollinger band');
      } else if (name === 'donchian_upper' || name === 'donchian_lower') {
        label = `此前${period}个交易日的${name === 'donchian_upper' ? '最高' : '最低'}价格（不含今天，唐奇安通道）`;
      } else if (name === 'stochastic_k' || name === 'stochastic_d') {
        label = `价格在最近${node.lookback_period ?? 14}日高低范围内的位置评分（随机指标${name === 'stochastic_k' ? 'K' : 'D'}；先用${node.k_period ?? 3}日平均得到K，再用${node.d_period ?? 3}日平均得到D）`;
      } else throw new Error('Unknown indicator');
      return shifted(label);
    }
    default: throw new Error('Unknown rule');
  }
}

export function describeRulePlan(plan, mode, originalSummary = {}) {
  if (mode !== 'beginner' || !plan?.entry || !plan?.exit) return originalSummary;
  try {
    return { ...originalSummary, entry: plainNode(plan.entry), exit: plainNode(plan.exit), semantics: [
      '每天收盘后判断买卖条件；到下一个有行情的交易日开盘时尝试模拟买卖。',
      '同时满足买和卖的条件时，先按卖出处理。都不满足时，沿用上一次目标；最开始不持有。',
      '先积累足够的历史天数，才能计算这些条件；天数不够时不发出买卖指令。',
      '规则说“持有”只是买入目标，实际能否成交还要看资金和交易限制。',
      '只使用当天及之前的数据。判断价格经过调整以便前后比较（复权），模拟成交仍用当时原价。',
    ] };
  } catch {
    // Older or newer unknown rules retain their exact stored explanation.
    return originalSummary;
  }
}

const BEGINNER_TEMPLATES = {
  sma_trend: ['把最近20天和60天的收盘价分别求平均。20天的平均价更高，就买入或继续持有；否则卖出或不买。', '这里的天数都是有交易的日子。每天收盘后判断，下一个有行情的交易日开盘尝试模拟买卖。'],
  ema_trend: ['比较12日和26日两条平均价格线，越近的价格分量越大（指数均线，EMA）。12日线更高就买入或继续持有，否则卖出或不买。', '计算刚开始时，先用普通平均价作为起点。'],
  macd_momentum: ['先算12日和26日两条指数平均价格线的差，再把这个差做9日指数平均，得到信号线。', '前面的差高于信号线（MACD柱值大于0）就买入或继续持有；小于或等于0就卖出或不买。'],
  roc_momentum: ['把今天的收盘价和20个交易日前比较。', '价格上涨就买入或继续持有；没涨或下跌就卖出或不买。'],
  donchian_breakout: ['今天的收盘价超过此前20个交易日的最高价，就买入。', '收盘价低于此前10个交易日的最低价，就卖出；其余时候保持原目标。', '前面的高低价不把今天算进去。买和卖同时满足时先卖。'],
  bollinger_reversion: ['先算最近20个交易日的平均收盘价，再上下各加减两倍标准差（价格分散程度），画出布林带。', '收盘价低于下边界时买入；回到平均价格或更高时卖出。价格低并不保证随后会上涨。'],
  rsi_reversion: ['用14日RSI给上涨和下跌的强弱打分，采用Wilder平滑方法。', '评分不高于30就买入，不低于70就卖出。低分不代表一定会反弹。'],
  keltner_breakout: ['先算更重视近期价格的20日平均价格线（EMA），再加两倍10日平均波动幅度（ATR），作为上边界。', '收盘价超过上边界时买入；跌回20日平均价格线以下时卖出。'],
  chandelier_trend: ['收盘价高于50日指数平均线、这条线正在上升，而且收盘价高于下述退出线时，才买入。', '退出线等于最近22个交易日的最高价，减去三倍22日平均波动幅度（ATR）。收盘价跌破它时卖出。', '这条退出线可能上升，也可能下降；它不能保证亏损不超过某个数。'],
  stochastic_reversal: ['看收盘价在最近14个交易日高低价范围中的位置，再连续做两次3日平均，得到K和D两条评分线。', 'K和D都不高于20，且K从不高于D变为高于D时买入。', 'K和D都不低于80，且K从不低于D变为低于D时卖出。这是随机指标14/3/3，不含KDJ的J值。'],
};

export function templateRules(strategyId, mode, originalRules) {
  return mode === 'beginner' && BEGINNER_TEMPLATES[strategyId] ? [...BEGINNER_TEMPLATES[strategyId]] : originalRules;
}
