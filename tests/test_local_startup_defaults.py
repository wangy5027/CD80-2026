"""The local launcher must use the canonical port and explain conflicts safely."""

import errno
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import main


class LocalStartupDefaultsTests(unittest.TestCase):
    def test_config_defaults_to_canonical_port_without_port_environment(self):
        environment = os.environ.copy()
        environment.pop("PORT", None)
        environment.pop("DESKTOP_PORT", None)
        result = subprocess.run(
            [sys.executable, "-c", "from backend.app.config import Config; print(Config.DESKTOP_PORT)"],
            cwd=Path(main.__file__).resolve().parent,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout.strip(), "8894")

    def test_default_conflict_reports_existing_service_without_port_fallback(self):
        service = Mock()
        app = SimpleNamespace(
            config={"DESKTOP_PORT": 8894, "MAX_CONTENT_LENGTH": 1024},
            extensions={"workbench_service": service},
        )
        create_app = Mock(return_value=app)
        create_server = Mock(side_effect=OSError(errno.EADDRINUSE, "address in use"))
        error_output = io.StringIO()
        with patch.object(main, "prefer_site_venv"), \
             patch.object(main, "load_dependencies", return_value=(create_server, create_app)), \
             patch.object(sys, "argv", ["main.py"]), \
             patch.object(sys, "stderr", error_output):
            with self.assertRaises(SystemExit) as raised:
                main.main()

        self.assertEqual(raised.exception.code, 2)
        create_app.assert_called_once_with({})
        self.assertEqual(create_server.call_args.kwargs["port"], 8894)
        service.close.assert_called_once()
        message = error_output.getvalue()
        self.assertIn("端口 8894", message)
        self.assertIn("scripts/manage_local.py", message)
        self.assertIn("status", message)
        self.assertNotIn("--port 8893", message)

    def test_explicit_qa_port_and_data_directory_remain_available(self):
        service = Mock()
        service.model_status.return_value = {"provider": "fixture", "model": "fixture", "configured": False}
        app = SimpleNamespace(
            config={"DESKTOP_PORT": 8895, "MAX_CONTENT_LENGTH": 1024},
            extensions={"workbench_service": service},
        )
        create_app = Mock(return_value=app)
        server = Mock(effective_port=8895)
        create_server = Mock(return_value=server)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(main, "prefer_site_venv"), \
             patch.object(main, "load_dependencies", return_value=(create_server, create_app)), \
             patch.object(main.signal, "signal"), \
             patch.object(sys, "argv", ["main.py", "--port", "8895", "--data-dir", directory]), \
             patch.object(sys, "stdout", io.StringIO()):
            main.main()

        create_app.assert_called_once_with({"DESKTOP_PORT": 8895, "DATA_DIR": str(Path(directory).resolve())})
        self.assertEqual(create_server.call_args.kwargs["port"], 8895)
        server.run.assert_called_once()
        server.close.assert_called_once()
        service.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
