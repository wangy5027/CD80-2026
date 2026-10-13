"""Deterministic SYNTHETIC fixtures. Never presented as market history."""
from datetime import date, timedelta
from math import exp, sin
from random import Random

from .models import Bar


def synthetic_bars(kind: str = "regimes", count: int = 1000) -> list[Bar]:
    rng = Random(20261013)
    current = date(2022, 1, 3)
    result = []
    previous = 10.0
    for i in range(count):
        while current.weekday() >= 5:
            current += timedelta(days=1)
        if kind == "constant":
            close = 10.0
        elif kind == "trend":
            close = 10 * exp(0.001 * i)
        elif kind == "oscillating":
            close = 10 + 2.5 * sin(i / 11) + 0.2 * sin(i / 2)
        elif kind == "regimes":
            close = 10 * exp(0.12 * sin(i / 36) + 0.07 * sin(i / 10) + 0.012 * rng.gauss(0, 1))
        else:
            raise ValueError(f"Unknown synthetic fixture: {kind}")
        opening = previous if kind != "constant" else close
        high = max(opening, close) * 1.008
        low = min(opening, close) * 0.992
        result.append(Bar(current.isoformat(), round(opening, 4), round(high, 4), round(low, 4),
                          round(close, 4), 1000000.0))
        previous = close
        current += timedelta(days=1)
    return result

