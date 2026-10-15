"""Immutable personal rule versions, saved before validation or market access."""
import copy
import json

from .contracts import digest, fail, now


def save_rules(service, db, owner, session):
    custom = session.get("customization")
    if custom and custom.get("status") not in {"rules_ready", "validated", "validation_failed"}:
        fail("规则尚未生成完整，请先完成需求对齐。", 409, "rules_required")
    candidate = session.get("candidate")
    if not candidate:
        fail("请先生成策略规则。", 409, "rules_required")
    source_hash = custom["sourceHash"] if custom else candidate["sourceHash"]
    existing = session.get("savedPersonalApp")
    if existing:
        app = service.repository.get(db, owner, "personal_app", existing["id"])
        if app.get("sourceHash") == source_hash:
            return public_rules(service, db, owner, app)
    frozen = {"idea": session["idea"], "candidate": copy.deepcopy(candidate),
              "baseCandidate": copy.deepcopy(session.get("baseCandidate") or candidate),
              "customization": copy.deepcopy(custom),
              "languageStyle": session.get("languageStyle", "beginner")}
    fingerprint = digest(frozen)
    app_id = "strategy_app_" + fingerprint[:32]
    row = db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='personal_app' AND id=?",
                     (owner, app_id)).fetchone()
    if row:
        return public_rules(service, db, owner, json.loads(row["doc"]))
    stamp = now()
    strategy = copy.deepcopy(candidate["strategy"])
    if custom:
        strategy.update(name=custom["rulePlan"]["name"], strategyId="custom_" + custom["planHash"][:16],
                        parameters={}, version="2.0.0")
    app = {"id": app_id, "versionId": "rules_" + fingerprint, "kind": "strategy", "personal": True,
           "name": strategy["name"], "description": session["idea"], "strategy": strategy,
           "strategyKind": "custom" if custom else "catalog", "sourceHash": source_hash,
           "customizationId": custom["id"] if custom else None,
           "definitionDigest": fingerprint, "_draftDefinition": frozen,
           "sessionId": session["id"], "runId": None, "requested": None, "actual": None,
           "mode": "diagnostic", "verification": {"status": "unverified"},
           "previousAppId": session.get("editingPersonalAppId"),
           "createdAt": stamp, "updatedAt": stamp}
    service.repository.insert(db, owner, "personal_app", app)
    return public_rules(service, db, owner, app)


def check_definition(app):
    frozen = app.get("_draftDefinition")
    if not isinstance(frozen, dict) or digest(frozen) != app["definitionDigest"]:
        fail("保存的规则快照完整性检查失败。", 503, "snapshot_corrupt")
    return frozen


def public_rules(service, db, owner, app):
    check_definition(app)
    result = service._public_personal_app(app)
    rows = db.execute("SELECT doc FROM workshop_objects WHERE owner=? AND kind='run' ORDER BY updated DESC,rowid DESC", (owner,))
    linked = [json.loads(row["doc"]) for row in rows]
    linked = [run for run in linked if run.get("savedPersonalAppId") == app["id"]
              and run.get("candidate", {}).get("sourceHash") == app["sourceHash"]]
    successful = next((run for run in linked if run["status"] == "succeeded" and run.get("result")), None)
    latest = successful or next(iter(linked), None)
    if latest:
        state = "passed" if successful else "failed" if latest["status"] == "failed" else "unverified"
        result.update(runId=latest["id"], requested=latest.get("requested"), actual=latest.get("actual"),
                      verification={"status": state, "runId": latest["id"], "kind": "engineering_diagnostic"})
    return result


def open_rules(service, db, owner, app, body):
    from .service import identifier
    from .presentation import language_style
    frozen = copy.deepcopy(check_definition(app))
    session_id, stamp = identifier("ws"), now()
    custom = frozen["customization"]
    editing = body.get("mode") == "edit"
    candidate = frozen["baseCandidate"] if custom else frozen["candidate"]
    candidate["selectionDigest"] = digest({"sessionId": session_id, "candidate": candidate, "appId": app["id"]})
    doc = {"id": session_id, "sessionId": session_id, "idea": frozen["idea"],
           "status": "awaiting_selection", "revision": 1, "matchRevision": 1,
           "selectionRevision": None, "selectionApproval": None, "configRevision": 0,
           "candidate": candidate, "baseCandidate": copy.deepcopy(candidate), "customization": None,
           "languageStyle": language_style(body.get("languageStyle", frozen["languageStyle"])),
           "preflightId": None, "runId": None, "createdAt": stamp, "updatedAt": stamp}
    if editing:
        doc["editingPersonalAppId"] = app["id"]
    else:
        doc["savedPersonalApp"] = public_rules(service, db, owner, app)
        if custom:
            custom.update(id=identifier("custom"), sessionId=session_id, matchRevision=1,
                          status="rules_ready", validation=None, createdAt=stamp, updatedAt=stamp)
            custom.pop("ruleApproval", None)
            custom["rulesDigest"] = digest({"customizationId": custom["id"], "matchRevision": 1,
                "plan": custom["rulePlan"], "summary": custom["summary"],
                "assumptions": custom["assumptions"], "sourceHash": custom["sourceHash"]})
            doc.update(status="customizing", customization=custom)
            service.repository.insert(db, owner, "customization", custom)
    service.repository.insert(db, owner, "session", doc)
    return doc
