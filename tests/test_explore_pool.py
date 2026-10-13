"""Session file/context contract with explicit local provider doubles; no network."""
import threading
import time
import unittest
from uuid import uuid4

from tests import test_explore as fixtures
from backend.app.modules.explore.repository import ExploreRepository

BASE = fixtures.BASE


class ExplorePoolTests(unittest.TestCase):
    setUp = fixtures.ExploreTests.setUp
    tearDown = fixtures.ExploreTests.tearDown
    session = fixtures.ExploreTests.session
    submit = fixtures.ExploreTests.submit

    def ready(self, sid, module='event', **extra):
        run, body = self.submit(sid, module, **extra)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            run = self.client.get(BASE + '/runs/' + run['id']).json['run']
            if run['status'] in {'succeeded', 'partial'}:
                return run, body
            self.assertNotIn(run['status'], {'failed', 'awaiting_review', 'awaiting_clarification', 'cancelled'})
            time.sleep(.01)
        self.fail('Clear test question did not directly complete.')

    def files(self, sid):
        response = self.client.get(BASE + f'/sessions/{sid}/files')
        self.assertEqual(response.status_code, 200)
        return response.json['files']

    def file_path(self, sid, fid):
        return BASE + f'/sessions/{sid}/files/{fid}'

    def set_context(self, sid, module, ids, mode='manual'):
        body = {'module': module, 'mode': mode, 'file_ids': ids}
        response = self.client.put(BASE + f'/sessions/{sid}/context', json=body)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['context'], body)
        return body

    def test_materials_and_report_pool_are_real_saved_downloads_and_reads_are_inert(self):
        sid = self.session()
        run, body = self.ready(sid)
        pool = self.files(sid)
        self.assertEqual(len(pool), len(run['materials']) + 1)
        self.assertEqual({item['id'] for item in pool},
                         {item['file_id'] for item in run['materials']} | {run['report']['file_id']})
        counts = len(self.provider.calls), len(self.provider.clarify_calls), len(self.search.calls), len(self.news.calls)
        for meta in pool:
            self.assertTrue(meta['name'].endswith('.md'))
            path = self.file_path(sid, meta['id'])
            detail = self.client.get(path).json['file']
            download = self.client.get(path + '/download')
            self.assertEqual(download.status_code, 200)
            self.assertEqual(download.get_data(as_text=True), detail['content'])
            self.assertEqual(download.headers['X-Content-Type-Options'], 'nosniff')
            self.assertIn('attachment', download.headers['Content-Disposition'])
            self.assertIn("default-src 'none'", download.headers['Content-Security-Policy'])
            if meta['kind'] == 'report':
                self.assertEqual(detail['content'], run['report']['markdown'])
                self.assertIn('AI 生成', detail['content'])
            elif meta['origin'] == 'web_search':
                self.assertIn('非全文', detail['content'])
                self.assertIn('公开网页摘要', detail['content'])
        replay = self.client.post(BASE + f'/sessions/{sid}/runs', json=body)
        self.assertEqual(replay.json['run']['id'], run['id'])
        self.assertEqual(len(self.files(sid)), len(pool))
        self.assertEqual(counts, (len(self.provider.calls), len(self.provider.clarify_calls), len(self.search.calls), len(self.news.calls)))

    def test_file_and_context_endpoints_enforce_owner_and_session_scope(self):
        sid = self.session()
        run, _ = self.ready(sid)
        fid = run['report']['file_id']
        other_sid = self.session()
        other = self.app.test_client()
        self.assertEqual(other.post('/desktop/api/register', json={'email': 'pool-other@example.test', 'password': 'long-password'}).status_code, 200)
        for client, target in ((other, sid), (self.client, other_sid)):
            path = self.file_path(target, fid)
            for method, suffix, payload in (('GET', '', None), ('GET', '/download', None),
                                            ('PATCH', '', {'name': 'foreign.md'}), ('DELETE', '', None)):
                response = client.open(path + suffix, method=method, json=payload)
                self.assertEqual(response.status_code, 404)
            if client is self.client:
                response = client.put(BASE + f'/sessions/{target}/context', json={'module': 'event', 'mode': 'manual', 'file_ids': [fid]})
                self.assertEqual(response.status_code, 404)
        for suffix in ('/files', '/context', '/context?module=event'):
            self.assertEqual(other.get(BASE + f'/sessions/{sid}' + suffix).status_code, 404)
            self.assertEqual(self.app.test_client().get(BASE + f'/sessions/{sid}' + suffix).status_code, 401)
        invalid = self.client.post(BASE + f'/sessions/{other_sid}/runs', json={
            'module': 'event', 'question': '具体政策事件', 'idempotency_key': str(uuid4()),
            'context_mode': 'manual', 'file_ids': [fid]})
        self.assertEqual(invalid.status_code, 404)
        self.assertEqual(self.files(other_sid), [])

    def test_manual_selection_is_module_specific_and_excludes_unselected_ancestors(self):
        sid = self.session()
        root, _ = self.ready(sid)
        source = next(item for item in root['materials'] if item['kind'] == 'web_search')
        selection = self.set_context(sid, 'relation', [source['file_id']])
        run, _ = self.ready(sid, 'relation', source_report_id=root['report']['id'])
        self.assertEqual(run['context_selection'], selection)
        self.assertEqual(run['context_snapshot'], [])
        for payload in (self.provider.clarify_calls[-1], self.provider.calls[-1]):
            self.assertEqual(payload['context'], [])
            self.assertEqual([item['file_id'] for item in payload['context_files']], [source['file_id']])
            self.assertEqual(payload['module'], 'relation')
        contexts = self.client.get(BASE + f'/sessions/{sid}/context').json['contexts']
        self.assertEqual(contexts['event']['mode'], 'auto')
        self.assertEqual(contexts['relation'], selection)
        empty = self.set_context(sid, 'event', [])
        run, _ = self.ready(sid, source_report_id=root['report']['id'])
        self.assertEqual(run['context_files'], [])
        self.assertEqual(run['context_selection'], empty)
        self.assertEqual(self.provider.calls[-1]['context'], [])
        self.assertEqual(self.provider.calls[-1]['context_files'], [])

    def test_auto_context_stays_on_branch_and_omits_removed_files_from_model(self):
        sid = self.session()
        root, _ = self.ready(sid)
        branch_a, _ = self.ready(sid, 'relation', source_report_id=root['report']['id'])
        branch_b, _ = self.ready(sid, 'relation', source_report_id=root['report']['id'])
        removed_id = root['report']['file_id']
        self.assertEqual(self.client.delete(self.file_path(sid, removed_id)).status_code, 200)
        run, _ = self.ready(sid, source_report_id=branch_a['report']['id'])
        ids = {item['file_id'] for item in run['context_files']}
        self.assertIn(branch_a['report']['file_id'], ids)
        self.assertNotIn(branch_b['report']['file_id'], ids)
        self.assertNotIn(removed_id, ids)
        self.assertNotIn(branch_b['id'], {item['source_run_id'] for item in run['context_files']})
        self.assertLessEqual(len(ids), 8)
        self.assertEqual(self.provider.calls[-1]['context'], [])
        self.assertEqual(self.provider.calls[-1]['context_files'], run['context_files'])
        preview = self.client.get(BASE + f"/sessions/{sid}/context?module=event&source_report_id={branch_a['report']['id']}").json['context']
        self.assertEqual(set(preview['preview_file_ids']), ids)

    def test_selected_snapshot_frozen_before_execution_survives_rename_and_remove(self):
        sid = self.session()
        parent, _ = self.ready(sid, question='新能源政策影响光伏产业。' * 70)
        fid = parent['report']['file_id']
        self.set_context(sid, 'relation', [fid])
        self.provider.started, self.provider.release = threading.Event(), threading.Event()
        try:
            run, _ = self.submit(sid, 'relation', source_report_id=parent['report']['id'])
            self.assertTrue(self.provider.started.wait(3))
            snapshot = self.client.get(BASE + f"/runs/{run['id']}").json['run']['context_files']
            self.assertIn('条件性分析', snapshot[0]['content_excerpt'])
            self.assertIn('AI', snapshot[0]['evidence_role'])
            self.assertLessEqual(snapshot[0]['used_chars'], 1000)
            self.assertIn('extraction_rule', snapshot[0])
            path = self.file_path(sid, fid)
            self.assertEqual(self.client.patch(path, json={'name': '新的研究报告名称.md'}).status_code, 200)
            self.assertEqual(self.client.delete(path).status_code, 200)
            self.provider.release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                restored = self.client.get(BASE + f"/runs/{run['id']}").json['run']
                if restored.get('report'):
                    break
                time.sleep(.01)
            self.assertIn(restored['status'], {'partial', 'succeeded'})
            self.assertEqual(restored['context_files'], snapshot)
            self.assertEqual(restored['report']['context_files'], snapshot)
            self.assertEqual(self.provider.calls[-1]['context_files'], snapshot)
            self.assertIn(snapshot[0]['name'], restored['report']['markdown'])
            self.assertEqual(self.client.get(BASE + f'/sessions/{sid}/context').json['contexts']['relation']['file_ids'], [])
        finally:
            self.provider.release.set()

    def test_startup_backfill_and_tombstones_are_idempotent(self):
        sid = self.session()
        run, _ = self.ready(sid)
        report_id, fid = run['report']['id'], run['report']['file_id']
        # Simulate a pre-pool installation while preserving its completed report.
        with self.service.repository.transaction() as db:
            db.execute('DELETE FROM explore_files')
        recovered = ExploreRepository(self.temp.name)
        initial = recovered.list_files(self.owner, sid)
        self.assertEqual(len(initial), len(run['materials']) + 1)
        recovered.delete_file(self.owner, sid, fid)
        recovered_again = ExploreRepository(self.temp.name)
        self.assertNotIn(fid, {item['id'] for item in recovered_again.list_files(self.owner, sid)})
        self.assertEqual(recovered_again.get_report(self.owner, report_id)['id'], report_id)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(self.search.calls), 1)
        self.assertEqual(len(recovered_again.list_files(self.owner, sid)), len(initial) - 1)

    def test_cancel_and_delete_prevent_late_report_file_insert(self):
        sid = self.session()
        self.provider.started, self.provider.release = threading.Event(), threading.Event()
        try:
            run, _ = self.submit(sid)
            self.assertTrue(self.provider.started.wait(3))
            self.assertEqual(self.client.post(BASE + f"/runs/{run['id']}/cancel", json={}).status_code, 200)
            self.provider.release.set()
            time.sleep(.05)
            self.assertFalse(any(item['kind'] == 'report' for item in self.files(sid)))
            self.assertEqual(self.client.get(BASE + f"/runs/{run['id']}").json['run']['status'], 'cancelled')
            self.provider.started, self.provider.release = threading.Event(), threading.Event()
            self.submit(sid)
            self.assertTrue(self.provider.started.wait(3))
            self.assertEqual(self.client.delete(BASE + f'/sessions/{sid}').status_code, 200)
            self.provider.release.set()
            time.sleep(.05)
            with self.service.repository.transaction() as db:
                for table in ('runs', 'reports', 'explore_files', 'context_preferences'):
                    self.assertEqual(db.execute(f'SELECT count(*) FROM {table} WHERE session_id=?', (sid,)).fetchone()[0], 0)
        finally:
            self.provider.release.set()

    def test_context_validation_and_download_filename_header_safety(self):
        sid = self.session()
        run, _ = self.ready(sid)
        fid = run['report']['file_id']
        path = self.file_path(sid, fid)
        for name in ('../report.md', 'x\r\nInjected: yes', 'javascript:evil.md', '<script>.md'):
            self.assertEqual(self.client.patch(path, json={'name': name}).status_code, 400)
        for body in ({'module': 'bad', 'mode': 'manual', 'file_ids': []},
                     {'module': 'event', 'mode': 'manual', 'file_ids': [fid, fid]},
                     {'module': 'event', 'mode': 'auto', 'file_ids': [fid]},
                     {'module': 'event', 'mode': 'manual', 'file_ids': ['missing']},
                     {'module': 'event', 'mode': 'manual', 'file_ids': [str(uuid4()) for _ in range(9)]}):
            self.assertIn(self.client.put(BASE + f'/sessions/{sid}/context', json=body).status_code, {400, 404})
        self.assertEqual(self.client.patch(path, json={'name': '中文报告.md'}).status_code, 200)
        download = self.client.get(path + '/download')
        self.assertIn("filename*=UTF-8''%E4%B8%AD", download.headers['Content-Disposition'])
        self.assertEqual(self.client.get(path).json['file']['content'], run['report']['markdown'])


if __name__ == '__main__':
    unittest.main()
