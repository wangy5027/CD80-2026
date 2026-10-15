"""MACD 动量：以当日收盘形成下一交易日的目标仓位。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..indicators import ema
from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="macd_momentum",
    name="MACD 动量",
    summary="MACD 采用 12/26/9，差值高于信号线时持有，否则空仓；参数固定，未经收益优化。",
    parameters={"fast_period": 12, "slow_period": 26, "signal_period": 9},
    warmup_bars=34,
    sources=("https://ta-lib.org/functions/macd.html", "https://ta-lib.org/functions/ema.html"),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None,
) -> Targets:
    """仅使用截至当前日的价格；暖启动返回 None，其后返回 0 或 1。"""
    settings = SPEC.resolved_parameters(parameters)
    close = [bar.signal_close for bar in bars]
    fast = ema(close, int(settings["fast_period"]))
    slow = ema(close, int(settings["slow_period"]))
    difference = [None if short is None or long is None else short - long
                  for short, long in zip(fast, slow)]
    signal = ema(difference, int(settings["signal_period"]))
    # 柱值为差值减信号线，不使用部分软件的两倍柱值；只判断正负。
    return [None if value is None or average is None else int(value > average)
            for value, average in zip(difference, signal)]
