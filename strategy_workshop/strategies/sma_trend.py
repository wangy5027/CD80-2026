"""SMA 双均线趋势：以当日收盘形成下一交易日的目标仓位。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import sma
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="sma_trend",
    name="SMA 双均线趋势",
    summary="20 日简单均线高于 60 日简单均线时持有，否则空仓；参数固定，未经收益优化。",
    parameters={"fast_period": 20, "slow_period": 60},
    warmup_bars=60,
    sources=("https://ta-lib.org/functions/sma.html",),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None,
) -> Targets:
    """仅使用截至当前日的价格；暖启动返回 None，其后返回 0 或 1。"""
    settings = SPEC.resolved_parameters(parameters)
    close = [bar.signal_close for bar in bars]
    fast = sma(close, int(settings["fast_period"]))
    slow = sma(close, int(settings["slow_period"]))
    # 首个有效日也按趋势状态建仓，不要求等待一次新的交叉。
    return [None if short is None or long is None else int(short > long)
            for short, long in zip(fast, slow)]
