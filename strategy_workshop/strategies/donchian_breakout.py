"""Donchian 突破：以当日收盘形成下一交易日的目标仓位。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import rolling_max, rolling_min
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="donchian_breakout",
    name="Donchian 通道突破",
    summary="收盘突破此前 20 日最高价时持有，跌破此前 10 日最低价时清仓，其余维持；参数固定，未经收益优化。",
    parameters={"entry_period": 20, "exit_period": 10},
    warmup_bars=21,
    sources=("https://ta-lib.org/functions/donchian.html",),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None,
) -> Targets:
    """仅使用截至当前日的价格；暖启动返回 None，其后返回 0 或 1。"""
    settings = SPEC.resolved_parameters(parameters)
    close = [bar.signal_close for bar in bars]
    high = [bar.signal_high for bar in bars]
    low = [bar.signal_low for bar in bars]
    upper = rolling_max(high, int(settings["entry_period"]))
    lower = rolling_min(low, int(settings["exit_period"]))
    targets: Targets = []
    target = 0
    for i, value in enumerate(close):
        # 取前一日通道，排除当前日最高/最低价，避免突破永不触发。
        if i == 0 or upper[i - 1] is None or lower[i - 1] is None:
            targets.append(None)
            continue
        # 退出优先；没有触发时继续维持此前的目标状态。
        if value < lower[i - 1]:
            target = 0
        elif value > upper[i - 1]:
            target = 1
        targets.append(target)
    return targets
