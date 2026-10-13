"""Opt-in real Explore validation, isolated from every existing account/database.

Only public regression questions are sent. No credentials or chat histories are exported.
"""

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", default="R06,R10,new_pep")
    parser.add_argument(
        "--dns-mode", choices=["system", "cloudflare_doh"], default="system"
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    from backend.app.config import Config
    from backend.app.modules.explore.service import ExploreService
    from backend.app.modules.workbench.builtin_tools import build_tool_registry
    from backend.app.modules.workbench.skills import SkillRegistry
    from backend.app.modules.news.service import NewsService

    cfg = {k: getattr(Config, k) for k in dir(Config) if k.isupper()}
    cases = []
    for case in args.cases.split(","):
        if case == 'huawei_launch':
            cases.append((case, 'event', '研究华为Mate 90系列2026年10月1日的发布情况，包括其搭载的鸿蒙HarmonyOS 7、30B MoE端侧大模型以及小艺智能体（小艺帮记、小艺导航条）的升级内容与实际功能表现', None))
        elif case == 'huawei_annual':
            cases.append((case, 'event', '请读取 https://www.huawei.com/en/annual-report/2024 ：华为2024年的研发费用及其占收入比例是多少？截至2024年过去十年的累计研发投入是多少？', None))
        elif case == "new_pep257":
            cases.append((case, "event", "请读取 https://peps.python.org/pep-0257/#one-line-docstrings ，回答：单行 docstring 对结尾标点、动词形式、三引号布局有什么建议？为什么不应重复函数签名？", None))
        elif case == "new_pep":
            cases.append(
                (
                    case,
                    "event",
                    "请读取 https://peps.python.org/pep-0008/#maximum-line-length ，分别回答：Python PEP 8 对代码行、注释和文档字符串各建议多少字符？团队是否可以协商不同上限，条件是什么？",
                    None,
                )
            )
        else:
            folder = next((args.old_package / "reports").glob(case + "_*"))
            old = json.loads((folder / "报告结构化数据.json").read_text())
            cases.append(
                (
                    case,
                    old["module"],
                    old.get("resolved_question") or old["question"],
                    old,
                )
            )
    summary = []
    with tempfile.TemporaryDirectory(prefix="explore-research-live-") as temporary:
        cfg.update(
            DATA_DIR=temporary,
            IMPORT_LEGACY_HISTORY=False,
            EXPLORE_PUBLIC_DNS_MODE=args.dns_mode,
        )
        workbench = SimpleNamespace(
            runtime=SimpleNamespace(
                tools=build_tool_registry(cfg), skills=SkillRegistry(cfg["SKILLS_DIR"])
            )
        )
        service = ExploreService(workbench, NewsService(cfg), cfg)
        owner = "isolated-research-validation"
        try:
            for case, module, question, old in cases:
                out = args.output / case
                out.mkdir(exist_ok=True)
                session = service.create_session(owner, {"title": "隔离验证 " + case})
                start = time.monotonic()
                run = service.create_run(
                    owner,
                    session["id"],
                    {
                        "module": module,
                        "question": question,
                        "idempotency_key": str(uuid4()),
                    },
                )
                while (
                    time.monotonic() - start < cfg["EXPLORE_RUN_TIMEOUT_SECONDS"] + 15
                ):
                    run = service.repository.get_run(owner, run["id"])
                    if run["status"] not in {"queued", "running"}:
                        break
                    time.sleep(0.2)
                report = run.pop("report", None)
                (out / "run.json").write_text(
                    json.dumps(run, ensure_ascii=False, indent=2)
                )
                if report:
                    (out / "report.json").write_text(
                        json.dumps(report, ensure_ascii=False, indent=2)
                    )
                    (out / "report.md").write_text(report["markdown"])
                item = {
                    "case": case,
                    "status": run["status"],
                    "error": run.get("error"),
                    "answer_status": (report or {}).get("answer_status"),
                    "elapsed_seconds": round(time.monotonic() - start, 2),
                    "tools": [
                        (x["name"], x["status"])
                        for x in run.get("retrieval", {}).get("tool_trace", [])
                    ],
                    "old_status": (old or {}).get("status"),
                    "old_source_kinds": list(
                        {x.get("content_kind") for x in (old or {}).get("sources", [])}
                    ),
                    "new_source_kinds": list(
                        {
                            x.get("content_kind")
                            for x in (report or {}).get("sources", [])
                        }
                    ),
                    "budget": run.get("research_budget"),
                    "coverage": (report or {}).get("coverage", []),
                }
                summary.append(item)
                (args.output / "summary.json").write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2)
                )
                print(
                    json.dumps(
                        {
                            k: v
                            for k, v in item.items()
                            if k not in {"coverage", "budget"}
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        finally:
            service.close()
    return 0 if all(x["status"] in {"succeeded", "partial"} for x in summary) else 1


if __name__ == "__main__":
    raise SystemExit(main())
