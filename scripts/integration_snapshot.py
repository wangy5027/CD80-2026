"""Read-only source inventory; never imports or opens source databases."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('main-code', '应用1', '网站_v10121930')
SKIP = {'.git', '.venv', 'node_modules', '__pycache__', '.cache', '.pytest_cache'}


def inventory():
    result = {}
    for name in SOURCES:
        base = ROOT.parent / name
        files = {}
        for directory, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in SKIP)
            for filename in sorted(names):
                path = Path(directory) / filename
                if filename == '.DS_Store' or path.is_symlink():
                    continue
                rel = str(path.relative_to(base))
                st = path.stat()
                item = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
                # Runtime histories are checked by metadata; source and configuration by bytes.
                if 'data' not in path.relative_to(base).parts:
                    item['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                files[rel] = item
        env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
        revision = subprocess.run(['git', '-C', str(base), 'rev-parse', 'HEAD'], env=env, capture_output=True, text=True)
        result[name] = {'revision': revision.stdout.strip(), 'files': files}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['capture', 'verify'])
    args = parser.parse_args()
    target = ROOT / 'docs/integration/验收记录/source-baseline.json'
    current = inventory()
    if args.action == 'capture':
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('x', encoding='utf-8') as stream:
            json.dump(current, stream, ensure_ascii=False, indent=2)
        print({name: len(value['files']) for name, value in current.items()})
    else:
        baseline = json.loads(target.read_text())
        changed = []
        for name in SOURCES:
            old, new = baseline[name]['files'], current[name]['files']
            changed.extend(f'{name}/{p}' for p in old.keys() | new.keys() if old.get(p) != new.get(p))
        print(json.dumps({'changed': changed, 'source_unchanged': not changed}, ensure_ascii=False))
        raise SystemExit(bool(changed))
