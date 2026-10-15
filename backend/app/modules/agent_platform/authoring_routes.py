"""Authenticated authoring commands; reads never start or resume execution."""
from flask import Blueprint, current_app, jsonify, request

from ..capability_library.contracts import CapabilityError
from ..workbench.routes import json_body

authoring_sessions = Blueprint('authoring_sessions', __name__, url_prefix='/desktop/api/authoring-sessions')


def context():
    w = current_app.extensions['workbench_service']
    return w.authoring, w.authenticate(request.headers.get('Cookie', ''))['user']['id']


@authoring_sessions.errorhandler(CapabilityError)
def error_response(error):
    return jsonify(error=str(error), code=error.code, details=error.details), error.status


@authoring_sessions.route('', methods=['GET', 'POST'])
@authoring_sessions.route('/', methods=['GET', 'POST'])
def collection():
    service, owner = context()
    if request.method == 'GET':
        return jsonify(service.listing(owner, request.args.get('conversationId'), request.args.get('assetId'),
                                       request.args.get('cursor'), request.args.get('limit', 20)))
    return jsonify(service.create(owner, json_body(max_bytes=9 * 1024 * 1024), request.headers.get('Idempotency-Key'))), 201


@authoring_sessions.get('/<session_id>')
def detail(session_id):
    service, owner = context()
    return jsonify(service.detail(owner, session_id))


@authoring_sessions.get('/<session_id>/model-traces/<trace_id>')
def model_trace(session_id, trace_id):
    service, owner = context()
    response = jsonify(service.model_trace(owner, session_id, trace_id))
    response.cache_control.no_store = True
    return response


@authoring_sessions.post('/<session_id>/<operation>')
def command(session_id, operation):
    service, owner = context()
    result = service.command(owner, session_id, operation, json_body(max_bytes=9 * 1024 * 1024),
                             request.headers.get('Idempotency-Key'))
    return jsonify(result), 200 if operation == 'confirm' and result['session']['phase'] == 'completed' else 202
