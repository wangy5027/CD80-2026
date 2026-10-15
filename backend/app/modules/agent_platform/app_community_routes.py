"""Authenticated public catalog for explicitly shared modern Apps."""
from flask import Blueprint, current_app, jsonify, request

from ..capability_library.contracts import CapabilityError, fail
from ..capability_library.routes import page
from ..workbench.routes import json_body

community_apps = Blueprint('community_apps', __name__, url_prefix='/desktop/api/community/apps')


@community_apps.errorhandler(CapabilityError)
def error_response(error):
    return jsonify({'error': str(error), 'code': error.code, 'details': error.details}), error.status


def context():
    workbench = current_app.extensions['workbench_service']
    owner = workbench.authenticate(request.headers.get('Cookie', ''))['user']['id']
    return current_app.extensions['app_community'], owner


def body(allowed):
    value = json_body()
    if set(value) - set(allowed):
        fail('INVALID_REQUEST', '公开应用请求包含不支持的字段。')
    return value


def key():
    return request.headers.get('Idempotency-Key')


@community_apps.route('', methods=['GET', 'POST'])
@community_apps.route('/', methods=['GET', 'POST'])
def releases():
    service, owner = context()
    if request.method == 'GET':
        return jsonify(page(service.listing(owner), owner))
    payload = body({'appId', 'versionId', 'title', 'description', 'snapshotDigest', 'confirmSharing'})
    return jsonify(service.publish(owner, payload, key())), 201


@community_apps.post('/preview')
def preview():
    service, owner = context()
    return jsonify(service.preview(owner, body({'appId', 'versionId', 'title', 'description'})))


@community_apps.get('/<release_id>')
def detail(release_id):
    service, owner = context()
    return jsonify(service.detail(owner, release_id))


@community_apps.post('/<release_id>/copy')
def copy(release_id):
    service, owner = context()
    return jsonify(service.copy(owner, release_id, body(set()), key())), 201


@community_apps.post('/<release_id>/withdraw')
def withdraw(release_id):
    service, owner = context()
    return jsonify(service.withdraw(owner, release_id, body({'revision'}), key()))
