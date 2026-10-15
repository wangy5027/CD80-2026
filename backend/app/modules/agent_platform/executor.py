"""Capability-isolated Python tools, powered by CPython/WASI + Wasmtime.

The subprocess is a lifecycle/timeout boundary; WebAssembly and read-only WASI
capabilities are the security boundary. No generated source executes in host Python.
No writable host directory, network, host environment, or process creation capability
is provided to a guest. /output files live in guest linear memory and are exported
through a bounded JSON protocol. Missing/unverified runtime always fails closed.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[4]
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,127}$")
_PINNED_ARCHIVE = "00b791b3729230f97a625087f534d71978433fb1e068e13a9cac8cd3f2541742"
_WINDOWS_DEVICES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _safe_name(name: Any) -> bool:
    return (isinstance(name, str) and bool(_SAFE_NAME.fullmatch(name)) and not name.endswith(".")
            and name.split(".")[0].upper() not in _WINDOWS_DEVICES)


class SandboxError(RuntimeError):
    def __init__(self, code: str, message: str, logs: str = ""):
        super().__init__(message)
        self.code = code
        self.logs = logs


class SandboxExecutor:
    def __init__(self, config: Mapping[str, Any] | None = None):
        settings = config or {}
        self.runtime_dir = Path(settings.get("AGENT_RUNTIME_DIR") or _ROOT / "data" / "agent-runtime").resolve()
        self.timeout = max(0.1, min(float(settings.get("AGENT_EXECUTION_TIMEOUT", 15)), 120))
        self.memory_mb = max(32, min(int(settings.get("AGENT_MEMORY_MB", 128)), 512))
        self.output_limit = max(1024, min(int(settings.get("AGENT_MAX_OUTPUT_BYTES", 4 * 1024 * 1024)), 16 * 1024 * 1024))
        self.log_limit = max(256, min(int(settings.get("AGENT_MAX_LOG_BYTES", 65536)), 1024 * 1024))
        self.input_limit = max(1024, min(int(settings.get("AGENT_MAX_INPUT_BYTES", 16 * 1024 * 1024)), 64 * 1024 * 1024))
        self.max_files = max(1, min(int(settings.get("AGENT_MAX_FILES", 32)), 128))
        self.fuel = max(1, min(int(settings.get("AGENT_FUEL", 20_000_000_000)), 200_000_000_000))

    def status(self) -> dict:
        try:
            version = importlib.metadata.version("wasmtime")
            if version != "49.0.0":
                raise ValueError("Requires pinned wasmtime==49.0.0")
            manifest = json.loads((self.runtime_dir / "manifest.json").read_text(encoding="utf-8"))
            if manifest.get("archiveSha256") != _PINNED_ARCHIVE or manifest.get("wasmtimeVersion") != version:
                raise ValueError("Runtime manifest does not match the pinned build")
            for name in ("python.wasm", "lib/python315.zip"):
                target = self.runtime_dir / name
                if hashlib.sha256(target.read_bytes()).hexdigest() != manifest["files"][name]:
                    raise ValueError("Runtime content checksum mismatch")
            if hashlib.sha256((self.runtime_dir / "python.cwasm").read_bytes()).hexdigest() != manifest["compiledSha256"]:
                raise ValueError("Compiled runtime checksum mismatch")
        except (OSError, ValueError, KeyError, importlib.metadata.PackageNotFoundError) as exc:
            return {"available": False, "runtime": "python-wasi", "reason": str(exc),
                    "setup": "python scripts/setup_agent_runtime.py"}
        return {"available": True, "runtime": "python-wasi", "pythonVersion": "3.15.0", "wasmtimeVersion": version,
                "network": False, "hostEnvironment": False, "inputReadOnly": True, "outputStorage": "guest_memory",
                "memoryMb": self.memory_mb, "timeoutSeconds": self.timeout, "maxOutputBytes": self.output_limit}

    def execute(self, code: str, payload: dict, files: dict[str, bytes], stopped: threading.Event | None = None, *, package_files=None, package_entry=None) -> dict:
        stopped = stopped or threading.Event()
        if stopped.is_set():
            raise SandboxError("cancelled", "Execution cancelled")
        availability = self.status()
        if not availability["available"]:
            raise SandboxError("sandbox_unavailable", "Isolated runtime unavailable: " + availability["reason"])
        if not isinstance(code, str) or len(code.encode("utf-8")) > 131072:
            raise SandboxError("invalid_code", "Tool code is absent or exceeds 128 KiB")
        if not isinstance(payload, dict) or not isinstance(files, dict):
            raise SandboxError("invalid_input", "Payload and input files must be objects")
        if len(files) > self.max_files or any(not _safe_name(name) for name in files):
            raise SandboxError("invalid_input", "Input file names or count are invalid")
        if any(not isinstance(content, bytes) for content in files.values()) or sum(len(content) for content in files.values()) > self.input_limit:
            raise SandboxError("input_limit", "Input file bytes exceed the allowed limit")
        try:
            encoded_payload = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise SandboxError("invalid_input", "Payload must be valid JSON") from exc
        if len(encoded_payload.encode("utf-8")) > self.input_limit:
            raise SandboxError("input_limit", "JSON input exceeds the allowed limit")
        with tempfile.TemporaryDirectory(prefix="cd80-wasi-") as temporary:
            work = Path(temporary)
            inputs = work / "input"
            program = work / "program"
            inputs.mkdir()
            program.mkdir()
            for name, content in files.items():
                (inputs / name).write_bytes(content)
            (program / "tool.py").write_text(code, encoding="utf-8")
            (program / "payload.json").write_text(encoded_payload, encoding="utf-8")
            if package_files is not None:
                from ..capability_library.packages import safe_path
                if len(package_files)>500 or sum(len(v) for v in package_files.values())>50*1024*1024:
                    raise SandboxError("input_limit", "Package exceeds sandbox limit")
                package = work / "package"
                package.mkdir()
                for name, content in package_files.items():
                    target = package / safe_path(name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                if package_entry not in package_files:
                    raise SandboxError("invalid_input", "Missing package entry")
                (program / "entry.json").write_text(json.dumps(safe_path(package_entry)), encoding="utf-8")
            settings = {"runtime": str(self.runtime_dir), "input": str(inputs), "program": str(program),
                        "result": str(work / "result.json"), "memoryMb": self.memory_mb,
                        "outputLimit": self.output_limit, "logLimit": self.log_limit,
                        "maxFiles": self.max_files, "fuel": self.fuel, "timeoutSeconds": self.timeout}
            if package_files is not None:
                settings["package"] = str(package)
            request = work / "request.json"
            request.write_text(json.dumps(settings), encoding="utf-8")
            # A new trusted WASI host is killed on timeout/cancel. WASI has no fork,
            # exec, socket capability, or writable host mounts, hence no child tree.
            worker = Path(__file__).with_name("executor_worker.py")
            clean_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PROCESSOR_ARCHITECTURE", "PROCESSOR_ARCHITEW6432") if key in os.environ}
            with (work / "worker.log").open("wb") as log:
                process = subprocess.Popen([sys.executable, "-I", str(worker), str(request)],
                                           cwd=str(work), env=clean_env, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=log,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                deadline = time.monotonic() + self.timeout
                try:
                    while process.poll() is None:
                        if stopped.wait(0.02):
                            raise SandboxError("cancelled", "Execution cancelled")
                        if time.monotonic() > deadline:
                            raise SandboxError("timeout", "Tool exceeded its execution time limit")
                    if stopped.is_set():
                        raise SandboxError("cancelled", "Execution cancelled")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
            response_path = work / "result.json"
            protocol_limit = self.output_limit * 2 + self.log_limit + 131072
            if process.returncode == 124:
                raise SandboxError("timeout", "Tool exceeded its execution time limit")
            if not response_path.is_file() or response_path.stat().st_size > protocol_limit:
                raise SandboxError("sandbox_failed", "Isolated worker stopped without a bounded result")
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
                logs = str(response.get("logs", ""))[:self.log_limit]
                if not response.get("ok"):
                    raise SandboxError(response.get("code", "tool_failed"), str(response.get("message", "Tool failed"))[:2048], logs)
                exported = response.get("files", {})
                if not isinstance(exported, dict) or len(exported) > self.max_files:
                    raise ValueError("Invalid output file collection")
                result_files = {}
                for name, content in exported.items():
                    if not _safe_name(name):
                        raise ValueError("Invalid output name")
                    result_files[name] = base64.b64decode(content, validate=True)
                if sum(len(content) for content in result_files.values()) > self.output_limit:
                    raise SandboxError("output_limit", "Output files exceed the allowed limit", logs)
                result = response["result"]
                if len(json.dumps(result, allow_nan=False).encode("utf-8")) > self.output_limit:
                    raise SandboxError("output_limit", "JSON result exceeds the allowed limit", logs)
                return {"result": result, "files": result_files, "logs": logs}
            except (ValueError, KeyError, TypeError) as exc:
                raise SandboxError("invalid_output", "Tool did not return valid bounded JSON/files") from exc
