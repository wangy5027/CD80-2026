"""Saving new strategies never performs validation or waits for a backtest."""
import unittest
from unittest.mock import patch

import test_workshop_customization as helpers
from test_workshop_customization import ready_reply, request_key


class SavedRulesTests(unittest.TestCase):
    setUp = helpers.CustomizationTests.setUp
    tearDown = helpers.CustomizationTests.tearDown
    body = helpers.CustomizationTests.body
    confirm_body = helpers.CustomizationTests.confirm_body
    validation = staticmethod(helpers.CustomizationTests.validation)

    def test_prompt_records_are_private_and_retries_have_separate_ids(self):
        from backend.app.modules.strategy_workshop.model_traces import TraceFactory, read_trace
        factory = TraceFactory(self.service.repository, self.owner, 'rewrite')
        first = factory()
        first('request', {'model': 'fixture-only', 'messages': [{'role': 'system', 'content': 'actual system prompt'}]})
        first('finished', 'failed')
        second = factory()
        second('request', {'messages': [{'role': 'system', 'content': 'retry prompt'}]})
        second('finished', 'completed')
        record = read_trace(self.service.repository, self.owner, factory.ids[1])
        self.assertEqual(record['retryOf'], factory.ids[0])
        self.assertEqual(record['detail']['value']['request']['messages'][0]['content'], 'retry prompt')
        with self.assertRaises(Exception) as caught:
            read_trace(self.service.repository, 'other-owner', factory.ids[0])
        self.assertEqual(caught.exception.status, 404)

    def test_generated_rules_are_saved_before_any_validation_and_are_owner_scoped(self):
        self.service.rewriting.prepare = lambda *args: ready_reply()
        with patch('backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy') as validate, \
                patch.object(self.service.market, 'prepare') as market:
            doc = self.service.customization(self.owner, self.session['id'], self.body())
            app = doc['savedPersonalApp']
            self.assertEqual(app['verification']['status'], 'unverified')
            self.assertEqual(self.service.list_personal_apps(self.owner), [app])
            detail = self.service.personal_app(self.owner, app['id'])
            self.assertIsNone(detail['run'])
            self.assertEqual(detail['definition']['customization']['sourceCode'], doc['customization']['sourceCode'])
            validate.assert_not_called()
            market.assert_not_called()
        self.assertEqual(self.service.list_personal_apps('another-owner'), [])
        with self.assertRaises(Exception) as caught:
            self.service.personal_app('another-owner', app['id'])
        self.assertEqual(caught.exception.status, 404)
        saved = self.service.save_session_app(self.owner, doc['id'], {'idempotencyKey': request_key()})
        self.assertEqual(saved['app']['id'], app['id'])
        self.assertEqual(len(self.service.list_personal_apps(self.owner)), 1)

    def test_open_saved_rules_validate_then_backtest_and_edit_preserves_old_definition(self):
        self.service.rewriting.prepare = lambda *args: ready_reply()
        doc = self.service.customization(self.owner, self.session['id'], self.body())
        app = doc['savedPersonalApp']
        original = self.service.repository.read(self.owner, 'personal_app', app['id'])
        opened = self.service.personal_app_session(self.owner, app['id'], {'idempotencyKey': request_key()})
        with patch('backend.app.modules.strategy_workshop.custom_sandbox.validate_strategy', self.validation):
            confirmed = self.service.customization_confirm(self.owner, opened['id'], self.confirm_body(opened))
        self.assertEqual(confirmed['customization']['status'], 'validated')
        self.assertEqual(self.service.personal_app(self.owner, app['id'])['app']['verification']['status'], 'unverified')
        selected = self.service.selection(self.owner, confirmed['id'], {**self.body(confirmed), 'decision': 'accept'})
        self.assertEqual(selected['savedPersonalApp']['id'], app['id'])
        preflight = self.service.preflight(self.owner, selected['id'], {'selectionRevision': 1,
            'symbol': '510300.SH', 'startDate': '', 'endDate': '', 'initialCash': 100000, 'idempotencyKey': request_key()})
        with patch.object(self.service.worker, 'start'):
            run = self.service.create_run(self.owner, {'sessionId': selected['id'], 'preflightId': preflight['id'],
                'configDigest': preflight['configDigest'], 'mode': 'diagnostic', 'diagnosticAcknowledged': True,
                'idempotencyKey': request_key()})
        for state in ('validating', 'running'):
            self.service.repository.transition(self.owner, run['id'], state)
        self.service.repository.transition(self.owner, run['id'], 'succeeded', result={'report_eligible': False})
        self.assertEqual(self.service.personal_app(self.owner, app['id'])['app']['verification']['status'], 'passed')
        self.assertEqual(self.service.repository.read(self.owner, 'personal_app', app['id']), original)
        edit = self.service.personal_app_session(self.owner, app['id'], {'idempotencyKey': request_key(), 'mode': 'edit'})
        self.assertEqual(edit['editingPersonalAppId'], app['id'])
        self.assertNotIn('savedPersonalApp', edit)
        revised = self.service.customization(self.owner, edit['id'], self.body(edit))
        self.assertNotEqual(revised['savedPersonalApp']['id'], app['id'])
        self.assertEqual(revised['savedPersonalApp']['previousAppId'], app['id'])
        self.assertEqual(revised['savedPersonalApp']['verification']['status'], 'unverified')
        self.assertEqual(self.service.repository.read(self.owner, 'personal_app', app['id']), original)
