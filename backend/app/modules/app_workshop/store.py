"""Owner-scoped workshop documents and a recoverable AssetApps save receipt."""
import copy
import json

from ..agent_platform.contracts import canonical, digest
from ..agent_platform.repository import now
from ..workbench.errors import GatewayError


def public(doc):
    return copy.deepcopy({key: value for key, value in doc.items() if not key.startswith("_")})


class Store:
    def __init__(self, repository):
        self.repository = repository
        with self.repository.transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS app_workshop_model_traces (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, doc TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS app_workshop_sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, doc TEXT NOT NULL, updated TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS app_workshop_sessions_owner ON app_workshop_sessions(owner,updated);
                CREATE TABLE IF NOT EXISTS app_workshop_requests (
                    owner TEXT NOT NULL, scope TEXT NOT NULL, key TEXT NOT NULL,
                    hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(owner,scope,key));
                CREATE TABLE IF NOT EXISTS app_workshop_sources (
                    owner TEXT NOT NULL, app_id TEXT NOT NULL, version_id TEXT NOT NULL,
                    definition_digest TEXT NOT NULL, spec_digest TEXT NOT NULL,
                    spec TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(owner,app_id));
                CREATE INDEX IF NOT EXISTS app_workshop_sources_owner ON app_workshop_sources(owner);
            """)
            for row in db.execute("SELECT id,owner,doc FROM app_workshop_model_traces").fetchall():
                trace = json.loads(row["doc"])
                if trace.get("status") == "running":
                    trace.update(status="interrupted", completedAt=now())
                    db.execute("UPDATE app_workshop_model_traces SET doc=? WHERE id=? AND owner=?",
                               (canonical(trace), row["id"], row["owner"]))

    def put_trace(self, owner, doc):
        with self.repository.transaction() as db:
            db.execute("INSERT OR REPLACE INTO app_workshop_model_traces VALUES(?,?,?)",
                       (doc["id"], owner, canonical(doc)))

    def trace(self, owner, trace_id):
        with self.repository.transaction() as db:
            row = db.execute("SELECT doc FROM app_workshop_model_traces WHERE id=? AND owner=?", (trace_id, owner)).fetchone()
            if row is None:
                raise GatewayError("模型调用记录不存在或无权访问。", 404)
            return json.loads(row["doc"])

    @staticmethod
    def get_in(db, owner, session_id):
        row = db.execute("SELECT doc FROM app_workshop_sessions WHERE id=? AND owner=?", (session_id, owner)).fetchone()
        if row is None:
            raise GatewayError("这次需求不存在或不属于当前账号。", 404)
        return json.loads(row["doc"])

    @staticmethod
    def replay_in(db, owner, scope, key, payload):
        row = db.execute("SELECT hash,result FROM app_workshop_requests WHERE owner=? AND scope=? AND key=?", (owner, scope, key)).fetchone()
        if row is None:
            return None
        if row["hash"] != digest(payload):
            raise GatewayError("这次提交标识已经用于其他内容，请重新提交。", 409, "request_conflict")
        return json.loads(row["result"])

    def replay(self, owner, scope, key, payload):
        with self.repository.transaction() as db:
            return self.replay_in(db, owner, scope, key, payload)

    def get(self, owner, session_id):
        with self.repository.transaction() as db:
            return self.get_in(db, owner, session_id)

    def current(self, owner):
        with self.repository.transaction() as db:
            row = db.execute("SELECT doc FROM app_workshop_sessions WHERE owner=? ORDER BY updated DESC,rowid DESC LIMIT 1", (owner,)).fetchone()
            return public(json.loads(row["doc"])) if row else None

    def sources(self, owner):
        with self.repository.transaction() as db:
            return [{"appId": row["app_id"], "versionId": row["version_id"],
                     "definitionDigest": row["definition_digest"], "specDigest": row["spec_digest"],
                     "spec": json.loads(row["spec"])} for row in db.execute(
                         "SELECT app_id,version_id,definition_digest,spec_digest,spec "
                         "FROM app_workshop_sources WHERE owner=?", (owner,))]

    @staticmethod
    def check_revision(doc, revision, states):
        if doc["revision"] != revision or doc["status"] not in states:
            raise GatewayError("页面内容已经改变，请刷新后再操作。", 409, "stale_revision")

    def commit(self, owner, scope, key, payload, doc, *, previous=None, saved=None, target=None):
        """Atomically commit session, workshop source index and replay receipt.

        The AssetApps save happens first with its own stable request key. If this
        transaction fails or the process dies, the same save request is replayed
        and this transaction can be attempted again without a duplicate asset.
        """
        with self.repository.transaction() as db:
            old = self.replay_in(db, owner, scope, key, payload)
            if old is not None:
                return old
            if previous is not None:
                current = self.get_in(db, owner, doc["id"])
                self.check_revision(current, previous["revision"], {previous["status"]})
            doc["updatedAt"] = now()
            if saved is not None:
                spec = doc["_spec"]
                db.execute("INSERT INTO app_workshop_sources VALUES(?,?,?,?,?,?,?)", (
                    owner, saved["appId"], saved["versionId"], saved["definitionDigest"],
                    digest(spec), canonical(spec), doc["updatedAt"]))
            if previous is None:
                db.execute("INSERT INTO app_workshop_sessions VALUES(?,?,?,?)", (doc["id"], owner, canonical(doc), doc["updatedAt"]))
            else:
                db.execute("UPDATE app_workshop_sessions SET doc=?,updated=? WHERE owner=? AND id=?", (canonical(doc), doc["updatedAt"], owner, doc["id"]))
            result = public(doc)
            if saved is not None:
                result = {"session": result, "appId": saved["appId"], "versionId": saved["versionId"],
                          "detail": copy.deepcopy(saved)}
            elif target is not None:
                result = {"session": result, "target": target, "requirement": doc["requirement"],
                          "inputs": doc["recommendation"].get("inputs", {})}
            db.execute("INSERT INTO app_workshop_requests VALUES(?,?,?,?,?)", (owner, scope, key, digest(payload), canonical(result)))
            return result
