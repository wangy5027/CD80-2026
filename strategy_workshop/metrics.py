"""Frozen daily-return conventions; display rounding belongs to the UI."""
from __future__ import annotations

from math import isfinite, sqrt
from statistics import mean, stdev
from typing import Sequence


def performance(equity: Sequence[float], initial_cash: float, closed_pnls: Sequence[float],
                annualization: int = 252, annual_risk_free_rate: float = 0.0) -> dict:
    if not equity or not isfinite(initial_cash) or initial_cash <= 0 or any(not isfinite(value) or value <= 0 for value in equity):
        raise ValueError("Positive initial cash and nonempty positive equity required")
    returns = []
    drawdowns = []
    previous = peak = initial_cash
    for value in equity:
        returns.append(value / previous - 1)
        peak = max(peak, value)
        drawdowns.append(value / peak - 1)
        previous = value
    total_return = equity[-1] / initial_cash - 1
    try:
        annual_return = (1 + total_return) ** (annualization / len(returns)) - 1
    except OverflowError:
        annual_return = None
    daily_risk_free = (1 + annual_risk_free_rate) ** (1 / annualization) - 1
    excess = [value - daily_risk_free for value in returns]
    volatility = stdev(excess) if len(excess) >= 2 else 0.0
    sharpe = sqrt(annualization) * mean(excess) / volatility if volatility > 1e-15 else None
    trade_count = len(closed_pnls)
    unavailable = {}
    if annual_return is None:
        unavailable["annualized_return"] = "numerical_overflow"
    if sharpe is None:
        unavailable["sharpe_ratio"] = "insufficient_observations" if len(excess) < 2 else "zero_daily_volatility"
    if not trade_count:
        unavailable["win_rate"] = "no_closed_round_trips"
    return {
        "total_return": total_return,
        "annualized_return": annual_return,
        "max_drawdown": -min(drawdowns),
        "sharpe_ratio": sharpe,
        "trade_count": trade_count,
        "win_rate": sum(value > 0 for value in closed_pnls) / trade_count if trade_count else None,
        "unavailable_reasons": unavailable,
        "drawdown": drawdowns,
        "daily_returns": returns,
        "conventions": {
            "annualization": annualization,
            "annual_risk_free_rate": annual_risk_free_rate,
            "sharpe_std_ddof": 1,
            "trade_count_basis": "closed_round_trips",
            "max_drawdown_card": "positive_magnitude",
            "drawdown_curve": "nonpositive",
        },
    }

