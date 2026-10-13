"""Explicit public entry grants are scoped and frozen by the server."""

import json
import unittest
from unittest.mock import Mock
from uuid import uuid4

from backend.app.modules.explore.public_entries import parse_entry_catalog, entries_for_question
from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError


class PublicEntryTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'keywords': ['西南财经大学', '西财'],
                      'urls': ['https://www.swufe.edu.cn/', 'http://news.swufe.edu.cn/']}]

    def test_scope_and_deduplication(self):
        catalog = parse_entry_catalog(json.dumps(self.rows * 2))
        self.assertEqual(entries_for_question(catalog, '西南财经大学官网表述'), self.rows[0]['urls'])
        self.assertEqual(entries_for_question(catalog, '西财设立的学院'), self.rows[0]['urls'])
        self.assertEqual(entries_for_question(catalog, '华为近期新闻'), [])

    def test_rejects_nonpublic_or_model_constructed_entry(self):
        for url in ['http://127.0.0.1/', 'http://198.18.0.1/', 'http://localhost/',
                    'https://official.example/?q=private', 'https://official.example/private',
                    'https://official.example/#private', 'https://secret@official.example/',
                    'http://official.example:8894/', 'file:///tmp/private']:
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_entry_catalog([{'keywords': ['西财'], 'urls': [url]}])

    def test_invalid_or_unbounded_catalog(self):
        for value in ['broken-json', {}, [{}], [{'keywords': [''], 'urls': []}], self.rows * 21]:
            with self.subTest(value=str(value)[:30]), self.assertRaises(ValueError):
                parse_entry_catalog(value)

    def make_service(self):
        service = ExploreService.__new__(ExploreService)
        service.public_entry_catalog = parse_entry_catalog(self.rows)
        service.max_concurrent = 2
        service.testing = False
        service._start = Mock()
        service.repository = Mock()
        service.repository.create_run.side_effect = lambda owner, request, run, *a, **k: (run, True)
        return service

    def test_server_freezes_entries_at_run_creation(self):
        service = self.make_service()
        run = service.create_run('fixture', 'session', {'module': 'event',
            'question': '西南财经大学官网表述', 'idempotency_key': str(uuid4())})
        self.assertEqual(run['authorized_public_urls'], self.rows[0]['urls'])
        service.public_entry_catalog.clear()
        self.assertEqual(run['authorized_public_urls'], self.rows[0]['urls'])
        service._start.assert_called_once()

    def test_browser_cannot_grant_arbitrary_urls(self):
        service = self.make_service()
        with self.assertRaises(GatewayError) as caught:
            service.create_run('fixture', 'session', {'module': 'event',
                'question': '西南财经大学官网表述', 'idempotency_key': str(uuid4()),
                'authorized_public_urls': ['https://attacker.example/private']})
        self.assertEqual(caught.exception.code, 'explore_unsupported_parameter')
        service.repository.create_run.assert_not_called()

    def test_confirmed_topic_changes_reauthorize_in_same_transition(self):
        for action in ('clarify', 'confirm', 'supply_input'):
            for topic in ('西南财经大学官网表述', '华为近期新闻'):
                with self.subTest(action=action, topic=topic):
                    service = self.make_service()
                    run = {'id': 'r', 'session_id': 's', 'module': 'event',
                           'status': 'awaiting_review', 'materials': [{'id': 'S1'}],
                           'review': {'revision': 1, 'interpreted_question': topic},
                           'authorized_public_urls': ['https://previous.example/']}
                    service.repository.get_run.return_value = run

                    def apply(owner, run_id, action, key, body, mutate, limit):
                        mutate(run)
                        return run, True

                    service.repository.apply_review_action.side_effect = apply
                    if action == 'clarify':
                        service.clarify('fixture', 'r', {'idempotency_key': str(uuid4()),
                            'revision': 1, 'question': topic})
                    elif action == 'confirm':
                        service.confirm('fixture', 'r', {'idempotency_key': str(uuid4()),
                            'revision': 1, 'source_ids': ['S1']})
                    else:
                        run.update(module='history', status='awaiting_input')

                        def transition(*args, fields, **kwargs):
                            run.update(fields)
                            return run

                        service.repository.transition.side_effect = transition
                        service.supply_input('fixture', 'r', {'judgment': topic})
                    self.assertEqual(run['authorized_public_urls'],
                                     self.rows[0]['urls'] if '西南财经大学' in topic else [])
                    self.assertNotEqual(run.get('execution_id'), None)


if __name__ == '__main__':
    unittest.main()
