"""Create a consistent SQLite backup, including committed WAL data."""
import argparse
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[1] / 'data/cd80.sqlite3')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.database.resolve(strict=True)
    target = args.output.resolve()
    if target.exists():
        parser.error('Backup destination already exists; choose a new filename.')
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.building')
    if temporary.exists():
        parser.error('Temporary backup exists; choose a new filename.')
    try:
        with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
            with sqlite3.connect(temporary) as dst:
                src.backup(dst, pages=2048, sleep=0.1)
                if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup integrity check failed')
        temporary.rename(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    print(f'Backup verified: {target}')


if __name__ == '__main__':
    main()

