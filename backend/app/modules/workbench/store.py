"""Per-user JSON persistence for the workbench."""

from __future__ import annotations

import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Callable


class StateStore:
    """Store workbench state in the legacy users/<id>/state.json format."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self._cache: dict[str, dict[str, Any]] = {}
        self._locks: dict[str, threading.RLock] = {}
        self._locks_lock = threading.Lock()

    def _key(self, user_id: str | int) -> str:
        """Match the existing JavaScript user-directory naming convention."""
        return re.sub(r"[^a-zA-Z0-9_-]", "_", str(user_id))

    def directory(self, user_id: str | int) -> Path:
        """Return this user's legacy data directory without reading its contents."""
        return self.root / "users" / self._key(user_id)

    def _lock(self, key: str) -> threading.RLock:
        """Share one lock per physical user directory across request threads."""
        with self._locks_lock:
            if key not in self._locks:
                self._locks[key] = threading.RLock()
            return self._locks[key]

    def _load(self, key: str) -> dict[str, Any]:
        """Load legacy state once and recover tasks left running by a prior process."""
        if key in self._cache:
            return self._cache[key]
        directory = self.directory(key)
        (directory / "files").mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with (directory / "state.json").open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except FileNotFoundError:
            state = {"projects": [], "tasks": [], "schedules": [], "files": [], "bindings": {}}
        if not state.get("bindings"):
            state["bindings"] = {}
        recovered = False
        for task in state["tasks"]:
            if task.get("status") == "running":
                task["status"] = "stopped"
                task["error"] = "上次生成被中断，已保留保存过的内容，可以继续发送消息。"
                recovered = True
        if recovered:
            self._persist(key, state)
        self._cache[key] = state
        return state

    def get(self, user_id: str | int) -> dict[str, Any]:
        """Return an independent copy of one user's current state."""
        key = self._key(user_id)
        with self._lock(key):
            return copy.deepcopy(self._load(key))

    def update(self, user_id: str | int, change: Callable[[dict[str, Any]], Any]) -> Any:
        """Apply one mutation atomically and return its callback result."""
        key = self._key(user_id)
        with self._lock(key):
            next_state = copy.deepcopy(self._load(key))
            result = change(next_state)
            self._persist(key, next_state)
            self._cache[key] = next_state
            return result

    def _persist(self, key: str, state: dict[str, Any]) -> None:
        """Replace state.json through a same-directory temporary file."""
        directory = self.directory(key)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        target = directory / "state.json"
        temporary = directory / f"state.json.{uuid.uuid4()}.tmp"
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
