"""Exercise the real website entrypoint without real accounts or model calls."""

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import requests
import main as entrypoint


SITE_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def free_port():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return server.getsockname()[1]


class WebsiteEntrypointTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ficagent-entry-test-")
        self.root = Path(self.temporary.name)
        self.dist = self.root / "dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<!doctype html><title>standalone fixture</title>")
        self.data = self.root / "data"
        self.environment = os.environ.copy()
        self.environment.update(
            GITEE_AI_API_KEY="",
            IMPORT_LEGACY_HISTORY="0",
            FRONTEND_DIST=str(self.dist),
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONIOENCODING="utf-8",
        )
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV"):
            self.environment.pop(key, None)

    def tearDown(self):
        self.temporary.cleanup()

    def _command(self, port):
        return [str(PYTHON), str(SITE_ROOT / "main.py"), "--port", str(port), "--data-dir", str(self.data)]

    def test_prefers_platform_interpreter_without_reexecuting_own_environment(self):
        venv = self.root / ".venv"
        for platform, relative in [("win32", "Scripts/python.exe"), ("linux", "bin/python")]:
            interpreter = venv / relative
            interpreter.parent.mkdir(parents=True, exist_ok=True)
            interpreter.touch()
            with self.subTest(platform=platform), patch.object(entrypoint, "SITE_ROOT", self.root), \
                    patch.object(sys, "platform", platform), patch.object(sys, "argv", ["main.py", "--port", "8894"]), \
                    patch.object(sys, "prefix", str(self.root / "other-python")), patch.object(os, "execv") as execute:
                entrypoint.prefer_site_venv()
                execute.assert_called_once_with(str(interpreter), [str(interpreter), str(self.root / "main.py"), "--port", "8894"])
                execute.reset_mock()
                with patch.object(sys, "prefix", str(venv)):
                    entrypoint.prefer_site_venv()
                execute.assert_not_called()

    def test_real_entry_serves_vue_only_and_exits_cleanly(self):
        port = free_port()
        process = subprocess.Popen(
            self._command(port), cwd=self.root, env=self.environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                if process.poll() is not None:
                    self.fail(f"网站提前退出：{process.communicate()[0]}")
                try:
                    if requests.get(base + "/desktop/health", timeout=0.3).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                time.sleep(0.1)
            else:
                self.fail("网站未在 10 秒内启动")

            home = requests.get(base + "/", allow_redirects=False, timeout=2)
            self.assertEqual((home.status_code, home.headers["Location"]), (302, "/desktop/"))
            self.assertIn("standalone fixture", requests.get(base + "/desktop/", timeout=2).text)
            self.assertEqual(requests.get(base + "/desktop/api/runtime", timeout=2).status_code, 401)
            self.assertEqual(requests.get(base + "/desktop/unknown-page", timeout=2).status_code, 404)
            self.assertEqual(requests.get(base + "/api/unknown-path", timeout=2).status_code, 404)
        finally:
            if process.poll() is None:
                if os.name == "nt":
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    process.terminate()
            try:
                output, _ = process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                output, _ = process.communicate(timeout=5)
                self.fail(f"网站没有及时停止：{output}")
        self.assertEqual(process.returncode, 0, output)
        self.assertIn("LangGraph", output)
        self.assertIn("未配置", output)
        self.assertTrue((self.data / "auth.sqlite3").is_file())

    def test_occupied_port_reports_a_clear_error(self):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            port = blocker.getsockname()[1]
            completed = subprocess.run(
                self._command(port), cwd=self.root, env=self.environment,
                capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("端口", completed.stderr)
        self.assertIn("占用", completed.stderr)

    def test_help_lists_supported_arguments(self):
        completed = subprocess.run(
            [str(PYTHON), str(SITE_ROOT / "main.py"), "--help"],
            cwd=self.root, env=self.environment,
            capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--port", completed.stdout)
        self.assertIn("--data-dir", completed.stdout)


if __name__ == "__main__":
    unittest.main()
