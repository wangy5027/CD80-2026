"""布林带简化均值回归：跌破下轨入场，回到中轨退出。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import rolling_std, sma
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="bollinger_reversion",
    name="布林带均值回归",
    summary="价格低于布林带下轨时买入，回到中轨时退出；这是固定的简化均值回归模板。",
    parameters={"period": 20, "multiplier": 2.0},
    warmup_bars=20,
    sources=(
        "https://www.bollingerbands.com/bollinger-band-rules",
        "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-overlays/bollinger-bands",
    ),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None
) -> Targets:
    """生成收盘后的目标仓位：None 为暖启动，0 为空仓，1 为持有。"""
    config = SPEC.resolved_parameters(parameters)
    period = int(config["period"])
    multiplier = float(config["multiplier"])

    # 使用统一的复权信号价格；标准差采用总体口径 ddof=0。
    close = [bar.signal_close for bar in bars]
    middle = sma(close, period)
    deviation = rolling_std(close, period)
    targets: Targets = [None] * len(bars)
    position = 0

    for i in range(len(bars)):
        if middle[i] is None or deviation[i] is None:
            continue
        lower = middle[i] - multiplier * deviation[i]
        # 布林带本身不是完整交易系统；以下为本产品固定的交易规则。
        if position == 0 and close[i] < lower:
            position = 1
        elif position == 1 and close[i] >= middle[i]:
            position = 0
        targets[i] = position

    return targets
