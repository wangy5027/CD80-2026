"""Authenticated authoring, reuse and local-platform publication endpoints."""
import io

from flask import Blueprint, current_app, jsonify, request, send_file
from ..workbench.routes import json_body
from ..capability_library.contracts import canonical, CapabilityError
from .capture import preview

evolution = Blueprint('evolution', __name__, url_prefix='/desktop/api/evolution')


@evolution.errorhandler(CapabilityError)
def capability_error(error):
    return jsonify({'error': str(error), 'code': error.code, 'details': error.details}), error.status


def context():
    w = current_app.extensions['workbench_service']
    owner = w.authenticate(request.headers.get('Cookie', ''))['user']['id']
    return current_app.extensions['evolution'], owner


def key():
    return request.headers.get('Idempotency-Key')


def download(package):
    result = send_file(io.BytesIO(canonical(package).encode()), mimetype='application/json',
                       as_attachment=True, download_name='infinity-application.json')
    result.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return result


@evolution.get('/developer-kit')
def developer_kit():
    context()
    from .developer_kit import build
    output = io.BytesIO(build())
    response = send_file(output, mimetype='application/zip', as_attachment=True, download_name='infinity-developer-kit.zip')
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return response


@evolution.route('/recipes', methods=['GET', 'POST'])
def recipes():
    service, owner = context()
    return jsonify({'items': service.recipes(owner)}) if request.method == 'GET' else (jsonify(service.create(owner, json_body(), key())), 201)


@evolution.get('/recipes/<rid>')
def recipe(rid):
    service, owner = context()
    return jsonify(service.recipe(owner, rid))


@evolution.post('/recipes/import')
def import_recipe():
    service, owner = context()
    return jsonify(service.import_recipe(owner, json_body(), key())), 201


@evolution.post('/recipes/<rid>/fork')
def fork(rid):
    service, owner = context()
    return jsonify(service.fork(owner, rid, json_body(), key())), 201


@evolution.post('/recipes/<rid>/run')
def run(rid):
    service, owner = context()
    return jsonify(service.run(owner, rid, json_body(), key())), 202


@evolution.get('/recipes/<rid>/export')
def export(rid):
    service, owner = context()
    return download(service.portable(owner, rid))


@evolution.post('/captures/preview')
def capture():
    service, owner = context()
    return jsonify(preview(service, owner, json_body()))


@evolution.route('/publications', methods=['GET', 'POST'])
def publications():
    service, owner = context()
    return jsonify({'items': service.publications(owner)}) if request.method == 'GET' else (jsonify(service.publish(owner, json_body(), key())), 201)


@evolution.post('/publications/preview')
def publication_preview():
    service, owner = context()
    return jsonify(service.preview_publication(owner, json_body()))


@evolution.get('/publications/<pid>/export')
def publication_export(pid):
    service, owner = context()
    return download(service.publication_package(owner, pid))


@evolution.post('/publications/<pid>/copy')
def copy_publication(pid):
    service, owner = context()
    return jsonify(service.copy_publication(owner, pid, json_body(), key())), 201


@evolution.post('/publications/<pid>/withdraw')
def withdraw(pid):
    service, owner = context()
    return jsonify(service.withdraw(owner, pid, json_body(), key()))
