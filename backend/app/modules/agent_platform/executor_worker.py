"""Trusted one-shot host for the WASI sandbox, invoked only by executor.py."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import threading


def run(settings):
    import wasmtime

    runtime = Path(settings["runtime"])
    manifest = json.loads((runtime / "manifest.json").read_text(encoding="utf-8"))
    compiled = (runtime / "python.cwasm").read_bytes()
    if hashlib.sha256(compiled).hexdigest() != manifest["compiledSha256"]:
        return {"ok": False, "code": "sandbox_unavailable", "message": "Compiled runtime checksum mismatch"}
    # Never deserialize a file produced by a guest. This cache is installed by
    # setup from checksum-verified CPython, and is never guest-writable/mounted.
    config = wasmtime.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.cranelift_opt_level = "speed"
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.deserialize(engine, compiled)
    store = wasmtime.Store(engine)
    store.set_limits(memory_size=settings["memoryMb"] * 1024 * 1024, instances=1, tables=4, memories=1, table_elements=30000)
    store.set_fuel(settings["fuel"])
    store.set_epoch_deadline(1)
    wasi = wasmtime.WasiConfig()
    wasi.argv = ["python", "-I", "-B", "/program/bootstrap.py", str(settings["outputLimit"]), str(settings["maxFiles"])]
    # -I ignores PYTHONPATH/PYTHONHOME; stdlib is mounted at the compiled prefix.
    wasi.env = []
    program = Path(settings["program"])
    (program / "bootstrap.py").write_bytes(Path(__file__).with_name("executor_guest.py").read_bytes())
    # Zipimport opens the stdlib directly; guest root is never host-mounted.
    # Only standard-library sources, explicit inputs and program text are visible.
    wasi.preopen_dir(str(runtime / "lib"), "/lib", fs_mutable=False)
    wasi.preopen_dir(str(program), "/program", fs_mutable=False)
    wasi.preopen_dir(settings["input"], "/input", fs_mutable=False)
    stdout = bytearray()
    stderr = bytearray()
    exceeded = []

    def capture(buffer, limit, label):
        def write(data):
            remaining = max(0, limit - len(buffer))
            buffer.extend(data[:remaining])
            if len(data) > remaining:
                exceeded.append(label)
                engine.increment_epoch()
                return -27  # EFBIG; an ignored guest exception still hits epoch.
            return len(data)
        return write

    wasi.stdout_custom = capture(stdout, settings["outputLimit"] * 2 + 65536, "output")
    wasi.stderr_custom = capture(stderr, settings["logLimit"], "logs")
    store.set_wasi(wasi)
    linker = wasmtime.Linker(engine)
    linker.define_wasi()
    failure = None
    try:
        instance = linker.instantiate(store, module)
        instance.exports(store)["_start"](store)
    except wasmtime.ExitTrap as exc:
        if exc.code != 0:
            failure = ("tool_failed", "Python tool exited with status " + str(exc.code))
    except (wasmtime.Trap, wasmtime.WasmtimeError) as exc:
        message = str(exc)
        code = "resource_limit" if "fuel" in message or "memory" in message or "interrupt" in message else "tool_failed"
        failure = (code, message[:2048])
    logs = bytes(stderr).decode("utf-8", errors="replace")
    if exceeded:
        return {"ok": False, "code": "output_limit", "message": "Tool exceeded its " + exceeded[0] + " limit", "logs": logs}
    if failure:
        return {"ok": False, "code": failure[0], "message": failure[1], "logs": logs}
    try:
        response = json.loads(bytes(stdout))
        return {"ok": True, "result": response["result"], "files": response["files"], "logs": logs}
    except (ValueError, KeyError, TypeError):
        return {"ok": False, "code": "invalid_output", "message": "Tool did not produce the JSON result protocol", "logs": logs}


def main():
    settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    # Independent of the web server's watchdog: if that process exits during a
    # WASI sleep/poll, this trusted host still exits within the approved budget.
    watchdog = threading.Timer(float(settings["timeoutSeconds"]), lambda: os._exit(124))
    watchdog.daemon = True
    watchdog.start()
    try:
        try:
            result = run(settings)
        except Exception as exc:
            result = {"ok": False, "code": "sandbox_failed", "message": str(exc)[:2048]}
        Path(settings["result"]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    finally:
        watchdog.cancel()


if __name__ == "__main__":
    main()
