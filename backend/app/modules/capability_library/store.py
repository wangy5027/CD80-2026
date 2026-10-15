"""Separate SQLite database; atomic records and original bytes, always owner scoped."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from .contracts import canonical, fail


class Store:
    def __init__(self, directory):
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        path = root / 'capabilities.sqlite3'
        if path.is_symlink():
            raise ValueError('Capability database must not be a symlink')
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS capability_records (
                owner TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
                body TEXT NOT NULL, PRIMARY KEY(owner, kind, id));
            CREATE TABLE IF NOT EXISTS capability_blobs (
                owner TEXT NOT NULL, id TEXT NOT NULL, content BLOB NOT NULL,
                PRIMARY KEY(owner, id));
            PRAGMA user_version=1;
        ''')
        # A lost worker is never replayed on restart.
        with self.transaction():
            for owner, kind, key, body in self.db.execute("SELECT owner,kind,id,body FROM capability_records WHERE kind IN ('job','invocation','verification','trial')").fetchall():
                value = json.loads(body)
                if value.get('status') in {'queued', 'running', 'cancelling'}:
                    value.update(status='interrupted', error={'code':'INTERRUPTED', 'error':'服务已重启，请检查记录后主动重试。'})
                    self.put(owner, kind, key, value)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def get(self, owner, kind, key, default=None):
        with self.lock:
            row = self.db.execute('SELECT body FROM capability_records WHERE owner=? AND kind=? AND id=?', (str(owner), kind, key)).fetchone()
        if row is None:
            if default is not None:
                return default
            fail('NOT_FOUND', '记录不存在或无权访问。', 404)
        return json.loads(row[0])

    def put(self, owner, kind, key, value):
        with self.lock:
            self.db.execute('INSERT INTO capability_records VALUES (?,?,?,?) ON CONFLICT(owner,kind,id) DO UPDATE SET body=excluded.body', (str(owner), kind, key, canonical(value)))

    def list(self, owner, kind):
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM capability_records WHERE owner=? AND kind=? ORDER BY rowid DESC', (str(owner), kind))]

    def delete(self, owner, kind, key):
        with self.lock:
            self.db.execute('DELETE FROM capability_records WHERE owner=? AND kind=? AND id=?', (str(owner), kind, key))

    def blob(self, owner, key, content=None):
        with self.lock:
            if content is not None:
                self.db.execute('INSERT OR IGNORE INTO capability_blobs VALUES (?,?,?)', (str(owner), key, content))
                return
            row = self.db.execute('SELECT content FROM capability_blobs WHERE owner=? AND id=?', (str(owner), key)).fetchone()
            if row is None:
                fail('NOT_FOUND', '文件不存在或无权访问。', 404)
            return row[0]

    def close(self):
        with self.lock:
            self.db.close()
