"""Authenticated Explore API. Reads and polling never schedule model work."""

from urllib.parse import quote

from flask import Blueprint, Response, current_app, jsonify, request

from ..workbench.errors import GatewayError
from ..workbench.routes import json_body


explore = Blueprint("explore", __name__)


def context():
    workbench = current_app.extensions["workbench_service"]
    owner = workbench.authenticate(request.headers.get("Cookie", ""))["user"]["id"]
    return current_app.extensions["explore_service"], owner


@explore.route("/desktop/api/explore/sessions", methods=["GET", "POST"])
def sessions():
    service, owner = context()
    if request.method == "POST":
        return jsonify({"session": service.create_session(owner, json_body())}), 201
    return jsonify({"sessions": service.repository.list_sessions(owner)})


@explore.route("/desktop/api/explore/sessions/<session_id>", methods=["GET", "PATCH", "DELETE"])
def session(session_id):
    service, owner = context()
    if request.method == "PATCH":
        return jsonify({"session": service.rename_session(owner, session_id, json_body())})
    if request.method == "DELETE":
        service.delete_session(owner, session_id)
        return jsonify({"deleted": True})
    item, runs = service.repository.get_session(owner, session_id)
    return jsonify({"session": item, "runs": runs})


@explore.route("/desktop/api/explore/sessions/<session_id>/files", methods=["GET"])
def files(session_id):
    service, owner = context()
    return jsonify({"files": service.repository.list_files(owner, session_id)})


@explore.route("/desktop/api/explore/sessions/<session_id>/files/<file_id>",
               methods=["GET", "PATCH", "DELETE"])
def file_item(session_id, file_id):
    service, owner = context()
    if request.method == "PATCH":
        return jsonify({"file": service.rename_file(owner, session_id, file_id, json_body())})
    if request.method == "DELETE":
        service.repository.delete_file(owner, session_id, file_id)
        return jsonify({"deleted": True})
    return jsonify({"file": service.repository.get_file(owner, session_id, file_id)})


@explore.route("/desktop/api/explore/sessions/<session_id>/files/<file_id>/download", methods=["GET"])
def file_download(session_id, file_id):
    service, owner = context()
    item = service.repository.get_file(owner, session_id, file_id)
    response = Response(item["content"], mimetype="text/markdown; charset=utf-8")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="explore-{file_id}.md"; filename*=UTF-8\'\'{quote(item["name"])}')
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response


@explore.route("/desktop/api/explore/sessions/<session_id>/context", methods=["GET", "PUT"])
def session_context(session_id):
    service, owner = context()
    if request.method == "PUT":
        return jsonify({"context": service.set_context(owner, session_id, json_body())})
    module = request.args.get("module")
    source_report_id = request.args.get("source_report_id") or None
    if module is None:
        if source_report_id is not None:
            raise GatewayError("预览来源报告需要指定模块。", 400)
        return jsonify({"contexts": service.repository.get_contexts(owner, session_id)})
    return jsonify({"context": service.repository.preview_context(owner, session_id, module,
                                                                  source_report_id)})


@explore.route("/desktop/api/explore/sessions/<session_id>/runs", methods=["POST"])
def create_run(session_id):
    service, owner = context()
    return jsonify({"run": service.create_run(owner, session_id, json_body())}), 202


@explore.route("/desktop/api/explore/runs/<run_id>", methods=["GET"])
def run(run_id):
    service, owner = context()
    return jsonify({"run": service.repository.get_run(owner, run_id)})


@explore.route("/desktop/api/explore/runs/<run_id>/events", methods=["GET"])
def run_events(run_id):
    service, owner = context()
    service.repository.get_run(owner, run_id)
    raw = request.args.get("after", "0")
    if not raw.isdigit() or len(raw) > 15:
        raise GatewayError("事件游标不正确。", 400)
    return jsonify({"events": service.repository.events(owner, run_id, int(raw))})


@explore.route("/desktop/api/explore/runs/<run_id>/input", methods=["POST"])
def supply_input(run_id):
    service, owner = context()
    return jsonify({"run": service.supply_input(owner, run_id, json_body())}), 202


@explore.route("/desktop/api/explore/runs/<run_id>/clarify", methods=["POST"])
def clarify(run_id):
    service, owner = context()
    return jsonify({"run": service.clarify(owner, run_id, json_body())}), 202


@explore.route("/desktop/api/explore/runs/<run_id>/confirm", methods=["POST"])
def confirm(run_id):
    service, owner = context()
    return jsonify({"run": service.confirm(owner, run_id, json_body())}), 202


@explore.route("/desktop/api/explore/runs/<run_id>/feedback", methods=["POST"])
def feedback(run_id):
    service, owner = context()
    return jsonify({"run": service.feedback(owner, run_id, json_body())}), 202


@explore.route("/desktop/api/explore/runs/<run_id>/cancel", methods=["POST"])
def cancel(run_id):
    service, owner = context()
    return jsonify({"run": service.cancel(owner, run_id)})


@explore.route("/desktop/api/explore/runs/<run_id>/retry", methods=["POST"])
def retry(run_id):
    service, owner = context()
    return jsonify({"run": service.retry(owner, run_id, json_body())}), 202


@explore.route("/desktop/api/explore/reports/<report_id>", methods=["GET"])
def report(report_id):
    service, owner = context()
    return jsonify({"report": service.repository.get_report(owner, report_id)})


@explore.route("/desktop/api/explore/reports/<report_id>/sources", methods=["GET"])
def report_sources(report_id):
    service, owner = context()
    return jsonify({"sources": service.repository.get_report(owner, report_id)["sources"]})


@explore.route("/desktop/api/explore/reports/<report_id>/markdown", methods=["GET"])
def report_markdown(report_id):
    service, owner = context()
    item = service.repository.get_report(owner, report_id)
    response = Response(item["markdown"], mimetype="text/markdown; charset=utf-8")
    response.headers["Content-Disposition"] = f'attachment; filename="explore-{report_id}.md"'
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "sandbox; default-src 'none'"
    response.headers["Cache-Control"] = "no-store"
    return response
