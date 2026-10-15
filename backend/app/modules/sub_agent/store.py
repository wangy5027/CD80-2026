"""Owner-scoped jobs, artifacts and ordered events in one SQLite transaction."""
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..workbench.errors import GatewayError

TERMINAL = {'succeeded', 'partial', 'failed', 'cancelled', 'interrupted', 'withdrawn'}


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def uid():
    return uuid4().hex


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


class Store:
    def __init__(self, root):
        self.path = Path(root) / 'sub-agent.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS records(owner TEXT, kind TEXT, id TEXT, body TEXT,
                PRIMARY KEY(owner,kind,id));
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner TEXT, run TEXT, body TEXT);
            CREATE INDEX IF NOT EXISTS event_run ON events(owner,run,id);
            CREATE TABLE IF NOT EXISTS artifacts(owner TEXT, id TEXT, run TEXT, body TEXT, data BLOB,
                PRIMARY KEY(owner,id));
            PRAGMA user_version=1;
            ''')
        with self.transaction() as db:
            for row in db.execute("SELECT owner,body FROM records WHERE kind='run'").fetchall():
                run = json.loads(row['body'])
                if run['status'] not in TERMINAL and run['status'] != 'queued':
                    run.update(status='interrupted', error='服务重启，任务未自动重跑。')
                    for step in run.get('steps', []):
                        if step.get('status') in {'running', 'awaiting_approval'}:
                            step.update(status='interrupted', completedAt=now())
                        record = self.get(db, row['owner'], 'execution_evidence', step['id'])
                        if record and record['status'] == 'running':
                            record.update(status='interrupted', completedAt=now())
                            self.put(db, row['owner'], 'execution_evidence', step['id'], record)
                    for call in run.get('modelCalls', []):
                        if call.get('status') == 'running':
                            interrupted_at = now()
                            call.update(status='interrupted', interruptedAt=interrupted_at,
                                        errorCode='PROCESS_INTERRUPTED', error='服务重启，本次模型请求未完成；仅保留已保存记录。')
                            trace = self.get(db, row['owner'], 'model_trace', call['id'])
                            if trace:
                                trace.update(status='interrupted', interruptedAt=interrupted_at,
                                    error={'code': 'PROCESS_INTERRUPTED', 'message': call['error']})
                                self.put(db, row['owner'], 'model_trace', call['id'], trace)
                    self.save_run(db, row['owner'], run, 'run.completed')
                if run['status'] not in TERMINAL or run['status'] == 'interrupted':
                    queue = self.get(db, row['owner'], 'queue', run['conversationId'])
                    if queue:
                        queue.update(paused=True, activeRunId=None, revision=queue['revision'] + 1)
                        self.put(db, row['owner'], 'queue', queue['id'], queue)

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA journal_mode=WAL')
                db.execute('BEGIN IMMEDIATE')
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    @staticmethod
    def get(db, owner, kind, key, required=False):
        row = db.execute('SELECT body FROM records WHERE owner=? AND kind=? AND id=?', (owner, kind, key)).fetchone()
        if not row and required:
            raise GatewayError('资源不存在或无权访问。', 404)
        return json.loads(row[0]) if row else None

    @staticmethod
    def put(db, owner, kind, key, value):
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)', (owner, kind, key, canonical(value)))

    @staticmethod
    def event(db, owner, run, kind, payload=None):
        item = {'runId': run['id'], 'conversationId': run['conversationId'], 'type': kind,
                'revision': run['revision'], 'at': now(), 'payload': payload or {}}
        db.execute('INSERT INTO events(owner,run,body) VALUES(?,?,?)', (owner, run['id'], canonical(item)))

    def save_run(self, db, owner, run, event='run.phase_changed', payload=None):
        run['revision'] += 1
        run['updatedAt'] = now()
        self.put(db, owner, 'run', run['id'], run)
        self.event(db, owner, run, event, payload or {'status': run['status']})

    def run(self, owner, key):
        with self.transaction() as db:
            return self.get(db, owner, 'run', key, True)

    def events(self, owner, key, after=0):
        with self.transaction() as db:
            self.get(db, owner, 'run', key, True)
            return [{'eventId': row['id'], **json.loads(row['body'])} for row in db.execute(
                'SELECT id,body FROM events WHERE owner=? AND run=? AND id>? ORDER BY id LIMIT 500', (owner, key, after))]

    def model_trace(self, owner, run_id, call_id):
        with self.transaction() as db:
            run = self.get(db, owner, 'run', run_id, True)
            if not any(call.get('id') == call_id for call in run.get('modelCalls', [])):
                raise GatewayError('请求记录不存在或无权访问。', 404)
            record = self.get(db, owner, 'model_trace', call_id, True)
            if record.get('runId') != run_id:
                raise GatewayError('请求记录不存在或无权访问。', 404)
            return record

    def execution_evidence(self, owner, run_id, step_id):
        with self.transaction() as db:
            run = self.get(db, owner, 'run', run_id, True)
            if not any(step['id'] == step_id for step in run['steps']):
                raise GatewayError('执行依据不属于此任务。', 404)
            record = self.get(db, owner, 'execution_evidence', step_id, True)
            if record['runId'] != run_id:
                raise GatewayError('执行依据不属于此任务。', 404)
            return record

    def artifact(self, owner, key):
        with self.transaction() as db:
            row = db.execute('SELECT body,data FROM artifacts WHERE owner=? AND id=?', (owner, key)).fetchone()
            if not row:
                raise GatewayError('产物不存在或无权访问。', 404)
            return json.loads(row['body']), bytes(row['data'])

    def skill_snapshot(self, owner, run_id):
        with self.transaction() as db:
            run = self.get(db, owner, 'run', run_id, True)
            key = run.get('componentSnapshotId')
            record = self.get(db, owner, 'skill_snapshot', key, True)
            if record['runId'] != run_id or record['snapshot']['snapshotDigest'] != run.get('componentSnapshotDigest'):
                raise GatewayError('Skill原件不属于此运行或摘要不符。', 409)
            return record['snapshot']
