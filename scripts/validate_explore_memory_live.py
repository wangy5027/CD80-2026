"""Validate Explore's A → B → C memory chain with the configured real GLM.

All research sources are clearly marked synthetic and served by local tools.
The account and databases exist only inside a temporary directory. This run
uses real model quota but never reads existing account data or news services.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from uuid import uuid4
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "/desktop/api/explore"
CURRENT_URL = "https://synthetic.example.test/current-auto-parts"
OLD_URL = "https://synthetic.example.test/2019-auto-parts"
SYNTHETIC_NOTICE = "【合成验收材料，非真实新闻，不可用于投资判断】"
CURRENT_TEXT = (
    SYNTHETIC_NOTICE + "2026年10月10日，虚构的星辰动力与远川汽车发布联合说明："
    "双方正在核对一项制动支架供货记录；说明没有披露合同责任划分，也没有提供事故结论。"
)
OLD_TEXT = (
    SYNTHETIC_NOTICE + "2019-05-02，虚构的远川汽车与另一零部件供应商曾发生供货争议。"
    "当时公开说明只确认双方启动合同核查，未直接认定哪一方承担最终责任。"
)
FIRST_QUESTION = (
    "请理解一条明确标为合成验收材料的虚构事件：星辰动力与远川汽车在制动支架供货记录中"
    "各自说了什么？哪些责任问题仍需核对？请保持合成标签，不把它说成真实新闻。"
)


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def verify_memory_call(call, expected):
    actual = (call.get("first_user_json") or {}).get("research_memory")
    if not isinstance(actual, dict):
        raise AssertionError("GLM 请求中缺少 research_memory 正文")
    for key in ("id", "version", "content_hash", "content"):
        if actual.get(key) != expected.get(key):
            raise AssertionError(f"GLM 请求中的研究记忆 {key} 与冻结版本不一致")
    if hashlib.sha256(actual["content"].encode("utf-8")).hexdigest() != actual["content_hash"]:
        raise AssertionError("GLM 请求中的记忆正文指纹不一致")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "data/explore-memory-validation-20261014/live")
    parser.add_argument("--timeout", type=int, default=420,
                        help="Overall deadline for each research run, 30-600 seconds")
    parser.add_argument("--require-history-writer", action="store_true",
                        help="Strict mode: also require a history writing-model call")
    args = parser.parse_args()
    if not 30 <= args.timeout <= 600:
        parser.error("--timeout must be between 30 and 600")
    os.umask(0o077)
    args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.output.chmod(0o700)

    # Configuration is loaded in this process only. Never print or persist keys.
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from backend.app import create_app
    from backend.app.config import Config
    from backend.app.modules.explore.repository import timestamp
    from backend.app.modules.workbench.errors import GatewayError
    from backend.app.modules.workbench.graph_runtime import GiteeProvider
    from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec

    summary = {
        "checked_at_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "real_model": bool(Config.GITEE_AI_API_KEY),
        "synthetic_sources": True,
        "temporary_accounts_and_database": True,
        "model": Config.GITEE_AI_MODEL,
        "model_host": Config.GITEE_AI_BASE_URL.split("/")[2],
        "status": "starting", "ok": False, "steps": [], "checks": {},
    }
    save_json(args.output / "summary.json", summary)
    if not Config.GITEE_AI_API_KEY:
        summary.update(status="blocked", failure="GITEE_AI_API_KEY is not configured; no real model call was made.")
        save_json(args.output / "summary.json", summary)
        print(json.dumps({"status": summary["status"], "reason": summary["failure"]}), flush=True)
        return 2

    sensitive = [Config.GITEE_AI_API_KEY]
    captures = []
    capture_lock = threading.Lock()

    def scrub(value):
        if isinstance(value, str):
            for token in sensitive:
                if token and len(token) >= 8:
                    value = value.replace(token, "[REDACTED]")
            return value
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items()}
        return value

    class CaptureProvider(GiteeProvider):
        def _capture(self, kind, messages, model, tools=None):
            # Shallow copies made by ExploreService share captures and the lock.
            safe_messages = scrub(copy.deepcopy(messages))
            first_user = next((item for item in safe_messages if item.get("role") == "user"), {})
            try:
                payload = json.loads(first_user.get("content") or "null")
            except (ValueError, TypeError):
                payload = None
            memory = payload.get("research_memory") if isinstance(payload, dict) else None
            stage = payload.get("stage") or payload.get("task") if isinstance(payload, dict) else None
            if not stage and isinstance(payload, dict):
                stage = "history_research" if "research_task" in payload else "event_initial"
            record = {
                "kind": kind, "stage": stage, "model": model,
                "at_shanghai": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "memory_id": memory.get("id") if isinstance(memory, dict) else None,
                "memory_version": memory.get("version") if isinstance(memory, dict) else None,
                "memory_hash": memory.get("content_hash") if isinstance(memory, dict) else None,
                "memory_chars": len(memory.get("content") or "") if isinstance(memory, dict) else 0,
                "messages": safe_messages,
                "first_user_json": payload,
                "tool_names": [item["function"]["name"] for item in tools or []
                              if isinstance(item, dict) and isinstance(item.get("function"), dict)],
            }
            with capture_lock:
                captures.append(record)

        def events(self, messages, model, stopped, on_response, *, tools=None,
                   tool_choice=None, response_format=None):
            self._capture("events", messages, model, tools)
            yield from super().events(messages, model, stopped, on_response,
                                      tools=tools, tool_choice=tool_choice,
                                      response_format=response_format)

        def json_tokens(self, messages, model, stopped, on_response):
            self._capture("json_tokens", messages, model)
            yield from super().json_tokens(messages, model, stopped, on_response)

    search_calls = []
    reader_calls = []

    def synthetic_search():
        def handler(arguments, _context):
            search_calls.append(scrub(copy.deepcopy(arguments)))
            return ToolResult(content={"searchedAt": timestamp(), "synthetic": True}, sources=[
                {"id": "synthetic-current", "title": "合成验收：星辰动力与远川汽车制动支架供货记录",
                 "url": CURRENT_URL, "publishedAt": "2026-10-10T10:00:00+08:00",
                 "snippet": CURRENT_TEXT},
                {"id": "synthetic-old", "title": "合成验收：2019年远川汽车零部件供货争议旧例",
                 "url": OLD_URL, "publishedAt": "2026-10-09T10:00:00+08:00",
                 "snippet": OLD_TEXT},
            ])

        return ToolSpec("web_search", "本地合成研究材料检索；所有内容均为虚构", {
            "type": "object", "properties": {
                "query": {"type": "string"}, "count": {"type": "integer"},
                "freshness": {"type": "string"}}, "required": ["query"]},
            handler, max_calls=12)

    def synthetic_reader(dns_mode="system"):
        def handler(arguments, _context):
            reader_calls.append(scrub(copy.deepcopy(arguments)))
            url = arguments["url"]
            if url not in {CURRENT_URL, OLD_URL}:
                raise GatewayError("合成验收只允许读取本地定义的两个地址。", 403)
            return ToolResult(content={"status": "ok", "title": "合成验收网页正文",
                               "publisher": "本地合成来源", "final_url": url,
                               "text": CURRENT_TEXT if url == CURRENT_URL else OLD_TEXT,
                               "synthetic": True})

        return ToolSpec("fetch_public_page", "本地合成网页正文", {"type": "object",
            "properties": {"url": {"type": "string"},
                           "target_section": {"type": "string"},
                           "max_chars": {"type": "integer"}},
            "required": ["url"]}, handler, max_calls=8)

    app = None
    service = None
    started = time.monotonic()

    def captured_transport():
        with capture_lock:
            return [copy.deepcopy(item) for item in captures if item["kind"] == "events"]

    def write_captures():
        with capture_lock:
            snapshot = copy.deepcopy(captures)
        save_json(args.output / "model_inputs_redacted.json", snapshot)

    def progress(label, run):
        print(json.dumps({"phase": label, "run_status": run.get("status"),
                          "run_stage": run.get("stage"),
                          "elapsed_seconds": round(time.monotonic() - started, 1)},
                         ensure_ascii=False), flush=True)

    def wait(client, run_id, label, expected):
        deadline = time.monotonic() + args.timeout + 15
        next_update = time.monotonic() + 10
        while True:
            response = client.get(BASE + "/runs/" + run_id)
            if response.status_code != 200:
                raise RuntimeError(f"读取 {label} 状态失败：HTTP {response.status_code}")
            run = response.json["run"]
            if run["status"] in expected:
                progress(label, run)
                return run
            if run["status"] in {"failed", "cancelled", "interrupted"}:
                progress(label, run)
                raise RuntimeError(f"{label} ended {run['status']}: {run.get('error')}")
            now = time.monotonic()
            if now >= deadline:
                client.post(BASE + f"/runs/{run_id}/cancel")
                raise TimeoutError(f"{label} exceeded its deadline; cancellation requested")
            if now >= next_update:
                progress(label, run)
                next_update = now + 10
            time.sleep(1)

    def post_run(client, session_id, module, question, **extra):
        body = {"module": module, "question": question,
                "idempotency_key": str(uuid4()), **extra}
        response = client.post(BASE + f"/sessions/{session_id}/runs", json=body)
        if response.status_code != 202:
            raise RuntimeError(f"提交 {module} 失败：HTTP {response.status_code} " +
                               str((response.json or {}).get("code") or (response.json or {}).get("error")))
        return response.json["run"]["id"]

    def write_stage(label, run):
        report = run.get("report")
        save_json(args.output / f"{label}-run.json", scrub(run))
        if report:
            save_json(args.output / f"{label}-synthetic-report.json", scrub(report))
            (args.output / f"{label}-synthetic-report.md").write_text(
                "> 本文件为真实 GLM 对合成验收材料的响应，不是真实新闻研究。\n\n" +
                report.get("markdown", ""), encoding="utf-8")
        summary["steps"].append({
            "label": label, "run_id": run["id"], "module": run["module"],
            "status": run["status"], "stage": run.get("stage"),
            "error_code": run.get("error_code"), "error": run.get("error"),
            "report_id": (report or {}).get("id"),
            "report_format": (report or {}).get("report_format"),
            "report_variant": (report or {}).get("report_variant"),
            "initial_answer_mode": run.get("initial_answer_mode"),
            "memory_input_id": (run.get("memory_input") or {}).get("id"),
            "memory_output_id": (run.get("memory_output") or {}).get("id"),
            "memory_output_version": (run.get("memory_output") or {}).get("version"),
            "model_transport_statuses": [item.get("status") for item in
                                        run.get("model_transport_trace") or []],
        })
        save_json(args.output / "summary.json", summary)

    try:
        with tempfile.TemporaryDirectory(prefix="explore-memory-real-glm-") as temporary:
            app = create_app({
                "TESTING": True, "DATA_DIR": str(Path(temporary) / "data"),
                "AGENT_RUNTIME_DIR": str(Path(temporary) / "runtime"),
                "IMPORT_LEGACY_HISTORY": False,
                "EXPLORE_EVENT_LITE_ENABLED": True,
                "EXPLORE_RELATION_LITE_ENABLED": True,
                "EXPLORE_RUN_TIMEOUT_SECONDS": args.timeout,
                "EXPLORE_PUBLIC_ENTRY_CATALOG": "[]", "SFC_TOKEN": "", "SFC_KEY_FILE": "",
            })
            client = app.test_client()
            service = app.extensions["explore_service"]
            provider_config = {**app.config,
                "GITEE_AI_MAX_TOKENS": app.config["EXPLORE_MODEL_MAX_TOKENS"],
                "GITEE_AI_REASONING_EFFORT": app.config["EXPLORE_MODEL_REASONING_EFFORT"]}
            service.provider = CaptureProvider(provider_config)
            service.search_tools = ToolRegistry((synthetic_search(),))
            password = secrets.token_urlsafe(24)
            sensitive.append(password)
            registered = client.post("/desktop/api/register", json={
                "email": "synthetic-explore-memory-" + secrets.token_hex(8) + "@example.test",
                "password": password})
            if registered.status_code != 200:
                raise RuntimeError(f"合成账号创建失败：HTTP {registered.status_code}")
            session_response = client.post(BASE + "/sessions", json={"title": "合成验收：连续研究记忆"})
            if session_response.status_code != 201:
                raise RuntimeError(f"合成会话创建失败：HTTP {session_response.status_code}")
            session_id = session_response.json["session"]["id"]
            summary["status"] = "running"
            save_json(args.output / "summary.json", summary)
            progress("start", {"status": "running", "stage": "preflight_complete"})

            with patch("backend.app.modules.explore.event_lite_runtime.build_public_reader_tool", synthetic_reader), \
                 patch("backend.app.modules.explore.relation_lite_runtime.build_public_reader_tool", synthetic_reader), \
                 patch("backend.app.modules.explore.research_runtime.build_public_reader_tool", synthetic_reader):
                event_id = post_run(client, session_id, "event", FIRST_QUESTION)
                first = wait(client, event_id, "event_initial", {"awaiting_feedback"})
                write_stage("event_initial", first)
                m1 = first.get("memory_output")
                if not m1:
                    raise AssertionError("理解事件初答未发布 v1 记忆")
                summary["checks"]["event_initial_model_complete"] = first.get("initial_answer_mode") == "model_complete"
                summary["checks"]["event_initial_answer_mode"] = first.get("initial_answer_mode")
                feedback = client.post(BASE + f"/runs/{event_id}/feedback", json={
                    "choice": "explain_more", "idempotency_key": str(uuid4())})
                if feedback.status_code != 202:
                    raise RuntimeError(f"进一步解释提交失败：HTTP {feedback.status_code}")
                event = wait(client, event_id, "event_deep", {"succeeded", "partial"})
                write_stage("event_deep", event)
                m2 = event.get("memory_output")
                if not event.get("report") or not m2 or m2.get("parent_id") != m1["id"]:
                    raise AssertionError("理解事件深入报告或 v2 记忆缺失")
                deep_calls = [item for item in captured_transport()
                              if item["stage"] == "event_deep_lite"]
                if not deep_calls:
                    raise AssertionError("未观察到真实 GLM 深入解释请求")
                verify_memory_call(deep_calls[-1], m1)
                summary["checks"]["event_deep_received_initial_memory"] = True

                options = event["report"].get("recommendations") or []
                relation_option = next((row for row in options if row.get("module") == "relation"), None)
                if not relation_option:
                    raise AssertionError("真实 GLM 的事件报告未生成可点击的发现关联推荐")
                relation_id = post_run(client, session_id, "relation", relation_option["question"],
                                       source_report_id=event["report"]["id"],
                                       context_mode="manual", file_ids=[])
                relation = wait(client, relation_id, "relation", {"succeeded", "partial"})
                write_stage("relation", relation)
                m3 = relation.get("memory_output")
                if not relation.get("report") or not m3 or m3.get("parent_id") != m2["id"]:
                    raise AssertionError("发现关联报告或 v3 记忆缺失")
                relation_calls = captured_transport()
                for stage in ("relation_select", "relation_lite_report"):
                    selected = [item for item in relation_calls if item["stage"] == stage]
                    if not selected:
                        raise AssertionError(f"未观察到真实 GLM 的 {stage} 请求")
                    for item in selected:
                        verify_memory_call(item, m2)
                summary["checks"]["relation_select_received_event_memory"] = True
                summary["checks"]["relation_report_received_event_memory"] = True
                if relation.get("context_files"):
                    raise AssertionError("手动空文件列表不应暗中选取旧文件")

                history_option = next((row for row in (relation["report"].get("recommendations") or [])
                                       if row.get("module") == "history"), None)
                if not history_option:
                    raise AssertionError("真实 GLM 的关联报告未生成可点击的历史验证推荐")
                history_start_index = len(captured_transport())
                history_id = post_run(client, session_id, "history", history_option["question"],
                                      source_report_id=relation["report"]["id"])
                history = wait(client, history_id, "history", {"succeeded", "partial"})
                write_stage("history", history)
                m4 = history.get("memory_output")
                if not history.get("report") or not m4 or m4.get("parent_id") != m3["id"]:
                    raise AssertionError("历史验证报告或 v4 记忆缺失")
                summary["checks"]["history_report_saved"] = True
                summary["checks"]["history_memory_version_published"] = True
                history_calls = captured_transport()[history_start_index:]
                history_research = [item for item in history_calls
                    if isinstance(item["first_user_json"], dict)
                    and "research_task" in item["first_user_json"]
                    and item["first_user_json"].get("task") != "research_report"]
                history_writers = [item for item in history_calls
                    if isinstance(item["first_user_json"], dict)
                    and item["first_user_json"].get("task") == "research_report"]
                if not any(stage["module"] == "event" for stage in m3["state"]["stages"]):
                    raise AssertionError("关联后的记忆丢失 A 阶段")
                if not any(stage["module"] == "relation" for stage in m3["state"]["stages"]):
                    raise AssertionError("关联后的记忆丢失 B 阶段")
                if not history_research:
                    raise AssertionError("未观察到历史验证的真实研究模型调用")
                for item in history_research:
                    verify_memory_call(item, m3)
                summary["checks"]["history_research_received_ab_memory"] = True

                listing = client.get(BASE + f"/sessions/{session_id}/memory",
                                     query_string={"run_id": history_id})
                if listing.status_code != 200:
                    raise AssertionError("读取合成研究链记忆版本失败")
                memories = listing.json["memories"]
                if [item["id"] for item in memories] != [m1["id"], m2["id"], m3["id"], m4["id"]]:
                    raise AssertionError("记忆版本历史不完整或顺序错误")
                save_json(args.output / "memory_versions.json", scrub(memories))
                for item in memories:
                    (args.output / f"memory-v{item['version']}.md").write_text(
                        item["content"], encoding="utf-8")
                summary["checks"]["immutable_memory_versions"] = True
                summary["checks"]["history_writer_invoked"] = bool(history_writers)
                if not history_writers:
                    summary["checks"]["history_writer_received_ab_memory"] = False
                    if history["report"].get("report_variant") != "insufficient_evidence":
                        raise AssertionError("历史写作模型未执行，且报告不是不足证据分支")
                    if args.require_history_writer:
                        raise AssertionError(
                            "历史研究没有形成可追溯主张，产品保存了不足证据报告和 v4，按设计未调用写作模型")
                else:
                    for item in history_writers:
                        verify_memory_call(item, m3)
                    summary["checks"]["history_writer_received_ab_memory"] = True

            summary.update(status="memory_delivery_passed", ok=True,
                           memory_delivery_ok=True,
                           analysis_status=history.get("status"),
                           history_writer_executed=bool(history_writers),
                           history_writer_skipped_reason=(
                               "insufficient_evidence" if not history_writers else None),
                           model_transport_requests=len(captured_transport()),
                           synthetic_search_calls=len(search_calls),
                           synthetic_reader_calls=len(reader_calls),
                           elapsed_seconds=round(time.monotonic() - started, 2))
            save_json(args.output / "synthetic_tool_calls.json", {
                "search": search_calls, "public_reader": reader_calls})
            write_captures()
            save_json(args.output / "summary.json", summary)
            print(json.dumps({"status": summary["status"], "model": summary["model"],
                              "requests": summary["model_transport_requests"],
                              "elapsed_seconds": summary["elapsed_seconds"]},
                             ensure_ascii=False), flush=True)
            return 0
    except Exception as exc:
        summary.update(status="failed", ok=False, failure_type=type(exc).__name__,
                       failure=scrub(str(exc)),
                       model_transport_requests=len(captured_transport()),
                       synthetic_search_calls=len(search_calls),
                       synthetic_reader_calls=len(reader_calls),
                       elapsed_seconds=round(time.monotonic() - started, 2))
        save_json(args.output / "synthetic_tool_calls.json", {
            "search": search_calls, "public_reader": reader_calls})
        write_captures()
        save_json(args.output / "summary.json", summary)
        print(json.dumps({"status": "failed", "type": summary["failure_type"],
                          "reason": summary["failure"],
                          "elapsed_seconds": summary["elapsed_seconds"]},
                         ensure_ascii=False), flush=True)
        return 1
    finally:
        if service is not None:
            service.close()
        if app is not None:
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    raise SystemExit(main())
