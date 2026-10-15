"""Rebuild file hashes and ZIP after editing a capability; never execute its code.

Usage: python pack_capability.py capabilities/decimal-total my-tool.zip
The output must be outside the source directory and must not already exist.
Review all included files before sharing. Import performs platform validation.
"""
import argparse
import hashlib
import json
import zipfile
from pathlib import Path


def build(source, destination):
    source, destination = Path(source).resolve(strict=True), Path(destination).resolve()
    if not source.is_dir() or destination.is_relative_to(source):
        raise ValueError('Choose a source directory and an output outside it.')
    manifest_path = source / 'capability.json'
    if manifest_path.is_symlink():
        raise ValueError('Symbolic links are not supported.')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    files = {}
    for item in sorted(source.rglob('*')):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('Symbolic links and junctions are not supported.')
        if not item.is_file():
            continue
        path = item.relative_to(source).as_posix()
        if path in {'capability.json', 'provenance.json'}:
            continue
        if item.stat().st_size > 1024 * 1024:
            raise ValueError('Each resource must be at most 1 MiB.')
        files[path] = item.read_bytes()
    if len(files) > 498 or sum(map(len, files.values())) > 48 * 1024 * 1024:
        raise ValueError('Package exceeds resource limits.')
    manifest['files'] = [{'path': path, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                         for path, data in sorted(files.items())]
    encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    files['capability.json'] = encode(manifest)
    files['provenance.json'] = encode({'source': manifest['source'], 'files': manifest['files'],
                                     'note': 'Author package; no execution or validation evidence.'})
    with destination.open('xb') as output:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path, data in sorted(files.items()):
                info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('output')
    args = parser.parse_args()
    print(build(args.source, args.output))
