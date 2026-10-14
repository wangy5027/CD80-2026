"""Disposable browser fixture for the live history-lite first-answer flow.

Run from the repository root after building frontend/dist::

    .venv/bin/python tests/browser_explore_history.py --port 0

The printed URL uses a free local port. Accounts and SQLite live in a temporary
directory. Model output, news search, and optional page reads are all synthetic.
The search gate stays closed until the browser test calls the fixture endpoint.
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
from backend.app.modules.explore import history_lite_runtime
from backend.app.modules.explore.repository import timestamp
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


FIRST_ANSWER = ("测试甲集团与测试乙公司目前存在供应合作线索。检索双方过去共同涉及的新闻，"
                "可以核对合作何时形成、分工是否变化，并帮助理解当前关系的由来。")
NEWS_TEXT = ("这是一则只用于浏览器验收的合成历史新闻。2022年，测试甲集团与测试乙公司"
             "共同披露供应框架协议，双方约定由乙公司提供测试零件，甲集团负责产品集成。"
             "协议中的实际供货规模和后来执行情况仍需要其他材料核实。" * 3)


class Observations:
    def __init__(self):
        self.lock = threading.Lock()
        self.search_started = threading.Event()
        self.search_release = threading.Event()
        self.initial_calls = 0
        self.report_calls = 0
        self.search_calls = []
        self.page_calls = 0

    def stats(self):
        with self.lock:
            return {"fixture": True, "realModel": False, "realNews": False,
                    "initialCalls": self.initial_calls, "reportCalls": self.report_calls,
                    "searchCalls": len(self.search_calls), "searchArguments": list(self.search_calls),
                    "searchStarted": self.search_started.is_set(),
                    "searchReleased": self.search_release.is_set(),
                    "pageCalls": self.page_calls}


class SyntheticHistoryProvider:
    model = "browser-history-synthetic"
    key = "fixture-only"

    def __init__(self, observations):
        self.observations = observations

    def events(self, messages, _model, stopped, on_response, *, tools=None, **_options):
        assert tools is None
        payload = json.loads(messages[1]["content"])
        assert payload.get("stage") == "history_initial", payload.get("stage")
        with self.observations.lock:
            self.observations.initial_calls += 1
        if stopped.is_set():
            raise GatewayError("合成首答已取消。", 409, "cancelled")
        try:
            yield {"type": "token", "text": FIRST_ANSWER}
            yield {"type": "turn_end", "tool_calls": [], "finish_reason": "stop"}
        finally:
            on_response(None)

    def json_tokens(self, messages, _model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        assert payload.get("stage") == "history_lite_report", payload.get("stage")
        with self.observations.lock:
            self.observations.report_calls += 1
        refs = [item["id"] for item in payload.get("sources") or []]
        answer = {
            "title": "测试甲与测试乙的历史合作", "summary":
                "合成历史材料记载双方曾披露供应框架协议；这为理解当前合作提供线索，不能单凭协议推断现有供货规模。[S1]",
            "key_findings": ["双方在2022年披露过供应框架协议，实际执行规模仍需核实。[S1]"],
            "sections": [{"heading": "历史关系与当前问题", "body":
                "2022年合成报道提及测试乙公司提供测试零件、测试甲集团负责产品集成。"
                "历史分工有助于理解当前供应合作的可能来源，但不能证明当前合同仍按原安排执行。[S1]",
                "source_refs": refs[:1]}],
            "recommendations": [
                {"module": "event", "question": "这次供应合作有哪些最新进展？", "reason": "厘清当前事实"},
                {"module": "relation", "question": "双方现在的产品分工是什么？", "reason": "核对关联路径"},
                {"module": "history", "question": "协议签订之后双方合作如何变化？", "reason": "继续核对历史变化"},
            ],
        }
        try:
            if stopped.is_set():
                raise GatewayError("合成报告已取消。", 409, "cancelled")
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


def fixture_app(data_dir, port, frontend_dist):
    app = create_app({"TESTING": True, "DESKTOP_PORT": port,
        "DATA_DIR": str(Path(data_dir) / "data"), "FRONTEND_DIST": str(frontend_dist),
        "AGENT_RUNTIME_DIR": str(Path(data_dir) / "runtime"),
        "GITEE_AI_API_KEY": "", "GITEE_AI_BASE_URL": "http://127.0.0.1:1/v1",
        "IMPORT_LEGACY_HISTORY": False, "EXPLORE_HISTORY_LITE_ENABLED": True,
        "EXPLORE_RUN_TIMEOUT_SECONDS": 90})
    observations = Observations()
    explore = app.extensions["explore_service"]
    explore.provider = SyntheticHistoryProvider(observations)

    def search(arguments, context):
        with observations.lock:
            observations.search_calls.append(dict(arguments))
        observations.search_started.set()
        while not observations.search_release.wait(.05):
            if context.stopped.is_set():
                raise GatewayError("合成检索已取消。", 409, "cancelled")
        return ToolResult(content={"searchedAt": timestamp()}, sources=[{
            "id": "history-browser-2022", "title": "测试甲集团与测试乙公司2022年供应框架协议",
            "snippet": NEWS_TEXT, "publishedAt": "2022-06-18T09:00:00+08:00",
            "url": "https://example.org/history/synthetic-2022",
        }])

    def read_page(arguments, _context):
        with observations.lock:
            observations.page_calls += 1
        return ToolResult(content={"status": "ok", "text": NEWS_TEXT,
            "final_url": arguments["url"], "publisher": "合成测试新闻"})

    explore.search_tools = ToolRegistry((ToolSpec("web_search", "合成历史新闻检索",
        {"type": "object"}, search, max_calls=1),))
    history_lite_runtime.build_public_reader_tool = lambda _mode: ToolSpec(
        "fetch_public_page", "合成历史新闻正文", {"type": "object"}, read_page, max_calls=5)

    @app.get("/__explore_history_fixture__/stats")
    def stats():
        return jsonify(observations.stats())

    @app.post("/__explore_history_fixture__/release")
    def release():
        observations.search_release.set()
        return jsonify(observations.stats())

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
    with tempfile.TemporaryDirectory(prefix="cd80-history-browser-") as directory:
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
