"""Synchronous, authenticated original-file storage and read-only download."""
import io

from flask import Blueprint, current_app, jsonify, request, send_file

from .contracts import CapabilityError, fail
from .packages import MAX_PACKAGE
from . import portable_assets as service

portable_assets = Blueprint('portable_assets', __name__, url_prefix='/desktop/api/portable-assets')


@portable_assets.errorhandler(CapabilityError)
def error_response(error):
    return jsonify({'error': str(error), 'code': error.code, 'details': error.details}), error.status


def context():
    workbench = current_app.extensions['workbench_service']
    owner = workbench.authenticate(request.headers.get('Cookie', ''))['user']['id']
    return current_app.extensions['capability_library'], owner


@portable_assets.post('/imports')
def import_original():
    library, owner = context()
    upload = request.files.get('file')
    if upload is None:
        fail('INVALID_REQUEST', '请选择要保存的原件。')
    raw = upload.stream.read(MAX_PACKAGE + 1)
    value = service.import_original(library, owner, raw, upload.filename or 'original.bin',
                                    request.form.get('kind'), request.headers.get('Idempotency-Key'),
                                    request.form.get('name'))
    return jsonify(value), 201


@portable_assets.get('')
@portable_assets.get('/')
def catalog():
    library, owner = context()
    return jsonify({'items': service.listing(library, owner, request.args.get('kind'))})


@portable_assets.get('/<asset_id>')
def detail(asset_id):
    library, owner = context()
    return jsonify(service.detail(library, owner, asset_id))


@portable_assets.get('/<asset_id>/export')
def export(asset_id):
    library, owner = context()
    value, raw = service.original(library, owner, asset_id)
    response = send_file(io.BytesIO(raw), as_attachment=True, download_name=value['originalName'],
                         mimetype='application/octet-stream')
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Content-Security-Policy': "sandbox; default-src 'none'"})
    return response
