"""Causal DSL/compiler/guest contract tests for phase-two strategy revisions."""
import ast
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest

from backend.app.modules.agent_platform.executor import SandboxExecutor, SandboxError
from backend.app.modules.strategy_workshop.custom_rules import (
    RuleValidationError, compile_strategy, describe_plan, make_strategy, normalize_plan,
    plan_hash, plan_warmup, reference_targets,
)
from backend.app.modules.strategy_workshop.custom_sandbox import (
    CustomValidationError, sandbox_targets, validate_strategy,
)
from strategy_workshop.catalog import get_strategy
from strategy_workshop.fixtures import synthetic_bars
from strategy_workshop.models import Bar


def field(name="close", lag=0):
    return {"type": "field", "name": name, "lag": lag}


def indicator(name, period=None, **kwargs):
    return {"type": "indicator", "name": name, **({"period": period} if period else {}), **kwargs}


def const(value):
    return {"type": "const", "value": value}


def compare(op, left, right):
    return {"type": "compare", "op": op, "left": left, "right": right}


def arithmetic(op, *args):
    return {"type": "arithmetic", "op": op, "args": list(args)}


def all_of(*args):
    return {"type": "all", "args": list(args)}


def cross(up, left, right):
    return {"type": "cross_above" if up else "cross_below", "left": left, "right": right}


def plan(entry=None, exit=None):
    return {"schema_version": 1, "name": "测试规则", "description": "确认的测试规则", "features": [],
            "entry": entry or compare("gt", indicator("sma", 20), indicator("sma", 60)),
            "exit": exit or compare("lte", indicator("sma", 20), indicator("sma", 60))}


def template_plans():
    close = field()
    sma, ema = (indicator("sma", 20), indicator("sma", 60)), (indicator("ema", 12), indicator("ema", 26))
    hist, roc, rsi = indicator("macd_hist"), indicator("roc", 20), indicator("rsi", 14)
    mid = indicator("ema", 20)
    upper = arithmetic("add", mid, arithmetic("mul", const(2), indicator("atr", 10)))
    trend, prior = indicator("ema", 50), indicator("ema", 50, lag=1)
    stop = arithmetic("sub", indicator("highest", 22, input=field("high")), arithmetic("mul", const(3), indicator("atr", 22)))
    k, d = indicator("stochastic_k"), indicator("stochastic_d")
    return {
        "sma_trend": plan(compare("gt", *sma), compare("lte", *sma)),
        "ema_trend": plan(compare("gt", *ema), compare("lte", *ema)),
        "macd_momentum": plan(compare("gt", hist, const(0)), compare("lte", hist, const(0))),
        "roc_momentum": plan(compare("gt", roc, const(0)), compare("lte", roc, const(0))),
        "donchian_breakout": plan(compare("gt", close, indicator("donchian_upper", 20)), compare("lt", close, indicator("donchian_lower", 10))),
        "bollinger_reversion": plan(compare("lt", close, indicator("bollinger_lower", 20)), compare("gte", close, indicator("bollinger_mid", 20))),
        "rsi_reversion": plan(compare("lte", rsi, const(30)), compare("gte", rsi, const(70))),
        "keltner_breakout": plan(compare("gt", close, upper), compare("lt", close, mid)),
        "chandelier_trend": plan(all_of(compare("gt", close, trend), compare("gt", trend, prior), compare("gt", close, stop)), compare("lt", close, stop)),
        "stochastic_reversal": plan(all_of(cross(True, k, d), compare("lte", k, const(20)), compare("lte", d, const(20))),
                                    all_of(cross(False, k, d), compare("gte", k, const(80)), compare("gte", d, const(80)))),
    }


class EchoExecutor:
    """Protocol fake uses the trusted interpreter; it never executes source."""
    def __init__(self, rule, corrupt=False):
        self.rule, self.corrupt = rule, corrupt

    def status(self):
        return {"available": True, "runtime": "test_protocol_only"}

    def execute(self, code, payload, files, stopped):
        self.files = files
        self.code = code
        cases = []
        for case in payload["cases"]:
            targets = reference_targets(self.rule, [Bar(**item) for item in case["bars"]])
            if self.corrupt:
                targets[-1] = True  # bool equals one in Python, but is not legal here.
            cases.append({"name": case["name"], "targets": targets,
                          "prefixes": [{"bars": count, "passed": True} for count in case.get("prefixes", [])]})
        return {"result": {"cases": cases, "spec": {"key": "custom_" + plan_hash(self.rule)[:16], "warmup": plan_warmup(self.rule)}}}


class CustomRuleTests(unittest.TestCase):
    def test_normalization_is_idempotent_and_hash_reproducible(self):
        rule = plan()
        normalized = normalize_plan(rule)
        self.assertEqual(normalized, normalize_plan(normalized))
        self.assertEqual(plan_hash(rule), plan_hash(normalized))
        self.assertNotIn("lag", rule["entry"]["left"])

    def test_strict_schema_external_features_and_future_lags_fail(self):
        cases = []
        for change in ({"features": [{"key": "sentiment"}]}, {"schema_version": True}, {"code": "import os"}):
            item = plan()
            item.update(change)
            cases.append(item)
        for lag in (-1, True, 251, 1.5):
            cases.append(plan(compare("gt", field(lag=lag), const(1))))
        cases.append(plan(compare("gt", {"type": "feature", "key": "x"}, const(1))))
        cases.append(plan(compare("gt", indicator("sma", 20, source="close"), const(1))))
        for item in cases:
            with self.subTest(item=item), self.assertRaises(RuleValidationError):
                normalize_plan(item)

    def test_boolean_numeric_types_and_nonfinite_numbers_fail(self):
        cases = [plan(compare("gt", const(True), const(0))), plan(compare("gt", const(float("nan")), const(0))),
                 plan(compare("gt", indicator("sma", 1), const(0))), plan(const(1)),
                 plan(compare([], field(), const(1))), plan(compare("gt", {"type": "field", "name": []}, const(1))),
                 plan(compare("gt", indicator("macd_line", fast_period=30, slow_period=20), const(0)))]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(RuleValidationError):
                normalize_plan(item)

    def test_complexity_bounds(self):
        expression = compare("gt", field(), const(1))
        for _ in range(14):
            expression = {"type": "not", "arg": expression}
        with self.assertRaises(RuleValidationError):
            normalize_plan(plan(expression))
        item = indicator("ema", 250)
        for _ in range(4):
            item = indicator("ema", 250, input=item)
        with self.assertRaises(RuleValidationError):
            normalize_plan(plan(compare("gt", item, const(1))))

    def test_all_ten_existing_algorithms_have_identical_reference_targets(self):
        for key, rule in template_plans().items():
            for kind in ("constant", "trend", "oscillating", "regimes"):
                bars = synthetic_bars(kind, 400)
                with self.subTest(key=key, kind=kind):
                    self.assertEqual(reference_targets(rule, bars), get_strategy(key).build_targets(bars))
                    self.assertEqual(plan_warmup(rule), get_strategy(key).SPEC.warmup_bars)

    def test_thresholds_hold_and_exit_has_priority(self):
        bars = synthetic_bars("constant", 5)
        bars = [replace(bar, open=value, high=value, low=value, close=value) for bar, value in zip(bars, (8, 11, 10, 8, 12))]
        rule = plan(compare("gt", field(), const(10)), compare("lt", field(), const(9)))
        self.assertEqual(reference_targets(rule, bars), [0, 1, 1, 0, 1])
        both = plan(compare("gt", const(1), const(0)), compare("gt", const(1), const(0)))
        self.assertEqual(reference_targets(both, bars), [0] * 5)

    def test_position_is_previous_target_and_exit_priority(self):
        rule = plan({"type": "position", "value": 0}, {"type": "position", "value": 1})
        self.assertEqual(reference_targets(rule, synthetic_bars("constant", 5)), [1, 0, 1, 0, 1])
        self.assertIn("不是实际成交持仓", "".join(describe_plan(rule)["semantics"]))

    def test_crossing_equality_and_missing_prior_value(self):
        bars = synthetic_bars("constant", 5)
        bars = [replace(bar, open=value, high=value, low=value, close=value) for bar, value in zip(bars, (10, 11, 10, 9, 11))]
        rule = plan(cross(True, field(), const(10)), cross(False, field(), const(10)))
        self.assertEqual(plan_warmup(rule), 2)
        self.assertEqual(reference_targets(rule, bars), [None, 1, 1, 0, 1])

    def test_boolean_or_and_not_have_strict_prewarmup(self):
        rule = plan({"type": "any", "args": [compare("gt", const(1), const(0)), compare("gt", indicator("sma", 3), const(0))]},
                    {"type": "not", "arg": compare("gt", const(1), const(0))})
        self.assertEqual(reference_targets(rule, synthetic_bars("constant", 5)), [None, None, 1, 1, 1])

    def test_adjusted_price_fields_and_raw_volume(self):
        bars = [replace(bar, adjustment_factor=2) for bar in synthetic_bars("constant", 3)]
        rule = plan(compare("eq", field(), const(20)), compare("lt", field("volume"), const(100)))
        self.assertEqual(reference_targets(rule, bars), [1, 1, 1])

    def test_numeric_arithmetic_zero_division_is_failure(self):
        rule = plan(compare("gt", arithmetic("div", field(), const(0)), const(1)))
        with self.assertRaisesRegex(ValueError, "除零"):
            reference_targets(rule, synthetic_bars("constant", 3))

    def test_lag_and_nested_indicator_warmup(self):
        rule = plan(compare("gt", indicator("ema", 3, input=indicator("sma", 4, lag=2), lag=1), const(0)))
        self.assertEqual(plan_warmup(rule), 60)  # the default exit retains SMA60.
        rule["exit"] = compare("lt", field(), const(0))
        self.assertEqual(plan_warmup(rule), 9)
        self.assertEqual(reference_targets(rule, synthetic_bars("constant", 12)), [None] * 8 + [1] * 4)

    def test_reference_prefix_causality_for_all_templates(self):
        bars = synthetic_bars("regimes", 200)
        for key, rule in template_plans().items():
            full = reference_targets(rule, bars)
            with self.subTest(key=key):
                for count in (21, 61, 150, 199):
                    self.assertEqual(reference_targets(rule, bars[:count]), full[:count])

    def test_compiler_is_deterministic_export_and_name_is_quoted(self):
        rule = plan()
        rule["name"] = "策略'; raise RuntimeError('injection') #"
        source = compile_strategy(rule)
        tree = ast.parse(source)
        self.assertEqual(source, compile_strategy(normalize_plan(rule)))
        self.assertIn("build_targets", [node.name for node in tree.body if isinstance(node, ast.FunctionDef)])
        self.assertLess(len(source.encode()), 32768)
        self.assertNotIn("exec(", source)
        self.assertNotIn("eval(", source)

    def test_guest_protocol_and_reference_equality(self):
        rule = plan()
        executor = EchoExecutor(rule)
        report = validate_strategy(rule, executor=executor)
        self.assertTrue(report["passed"])
        self.assertEqual(len(report["cases"]), 4)
        self.assertEqual(sum(len(item["prefixChecks"]) for item in report["cases"]), 16)
        self.assertEqual(set(executor.files), {"strategy.py", "support.zip"})
        self.assertIn("exec(compile", executor.code)

    def test_guest_bool_target_rejected_even_though_equals_integer(self):
        with self.assertRaises(CustomValidationError):
            sandbox_targets(plan(), synthetic_bars("constant", 80), executor=EchoExecutor(plan(), corrupt=True))

    def test_cancellation_contract_requires_event(self):
        with self.assertRaisesRegex(ValueError, "threading.Event"):
            sandbox_targets(plan(), synthetic_bars("constant", 80), executor=EchoExecutor(plan()), stopped=lambda: False)

    def test_adapter_binds_source_and_exact_frozen_input(self):
        rule, bars = plan(), synthetic_bars("regimes", 90)
        targets = reference_targets(rule, bars)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "strategy.py"
            path.write_bytes(compile_strategy(rule).encode("utf-8"))
            adapter = make_strategy(rule, bars, targets, path)
            self.assertEqual(adapter.build_targets(bars[:80]), targets[:80])
            changed = [replace(bars[0], close=bars[0].open), *bars[1:]]
            with self.assertRaises(RuleValidationError):
                adapter.build_targets(changed)
            path.write_text("# replaced", encoding="utf-8")
            with self.assertRaises(RuleValidationError):
                adapter.build_targets(bars)
            with self.assertRaises(RuleValidationError):
                make_strategy(rule, bars, targets, path)

    def test_wasi_live_generated_code_matches_all_ten_templates(self):
        executor = SandboxExecutor()
        if not executor.status()["available"]:
            self.skipTest("Pinned WASI runtime is unavailable")
        bars = synthetic_bars("oscillating", 180)
        for key, rule in template_plans().items():
            with self.subTest(key=key):
                self.assertEqual(sandbox_targets(rule, bars, executor=executor), get_strategy(key).build_targets(bars))

    def test_wasi_live_full_validation_and_cancel(self):
        executor = SandboxExecutor()
        if not executor.status()["available"]:
            self.skipTest("Pinned WASI runtime is unavailable")
        evidence = validate_strategy(plan(), executor=executor)
        self.assertTrue(evidence["passed"])
        self.assertEqual(evidence["runtime"]["runtime"], "python-wasi")
        stopped = threading.Event()
        stopped.set()
        with self.assertRaises(SandboxError) as raised:
            sandbox_targets(plan(), synthetic_bars("constant", 80), executor=executor, stopped=stopped)
        self.assertEqual(raised.exception.code, "cancelled")


if __name__ == "__main__":
    unittest.main()
