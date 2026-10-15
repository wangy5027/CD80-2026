"""Isolated browser acceptance server; never imported by the production app.

Run from the repository root:
    .venv/Scripts/python.exe tests/browser_fixture.py

Uses the built frontend, temporary accounts/data, deterministic model text, and
the real WASI executor. No dotenv loader or production model requests are used.
Synthetic test markers in a create request:
    [WAIT_GENERATION]        keep generation pending until stopped or 30 seconds
    [WAIT_TOOL]              run a real sleeping WASI tool, allowing cancellation
    [EXTRA_FILE:<file-id>]   require explicit access to that uploaded test file
The test-only /__fixture__/stats route exposes counters, never account content.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Remove inherited provider settings before importing Config. This process never
# loads .env and cannot accidentally pass a real credential to an HTTP provider.
for _name in tuple(os.environ):
    if _name.startswith("GITEE_AI_"):
        os.environ.pop(_name, None)

from flask import jsonify
from werkzeug.serving import make_server

from backend.app import create_app
from backend.app.modules.agent_platform.executor import SandboxExecutor
from backend.app.modules.agent_platform.planner import Planner
from backend.app.modules.agent_platform.presets import CSV_SUMMARY_REF, register_builtin_agents
from backend.app.modules.workbench.errors import GatewayError
from tests.test_agent_platform import FixtureProvider, fixture_spec


class Counters:
    def __init__(self):
        self.lock = threading.Lock()
        self.values = {"classificationCalls": 0, "generationCalls": 0,
                       "explanationCalls": 0, "chatCalls": 0,
                       "toolCalls": 0, "toolSuccesses": 0, "toolFailures": 0}
        self.last_code_sha256 = None

    def add(self, name):
        with self.lock:
            self.values[name] += 1

    def snapshot(self):
        with self.lock:
            return {**self.values, "lastToolCodeSha256": self.last_code_sha256,
                    "fixture": True, "realWasi": True, "realModel": False}


class ObservedWasiExecutor:
    def __init__(self, counters, runtime):
        self.counters = counters
        self.executor = SandboxExecutor({"AGENT_RUNTIME_DIR": str(runtime), "AGENT_EXECUTION_TIMEOUT": 15})

    def status(self):
        return self.executor.status()

    def execute(self, code, payload, files, stopped):
        self.counters.add("toolCalls")
        with self.counters.lock:
            self.counters.last_code_sha256 = hashlib.sha256(code.encode()).hexdigest()
        try:
            result = self.executor.execute(code, payload, files, stopped)
        except Exception:
            self.counters.add("toolFailures")
            raise
        self.counters.add("toolSuccesses")
        return result


class BrowserFixtureProvider(FixtureProvider):
    model = "browser-fixture-no-network"
    key = "fixture-only"

    def __init__(self, counters):
        super().__init__()
        self.counters = counters

    @staticmethod
    def classify(payload):
        message = payload["message"]
        if "缺列名" in message or "未明确列名" in message:
            return {"intent": "clarify", "question": "请明确分组列名和需要求和的数值列名，例如 category 和 amount。"}
        if any(word in message for word in ("概念", "普通聊天", "你好", "什么是")):
            return {"intent": "chat"}
        files = payload.get("files", [])
        if len(files) != 1:
            return {"intent": "clarify", "question": "请为本次测试选择一份 CSV 文件。"}
        history = " ".join(item.get("content", "") for item in payload.get("history", []))
        context = message + " " + history
        if "category" not in context or not any(column in context for column in ("amount", "cost")):
            return {"intent": "clarify", "question": "请明确分组列名 category 和数值列名 amount 或 cost。"}
        # Only synthetic, explicitly named columns are recognized. Current text
        # wins over history, so a revision from amount to cost remains observable.
        value_column = "cost" if "cost" in message else "amount" if "amount" in message else "cost" if "cost" in context else "amount"
        inputs = {"fileId": files[0]["id"], "groupBy": "category", "valueColumn": value_column}
        if "调用" in message or "复用" in message:
            candidates = payload.get("candidates", [])
            refs = [item["agentRef"] for item in candidates]
            if "预置" in message:
                reference = CSV_SUMMARY_REF if CSV_SUMMARY_REF in refs else None
            else:
                reference = next((ref for ref in refs if ref != CSV_SUMMARY_REF), None)
                if reference is None and "已有" in message:
                    reference = CSV_SUMMARY_REF if CSV_SUMMARY_REF in refs else None
            if reference:
                return {"intent": "invoke", "agentRef": reference, "input": inputs}
            return {"intent": "clarify", "question": "当前没有匹配的已保存应用，请先创建并保存应用。"}
        return {"intent": "create", "goal": message, "input": inputs}

    def tokens(self, messages, model, stopped, on_response):
        self.calls.append(copy.deepcopy(messages))
        system = messages[0]["content"]
        try:
            if "分类器" in system:
                self.counters.add("classificationCalls")
                payload = json.loads(messages[1]["content"])
                answer = json.dumps(self.classify(payload), ensure_ascii=False)
            elif "生成一个通用" in system:
                self.counters.add("generationCalls")
                payload = json.loads(messages[1]["content"])
                goal = payload.get("goal", "")
                if "[WAIT_GENERATION]" in goal:
                    until = time.monotonic() + 30
                    while time.monotonic() < until:
                        if stopped.wait(.05):
                            raise GatewayError("浏览器测试生成已取消。", 409, "cancelled")
                spec = fixture_spec()
                spec["name"] = "浏览器测试 CSV 汇总应用"
                spec["goal"] = goal
                spec["tools"][0]["code"] = spec["tools"][0]["code"].replace(
                    '"rowCount": count,',
                    '"rowCount": count, "total": str(sum(totals.values())), "groupBy": payload["groupBy"], "valueColumn": payload["valueColumn"],')
                if "[WAIT_TOOL]" in goal:
                    spec["tools"][0]["code"] = "import time\n" + spec["tools"][0]["code"].replace(
                        "def run(payload):", "def run(payload):\n    time.sleep(60)")
                extra = re.search(r"\[EXTRA_FILE:([A-Za-z0-9_-]+)\]", goal)
                if extra:
                    spec["permissions"]["files"] = [extra.group(1)]
                    spec["tools"][0]["permissions"]["files"].append(extra.group(1))
                answer = json.dumps(spec, ensure_ascii=False)
            elif "独立 sub-agent" in system:
                self.counters.add("explanationCalls")
                payload = json.loads(messages[1]["content"])
                result = payload["stepOutputs"]["aggregate"]
                groups = result.get("totals") or {item["group"]: item["total"] for item in result.get("preview", [])}
                detail = "、".join(f"{key}={value}" for key, value in groups.items())
                answer = f"实际汇总结果：{result['rowCount']} 行，{result.get('groupCount', len(groups))} 组，{detail}，总计 {result['total']}。结果已写入 summary.csv，仅验证本次输入。"
            else:
                self.counters.add("chatCalls")
                answer = "这是浏览器验收的普通聊天回复：复利是本金与已获得收益一起继续产生收益。本次只是概念说明，没有运行任何应用工具。"
            for start in range(0, len(answer), 127):
                if stopped.is_set():
                    raise GatewayError("浏览器测试模型已取消。", 409, "cancelled")
                yield answer[start:start + 127]
        finally:
            on_response(None)


def fixture_app(data_dir, *, port=48993, frontend_dist=None):
    counters = Counters()
    app = create_app({
        "TESTING": True, "DESKTOP_PORT": port, "DATA_DIR": str(data_dir),
        "FRONTEND_DIST": str(frontend_dist or ROOT / "frontend" / "dist"),
        "GITEE_AI_API_KEY": "", "GITEE_AI_MODEL": BrowserFixtureProvider.model,
        "GITEE_AI_BASE_URL": "http://127.0.0.1:1/v1", "IMPORT_LEGACY_HISTORY": False,
        "AGENT_RUNTIME_DIR": str(ROOT / "data" / "agent-runtime"),
    })
    platform = app.extensions["agent_platform"]
    register_builtin_agents(platform)
    provider = BrowserFixtureProvider(counters)
    platform.planner = Planner(provider)
    platform.executor = ObservedWasiExecutor(counters, ROOT / "data" / "agent-runtime")
    workbench = app.extensions["workbench_service"]
    workbench.runtime.provider = provider
    workbench.model_id = provider.model

    @app.get("/__fixture__/stats")
    def stats():
        return jsonify(counters.snapshot())

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=48993)
    parser.add_argument("--frontend-dist", type=Path, default=ROOT / "frontend" / "dist")
    options = parser.parse_args()
    if not (options.frontend_dist / "index.html").is_file():
        parser.error("Build the frontend first: npm --prefix frontend run build")

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    with tempfile.TemporaryDirectory(prefix="cd80-browser-") as directory:
        app = fixture_app(directory, port=options.port, frontend_dist=options.frontend_dist)
        service = app.extensions["workbench_service"]
        server = None
        try:
            runtime = app.extensions["agent_platform"].executor.status()
            if not runtime["available"]:
                raise RuntimeError("Real WASI runtime is required: " + runtime.get("reason", "unavailable"))
            server = make_server("127.0.0.1", options.port, app, threaded=True)
            print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{options.port}",
                              "realWasi": True, "realModel": False, "temporaryData": True}), flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if server is not None:
                server.server_close()
            service.close()


if __name__ == "__main__":
    main()
