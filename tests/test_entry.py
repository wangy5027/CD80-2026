"""Standalone website entrypoint checks from outside the website directory."""

import os
import re
import selectors
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


SITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SITE_ROOT))
import main  # noqa: E402


class EntrypointTests(unittest.TestCase):
    def test_occupied_port_has_actionable_error(self):
        with socket.socket() as occupied, tempfile.TemporaryDirectory(prefix="ficagent-site-busy-") as root:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            process = subprocess.run(
                [sys.executable, str(SITE_ROOT / "main.py"), "--port", str(port),
                 "--coze-base", "http://127.0.0.1:1", "--data-dir", root],
                cwd=root, capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(process.returncode, 2)
            self.assertIn(f"端口 {port} 已被占用", process.stderr)
            self.assertIn("python main.py --port", process.stderr)
            self.assertNotIn("Traceback", process.stderr)

    def test_env_file_is_loaded_from_website_root(self):
        with tempfile.TemporaryDirectory(prefix="ficagent-site-env-") as root:
            site = Path(root)
            (site / ".env").write_text("FICAGENT_SITE_ENV_TEST=loaded-from-site\n", encoding="utf-8")
            with patch.object(main, "SITE_ROOT", site):
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("FICAGENT_SITE_ENV_TEST", None)
                    main.load_dependencies()
                    self.assertEqual(os.environ["FICAGENT_SITE_ENV_TEST"], "loaded-from-site")

    def test_process_runs_from_other_cwd_and_stops_cleanly(self):
        with tempfile.TemporaryDirectory(prefix="ficagent-site-process-") as root:
            temp = Path(root)
            dist = temp / "dist"
            dist.mkdir()
            (dist / "index.html").write_text("<!doctype html><title>isolated website</title>", encoding="utf-8")
            env = os.environ.copy()
            env.update({
                "PORT": "9999",  # --port must take precedence.
                "FRONTEND_DIST": str(dist),
                "DATA_DIR": str(temp / "data"),
                "COZE_BASE_URL": "http://127.0.0.1:1",  # Unavailable Coze must not prevent page access.
                "PYTHONUNBUFFERED": "1",
            })
            interpreter = "/usr/bin/python3" if Path("/usr/bin/python3").is_file() else sys.executable
            process = subprocess.Popen(
                [interpreter, str(SITE_ROOT / "main.py"), "--port", "0", "--data-dir", str(temp / "site-data")],
                cwd=temp,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = ""
            try:
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 15
                port = None
                while time.monotonic() < deadline and port is None:
                    for _key, _mask in selector.select(timeout=0.25):
                        chunk = os.read(process.stdout.fileno(), 65536)
                        if not chunk:
                            break
                        output += chunk.decode("utf-8", errors="replace")
                        match = re.search(r"127\.0\.0\.1:(\d+)/desktop/", output)
                        if match:
                            port = int(match.group(1))
                    if process.poll() is not None:
                        break
                self.assertIsNotNone(port, "Website did not start:\n" + output)
                self.assertNotEqual(port, 9999)
                self.assertIn("警告：", output)
                self.assertIn("Coze", output)
                base = f"http://127.0.0.1:{port}"
                health = requests.get(base + "/desktop/health", timeout=5)
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json()["backend"], "flask")
                home = requests.get(base + "/", timeout=5)
                self.assertEqual(home.status_code, 200)
                self.assertIn("isolated website", home.text)
                self.assertFalse((temp / "site-data").exists())
            finally:
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    remaining, _ = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    remaining, _ = process.communicate(timeout=5)
                    self.fail("Website did not stop on SIGTERM:\n" + output + remaining.decode("utf-8", errors="replace"))
                self.assertEqual(process.returncode, 0, output + remaining.decode("utf-8", errors="replace"))


if __name__ == "__main__":
    unittest.main()
