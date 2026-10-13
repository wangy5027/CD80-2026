"""Authenticated same-origin workshop HTTP endpoints."""
from uuid import uuid4

from flask import Blueprint, current_app, jsonify, request

from ..workbench.errors import GatewayError
from ..workbench.routes import json_body
from .contracts import fail

workshop = Blueprint("strategy_workshop", __name__, url_prefix="/desktop/api/strategy-workshop")


def context():
    workbench = current_app.extensions["workbench_service"]
    owner = workbench.authenticate(request.headers.get("Cookie", ""))["user"]["id"]
    return current_app.extensions["strategy_workshop"], owner


@workshop.errorhandler(GatewayError)
def workshop_error(error):
    if request.method == "POST" and request.endpoint != "strategy_workshop.create_session":
        try:
            service, owner = context()
            body = request.get_json(silent=True)
            session_id = (request.view_args or {}).get("session_id") or (body or {}).get("sessionId") if isinstance(body, dict) else None
            if session_id:
                service.record_conversation_failure(owner, session_id, (request.endpoint or "").split(".")[-1], body, error)
        except Exception:
            # History must not change the public error or disclose auth/storage
            # diagnostics when a rejected request itself could not be recorded.
            pass
    return jsonify({"error": {"code": error.code or "request_failed", "message": str(error),
                               "field": getattr(error, "field", None), "stage": getattr(error, "stage", None),
                               "retryable": getattr(error, "retryable", False)}, "requestId": uuid4().hex}), error.status


@workshop.get("/capabilities")
def capabilities():
    service, _ = context()
    return jsonify(service.capabilities())


@workshop.get("/strategies")
def strategies():
    service, _ = context()
    return jsonify(service.strategies())


@workshop.get("/symbols")
def symbols():
    service, _ = context()
    query = request.args.get("q", "")
    if len(query) > 100:
        fail("标的搜索词过长。")
    value = service.symbols(query)
    return jsonify(value if isinstance(value, dict) else {"symbols": value})


@workshop.post("/sessions")
def create_session():
    service, owner = context()
    return jsonify(service.create_session(owner, json_body())), 201


@workshop.get("/sessions/current")
def current():
    service, owner = context()
    return jsonify(service.current(owner))


@workshop.get("/sessions")
def sessions():
    service, owner = context()
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        fail("历史记录条数须为1至100。", field="limit")
    return jsonify(service.sessions(owner, request.args.get("cursor"), limit))


@workshop.get("/sessions/<session_id>/conversation")
def session_conversation(session_id):
    service, owner = context()
    return jsonify(service.conversation(owner, session_id=session_id))


@workshop.get("/sessions/<session_id>")
def session(session_id):
    service, owner = context()
    return jsonify(service.session(owner, session_id))


@workshop.post("/sessions/<session_id>/rematch")
def rematch(session_id):
    service, owner = context()
    return jsonify(service.rematch(owner, session_id, json_body()))


@workshop.post("/sessions/<session_id>/selection")
def selection(session_id):
    service, owner = context()
    return jsonify(service.selection(owner, session_id, json_body()))


@workshop.post("/sessions/<session_id>/preflight")
def preflight(session_id):
    service, owner = context()
    return jsonify(service.preflight(owner, session_id, json_body()))


@workshop.post("/sessions/<session_id>/customization")
def customization(session_id):
    service, owner = context()
    return jsonify(service.customization(owner, session_id, json_body()))


@workshop.post("/sessions/<session_id>/customization/confirm")
def customization_confirm(session_id):
    service, owner = context()
    return jsonify(service.customization_confirm(owner, session_id, json_body()))


@workshop.post("/sessions/<session_id>/customization/reset")
def customization_reset(session_id):
    service, owner = context()
    return jsonify(service.customization_reset(owner, session_id, json_body()))


@workshop.route("/runs", methods=["GET", "POST"])
def runs():
    service, owner = context()
    if request.method == "POST":
        return jsonify(service.create_run(owner, json_body())), 202
    return jsonify(service.history(owner, request.args.get("cursor")))


@workshop.get("/runs/<run_id>")
def run(run_id):
    service, owner = context()
    return jsonify(service.run(owner, run_id))


@workshop.get("/runs/<run_id>/conversation")
def run_conversation(run_id):
    service, owner = context()
    return jsonify(service.conversation(owner, run_id=run_id))


@workshop.post("/runs/<run_id>/cancel")
def cancel(run_id):
    service, owner = context()
    return jsonify(service.cancel(owner, run_id, json_body()))


@workshop.get("/runs/<run_id>/code")
def code(run_id):
    service, owner = context()
    return jsonify(service.code(owner, run_id))


@workshop.get("/runs/<run_id>/events")
def events(run_id):
    service, owner = context()
    try:
        after = max(0, int(request.args.get("after", 0)))
    except ValueError:
        fail("事件游标不正确。")
    return jsonify(service.events(owner, run_id, after))
