"""Replay R06's unsupported ledger through current Explore code with scripted fixtures.

This is deliberately NOT a GLM or live-search validation. The only search item is
copied from a saved intermediate report; the model turns are deterministic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
from uuid import uuid4
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_original_r06(package: Path) -> dict:
    if package.is_file() and package.suffix.lower() == ".zip":
        with ZipFile(package) as archive:
            matches = [name for name in archive.namelist()
                       if "/reports/R06_" in name and name.endswith("/报告结构化数据.json")]
            if len(matches) != 1:
                raise ValueError("原交接包内未唯一找到 R06 报告结构化数据")
            return json.loads(archive.read(matches[0]).decode("utf-8"))
    reports = package / "reports" if (package / "reports").is_dir() else package
    matches = list(reports.glob("R06_*/报告结构化数据.json"))
    if len(matches) != 1:
        raise ValueError("原交接目录内未唯一找到 R06 报告结构化数据")
    return json.loads(matches[0].read_text(encoding="utf-8"))


class ReplayProvider:
    model = "SCRIPTED_STUB_NOT_GLM"

    def __init__(self, frozen_ledger: dict):
        self.ledger = frozen_ledger
        self.turns = 0
        self.writer_calls = 0
        self.search_result_seen = False

    def events(self, messages, model, stopped, on_response, *, tools=None, **_):
        self.turns += 1
        if self.turns == 1:
            if not any(item["function"]["name"] == "web_search" for item in tools or []):
                raise AssertionError("真实 ToolRegistry 未向研究轮次开放 web_search")
            yield {"type": "turn_end", "tool_calls": [{
                "id": "replay-search-1", "type": "function",
                "function": {"name": "web_search", "arguments": json.dumps({
                    "query": "尊界V800踏板支架断裂 华为 官方表态 合作责任",
                }, ensure_ascii=False)},
            }], "finish_reason": "tool_calls"}
            return
        if self.turns != 2:
            raise AssertionError("回放模型不允许额外研究轮次")
        returned = next((item for item in messages if item.get("role") == "tool"
                         and item.get("tool_call_id") == "replay-search-1"), None)
        if returned is None or not json.loads(returned["content"]).get("evidence"):
            raise AssertionError("固定搜索摘要未通过真实 ToolRegistry 和相关性检查")
        self.search_result_seen = True
        yield {"type": "token", "text": json.dumps(self.ledger, ensure_ascii=False)}
        yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}

    def json_tokens(self, messages, model, stopped, on_response):
        self.writer_calls += 1
        raise AssertionError("无有效支持的 R06 不应启动长篇 writer")
        yield ""  # Keep the interface a generator; this branch must never run.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-package", type=Path, required=True)
    parser.add_argument("--intermediate-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    old = read_original_r06(args.old_package)
    middle = json.loads(args.intermediate_report.read_text(encoding="utf-8"))
    claims = middle.get("claims") or []
    coverage = middle.get("coverage") or []
    if (old.get("module") != "relation" or not old.get("question")
            or not claims or not coverage
            or any(item.get("support_status") != "unsupported" or item.get("supports")
                   for item in claims)
            or any(item.get("status") != "partial" for item in coverage)):
        raise ValueError("原 R06 问题或中间账本不符合固定回放前提")
    source = next((item for item in middle.get("sources") or []
                   if item.get("content_kind") == "search_snippet"
                   and item.get("url") and item.get("summary")
                   and "尊界V800" in item.get("title", "")), None)
    if source is None:
        raise ValueError("中间报告没有可回放的已捕获 R06 搜索摘要")
    ledger = {"coverage": coverage, "claims": claims,
              "conflicts": middle.get("conflicts") or [], "done": True}

    from backend.app import create_app
    from backend.app.modules.explore.repository import timestamp
    from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec

    search_calls = []

    def fixed_search(arguments, _context):
        search_calls.append(dict(arguments))
        return ToolResult(content={"query": arguments["query"], "searchedAt": timestamp(),
                                   "replay": "fixed_saved_summary"}, sources=[{
            "title": source["title"], "snippet": source["summary"],
            "url": source["url"], "publishedAt": source.get("published_at"),
        }])

    spec = ToolSpec(name="web_search", description="固定 R06 已捕获摘要回放",
                    parameters={"type": "object", "properties": {
                        "query": {"type": "string", "minLength": 1, "maxLength": 200}},
                        "required": ["query"], "additionalProperties": False},
                    handler=fixed_search, capabilities=frozenset({"search"}), max_calls=3)
    provider = ReplayProvider(ledger)
    with tempfile.TemporaryDirectory(prefix="explore-r06-scripted-replay-") as temporary:
        temp = Path(temporary)
        app = create_app({"TESTING": True, "DATA_DIR": str(temp / "data"),
                          "AGENT_RUNTIME_DIR": str(temp / "runtime"),
                          "GITEE_AI_API_KEY": "unused-scripted-replay",
                          "IMPORT_LEGACY_HISTORY": False,
                          "EXPLORE_RUN_TIMEOUT_SECONDS": 30})
        service = app.extensions["explore_service"]
        service.provider = provider
        service.search_tools = ToolRegistry((spec,))
        try:
            owner = "isolated-scripted-replay"
            session = service.create_session(owner, {"title": "R06 全替身修复回放"})
            submitted = service.create_run(owner, session["id"], {
                "module": "relation", "question": old["question"],
                "idempotency_key": str(uuid4()),
            })
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = service.repository.get_run(owner, submitted["id"])
                if run["status"] not in {"queued", "running"}:
                    break
                time.sleep(0.02)
            else:
                raise TimeoutError("R06 替身回放在 10 秒内未结束")
        finally:
            service.close()
            app.extensions["workbench_service"].close()

    report = run.pop("report", None)
    traces = (run.get("retrieval") or {}).get("tool_trace") or []
    if (run["status"] != "partial" or not report
            or report.get("answer_status") != "unanswered"
            or report.get("report_variant") != "insufficient_evidence"
            or [item.get("status") for item in report.get("coverage") or []]
            != ["unanswered"] * len(coverage)
            or provider.writer_calls != 0 or provider.turns != 2
            or not provider.search_result_seen or len(search_calls) != 1
            or not any(item.get("name") == "web_search" and item.get("status") == "complete"
                       for item in traces)):
        raise AssertionError("最终代码未生成符合预期的 R06 证据不足短报告")

    args.output.mkdir(parents=True, exist_ok=True)
    export_label = {"verification_type": "scripted_stub_replay",
                    "model": "SCRIPTED_STUB_NOT_GLM",
                    "search": "fixed_saved_summary_not_live"}
    (args.output / "run.json").write_text(
        json.dumps({**run, "_verification": export_label}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (args.output / "report.json").write_text(
        json.dumps({**report, "_verification": export_label}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    notice = ("> **全替身修复回放**：模型输出和 web_search 摘要均为固定脚本回放；"
              "未调用 GLM，也未进行实时联网搜索。以下为当前 Explore 代码渲染结果。\n\n")
    (args.output / "report.md").write_text(notice + report["markdown"], encoding="utf-8")
    metadata = {
        "case": "R06", "verification_type": "scripted_stub_replay",
        "model": "SCRIPTED_STUB_NOT_GLM", "search": "fixed_saved_summary_not_live",
        "public_reader": "not_invoked", "old_package": str(args.old_package),
        "intermediate_report": str(args.intermediate_report),
        "captured_source_id": source.get("id"),
        "captured_summary_sha256": hashlib.sha256(source["summary"].encode()).hexdigest(),
        "input_question_matches_old_package": run["question"] == old["question"],
        "frozen_claim_count": len(claims), "frozen_claims_all_unsupported": True,
        "frozen_coverage_statuses": [item["status"] for item in coverage],
        "provider_research_turns": provider.turns, "provider_writer_calls": provider.writer_calls,
        "registered_search_calls": len(search_calls),
        "result_status": run["status"], "result_answer_status": report["answer_status"],
        "report_variant": report["report_variant"],
        "result_coverage_statuses": [item["status"] for item in report["coverage"]],
        "limitation": "展示最终代码在旧 R06 不受支持账本下的降级；不构成真实 GLM 或实时资料端到端通过。",
    }
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "status": run["status"],
                      "answer_status": report["answer_status"],
                      "variant": report["report_variant"],
                      "stub_only": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
