"""Reference daily, single-instrument, long-only backtest.

Strategies are trusted local modules. This runner never evaluates user code,
fetches data or places orders. It is independent of the existing web runner.
"""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
from typing import Mapping, Sequence

from .metrics import performance
from .models import BacktestConfig, Bar, CorporateAction, Instrument, validate_bars

CENT = Decimal("0.01")


def _d(value: float | int) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def transaction_cost(notional: Decimal, side: str, date: str, instrument: Instrument,
                     config: BacktestConfig) -> dict[str, Decimal]:
    """Reference fee profile; broker commission is inclusive of exchange fees."""
    commission = _money(max(notional * _d(config.commission_rate), _d(config.minimum_commission)))
    stamp_rate = 0.0
    if instrument.asset_type == "stock" and side == "sell":
        stamp_rate = 0.001 if date < "2023-08-28" else 0.0005
    stamp = _money(notional * _d(stamp_rate))
    transfer = _money(notional * _d(config.stock_transfer_rate)) if instrument.asset_type == "stock" else Decimal(0)
    return {"commission": commission, "stamp_tax": stamp, "transfer_fee": transfer}


def _fill_price(bar: Bar, side: str, instrument: Instrument, config: BacktestConfig) -> tuple[Decimal | None, str | None]:
    if bar.suspended or bar.volume <= 0:
        return None, "suspended_or_zero_volume"
    if side == "buy" and bar.limit_up is not None and bar.open >= bar.limit_up:
        return None, "open_at_upper_limit"
    if side == "sell" and bar.limit_down is not None and bar.open <= bar.limit_down:
        return None, "open_at_lower_limit"
    sign = 1 if side == "buy" else -1
    price = _d(bar.open) * (1 + sign * _d(config.slippage_bps) / 10000)
    tick = _d(instrument.tick_size)
    rounding = ROUND_CEILING if side == "buy" else ROUND_FLOOR
    price = (price / tick).to_integral_value(rounding=rounding) * tick
    # Do not condition an opening fill on the day's subsequently observed H/L.
    if price <= 0:
        return None, "nonpositive_slipped_price"
    if bar.limit_up is not None and price > _d(bar.limit_up):
        return None, "slipped_price_above_upper_limit"
    if bar.limit_down is not None and price < _d(bar.limit_down):
        return None, "slipped_price_below_lower_limit"
    return price, None


def run_backtest(bars: Sequence[Bar], strategy, config: BacktestConfig,
                 instrument: Instrument | None = None,
                 corporate_actions: Sequence[CorporateAction] = (),
                 provenance: Mapping | None = None, strict: bool = False) -> dict:
    validate_bars(bars)
    instrument = instrument or Instrument()
    provenance = dict(provenance or {})
    required = ("unadjusted_execution_prices", "corporate_actions_complete",
                "market_rules_verified", "calendar_complete", "signal_adjustment_consistent")
    missing = [key for key in required if provenance.get(key) is not True]
    if strict and (missing or provenance.get("dataset_kind") != "real_sqlite"):
        raise ValueError(f"Formal report needs verified real data; missing={missing}")
    if instrument.asset_type == "stock" and config.start_date < "2022-04-29":
        raise ValueError("Reference stock fee profile supports dates from 2022-04-29; supply a historical fee model for earlier data")
    selected = [i for i, bar in enumerate(bars) if config.start_date <= bar.date <= config.end_date]
    if not selected:
        raise ValueError("No bars in requested date interval")
    # Exclude any supplied data after the evaluation interval before indicators.
    bars = bars[:selected[-1] + 1]
    targets = strategy.build_targets(bars)
    if len(targets) != len(bars) or any(value not in (None, 0, 1) for value in targets):
        raise ValueError("Invalid strategy target series")
    if selected[0] == 0 or targets[selected[0] - 1] is None:
        raise ValueError("Insufficient warm-up before requested start; propose a later start and obtain confirmation first")
    actions: dict[str, CorporateAction] = {}
    for action in corporate_actions:
        if action.date in actions:
            raise ValueError("Combine corporate actions on the same ex-date first")
        from datetime import date
        date.fromisoformat(action.date)
        if not all(isfinite(value) for value in (action.share_ratio, action.cash_per_share)) or action.share_ratio <= 0 or action.cash_per_share < 0:
            raise ValueError("Invalid corporate action")
        if action.cash_per_share > 0 and action.pay_date is None:
            raise ValueError("Cash dividends require an explicit payment date")
        if action.pay_date is not None and date.fromisoformat(action.pay_date) < date.fromisoformat(action.date):
            raise ValueError("Dividend payment cannot precede ex-date")
        actions[action.date] = action

    def simulate(is_benchmark: bool) -> dict:
        cash = _money(_d(config.initial_cash))
        quantity = Decimal(0)
        bought_index: int | None = None
        entry: dict | None = None
        equity_rows, fills, closed, rejected = [], [], [], []
        total_cost = Decimal(0)
        total_dividend = Decimal(0)
        receivables: list[tuple[str, Decimal]] = []
        for i in selected:
            bar = bars[i]
            cash += sum((amount for pay_date, amount in receivables if pay_date <= bar.date), Decimal(0))
            receivables = [(pay_date, amount) for pay_date, amount in receivables if pay_date > bar.date]
            action = actions.get(bar.date)
            if action and quantity:
                dividend = _money(quantity * _d(action.cash_per_share) * (1 - _d(config.dividend_tax_rate)))
                if action.pay_date is not None and action.pay_date <= bar.date:
                    cash += dividend
                elif dividend:
                    receivables.append((action.pay_date, dividend))
                total_dividend += dividend
                quantity *= _d(action.share_ratio)
                if quantity != quantity.to_integral_value():
                    raise ValueError("Fractional corporate-action shares require a provider-specific settlement rule")
                if entry:
                    entry["dividends"] += dividend
            target = 1 if is_benchmark else (targets[i - 1] if i > 0 else None)
            side = "buy" if target == 1 and not quantity else "sell" if target == 0 and quantity else None
            if side:
                if side == "sell" and bought_index is not None and i - bought_index < instrument.settlement_days:
                    rejected.append({"date": bar.date, "side": side, "reason": "position_not_settled"})
                else:
                    price, reason = _fill_price(bar, side, instrument, config)
                    if price is None:
                        rejected.append({"date": bar.date, "side": side, "reason": reason})
                    else:
                        if side == "buy":
                            affordable = int(cash / price)
                            qty = instrument.lot_size + max(0, (affordable - instrument.lot_size) // instrument.quantity_step) * instrument.quantity_step
                            while qty >= instrument.lot_size:
                                costs = transaction_cost(price * qty, side, bar.date, instrument, config)
                                if price * qty + sum(costs.values()) <= cash:
                                    break
                                qty -= instrument.quantity_step
                            if qty < instrument.lot_size:
                                rejected.append({"date": bar.date, "side": side, "reason": "insufficient_cash_for_minimum_lot"})
                                qty = 0
                        else:
                            qty = int(quantity)
                            costs = transaction_cost(price * qty, side, bar.date, instrument, config)
                        if qty:
                            amount = price * qty
                            cost = sum(costs.values())
                            total_cost += cost
                            if side == "buy":
                                cash -= amount + cost
                                quantity = Decimal(qty)
                                bought_index = i
                                entry = {"entry_date": bar.date, "entry_outlay": amount + cost, "dividends": Decimal(0)}
                            else:
                                cash += amount - cost
                                closed.append({"entry_date": entry["entry_date"], "exit_date": bar.date,
                                               "net_pnl": float(amount - cost + entry["dividends"] - entry["entry_outlay"])})
                                quantity = Decimal(0)
                                bought_index = None
                                entry = None
                            fills.append({"date": bar.date, "signal_date": bars[i - 1].date if not is_benchmark and i > 0 else None,
                                          "side": side, "quantity": qty, "price": float(price), "notional": float(amount),
                                          "costs": {key: float(value) for key, value in costs.items()}})
            receivable = sum((amount for _, amount in receivables), Decimal(0))
            equity = cash + quantity * _d(bar.close) + receivable
            if cash < 0 or equity <= 0:
                raise ArithmeticError("Cash/equity invariant violated")
            equity_rows.append({"date": bar.date, "equity": float(equity), "net_value": float(equity / _d(config.initial_cash)),
                                "cash": float(cash), "dividend_receivable": float(receivable),
                                "quantity": int(quantity), "close": bar.close})
        stats = performance([row["equity"] for row in equity_rows], config.initial_cash,
                            [trade["net_pnl"] for trade in closed], config.annualization, config.annual_risk_free_rate)
        for row, drawdown in zip(equity_rows, stats.pop("drawdown")):
            row["drawdown"] = drawdown
        stats.pop("daily_returns")
        return {"metrics": stats, "equity_baseline": {"date": bars[selected[0]].date, "phase": "before_open",
                                                     "equity": config.initial_cash, "net_value": 1.0, "drawdown": 0.0},
                "equity_curve": equity_rows, "fills": fills, "closed_trades": closed,
                "rejected_orders": rejected, "total_costs": float(total_cost), "cash_dividends_accrued": float(total_dividend),
                "dividend_receivable": float(sum((amount for _, amount in receivables), Decimal(0))),
                "open_position": {"quantity": int(quantity), "market_value": float(quantity * _d(bars[selected[-1]].close)),
                                  "entry_date": entry["entry_date"] if entry else None,
                                  "unrealized_pnl": float(quantity * _d(bars[selected[-1]].close) + entry["dividends"] - entry["entry_outlay"]) if entry else None}}

    data_payload = {"bars": [asdict(bar) for bar in bars], "actions": [asdict(action) for action in corporate_actions]}
    data_hash = sha256(json.dumps(data_payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    code_hash = sha256(Path(strategy.__file__).read_bytes()).hexdigest()
    from .catalog import implementation_hash
    is_real = provenance.get("dataset_kind") == "real_sqlite"
    result = {
        "schema_version": "1.0.0", "engine_version": "reference-1.0.0", "currency": "CNY",
        "implementation_sha256": implementation_hash(),
        "strategy": {"id": strategy.SPEC.key, "name": strategy.SPEC.name, "version": strategy.SPEC.version,
                     "parameters": dict(strategy.SPEC.parameters), "source_sha256": code_hash},
        "instrument": asdict(instrument), "config": asdict(config),
        "actual_interval": {"start_date": bars[selected[0]].date, "end_date": bars[selected[-1]].date,
                            "trading_days": len(selected), "warmup_bars": selected[0]},
        "data_snapshot_sha256": data_hash, "provenance": provenance,
        "report_eligible": is_real and not missing,
        "warnings": [f"Unverified data capability: {key}" for key in missing],
        "strategy_result": simulate(False), "benchmark_result": simulate(True),
    }
    if not is_real:
        result["warnings"].insert(0, "Synthetic/development input: not a real-market performance report")
    if config.dividend_tax_rate == 0:
        result["warnings"].append("Personal dividend withholding tax is not simulated")
    if len(selected) < config.annualization:
        result["warnings"].append("Annualized metrics use less than one year of observations")
    result["warnings"].append("Daily-bar fills are a simulation; order-book queue and market impact are not reconstructed")
    result["warnings"].append("Fixed opening slippage is a model cost; execution is not conditioned on subsequently observed daily high/low")
    return result
