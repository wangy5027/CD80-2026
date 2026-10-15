"""EMA 趋势入场与 Chandelier 波动退出的固定组合模板。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import atr, ema, rolling_max
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="chandelier_trend",
    name="EMA 趋势与 Chandelier 退出",
    summary="价格站上上升的 EMA 时买入，用近期最高价减三倍 ATR 的 Chandelier 线判断退出。",
    parameters={
        "ema_period": 50,
        "atr_period": 22,
        "lookback_period": 22,
        "multiplier": 3.0,
    },
    warmup_bars=51,
    sources=(
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/chandelier-exit",
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/moving-averages-simple-and-exponential",
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr.md",
    ),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None
) -> Targets:
    """生成收盘后的目标仓位：None 为暖启动，0 为空仓，1 为持有。"""
    config = SPEC.resolved_parameters(parameters)
    ema_period = int(config["ema_period"])
    atr_period = int(config["atr_period"])
    lookback_period = int(config["lookback_period"])
    multiplier = float(config["multiplier"])

    # Chandelier 提供退出线；EMA 入场条件是本产品明确选定的组合规则。
    close = [bar.signal_close for bar in bars]
    high = [bar.signal_high for bar in bars]
    low = [bar.signal_low for bar in bars]
    trend = ema(close, ema_period)
    volatility = atr(high, low, close, atr_period)
    highest = rolling_max(high, lookback_period)
    targets: Targets = [None] * len(bars)
    position = 0

    for i in range(1, len(bars)):
        if (trend[i] is None or trend[i - 1] is None
                or volatility[i] is None or highest[i] is None):
            continue
        # 保留原始滚动窗口定义，退出线可以升降，不额外加入只升不降约束。
        long_line = highest[i] - multiplier * volatility[i]
        if position == 0 and close[i] > trend[i] > trend[i - 1] and close[i] > long_line:
            position = 1
        elif position == 1 and close[i] < long_line:
            position = 0
        targets[i] = position

    return targets
