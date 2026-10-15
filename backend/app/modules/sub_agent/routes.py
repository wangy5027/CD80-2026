"""Authenticated submission and observation; model approvals remain internal."""
import io
import json
from flask import Blueprint, current_app, jsonify, request, Response, send_file
from ..workbench.routes import json_body
from ..workbench.errors import GatewayError

sub_agents = Blueprint('sub_agents', __name__, url_prefix='/desktop/api')


def context():
    w = current_app.extensions['workbench_service']
    return current_app.extensions['sub_agents'], w.authenticate(request.headers.get('Cookie', ''))['user']['id']


@sub_agents.post('/conversations/<cid>/messages')
def submit(cid):
    service, owner = context()
    return jsonify(service.submit(owner, cid, json_body(), request.headers.get('Idempotency-Key'))), 202


@sub_agents.get('/conversations/<cid>/queue')
def queue(cid):
    service, owner = context()
    return jsonify(service.queue(owner, cid))


@sub_agents.post('/conversations/<cid>/queue/resume')
def resume(cid):
    service, owner = context()
    service.task(owner, cid)
    return jsonify(service.command(owner, cid, None, 'resume', json_body(), request.headers.get('Idempotency-Key')))


@sub_agents.post('/conversations/<cid>/queue/items/<rid>/withdraw')
def withdraw(cid, rid):
    service, owner = context()
    return jsonify(service.command(owner, cid, rid, 'withdraw', json_body(), request.headers.get('Idempotency-Key')))


@sub_agents.get('/sub-agent-runs/<rid>')
def run(rid):
    service, owner = context()
    return jsonify(service.store.run(owner, rid))


@sub_agents.post('/sub-agent-runs/<rid>/cancel')
def cancel(rid):
    service, owner = context()
    run = service.store.run(owner, rid)
    return jsonify(service.command(owner, run['conversationId'], rid, 'cancel', json_body(), request.headers.get('Idempotency-Key')))


@sub_agents.post('/sub-agent-runs/<rid>/questions/<question_id>/answer')
def answer(rid, question_id):
    service, owner = context()
    run = service.store.run(owner, rid)
    return jsonify(service.command(owner, run['conversationId'], rid, 'answer', {**json_body(), 'questionId': question_id}, request.headers.get('Idempotency-Key')))


@sub_agents.get('/sub-agent-runs/<rid>/events')
def events(rid):
    service, owner = context()
    try:
        after = int(request.headers.get('Last-Event-ID') or request.args.get('after', '0'))
        if after < 0:
            raise ValueError()
    except ValueError:
        raise GatewayError('事件游标无效。', 400) from None
    values = service.store.events(owner, rid, after)
    text = ''.join(f"id: {v['eventId']}\nevent: {v['type']}\ndata: {json.dumps(v, ensure_ascii=False)}\n\n" for v in values)
    return Response(text + ': reconnect for updates\n\n', mimetype='text/event-stream', headers={'Cache-Control': 'no-store'})


@sub_agents.get('/sub-agent-runs/<rid>/model-calls/<call_id>')
def model_call(rid, call_id):
    service, owner = context()
    record = service.store.model_trace(owner, rid, call_id)
    if 'download' in request.args:
        response = send_file(io.BytesIO(json.dumps(record, ensure_ascii=False, indent=2).encode('utf-8')),
            mimetype='application/json', download_name='model-request-' + call_id + '.json', as_attachment=True)
    else:
        response = jsonify(record)
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                             'Content-Security-Policy': "sandbox; default-src 'none'"})
    return response


@sub_agents.get('/sub-agent-runs/<rid>/execution-evidence/<step_id>')
def execution_evidence(rid, step_id):
    service, owner = context()
    response = jsonify(service.execution_evidence(owner, rid, step_id))
    response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
    return response


@sub_agents.get('/sub-agent-artifacts/<aid>')
def artifact(aid):
    service, owner = context()
    meta, data = service.store.artifact(owner, aid)
    safe_inline = meta['mediaType'] in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'} and 'download' not in request.args
    response = send_file(io.BytesIO(data), mimetype=meta['mediaType'], download_name=meta['name'], as_attachment=not safe_inline)
    response.headers.update({'Content-Security-Policy': "sandbox; default-src 'none'", 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})
    return response
