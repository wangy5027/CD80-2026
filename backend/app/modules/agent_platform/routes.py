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
    return jsonify(platform.dispatch(owner, json_body()))


@agents.route("/desktop/api/agent-dispatch/<request_id>/cancel", methods=["POST"])
def cancel_dispatch(request_id):
    platform, owner = context()
    return jsonify(platform.cancel_dispatch(owner, request_id))


@agents.route("/desktop/api/agents", methods=["GET", "POST"])
def catalog():
    platform, owner = context()
    if request.method == "POST":
        return jsonify(platform.save(owner, json_body())), 201
    workshop = current_app.extensions.get("strategy_workshop")
    return jsonify({"agents": platform.list_agents(owner),
                    "strategyApps": workshop.list_personal_apps(owner) if workshop else []})


@agents.route("/desktop/api/agents/<agent_id>/versions/<version>", methods=["GET"])
def definition(agent_id, version):
    platform, owner = context()
    return jsonify(platform.agent(owner, agent_id + "@" + version))


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
