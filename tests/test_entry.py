"""Exercise the real website entrypoint without real accounts or model calls."""

import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

import requests


SITE_ROOT = Path(__file__).resolve().parents[1]
PYTHON = SITE_ROOT / ".venv" / "bin" / "python"


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
        )
        for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "VIRTUAL_ENV"):
            self.environment.pop(key, None)

    def tearDown(self):
        self.temporary.cleanup()

    def _command(self, port):
        return [str(PYTHON), str(SITE_ROOT / "main.py"), "--port", str(port), "--data-dir", str(self.data)]

    def test_real_entry_serves_vue_only_and_exits_cleanly(self):
        port = free_port()
        process = subprocess.Popen(
            self._command(port), cwd=self.root, env=self.environment,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
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
            process.terminate()
            output, _ = process.communicate(timeout=8)
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
                capture_output=True, text=True, timeout=10, check=False,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("端口", completed.stderr)
        self.assertIn("占用", completed.stderr)

    def test_help_lists_supported_arguments(self):
        completed = subprocess.run(
            [str(PYTHON), str(SITE_ROOT / "main.py"), "--help"],
            cwd=self.root, env=self.environment,
            capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--port", completed.stdout)
        self.assertIn("--data-dir", completed.stdout)


if __name__ == "__main__":
    unittest.main()
