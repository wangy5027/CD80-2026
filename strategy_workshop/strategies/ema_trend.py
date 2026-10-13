"""EMA 双均线趋势：以当日收盘形成下一交易日的目标仓位。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import ema
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="ema_trend",
    name="EMA 双均线趋势",
    summary="12 日指数均线高于 26 日指数均线时持有，否则空仓；参数固定，未经收益优化。",
    parameters={"fast_period": 12, "slow_period": 26},
    warmup_bars=26,
    sources=("https://ta-lib.org/functions/ema.html",),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None,
) -> Targets:
    """仅使用截至当前日的价格；暖启动返回 None，其后返回 0 或 1。"""
    settings = SPEC.resolved_parameters(parameters)
    close = [bar.signal_close for bar in bars]
    fast = ema(close, int(settings["fast_period"]))
    slow = ema(close, int(settings["slow_period"]))
    # 共享 EMA 采用前 N 根价格的 SMA 作为种子，随后逐日递推。
    return [None if short is None or long is None else int(short > long)
            for short, long in zip(fast, slow)]
