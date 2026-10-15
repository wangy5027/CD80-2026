"""Owner-scoped immutable definitions and idempotent publication transactions."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from ..capability_library.contracts import canonical, digest, fail


class Store:
    def __init__(self, root):
        self.path = Path(root) / 'evolution.sqlite3'
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records(owner TEXT, kind TEXT, id TEXT, body TEXT, PRIMARY KEY(owner,kind,id))')
            db.execute('PRAGMA user_version=1')

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
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
    def get(db, owner, kind, key):
        row = db.execute('SELECT body FROM records WHERE owner=? AND kind=? AND id=?', (owner, kind, key)).fetchone()
        if not row:
            fail('NOT_FOUND', '资源不存在或无权访问。', 404)
        return json.loads(row[0])

    @staticmethod
    def put(db, owner, kind, key, value):
        db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?)', (owner, kind, key, canonical(value)))

    @staticmethod
    def list(db, owner, kind):
        return [json.loads(row[0]) for row in db.execute('SELECT body FROM records WHERE owner=? AND kind=? ORDER BY rowid DESC', (owner, kind))]

    def replay(self, owner, scope, key, payload):
        """Return a committed result before checking mutable external resources.

        write() checks again under its transaction, so concurrent first attempts
        remain serialized. This lookup never starts a new action.
        """
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            fail('INVALID_REQUEST', '请提供Idempotency-Key。')
        with self.transaction() as db:
            row = db.execute('SELECT body FROM records WHERE owner=? AND kind=? AND id=?',
                             (owner, 'request', digest([scope, key]))).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved['hash'] != digest(payload):
                    fail('IDEMPOTENCY_CONFLICT', '同一请求标识不能用于不同内容。', 409)
                return True, saved['result']
            return False, None

    def write(self, owner, scope, key, payload, function):
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            fail('INVALID_REQUEST', '请提供Idempotency-Key。')
        with self.transaction() as db:
            request_id = digest([scope, key])
            fingerprint = digest(payload)
            row = db.execute('SELECT body FROM records WHERE owner=? AND kind=? AND id=?', (owner, 'request', request_id)).fetchone()
            if row:
                saved = json.loads(row[0])
                if saved['hash'] != fingerprint:
                    fail('IDEMPOTENCY_CONFLICT', '同一请求标识不能用于不同内容。', 409)
                return saved['result']
            result = function(db)
            self.put(db, owner, 'request', request_id, {'hash': fingerprint, 'result': result})
            return result
