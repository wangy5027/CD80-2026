import { inject, unref } from 'vue';

export const LANGUAGE_STYLE_KEY = Symbol('workshop-language-style');
export const normalizeLanguageStyle = (value) => value === 'expert' ? 'expert' : 'beginner';
export const languageStorageKey = (owner) => `strategy-workshop:language:v1:${encodeURIComponent(String(owner))}`;

// Keep presentation preferences separate from drafts and server-approved strategy state.
export function loadLanguageStyle(owner) {
  try { return normalizeLanguageStyle(globalThis.localStorage?.getItem(languageStorageKey(owner))); }
  catch { return 'beginner'; }
}
export function saveLanguageStyle(owner, style) {
  try { globalThis.localStorage?.setItem(languageStorageKey(owner), normalizeLanguageStyle(style)); }
  catch { /* The switch remains usable when storage is unavailable. */ }
}
export const languageStyleLabel = (style) => style === 'expert' ? '专家表达' : style === 'beginner' ? '小白表达' : '原始表达';

// Translate only known service explanations. Unknown messages retain their exact meaning.
export function dataMessage(message, style) {
  if (style === 'expert' || typeof message !== 'string') return message;
  const known = {
    '尚未核验完整交易日历；不填补缺失行情日。': '还没核实哪些日期本应开市。缺少的行情数据不会猜着补上。',
    '缺少逐日涨跌停价格、停复牌状态及历史交易规则核验。': '还没逐日核实涨跌停价格、是否暂停交易，以及当时适用的交易规定，所以不能确定每次买卖都能成交。',
    '公司行动覆盖完整性尚未独立核验，个人红利税按明确的模拟假设处理。': '分红、拆股等公司事件是否记录完整，还没有独立核实；个人分红税按页面列出的模拟方式处理。',
    'ETF 缺完整分红和复权因子，本次只检验未经复权的价格规则。': '这只 ETF 缺少完整的分红记录和分红后的价格调整数据（复权因子）。这次只能按原始价格试规则，不能算作完整投资回报。',
    '510300 原始行情缺 2026-06-23、2026-07-15 两个对照交易日；不补值，日线窗口按现有记录计算。': '510300 缺少 2026-06-23 和 2026-07-15 两天的行情。这次不猜数据，按已有记录计算每天的规则。',
  };
  if (known[message]) return known[message];
  let match = message.match(/^请求开始日 (\d{4}-\d{2}-\d{2}) 调整为 (\d{4}-\d{2}-\d{2})（行情覆盖、预热或交易日对齐）。$/);
  if (match) return `你选的开始日期是 ${match[1]}，实际从 ${match[2]} 开始。需要有行情、先积累够计算所需的数据，并对齐可用交易日。`;
  match = message.match(/^请求结束日 (\d{4}-\d{2}-\d{2}) 调整为 (\d{4}-\d{2}-\d{2})（行情覆盖或交易日对齐）。$/);
  if (match) return `你选的结束日期是 ${match[1]}，实际到 ${match[2]} 结束，按现有行情和可用交易日确定。`;
  match = message.match(/^复权因子从 (\d{4}-\d{2}-\d{2}) 起存在缺口，实际区间截在此前，不填值、不跳过内部缺口。$/);
  if (match) return `从 ${match[1]} 起缺少用于调整分红等价格影响的数据（复权因子），所以测试只能在这之前结束。不会猜着补齐，也不会跳过中间的缺口。`;
  return message;
}

export function useWorkshopLanguage() {
  const style = inject(LANGUAGE_STYLE_KEY, 'beginner');
  return {
    say: (beginner, expert) => normalizeLanguageStyle(unref(style)) === 'expert' ? expert : beginner,
    languageStyleLabel,
  };
}
