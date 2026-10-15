"""Copy a CLOSED delivery database without touching its source directory.

For a live service use SQLite's online backup API instead. This command refuses
WAL/journal sidecars and checks source stability and content hashes.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import shutil


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_closed_database(source: str, output: str) -> dict:
    source_path = Path(source).absolute()
    output_path = Path(output).absolute()
    if source_path == output_path or output_path.exists():
        raise ValueError("Output must be a new file, separate from the source")
    if not source_path.is_file():
        raise ValueError("Source database does not exist")
    sidecars = [Path(str(source_path) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    if any(path.exists() for path in sidecars):
        raise ValueError("Live/recovery sidecars detected; use the online backup API")
    before = source_path.stat()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(str(output_path) + ".building")
    if staging.exists():
        raise ValueError("Staging file already exists; inspect it before retrying")
    shutil.copyfile(source_path, staging)
    source_digest, copy_digest = file_hash(source_path), file_hash(staging)
    after = source_path.stat()
    if source_digest != copy_digest or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or any(path.exists() for path in sidecars):
        raise RuntimeError("Source changed; staging is retained for inspection, not promoted")
    staging.replace(output_path)
    manifest = {"source": str(source_path), "snapshot": str(output_path), "bytes": after.st_size,
                "sha256": copy_digest, "source_mtime_ns": after.st_mtime_ns,
                "method": "closed_delivery_copy_with_stability_and_hash_checks",
                "source_modified": False}
    Path(str(output_path) + ".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--offline-source", action="store_true", required=True,
                        help="Assert the supplied delivery database is closed, not an active service")
    args = parser.parse_args()
    print(json.dumps(snapshot_closed_database(args.source, args.output), ensure_ascii=False, indent=2))
