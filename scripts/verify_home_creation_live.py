"""Compare home-composer live creation evidence against the delivered App prompts.

This checker reads the original acceptance oracles without modifying the
competition handoff. The browser script performs creation and WASI HTTP runs.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DELIVERY = ROOT.parent / "比赛演示交付"
ORACLES = {
    "holdings": ("自然语言创建示例", "verify_natural_app_prompt.py"),
    "orders": ("自然语言创建示例-订单退款对账", "verify_order_reconciliation_prompt.py"),
}


def oracle(kind):
    label, script = ORACLES[kind]
    path = DELIVERY / "验收工具" / script
    spec = importlib.util.spec_from_file_location("home_creation_oracle_" + kind, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    prompt = (DELIVERY / label / "创建APP-Prompt.txt").read_text(encoding="utf-8").strip()
    return module, prompt


def manifest():
    result = {}
    for kind in ORACLES:
        helper, prompt = oracle(kind)
        verification, ordinary = helper.cases()
        boundary_label, boundary_input, boundary_expected = ordinary[0]
        result[kind] = {
            "prompt": prompt,
            "promptSha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "original": verification[0],
            "additional": verification[1],
            "boundary": {"label": boundary_label, "input": boundary_input,
                         "expectation": {"mode": "json_equals", "value": boundary_expected}},
        }
    return result


def independent_expected(kind, input_value):
    if kind == "holdings":
        positions, threshold = input_value["positions"], input_value["thresholdPct"]
        assert positions and type(threshold) is int and 0 <= threshold <= 100
        total = sum(item["value"] for item in positions)
        assert total > 0
        winner = max(positions, key=lambda item: item["value"])
        exceeds = winner["value"] * 100 > threshold * total
        pct = (Decimal(winner["value"]) * 100 / Decimal(total)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return {"summary": {"totalValue": total, "positionCount": len(positions),
                "maxPositionName": winner["name"], "maxWeightPct": format(pct, ".2f"),
                "thresholdPct": threshold, "exceedsThreshold": exceeds,
                "concentrationLabel": "超出设定线" if exceeds else "未超出设定线"}}
    orders = input_value["orders"]
    groups = []
    for channel in ("门店", "线上", "其他"):
        matching = [row for row in orders if row["channel"] == channel]
        if matching:
            paid = sum(row["paidCents"] for row in matching)
            refunded = sum(row["refundedCents"] for row in matching)
            groups.append({"channel": channel, "orderCount": len(matching), "paidCents": paid,
                           "refundedCents": refunded, "netCents": paid - refunded})
    paid = sum(row["paidCents"] for row in orders)
    refunded = sum(row["refundedCents"] for row in orders)
    return {"summary": {"orderCount": len(orders), "paidCents": paid,
            "refundedCents": refunded, "netCents": paid - refunded, "byChannel": groups}}


def check(evidence: Path):
    manifest_data = manifest()
    report = json.loads((evidence / "report.json").read_text(encoding="utf-8"))
    assert report["realModel"] is True and report["model"] == "GLM-5.3-Flash"
    assert len(report["attempts"]) == 2
    for item in report["attempts"]:
        kind = item["kind"]
        helper, _ = oracle(kind)
        case = manifest_data[kind]
        assert item["status"] == "passed", (kind, item)
        assert item["promptSha256"] == case["promptSha256"]
        folder = evidence / kind
        initial = json.loads((folder / "app-initial.json").read_text(encoding="utf-8"))
        definition, _ = helper.check_definition(initial)
        assert initial["name"] == {"holdings": "持仓集中度计算器", "orders": "订单退款对账助手"}[kind]
        canonical = json.dumps(definition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert initial["definitionDigest"] == hashlib.sha256(canonical).hexdigest()
        assert initial["verification"]["status"] == "unverified"
        assert initial["enabledVersionId"] is None
        assert initial["authoringProvenance"]["sessionId"] == item["sessionId"]
        assert initial["authoringProvenance"]["modelTraceIds"]
        traces = json.loads((folder / "model-trace-summary.json").read_text(encoding="utf-8"))
        assert {trace["id"] for trace in traces} == set(initial["authoringProvenance"]["modelTraceIds"])
        assert all(trace["model"] == "GLM-5.3-Flash" and trace["provider"] == "GiteeProvider"
                   and trace["requestId"] for trace in traces), (kind, "real model trace missing")
        saved = initial.get("testCases") or []
        assert saved and saved[0]["expectation"] is not None, (kind, "original case missing independent expectation")
        saved_expectation = saved[0]["expectation"]
        expected_original = independent_expected(kind, saved[0]["input"])
        if saved_expectation["mode"] == "json_equals":
            assert helper.same_exact(saved_expectation["value"], expected_original), (kind, "saved original oracle differs")
        elif saved_expectation["mode"] == "fields":
            fields = saved_expectation["values"]
            required = {"summary." + key: value for key, value in expected_original["summary"].items()}
            assert helper.same_exact(fields, required), (kind, "saved original fields differ or are incomplete")
        else:
            raise AssertionError((kind, "saved original lacks an objective oracle", saved_expectation["mode"]))
        assert not json.loads((folder / "runs-before.json").read_text(encoding="utf-8"))["items"]
        original = json.loads((folder / "run-original.json").read_text(encoding="utf-8"))
        helper.check_run(original, case["original"]["input"], case["original"]["expectation"]["value"])
        for label in ("additional", "boundary"):
            run = json.loads((folder / ("run-" + label + ".json")).read_text(encoding="utf-8"))
            example = case[label]
            helper.check_run(run, example["input"], example["expectation"]["value"])
        final = json.loads((folder / "app-enabled.json").read_text(encoding="utf-8"))
        assert final["verification"]["status"] == "passed"
        assert final["enabledVersionId"] == initial["versionId"]
        print(f"PASS {kind}: saved original, standard definition, single-case verification, two WASI runs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--emit-cases", type=Path)
    sub.add_argument("--check-evidence", type=Path)
    args = parser.parse_args()
    if args.emit_cases:
        args.emit_cases.parent.mkdir(parents=True, exist_ok=True)
        args.emit_cases.write_text(json.dumps(manifest(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        check(args.check_evidence.resolve(strict=True))


if __name__ == "__main__":
    main()
