"""Synthetic, disposable Explore event -> relation -> history browser fixture.

Start after building frontend/dist::

    .venv/bin/python tests/browser_explore_chain.py --port 0

The printed local URL uses a free port. Accounts and SQLite are temporary.
No model, search, or article request leaves this process. The audit route
returns only stage names, call counts, and whether a harmless analysis marker
reached the actual model payloads; it never returns prompts or user content.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _name in tuple(os.environ):
    if _name.startswith("GITEE_AI_"):
        os.environ.pop(_name, None)

from flask import jsonify
from werkzeug.serving import WSGIRequestHandler, make_server

from backend.app import create_app
from backend.app.modules.explore import event_lite_runtime, relation_lite_runtime, history_lite_runtime
from backend.app.modules.explore.repository import timestamp
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


ANALYSIS_MARKER = "CHAIN_ANALYSIS_MARKER_X7Q4"
CURRENT_TEXT = ("合成测试材料：测试甲集团与测试乙公司于近期公开说明，双方讨论测试零件供应与"
                "产品集成合作。公告只说明合作方向，实际合同数量和履约情况仍未披露。")
HISTORY_TEXT = ("合成测试材料：2022年测试甲集团与测试乙公司披露供应框架协议。"
                "乙公司曾计划提供测试零件，甲集团负责产品集成；后续履约规模没有在这则材料中确认。")


class Audit:
    def __init__(self):
        self.lock = threading.Lock()
        self.history_search_started = threading.Event()
        self.history_search_release = threading.Event()
        self.calls = []
        self.search_calls = []
        self.page_calls = 0

    def model(self, kind, stage, module, payload):
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self.lock:
            self.calls.append({"kind": kind, "stage": stage, "module": module,
                "analysisMarkerSeen": ANALYSIS_MARKER in raw,
                "contextFileCount": len(payload.get("context_files") or []),
                "memoryPresent": bool(payload.get("research_memory"))})

    def snapshot(self):
        with self.lock:
            calls = list(self.calls)
            searches = list(self.search_calls)
            page_calls = self.page_calls
        expected = ("relation_select", "relation_lite_report",
                    "history_initial", "history_lite_report")
        marker = {stage: any(call["stage"] == stage and call["analysisMarkerSeen"]
                             for call in calls) for stage in expected}
        return {"fixture": True, "realModel": False, "realNews": False,
            "calls": calls, "analysisMarkerInModelPayload": marker,
            "searchCount": len(searches), "searches": searches,
            "pageReadCount": page_calls,
            "historySearchStarted": self.history_search_started.is_set(),
            "historySearchReleased": self.history_search_release.is_set()}


class ChainProvider:
    model = "browser-chain-synthetic"
    key = "fixture-only"

    def __init__(self, audit):
        self.audit = audit

    def events(self, messages, _model, stopped, on_response, *, tools=None, **_options):
        assert tools is None
        payload = json.loads(messages[1]["content"])
        stage = payload.get("stage") or "event_initial"
        module = payload.get("module") or "event"
        self.audit.model("events", stage, module, payload)
        if stopped.is_set():
            raise GatewayError("合成首答已取消。", 409, "cancelled")
        answer = ("测试甲集团与测试乙公司当前有供应合作线索。查找双方过去共同涉及的新闻，"
                  "可核对合作如何形成和分工是否变化，帮助理解目前的关联。"
                  if stage == "history_initial" else
                  "公开材料说明双方讨论测试零件供应与产品集成；具体合同及履约结果仍需进一步核实。")
        try:
            yield {"type": "token", "text": answer}
            yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}
        finally:
            on_response(None)

    def json_tokens(self, messages, _model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        stage = payload.get("stage")
        module = payload.get("module") or ("event" if stage == "event_deep_lite" else "relation")
        self.audit.model("json_tokens", stage, module, payload)
        if stopped.is_set():
            raise GatewayError("合成报告已取消。", 409, "cancelled")
        refs = [row["id"] for row in payload.get("sources") or []]
        recommendations = [
            {"module": "event", "question": "测试甲集团与测试乙公司的合作最近有何进展？",
             "reason": "核对当前事实"},
            {"module": "relation", "question": "测试甲集团与测试乙公司目前的供应分工是什么？",
             "reason": "厘清双方作用"},
            {"module": "history", "question": "测试甲集团与测试乙公司的历史合作如何形成和变化？",
             "reason": "理解历史关系"},
        ]
        if stage == "relation_select":
            answer = {"needs_clarification": False, "question": "",
                "entities": [{"name": "测试甲集团", "reason": "核对产品集成职责"},
                             {"name": "测试乙公司", "reason": "核对零件供应职责"}]}
        elif stage == "event_deep_lite":
            answer = {"title": "双方合作事件进一步解释",
                "summary": f"{ANALYSIS_MARKER}。合成材料显示双方讨论供应合作，履约结果尚未确认。[S1]",
                "key_findings": ["双方公开讨论测试零件供应和产品集成，实际规模仍需核实。[S1]"],
                "sections": [{"heading": "事件和现有材料", "body":
                    f"{ANALYSIS_MARKER}。公告提到合作方向，但尚不能据此推断已形成稳定供货。[S1]",
                    "source_refs": refs[:1]}], "recommendations": recommendations}
        elif stage == "relation_lite_report":
            answer = {"title": "双方供应关系分析",
                "summary": f"{ANALYSIS_MARKER}。双方可能形成供应与集成分工，具体合同边界待核实。[S1]",
                "key_findings": ["当前公开材料支持合作线索，不能证明实际采购规模。[S1]"],
                "sections": [{"heading": "当前关联及边界", "body":
                    f"{ANALYSIS_MARKER}。测试乙公司提供测试零件、测试甲集团负责产品集成的安排仍以具体合同为准。[S1]",
                    "source_refs": refs[:1]}],
                "entities": ["测试甲集团", "测试乙公司"], "relationships": [],
                "recommendations": recommendations}
        elif stage == "history_lite_report":
            answer = {"title": "双方历史合作分析",
                "summary": f"{ANALYSIS_MARKER}。2022年报道提及框架协议，历史分工为当前关系提供线索，履约结果仍待核对。[S1]",
                "key_findings": ["历史新闻提及双方供应框架协议，但没有证明目前合同继续履行。[S1]"],
                "sections": [{"heading": "历史关系与当前问题", "body":
                    f"{ANALYSIS_MARKER}。当年协议安排有助于理解当前合作来源，但不能直接证明现有供货规模。[S1]",
                    "source_refs": refs[:1]}], "recommendations": recommendations}
        else:
            raise AssertionError(f"unexpected stage: {stage!r}")
        try:
            yield json.dumps(answer, ensure_ascii=False)
        finally:
            on_response(None)


class QuietHandler(WSGIRequestHandler):
    def log(self, _type, _message, *args):
        pass


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fixture_app(directory, port, frontend_dist):
    root = Path(directory)
    app = create_app({"TESTING": True, "DESKTOP_PORT": port,
        "DATA_DIR": str(root / "data"), "FRONTEND_DIST": str(frontend_dist),
        "AGENT_RUNTIME_DIR": str(root / "runtime"), "GITEE_AI_API_KEY": "",
        "GITEE_AI_BASE_URL": "http://127.0.0.1:1/v1", "IMPORT_LEGACY_HISTORY": False,
        "EXPLORE_EVENT_LITE_ENABLED": True, "EXPLORE_RELATION_LITE_ENABLED": True,
        "EXPLORE_HISTORY_LITE_ENABLED": True, "EXPLORE_RUN_TIMEOUT_SECONDS": 120})
    audit = Audit()
    explore = app.extensions["explore_service"]
    explore.provider = ChainProvider(audit)

    def search(arguments, context):
        query = str(arguments.get("query") or "")
        is_history = query.endswith("历史关联 新闻")
        with audit.lock:
            audit.search_calls.append({"count": arguments.get("count"),
                "freshness": arguments.get("freshness"), "history": is_history})
        if is_history:
            audit.history_search_started.set()
            while not audit.history_search_release.wait(.05):
                if context.stopped.is_set():
                    raise GatewayError("合成历史检索已取消。", 409, "cancelled")
        current = {"id": "chain-current", "title": "测试甲集团与测试乙公司近期供应合作说明",
            "snippet": CURRENT_TEXT, "publishedAt": timestamp(),
            "url": "https://example.org/chain/current"}
        historical = {"id": "chain-history", "title": "测试甲集团与测试乙公司2022年供应框架协议",
            "snippet": HISTORY_TEXT, "publishedAt": "2022-06-18T09:00:00+08:00",
            "url": "https://example.org/chain/history"}
        rows = [historical, current] if is_history else [current, historical]
        return ToolResult(content={"searchedAt": timestamp()},
                          sources=rows[:int(arguments.get("count") or 2)])

    def read_page(arguments, _context):
        with audit.lock:
            audit.page_calls += 1
        body = HISTORY_TEXT if "/history" in arguments["url"] else CURRENT_TEXT
        return ToolResult(content={"status": "ok", "text": body,
            "final_url": arguments["url"], "publisher": "合成测试新闻"})

    explore.search_tools = ToolRegistry((ToolSpec("web_search", "合成新闻搜索",
        {"type": "object"}, search, max_calls=12),))
    def reader(_mode):
        return ToolSpec("fetch_public_page", "合成新闻正文", {"type": "object"},
                        read_page, max_calls=8)
    event_lite_runtime.build_public_reader_tool = reader
    relation_lite_runtime.build_public_reader_tool = reader
    history_lite_runtime.build_public_reader_tool = reader

    @app.get("/__explore_chain__/audit")
    def audit_route():
        return jsonify(audit.snapshot())

    @app.post("/__explore_chain__/gate/release")
    def release_gate():
        audit.history_search_release.set()
        return jsonify(audit.snapshot())

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--frontend-dist", type=Path, default=ROOT / "frontend" / "dist")
    options = parser.parse_args()
    if not (options.frontend_dist / "index.html").is_file():
        parser.error("Build the frontend first: npm --prefix frontend run build")
    port = options.port or free_port()
    signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    with tempfile.TemporaryDirectory(prefix="cd80-explore-chain-") as directory:
        app = fixture_app(directory, port, options.frontend_dist)
        server = None
        try:
            server = make_server("127.0.0.1", port, app, threaded=True,
                                 request_handler=QuietHandler)
            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{port}",
                "fixture": True, "realModel": False, "realNews": False,
                "temporaryData": True}), flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server is not None:
                server.server_close()
            app.extensions["explore_service"].close()
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    main()
