"""Owner-scoped workshop documents and atomic, idempotent draft publication."""
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

    @staticmethod
    def check_revision(doc, revision, states):
        if doc["revision"] != revision or doc["status"] not in states:
            raise GatewayError("页面内容已经改变，请刷新后再操作。", 409, "stale_revision")

    def commit(self, owner, scope, key, payload, doc, *, previous=None, publish=False, target=None):
        """Commit the session, immutable app and replay result in one transaction."""
        with self.repository.transaction() as db:
            old = self.replay_in(db, owner, scope, key, payload)
            if old is not None:
                return old
            if previous is not None:
                current = self.get_in(db, owner, doc["id"])
                self.check_revision(current, previous["revision"], {previous["status"]})
            doc["updatedAt"] = now()
            metadata = None
            if publish:
                spec = doc["_spec"]
                ref = spec["id"] + "@" + spec["version"]
                existing = db.execute("SELECT doc FROM agents WHERE owner=? AND id=? AND version=?", (owner, spec["id"], spec["version"])).fetchone()
                if existing is not None:
                    raise GatewayError("已有同名应用版本，请重新创建新应用。", 409)
                metadata = {"sessionId": doc["id"], "requirement": doc["requirement"],
                            "clarifications": doc["clarifications"],
                            "extraRequirements": doc["appPreview"].get("extraRequirements", []),
                            "sourceApps": doc["appPreview"].get("sourceApps", []),
                            "composition": doc["appPreview"].get("composition"),
                            "executionStatus": "not_run", "savedAt": doc["updatedAt"]}
                db.execute("INSERT INTO agents VALUES(?,?,?,?)", (owner, spec["id"], spec["version"], canonical(spec)))
                db.execute("INSERT INTO app_workshop_apps VALUES(?,?,?)", (owner, ref, canonical(metadata)))
            if previous is None:
                db.execute("INSERT INTO app_workshop_sessions VALUES(?,?,?,?)", (doc["id"], owner, canonical(doc), doc["updatedAt"]))
            else:
                db.execute("UPDATE app_workshop_sessions SET doc=?,updated=? WHERE owner=? AND id=?", (canonical(doc), doc["updatedAt"], owner, doc["id"]))
            result = public(doc)
            if publish:
                result = {"session": result, "agentRef": doc["savedAgentRef"],
                          "entry": {"agentRef": doc["savedAgentRef"], "spec": copy.deepcopy(doc["_spec"]), "personal": True, "workshop": metadata}}
            elif target is not None:
                result = {"session": result, "target": target, "requirement": doc["requirement"],
                          "inputs": doc["recommendation"].get("inputs", {})}
            db.execute("INSERT INTO app_workshop_requests VALUES(?,?,?,?,?)", (owner, scope, key, digest(payload), canonical(result)))
            return result
