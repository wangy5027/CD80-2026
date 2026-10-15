"""Cross-module authentication and fresh-data contract, with no model calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.app import create_app


class IntegratedRoutesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='integrated-routes-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                              'GITEE_AI_API_KEY': 'fixture-only', 'IMPORT_LEGACY_HISTORY': False,
                              'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.workbench = self.app.extensions['workbench_service']

    def tearDown(self):
        self.workbench.close()
        self.temp.cleanup()

    def client(self, email):
        client = self.app.test_client()
        response = client.post('/desktop/api/register', json={'email': email, 'password': 'test-only-123'})
        self.assertEqual(response.status_code, 200)
        return client

    def test_one_login_and_logout_cover_platform_and_all_three_apps(self):
        routes = ['/state', '/agents', '/asset-apps', '/explore/sessions',
                  '/strategy-workshop/sessions', '/app-workshop/sessions/current']
        client = self.app.test_client()
        for path in routes:
            self.assertEqual(client.get('/desktop/api' + path).status_code, 401, path)
        client = self.client('one-login@example.invalid')
        with patch.object(self.workbench.runtime.provider, 'events', side_effect=AssertionError('read must not invoke model')):
            for _ in range(2):
                for path in routes:
                    self.assertEqual(client.get('/desktop/api' + path).status_code, 200, path)
        self.assertEqual(client.get('/desktop/api/explore/sessions').json['sessions'], [])
        self.assertIsNone(client.get('/desktop/api/app-workshop/sessions/current').json['session'])
        self.assertEqual(client.get('/desktop/api/agents').json['strategyApps'], [])
        self.assertEqual(client.get('/desktop/api/asset-apps').json['items'], [])
        client.post('/desktop/api/logout')
        for path in routes:
            self.assertEqual(client.get('/desktop/api' + path).status_code, 401, path)

    def test_cross_account_research_and_platform_files_do_not_leak(self):
        import io
        first = self.client('first@example.invalid')
        second = self.client('second@example.invalid')
        created = first.post('/desktop/api/explore/sessions', json={'title': 'private-research'})
        self.assertEqual(created.status_code, 201)
        session_id = created.json['session']['id']
        uploaded = first.post('/desktop/api/files', data={'file': (io.BytesIO(b'private-test-only'), 'private.txt')})
        self.assertEqual(uploaded.status_code, 201)
        for path in [f'/explore/sessions/{session_id}', f'/explore/sessions/{session_id}/files',
                     f'/explore/sessions/{session_id}/memory', f'/files/{uploaded.json["id"]}']:
            self.assertEqual(second.get('/desktop/api' + path).status_code, 404, path)
        self.assertEqual(second.get('/desktop/api/explore/sessions').json['sessions'], [])
        self.assertEqual(first.get('/desktop/api/explore/sessions').json['sessions'][0]['id'], session_id)


if __name__ == '__main__':
    unittest.main()
