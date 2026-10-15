"""Model protocol recovery and owner-scoped records with isolated local fixtures."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.sub_agent.model import Model
from backend.app.modules.sub_agent.diagnostics import ModelTrace
from backend.app.modules.sub_agent.store import Store, TERMINAL
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider
from backend.app.modules.workbench.tool_registry import ToolResult, ToolSpec


class ScriptedProvider:
    model = 'local-fixture'

    def __init__(self, replies):
        self.replies = iter(replies)
        self.requests = []

    def tokens(self, messages, model, stopped, on_response):
        self.requests.append(messages)
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            yield from reply(stopped)
        else:
            yield reply if isinstance(reply, str) else json.dumps(reply)


class ModelRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-model-records-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                              'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'trace@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.s = self.app.extensions['sub_agents']
        self.w = self.app.extensions['workbench_service']

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def submit(self, replies, message='local model record fixture'):
        self.provider = ScriptedProvider(replies)
        self.s.model = Model(self.provider)
        result = self.client.post('/desktop/api/conversations/new/messages', json={'message': message},
                                  headers={'Idempotency-Key': str(time.monotonic_ns())})
        self.assertEqual(result.status_code, 202, result.json)
        return result.json

    def wait(self, rid):
        until = time.monotonic() + 15
        while time.monotonic() < until:
            run = self.s.store.run(self.owner, rid)
            if run['status'] in TERMINAL and (self.owner, rid) not in self.s.controls:
                return run
            time.sleep(.01)
        self.fail('fixture did not finish')

    def record(self, call):
        response = self.client.get(call['traceUrl'])
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        return response.json

    def test_json_recovery_retains_actual_request_response_and_does_not_replay_tool(self):
        executed = []
        self.w.runtime.tools.register(ToolSpec('trace_count', 'count fixture', {'type': 'object'},
            lambda args, context: (executed.append(args) or ToolResult({'count': 1}))))
        run = self.submit([
            {'mode': 'delegate', 'goal': 'count fixture'},
            {'type': 'tool', 'name': 'trace_count', 'arguments': {}},
            '\n  {"type":',
            {'type': 'finish', 'answer': 'counted', 'artifactIds': []},
            {'decision': 'accept', 'answer': 'counted once'},
        ], 'fixture credentials api_key=not-a-real-key')
        done = self.wait(run['id'])
        self.assertEqual(done['status'], 'succeeded', done)
        self.assertEqual(len(executed), 1)
        self.assertEqual(done['correctionsUsed'], 1)
        failed = next(c for c in done['modelCalls'] if c['status'] == 'failed')
        self.assertEqual(failed['errorCode'], 'MODEL_JSON_INVALID')
        record = self.record(failed)
        detail = record['detail']['value']
        self.assertEqual(detail['response'], {'text': '\n  {"type":', 'complete': True})
        self.assertIn('parsePosition', record['error']['details'])
        self.assertEqual(record['error']['details']['parsePosition'], len(detail['response']['text']))
        self.assertNotIn('not-a-real-key', json.dumps(record))
        self.assertIn('messages', detail['request'])
        retry = next(c for c in done['modelCalls'] if c.get('retryOf') == failed['id'])
        retry_request = self.record(retry)['detail']['value']['request']
        payload = json.loads(retry_request['messages'][1]['content'])
        self.assertEqual(payload['validationFeedback']['code'], 'MODEL_JSON_INVALID')
        self.assertEqual(sum(s['status'] == 'succeeded' for s in payload['steps']), 1)
        self.assertNotIn('request', failed)
        download = self.client.get(failed['traceUrl'] + '?download=1')
        self.assertIn('attachment;', download.headers['Content-Disposition'])

    def test_schema_recovery_and_format_budget_exhaustion(self):
        done = self.wait(self.submit([{'mode': 'answer', 'answer': 99},
                                     {'mode': 'answer', 'answer': 'valid'}])['id'])
        self.assertEqual(done['status'], 'succeeded')
        first = self.record(done['modelCalls'][0])
        self.assertEqual(first['error']['code'], 'MODEL_SCHEMA_INVALID')
        self.assertEqual(first['detail']['value']['parsed']['answer'], 99)
        exhausted = self.wait(self.submit(['{bad'] * 4)['id'])
        self.assertEqual(exhausted['status'], 'failed')
        self.assertEqual(exhausted['rounds'], 4)
        self.assertEqual(exhausted['correctionsUsed'], 3)
        self.assertIn('3次纠错', exhausted['answer'])
        self.assertTrue(all(c['traceAvailable'] for c in exhausted['modelCalls']))

    def test_last_remaining_correction_and_model_call_limit(self):
        with patch.object(self.s, 'kick'):
            run = self.submit(['{bad', {'type': 'finish', 'answer': 'done'}])
        self.s.mutate(self.owner, run['id'], lambda r: r.update(correctionsUsed=2))
        control = {'stop': threading.Event(), 'response': None}
        value = self.s._call('act', self.owner, run['id'], {}, control, time.monotonic() + 10)
        self.assertEqual(value['type'], 'finish')
        saved = self.s.store.run(self.owner, run['id'])
        self.assertEqual(saved['correctionsUsed'], 3)
        self.assertEqual(len(self.provider.requests), 2)
        self.s.mutate(self.owner, run['id'], lambda r: r.update(rounds=32))
        with self.assertRaises(GatewayError) as caught:
            self.s._call('act', self.owner, run['id'], {}, control, time.monotonic() + 10)
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(self.provider.requests), 2)

    def test_partial_response_preserved_without_retrying_connection_error(self):
        def partial(stopped):
            yield '{"mode":'
            raise GatewayError('fixture connection ended', 502, 'fixture_connection_error')
        done = self.wait(self.submit([partial])['id'])
        self.assertEqual(len(self.provider.requests), 1)
        record = self.record(done['modelCalls'][0])
        self.assertEqual(record['detail']['value']['response'], {'text': '{"mode":', 'complete': False})
        self.assertEqual(record['error']['code'], 'fixture_connection_error')

    def test_auth_error_after_timeout_is_not_misreported_as_timeout(self):
        done = self.wait(self.submit([GatewayError('fixture timeout', 504, 'agent_model_deadline'),
                                     GatewayError('fixture authentication failed', 401, 'fixture_auth_error')])['id'])
        self.assertEqual(len(self.provider.requests), 2)
        self.assertEqual(done['correctionsUsed'], 1)
        self.assertIn('authentication failed', done['answer'])
        self.assertNotIn('持续超时', done['answer'])
        self.assertFalse(self.s.queue(self.owner, done['conversationId'])['paused'])

    def test_trace_is_scoped_to_owner_and_exact_run_and_old_records_are_not_fabricated(self):
        first = self.wait(self.submit([{'mode': 'answer', 'answer': 'first'}])['id'])
        second = self.wait(self.submit([{'mode': 'answer', 'answer': 'second'}])['id'])
        call = first['modelCalls'][0]
        wrong_run = '/desktop/api/sub-agent-runs/' + second['id'] + '/model-calls/' + call['id']
        self.assertEqual(self.client.get(wrong_run).status_code, 404)
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'other-trace@example.invalid', 'password': 'test-password'})
        self.assertEqual(other.get(call['traceUrl']).status_code, 404)
        self.assertEqual(other.get(call['traceUrl'] + '?download=1').status_code, 404)
        self.assertEqual(self.app.test_client().get(call['traceUrl']).status_code, 401)
        with self.s.store.transaction() as db:
            db.execute("DELETE FROM records WHERE owner=? AND kind='model_trace' AND id=?", (self.owner, call['id']))
        self.assertEqual(self.client.get(call['traceUrl']).status_code, 404)

    def test_restart_closes_running_request_record_without_inventing_response(self):
        with patch.object(self.s, 'kick'):
            run = self.submit([])
        call = {'id': 'interrupted-call', 'stage': 'plan', 'status': 'running'}
        self.s.mutate(self.owner, run['id'], lambda r: r.update(status='planning', modelCalls=[call]))
        with self.s.store.transaction() as db:
            self.s.store.put(db, self.owner, 'model_trace', call['id'],
                {'runId': run['id'], 'callId': call['id'], 'status': 'running', 'detail': {'request': 'saved fixture'}})
        recovered = Store(self.temp.name)
        saved = recovered.run(self.owner, run['id'])
        trace = recovered.model_trace(self.owner, run['id'], call['id'])
        self.assertEqual(saved['status'], 'interrupted')
        self.assertEqual(saved['modelCalls'][0]['status'], 'interrupted')
        self.assertEqual(trace['status'], 'interrupted')
        self.assertEqual(trace['error']['code'], 'PROCESS_INTERRUPTED')
        self.assertEqual(trace['detail'], {'request': 'saved fixture'})
        self.assertNotIn('completedAt', trace)

    def test_final_review_verdict_overrides_model_suggestion_in_timeline_and_record(self):
        done = self.wait(self.submit([
            {'mode': 'delegate', 'goal': 'fixture', 'requiredCapabilities': ['missing-fixture']},
            {'type': 'finish', 'answer': 'proposal'},
            {'decision': 'accept', 'answer': 'model accepted'},
            '{bad', '{bad', '{bad',
        ])['id'])
        review = next(c for c in done['modelCalls'] if c['stage'] == 'review')
        self.assertEqual(review['modelDecision'], 'accept')
        self.assertEqual(review['finalDecision'], 'revise')
        self.assertNotEqual(review['summary'], '验收通过')
        record = self.record(review)
        self.assertEqual(record['detail']['value']['parsed']['decision'], 'accept')
        self.assertEqual(record['platformDecision']['decision'], 'revise')
        self.assertIn('missing-fixture', done['answer'])
        self.assertIn('此前已记录', done['answer'])

    def test_invalid_approval_does_not_execute_code_or_double_count_corrections(self):
        with patch.object(self.s.executor, 'execute') as execute:
            done = self.wait(self.submit([
                {'mode': 'delegate', 'goal': 'fixture code', 'allowTemporaryCode': True},
                {'type': 'code', 'reason': 'fixture', 'code': 'def run(payload): return {}'},
                '{bad', '{bad', '{bad', '{bad',
            ])['id'])
        execute.assert_not_called()
        self.assertEqual(done['status'], 'failed')
        self.assertEqual(done['correctionsUsed'], 3)
        self.assertEqual(len([c for c in done['modelCalls'] if c['stage'] == 'approve']), 4)
        self.assertEqual(done['steps'][0]['status'], 'failed')
        self.assertEqual(done['approvals'], [])

    def test_cancelled_response_is_recorded_but_never_executed_or_retried(self):
        entered = threading.Event()
        def cancelled(stopped):
            entered.set()
            while not stopped.wait(.01):
                pass
            yield '{bad'
        run = self.submit([cancelled])
        self.assertTrue(entered.wait(5))
        current = self.s.store.run(self.owner, run['id'])
        result = self.client.post('/desktop/api/sub-agent-runs/' + run['id'] + '/cancel',
            json={'revision': current['revision']}, headers={'Idempotency-Key': 'cancel-trace'})
        self.assertEqual(result.status_code, 200)
        done = self.wait(run['id'])
        self.assertEqual(done['status'], 'cancelled')
        self.assertEqual(len(self.provider.requests), 1)
        self.assertEqual(done['correctionsUsed'], 0)
        self.assertEqual(done['steps'], [])
        self.assertEqual(self.record(done['modelCalls'][0])['status'], 'cancelled')


class TransportTraceTests(unittest.TestCase):
    def test_actual_transport_records_whitelisted_metadata_without_headers_or_reasoning(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('x-request-id', 'fixture-request-id')
                self.send_header('set-cookie', 'fixture-cookie-secret')
                self.end_headers()
                chunk = {'id': 'fixture-response-id', 'choices': [{'delta': {
                    'reasoning_content': 'private-reasoning-fixture',
                    'content': '{"mode":"answer","answer":"fixture"}'}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 12, 'completion_tokens': 8, 'total_tokens': 20}}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode())
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = GiteeProvider({'TESTING': True, 'GITEE_AI_BASE_URL': f'http://127.0.0.1:{server.server_port}/v1',
                'GITEE_AI_API_KEY': 'fixture-credential-secret', 'GITEE_AI_MODEL': 'fixture-transport'})
            trace = ModelTrace()
            with patch.dict('os.environ', {'NO_PROXY': '127.0.0.1,localhost', 'no_proxy': '127.0.0.1,localhost'}):
                Model(provider).call('plan', {'message': 'fixture'}, threading.Event(), lambda _: None, on_trace=trace.receive)
            record = trace.record()
            self.assertEqual(record['request']['messages'], requests[0]['messages'])
            self.assertEqual(record['transport']['finishReason'], 'stop')
            self.assertEqual(record['transport']['requestId'], 'fixture-request-id')
            self.assertEqual(record['transport']['usage']['total_tokens'], 20)
            self.assertEqual(record['transport']['responseFormat'], {'type': 'json_object'})
            serialized = json.dumps(record)
            for secret in ('fixture-credential-secret', 'fixture-cookie-secret', 'private-reasoning-fixture'):
                self.assertNotIn(secret, serialized)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
