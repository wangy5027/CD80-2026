"""Authenticated API for the unified reusable application model."""
import io

from flask import Blueprint, current_app, jsonify, request, send_file

from ..capability_library.contracts import CapabilityError, fail
from ..workbench.routes import json_body as bounded_json_body

asset_apps = Blueprint('asset_apps', __name__, url_prefix='/desktop/api/asset-apps')


def json_body():
    # An 8 MiB portable definition/package needs room for its request envelope.
    return bounded_json_body(max_bytes=9 * 1024 * 1024)


@asset_apps.errorhandler(CapabilityError)
def error_response(error):
    return jsonify({'error': str(error), 'code': error.code, 'details': error.details}), error.status


def context():
    w = current_app.extensions['workbench_service']
    owner = w.authenticate(request.headers.get('Cookie', ''))['user']['id']
    return current_app.extensions['asset_apps'], owner


def key():
    return request.headers.get('Idempotency-Key')


@asset_apps.route('', methods=['GET', 'POST'])
@asset_apps.route('/', methods=['GET', 'POST'])
def catalog():
    service, owner = context()
    if request.method == 'GET':
        return jsonify({'items': service.listing(owner)})
    return jsonify(service.save(owner, json_body(), key())), 201


@asset_apps.get('/<app_id>')
def detail(app_id):
    service, owner = context()
    return jsonify(service.detail(owner, app_id, request.args.get('versionId')))


@asset_apps.post('/preview')
def preview():
    service, owner = context()
    return jsonify(service.preview(owner, json_body()))


@asset_apps.post('/creator')
def creator():
    service, owner = context()
    return jsonify(service.creator(owner, json_body(), key()))


@asset_apps.get('/<app_id>/creator-traces/<trace_id>')
def creator_trace(app_id, trace_id):
    service, owner = context()
    response = jsonify(service.creator_trace(owner, app_id, trace_id))
    response.cache_control.no_store = True
    return response


@asset_apps.get('/legacy')
def legacy():
    from .asset_creator import legacy_items
    service, owner = context()
    return jsonify({'items': legacy_items(service, owner, current_app.extensions['evolution'])})


@asset_apps.post('/<app_id>/extract')
def extract(app_id):
    from .asset_creator import extract_nodes
    service, owner = context()
    return jsonify(extract_nodes(service, owner, app_id, json_body()))


@asset_apps.post('/<app_id>/verify')
def verify(app_id):
    service, owner = context()
    return jsonify(service.verify(owner, app_id, json_body(), key())), 202


@asset_apps.post('/<app_id>/enable')
def enable(app_id):
    service, owner = context()
    return jsonify(service.enable(owner, app_id, json_body(), key()))


@asset_apps.post('/<app_id>/bindings')
def bindings(app_id):
    service, owner = context()
    return jsonify(service.configure(owner, app_id, json_body(), key()))


@asset_apps.route('/<app_id>/runs', methods=['GET', 'POST'])
def runs(app_id):
    service, owner = context()
    if request.method == 'GET':
        return jsonify({'items': service.runs(owner, app_id)})
    return jsonify(service.create_run(owner, app_id, json_body(), key())), 202


@asset_apps.get('/runs/<run_id>')
def run(run_id):
    service, owner = context()
    return jsonify(service.run(owner, run_id))


@asset_apps.post('/runs/<run_id>/resume')
def resume(run_id):
    service, owner = context()
    return jsonify(service.resume(owner, run_id, json_body(), key())), 202


@asset_apps.post('/runs/<run_id>/cancel')
def cancel(run_id):
    service, owner = context()
    return jsonify(service.cancel(owner, run_id, json_body(), key())), 202


@asset_apps.get('/artifacts/<artifact_id>')
def artifact(artifact_id):
    service, owner = context()
    meta, data = service.store.artifact(owner, artifact_id)
    response = send_file(io.BytesIO(data), as_attachment=True, download_name=meta['name'], mimetype=meta['mediaType'])
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Content-Security-Policy': "sandbox; default-src 'none'"})
    return response


@asset_apps.get('/<app_id>/export')
def export(app_id):
    service, owner = context()
    response = jsonify(service.export(owner, app_id, request.args.get('versionId')))
    response.headers['Content-Disposition'] = 'attachment; filename="application.json"'
    return response


@asset_apps.post('/import/preview')
def import_preview():
    service, owner = context()
    return jsonify(service.import_preview(owner, json_body()))


@asset_apps.post('/import/file-preview')
def import_file_preview():
    from .asset_imports import MAX_UPLOAD, preview_file
    service, owner = context()
    if request.content_length is not None and request.content_length > MAX_UPLOAD + 64 * 1024:
        fail('LIMIT_EXCEEDED', '上传请求超过 9 MiB。', 413)
    request.max_content_length = MAX_UPLOAD + 64 * 1024
    if len(request.files) != 1 or len(request.files.getlist('file')) != 1:
        fail('INVALID_REQUEST', '请选择一个 APP JSON、Python 或 ZIP 文件。')
    upload = request.files['file']
    return jsonify(preview_file(service, owner, upload.stream.read(MAX_UPLOAD + 1), upload.filename))


@asset_apps.get('/import/template.zip')
def import_template():
    from .asset_imports import template_zip
    context()
    response = send_file(io.BytesIO(template_zip()), as_attachment=True, download_name='cd80-app-source-template.zip', mimetype='application/zip')
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return response


def _import_download(data, filename, mimetype):
    context()
    response = send_file(io.BytesIO(data), as_attachment=True, download_name=filename, mimetype=mimetype)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return response


@asset_apps.get('/import/schema.json')
def import_schema():
    from .app_import_contract import definition_schema
    from ..capability_library.contracts import canonical
    return _import_download(canonical(definition_schema()).encode('utf-8'), 'cd80.app-2.schema.json', 'application/json')


@asset_apps.get('/import/specification.json')
def import_specification():
    from .app_import_contract import specification
    from ..capability_library.contracts import canonical
    return _import_download(canonical(specification()).encode('utf-8'), 'cd80-app-import.openapi.json', 'application/json')


@asset_apps.get('/import/template.json')
def import_template_json():
    from .asset_imports import template_definition
    from ..capability_library.contracts import canonical
    return _import_download(canonical(template_definition()).encode('utf-8'), 'cd80-app-template.json', 'application/json')


@asset_apps.get('/import/example.py')
def import_example_python():
    from .app_import_contract import python_example
    return _import_download(python_example().encode('utf-8'), 'cd80-app-example.py', 'text/x-python')


@asset_apps.get('/import/data-interface.zip')
def import_data_interface():
    from .app_import_contract import data_interface_zip
    return _import_download(data_interface_zip(), 'cd80-data-interface.zip', 'application/zip')


@asset_apps.post('/import')
def import_app():
    service, owner = context()
    return jsonify(service.import_app(owner, json_body(), key())), 201
