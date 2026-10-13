"""Account-scoped transactional run, event, approval and immutable definition store."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .contracts import canonical, digest
from ..workbench.errors import GatewayError


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Repository:
    def __init__(self, data_dir):
        root = Path(data_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "agent-platform.sqlite3"
        self.lock = threading.RLock()
        with self.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, task TEXT NOT NULL,
                    doc TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS runs_owner ON runs(owner, updated);
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL,
                    owner TEXT NOT NULL, doc TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(owner, run, seq);
                CREATE TABLE IF NOT EXISTS agents (
                    owner TEXT NOT NULL, id TEXT NOT NULL, version TEXT NOT NULL,
                    doc TEXT NOT NULL, PRIMARY KEY(owner, id, version));
                CREATE TABLE IF NOT EXISTS requests (
                    owner TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
                    hash TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(owner, scope, key));
            """)
        self.recover()

    @contextmanager
    def transaction(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=15)
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

    @staticmethod
    def _event(db, owner, run, kind, data=None):
        event = {"runId": run["id"], "taskId": run.get("taskId", ""), "type": kind, "at": now(), **(data or {})}
        cursor = db.execute("INSERT INTO events(run,owner,doc) VALUES(?,?,?)", (run["id"], owner, canonical(event)))
        event["eventId"] = cursor.lastrowid
        return event

    @staticmethod
    def _get(db, owner, run_id):
        row = db.execute("SELECT doc FROM runs WHERE id=? AND owner=?", (run_id, owner)).fetchone()
        if row is None:
            raise GatewayError("运行不存在或无权访问。", 404)
        return json.loads(row["doc"])

    @staticmethod
    def _put(db, owner, doc):
        doc["updatedAt"] = now()
        db.execute("UPDATE runs SET doc=?,updated=? WHERE id=? AND owner=?", (canonical(doc), doc["updatedAt"], doc["id"], owner))

    def create(self, owner, request, doc, key):
        request_hash = digest(request)
        with self.transaction() as db:
            old = db.execute("SELECT hash,result FROM requests WHERE owner=? AND scope='create' AND key=?", (owner, key)).fetchone()
            if old:
                if old["hash"] != request_hash:
                    raise GatewayError("同一请求标识不能用于不同内容。", 409)
                return self._get(db, owner, old["result"]), False
            doc["createdAt"] = doc["updatedAt"] = now()
            db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (doc["id"], owner, doc.get("taskId", ""), canonical(doc), doc["updatedAt"]))
            db.execute("INSERT INTO requests VALUES(?,'create',?,?,?)", (owner, key, request_hash, doc["id"]))
            self._event(db, owner, doc, "run.created", {"status": doc["status"]})
            return doc, True

    def get(self, owner, run_id):
        with self.transaction() as db:
            return self._get(db, owner, run_id)

    def list_runs(self, owner, task_id=None):
        with self.transaction() as db:
            if task_id:
                rows = db.execute("SELECT doc FROM runs WHERE owner=? AND task=? ORDER BY updated DESC LIMIT 100", (owner, task_id))
            else:
                rows = db.execute("SELECT doc FROM runs WHERE owner=? ORDER BY updated DESC LIMIT 100", (owner,))
            return [json.loads(row["doc"]) for row in rows]

    def mutate(self, owner, run_id, callback):
        with self.transaction() as db:
            doc = self._get(db, owner, run_id)
            events = callback(doc) or []
            self._put(db, owner, doc)
            for kind, data in events:
                self._event(db, owner, doc, kind, data)
            return doc

    def mutate_request(self, owner, run_id, scope, key, request, callback):
        request_hash = digest(request)
        with self.transaction() as db:
            old = db.execute("SELECT hash,result FROM requests WHERE owner=? AND scope=? AND key=?", (owner, scope, key)).fetchone()
            if old:
                if old["hash"] != request_hash:
                    raise GatewayError("同一请求标识不能用于不同的确认内容。", 409)
                return self._get(db, owner, old["result"])
            doc = self._get(db, owner, run_id)
            events = callback(doc) or []
            self._put(db, owner, doc)
            for kind, data in events:
                self._event(db, owner, doc, kind, data)
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?)", (owner, scope, key, request_hash, run_id))
            return doc

    def events(self, owner, run_id, after=0):
        with self.transaction() as db:
            self._get(db, owner, run_id)
            rows = db.execute("SELECT seq,doc FROM events WHERE owner=? AND run=? AND seq>? ORDER BY seq LIMIT 500", (owner, run_id, after))
            return [{**json.loads(row["doc"]), "eventId": row["seq"]} for row in rows]

    def request_result(self, owner, scope, key, payload=None):
        with self.transaction() as db:
            row = db.execute("SELECT hash,result FROM requests WHERE owner=? AND scope=? AND key=?", (owner, scope, key)).fetchone()
            if row is None:
                return None
            if payload is not None and row["hash"] != digest(payload):
                raise GatewayError("同一请求标识不能用于不同内容。", 409)
            return json.loads(row["result"])

    def record_request(self, owner, scope, key, payload, result):
        with self.transaction() as db:
            db.execute("INSERT INTO requests VALUES(?,?,?,?,?)", (owner, scope, key, digest(payload), canonical(result)))

    def artifact(self, owner, artifact_id):
        # Downloads cannot depend on the pagination limit of the run list.
        with self.transaction() as db:
            for row in db.execute("SELECT doc FROM runs WHERE owner=?", (owner,)):
                match = next((item for item in json.loads(row["doc"])["artifacts"] if item["id"] == artifact_id), None)
                if match:
                    return match
        return None

    def recover(self):
        with self.transaction() as db:
            for row in db.execute("SELECT owner,doc FROM runs").fetchall():
                doc = json.loads(row["doc"])
                if doc["status"] in {"generating", "running", "queued", "cancelling"}:
                    doc["status"] = "interrupted"
                    doc["error"] = {"code": "process_restarted", "message": "上次运行被中断；不会自动重跑，请明确发起新的运行。"}
                    for step in doc.get("steps", []):
                        if step["status"] == "running":
                            step["status"] = "interrupted"
                    self._put(db, row["owner"], doc)
                    self._event(db, row["owner"], doc, "run.interrupted", {"error": doc["error"]})

    def put_agent(self, owner, spec):
        with self.transaction() as db:
            existing = db.execute("SELECT doc FROM agents WHERE owner=? AND id=? AND version=?", (owner, spec["id"], spec["version"])).fetchone()
            if existing and canonical(json.loads(existing["doc"])) != canonical(spec):
                raise GatewayError("不能覆盖已保存的应用版本。", 409)
            db.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?,?)", (owner, spec["id"], spec["version"], canonical(spec)))
        return spec

    def list_agents(self, owner):
        with self.transaction() as db:
            return [json.loads(row["doc"]) for row in db.execute("SELECT doc FROM agents WHERE owner=? ORDER BY id,version", (owner,))]

    def agent(self, owner, reference):
        agent_id, sep, version = reference.partition("@")
        if not sep:
            raise GatewayError("应用引用缺少精确版本。", 400)
        with self.transaction() as db:
            row = db.execute("SELECT doc FROM agents WHERE owner=? AND id=? AND version=?", (owner, agent_id, version)).fetchone()
            if row is None:
                raise GatewayError("应用不存在或无权访问。", 404)
            return json.loads(row["doc"])

    def save(self, owner, run_id, approval_id, binding, key):
        with self.transaction() as db:
            request_hash = digest({"runId": run_id, "approvalId": approval_id, "binding": binding})
            old = db.execute("SELECT hash,result FROM requests WHERE owner=? AND scope='save' AND key=?", (owner, key)).fetchone()
            if old:
                if old["hash"] != request_hash:
                    raise GatewayError("保存请求标识冲突。", 409)
                return json.loads(old["result"])
            doc = self._get(db, owner, run_id)
            approval = next((a for a in doc["approvals"] if a["id"] == approval_id and a["kind"] == "save"), None)
            if doc["status"] != "succeeded" or not approval or approval["bindingDigest"] != binding or approval["status"] not in {"pending", "approved"}:
                raise GatewayError("保存确认已经失效或运行尚未成功。", 409)
            spec = doc["spec"]
            if digest(spec) != binding:
                raise GatewayError("应用版本已改变，请重新确认。", 409)
            existing = db.execute("SELECT doc FROM agents WHERE owner=? AND id=? AND version=?", (owner, spec["id"], spec["version"])).fetchone()
            if existing and canonical(json.loads(existing["doc"])) != canonical(spec):
                raise GatewayError("已有应用版本冲突。", 409)
            db.execute("INSERT OR IGNORE INTO agents VALUES(?,?,?,?)", (owner, spec["id"], spec["version"], canonical(spec)))
            approval.update(status="approved", decidedAt=now())
            doc["savedAgentRef"] = spec["id"] + "@" + spec["version"]
            self._put(db, owner, doc)
            self._event(db, owner, doc, "agent.saved", {"agentRef": doc["savedAgentRef"]})
            result = {"agentRef": doc["savedAgentRef"], "spec": spec}
            db.execute("INSERT INTO requests VALUES(?,'save',?,?,?)", (owner, key, request_hash, canonical(result)))
            return result
