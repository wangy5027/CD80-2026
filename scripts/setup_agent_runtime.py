"""Provision the project's pinned, verified WASI Python runtime (no host execution).

Run with the same virtualenv as the backend. Network is used only for this setup;
the application executor itself has no download or installation fallback.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import runpy
import tempfile
import urllib.request
import zipfile

VERSION = "3.15.0"
URL = "https://github.com/brettcannon/cpython-wasi-build/releases/download/v3.15.0/python-3.15.0-wasi_sdk-33.zip"
ARCHIVE_SHA256 = "00b791b3729230f97a625087f534d71978433fb1e068e13a9cac8cd3f2541742"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "agent-runtime")
    args = parser.parse_args()
    target = args.runtime_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="setup-", dir=target) as temporary:
        archive = Path(temporary) / "python.zip"
        request = urllib.request.Request(URL, headers={"User-Agent": "CD80-agent-runtime-setup"})
        with urllib.request.urlopen(request, timeout=60) as response, archive.open("wb") as output:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > 64 * 1024 * 1024:
                    raise RuntimeError("Runtime download exceeds expected maximum")
                output.write(chunk)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
            raise RuntimeError("Runtime archive SHA256 mismatch; refusing installation")
        with zipfile.ZipFile(archive) as source:
            for info in source.infolist():
                parts = PurePosixPath(info.filename).parts
                if not parts or any(p in ("..", "") for p in parts) or PurePosixPath(info.filename).is_absolute() or "\\" in info.filename:
                    raise RuntimeError("Invalid archive member")
                destination = target.joinpath(*parts)
                if not destination.resolve().is_relative_to(target):
                    raise RuntimeError("Archive member leaves runtime directory")
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(source.read(info))
    # A stored stdlib zip avoids platform-specific directory enumeration in the
    # WASI import finder, and needs no compression extension inside the guest.
    stdlib = target / "lib" / "python3.15"
    with zipfile.ZipFile(target / "lib" / "python315.zip", "w", compression=zipfile.ZIP_STORED) as library:
        for source in sorted(stdlib.rglob("*.py")):
            library.write(source, str(source.relative_to(stdlib)).replace("\\", "/"))
    files = {str(path.relative_to(target)).replace("\\", "/"): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in target.rglob("*") if path.is_file() and path.name not in {"manifest.json", "python.cwasm"}}
    import wasmtime
    config = wasmtime.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.cranelift_opt_level = "speed"
    engine = wasmtime.Engine(config)
    module = wasmtime.Module.from_file(engine, str(target / "python.wasm"))
    compiled = module.serialize()
    (target / "python.cwasm").write_bytes(compiled)
    manifest = {"pythonVersion": VERSION, "source": URL, "archiveSha256": ARCHIVE_SHA256, "files": files,
                "wasmtimeVersion": importlib.metadata.version("wasmtime"), "compiledSha256": hashlib.sha256(compiled).hexdigest()}
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    executor_type = runpy.run_path(str(Path(__file__).resolve().parents[1] / "backend" / "app" / "modules" / "agent_platform" / "executor.py"))["SandboxExecutor"]
    executor = executor_type({"AGENT_RUNTIME_DIR": str(target)})
    probe = executor.execute("def run(payload):\n return {'ready': True}", {}, {})
    if probe["result"] != {"ready": True}:
        raise RuntimeError("Installed runtime did not pass its isolated startup check")
    print(json.dumps({"runtimeDir": str(target), "files": len(files), "wasm": [p for p in files if p.endswith(".wasm")], "verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
