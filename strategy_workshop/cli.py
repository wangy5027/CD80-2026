"""python -m strategy_workshop.cli: inspect, run, or validate the curated library."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .catalog import STRATEGIES, get_strategy, implementation_hash
from .engine import run_backtest
from .fixtures import synthetic_bars
from .models import BacktestConfig, Instrument
from .sqlite_source import inspect_database, load_snapshot


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_parser = sub.add_parser("inspect", help="Read SQLite table/column names only")
    inspect_parser.add_argument("--db", required=True)
    for command in ("run", "validate"):
        child = sub.add_parser(command)
        child.add_argument("--db")
        child.add_argument("--mapping")
        child.add_argument("--symbol", default="510300.SH")
        child.add_argument("--start")
        child.add_argument("--end")
        child.add_argument("--cash", type=float, default=100000)
        child.add_argument("--synthetic", action="store_true", help="Clearly labelled development data only")
        child.add_argument("--research", action="store_true", help="Allow incomplete real data for diagnostics, never a formal report")
        child.add_argument("--output", required=True)
        if command == "run":
            child.add_argument("--strategy", choices=tuple(STRATEGIES), required=True)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        print(json.dumps(inspect_database(args.db), ensure_ascii=False, indent=2))
        return 0
    if args.synthetic and args.db:
        parser.error("--synthetic and --db are mutually exclusive")
    if not args.synthetic and not all((args.db, args.mapping, args.start, args.end)):
        parser.error("Real data needs --db, --mapping, --start and --end")
    strategies = [get_strategy(args.strategy)] if args.command == "run" else list(STRATEGIES.values())
    cases = ("trend", "oscillating", "constant", "regimes") if args.synthetic and args.command == "validate" else ("regimes",)
    rows = []
    for case in cases:
        if args.synthetic:
            bars = synthetic_bars(case)
            start, end = args.start or bars[250].date, args.end or bars[-1].date
            actions, instrument = [], Instrument()
            provenance = {"dataset_kind": "synthetic_fixture", "fixture": case,
                          "unadjusted_execution_prices": True, "corporate_actions_complete": True,
                          "market_rules_verified": False, "calendar_complete": False,
                          "signal_adjustment_consistent": True}
        else:
            mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8-sig"))
            start, end = args.start, args.end
            bars, actions, instrument, provenance = load_snapshot(args.db, mapping, args.symbol, start, end)
        config = BacktestConfig(start, end, initial_cash=args.cash)
        for strategy in strategies:
            result = run_backtest(bars, strategy, config, instrument, actions, provenance,
                                  strict=not args.synthetic and not args.research)
            result["implementation_sha256"] = implementation_hash()
            if args.command == "run":
                destination = Path(args.output)
            else:
                destination = Path(args.output) / f"{case if args.synthetic else 'real_sqlite'}_{strategy.SPEC.key}.json"
            _save(destination, result)
            metrics = result["strategy_result"]["metrics"]
            rows.append({"strategy": strategy.SPEC.key, "case": case if args.synthetic else "real_sqlite",
                         "passed": True, "report_eligible": result["report_eligible"],
                         "trade_count": metrics["trade_count"], "fill_count": len(result["strategy_result"]["fills"]),
                         "total_return": metrics["total_return"], "data_snapshot_sha256": result["data_snapshot_sha256"],
                         "result_file": str(destination.resolve())})
    summary = {"validation_kind": "synthetic_only" if args.synthetic else "real_sqlite",
               "strategy_count": len(strategies), "case_count": len(cases),
               "passed": len(rows), "implementation_sha256": implementation_hash(), "cases": rows}
    if args.command == "validate":
        _save(Path(args.output) / "summary.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key != "cases"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
