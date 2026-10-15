"""Exercise both Explore event buttons in a real frontend build on an isolated server.

Default mode uses synthetic model/search/page responses and makes no external
requests. ``--real`` uses configured GLM and news services while still creating
a disposable account and SQLite database. Neither mode touches the live site.

    .venv/bin/python scripts/validate_explore_event_choices_live.py --output /tmp/event-choice-check
    .venv/bin/python scripts/validate_explore_event_choices_live.py --real --output /tmp/event-choice-real
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DEFAULT_QUESTION = "测试甲集团拟收购测试乙公司，这类消息为什么可能影响股票价格？"


class Observations:
    def __init__(self):
        self.lock = threading.Lock()
        self.initial_model_calls = 0
        self.report_model_calls = 0
        self.search_calls = []
        self.fetch_calls = []

    def initial(self):
        with self.lock:
            self.initial_model_calls += 1

    def report(self):
        with self.lock:
            self.report_model_calls += 1

    def search(self, arguments):
        with self.lock:
            self.search_calls.append(dict(arguments))

    def fetch(self, arguments):
        with self.lock:
            self.fetch_calls.append(dict(arguments))

    def snapshot(self):
        with self.lock:
            return {"fixture": True, "realModel": False, "realNews": False,
                    "initialModelCalls": self.initial_model_calls,
                    "reportModelCalls": self.report_model_calls,
                    "searchCalls": list(self.search_calls),
                    "fetchCalls": len(self.fetch_calls),
                    "fetchUrls": [item["url"] for item in self.fetch_calls]}


class SyntheticProvider:
    model = "event-choice-fixture-no-network"
    key = "fixture-only"

    def __init__(self, observations):
        self.observations = observations

    def tokens(self, messages, model, stopped, on_response, *, response_format=None):
        assert len(messages) == 2 and messages[1]["role"] == "user"
        assert "只输出解释正文" in messages[0]["content"]
        self.observations.initial()
        answer = (
            "拟收购的消息会改变投资者对两家公司未来盈利的预期。买方可能付出较高成本，"
            "卖方可能获得更明确的交易价格，所以相关股价有时会波动。"
            "但公告本身不能证明股价已经上涨，也不能证明交易一定完成。"
        )
        try:
            for offset in range(0, len(answer), 60):
                if stopped.is_set():
                    raise RuntimeError("fixture model cancelled")
                yield answer[offset:offset + 60]
        finally:
            on_response(None)

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        assert payload["task"] == "research_report"
        assert payload["stage"] == "event_deep_lite"
        assert len(payload["sources"]) <= 5
        self.observations.report()
        refs = [item["id"] for item in payload["sources"]]
        body = (
            "合成新闻显示测试甲集团提出拟收购测试乙公司，交易仍需批准。"
            "这类消息可能改变市场对未来收入、成本与竞争格局的估计。"
            "价格变化还会受到估值、资金和大盘共同影响，因此不能把同期波动全归因于并购消息。"
        )
        answer = json.dumps({
            "title": "合成新闻：收购消息与股价预期",
            "summary": "合成材料支持拟收购消息；股价变化的具体原因仍需行情与公告核对。",
            "key_findings": ["测试甲集团提出拟收购，交易结果尚未确定。"],
            "sections": [{"heading": "结合新闻进一步解释", "body": body,
                          "source_refs": refs[:2]}],
        }, ensure_ascii=False)
        try:
            for offset in range(0, len(answer), 100):
                if stopped.is_set():
                    raise RuntimeError("fixture report cancelled")
                yield answer[offset:offset + 100]
        finally:
            on_response(None)

    def events(self, *_args, **_kwargs):
        raise AssertionError("the two-step event fixture must use plain and JSON model calls")


class SyntheticSearch:
    available = True

    def __init__(self, observations):
        self.observations = observations

    def search(self, arguments, stopped, on_response):
        self.observations.search(arguments)
        assert arguments["count"] == 5, "event deep report must request five at most"
        assert not stopped.is_set()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        sources = [{
            "id": f"synthetic-{index}",
            "title": f"合成新闻 {index}：测试甲集团拟收购测试乙公司",
            "snippet": "合成测试材料：测试甲集团提出拟收购测试乙公司，交易尚需批准。",
            "publishedAt": now,
            "url": f"https://example.org/event-choice/article-{index}",
        } for index in range(1, 8)]
        on_response(None)
        return {"query": arguments["query"], "freshness": arguments["freshness"],
                "searchedAt": now, "sources": sources[:arguments["count"]]}


def install_fixture(app):
    """Replace only outbound dependencies on this temporary app instance."""
    from flask import jsonify
    from backend.app.modules.explore import event_lite_runtime
    from backend.app.modules.workbench.builtin_tools import make_search_tool
    from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec

    observations = Observations()
    explore = app.extensions["explore_service"]
    explore.provider = SyntheticProvider(observations)
    explore.search_tools = ToolRegistry((make_search_tool(SyntheticSearch(observations)),))

    # The production runtime still calls the registered public-reader tool;
    # only its network handler is synthetic within this isolated process.
    def reader_tool(_dns_mode):
        def read(arguments, context):
            observations.fetch(arguments)
            assert arguments["url"].startswith("https://example.test/event-choice/article-")
            assert not context.stopped.is_set()
            context.on_response(None)
            return ToolResult(content={"status": "ok", "final_url": arguments["url"],
                "publisher": "合成测试新闻", "text": "测试甲集团拟收购测试乙公司；交易仍需监管及股东批准。"})
        return ToolSpec(name="fetch_public_page", description="合成原文读取，仅用于浏览器验收。",
            parameters={"type": "object", "properties": {"url": {"type": "string"},
                        "max_chars": {"type": "integer"}}, "required": ["url"],
                        "additionalProperties": False}, handler=read, max_calls=5)
    event_lite_runtime.build_public_reader_tool = reader_tool

    @app.get("/__explore_event_choice_fixture__/stats")
    def fixture_stats():
        return jsonify(observations.snapshot())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "explore-event-choices" / time.strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--real", action="store_true", help="Use actual configured GLM and public search; still keep a temporary account and database.")
    parser.add_argument("--question", default=DEFAULT_QUESTION,
                        help="Question used for both branches. Supply a real public-market question with --real.")
    args = parser.parse_args()
    frontend_dist = ROOT / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file():
        parser.error("Build the current frontend first: npm --prefix frontend run build")

    if args.real:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    else:
        # A shell that happens to contain real credentials must not cause a
        # model or news request in the default acceptance run.
        for name in tuple(os.environ):
            if name.startswith("GITEE_AI_") or name in {"SFC_TOKEN", "SFC_KEY_FILE", "CD80_ADMIN_API_KEY", "CD80_READ_API_KEY"}:
                os.environ.pop(name, None)

    import requests
    from werkzeug.serving import WSGIRequestHandler, make_server
    from backend.app import create_app
    from backend.app.config import Config

    if args.real and not Config.GITEE_AI_API_KEY:
        parser.error("Real mode requires the configured model credential; no request was made.")
    args.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.output, 0o700)

    class Quiet(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix="explore-event-choices-") as temporary:
        app = create_app({
            "TESTING": True, "DATA_DIR": temporary, "DESKTOP_PORT": 0,
            "FRONTEND_DIST": str(frontend_dist), "IMPORT_LEGACY_HISTORY": False,
            "AGENT_RUNTIME_DIR": str(Path(temporary) / "no-runtime"),
            "EXPLORE_EVENT_LITE_ENABLED": True,
            "EXPLORE_RUN_TIMEOUT_SECONDS": 600 if args.real else 60,
            **({} if args.real else {"GITEE_AI_API_KEY": "", "SFC_TOKEN": "",
                                    "GITEE_AI_BASE_URL": "http://127.0.0.1:1/v1"}),
        })
        if not args.real:
            install_fixture(app)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=Quiet)
        app.config["DESKTOP_PORT"] = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f"http://127.0.0.1:{server.server_port}"
        client = requests.Session()
        client.trust_env = False
        try:
            registered = client.post(origin + "/desktop/api/register", json={
                "name": "Explore event choice acceptance",
                "email": f"event-choice-{secrets.token_hex(8)}@example.invalid",
                "password": secrets.token_urlsafe(32)}, timeout=15)
            registered.raise_for_status()
            payload = {"origin": origin, "output": str(args.output.resolve()),
                "fixture": not args.real, "questions": [args.question, args.question],
                "maxWaitMs": 540000 if args.real else 45000,
                "cookies": [{"name": cookie.name, "value": cookie.value, "url": origin}
                            for cookie in client.cookies]}
            # The short-lived cookie stays on stdin, never in arguments or output.
            result = subprocess.run(["node", str(ROOT / "scripts/capture_explore_event_choices.mjs")],
                input=json.dumps(payload), text=True, capture_output=True,
                timeout=1200 if args.real else 180, check=False)
            if result.stdout:
                print(result.stdout.strip())
            if result.stderr:
                print(result.stderr.strip(), file=sys.stderr)
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            app.extensions["explore_service"].close()
            app.extensions["workbench_service"].close()


if __name__ == "__main__":
    raise SystemExit(main())
