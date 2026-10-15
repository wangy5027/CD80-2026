"""RSI 简化均值回归：进入超卖区买入，进入超买区退出。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import rsi
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="rsi_reversion",
    name="RSI 均值回归",
    summary="Wilder RSI 进入 30 以下的超卖区时买入，达到 70 的超买区时退出。",
    parameters={"period": 14, "lower_threshold": 30.0, "upper_threshold": 70.0},
    warmup_bars=15,
    sources=(
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi",
    ),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None
) -> Targets:
    """生成收盘后的目标仓位：None 为暖启动，0 为空仓，1 为持有。"""
    config = SPEC.resolved_parameters(parameters)
    period = int(config["period"])
    lower = float(config["lower_threshold"])
    upper = float(config["upper_threshold"])

    # 共享 RSI 使用 14 个价格变动的均值种子，再按 Wilder 公式递推。
    close = [bar.signal_close for bar in bars]
    oscillator = rsi(close, period)
    targets: Targets = [None] * len(bars)
    position = 0

    for i in range(len(bars)):
        if oscillator[i] is None:
            continue
        if position == 0 and oscillator[i] <= lower:
            position = 1
        elif position == 1 and oscillator[i] >= upper:
            position = 0
        targets[i] = position

    return targets
