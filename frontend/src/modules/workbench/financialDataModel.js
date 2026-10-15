// Keep database values typed. Source identifiers and dates stored as TEXT must
// never be coerced into numbers while they pass through the management page.

export const PAGE_SIZES = [25, 50, 100, 250];
export const MAX_OFFSET = 1_000_000;
export const MAX_FILTERS = 20;

const TABLE_LABELS = {
  cd80_anns_d: '上市公司公告',
  cd80_bond_cb_basic: '可转债基础信息',
  cd80_bond_cb_dd: '可转债日行情',
  cd80_bond_cb_redeem: '可转债赎回',
  cd80_cb_price_chg: '转股价变动',
  cd80_ci_index_member: '申万行业成分',
  cd80_cn_cpi: '居民消费价格指数',
  cd80_cn_lpr: '贷款市场报价利率',
  cd80_daily: '股票日行情',
  cd80_daily_basic: '股票每日指标',
  cd80_etf_basic: 'ETF 基础信息',
  cd80_etf_fund_daily: 'ETF 日行情',
  cd80_etf_index: 'ETF 跟踪指数',
  cd80_etf_share_size: 'ETF 份额规模',
  cd80_factor_list: '量化因子目录',
  cd80_fund_basic: '公募基金基础信息',
  cd80_fund_nav: '公募基金净值',
  cd80_fund_portfolio: '公募基金持仓',
  cd80_index_dd: '指数日行情',
  cd80_ipo: '新股发行',
  cd80_lhb_summary: '龙虎榜明细',
  cd80_limit_down: '跌停股票',
  cd80_limit_surge: '涨停与急涨股票',
  cd80_margin_data: '融资融券数据',
  cd80_spots_sh_gold_dd: '上海金交所黄金行情',
  cd80_stock_basic: '股票基础信息',
  cd80_stock_basic_calendar: '交易日历',
  cd80_stock_basic_managers: '上市公司管理层',
  cd80_stock_fin_balancesheet: '上市公司资产负债表',
  cd80_stock_fin_cashflow: '上市公司现金流量表',
  cd80_stock_fin_dividend: '上市公司分红',
  cd80_stock_fin_income: '上市公司利润表',
  cd80_stock_fin_indicator: '上市公司财务指标',
  cd80_stock_market_pr_factor: '股票复权因子',
  cd80_stock_refer_holders_top10: '十大股东',
  cd80_stock_refer_pledge_stat: '股票质押统计',
  cd80_yc_cb: '中债国债收益率曲线',
};

export function tableLabel(table) {
  return TABLE_LABELS[table?.name] || table?.name || table?.title || '';
}

const COMMON_OPERATORS = [
  { value: 'eq', label: '等于' },
  { value: 'ne', label: '不等于' },
  { value: 'isnull', label: '为空 / 非空' },
];
const NUMBER_OPERATORS = [
  { value: 'gt', label: '大于' },
  { value: 'gte', label: '大于等于' },
  { value: 'lt', label: '小于' },
  { value: 'lte', label: '小于等于' },
];

export function fieldOperators(field) {
  if (!field) return COMMON_OPERATORS;
  if (field.type === 'TEXT') {
    return [...COMMON_OPERATORS, ...NUMBER_OPERATORS, { value: 'contains', label: '包含' }];
  }
  return [...COMMON_OPERATORS, ...NUMBER_OPERATORS];
}

export function parseFieldValue(field, raw) {
  if (field.type === 'TEXT') return String(raw ?? '');
  const input = String(raw ?? '').trim();
  if (!input) throw new Error(`“${field.name}”请输入数值，或选择 NULL。`);
  if (field.type === 'INTEGER') {
    if (!/^[+-]?\d+$/.test(input)) throw new Error(`“${field.name}”必须是整数。`);
    const value = Number(input);
    if (!Number.isSafeInteger(value)) {
      throw new Error(`“${field.name}”超出浏览器可精确处理的整数范围。`);
    }
    return value;
  }
  if (field.type === 'REAL') {
    if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/.test(input)) {
      throw new Error(`“${field.name}”必须是有限数值。`);
    }
    const value = Number(input);
    if (!Number.isFinite(value)) throw new Error(`“${field.name}”必须是有限数值。`);
    return value;
  }
  throw new Error(`“${field.name}”的数据类型不受支持。`);
}

export function buildFilters(drafts, fields) {
  if (drafts.length > MAX_FILTERS) throw new Error(`最多可设置 ${MAX_FILTERS} 个筛选条件。`);
  const byName = new Map(fields.map((field) => [field.name, field]));
  return drafts.map((draft) => {
    const field = byName.get(draft.field);
    if (!field) throw new Error('请先选择筛选字段。');
    if (!fieldOperators(field).some((item) => item.value === draft.op)) {
      throw new Error(`“${field.name}”不支持这个筛选方式。`);
    }
    if (draft.op === 'isnull') {
      return { field: field.name, op: 'isnull', value: draft.nullMode !== 'notnull' };
    }
    return { field: field.name, op: draft.op, value: parseFieldValue(field, draft.value) };
  });
}

export function initialFormValues(fields, original = null) {
  return Object.fromEntries(
    fields.filter((field) => field.name !== '_row_id').map((field) => [
      field.name,
      { mode: 'omit', text: original?.[field.name] == null ? '' : String(original[field.name]) },
    ]),
  );
}

export function buildWriteData(fields, entries, original = null) {
  const data = {};
  for (const field of fields) {
    if (field.name === '_row_id') continue;
    const entry = entries[field.name];
    if (!entry || entry.mode === 'omit') continue;
    if (entry.mode !== 'null' && entry.mode !== 'value') {
      throw new Error(`“${field.name}”的填写方式不正确。`);
    }
    const value = entry.mode === 'null' ? null : parseFieldValue(field, entry.text);
    if (original && Object.is(original[field.name], value)) continue;
    data[field.name] = value;
  }
  if (!Object.keys(data).length) {
    throw new Error(original ? '请至少修改一个字段。' : '请至少填写一个字段或显式设置 NULL。');
  }
  return data;
}

export function buildPageQuery({ pageSize, pageIndex, sortBy, order, filters, cursors }) {
  if (!PAGE_SIZES.includes(pageSize)) throw new Error('每页条数不正确。');
  if (!Number.isSafeInteger(pageIndex) || pageIndex < 0) throw new Error('页码不正确。');
  const query = { limit: pageSize, sort_by: sortBy, order, filters };
  if (sortBy === '_row_id' && order === 'asc') {
    if (pageIndex > 0) {
      const cursor = cursors[pageIndex];
      if (!Number.isSafeInteger(cursor) || cursor < 1) throw new Error('翻页位置已失效，请回到第一页。');
      query.after_id = cursor;
    }
  } else {
    const offset = pageIndex * pageSize;
    if (!Number.isSafeInteger(offset) || offset > MAX_OFFSET) {
      throw new Error('此排序最多浏览前 100 万条偏移记录。请缩小筛选范围。');
    }
    query.offset = offset;
  }
  return query;
}

export function canMoveToNextPage({ pageSize, pageIndex, sortBy, order, hasMore }) {
  if (!hasMore) return false;
  if (sortBy === '_row_id' && order === 'asc') return true;
  return (pageIndex + 1) * pageSize <= MAX_OFFSET;
}

export function displayValue(value) {
  if (value === null || value === undefined) return 'NULL';
  if (value === '') return '空字符串';
  return String(value);
}
