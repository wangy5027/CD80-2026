"""HTTP contracts with local authentication and an isolated model server."""
import io
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from backend.app import create_app


class ModelFixture(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *_args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        type(self).calls.append(body)
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        for delta in ['LangGraph ', '连接测试已通过。']:
            event = {'choices': [{'delta': {'content': delta}, 'finish_reason': None}]}
            self.wfile.write(('data: '+json.dumps(event, ensure_ascii=False)+'\n\n').encode())
            self.wfile.flush()
        self.wfile.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
        self.wfile.flush()


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = ThreadingHTTPServer(('127.0.0.1', 0), ModelFixture)
        cls.thread = threading.Thread(target=cls.model.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.model.shutdown()
        cls.model.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ficagent-langgraph-http-')
        root = Path(self.temp.name)
        dist = root/'dist'
        (dist/'assets').mkdir(parents=True)
        (dist/'index.html').write_text('<!doctype html><title>Website fixture</title>')
        (dist/'assets/app.js').write_text('window.websiteFixture=true')
        self.config = {
            'TESTING': True, 'DATA_DIR': str(root/'data'), 'FRONTEND_DIST': str(dist),
            'DESKTOP_PORT': 8893, 'IMPORT_LEGACY_HISTORY': False,
            'GITEE_AI_BASE_URL': f'http://127.0.0.1:{self.model.server_port}/v1',
            'GITEE_AI_API_KEY': 'private-fixture-key', 'GITEE_AI_MODEL': 'GLM-5.3',
        }
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.register(self.client, 'alice@example.invalid')
        ModelFixture.calls.clear()

    def tearDown(self):
        self.app.extensions['workbench_service'].close()
        self.temp.cleanup()

    def register(self, client, email):
        response = client.post('/desktop/api/register', json={
            'name': email.split('@')[0], 'email': email, 'password': 'test-only-123',
        })
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])

    def test_auth_runtime_and_request_boundary(self):
        session = self.client.get('/desktop/api/session').json
        self.assertTrue(session['authenticated'])
        self.assertEqual(session['models'][0]['id'], 'GLM-5.3')
        runtime = self.client.get('/desktop/api/runtime')
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json['provider'], 'Gitee AI')
        self.assertTrue(runtime.json['configured'])
        self.assertNotIn('private-fixture-key', runtime.get_data(as_text=True))
        self.assertEqual(self.app.test_client().get('/desktop/api/runtime').status_code, 401)
        self.assertEqual(self.client.post('/desktop/api/projects', json={'name': 'blocked'}, headers={'Origin': 'https://invalid.example'}).status_code, 403)
        self.assertEqual(self.client.get('/desktop/health', headers={'Host': 'invalid.example'}).status_code, 403)
        self.assertEqual(self.client.get('/unrecognized-upstream-path').status_code, 404)
        self.assertEqual(self.client.get('/desktop/api/login').status_code, 405)
        self.assertEqual(self.client.post('/desktop/api/logout').status_code, 200)
        self.assertFalse(self.client.get('/desktop/api/session').json['authenticated'])
        self.assertEqual(self.client.get('/desktop/api/state').status_code, 401)
        self.assertEqual(self.client.post('/desktop/api/login', json={'email': 'alice@example.invalid', 'password': 'test-only-123'}).status_code, 200)

    def test_account_isolation_files_projects_schedules_and_static(self):
        project = self.client.post('/desktop/api/projects', json={'name': '独立项目'}).json
        schedule = self.client.post('/desktop/api/schedules', json={'title': '独立日程', 'at': '2030-01-01T10:00:00+08:00'}).json
        upload = self.client.post('/desktop/api/files', data={'file': (io.BytesIO('上传正文'.encode()), 'fixture.txt'), 'projectId': project['id']})
        self.assertEqual(upload.status_code, 201)
        file_id = upload.json['id']
        preview = self.client.get(f'/desktop/api/files/{file_id}')
        self.assertEqual(preview.data.decode(), '上传正文')
        preview.close()
        download = self.client.get(f'/desktop/api/files/{file_id}?download=1')
        self.assertIn('attachment', download.headers['Content-Disposition'])
        download.close()
        bob = self.app.test_client()
        self.register(bob, 'bob@example.invalid')
        state = bob.get('/desktop/api/state').json
        for key in ['projects', 'files', 'schedules', 'tasks']:
            self.assertEqual(state[key], [])
        self.assertEqual(bob.get(f'/desktop/api/files/{file_id}').status_code, 404)
        self.assertEqual(bob.delete(f'/desktop/api/projects/{project["id"]}').status_code, 200)
        self.assertEqual(self.client.get('/desktop/api/state').json['projects'][0]['id'], project['id'])
        self.assertEqual(self.client.patch(f'/desktop/api/schedules/{schedule["id"]}', json={'done': True}).status_code, 200)
        self.assertEqual(self.client.delete(f'/desktop/api/files/{file_id}').status_code, 200)
        self.assertEqual(self.client.delete(f'/desktop/api/schedules/{schedule["id"]}').status_code, 200)
        self.assertEqual(self.client.delete(f'/desktop/api/projects/{project["id"]}').status_code, 200)
        for path in ['/', '/desktop/', '/desktop/assets/app.js']:
            response = self.client.get(path, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_graph_stream_and_multiturn_history_survive_app_restart(self):
        first = self.client.post('/desktop/api/chat', json={'message': '第一轮测试', 'agentId': 'assistant', 'modelId': 'GLM-5.3'}, buffered=True)
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        self.assertIn('event: token', first.get_data(as_text=True))
        self.assertIn('event: done', first.get_data(as_text=True))
        task_id = self.client.get('/desktop/api/state').json['tasks'][0]['id']
        first.close()
        self.app.extensions['workbench_service'].close()
        self.app = create_app(self.config)
        self.client = self.app.test_client()
        self.client.post('/desktop/api/login', json={'email': 'alice@example.invalid', 'password': 'test-only-123'})
        second = self.client.post('/desktop/api/chat', json={'taskId': task_id, 'message': '第二轮测试', 'agentId': 'assistant', 'modelId': 'GLM-5.3'}, buffered=True)
        self.assertEqual(second.status_code, 200)
        self.assertIn('event: done', second.get_data(as_text=True))
        self.assertEqual(len(ModelFixture.calls), 2)
        contents = [item.get('content') for item in ModelFixture.calls[-1]['messages']]
        self.assertIn('第一轮测试', contents)
        self.assertIn('LangGraph 连接测试已通过。', contents)
        self.assertIn('第二轮测试', contents)
        second.close()
        self.assertEqual(self.client.get(f'/desktop/api/tasks/{task_id}').status_code, 200)
        self.assertEqual(self.client.delete(f'/desktop/api/tasks/{task_id}').status_code, 200)

    def test_json_limit_is_enforced_before_account_mutation(self):
        response = self.client.post('/desktop/api/projects', data=json.dumps({'name': 'x'*(2*1024*1024)}), content_type='application/json')
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json['error'], 'JSON 请求过大，上限为 2 MiB。')
        self.assertEqual(self.client.get('/desktop/api/state').json['projects'], [])

    def test_file_limit_keeps_upload_specific_message(self):
        response = self.client.post('/desktop/api/files', data=b'',
            content_type='multipart/form-data; boundary=fixture',
            environ_overrides={'CONTENT_LENGTH': str(36 * 1024 * 1024)})
        self.assertEqual(response.status_code, 413)
        self.assertIn('30 MB', response.json['error'])
        self.assertEqual(self.client.get('/desktop/api/state').json['files'], [])


if __name__ == '__main__':
    unittest.main()
