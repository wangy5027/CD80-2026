"""WASI-only execution and bounded evidence for compiled custom strategies.

The host interprets the typed rule plan for comparison. It never imports,
evaluates, or executes the exported Python module. The shared account ledger is
outside the guest and accepts only checked target arrays.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import threading
import zipfile

from strategy_workshop.fixtures import synthetic_bars
from strategy_workshop.models import validate_bars

from ..agent_platform.executor import SandboxExecutor
from .custom_rules import (COMPILER_VERSION, DSL_VERSION, MAX_BARS, compile_strategy,
                           normalize_plan, plan_hash, plan_warmup, reference_targets)


class CustomValidationError(ValueError):
    """Compiled output failed a rule/interface/causality comparison."""


_GUEST_PROGRAM = '''import json
import sys
import types

sys.path.insert(0, "/input/support.zip")
from strategy_workshop.models import Bar

def checked(values, count):
    if not isinstance(values, list) or len(values) != count:
        raise ValueError("Invalid target length/type")
    if any(item is not None and (type(item) is not int or item not in (0, 1)) for item in values):
        raise ValueError("Targets must be null or integer zero/one")
    return values

def run(payload):
    with open("/input/strategy.py", encoding="utf-8") as source:
        code = source.read()
    module = types.ModuleType("confirmed_custom_strategy")
    module.__file__ = "/input/strategy.py"
    sys.modules[module.__name__] = module
    # This bootstrap executes only inside capability-isolated CPython/WASI.
    exec(compile(code, module.__file__, "exec"), module.__dict__)
    results = []
    for case in payload["cases"]:
        bars = [Bar(**item) for item in case["bars"]]
        targets = checked(module.build_targets(bars), len(bars))
        prefixes = []
        for count in case.get("prefixes", []):
            subset = checked(module.build_targets(bars[:count]), count)
            if subset != targets[:count]:
                raise ValueError("Prefix causality check failed")
            prefixes.append({"bars": count, "passed": True})
        results.append({"name": case["name"], "targets": targets, "prefixes": prefixes})
    return {"cases": results, "spec": {"key": module.SPEC.key, "warmup": module.SPEC.warmup_bars}}
'''


def _support_bundle() -> bytes:
    """Read only two trusted math/contracts modules; do not ship application state."""
    import strategy_workshop.models as models
    import strategy_workshop.indicators as indicators
    stream = BytesIO()
    # The pinned CPython/WASI build has no zlib; stored ZIP entries are portable.
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in (("strategy_workshop/__init__.py", b""),
                           ("strategy_workshop/models.py", Path(models.__file__).read_bytes()),
                           ("strategy_workshop/indicators.py", Path(indicators.__file__).read_bytes())):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, data)
    return stream.getvalue()


def _execute_cases(plan, cases, executor=None, stopped=None):
    executor = executor or SandboxExecutor()
    stopped = stopped or threading.Event()
    if not hasattr(stopped, "is_set") or not hasattr(stopped, "wait"):
        raise ValueError("stopped 必须为 threading.Event")
    source = compile_strategy(plan)
    payload = {"cases": [{"name": case["name"], "bars": [asdict(bar) for bar in case["bars"]],
                           "prefixes": case.get("prefixes", [])} for case in cases]}
    response = executor.execute(_GUEST_PROGRAM, payload,
                                {"strategy.py": source.encode("utf-8"), "support.zip": _support_bundle()}, stopped)
    result = response.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("cases"), list) or len(result["cases"]) != len(cases):
        raise CustomValidationError("WASI 返回结构非法")
    if result.get("spec") != {"key": "custom_" + plan_hash(plan)[:16], "warmup": plan_warmup(plan)}:
        raise CustomValidationError("WASI 策略标识或预热与确认规则不一致")
    for requested, received in zip(cases, result["cases"]):
        if not isinstance(received, dict) or received.get("name") != requested["name"]:
            raise CustomValidationError("WASI 场景标识不一致")
        targets = received.get("targets")
        if (not isinstance(targets, list) or len(targets) != len(requested["bars"])
                or any(item is not None and (type(item) is not int or item not in (0, 1)) for item in targets)):
            raise CustomValidationError("WASI 目标序列必须为等长 null/整数0/整数1")
        if received.get("prefixes") != [{"bars": count, "passed": True} for count in requested.get("prefixes", [])]:
            raise CustomValidationError("WASI 缺少完整前缀因果检查")
        expected = reference_targets(plan, requested["bars"])
        if targets != expected:
            raise CustomValidationError("WASI 输出与已确认规则逐项不一致")
    return result, source


def sandbox_targets(plan, bars, features=None, executor=None, stopped=None):
    """Execute one frozen real/synthetic dataset and compare every target to DSL."""
    plan = normalize_plan(plan)
    if features:
        raise CustomValidationError("本阶段不支持外源 feature")
    if len(bars) > MAX_BARS:
        raise CustomValidationError("单次最多 10000 根日线")
    validate_bars(bars)
    result, _ = _execute_cases(plan, [{"name": "frozen_market", "bars": bars}], executor, stopped)
    return result["cases"][0]["targets"]


def validate_strategy(plan, executor=None, stopped=None) -> dict:
    """Return bounded engineering evidence, never a proof of financial correctness.

    The closed DSL and fixed compiler prohibit future indexing structurally;
    prefix tests additionally detect implementation regressions. Natural-language
    fidelity still requires the user's explicit confirmation of the rule summary.
    """
    plan = normalize_plan(plan)
    executor = executor or SandboxExecutor()
    warmup = plan_warmup(plan)
    count = max(360, warmup + 128)
    prefixes = sorted({max(1, warmup - 1), warmup, min(count - 1, warmup + 17), count - 1})
    cases = [{"name": name, "bars": synthetic_bars(name, count), "prefixes": prefixes}
             for name in ("constant", "oscillating", "trend", "regimes")]
    result, source = _execute_cases(plan, cases, executor, stopped)
    evidence = []
    for case in result["cases"]:
        targets = case["targets"]
        if any(value is not None for value in targets[:warmup - 1]):
            raise CustomValidationError("规则在声明预热完成前产生信号")
        if not any(value is not None for value in targets[warmup - 1:]):
            raise CustomValidationError("规则在预热完成后仍无可评估目标")
        evidence.append({"name": case["name"], "bars": len(targets),
                         "readyBars": sum(value is not None for value in targets),
                         "targetChanges": sum(a is not None and b is not None and a != b for a, b in zip(targets, targets[1:])),
                         "prefixChecks": case["prefixes"]})
    return {"passed": True, "ruleHash": plan_hash(plan), "sourceHash": sha256(source.encode("utf-8")).hexdigest(),
            "compilerVersion": COMPILER_VERSION, "dslVersion": DSL_VERSION, "runtime": executor.status(),
            "cases": evidence, "checks": ["strict_typed_plan", "causal_allowlist", "target_interface",
                                             "reference_equality", "warmup_boundaries", "constant_oscillating_trend_regimes", "prefix_causality"],
            "limitations": ["仅验证已确认结构化规则与代码执行一致；自然语言意图仍须用户确认",
                              "工程验证不能证明盈利，也不能替代行情、复权、分红及可成交状态的数据门槛",
                              "目标仓位状态不等于实际成交持仓"],
            "validatedAt": datetime.now(timezone.utc).isoformat()}
