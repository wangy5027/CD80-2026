"""Offline regression over the eleven original research artefacts; no live calls."""

import argparse, hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.modules.explore.research_task import (
    build_research_task,
    search_queries,
    qualified_source,
)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-package", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    results = []
    for directory in sorted((args.old_package / "reports").iterdir()):
        old = json.loads((directory / "报告结构化数据.json").read_text())
        run = json.loads((directory / "运行与检索记录.json").read_text())
        task = build_research_task(old)
        queries = search_queries(old)
        original = old["question"]
        resolved = old.get("resolved_question") or original
        checks = {
            "original_preserved": task["original_input"] == original,
            "confirmed_goal_preserved": task["resolved_goal"] == resolved,
            "all_question_parts_present": bool(task["subquestions"]),
            "bounded_queries": all(0 < len(x) <= 200 for x in queries),
        }
        sources = old.get("sources", [])
        relevant = [
            s["id"]
            for s in sources
            if s.get("kind") != "user" and qualified_source(resolved, s)
        ]
        case = directory.name.split("_")[0]
        if case == "R06":
            checks["second_contract_question"] = any(
                "合同责任边界" in q["question"] for q in task["subquestions"]
            )
        if case == "R10":
            checks["url_fragment_retained"] = any(
                "#data-availability" in u for u in task["urls"]
            )
        if case == "R11":
            checks["second_data_availability_question"] = any(
                "可用性" in q["question"] for q in task["subquestions"]
            )
        if case == "R05":
            checks["tourism_and_chip_news_rejected"] = not any(
                x in relevant for x in ["S4", "S5", "S6"]
            )
        results.append(
            {
                "case": case,
                "input_sha256": hashlib.sha256(original.encode()).hexdigest(),
                "module": old["module"],
                "old_searches": (
                    old.get("retrieval") or run.get("retrieval") or {}
                ).get("searches", []),
                "new_task": task,
                "new_query_candidates": queries,
                "old_source_ids_passing_new_gate": relevant,
                "checks": checks,
                "passed": all(checks.values()),
                "scope": "offline task/query/relevance regression; not a regenerated model report",
            }
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "count": len(results),
                "passed": sum(x["passed"] for x in results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(
        json.dumps(
            {
                "count": len(results),
                "passed": sum(x["passed"] for x in results),
                "failures": [
                    {x["case"]: x["checks"]} for x in results if not x["passed"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if len(results) == 11 and all(x["passed"] for x in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
