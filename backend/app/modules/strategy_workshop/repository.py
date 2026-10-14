"""Transactional owner isolation, idempotency, and immutable run snapshots."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading

from .contracts import ACTIVE, TERMINAL, canonical, digest, fail, now


class Repository:
    def __init__(self, data_dir):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "strategy-workshop.sqlite3"
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workshop_objects(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    doc TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workshop_owner_kind
                    ON workshop_objects(owner, kind, updated DESC, id DESC);
                CREATE TABLE IF NOT EXISTS workshop_requests(
                    owner TEXT NOT NULL, action TEXT NOT NULL, key TEXT NOT NULL,
                    digest TEXT NOT NULL, response TEXT NOT NULL,
                    PRIMARY KEY(owner,action,key));
                CREATE TABLE IF NOT EXISTS workshop_events(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    run TEXT NOT NULL, doc TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workshop_event_run ON workshop_events(owner,run,seq);
                CREATE TABLE IF NOT EXISTS workshop_snapshots(
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    sha256 TEXT NOT NULL, doc TEXT NOT NULL, created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workshop_conversation(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL,
                    session TEXT NOT NULL, run TEXT, source TEXT NOT NULL,
                    request_key TEXT NOT NULL, doc TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS workshop_conversation_session
                    ON workshop_conversation(owner,session,seq);
            """)

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=20)
            db.row_factory = sqlite3.Row
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("BEGIN IMMEDIATE")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise
            finally:
                db.close()

    def cached(self, db, owner, action, body):
        row = db.execute("SELECT digest,response FROM workshop_requests WHERE owner=? AND action=? AND key=?",
                         (owner, action, body["idempotencyKey"])).fetchone()
        if row:
            if row["digest"] != digest(body):
                fail("同一请求标识不能用于不同内容。", 409, "idempotency_conflict")
            return json.loads(row["response"])
        return None

    def remember(self, db, owner, action, body, response):
        db.execute("INSERT INTO workshop_requests VALUES(?,?,?,?,?)",
                   (owner, action, body["idempotencyKey"], digest(body), canonical(response)))
        from .conversation import append_action
        append_action(db, owner, action, body, response)

    def get(self, db, owner, kind, object_id):
        row = db.execute("SELECT doc FROM workshop_objects WHERE id=? AND owner=? AND kind=?",
                         (object_id, owner, kind)).fetchone()
        if row is None:
            fail("记录不存在或无权访问。", 404, "not_found")
        return json.loads(row["doc"])

    def read(self, owner, kind, object_id):
        with self.transaction() as db:
            return self.get(db, owner, kind, object_id)

    def insert(self, db, owner, kind, doc):
        db.execute("INSERT INTO workshop_objects VALUES(?,?,?,?,?)",
                   (doc["id"], owner, kind, canonical(doc), doc["updatedAt"]))

    def put(self, db, owner, kind, doc):
        old = self.get(db, owner, kind, doc["id"])
        if (kind in {"preflight", "personal_app"} or (kind == "run" and old["status"] in TERMINAL)
                or (kind == "customization" and old["status"] in {"validated", "validation_failed"})):
            fail("已冻结记录不能修改。", 409, "immutable_record")
        doc["updatedAt"] = now()
        db.execute("UPDATE workshop_objects SET doc=?,updated=? WHERE id=? AND owner=? AND kind=?",
                   (canonical(doc), doc["updatedAt"], doc["id"], owner, kind))

    def snapshot(self, db, owner, snapshot_id, kind, data):
        db.execute("INSERT INTO workshop_snapshots VALUES(?,?,?,?,?,?)",
                   (snapshot_id, owner, kind, digest(data), canonical(data), now()))

    def load_snapshot(self, owner, snapshot_id):
        with self.transaction() as db:
            row = db.execute("SELECT doc,sha256 FROM workshop_snapshots WHERE id=? AND owner=?",
                             (snapshot_id, owner)).fetchone()
            if not row:
                fail("快照不存在或无权访问。", 404, "not_found")
            value = json.loads(row["doc"])
            if digest(value) != row["sha256"]:
                fail("冻结快照完整性检查失败。", 503, "snapshot_corrupt")
            return value

    @staticmethod
    def personal_app_id(run_id):
        # Runs have globally unique immutable IDs, so all requests for the same
        # run resolve to one application even with different idempotency keys.
        return "strategy_app_" + run_id

    def personal_app_for_run(self, db, owner, run_id):
        row = db.execute("SELECT doc FROM workshop_objects WHERE id=? AND owner=? AND kind='personal_app'",
                         (self.personal_app_id(run_id), owner)).fetchone()
        return json.loads(row["doc"]) if row else None

    def personal_apps(self, owner):
        with self.transaction() as db:
            return [json.loads(row["doc"]) for row in db.execute(
                "SELECT doc FROM workshop_objects WHERE owner=? AND kind='personal_app' ORDER BY updated DESC,id DESC", (owner,))]

    def event(self, db, owner, run_id, kind, data=None):
        value = {"runId": run_id, "type": kind, "at": now(), **(data or {})}
        cursor = db.execute("INSERT INTO workshop_events(owner,run,doc) VALUES(?,?,?)",
                            (owner, run_id, canonical(value)))
        from .conversation import append_event
        append_event(db, owner, run_id, value, cursor.lastrowid)

    def events(self, owner, run_id, after=0):
        with self.transaction() as db:
            self.get(db, owner, "run", run_id)
            rows = db.execute("SELECT seq,doc FROM workshop_events WHERE owner=? AND run=? AND seq>? ORDER BY seq LIMIT 200",
                              (owner, run_id, after)).fetchall()
            return [{**json.loads(row["doc"]), "eventId": row["seq"]} for row in rows]

    def current(self, owner):
        with self.transaction() as db:
            # Millisecond timestamps can tie when a new rerun is opened directly
            # after another session update. Random UUID ordering is not recency.
            row = db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='session' ORDER BY updated DESC,rowid DESC LIMIT 1", (owner,)).fetchone()
            return json.loads(row["doc"]) if row else None

    def history(self, owner, cursor=None, limit=20, kind="run"):
        with self.transaction() as db:
            args, where = [owner, kind], "owner=? AND kind=?"
            if cursor:
                anchor = self.get(db, owner, kind, cursor)
                where += " AND (updated < ? OR (updated = ? AND id < ?))"
                args.extend([anchor["updatedAt"], anchor["updatedAt"], anchor["id"]])
            rows = db.execute("SELECT doc FROM workshop_objects WHERE " + where + " ORDER BY updated DESC,id DESC LIMIT ?", (*args, limit + 1)).fetchall()
            docs = [json.loads(row["doc"]) for row in rows]
            return docs[:limit], docs[limit - 1]["id"] if len(docs) > limit else None

    def active_count(self, db):
        rows = db.execute("SELECT doc FROM workshop_objects WHERE kind='run'").fetchall()
        return sum(json.loads(row["doc"])["status"] in ACTIVE for row in rows)

    def transition(self, owner, run_id, status, **updates):
        with self.transaction() as db:
            doc = self.get(db, owner, "run", run_id)
            if doc["status"] in TERMINAL:
                return doc
            # Callers publish terminal states only after the child has exited.
            # A cancellation committed immediately before publication wins.
            if doc["status"] == "cancelling" and status in {"succeeded", "failed"}:
                status = "cancelled"
                updates.pop("result", None)
                updates.pop("error", None)
            allowed = {"queued": {"validating", "cancelling", "failed", "interrupted"},
                       "validating": {"running", "cancelling", "failed", "interrupted"},
                       "running": {"succeeded", "failed", "cancelling", "interrupted"},
                       "cancelling": {"cancelled", "failed", "interrupted"}}
            if status not in allowed[doc["status"]]:
                return doc
            doc.update(updates)
            doc["status"] = status
            if status in TERMINAL:
                doc["finishedAt"] = now()
            self.put(db, owner, "run", doc)
            self.event(db, owner, run_id, "run." + status, {"status": status})
            return doc

    def recover(self):
        """The local server is single-instance; startup never resumes old work."""
        with self.transaction() as db:
            rows = db.execute("SELECT owner,doc FROM workshop_objects WHERE kind='run'").fetchall()
            for row in rows:
                doc = json.loads(row["doc"])
                if doc["status"] in ACTIVE:
                    doc.update(status="interrupted", finishedAt=now(), error={"code": "server_restarted", "message": "服务重启中断了运行，请创建新回测。"})
                    self.put(db, row["owner"], "run", doc)
                    self.event(db, row["owner"], doc["id"], "run.interrupted", {"status": "interrupted"})
