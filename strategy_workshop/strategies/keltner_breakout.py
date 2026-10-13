"""Keltner 通道趋势：突破上轨入场，跌破中轨退出。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import atr, ema
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="keltner_breakout",
    name="Keltner 通道突破",
    summary="价格突破 EMA 与 ATR 构成的上轨时跟随趋势买入，跌破 EMA 中轨时退出。",
    parameters={"ema_period": 20, "atr_period": 10, "multiplier": 2.0},
    warmup_bars=20,
    sources=(
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/keltner-channels",
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
    multiplier = float(config["multiplier"])

    # 共享 ATR 从具有前收盘价的第一根开始累计 TR，需 period+1 根。
    close = [bar.signal_close for bar in bars]
    high = [bar.signal_high for bar in bars]
    low = [bar.signal_low for bar in bars]
    middle = ema(close, ema_period)
    volatility = atr(high, low, close, atr_period)
    targets: Targets = [None] * len(bars)
    position = 0

    for i in range(len(bars)):
        if middle[i] is None or volatility[i] is None:
            continue
        upper = middle[i] + multiplier * volatility[i]
        if position == 0 and close[i] > upper:
            position = 1
        elif position == 1 and close[i] < middle[i]:
            position = 0
        targets[i] = position

    return targets
