"""Disposable Explore AI-failure presentation fixture; never calls an external service.

This derives from the current relation-lite chain fixture, then adds synthetic
question markers for empty search, first-answer timeout and user cancellation.
All accounts and reports live in a temporary directory and are deleted on exit.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import tempfile
import time

from flask import jsonify
from werkzeug.serving import make_server

from browser_explore_chain import (ROOT, ChainProvider, QuietHandler, fixture_app,
                                   free_port)
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.tool_registry import ToolRegistry, ToolResult, ToolSpec


class FailureCaseProvider(ChainProvider):
    def events(self, messages, model, stopped, on_response, *, tools=None, **options):
        payload = json.loads(messages[1]["content"])
        if "[FAIL_INITIAL]" in str(payload.get("question") or ""):
            # Matches the real first-step timeout fallback contract.
            raise GatewayError("合成首答超时。", 504, "model_stream_timeout")
        yield from super().events(messages, model, stopped, on_response,
                                  tools=tools, **options)

    def json_tokens(self, messages, model, stopped, on_response):
        payload = json.loads(messages[1]["content"])
        question = str(payload.get("question") or "")
        if "[WAIT_EXPLORE]" in question:
            until = time.monotonic() + 25
            while time.monotonic() < until and not stopped.wait(.05):
                pass
            if stopped.is_set():
                raise GatewayError("合成模型已取消。", 409, "cancelled")
        if "[FAIL_EXPLORE]" in question and payload.get("stage") == "relation_lite_report":
            raise GatewayError("合成报告生成失败。", 502, "model_stream_interrupted")
        yield from super().json_tokens(messages, model, stopped, on_response)


def ai_failure_fixture_app(directory, port, frontend_dist):
    app = fixture_app(directory, port, frontend_dist)
    explore = app.extensions["explore_service"]
    audit = explore.provider.audit
    audit.history_search_release.set()
    explore.provider = FailureCaseProvider(audit)
    original = explore.search_tools.get("web_search")

    def search(arguments, context):
        if "[WEB_SEARCH_EMPTY]" in str(arguments.get("query") or ""):
            with audit.lock:
                audit.search_calls.append({"empty": True,
                    "count": arguments.get("count"), "freshness": arguments.get("freshness")})
            return ToolResult(content={"searchedAt": "synthetic", "rawCount": 0}, sources=[])
        return original.handler(arguments, context)

    explore.search_tools = ToolRegistry((ToolSpec("web_search", "合成新闻搜索",
        {"type": "object"}, search, max_calls=12),))

    @app.get("/__ai_failure_fixture__/stats")
    def stats():
        return jsonify({**audit.snapshot(), "fixture": "ai-failure-presentation",
                        "realModel": False, "realNews": False,
                        "temporaryData": True})

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--frontend-dist", type=Path, default=ROOT / "frontend" / "dist")
    options = parser.parse_args()
    if not (options.frontend_dist / "index.html").is_file():
        parser.error("Build the backup frontend first")
    port = options.port or free_port()
    signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    with tempfile.TemporaryDirectory(prefix="cd80-ai-failure-fixture-") as directory:
        app = ai_failure_fixture_app(directory, port, options.frontend_dist)
        server = None
        try:
            server = make_server("127.0.0.1", port, app, threaded=True,
                                 request_handler=QuietHandler)
            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{port}",
                "fixture": "ai-failure-presentation", "realModel": False,
                "realNews": False, "temporaryData": True}), flush=True)
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
