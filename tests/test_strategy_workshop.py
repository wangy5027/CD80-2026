"""Financial and causality contracts for the independent strategy workshop.

All fixtures here are generated examples or temporary SQLite databases. Passing
these tests is not evidence of a run against the user's historical market data.
"""
from __future__ import annotations

from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
from math import sin, sqrt
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from strategy_workshop.catalog import (
    FALLBACK_ID, STRATEGIES, get_strategy, implementation_hash, source_code,
)
from strategy_workshop.engine import run_backtest, transaction_cost
from strategy_workshop.indicators import atr, ema, rolling_std, rsi, sma
from strategy_workshop.metrics import performance
from strategy_workshop.models import BacktestConfig, Bar, CorporateAction, Instrument, StrategySpec
from strategy_workshop.sqlite_source import _connect, inspect_database, load_snapshot


EXPECTED_WARMUPS = {
    "sma_trend": 60,
    "ema_trend": 26,
    "macd_momentum": 34,
    "roc_momentum": 21,
    "donchian_breakout": 21,
    "bollinger_reversion": 20,
    "rsi_reversion": 15,
    "keltner_breakout": 20,
    "chandelier_trend": 51,
    "stochastic_reversal": 19,
}
VERIFIED_CAPABILITIES = {
    "unadjusted_execution_prices": True,
    "corporate_actions_complete": True,
    "market_rules_verified": True,
    "calendar_complete": True,
    "signal_adjustment_consistent": True,
}


def make_bar(day: int, opening: float = 10.0, closing: float | None = None, **kwargs) -> Bar:
    closing = opening if closing is None else closing
    return Bar(
        date=(date(2024, 1, 1) + timedelta(days=day)).isoformat(),
        open=opening,
        high=kwargs.pop("high", max(opening, closing)),
        low=kwargs.pop("low", min(opening, closing)),
        close=closing,
        volume=kwargs.pop("volume", 100000.0),
        **kwargs,
    )


def fixed_strategy(targets):
    """Trusted test module; the engine can hash this real source file."""
    return SimpleNamespace(
        SPEC=StrategySpec("test_targets", "Test targets", "Deterministic fixture", {}, 1, ()),
        build_targets=lambda bars: list(targets),
        __file__=__file__,
    )


def free_config(bars, initial_cash: float = 10000.0, **overrides) -> BacktestConfig:
    values = {
        "start_date": bars[1].date,
        "end_date": bars[-1].date,
        "initial_cash": initial_cash,
        "commission_rate": 0.0,
        "minimum_commission": 0.0,
        "slippage_bps": 0.0,
        "stock_transfer_rate": 0.0,
    }
    values.update(overrides)
    return BacktestConfig(**values)


class IndicatorTests(unittest.TestCase):
    def test_sma_hand_calculated_window(self):
        self.assertEqual(sma([1, 2, 3, 4], 3), [None, None, 2.0, 3.0])

    def test_ema_uses_sma_seed_then_recursive_weight(self):
        self.assertEqual(ema([1, 2, 3, 6], 3), [None, None, 2.0, 4.0])

    def test_ema_missing_value_restarts_seed(self):
        self.assertEqual(ema([1, 2, None, 4, 6, 8], 2), [None, 1.5, None, None, 5.0, 7.0])

    def test_rsi_wilder_seed_and_next_update(self):
        result = rsi([10, 12, 11, 14, 12], 3)
        self.assertEqual(result[:3], [None] * 3)
        self.assertAlmostEqual(result[3], 100 - 100 / 6)
        self.assertAlmostEqual(result[4], 100 - 100 / 2.25)

    def test_rsi_flat_gain_only_and_loss_only_edges(self):
        self.assertEqual(rsi([10, 10, 10, 10], 3)[-1], 50.0)
        self.assertEqual(rsi([10, 11, 12, 13], 3)[-1], 100.0)
        self.assertEqual(rsi([13, 12, 11, 10], 3)[-1], 0.0)

    def test_atr_requires_previous_close_and_wilder_seed(self):
        result = atr([11, 14, 13, 16, 15], [9, 11, 10, 13, 12], [10, 13, 11, 15, 13], 3)
        # The first bar supplies previous close, then TR values are 4, 3, 5, 3.
        self.assertEqual(result[:3], [None] * 3)
        self.assertEqual(result[3], 4.0)
        self.assertAlmostEqual(result[4], 11 / 3)

    def test_bollinger_population_standard_deviation(self):
        result = rolling_std([1, 2, 3], 3)
        self.assertEqual(result[:2], [None, None])
        self.assertAlmostEqual(result[2], sqrt(2 / 3))


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = [
            make_bar(i, 100 + 15 * sin(i / 13) + 3 * sin(i / 3), high=121, low=79)
            for i in range(400)
        ]

    def test_ten_allowlisted_modules_and_consistent_interface(self):
        self.assertEqual(set(STRATEGIES), set(EXPECTED_WARMUPS))
        self.assertEqual(FALLBACK_ID, "sma_trend")
        for key, module in STRATEGIES.items():
            with self.subTest(strategy=key):
                self.assertIs(get_strategy(key), module)
                self.assertIsInstance(module.SPEC, StrategySpec)
                self.assertEqual(module.SPEC.key, key)
                self.assertEqual(module.SPEC.version, "1.0.0")
                self.assertTrue(callable(module.build_targets))
                self.assertIn("def build_targets(", source_code(key))
                self.assertEqual(module.SPEC.resolved_parameters(), dict(module.SPEC.parameters))
                self.assertTrue(all(url.startswith("https://") for url in module.SPEC.sources))
        digest = implementation_hash()
        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, implementation_hash())

    def test_unknown_strategy_and_parameters_are_rejected(self):
        for key in ("unknown", "../../arbitrary.py", "__import__('os')"):
            with self.subTest(strategy=key), self.assertRaises(ValueError):
                get_strategy(key)
        for key, module in STRATEGIES.items():
            with self.subTest(strategy=key), self.assertRaises(ValueError):
                module.build_targets(self.bars, {"unsupported_parameter": 10})

    def test_warmup_exactly_matches_spec_and_target_contract(self):
        for key, module in STRATEGIES.items():
            with self.subTest(strategy=key):
                targets = module.build_targets(self.bars)
                first = EXPECTED_WARMUPS[key] - 1
                self.assertEqual(module.SPEC.warmup_bars, first + 1)
                self.assertEqual(len(targets), len(self.bars))
                self.assertEqual(targets[:first], [None] * first)
                self.assertTrue(all(value in (0, 1) for value in targets[first:]))

    def test_all_strategies_are_causal_under_prefix_truncation(self):
        for key, module in STRATEGIES.items():
            targets = module.build_targets(self.bars)
            for length in (1, 14, 19, 20, 34, 51, 60, 100, 237, 399):
                with self.subTest(strategy=key, length=length):
                    self.assertEqual(module.build_targets(self.bars[:length]), targets[:length])

    def test_flat_prices_do_not_create_trades_or_division_errors(self):
        flat = [make_bar(i, 100) for i in range(120)]
        for key, module in STRATEGIES.items():
            with self.subTest(strategy=key):
                targets = module.build_targets(flat)
                self.assertTrue(all(value == 0 for value in targets if value is not None))
                self.assertEqual(module.build_targets([]), [])

    def test_all_ten_modules_run_reference_backtest(self):
        config = free_config(self.bars, start_date=self.bars[100].date)
        for key, module in STRATEGIES.items():
            with self.subTest(strategy=key):
                result = run_backtest(self.bars, module, config)
                self.assertEqual(result["strategy"]["id"], key)
                self.assertEqual(len(result["strategy_result"]["equity_curve"]), 300)
                self.assertFalse(result["report_eligible"])
                self.assertTrue(all(row["cash"] >= 0 for row in result["strategy_result"]["equity_curve"]))
                self.assertTrue(all(fill["quantity"] % 100 == 0 for fill in result["strategy_result"]["fills"]))


class EngineTests(unittest.TestCase):
    def test_signal_executes_next_day_at_open_not_same_day_close(self):
        bars = [make_bar(0, 10), make_bar(1, 10, 20), make_bar(2, 12, 15), make_bar(3, 15)]
        result = run_backtest(bars, fixed_strategy([0, 1, 1, 1]), free_config(bars))
        fill = result["strategy_result"]["fills"][0]
        self.assertEqual(fill["date"], bars[2].date)
        self.assertEqual(fill["signal_date"], bars[1].date)
        self.assertEqual(fill["price"], 12.0)
        self.assertEqual(fill["side"], "buy")

    def test_last_day_signal_does_not_force_liquidation(self):
        bars = [make_bar(0, 10), make_bar(1, 10), make_bar(2, 12)]
        result = run_backtest(bars, fixed_strategy([1, 1, 0]), free_config(bars))["strategy_result"]
        self.assertEqual([fill["side"] for fill in result["fills"]], ["buy"])
        self.assertEqual(result["open_position"]["quantity"], 1000)
        self.assertEqual(result["equity_curve"][-1]["equity"], 12000)
        self.assertEqual(result["metrics"]["trade_count"], 0)
        self.assertIsNone(result["metrics"]["win_rate"])

    def test_opening_fill_does_not_depend_on_future_high_low(self):
        bars = [make_bar(0, 10), make_bar(1, 10), make_bar(2, 10)]
        wider = [bars[0], replace(bars[1], high=100, low=1), bars[2]]
        config = free_config(bars, slippage_bps=100)
        instrument = Instrument(tick_size=0.01)
        normal = run_backtest(bars, fixed_strategy([1, 1, 1]), config, instrument)["strategy_result"]
        changed = run_backtest(wider, fixed_strategy([1, 1, 1]), config, instrument)["strategy_result"]
        self.assertEqual(len(normal["fills"]), 1)
        self.assertEqual(normal["fills"], changed["fills"])
        self.assertEqual(normal["fills"][0]["price"], 10.10)

    def test_cash_must_cover_minimum_lot_and_commission(self):
        bars = [make_bar(0), make_bar(1)]
        for initial, minimum_fee in ((999, 0), (1000, 5)):
            with self.subTest(initial=initial, commission=minimum_fee):
                config = free_config(bars, initial_cash=initial, minimum_commission=minimum_fee)
                result = run_backtest(bars, fixed_strategy([1, 1]), config)["strategy_result"]
                self.assertEqual(result["fills"], [])
                self.assertEqual(result["rejected_orders"][0]["reason"], "insufficient_cash_for_minimum_lot")
                self.assertEqual(result["equity_curve"][-1]["cash"], initial)

    def test_buy_rejection_at_limit_suspension_or_zero_volume_then_retry(self):
        blocked = (
            (make_bar(1, limit_up=10), "open_at_upper_limit"),
            (make_bar(1, suspended=True), "suspended_or_zero_volume"),
            (make_bar(1, volume=0), "suspended_or_zero_volume"),
        )
        for bar, reason in blocked:
            with self.subTest(reason=reason):
                bars = [make_bar(0), bar, make_bar(2)]
                result = run_backtest(bars, fixed_strategy([1, 1, 1]), free_config(bars))["strategy_result"]
                self.assertEqual(result["rejected_orders"][0]["reason"], reason)
                self.assertEqual(result["fills"][0]["date"], bars[2].date)

    def test_sell_at_lower_limit_is_rejected_until_executable_day(self):
        bars = [make_bar(0), make_bar(1), make_bar(2, limit_down=10), make_bar(3)]
        result = run_backtest(bars, fixed_strategy([1, 0, 0, 0]), free_config(bars))["strategy_result"]
        self.assertEqual(result["rejected_orders"][0]["reason"], "open_at_lower_limit")
        self.assertEqual([fill["date"] for fill in result["fills"]], [bars[1].date, bars[3].date])

    def test_slippage_cannot_cross_known_price_limit(self):
        bars = [make_bar(0), make_bar(1, limit_up=10.05)]
        result = run_backtest(bars, fixed_strategy([1, 1]), free_config(bars, slippage_bps=100))["strategy_result"]
        self.assertEqual(result["fills"], [])
        self.assertEqual(result["rejected_orders"][0]["reason"], "slipped_price_above_upper_limit")

    def test_stock_etf_costs_and_stamp_tax_date_switch(self):
        bars = [make_bar(0), make_bar(1)]
        config = BacktestConfig(bars[1].date, bars[1].date)
        stock = Instrument(asset_type="stock")
        etf = Instrument(asset_type="etf")
        before = transaction_cost(Decimal("10000"), "sell", "2023-08-27", stock, config)
        after = transaction_cost(Decimal("10000"), "sell", "2023-08-28", stock, config)
        bought = transaction_cost(Decimal("10000"), "buy", "2023-08-27", stock, config)
        fund = transaction_cost(Decimal("10000"), "sell", "2023-08-28", etf, config)
        self.assertEqual(before, {"commission": Decimal("5.00"), "stamp_tax": Decimal("10.00"), "transfer_fee": Decimal("0.10")})
        self.assertEqual(after["stamp_tax"], Decimal("5.00"))
        self.assertEqual(bought["stamp_tax"], Decimal("0.00"))
        self.assertEqual(fund, {"commission": Decimal("5.00"), "stamp_tax": Decimal(0), "transfer_fee": Decimal(0)})

    def test_closed_trade_pnl_includes_both_sides_fees(self):
        bars = [make_bar(0), make_bar(1), make_bar(2)]
        result = run_backtest(
            bars, fixed_strategy([1, 0, 0]), free_config(bars, initial_cash=10010, minimum_commission=5)
        )["strategy_result"]
        self.assertEqual(result["total_costs"], 10)
        self.assertEqual(result["closed_trades"][0]["net_pnl"], -10)
        self.assertEqual(result["metrics"]["trade_count"], 1)
        self.assertEqual(result["metrics"]["win_rate"], 0)

    def test_cash_dividend_requires_explicit_payment_date(self):
        bars = [make_bar(0), make_bar(1), make_bar(2)]
        with self.assertRaisesRegex(ValueError, "payment date"):
            run_backtest(bars, fixed_strategy([1, 1, 1]), free_config(bars), corporate_actions=[CorporateAction(bars[2].date, 1)])

    def test_dividend_receivable_counts_in_equity_but_cannot_fund_buy_before_payment(self):
        bars = [make_bar(0), make_bar(1), make_bar(2, 9), make_bar(3), make_bar(4)]
        action = CorporateAction(bars[2].date, cash_per_share=1, pay_date=bars[4].date)
        result = run_backtest(
            bars, fixed_strategy([1, 0, 1, 1, 1]), free_config(bars, initial_cash=1000), corporate_actions=[action]
        )["strategy_result"]
        ex_row, waiting_row, paid_row = result["equity_curve"][1:]
        self.assertEqual(ex_row["dividend_receivable"], 100)
        self.assertEqual(ex_row["equity"], 1000)
        self.assertEqual(ex_row["cash"], 900)
        self.assertEqual(waiting_row["dividend_receivable"], 100)
        self.assertEqual(waiting_row["quantity"], 0)
        self.assertEqual(waiting_row["cash"], 900)
        self.assertEqual(result["rejected_orders"][0]["date"], bars[3].date)
        self.assertEqual(result["rejected_orders"][0]["reason"], "insufficient_cash_for_minimum_lot")
        self.assertEqual(paid_row["dividend_receivable"], 0)
        self.assertEqual(paid_row["quantity"], 100)
        self.assertEqual(result["closed_trades"][0]["net_pnl"], 0)

    def test_formal_report_requires_real_sqlite_and_every_verified_capability(self):
        bars = [make_bar(0), make_bar(1)]
        complete = {"dataset_kind": "real_sqlite", **VERIFIED_CAPABILITIES}
        for bad in (
            {"dataset_kind": "synthetic", **VERIFIED_CAPABILITIES},
            {**complete, "calendar_complete": False},
            {"dataset_kind": "real_sqlite"},
        ):
            with self.subTest(provenance=bad), self.assertRaisesRegex(ValueError, "verified real data"):
                run_backtest(bars, fixed_strategy([1, 1]), free_config(bars), provenance=bad, strict=True)
        # This tests the gate's contract; the generated bars remain test fixtures.
        result = run_backtest(bars, fixed_strategy([1, 1]), free_config(bars), provenance=complete, strict=True)
        self.assertTrue(result["report_eligible"])

    def test_requested_start_requires_ready_signal_before_start(self):
        bars = [make_bar(0), make_bar(1), make_bar(2)]
        for start, targets in ((bars[0].date, [1, 1, 1]), (bars[1].date, [None, 1, 1])):
            with self.subTest(start=start, targets=targets), self.assertRaisesRegex(ValueError, "warm-up"):
                run_backtest(bars, fixed_strategy(targets), free_config(bars, start_date=start))


class MetricTests(unittest.TestCase):
    def test_win_rate_includes_zero_pnl_in_closed_trade_denominator(self):
        result = performance([100, 110], 100, [10, 0, -3])
        self.assertEqual(result["trade_count"], 3)
        self.assertAlmostEqual(result["win_rate"], 1 / 3)
        self.assertIsNone(performance([100, 110], 100, [])["win_rate"])

    def test_initial_capital_is_first_drawdown_peak(self):
        result = performance([90, 95], 100, [])
        self.assertAlmostEqual(result["max_drawdown"], 0.10)
        self.assertAlmostEqual(result["drawdown"][0], -0.10)
        self.assertAlmostEqual(result["drawdown"][1], -0.05)
        self.assertAlmostEqual(result["daily_returns"][0], -0.10)

    def test_drawdown_restarts_peak_after_new_high(self):
        result = performance([90, 120, 90], 100, [])
        self.assertAlmostEqual(result["max_drawdown"], 0.25)
        self.assertEqual(result["drawdown"][1], 0)

    def test_zero_volatility_or_one_observation_has_null_sharpe(self):
        self.assertIsNone(performance([100, 100, 100], 100, [])["sharpe_ratio"])
        self.assertIsNone(performance([110], 100, [])["sharpe_ratio"])

    def test_compound_annualization_and_sample_sharpe_convention(self):
        result = performance([100, 110], 100, [], annualization=2)
        self.assertAlmostEqual(result["total_return"], 0.10)
        self.assertAlmostEqual(result["annualized_return"], 0.10)
        self.assertAlmostEqual(result["sharpe_ratio"], 1.0)
        self.assertEqual(result["conventions"]["sharpe_std_ddof"], 1)


class SQLiteSourceTests(unittest.TestCase):
    def setUp(self):
        # Keep generated data in the writable workspace: Windows sandbox TEMP
        # can deny the canonical-path lookup used by the production adapter.
        self.temp = TemporaryDirectory(prefix="strategy_workshop_test_", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "fixture.sqlite"
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute(
                "CREATE TABLE daily (code TEXT, day INTEGER, opening REAL, highest REAL, lowest REAL, closing REAL, vol REAL, factor REAL, halted INTEGER, ceiling REAL, floor REAL)"
            )
            connection.executemany(
                "INSERT INTO daily VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(symbol, 20240100 + day, 10, 11, 9, 10, 1000, 1.5, 0, 11, 9)
                 for symbol in ("TEST.SH", "OTHER.SH") for day in range(1, 6)],
            )
            connection.execute("CREATE TABLE actions (code TEXT, ex_day INTEGER, cash REAL, ratio REAL, paid_day INTEGER)")
            connection.execute("INSERT INTO actions VALUES ('TEST.SH', 20240104, 0.1, 1, 20240108)")
        self.mapping = {
            "table": "daily",
            "date_format": "%Y%m%d",
            "columns": {
                "symbol": "code", "date": "day", "open": "opening", "high": "highest",
                "low": "lowest", "close": "closing", "volume": "vol", "adjustment_factor": "factor",
                "suspended": "halted", "limit_up": "ceiling", "limit_down": "floor",
            },
            "instrument": {
                "name": "Generated fixture", "asset_type": "etf", "lot_size": 100,
                "quantity_step": 100, "tick_size": 0.001, "settlement_days": 1,
            },
            "corporate_actions": {
                "table": "actions",
                "columns": {"symbol": "code", "date": "ex_day", "cash_per_share": "cash", "share_ratio": "ratio", "pay_date": "paid_day"},
            },
            "verified_capabilities": dict(VERIFIED_CAPABILITIES),
        }

    def load(self, mapping=None, **kwargs):
        return load_snapshot(
            self.path, self.mapping if mapping is None else mapping, "TEST.SH", "2024-01-03", "2024-01-04", **kwargs
        )

    def test_schema_inspection_and_compact_date_mapped_snapshot_are_read_only(self):
        before = sha256(self.path.read_bytes()).hexdigest()
        schema = inspect_database(self.path)
        self.assertEqual(set(schema), {"daily", "actions"})
        self.assertIn({"name": "day", "type": "INTEGER"}, schema["daily"])
        bars, actions, instrument, provenance = self.load(warmup_bars=1)
        self.assertEqual([bar.date for bar in bars], ["2024-01-02", "2024-01-03", "2024-01-04"])
        self.assertEqual(len(bars), 3)
        self.assertEqual(bars[0].signal_close, 15)
        self.assertFalse(bars[0].suspended)
        self.assertEqual(bars[0].limit_up, 11)
        self.assertEqual(actions, [CorporateAction("2024-01-04", 0.1, 1, "2024-01-08")])
        self.assertEqual(instrument.code, "TEST.SH")
        self.assertEqual(provenance["dataset_kind"], "real_sqlite")
        self.assertTrue(provenance["read_only"])
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), before)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM daily").fetchone()[0], 10)

    def test_zero_warmup_returns_only_requested_interval(self):
        bars, _, _, _ = self.load(warmup_bars=0)
        self.assertEqual([bar.date for bar in bars], ["2024-01-03", "2024-01-04"])

    def test_adapter_connection_rejects_writes(self):
        before = sha256(self.path.read_bytes()).hexdigest()
        with closing(_connect(self.path)) as connection:
            self.assertEqual(connection.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM daily")
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), before)

    def test_symbol_filter_is_bound_as_a_value(self):
        with self.assertRaisesRegex(ValueError, "No bars"):
            load_snapshot(self.path, self.mapping, "TEST.SH' OR 1=1 --", "2024-01-03", "2024-01-04")

    def test_iso_date_format_mapping(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE iso_daily AS SELECT * FROM daily")
            connection.execute("UPDATE iso_daily SET day = substr(day,1,4)||'-'||substr(day,5,2)||'-'||substr(day,7,2)")
        mapping = deepcopy(self.mapping)
        mapping["table"] = "iso_daily"
        mapping["date_format"] = "%Y-%m-%d"
        mapping.pop("corporate_actions")
        bars, _, _, _ = self.load(mapping, warmup_bars=1)
        self.assertEqual(bars[0].date, "2024-01-02")
        self.assertEqual(bars[-1].date, "2024-01-04")

    def test_missing_table_field_and_required_mapping_are_rejected(self):
        cases = []
        absent_table = deepcopy(self.mapping)
        absent_table["table"] = "missing_table"
        cases.append(absent_table)
        absent_field = deepcopy(self.mapping)
        absent_field["columns"]["close"] = "missing_field"
        cases.append(absent_field)
        absent_mapping = deepcopy(self.mapping)
        absent_mapping["columns"].pop("volume")
        cases.append(absent_mapping)
        for mapping in cases:
            with self.subTest(mapping=mapping), self.assertRaises(ValueError):
                self.load(mapping)

    def test_invalid_date_format_or_implicit_instrument_rules_are_rejected(self):
        for changed in ("date_format", "instrument"):
            mapping = deepcopy(self.mapping)
            mapping[changed] = "%d/%m/%Y" if changed == "date_format" else {}
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.load(mapping)

    def test_invalid_suspension_and_duplicate_dates_are_rejected(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE daily SET halted=2 WHERE code='TEST.SH' AND day=20240103")
        with self.assertRaisesRegex(ValueError, "suspended"):
            self.load()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("UPDATE daily SET halted=0")
            connection.execute("INSERT INTO daily SELECT * FROM daily WHERE code='TEST.SH' AND day=20240103")
        with self.assertRaisesRegex(ValueError, "unique"):
            self.load()

    def test_sql_like_identifiers_are_not_executed(self):
        before = sha256(self.path.read_bytes()).hexdigest()
        mapping = deepcopy(self.mapping)
        mapping["table"] = 'daily"; DROP TABLE daily; --'
        with self.assertRaises(ValueError):
            self.load(mapping)
        self.assertIn("daily", inspect_database(self.path))
        self.assertEqual(sha256(self.path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
