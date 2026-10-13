"""Full Stochastic 低位金叉入场、高位死叉退出。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import rolling_max, rolling_min, sma
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="stochastic_reversal",
    name="随机指标低位反转",
    summary="Full Stochastic 的 K、D 均在低位且形成金叉时买入，均在高位且形成死叉时退出。",
    parameters={
        "lookback_period": 14,
        "k_period": 3,
        "d_period": 3,
        "lower_threshold": 20.0,
        "upper_threshold": 80.0,
    },
    warmup_bars=19,
    sources=(
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stochastic-oscillator-fast-slow-and-full",
    ),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None
) -> Targets:
    """生成收盘后的目标仓位：None 为暖启动，0 为空仓，1 为持有。"""
    config = SPEC.resolved_parameters(parameters)
    lookback_period = int(config["lookback_period"])
    k_period = int(config["k_period"])
    d_period = int(config["d_period"])
    lower = float(config["lower_threshold"])
    upper = float(config["upper_threshold"])

    # Full Stochastic 使用两级简单均线平滑，不混用 KDJ 的递推公式。
    close = [bar.signal_close for bar in bars]
    high = [bar.signal_high for bar in bars]
    low = [bar.signal_low for bar in bars]
    highest = rolling_max(high, lookback_period)
    lowest = rolling_min(low, lookback_period)
    fast_k: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        if highest[i] is None or lowest[i] is None:
            continue
        spread = highest[i] - lowest[i]
        # 全窗口无振幅时以 50 表示中性，避免零除；这是统一的工程约定。
        fast_k[i] = 50.0 if spread == 0 else 100.0 * (close[i] - lowest[i]) / spread
    k = sma(fast_k, k_period)
    d = sma(k, d_period)
    targets: Targets = [None] * len(bars)
    position = 0

    for i in range(1, len(bars)):
        if k[i] is None or d[i] is None or k[i - 1] is None or d[i - 1] is None:
            continue
        cross_up = k[i - 1] <= d[i - 1] and k[i] > d[i]
        cross_down = k[i - 1] >= d[i - 1] and k[i] < d[i]
        if position == 0 and cross_up and k[i] <= lower and d[i] <= lower:
            position = 1
        elif position == 1 and cross_down and k[i] >= upper and d[i] >= upper:
            position = 0
        targets[i] = position

    return targets
