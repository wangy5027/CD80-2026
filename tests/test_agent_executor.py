"""Real WASI isolation tests. They never substitute a fake executor.

Provision the runtime with scripts/setup_agent_runtime.py before running this
suite. Missing runtime is an actionable failure, not a silently skipped check.
"""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from backend.app.modules.agent_platform.executor import SandboxError, SandboxExecutor


class AgentExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = SandboxExecutor()
        state = cls.executor.status()
        if not state["available"]:
            raise RuntimeError("Real WASI runtime required: " + state["reason"] + "; run python scripts/setup_agent_runtime.py")

    def execute(self, code, payload=None, files=None, executor=None):
        try:
            return (executor or self.executor).execute(code, payload or {}, files or {})
        except SandboxError as exc:
            self.fail(f"Unexpected sandbox error {exc.code}: {exc}; logs: {exc.logs}")

    def test_echo_and_standard_library_csv_decimal(self):
        result = self.execute('''
import csv
from decimal import Decimal
def run(payload):
    totals = {}
    with open(payload['filePath'], newline='', encoding='utf-8') as source:
        rows = list(csv.DictReader(source))
    for row in rows:
        totals[row['category']] = totals.get(row['category'], Decimal(0)) + Decimal(row['amount'])
    with open('/output/summary.csv', 'w', newline='', encoding='utf-8') as output:
        writer = csv.writer(output, lineterminator='\\n')
        writer.writerow(['category', 'total_amount'])
        for key in sorted(totals):
            writer.writerow([key, str(totals[key])])
    print('completed')
    return {'rowCount': len(rows), 'total': str(sum(totals.values())), 'artifacts': ['summary.csv']}
''', {"filePath": "/input/sample"}, {"sample": b"category,amount\nA,10\nB,7\nA,5\n"})
        self.assertEqual(result["result"]["total"], "22")
        self.assertEqual(result["files"]["summary.csv"], b"category,total_amount\nA,15\nB,7\n")
        self.assertIn("completed", result["logs"])

    def test_pathlib_output_and_binary_output(self):
        result = self.execute('''
from pathlib import Path
def run(payload):
    Path('/output/readme.txt').write_text('hello', encoding='utf-8')
    with open('/output/data.bin', 'wb') as output:
        output.write(bytes([0, 1, 255]))
    return {'ok': True}
''')
        self.assertEqual(result["files"], {"readme.txt": b"hello", "data.bin": bytes([0, 1, 255])})

    def test_host_files_environment_network_process_and_writes_are_denied(self):
        with tempfile.TemporaryDirectory() as temporary:
            secret = Path(temporary) / "secret.txt"
            secret.write_text("host-private", encoding="utf-8")
            original = os.environ.get("CD80_TEST_SECRET")
            os.environ["CD80_TEST_SECRET"] = "do-not-expose"
            try:
                result = self.execute('''
import os
def run(payload):
    checks = {'env_hidden': os.environ.get('CD80_TEST_SECRET') is None}
    actions = {
       'absolute_denied': lambda: open(payload['hostSecret']).read(),
       'traversal_denied': lambda: open('/input/../../secret.txt').read(),
       'input_readonly': lambda: open('/input/given', 'w').write('changed'),
       'raw_output_denied': lambda: os.open('/output/raw.txt', os.O_CREAT | os.O_WRONLY, 0o600),
       'runtime_readonly': lambda: open('/lib/python3.15/os.py', 'w').write('changed'),
       'unlink_denied': lambda: os.unlink('/input/given'),
       'truncate_denied': lambda: os.truncate('/input/given', 0),
       'rename_denied': lambda: os.rename('/input/given', '/program/stolen'),
       'symlink_denied': lambda: os.symlink(payload['hostSecret'], '/input/link'),
       'hardlink_denied': lambda: os.link('/input/given', '/program/alias'),
       'socket_denied': lambda: __import__('socket').socket(),
       'process_denied': lambda: __import__('subprocess').run(['python', '-c', 'print(1)']),
    }
    for name, action in actions.items():
        try:
            action()
            checks[name] = False
        except Exception:
            checks[name] = True
    with open('/input/given') as source:
        checks['selected_input_visible'] = source.read() == 'authorized'
    return checks
''', {"hostSecret": str(secret)}, {"given": b"authorized"})
            finally:
                if original is None:
                    os.environ.pop("CD80_TEST_SECRET", None)
                else:
                    os.environ["CD80_TEST_SECRET"] = original
            self.assertTrue(all(result["result"].values()), result)
            self.assertEqual(secret.read_text(), "host-private")

    def test_loop_timeout_terminates_guest(self):
        executor = SandboxExecutor({"AGENT_EXECUTION_TIMEOUT": 1.5, "AGENT_FUEL": 20_000_000_000})
        start = time.monotonic()
        with self.assertRaises(SandboxError) as raised:
            executor.execute("def run(payload):\n while True: pass", {}, {})
        self.assertIn(raised.exception.code, {"timeout", "resource_limit"})
        self.assertLess(time.monotonic() - start, 6)

    def test_cancel_interrupts_sleep_not_just_python_loop(self):
        stopped = threading.Event()
        timer = threading.Timer(1.5, stopped.set)
        timer.start()
        start = time.monotonic()
        try:
            with self.assertRaises(SandboxError) as raised:
                self.executor.execute("import time\ndef run(payload):\n time.sleep(600)\n return {}", {}, {}, stopped)
            self.assertEqual(raised.exception.code, "cancelled")
            self.assertLess(time.monotonic() - start, 6)
        finally:
            timer.cancel()

    def test_log_flood_is_bounded(self):
        executor = SandboxExecutor({"AGENT_MAX_LOG_BYTES": 1024})
        with self.assertRaises(SandboxError) as raised:
            executor.execute("def run(payload):\n print('x' * 100000)\n return {}", {}, {})
        self.assertEqual(raised.exception.code, "output_limit")
        self.assertLessEqual(len(raised.exception.logs), 1024)

    def test_output_and_memory_limits(self):
        for code in [
            "def run(payload):\n return {'big': 'x' * 1000000}",
            "def run(payload):\n open('/output/large.txt', 'w').write('x' * 1000000)\n return {}",
            "def run(payload):\n x = bytearray(1024 * 1024 * 512)\n return {}",
        ]:
            with self.subTest(code=code), self.assertRaises(SandboxError):
                SandboxExecutor({"AGENT_MAX_OUTPUT_BYTES": 1024, "AGENT_MEMORY_MB": 64}).execute(code, {}, {})

    def test_missing_runtime_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            executor = SandboxExecutor({"AGENT_RUNTIME_DIR": temporary})
            self.assertFalse(executor.status()["available"])
            with self.assertRaises(SandboxError) as raised:
                executor.execute("raise RuntimeError('must not execute')", {}, {})
            self.assertEqual(raised.exception.code, "sandbox_unavailable")

    def test_invalid_inputs_rejected_before_guest(self):
        for name in ("../escape", "/absolute", "folder/file", "x\\file", "CON", "nul.txt", "LPT1.csv", "bad."):
            with self.subTest(name=name), self.assertRaises(SandboxError) as raised:
                self.executor.execute("def run(payload): return {}", {}, {name: b"data"})
            self.assertEqual(raised.exception.code, "invalid_input")

    def test_syntax_error_is_tool_failure_not_host_execution(self):
        with self.assertRaises(SandboxError) as raised:
            self.executor.execute("def invalid syntax", {}, {})
        self.assertEqual(raised.exception.code, "tool_failed")
        self.assertIn("SyntaxError", raised.exception.logs)

    def test_raw_stdio_protocol_corruption_is_rejected(self):
        with self.assertRaises(SandboxError) as raised:
            self.executor.execute("import os\ndef run(payload):\n os.write(1, b'corrupt protocol')\n return {}", {}, {})
        self.assertEqual(raised.exception.code, "invalid_output")

    def test_independent_runs_have_no_files_from_previous_run(self):
        self.execute("def run(payload):\n open('/output/only_first.txt', 'w').write('private')\n return {}")
        result = self.execute('''
def run(payload):
    try:
        open('/output/only_first.txt').read()
        return {'isolated': False}
    except FileNotFoundError:
        return {'isolated': True}
''')
        self.assertTrue(result["result"]["isolated"])

    def test_output_count_and_native_device_names_rejected(self):
        with self.assertRaises(SandboxError):
            SandboxExecutor({"AGENT_MAX_FILES": 1}).execute("def run(payload):\n open('/output/a', 'w').write('a')\n open('/output/b', 'w').write('b')\n return {}", {}, {})
        with self.assertRaises(SandboxError) as raised:
            self.executor.execute("def run(payload):\n open('/output/CON.txt', 'w').write('data')\n return {}", {}, {})
        self.assertEqual(raised.exception.code, "invalid_output")

    def test_worker_watchdog_survives_missing_parent_monitor(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            (work / "program").mkdir()
            (work / "input").mkdir()
            (work / "program" / "tool.py").write_text("import time\ndef run(payload):\n time.sleep(600)\n return {}", encoding="utf-8")
            (work / "program" / "payload.json").write_text("{}", encoding="utf-8")
            settings = {"runtime": str(self.executor.runtime_dir), "input": str(work / "input"), "program": str(work / "program"),
                        "result": str(work / "result.json"), "memoryMb": 128, "outputLimit": 4096,
                        "logLimit": 4096, "maxFiles": 4, "fuel": 20_000_000_000, "timeoutSeconds": 1.5}
            request = work / "request.json"
            request.write_text(json.dumps(settings), encoding="utf-8")
            worker = Path(__file__).resolve().parents[1] / "backend/app/modules/agent_platform/executor_worker.py"
            # Deliberately bypass SandboxExecutor's parent monitoring loop. The
            # trusted worker must enforce its own deadline while blocked in WASI.
            completed = subprocess.run([sys.executable, "-I", str(worker), str(request)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(completed.returncode, 124)


if __name__ == "__main__":
    unittest.main()
