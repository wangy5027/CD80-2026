"""Authenticated browser downloads for every skill-pool asset kind."""
import io
import tempfile
import unittest
import zipfile
from urllib.parse import unquote
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.workbench.standard_exports import archive_filename


class StandardExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='standard-export-routes-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'CAPABILITY_SKIP_PUBLIC_SEED': True, 'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'export@example.invalid', 'password': 'test-password'})

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def test_requires_login_and_valid_source_selection(self):
        path = '/desktop/api/skill-pool/standard-export'
        self.assertEqual(self.app.test_client().get(path, query_string={'kind': 'app', 'source': 'builtin', 'id': 'explore'}).status_code, 401)
        for query in ({}, {'kind': 'other', 'source': 'personal', 'id': 'x'},
                      {'kind': 'app', 'source': 'builtin', 'id': 'x' * 257}):
            self.assertEqual(self.client.get(path, query_string=query).status_code, 400)
        self.assertEqual(self.client.post(path).status_code, 405)

    def test_download_headers_and_exact_dispatch_are_read_only(self):
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as archive:
            archive.writestr('readme.md', '# 示例资产')
        for kind in ('app', 'skill', 'tool'):
            module = 'agent_platform' if kind == 'app' else 'capability_library'
            source = 'asset' if kind == 'app' else 'personal'
            with self.subTest(kind=kind), patch('backend.app.modules.' + module + '.standard_exports.export_standard',
                                               return_value=('示例：完整资产', data.getvalue())) as export:
                response = self.client.get('/desktop/api/skill-pool/standard-export',
                    query_string={'kind': kind, 'source': source, 'id': 'exact-asset', 'versionId': 'exact-version'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.mimetype, 'application/zip')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                self.assertIn("filename*=UTF-8''示例：完整资产.zip", unquote(response.headers['Content-Disposition']))
                self.assertEqual(response.data, data.getvalue())
                args = export.call_args.args
                self.assertEqual(args[-3:], (source, 'exact-asset', 'exact-version'))

    def test_filename_keeps_chinese_and_removes_path_and_header_controls(self):
        self.assertEqual(archive_filename('完整中文应用'), '完整中文应用.zip')
        value = archive_filename('../a\\b\r\n"test?')
        self.assertNotIn('/', value)
        self.assertNotIn('\\', value)
        self.assertNotIn('\n', value)
        self.assertNotIn('"', value)
        self.assertTrue(value.endswith('.zip'))
        self.assertLessEqual(len(archive_filename('资' * 500).encode()), 239)


if __name__ == '__main__':
    unittest.main()
