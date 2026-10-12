"""Atomic local state persistence and account isolation."""

import concurrent.futures
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.modules.workbench.store import StateStore


class StateStoreTests(unittest.TestCase):
    def test_legacy_file_recovery_and_copy_isolation(self):
        """Existing JSON loads at its old path and a stale run becomes stopped."""
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "users" / "123"
            directory.mkdir(parents=True)
            original = {"projects": [], "tasks": [{"id": "run", "status": "running", "messages": [], "botId": "old-bot", "conversationId": "old-session"}], "schedules": [], "files": []}
            (directory / "state.json").write_text(json.dumps(original), encoding="utf-8")
            store = StateStore(root)
            state = store.get("123")
            self.assertEqual(state["tasks"][0]["status"], "stopped")
            self.assertNotIn("botId", state["tasks"][0])
            self.assertNotIn("conversationId", state["tasks"][0])
            self.assertEqual(json.loads((directory / "state.json").read_text())["tasks"][0]["status"], "stopped")
            self.assertEqual(state["bindings"], {})
            self.assertTrue((directory / "files").is_dir())
            state["tasks"].clear()
            self.assertEqual(len(store.get("123")["tasks"]), 1)
            self.assertEqual(store.get("other")["tasks"], [])

    def test_concurrent_updates_and_failed_write(self):
        """Concurrent callbacks retain every update and a failed rename keeps prior data."""
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(root)

            def add_project(index):
                return store.update("123", lambda state: state["projects"].append(index))

            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(add_project, range(40)))
            self.assertEqual(sorted(store.get("123")["projects"]), list(range(40)))
            with patch("backend.app.modules.workbench.store.os.replace", side_effect=OSError("disk failure")):
                with self.assertRaises(OSError):
                    add_project(41)
            self.assertEqual(len(store.get("123")["projects"]), 40)
            self.assertEqual(len(list(store.directory("123").glob("*.tmp"))), 0)
            reloaded = StateStore(root)
            self.assertEqual(len(reloaded.get("123")["projects"]), 40)


if __name__ == "__main__":
    unittest.main()
