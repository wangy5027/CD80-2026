"""Typed, causal strategy rules and a deterministic Python compiler.

Only the fixed expression grammar is interpreted on the host. Generated Python is
an export artifact; it is never imported or evaluated by host Python. Price fields
use adjusted signal prices, volume uses the original share/unit count. ``position``
means the previous target, never an account's actual filled position.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
import inspect
import json
from math import isfinite, prod
from pathlib import Path
from typing import Any, Sequence

from strategy_workshop import indicators
from strategy_workshop.models import Bar, StrategySpec, validate_bars

DSL_VERSION = "workshop-rules-1.0.0"
COMPILER_VERSION = "workshop-compiler-1.0.0"
MAX_BARS = 10000
MAX_NODES = 128
MAX_DEPTH = 12
_FIELDS = {"open", "high", "low", "close", "volume"}
_PERIOD_INDICATORS = {"sma", "ema", "rsi", "roc", "atr", "std", "highest", "lowest",
                      "bollinger_upper", "bollinger_mid", "bollinger_lower", "donchian_upper", "donchian_lower"}
_MACD = {"macd_line", "macd_signal", "macd_hist"}
_STOCHASTIC = {"stochastic_k", "stochastic_d"}
_INDICATORS = _PERIOD_INDICATORS | _MACD | _STOCHASTIC


class RuleValidationError(ValueError):
    """A plan does not satisfy the fixed, bounded rule contract."""


def _keys(value, required, optional=(), where="node"):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise RuleValidationError(f"{where}: 字段必须为 {sorted(required)}，可选 {sorted(optional)}")


def _integer(value, name, low=2, high=250):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise RuleValidationError(f"{name} 必须为 {low} 至 {high} 的整数")
    return value


def _number(value, name="value"):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or abs(value) > 1e12 or not isfinite(value):
        raise RuleValidationError(f"{name} 必须为有限数字，绝对值不超过 1e12")
    return value


def normalize_plan(plan: Any) -> dict:
    """Validate and freeze defaults. Extra keys, external data and future lags fail."""
    _keys(plan, {"schema_version", "name", "description", "features", "entry", "exit"}, where="plan")
    if type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
        raise RuleValidationError("仅支持 schema_version=1")
    if not isinstance(plan["name"], str) or not 1 <= len(plan["name"].strip()) <= 100:
        raise RuleValidationError("策略名称须为 1 至 100 字")
    if not isinstance(plan["description"], str) or not 1 <= len(plan["description"].strip()) <= 2500:
        raise RuleValidationError("策略说明须为 1 至 2500 字")
    if not isinstance(plan["features"], list) or plan["features"]:
        raise RuleValidationError("本阶段仅支持 SQLite 价量，features 必须为空；不能以价格代理外源信号")
    count = 0

    def node(value, boolean=False, depth=0):
        nonlocal count
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            raise RuleValidationError("规则超过 128 节点或 12 层深度")
        if not isinstance(value, dict) or not isinstance(value.get("type"), str):
            raise RuleValidationError("表达式必须有 type")
        kind = value["type"]
        child = lambda item, expected=False: node(item, expected, depth + 1)
        if boolean:
            if kind == "compare":
                _keys(value, {"type", "op", "left", "right"})
                if not isinstance(value["op"], str) or value["op"] not in {"gt", "gte", "lt", "lte", "eq", "ne"}:
                    raise RuleValidationError("比较运算不在白名单")
                return {"type": kind, "op": value["op"], "left": child(value["left"]), "right": child(value["right"])}
            if kind in {"all", "any"}:
                _keys(value, {"type", "args"})
                if not isinstance(value["args"], list) or not 2 <= len(value["args"]) <= 12:
                    raise RuleValidationError("all/any 须有 2 至 12 项")
                return {"type": kind, "args": [child(item, True) for item in value["args"]]}
            if kind == "not":
                _keys(value, {"type", "arg"})
                return {"type": kind, "arg": child(value["arg"], True)}
            if kind in {"cross_above", "cross_below"}:
                _keys(value, {"type", "left", "right"})
                return {"type": kind, "left": child(value["left"]), "right": child(value["right"])}
            if kind == "position":
                _keys(value, {"type", "value"})
                return {"type": kind, "value": _integer(value["value"], "position", 0, 1)}
            raise RuleValidationError(f"布尔表达式不支持 {kind}")
        if kind == "const":
            _keys(value, {"type", "value"})
            return {"type": kind, "value": _number(value["value"])}
        if kind == "field":
            _keys(value, {"type", "name"}, {"lag"})
            if not isinstance(value["name"], str) or value["name"] not in _FIELDS:
                raise RuleValidationError("价量字段不在白名单")
            return {"type": kind, "name": value["name"], "lag": _integer(value.get("lag", 0), "lag", 0, 250)}
        if kind == "arithmetic":
            _keys(value, {"type", "op", "args"})
            if not isinstance(value["op"], str) or value["op"] not in {"add", "sub", "mul", "div", "min", "max"}:
                raise RuleValidationError("数字运算不在白名单")
            if not isinstance(value["args"], list) or not 2 <= len(value["args"]) <= 8:
                raise RuleValidationError("数字运算须有 2 至 8 项")
            if value["op"] in {"sub", "div"} and len(value["args"]) != 2:
                raise RuleValidationError("sub/div 必须恰有 2 项")
            return {"type": kind, "op": value["op"], "args": [child(item) for item in value["args"]]}
        if kind == "indicator":
            name = value.get("name")
            if not isinstance(name, str) or name not in _INDICATORS:
                raise RuleValidationError("指标名称不在白名单")
            required, optional = {"type", "name"}, {"lag"}
            if name in _PERIOD_INDICATORS:
                required.add("period")
            if name in _MACD:
                optional |= {"fast_period", "slow_period", "signal_period"}
            if name in _STOCHASTIC:
                optional |= {"lookback_period", "k_period", "d_period"}
            if name.startswith("bollinger_"):
                optional.add("multiplier")
            input_supported = name not in {"atr", "donchian_upper", "donchian_lower"} | _STOCHASTIC
            if input_supported:
                optional.add("input")
            _keys(value, required, optional)
            result = {"type": kind, "name": name, "lag": _integer(value.get("lag", 0), "lag", 0, 250)}
            if name in _PERIOD_INDICATORS:
                result["period"] = _integer(value["period"], "period")
            if name in _MACD:
                result.update({key: _integer(value.get(key, default), key) for key, default in
                               (("fast_period", 12), ("slow_period", 26), ("signal_period", 9))})
                if result["fast_period"] >= result["slow_period"]:
                    raise RuleValidationError("MACD fast_period 必须小于 slow_period")
            if name in _STOCHASTIC:
                result.update({key: _integer(value.get(key, default), key) for key, default in
                               (("lookback_period", 14), ("k_period", 3), ("d_period", 3))})
            if name.startswith("bollinger_"):
                result["multiplier"] = _number(value.get("multiplier", 2.0), "multiplier")
                if not 0 < result["multiplier"] <= 20:
                    raise RuleValidationError("布林 multiplier 必须大于 0 且不超过 20")
            if input_supported:
                result["input"] = child(value.get("input", {"type": "field", "name": "close"}))
            return result
        raise RuleValidationError(f"数字表达式不支持 {kind}")

    result = {"schema_version": 1, "name": plan["name"].strip(), "description": plan["description"].strip(),
              "features": [], "entry": node(plan["entry"], True), "exit": node(plan["exit"], True)}
    if _plan_warmup(result) > 1000:
        raise RuleValidationError("累计预热超过 1000 根日线")
    return result


def plan_hash(plan: dict) -> str:
    return sha256(json.dumps(normalize_plan(plan), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _warmup(node):
    kind = node["type"]
    if kind == "const" or kind == "position":
        return 1
    if kind == "field":
        return node["lag"] + 1
    if kind in {"compare", "cross_above", "cross_below"}:
        return max(_warmup(node["left"]), _warmup(node["right"])) + int(kind != "compare")
    if kind in {"all", "any", "arithmetic"}:
        return max(map(_warmup, node["args"]))
    if kind == "not":
        return _warmup(node["arg"])
    name, lag = node["name"], node["lag"]
    first = _warmup(node["input"]) if "input" in node else 1
    if name in _MACD:
        return first + node["slow_period"] - 1 + (node["signal_period"] - 1 if name != "macd_line" else 0) + lag
    if name in _STOCHASTIC:
        return node["lookback_period"] + node["k_period"] - 1 + (node["d_period"] - 1 if name == "stochastic_d" else 0) + lag
    return first + node["period"] - 1 + int(name in {"rsi", "roc", "atr", "donchian_upper", "donchian_lower"}) + lag


def _plan_warmup(plan):
    return max(_warmup(plan["entry"]), _warmup(plan["exit"]))


def plan_warmup(plan: dict) -> int:
    return _plan_warmup(normalize_plan(plan))


def describe_plan(plan: dict) -> dict:
    """Readable deterministic rules, separate from a model's prose interpretation."""
    plan = normalize_plan(plan)
    operators = {"gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "eq": "=", "ne": "≠",
                 "add": "+", "sub": "−", "mul": "×", "div": "÷"}
    fields = {"open": "复权开盘价", "high": "复权最高价", "low": "复权最低价", "close": "复权收盘价", "volume": "原始成交量"}

    def text(node):
        kind = node["type"]
        if kind == "const":
            return str(node["value"])
        if kind == "field":
            return fields[node["name"]] + (f"（前{node['lag']}日）" if node["lag"] else "")
        if kind == "indicator":
            name = node["name"]
            source = "，按" + text(node["input"]) + "计算" if "input" in node else ""
            labels = {"sma": "简单均线", "ema": "指数均线", "rsi": "RSI相对强弱指标",
                      "roc": "变化率（%）", "atr": "平均真实波幅", "std": "标准差",
                      "highest": "历史最高值", "lowest": "历史最低值"}
            if name in labels:
                label = f"{node['period']}日{labels[name]}"
            elif name in _MACD:
                label = {"macd_line": "MACD快慢线差", "macd_signal": "MACD信号线", "macd_hist": "MACD柱值"}[name]
                label += f"（快线{node['fast_period']}日、慢线{node['slow_period']}日、信号{node['signal_period']}日）"
            elif name in _STOCHASTIC:
                label = ("随机指标K" if name == "stochastic_k" else "随机指标D")
                label += f"（{node['lookback_period']}日区间、K平滑{node['k_period']}日、D平滑{node['d_period']}日）"
            elif name.startswith("bollinger_"):
                label = f"{node['period']}日布林" + {"bollinger_upper": "上轨", "bollinger_mid": "中轨", "bollinger_lower": "下轨"}[name]
                if name != "bollinger_mid":
                    label += f"（{node['multiplier']:g}倍标准差）"
            else:
                label = f"前{node['period']}日唐奇安" + ("上轨" if name == "donchian_upper" else "下轨") + "（不含当日）"
            return label + source + (f"（取前{node['lag']}日的值）" if node["lag"] else "")
        if kind == "position":
            return "上一目标为" + ("持有" if node["value"] else "空仓")
        if kind == "compare":
            return f"({text(node['left'])} {operators[node['op']]} {text(node['right'])})"
        if kind in {"cross_above", "cross_below"}:
            return f"{text(node['left'])} {'上穿' if kind == 'cross_above' else '下穿'} {text(node['right'])}"
        if kind == "not":
            return f"不满足({text(node['arg'])})"
        if kind in {"all", "any"}:
            return "(" + (" 且 " if kind == "all" else " 或 ").join(map(text, node["args"])) + ")"
        if node["op"] in {"min", "max"}:
            return node["op"] + "(" + ", ".join(map(text, node["args"])) + ")"
        return "(" + f" {operators[node['op']]} ".join(map(text, node["args"])) + ")"

    return {"name": plan["name"], "summary": plan["description"], "entry": text(plan["entry"]),
            "exit": text(plan["exit"]), "warmup_bars": _plan_warmup(plan), "required_features": [],
            "semantics": ["每日收盘后决定目标，下一交易日开盘由统一撮合执行", "退出优先；无信号保持上一目标；初始目标为空仓",
                          "所有规则完成预热前输出空值", "持有/空仓状态表示上一策略目标，不是实际成交持仓", "只使用当日及此前 SQLite 价量"]}


def _lag(values, lag):
    return [None] * min(lag, len(values)) + (list(values[:-lag]) if lag else list(values))


def _field(bars, name, lag):
    values = [getattr(bar, name) * (1 if name == "volume" else bar.adjustment_factor) for bar in bars]
    return _lag(values, lag)


def _arithmetic(op, arrays):
    result = []
    for items in zip(*arrays):
        if any(item is None for item in items):
            result.append(None)
            continue
        if op == "add":
            value = sum(items)
        elif op == "sub":
            value = items[0] - items[1]
        elif op == "mul":
            value = prod(items)
        elif op == "div":
            if items[1] == 0:
                raise ValueError("规则数字除零：请修改分母条件，不能伪造信号")
            value = items[0] / items[1]
        else:
            value = min(items) if op == "min" else max(items)
        try:
            finite = isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise ValueError("规则数字运算产生非有限数值")
        result.append(value)
    return result


def _dense(values, function, period):
    first = next((i for i, item in enumerate(values) if item is not None), len(values))
    if any(item is None for item in values[first:]):
        raise ValueError("指标输入不能有内部缺失值")
    return [None] * first + function(values[first:], period)


def _indicator(node, bars, source=None):
    name = node["name"]
    close = [bar.signal_close for bar in bars]
    high = [bar.signal_high for bar in bars]
    low = [bar.signal_low for bar in bars]
    if name == "sma" or name == "ema":
        values = (indicators.sma if name == "sma" else indicators.ema)(source, node["period"])
    elif name in {"rsi", "std", "highest", "lowest"}:
        function = {"rsi": indicators.rsi, "std": indicators.rolling_std,
                    "highest": indicators.rolling_max, "lowest": indicators.rolling_min}[name]
        values = _dense(source, function, node["period"])
    elif name == "roc":
        period = node["period"]
        values = []
        for i, current in enumerate(source):
            prior = source[i - period] if i >= period else None
            if prior is None or current is None:
                values.append(None)
            elif prior == 0:
                raise ValueError("ROC 输入基期为零")
            else:
                values.append(100.0 * (current / prior - 1))
    elif name == "atr":
        values = indicators.atr(high, low, close, node["period"])
    elif name.startswith("macd_"):
        fast = indicators.ema(source, node["fast_period"])
        slow = indicators.ema(source, node["slow_period"])
        difference = [None if a is None or b is None else a - b for a, b in zip(fast, slow)]
        signal = indicators.ema(difference, node["signal_period"])
        values = difference if name == "macd_line" else signal if name == "macd_signal" else [None if a is None or b is None else a - b for a, b in zip(difference, signal)]
    elif name.startswith("bollinger_"):
        middle = indicators.sma(source, node["period"])
        deviation = _dense(source, indicators.rolling_std, node["period"])
        sign = 1 if name == "bollinger_upper" else -1
        values = middle if name == "bollinger_mid" else [None if a is None or b is None else a + sign * node["multiplier"] * b for a, b in zip(middle, deviation)]
    elif name.startswith("donchian_"):
        values = _lag((indicators.rolling_max if name == "donchian_upper" else indicators.rolling_min)(high if name == "donchian_upper" else low, node["period"]), 1)
    else:
        upper = indicators.rolling_max(high, node["lookback_period"])
        lower = indicators.rolling_min(low, node["lookback_period"])
        raw = [None if a is None or b is None else 50.0 if a == b else 100.0 * (c - b) / (a - b) for c, a, b in zip(close, upper, lower)]
        k = indicators.sma(raw, node["k_period"])
        values = k if name == "stochastic_k" else indicators.sma(k, node["d_period"])
    if any(value is not None and not isfinite(value) for value in values):
        raise ValueError("指标产生非有限数值")
    return _lag(values, node["lag"])


def _comparison(op, left, right):
    if left is None or right is None:
        return None
    return {"gt": left > right, "gte": left >= right, "lt": left < right,
            "lte": left <= right, "eq": left == right, "ne": left != right}[op]


def _cross(up, left, right, i):
    if i == 0 or any(item is None for item in (left[i], right[i], left[i - 1], right[i - 1])):
        return None
    return left[i - 1] <= right[i - 1] and left[i] > right[i] if up else left[i - 1] >= right[i - 1] and left[i] < right[i]


def _boolean(kind, values):
    if any(value is None for value in values):
        return None
    return all(values) if kind == "all" else any(values) if kind == "any" else not values[0]


def reference_targets(plan: dict, bars: Sequence[Bar], features=None) -> list[int | None]:
    """Interpret only the validated typed plan, with no source-code evaluation."""
    plan = normalize_plan(plan)
    if features:
        raise RuleValidationError("本阶段不支持外源 feature")
    if len(bars) > MAX_BARS:
        raise RuleValidationError("单次最多 10000 根日线")
    validate_bars(bars)
    cache = {}

    def numeric(node):
        key = json.dumps(node, sort_keys=True, ensure_ascii=False)
        if key not in cache:
            kind = node["type"]
            if kind == "const":
                values = [node["value"]] * len(bars)
            elif kind == "field":
                values = _field(bars, node["name"], node["lag"])
            elif kind == "arithmetic":
                values = _arithmetic(node["op"], [numeric(item) for item in node["args"]])
            else:
                values = _indicator(node, bars, numeric(node["input"]) if "input" in node else None)
            cache[key] = values
        return cache[key]

    def boolean(node, i, position):
        kind = node["type"]
        if kind == "position":
            return position == node["value"]
        if kind == "compare":
            return _comparison(node["op"], numeric(node["left"])[i], numeric(node["right"])[i])
        if kind in {"cross_above", "cross_below"}:
            return _cross(kind == "cross_above", numeric(node["left"]), numeric(node["right"]), i)
        if kind == "not":
            return _boolean(kind, [boolean(node["arg"], i, position)])
        return _boolean(kind, [boolean(item, i, position) for item in node["args"]])

    result, position = [], 0
    for i in range(len(bars)):
        enter, leave = boolean(plan["entry"], i, position), boolean(plan["exit"], i, position)
        if enter is None or leave is None:
            result.append(None)
            continue
        if leave:
            position = 0
        elif enter:
            position = 1
        result.append(position)
    return result


def compile_strategy(plan: dict) -> str:
    """Emit a standalone SPEC/build_targets module from the closed grammar.

    Expressions become direct Python calls/branches. No model source is accepted;
    host callers may save the result but must execute it only through WASI.
    """
    plan = normalize_plan(plan)
    lines, cache = [], {}

    def numeric(node):
        key = json.dumps(node, sort_keys=True, ensure_ascii=False)
        if key in cache:
            return cache[key]
        kind = node["type"]
        if kind == "const":
            expression = f"[{node['value']!r}] * len(bars)"
        elif kind == "field":
            expression = f"_field(bars, {node['name']!r}, {node['lag']!r})"
        elif kind == "arithmetic":
            children = ", ".join(numeric(item) for item in node["args"])
            expression = f"_arithmetic({node['op']!r}, [{children}])"
        else:
            source = numeric(node["input"]) if "input" in node else "None"
            settings = {key: value for key, value in node.items() if key != "input"}
            expression = f"_indicator({settings!r}, bars, {source})"
        variable = f"_v{len(cache)}"
        cache[key] = variable
        lines.append(f"    {variable} = {expression}")
        return variable

    def boolean(node):
        kind = node["type"]
        if kind == "position":
            return f"position == {node['value']}"
        if kind == "compare":
            return f"_comparison({node['op']!r}, {numeric(node['left'])}[i], {numeric(node['right'])}[i])"
        if kind in {"cross_above", "cross_below"}:
            return f"_cross({kind == 'cross_above'!r}, {numeric(node['left'])}, {numeric(node['right'])}, i)"
        values = [boolean(node["arg"])] if kind == "not" else [boolean(item) for item in node["args"]]
        return f"_boolean({kind!r}, [{', '.join(values)}])"

    entry, exit_rule = boolean(plan["entry"]), boolean(plan["exit"])
    rule_hash = plan_hash(plan)
    helpers = "\n\n".join(inspect.getsource(function) for function in
                             (_lag, _field, _arithmetic, _dense, _indicator, _comparison, _cross, _boolean))
    return (f'"""Custom strategy. {COMPILER_VERSION}; confirmed rule {rule_hash}.\n'
            'Generated deterministically from a typed causal plan. Run in WASI.\n'
            'position means the previous target, never actual account holdings.\n"""\n'
            "from math import isfinite, prod\nfrom strategy_workshop import indicators\n"
            "from strategy_workshop.models import StrategySpec, validate_bars\n\n"
            f"RULE_HASH = {rule_hash!r}\nCOMPILER_VERSION = {COMPILER_VERSION!r}\n"
            f"SPEC = StrategySpec(key={'custom_' + rule_hash[:16]!r}, name={plan['name']!r}, "
            f"summary={plan['description']!r}, parameters={{}}, warmup_bars={_plan_warmup(plan)}, sources=(), version='2.0.0')\n\n"
            + helpers + "\n\ndef build_targets(bars, parameters=None):\n"
            "    if parameters:\n        raise ValueError('Change the confirmed plan to create a new revision')\n"
            f"    if len(bars) > {MAX_BARS}:\n        raise ValueError('Too many bars')\n"
            "    validate_bars(bars)\n" + "\n".join(lines) + "\n"
            "    result, position = [], 0\n    for i in range(len(bars)):\n"
            f"        enter = {entry}\n        leave = {exit_rule}\n"
            "        if enter is None or leave is None:\n            result.append(None)\n            continue\n"
            "        if leave:\n            position = 0\n        elif enter:\n            position = 1\n"
            "        result.append(position)\n    return result\n")


class PrecomputedStrategy:
    """Trusted adapter for the shared host ledger; never imports generated code."""
    def __init__(self, plan, bars, targets, code_path):
        plan = normalize_plan(plan)
        source = compile_strategy(plan).encode("utf-8")
        path = Path(code_path)
        if not path.is_file() or path.read_bytes() != source:
            raise RuleValidationError("策略文件与已确认规则编译结果不一致")
        if len(bars) != len(targets) or any(value is not None and (type(value) is not int or value not in (0, 1)) for value in targets):
            raise RuleValidationError("策略目标序列非法")
        self.SPEC = StrategySpec("custom_" + plan_hash(plan)[:16], plan["name"], plan["description"], {},
                                 _plan_warmup(plan), (), "2.0.0")
        self.__file__ = str(path.absolute())
        self._source = source
        self._bars = [asdict(bar) for bar in bars]
        self._targets = list(targets)

    def build_targets(self, bars, parameters=None):
        if (parameters or Path(self.__file__).read_bytes() != self._source or len(bars) > len(self._bars)
                or [asdict(bar) for bar in bars] != self._bars[:len(bars)]):
            raise RuleValidationError("冻结行情或规则参数发生变化，需要重新验证")
        return self._targets[:len(bars)]


def make_strategy(plan, bars, targets, code_path) -> PrecomputedStrategy:
    return PrecomputedStrategy(plan, bars, targets, code_path)
