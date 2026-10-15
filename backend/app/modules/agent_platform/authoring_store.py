"""Owner-scoped, recoverable authoring records in the platform database."""
import json

from ..capability_library.contracts import canonical, digest, fail, now


KINDS = ('sessions', 'messages', 'sources', 'candidates', 'authorizations', 'test_plans',
         'evidence', 'review_snapshots', 'attempts', 'budget_rounds', 'model_requests',
         'model_traces', 'repair_rounds', 'commits')


class AuthoringStore:
    def __init__(self, repository):
        self.transaction = repository.transaction
        with self.transaction() as db:
            for kind in KINDS:
                db.execute(f'CREATE TABLE IF NOT EXISTS authoring_{kind} '
                           '(owner TEXT NOT NULL,id TEXT NOT NULL,session TEXT NOT NULL,doc TEXT NOT NULL,PRIMARY KEY(owner,id))')
                db.execute(f'CREATE INDEX IF NOT EXISTS authoring_{kind}_session ON authoring_{kind}(owner,session)')
            db.execute('CREATE TABLE IF NOT EXISTS authoring_requests '
                       '(owner TEXT,scope TEXT,key TEXT,hash TEXT,result TEXT,PRIMARY KEY(owner,scope,key))')
            # Recovery never dispatches business work or resets the budget.
            for row in db.execute('SELECT owner,doc FROM authoring_sessions').fetchall():
                doc = json.loads(row['doc'])
                if doc['phase'] in {'preparing', 'checking', 'validating', 'repairing', 'committing'}:
                    doc.update(phase='committing' if doc['phase'] == 'committing' else 'interrupted', error={'code': 'PROCESS_RESTARTED',
                               'message': '服务重启，草稿和进度已保留；请核对后继续。'})
                    doc['revision'] += 1
                    self.put(db, 'sessions', row['owner'], doc)
            for kind in ('attempts', 'model_requests'):
                for row in db.execute(f'SELECT owner,doc FROM authoring_{kind}').fetchall():
                    doc = json.loads(row['doc'])
                    field = 'state' if kind == 'model_requests' else 'status'
                    if doc.get(field) in {'queued', 'running', 'reserved', 'sent'}:
                        doc[field] = 'unknown' if kind == 'model_requests' else 'interrupted'
                        self.put(db, kind, row['owner'], doc)
            for row in db.execute('SELECT owner,doc FROM authoring_budget_rounds').fetchall():
                doc = json.loads(row['doc'])
                if doc.pop('activeSince', None):
                    # An unclosed activity interval has unknown duration. A restart
                    # may not manufacture additional time; require explicit extension.
                    doc['consumed']['activeMilliseconds'] = doc['limits']['activeMilliseconds']
                    doc['recoveryUnknown'] = True
                    doc['status'] = 'paused'
                    self.put(db, 'budget_rounds', row['owner'], doc)

    @staticmethod
    def get(db, kind, owner, key, required=True):
        if kind not in KINDS:
            raise ValueError(kind)
        row = db.execute(f'SELECT doc FROM authoring_{kind} WHERE owner=? AND id=?', (owner, key)).fetchone()
        if row is None:
            if required:
                fail('NOT_FOUND', '创作记录不存在或无权访问。', 404)
            return None
        return json.loads(row['doc'])

    @staticmethod
    def put(db, kind, owner, doc):
        if kind not in KINDS:
            raise ValueError(kind)
        doc['updatedAt'] = now()
        db.execute(f'INSERT OR REPLACE INTO authoring_{kind} VALUES(?,?,?,?)',
                   (owner, doc['id'], doc.get('sessionId', doc['id']), canonical(doc)))

    def read(self, kind, owner, key, required=True):
        with self.transaction() as db:
            return self.get(db, kind, owner, key, required)

    def listing(self, kind, owner, session=None):
        if kind not in KINDS:
            raise ValueError(kind)
        with self.transaction() as db:
            args = [owner]
            query = f'SELECT doc FROM authoring_{kind} WHERE owner=?'
            if session is not None:
                query += ' AND session=?'
                args.append(session)
            return [json.loads(row['doc']) for row in db.execute(query, args)]

    @staticmethod
    def request(db, owner, scope, key, payload, action):
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            fail('IDEMPOTENCY_REQUIRED', '操作需要有效的请求标识。')
        fingerprint = digest(payload)
        old = db.execute('SELECT hash,result FROM authoring_requests WHERE owner=? AND scope=? AND key=?',
                         (owner, scope, key)).fetchone()
        if old:
            if old['hash'] != fingerprint:
                fail('IDEMPOTENCY_CONFLICT', '同一请求标识不能用于不同内容。', 409)
            return json.loads(old['result']), False
        result = action()
        db.execute('INSERT INTO authoring_requests VALUES(?,?,?,?,?)', (owner, scope, key, fingerprint, canonical(result)))
        return result, True
