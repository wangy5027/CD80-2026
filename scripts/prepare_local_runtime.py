"""Verify existing portable WASI bytes and compile them for this machine."""
from pathlib import Path
import hashlib
import importlib.metadata
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import wasmtime
    from backend.app.modules.agent_platform.executor import SandboxExecutor, _PINNED_ARCHIVE
    target = ROOT / 'data/agent-runtime'
    manifest = json.loads((target / 'manifest.json').read_text())
    version = importlib.metadata.version('wasmtime')
    if version != '49.0.0' or manifest['archiveSha256'] != _PINNED_ARCHIVE:
        raise RuntimeError('Runtime provenance or Wasmtime version mismatch')
    for name, expected in manifest['files'].items():
        path = (target / name).resolve()
        if not path.is_relative_to(target.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'Runtime content mismatch: {name}')
    config = wasmtime.Config()
    config.consume_fuel = True
    config.epoch_interruption = True
    config.cranelift_opt_level = 'speed'
    compiled = wasmtime.Module.from_file(wasmtime.Engine(config), str(target / 'python.wasm')).serialize()
    (target / 'python.cwasm').write_bytes(compiled)
    manifest.update(wasmtimeVersion=version, compiledSha256=hashlib.sha256(compiled).hexdigest())
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    executor = SandboxExecutor({'AGENT_RUNTIME_DIR': str(target)})
    result = executor.execute("def run(payload):\n return {'ready': True}", {}, {})
    assert result['result'] == {'ready': True}
    print(json.dumps({'verified': True, 'runtime': executor.status()}))


if __name__ == '__main__':
    main()
