"""Bounded subprocess execution of the archived, trusted strategy package."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from .contracts import TERMINAL, canonical, custom_runtime_digest, now

# This runner is application code, never user- or model-generated Python.
CHILD_SCRIPT = r'''
import ctypes, json, os, pathlib, signal, sys
# Linux uses parent-death signalling; Windows is protected by the parent job.
if sys.platform.startswith("linux"):
    ctypes.CDLL(None).prctl(1, signal.SIGTERM)
    if os.getppid() != int(sys.argv[4]):
        os._exit(74)
sys.path.insert(0, sys.argv[1])
from strategy_workshop.models import Bar, CorporateAction, Instrument, BacktestConfig, StrategySpec
from strategy_workshop.catalog import get_strategy
from strategy_workshop.engine import run_backtest
payload = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
s = payload["snapshot"]
bars = [Bar(**b) for b in s["bars"]]
if payload.get("custom"):
    # Only this trusted adapter executes in the host. Exported strategy source
    # is read solely for provenance hashing, never imported or evaluated here.
    from types import SimpleNamespace
    c = payload["custom"]
    spec = c["strategy"]
    def targets(values, parameters=None):
        if parameters or len(values) != len(bars) or values != bars:
            raise ValueError("Frozen custom input changed")
        return c["targets"]
    strategy = SimpleNamespace(SPEC=StrategySpec(spec["key"], spec["name"], spec["summary"], {},
        spec["warmup_bars"], (), spec["version"]), __file__=c["sourcePath"], build_targets=targets)
else:
    strategy = get_strategy(payload["strategyId"])
result = run_backtest(bars, strategy,
    BacktestConfig(**s["config"]), Instrument(**s["instrument"]),
    [CorporateAction(**a) for a in s["actions"]], s["provenance"], strict=payload["mode"] == "formal")
if payload["mode"] == "diagnostic":
    result["report_eligible"] = False
    result["report_mode"] = "diagnostic"
    result["diagnostic_notice"] = "工程诊断结果；数据完整性未通过正式收益报告门槛。"
pathlib.Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
'''


class ProcessJob:
    """Kill archived computation when the Windows server dies unexpectedly."""
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        import ctypes
        from ctypes import wintypes
        class BasicLimits(ctypes.Structure):
            _fields_ = [("processTime", ctypes.c_longlong), ("jobTime", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("minWorkingSet", ctypes.c_size_t),
                        ("maxWorkingSet", ctypes.c_size_t), ("activeProcesses", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IOCounters(ctypes.Structure):
            _fields_ = [(field, ctypes.c_ulonglong) for field in ("readOps", "writeOps", "otherOps", "readBytes", "writeBytes", "otherBytes")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", IOCounters), ("processMemory", ctypes.c_size_t),
                        ("jobMemory", ctypes.c_size_t), ("peakProcessMemory", ctypes.c_size_t), ("peakJobMemory", ctypes.c_size_t)]
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if self.handle:
            import ctypes
            if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class WorkerManager:
    def __init__(self, repository, root, timeout=180, sandbox_config=None):
        self.repository = repository
        self.root = Path(root) / "strategy-workshop-execution"
        self.timeout = timeout
        self.sandbox_config = dict(sandbox_config or {})
        self.lock = threading.RLock()
        self.jobs = {}
        self.closed = False

    def start(self, owner, run):
        with self.lock:
            if self.closed:
                self.repository.transition(owner, run["id"], "interrupted", error={"code": "service_closed", "message": "服务已停止。"})
                return
            cancel = threading.Event()
            thread = threading.Thread(target=self._execute, args=(owner, run, cancel), name="workshop-" + run["id"], daemon=True)
            self.jobs[run["id"]] = {"thread": thread, "cancel": cancel, "process": None, "owner": owner, "runId": run["id"]}
            thread.start()

    def cancel(self, run_id):
        with self.lock:
            job = self.jobs.get(run_id)
            if job:
                job["cancel"].set()
                return True
            return False

    @staticmethod
    def _stop(process):
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)

    def _execute(self, owner, run, cancel):
        process = None
        job = None
        started = time.monotonic()
        try:
            current = self.repository.transition(owner, run["id"], "validating", startedAt=now())
            if current["status"] == "cancelling" or cancel.is_set():
                self.repository.transition(owner, run["id"], "cancelled")
                return
            archived = self.repository.load_snapshot(owner, run["executionSnapshotId"])
            directory = self.root / run["id"]
            package_root = directory / "package"
            package = package_root / "strategy_workshop"
            package.mkdir(parents=True, exist_ok=False)
            actual_hash = hashlib.sha256()
            for relative, encoded in sorted(archived["package"]["files"].items()):
                parts = Path(relative).parts
                if Path(relative).is_absolute() or ".." in parts or not relative.endswith(".py"):
                    raise ValueError("Invalid trusted package archive")
                content = base64.b64decode(encoded, validate=True)
                actual_hash.update(relative.encode())
                actual_hash.update(content)
                destination = package / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
            if actual_hash.hexdigest() != archived["package"]["implementationHash"]:
                raise ValueError("Archived package hash mismatch")
            payload = {"snapshot": archived["market"], "strategyId": run["strategy"]["strategyId"], "mode": run["mode"]}
            custom = archived["package"].get("custom")
            if custom:
                from strategy_workshop.models import Bar
                from strategy_workshop.catalog import implementation_hash
                from ..agent_platform.executor import SandboxExecutor
                from .custom_rules import compile_strategy, plan_hash
                from .custom_sandbox import sandbox_targets
                raw = custom["sourceCode"].encode("utf-8")
                if (implementation_hash() != archived["package"]["implementationHash"]
                        or custom["runtimeHash"] != custom_runtime_digest()
                        or hashlib.sha256(raw).hexdigest() != custom["sourceHash"]
                        or compile_strategy(custom["rulePlan"]).encode("utf-8") != raw
                        or plan_hash(custom["rulePlan"]) != custom["planHash"]
                        or not custom["validation"].get("passed")
                        or custom["validation"].get("sourceHash") != custom["sourceHash"]):
                    raise ValueError("Frozen custom code or runtime evidence mismatch")
                remaining = self.timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("Execution deadline reached before custom signals")
                executor = SandboxExecutor({**self.sandbox_config, "AGENT_EXECUTION_TIMEOUT": min(15, remaining,
                                            float(self.sandbox_config.get("AGENT_EXECUTION_TIMEOUT", 15)))})
                signal_targets = sandbox_targets(custom["rulePlan"], [Bar(**value) for value in archived["market"]["bars"]],
                                                 executor=executor, stopped=cancel)
                if time.monotonic() - started > self.timeout:
                    raise TimeoutError("Execution deadline reached during custom signals")
                source_path = directory / "custom_strategy.py"
                source_path.write_bytes(raw)
                payload["custom"] = {"targets": signal_targets, "strategy": custom["strategy"], "sourcePath": str(source_path.absolute())}
            input_path, output_path = directory / "input.json", directory / "result.json"
            input_path.write_text(canonical(payload), encoding="utf-8")
            if cancel.is_set():
                self.repository.transition(owner, run["id"], "cancelled")
                return
            with (directory / "stderr.txt").open("wb") as errors:
                job = ProcessJob()
                process = subprocess.Popen([sys.executable, "-B", "-c", CHILD_SCRIPT, str(package_root.absolute()),
                                            str(input_path.absolute()), str(output_path.absolute()), str(os.getpid())],
                                           cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=errors, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                job.assign(process)
                with self.lock:
                    self.jobs[run["id"]]["process"] = process
                current = self.repository.transition(owner, run["id"], "running")
                while process.poll() is None:
                    if cancel.wait(.04) or current["status"] == "cancelling":
                        self._stop(process)
                        self.repository.transition(owner, run["id"], "cancelled")
                        return
                    if time.monotonic() - started > self.timeout:
                        self._stop(process)
                        self.repository.transition(owner, run["id"], "failed", error={"code": "execution_timeout", "message": "回测超过执行时限，计算已停止。"})
                        return
                process.wait()
            if cancel.is_set():
                self.repository.transition(owner, run["id"], "cancelled")
                return
            if process.returncode != 0 or not output_path.is_file():
                raise ValueError("Trusted strategy execution failed")
            result = json.loads(output_path.read_text(encoding="utf-8"))
            if run["mode"] == "diagnostic":
                result["report_eligible"] = False
            result["implementation_sha256"] = archived["package"]["implementationHash"]
            if custom:
                result["custom_strategy"] = {"customization_id": custom["id"], "rules_digest": custom["rulesDigest"],
                    "rule_plan_sha256": custom["planHash"], "source_sha256": custom["sourceHash"],
                    "runtime_sha256": custom["runtimeHash"], "skill_version": custom["skillVersion"],
                    "validation": custom["validation"], "rule_approval": custom["ruleApproval"],
                    "original_idea": custom.get("originalIdea"), "clarifications": custom.get("clarifications"),
                    "base_strategy": custom.get("baseStrategy")}
            self.repository.transition(owner, run["id"], "succeeded", result=result)
        except Exception as error:
            if process is not None and process.poll() is None:
                self._stop(process)
            if cancel.is_set():
                self.repository.transition(owner, run["id"], "cancelled")
            else:
                timed_out = isinstance(error, TimeoutError) or getattr(error, "code", "") in {"execution_timeout", "timeout"}
                self.repository.transition(owner, run["id"], "failed", error={"code": "execution_timeout" if timed_out else "execution_failed",
                    "message": "回测超过执行时限，计算已停止。" if timed_out else "回测执行失败，请检查数据和运行记录后重试。"})
        finally:
            if job:
                job.close()
            with self.lock:
                self.jobs.pop(run["id"], None)

    def close(self):
        with self.lock:
            self.closed = True
            jobs = list(self.jobs.values())
            for job in jobs:
                job["cancel"].set()
        for job in jobs:
            job["thread"].join(timeout=8)
            process = job["process"]
            if process is not None and process.poll() is None:
                self._stop(process)
            self.repository.transition(job["owner"], job["runId"], "interrupted",
                                       error={"code": "service_closed", "message": "服务停止中断了运行，请创建新回测。"})
