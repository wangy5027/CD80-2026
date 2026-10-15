"""Authenticated platform API; SSE observes durable state, never starts work."""

import json
import time

from flask import Blueprint, Response, current_app, jsonify, request, send_file, stream_with_context

from .contracts import TERMINAL
from ..workbench.errors import GatewayError
from ..workbench.routes import json_body

agents = Blueprint("agent_platform", __name__)


def context():
    workbench = current_app.extensions["workbench_service"]
    owner = workbench.authenticate(request.headers.get("Cookie", ""))["user"]["id"]
    return current_app.extensions["agent_platform"], owner


@agents.route("/desktop/api/agent-runtime", methods=["GET"])
def runtime():
    platform, _ = context()
    return jsonify(platform.status())


@agents.route("/desktop/api/agent-dispatch", methods=["POST"])
def dispatch():
    platform, owner = context()
    payload = json_body()
    if payload.get("capabilityRefs"):
        workbench = current_app.extensions["workbench_service"]
        task = workbench.get_task(owner, payload["taskId"]) if payload.get("taskId") else None
        workbench.capabilities.prepare_chat(owner, task, payload["capabilityRefs"], payload.get("replaceCapabilityLocks") is True, payload.get("taskRevision"))
        return jsonify({"intent":"chat", "taskId":payload.get("taskId"), "capabilityRefs":payload["capabilityRefs"], "fileIds":payload.get("fileIds",[])})
    return jsonify(platform.dispatch(owner, payload))


@agents.route("/desktop/api/agent-dispatch/<request_id>/cancel", methods=["POST"])
def cancel_dispatch(request_id):
    platform, owner = context()
    return jsonify(platform.cancel_dispatch(owner, request_id))


@agents.route("/desktop/api/agents", methods=["GET", "POST"])
def catalog():
    platform, owner = context()
    if request.method == "POST":
        return jsonify(platform.save(owner, json_body())), 201
    workshop = current_app.extensions.get('strategy_workshop')
    return jsonify({"agents": platform.list_agents(owner,catalog=True),
                    "strategyApps": workshop.list_personal_apps(owner) if workshop else []})


@agents.route("/desktop/api/agents/<agent_id>/versions/<version>", methods=["GET"])
def definition(agent_id, version):
    platform, owner = context()
    return jsonify(platform.agent(owner, agent_id + "@" + version))


@agents.get('/desktop/api/agent-components')
def components():
    platform, owner = context()
    return jsonify({'components': platform.components(owner)})


@agents.get('/desktop/api/agents/<agent_id>/versions/<version>/export')
def export_application(agent_id, version):
    platform, owner = context()
    package = platform.export_application(owner, agent_id + '@' + version)
    response = jsonify(package)
    response.headers.update({'Content-Disposition': 'attachment; filename="application.json"',
                             'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return response


@agents.post('/desktop/api/agents/import')
def import_application():
    platform, owner = context()
    return jsonify(platform.import_application(owner, json_body())), 201


@agents.post('/desktop/api/agents/fork')
def fork_application():
    platform, owner = context()
    return jsonify(platform.fork_application(owner, json_body())), 201


@agents.post('/desktop/api/agents/copy-preset')
def copy_preset():
    from .contracts import text
    from .asset_legacy import migrate_pipeline
    from ..capability_library.contracts import digest
    platform,owner=context();body=json_body()
    source=platform.presets.get(body.get('agentRef',''))
    if source is None:raise GatewayError('预置应用不存在。',404)
    workbench = current_app.extensions['workbench_service']
    def save_modern(db, spec):
        doc = migrate_pipeline(workbench.asset_apps, owner, spec)
        provenance = {'kind': 'official_legacy_preset', 'agentRef': body['agentRef'],
                      'definitionDigest': digest(source)}
        return workbench.asset_apps._save_in_transaction(db, owner, {'definition': doc}, provenance=provenance)
    result = platform.repository.copy_preset(owner, source, text(body.get('idempotencyKey'), '请求标识', 150),
                                             body.get('confirmDuplicate') is True, save_modern=save_modern)
    return jsonify(result), 201


@agents.route("/desktop/api/agent-runs", methods=["GET", "POST"])
def runs():
    platform, owner = context()
    if request.method == "POST":
        return jsonify(platform.create(owner, json_body())), 202
    return jsonify({"runs": platform.repository.list_runs(owner, request.args.get("taskId"))})


@agents.route("/desktop/api/agent-runs/<run_id>", methods=["GET"])
def run(run_id):
    platform, owner = context()
    return jsonify(platform.repository.get(owner, run_id))


@agents.route("/desktop/api/agent-runs/<run_id>/input", methods=["POST"])
def supply_input(run_id):
    platform, owner = context()
    return jsonify(platform.supply_input(owner, run_id, json_body()))


@agents.route("/desktop/api/agent-runs/<run_id>/approvals/<approval_id>", methods=["POST"])
def approval(run_id, approval_id):
    platform, owner = context()
    return jsonify(platform.approve(owner, run_id, approval_id, json_body()))


@agents.route("/desktop/api/agent-runs/<run_id>/feedback", methods=["POST"])
def feedback(run_id):
    platform, owner = context()
    return jsonify(platform.feedback(owner, run_id, json_body()))


@agents.route("/desktop/api/agent-runs/<run_id>/cancel", methods=["POST"])
def cancel(run_id):
    platform, owner = context()
    return jsonify(platform.cancel(owner, run_id))


@agents.route("/desktop/api/agent-runs/<run_id>/events", methods=["GET"])
def events(run_id):
    platform, owner = context()
    platform.repository.get(owner, run_id)
    try:
        after = max(0, int(request.headers.get("Last-Event-ID") or request.args.get("after", "0")))
    except ValueError:
        raise GatewayError("事件游标不正确。", 400) from None
    def stream():
        cursor = after
        for _ in range(100):
            batch = platform.repository.events(owner, run_id, cursor)
            for event in batch:
                cursor = event["eventId"]
                yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            doc = platform.repository.get(owner, run_id)
            if doc["status"] in TERMINAL | {"awaiting_input", "awaiting_confirmation", "awaiting_authorization"}:
                yield "event: done\ndata: " + json.dumps({"runId": run_id, "status": doc["status"]}) + "\n\n"
                return
            if not batch:
                yield ": waiting\n\n"
            time.sleep(.2)
    return Response(stream_with_context(stream()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@agents.route("/desktop/api/agent-artifacts/<artifact_id>", methods=["GET"])
def artifact(artifact_id):
    platform, owner = context()
    path, metadata = platform.artifact(owner, artifact_id)
    response = send_file(path, as_attachment=True, download_name=metadata["name"], mimetype=metadata["mediaType"], conditional=False)
    response.headers.update({"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'", "Cache-Control": "no-store"})
    return response
