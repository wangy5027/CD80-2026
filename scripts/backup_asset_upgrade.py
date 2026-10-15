"""Create consistent per-database backups before the additive asset upgrade.

Reads the specified site DATA_DIR. Does not stop services, alter originals or
claim an atomic multi-database snapshot. Run while all execution is quiescent.
"""
import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATABASES = ('auth.sqlite3', 'graph-checkpoints.sqlite3', 'agent-platform.sqlite3',
             'sub-agent.sqlite3', 'evolution.sqlite3', 'capability-library/capabilities.sqlite3')


def backup(source_root, destination):
    source_root, destination = Path(source_root).resolve(), Path(destination).resolve()
    if not source_root.is_dir() or destination.exists():
        raise ValueError('Use an existing DATA_DIR and a fresh backup directory.')
    destination.mkdir(parents=True)
    report = {'createdAt': datetime.now(timezone.utc).isoformat(), 'sourceDataDirectory': str(source_root),
              'databaseBackups': [], 'originalsModified': False, 'crossDatabaseAtomic': False}
    for relative in DATABASES:
        source = source_root / relative
        if not source.is_file():
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader, closing(sqlite3.connect(target)) as writer:
            reader.backup(writer)
            check = writer.execute('PRAGMA integrity_check').fetchone()[0]
            if check != 'ok':
                raise RuntimeError('Backup integrity check failed: ' + relative)
        report['databaseBackups'].append({'path': relative, 'size': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'integrityCheck': check})
    # Exercise additive tables twice on a separate copy of the real old schema.
    platform_backup = destination / 'agent-platform.sqlite3'
    if platform_backup.exists():
        from backend.app.modules.agent_platform.repository import Repository
        from backend.app.modules.agent_platform.asset_store import AssetStore
        with tempfile.TemporaryDirectory(prefix='asset-migration-probe-') as probe:
            target = Path(probe) / 'agent-platform.sqlite3'
            shutil.copy2(platform_backup, target)
            def legacy_rows():
                with closing(sqlite3.connect(target)) as db:
                    names = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                             if not r[0].startswith(('asset_', 'sqlite_'))]
                    return {name: sorted(repr(row) for row in db.execute('SELECT * FROM "' + name.replace('"', '""') + '"'))
                            for name in names}
            before = legacy_rows()
            repository = Repository(probe)
            AssetStore(repository)
            AssetStore(repository)
            if before != legacy_rows():
                raise RuntimeError('The migration modified existing non-asset records.')
            report['migrationProbe'] = {'additiveTablesCreated': True, 'secondOpenSucceeded': True,
                                        'oldRecordsUnchanged': True, 'productionMigrationExecuted': False}
    (destination / 'manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--destination', required=True)
    args = parser.parse_args()
    report = backup(args.data_dir, args.destination)
    print(json.dumps({'databases': len(report['databaseBackups']), 'migrationProbe': report.get('migrationProbe'),
                      'manifest': str(Path(args.destination).resolve() / 'manifest.json')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
