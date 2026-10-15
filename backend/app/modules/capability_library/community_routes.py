"""Authenticated community routes; official capability catalog remains separate."""
from flask import Blueprint, request

from ..workbench.errors import GatewayError
from ..workbench.routes import json_body
from . import community
from .contracts import CapabilityError, fail
from .routes import action, context, error_response, output, page

community_capabilities = Blueprint('community_capabilities', __name__, url_prefix='/desktop/api/community/capabilities')
community_capabilities.register_error_handler(CapabilityError, error_response)
community_capabilities.register_error_handler(GatewayError, error_response)


def _body(allowed):
    payload = json_body()
    if not isinstance(payload, dict) or set(payload) - set(allowed):
        fail('INVALID_REQUEST', '社区请求包含不支持的字段。')
    return payload


@community_capabilities.route('', methods=['GET', 'POST'])
def releases():
    lib, owner = context()
    if request.method == 'GET':
        values = community.list_releases(lib, owner, mine=request.args.get('mine') == 'true',
                                         kind=request.args.get('kind'), query=request.args.get('q', ''))
        return output(page(values, owner))
    payload = _body({'assetId', 'versionId', 'title', 'description', 'snapshotDigest', 'confirmSharing'})
    return action(lib, owner, payload, lambda: community.publish(lib, owner, payload), 201)


@community_capabilities.post('/preview')
def preview():
    lib, owner = context()
    payload = _body({'assetId', 'versionId', 'title', 'description'})
    return action(lib, owner, payload, lambda: community.preview(lib, owner, payload), 200)


@community_capabilities.get('/<release_id>')
def release(release_id):
    lib, owner = context()
    with lib.store.lock:
        return output(community.view(lib, owner, community.get(lib, owner, release_id)))


@community_capabilities.post('/<release_id>/copy')
def copy_release(release_id):
    lib, owner = context()
    payload = _body({'confirmDuplicate', 'ensureOwned'})
    return action(lib, owner, payload, lambda: community.copy_release(lib, owner, release_id, payload), 201)


@community_capabilities.post('/<release_id>/withdraw')
def withdraw(release_id):
    lib, owner = context()
    payload = _body({'revision'})
    return action(lib, owner, payload, lambda: community.withdraw(lib, owner, release_id, payload), 200)
