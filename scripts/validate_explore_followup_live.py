"""Opt-in live replay of one selected follow-up in an isolated Explore database.

Reads only its source-report ancestry and associated research files. No account
credentials, ordinary chat history, or unrelated sessions are copied. Existing
production runs/reports are not changed. Real model/search requests consume quota.
"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    from backend.app.config import Config
    from backend.app.modules.explore.repository import ExploreRepository
    from backend.app.modules.explore.service import ExploreService
    from backend.app.modules.workbench.builtin_tools import build_tool_registry
    from backend.app.modules.workbench.skills import SkillRegistry
    from backend.app.modules.news.service import NewsService

    cfg = {k: getattr(Config, k) for k in dir(Config) if k.isupper()}
    source_db = Path(cfg['DATA_DIR']) / 'explore.sqlite3'
    original = sqlite3.connect(f'file:{source_db.resolve()}?mode=ro', uri=True)
    original.row_factory = sqlite3.Row
    failed_row = original.execute('SELECT * FROM runs WHERE id=?', (args.run_id,)).fetchone()
    if failed_row is None:
        raise ValueError('Requested run does not exist')
    failed = json.loads(failed_row['doc'])
    if not failed.get('source_report_id'):
        raise ValueError('Selected run is not a follow-up')
    owner, session_id = failed_row['owner'], failed_row['session_id']
    reports, runs, seen = [], [], set()
    report_id = failed['source_report_id']
    while report_id:
        if report_id in seen or len(seen) >= 4:
            raise ValueError('Unsupported ancestry')
        seen.add(report_id)
        report = original.execute('SELECT * FROM reports WHERE owner=? AND session_id=? AND id=?',
                                  (owner, session_id, report_id)).fetchone()
        if report is None:
            raise ValueError('Source report is no longer authorized')
        run = original.execute('SELECT * FROM runs WHERE owner=? AND id=?',
                               (owner, report['run_id'])).fetchone()
        if run['status'] in {'queued', 'running'}:
            raise ValueError('Cannot replay an active ancestor')
        reports.append(dict(report)); runs.append(dict(run))
        report_id = json.loads(report['doc']).get('source_report_id')
    run_ids = {r['id'] for r in runs}
    files = [dict(row) for row in original.execute(
        'SELECT * FROM explore_files WHERE owner=? AND session_id=?', (owner, session_id))
        if json.loads(row['metadata']).get('source_run_id') in run_ids]
    file_ids = {f['id'] for f in files}
    evidence = [dict(row) for row in original.execute(
        'SELECT * FROM explore_evidence WHERE owner=? AND session_id=?', (owner, session_id))
        if row['run_id'] in run_ids and row['file_id'] in file_ids]
    session = dict(original.execute('SELECT * FROM sessions WHERE owner=? AND id=?',
                                    (owner, session_id)).fetchone())
    original.close()
    args.output.mkdir(parents=True, exist_ok=True)
    replay_owner = 'isolated-followup-validation'
    with tempfile.TemporaryDirectory(prefix='explore-followup-live-') as temporary:
        repository = ExploreRepository(temporary)
        with repository.transaction() as db:
            for table, rows in [('sessions', [session]), ('runs', runs), ('reports', reports),
                                ('explore_files', files), ('explore_evidence', evidence)]:
                for row in rows:
                    row = {**row, 'owner': replay_owner}
                    columns = ','.join(row)
                    placeholders = ','.join('?' for _ in row)
                    db.execute(f'INSERT INTO {table} ({columns}) VALUES ({placeholders})', tuple(row.values()))
        cfg.update(DATA_DIR=temporary, IMPORT_LEGACY_HISTORY=False)
        workbench = SimpleNamespace(runtime=SimpleNamespace(
            tools=build_tool_registry(cfg), skills=SkillRegistry(cfg['SKILLS_DIR'])))
        service = ExploreService(workbench, NewsService(cfg), cfg)
        selection = failed['context_selection']
        body = {'module': failed['module'], 'question': failed.get('resolved_question') or failed['question'],
                'source_report_id': failed['source_report_id'], 'context_mode': selection['mode'],
                'file_ids': selection['file_ids'] if selection['mode'] == 'manual' else [],
                'idempotency_key': str(uuid4())}
        try:
            started = time.monotonic()
            run = service.create_run(replay_owner, session_id, body)
            if run['context_selection']['file_ids'] != selection['file_ids']:
                service.cancel(replay_owner, run['id'])
                raise AssertionError('Replay changed the original selected files')
            while time.monotonic() - started < service.timeout + 15:
                run = service.repository.get_run(replay_owner, run['id'])
                if run['status'] not in {'queued', 'running'}:
                    break
                time.sleep(.2)
            report = run.pop('report', None)
            (args.output / 'run.json').write_text(json.dumps(run, ensure_ascii=False, indent=2))
            if report:
                (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
                (args.output / 'report.md').write_text(report['markdown'])
            result = {'source_run_id': args.run_id, 'same_followup_question': True,
                      'same_selected_files': True, 'selected_file_count': len(selection['file_ids']),
                      'selection_mode': selection['mode'], 'database': 'isolated',
                      'ordinary_chat_history_used': False, 'status': run['status'], 'error': run.get('error'),
                      'answer_status': (report or {}).get('answer_status'),
                      'report_variant': (report or {}).get('report_variant'),
                      'completion_warning': (report or {}).get('completion_warning'),
                      'stop_reason': run.get('research_stop_reason'),
                      'public_dns_mode': service.public_dns_mode,
                      'timeout_seconds': service.timeout,
                      'write_reserve_seconds': service.research_limits['write_reserve_seconds'],
                      'authorized_public_urls': run.get('authorized_public_urls', []),
                      'model_retries': run.get('model_retries', []),
                      'elapsed_seconds': round(time.monotonic()-started, 2),
                      'tools': [{k: x.get(k) for k in ('name', 'status')} for x in run.get('retrieval', {}).get('tool_trace', [])]}
            (args.output / 'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if run['status'] in {'succeeded', 'partial'} else 1
        finally:
            service.close()


if __name__ == '__main__':
    raise SystemExit(main())
