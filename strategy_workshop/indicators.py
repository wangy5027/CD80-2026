"""Causal indicators, originally implemented from the cited mathematical definitions.

Missing warm-up outputs are None. EMA uses an SMA seed; RSI/ATR use Wilder
smoothing. These conventions are frozen and are not claimed bit-identical to
every third-party library's initialization.
"""
from __future__ import annotations

from math import sqrt
from typing import Sequence

Series = list[float | None]


def _period(period: int) -> None:
    if isinstance(period, bool) or not isinstance(period, int) or period <= 0:
        raise ValueError("period must be a positive integer")


def sma(values: Sequence[float | None], period: int) -> Series:
    _period(period)
    result: Series = [None] * len(values)
    for i in range(period - 1, len(values)):
        window = values[i - period + 1:i + 1]
        if all(value is not None for value in window):
            result[i] = sum(window) / period
    return result


def ema(values: Sequence[float | None], period: int) -> Series:
    _period(period)
    result: Series = [None] * len(values)
    seed: list[float] = []
    previous: float | None = None
    alpha = 2.0 / (period + 1)
    for i, value in enumerate(values):
        if value is None:
            previous = None
            seed = []
        elif previous is None:
            seed.append(value)
            if len(seed) == period:
                previous = sum(seed) / period
                result[i] = previous
        else:
            previous = alpha * value + (1 - alpha) * previous
            result[i] = previous
    return result


def rolling_std(values: Sequence[float], period: int) -> Series:
    """Population standard deviation (ddof=0), as used by this BB template."""
    means = sma(values, period)
    result: Series = [None] * len(values)
    for i, mean in enumerate(means):
        if mean is not None:
            result[i] = sqrt(sum((value - mean) ** 2 for value in values[i - period + 1:i + 1]) / period)
    return result


def rolling_max(values: Sequence[float], period: int) -> Series:
    _period(period)
    return [None if i < period - 1 else max(values[i - period + 1:i + 1]) for i in range(len(values))]


def rolling_min(values: Sequence[float], period: int) -> Series:
    _period(period)
    return [None if i < period - 1 else min(values[i - period + 1:i + 1]) for i in range(len(values))]


def rsi(values: Sequence[float], period: int) -> Series:
    _period(period)
    result: Series = [None] * len(values)
    if len(values) <= period:
        return result
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gain = sum(max(change, 0) for change in changes[:period]) / period
    loss = sum(max(-change, 0) for change in changes[:period]) / period

    def value() -> float:
        if gain == loss == 0:
            return 50.0
        if loss == 0:
            return 100.0
        return 100 - 100 / (1 + gain / loss)

    result[period] = value()
    for i in range(period + 1, len(values)):
        change = changes[i - 1]
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
        result[i] = value()
    return result


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], period: int) -> Series:
    _period(period)
    if not len(high) == len(low) == len(close):
        raise ValueError("OHLC lengths differ")
    result: Series = [None] * len(close)
    if len(close) <= period:
        return result
    ranges = [max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])) for i in range(1, len(close))]
    previous = sum(ranges[:period]) / period
    result[period] = previous
    for i in range(period + 1, len(close)):
        previous = (previous * (period - 1) + ranges[i - 1]) / period
        result[i] = previous
    return result

