"""Offline publication gate; exits nonzero for missing/stale/failed examples."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.modules.capability_library.packages import inspect, parse_package, zip_bytes
from backend.app.modules.capability_library.releases import check_release


def main():
    count = 0
    for folder in sorted((ROOT/'docs/capability-library/examples').iterdir()):
        if not folder.is_dir(): continue
        manifest = json.loads((folder/'capability.json').read_text(encoding='utf-8'))
        if manifest['kind'] not in {'skill', 'tool'}: continue
        raw = zip_bytes({p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()})
        manifest, files = parse_package(raw, folder.name+'.zip')
        if inspect(manifest, files): raise RuntimeError(f'{folder.name}: package checks failed')
        check_release(manifest)
        count += 1
    catalog = ROOT/'capabilities/public-catalog.json'
    if catalog.exists():
        for item in json.loads(catalog.read_text(encoding='utf-8'))['packages']:
            package=ROOT/'capabilities'/item['package']
            manifest,files=parse_package(package.read_bytes(),package.name)
            if inspect(manifest,files): raise RuntimeError(package.name+': package checks failed')
            evidence=json.loads((ROOT/'capabilities'/item['evidence']).read_text(encoding='utf-8'))
            check_release(manifest,evidence)
            count+=1
    print(f'Official publication gate passed: {count} capabilities')


if __name__ == '__main__': main()
