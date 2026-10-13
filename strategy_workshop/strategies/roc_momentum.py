"""ROC 动量：以当日收盘形成下一交易日的目标仓位。"""
from __future__ import annotations

from typing import Mapping, Sequence

from ..models import Bar, Parameter, StrategySpec, Targets


SPEC = StrategySpec(
    key="roc_momentum",
    name="20 日 ROC 动量",
    summary="当日收盘价相对 20 个交易日前的变化率为正时持有，否则空仓；参数固定，未经收益优化。",
    parameters={"period": 20},
    warmup_bars=21,
    sources=("https://ta-lib.org/functions/roc.html",),
)


def build_targets(
    bars: Sequence[Bar], parameters: Mapping[str, Parameter] | None = None,
) -> Targets:
    """仅使用截至当前日的价格；暖启动返回 None，其后返回 0 或 1。"""
    settings = SPEC.resolved_parameters(parameters)
    close = [bar.signal_close for bar in bars]
    period = int(settings["period"])
    targets: Targets = [None] * len(bars)
    # 相隔 N 个交易日需要 N+1 根收盘价；ROC 以百分数表示。
    for i in range(period, len(close)):
        rate = (close[i] / close[i - period] - 1.0) * 100.0
        targets[i] = int(rate > 0)
    return targets
