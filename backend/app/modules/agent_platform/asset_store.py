"""Atomic App definitions, executions and output bytes in platform SQLite."""
import json
from contextlib import nullcontext

from ..capability_library.contracts import canonical, digest, fail, now


class AssetStore:
    def __init__(self, repository):
        self.repository = repository
        self.transaction = repository.transaction
        with self.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS asset_apps(owner TEXT,id TEXT,doc TEXT,PRIMARY KEY(owner,id));
                CREATE TABLE IF NOT EXISTS asset_versions(owner TEXT,id TEXT,app TEXT,doc TEXT,PRIMARY KEY(owner,id));
                CREATE TABLE IF NOT EXISTS asset_runs(owner TEXT,id TEXT,app TEXT,doc TEXT,PRIMARY KEY(owner,id));
                CREATE TABLE IF NOT EXISTS asset_artifacts(owner TEXT,id TEXT,run TEXT,doc TEXT,data BLOB,PRIMARY KEY(owner,id));
                CREATE TABLE IF NOT EXISTS asset_requests(owner TEXT,scope TEXT,key TEXT,hash TEXT,result TEXT,PRIMARY KEY(owner,scope,key));
                CREATE TABLE IF NOT EXISTS asset_creator_traces(
                    owner TEXT NOT NULL,id TEXT NOT NULL,app TEXT NOT NULL,version TEXT NOT NULL,
                    doc TEXT NOT NULL,PRIMARY KEY(owner,id));
                CREATE TABLE IF NOT EXISTS asset_community_releases(
                    id TEXT PRIMARY KEY,publisher TEXT NOT NULL,source_app TEXT NOT NULL,
                    source_version TEXT NOT NULL,status TEXT NOT NULL,doc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS asset_community_copies(
                    owner TEXT NOT NULL,release_id TEXT NOT NULL,app_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,PRIMARY KEY(owner,release_id));
                CREATE INDEX IF NOT EXISTS asset_runs_app ON asset_runs(owner,app);
                CREATE INDEX IF NOT EXISTS asset_versions_app ON asset_versions(owner,app);
                CREATE INDEX IF NOT EXISTS asset_community_public ON asset_community_releases(status,id);
                CREATE INDEX IF NOT EXISTS asset_community_source ON asset_community_releases(publisher,source_app);
            ''')
            for row in db.execute('SELECT owner,id,doc FROM asset_runs').fetchall():
                run = json.loads(row['doc'])
                if run['status'] in {'queued', 'running', 'cancelling'}:
                    run['status'] = 'interrupted'
                    run['revision'] += 1
                    run['error'] = {'code': 'PROCESS_RESTARTED', 'message': '服务重启，执行已中断；请核对后继续。'}
                    for step in run['steps']:
                        if step['status'] == 'running':
                            step['status'] = 'interrupted'
                            step['outcomeUnknown'] = step['kind'] in {'tool', 'skill'}
                    self.put(db, 'runs', row['owner'], run)
            for row in db.execute("SELECT owner,key,result FROM asset_requests WHERE scope='app.creator'").fetchall():
                if json.loads(row['result']).get('state') == 'creating':
                    error = {'state': 'failed', 'error': {'code': 'CREATOR_INTERRUPTED', 'status': 409,
                             'message': '上次创作请求因服务重启中断，请明确重新生成；不会自动调用模型。'}}
                    db.execute("UPDATE asset_requests SET result=? WHERE owner=? AND scope='app.creator' AND key=?",
                               (canonical(error), row['owner'], row['key']))

    @staticmethod
    def get(db, kind, owner, key):
        row = db.execute(f'SELECT doc FROM asset_{kind} WHERE owner=? AND id=?', (owner, key)).fetchone()
        if row is None:
            fail('NOT_FOUND', '记录不存在或无权访问。', 404)
        return json.loads(row['doc'])

    @staticmethod
    def put(db, kind, owner, doc):
        doc['updatedAt'] = now()
        if kind == 'apps':
            db.execute('INSERT OR REPLACE INTO asset_apps VALUES(?,?,?)', (owner, doc['id'], canonical(doc)))
        else:
            db.execute(f'INSERT OR REPLACE INTO asset_{kind} VALUES(?,?,?,?)',
                       (owner, doc['id'], doc['appId'], canonical(doc)))

    def read(self, kind, owner, key):
        with self.transaction() as db:
            return self.get(db, kind, owner, key)

    def listing(self, kind, owner, app=None):
        with self.transaction() as db:
            query, args = f'SELECT doc FROM asset_{kind} WHERE owner=?', [owner]
            if app is not None:
                query += ' AND app=?'
                args.append(app)
            return [json.loads(r['doc']) for r in db.execute(query, args)]

    @staticmethod
    def request(db, owner, scope, key, payload, action):
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            fail('IDEMPOTENCY_REQUIRED', '写入操作需要有效的请求标识。')
        fingerprint = digest(payload)
        row = db.execute('SELECT hash,result FROM asset_requests WHERE owner=? AND scope=? AND key=?',
                         (owner, scope, key)).fetchone()
        if row:
            if row['hash'] != fingerprint:
                fail('IDEMPOTENCY_CONFLICT', '同一请求标识不能用于不同内容。', 409)
            return json.loads(row['result']), False
        result = action()
        db.execute('INSERT INTO asset_requests VALUES(?,?,?,?,?)', (owner, scope, key, fingerprint, canonical(result)))
        return result, True

    def mutate_run(self, owner, key, action):
        with self.transaction() as db:
            run = self.get(db, 'runs', owner, key)
            action(run, db)
            run['revision'] += 1
            self.put(db, 'runs', owner, run)
            return run

    def artifact(self, owner, key, db=None):
        with (nullcontext(db) if db is not None else self.transaction()) as db:
            row = db.execute('SELECT doc,data FROM asset_artifacts WHERE owner=? AND id=?', (owner, key)).fetchone()
            if row is None:
                fail('NOT_FOUND', '输出不存在或无权访问。', 404)
            meta, data = json.loads(row['doc']), bytes(row['data'])
            if digest(data) != meta['sha256']:
                fail('ARTIFACT_CHANGED', '输出文件摘要不一致，不能继续使用。', 409)
            return meta, data
