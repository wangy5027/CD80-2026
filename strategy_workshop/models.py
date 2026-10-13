"""Shared contracts for all curated strategies and the reference backtest runner."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Mapping, Sequence

Parameter = int | float
Targets = list[int | None]


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjustment_factor: float = 1.0
    suspended: bool = False
    limit_up: float | None = None
    limit_down: float | None = None

    def validate(self) -> None:
        from datetime import date

        date.fromisoformat(self.date)
        values = (self.open, self.high, self.low, self.close, self.volume,
                  self.adjustment_factor)
        if not all(isfinite(value) for value in values):
            raise ValueError(f"Non-finite bar: {self.date}")
        if min(self.open, self.high, self.low, self.close, self.adjustment_factor) <= 0:
            raise ValueError(f"Non-positive price/factor: {self.date}")
        if self.volume < 0 or not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError(f"Invalid OHLCV: {self.date}")
        for value in (self.limit_up, self.limit_down):
            if value is not None and (not isfinite(value) or value <= 0):
                raise ValueError(f"Invalid price limit: {self.date}")

    @property
    def signal_close(self) -> float:
        return self.close * self.adjustment_factor

    @property
    def signal_high(self) -> float:
        return self.high * self.adjustment_factor

    @property
    def signal_low(self) -> float:
        return self.low * self.adjustment_factor


@dataclass(frozen=True)
class StrategySpec:
    key: str
    name: str
    summary: str
    parameters: Mapping[str, Parameter]
    warmup_bars: int
    sources: tuple[str, ...]
    version: str = "1.0.0"

    def resolved_parameters(self, overrides: Mapping[str, Parameter] | None = None) -> dict[str, Parameter]:
        """Offline research hook. Phase-one web API must not accept overrides."""
        result = dict(self.parameters)
        if overrides:
            unknown = set(overrides) - set(result)
            if unknown:
                raise ValueError(f"Unknown parameters: {sorted(unknown)}")
            for key, value in overrides.items():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
                    raise ValueError(f"Invalid parameter: {key}")
                if isinstance(result[key], int) and not isinstance(value, int):
                    raise ValueError(f"Integer parameter required: {key}")
                result[key] = value
        for key, value in result.items():
            if (key.endswith("period") or key.endswith("multiplier")) and value <= 0:
                raise ValueError(f"Positive parameter required: {key}")
        if "fast_period" in result and result["fast_period"] >= result["slow_period"]:
            raise ValueError("fast_period must be less than slow_period")
        if "lower_threshold" in result and not 0 <= result["lower_threshold"] < result["upper_threshold"] <= 100:
            raise ValueError("Thresholds must satisfy 0 <= lower < upper <= 100")
        return result


def validate_bars(bars: Sequence[Bar]) -> None:
    if not bars:
        raise ValueError("No bars supplied")
    previous = ""
    for bar in bars:
        bar.validate()
        if bar.date <= previous:
            raise ValueError("Bars must be unique and strictly date-ascending")
        previous = bar.date


@dataclass(frozen=True)
class Instrument:
    code: str = "510300.SH"
    name: str = "华泰柏瑞沪深300ETF"
    asset_type: str = "etf"
    lot_size: int = 100
    quantity_step: int = 100
    tick_size: float = 0.001
    settlement_days: int = 1

    def __post_init__(self) -> None:
        from math import isfinite
        if self.asset_type not in ("stock", "etf"):
            raise ValueError("asset_type must be stock or etf")
        if not isinstance(self.lot_size, int) or not isinstance(self.quantity_step, int) or self.lot_size <= 0 or self.quantity_step <= 0 or not isfinite(self.tick_size) or self.tick_size <= 0:
            raise ValueError("Invalid instrument trading units")
        if self.settlement_days not in (0, 1):
            raise ValueError("Reference runner supports T+0/T+1 only")


@dataclass(frozen=True)
class CorporateAction:
    """Applied before ex-date open. Both values use pre-action share count."""
    date: str
    cash_per_share: float = 0.0
    share_ratio: float = 1.0
    pay_date: str | None = None


@dataclass(frozen=True)
class BacktestConfig:
    start_date: str
    end_date: str
    initial_cash: float = 100000.0
    commission_rate: float = 0.0003
    minimum_commission: float = 5.0
    slippage_bps: float = 5.0
    annualization: int = 252
    annual_risk_free_rate: float = 0.0
    stock_transfer_rate: float = 0.00001
    fee_profile_version: str = "reference-cn-1"
    # Reference rates are explicit simulation assumptions, not a broker quote.
    dividend_tax_rate: float = 0.0

    def __post_init__(self) -> None:
        from datetime import date
        from decimal import Decimal

        start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        if start > end:
            raise ValueError("start_date must not exceed end_date")
        if not isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        cash = Decimal(str(self.initial_cash))
        if cash != cash.quantize(Decimal("0.01")):
            raise ValueError("initial_cash supports at most two decimal places")
        values = (self.commission_rate, self.minimum_commission, self.slippage_bps,
                  self.stock_transfer_rate, self.dividend_tax_rate)
        if not all(isfinite(value) and value >= 0 for value in values):
            raise ValueError("Fee/slippage parameters must be finite and nonnegative")
        if self.annualization <= 0 or not isfinite(self.annual_risk_free_rate) or self.annual_risk_free_rate <= -1:
            raise ValueError("Invalid annualization/risk-free rate")
        if self.dividend_tax_rate > 1:
            raise ValueError("dividend_tax_rate must not exceed one")

