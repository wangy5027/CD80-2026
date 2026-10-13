"""Human-readable, versioned explanations of the ten audited local templates."""
from __future__ import annotations

from strategy_workshop.catalog import STRATEGIES


CONTENT = {
    "sma_trend": {
        "rules": ["20 日简单均线高于 60 日简单均线时持有，否则空仓。", "收盘判断信号，下一可用交易日开盘尝试成交。"],
        "suitableFor": "想跟随一段持续上涨趋势，接受信号滞后。",
        "limitations": ["横盘时可能反复买卖。", "固定 20/60 日规则不能表达估值、新闻或行业判断。"],
    },
    "ema_trend": {
        "rules": ["12 日指数均线高于 26 日指数均线时持有，否则空仓。", "指数均线给予近期价格较高权重，起始值使用简单均线。"],
        "suitableFor": "希望价格趋势改变时更快调整持仓。",
        "limitations": ["更灵敏也可能带来更多无效信号。", "仅使用价格趋势，不使用基本面。"],
    },
    "macd_momentum": {
        "rules": ["计算 12/26 日 EMA 差值及其 9 日 EMA 信号线。", "MACD 柱值大于零时持有，小于等于零时空仓。"],
        "suitableFor": "希望跟随价格动能转强与转弱。",
        "limitations": ["并非背离识别，也不使用零轴过滤。", "震荡行情可能连续产生反向信号。"],
    },
    "roc_momentum": {
        "rules": ["计算当前收盘价相对 20 个交易日前的涨跌幅。", "涨跌幅大于零时持有，否则空仓。"],
        "suitableFor": "看好过去一段时间上涨能够延续。",
        "limitations": ["单一回看窗口可能错过转折。", "不进行多股票强弱排名或组合轮动。"],
    },
    "donchian_breakout": {
        "rules": ["收盘价突破此前 20 根日线最高价时入场。", "跌破此前 10 根日线最低价时退出，否则维持仓位。", "通道不包含当日数据；同时触发时优先退出。"],
        "suitableFor": "认为突破近期高点后会延续趋势。",
        "limitations": ["假突破会造成损失。", "没有海龟交易的加仓与风险预算规则。"],
    },
    "bollinger_reversion": {
        "rules": ["以 20 日简单均线加减两倍总体标准差构建布林带。", "收盘价低于下轨时买入，回到中轨或以上时退出。"],
        "suitableFor": "认为短期过度下跌后价格会回归均值。",
        "limitations": ["持续下跌时可能长期亏损。", "这是明确选定的均值回归模板，触碰下轨本身并非保证买点。"],
    },
    "rsi_reversion": {
        "rules": ["使用 Wilder 平滑的 14 日 RSI。", "RSI 小于等于 30 时买入，大于等于 70 时退出。"],
        "suitableFor": "想把短期超卖后反弹的想法转成规则。",
        "limitations": ["超卖可以持续，并不保证反弹。", "不识别 RSI 背离，不附加止损。"],
    },
    "keltner_breakout": {
        "rules": ["上轨为 20 日 EMA 加两倍 10 日 ATR。", "收盘价突破上轨时买入，跌破 EMA 中轨时退出。"],
        "suitableFor": "希望用价格突破和近期波动共同判断趋势。",
        "limitations": ["波动突然放大时会改变通道宽度。", "不是布林带，波动尺度采用 ATR。"],
    },
    "chandelier_trend": {
        "rules": ["收盘价高于上升的 50 日 EMA，且高于 Chandelier 线时买入。", "Chandelier 线为最近 22 日最高价减三倍 22 日 ATR；跌破该线退出。", "滚动退出线可以上升或下降。"],
        "suitableFor": "希望跟随趋势并使用随波动变化的退出条件。",
        "limitations": ["不是保证损失上限的止损，跳空仍可能扩大损失。", "没有只升不降的追踪止损约束。"],
    },
    "stochastic_reversal": {
        "rules": ["使用 Full Stochastic 14/3/3，K、D 以简单均线平滑。", "K、D 均不高于 20 且 K 上穿 D 时买入。", "K、D 均不低于 80 且 K 下穿 D 时退出。"],
        "suitableFor": "希望用低位动能反转识别反弹。",
        "limitations": ["不等同于 KDJ，也没有 J 值。", "趋势行情可能长期处在高位或低位。"],
    },
}


def public_catalog() -> list[dict]:
    """All explanatory fields are local content, never executable model output."""
    if len(STRATEGIES) != 10 or set(STRATEGIES) != set(CONTENT):
        raise ValueError("The complete audited ten-strategy catalog is required")
    result = []
    for key, module in STRATEGIES.items():
        spec = module.SPEC
        if spec.key != key or not callable(module.build_targets):
            raise ValueError("Invalid curated strategy contract")
        result.append({"strategyId": key, "name": spec.name, "description": spec.summary,
                       "params": dict(spec.parameters), "warmupBars": spec.warmup_bars,
                       "sources": list(spec.sources), "version": spec.version, **CONTENT[key]})
    return result
