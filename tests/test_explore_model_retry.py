"""Bounded writer transport retries: no network or private account data."""

import threading
import time
import unittest
from unittest.mock import Mock

from backend.app.modules.explore.service import ExploreService
from backend.app.modules.workbench.errors import GatewayError


class RetryProvider:
    model = 'retry-fixture'

    def __init__(self, attempts):
        self.attempts = attempts
        self.calls = 0
        self.closed = 0

    def json_tokens(self, messages, model, stopped, on_response):
        attempt = self.attempts[self.calls]
        self.calls += 1
        try:
            for item in attempt:
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.closed += 1


class WriterRetryTests(unittest.TestCase):
    def service(self, attempts):
        service = ExploreService.__new__(ExploreService)
        service.provider = RetryProvider(attempts)
        service.repository = Mock()
        service.repository.validate_memory.return_value = None
        control = {'stop': threading.Event(), 'timed_out': threading.Event(),
                   'deadline': time.monotonic() + 120, 'evidence_guard': Mock()}
        return service, control

    @staticmethod
    def timeout():
        return GatewayError('timeout', 504, 'model_stream_timeout')

    def invoke(self, service, control):
        return service._invoke_model({'task': 'research_report', 'stage': 'write'},
                                     'fixture', control)

    def test_discards_partial_json_and_retries_once(self):
        service, control = self.service([
            ['{"stale":', self.timeout()], ['{"fresh":true}']])
        self.assertEqual(self.invoke(service, control), {'fresh': True})
        self.assertEqual(service.provider.calls, 2)
        self.assertEqual(service.provider.closed, 2)
        self.assertEqual(control['writer_model_calls'], 2)
        self.assertEqual(len(control['model_retries']), 1)
        self.assertEqual(control['model_retries'][0]['stage'], 'write')
        self.assertEqual(control['evidence_guard'].call_count, 3)

    def test_second_failure_cannot_retry_again(self):
        service, control = self.service([[self.timeout()], [self.timeout()]])
        with self.assertRaises(GatewayError):
            self.invoke(service, control)
        self.assertEqual(service.provider.calls, 2)

    def test_research_retry_uses_same_run_allowance(self):
        service, control = self.service([[self.timeout()]])
        control['model_retries'] = [{'stage': 'research'}]
        with self.assertRaises(GatewayError):
            self.invoke(service, control)
        self.assertEqual(service.provider.calls, 1)

    def test_no_retry_without_time_or_deadline(self):
        for deadline in (None, time.monotonic() + 10):
            with self.subTest(deadline=deadline):
                service, control = self.service([[self.timeout()]])
                control['deadline'] = deadline
                with self.assertRaises(GatewayError):
                    self.invoke(service, control)
                self.assertEqual(service.provider.calls, 1)
                self.assertNotIn('model_retries', control)

    def test_auth_and_invalid_output_are_not_transport_retries(self):
        for failure in (GatewayError('denied', 403, 'model_http_error'),
                        GatewayError('bad JSON', 502, 'explore_invalid_model_output')):
            with self.subTest(code=failure.code):
                service, control = self.service([[failure]])
                with self.assertRaises(GatewayError):
                    self.invoke(service, control)
                self.assertEqual(service.provider.calls, 1)
                self.assertNotIn('model_retries', control)

    def test_cancel_or_global_timeout_stops_before_retry(self):
        for field, code in (('stop', 'explore_cancelled'),
                            ('timed_out', 'explore_timeout')):
            with self.subTest(field=field):
                service, control = self.service([[self.timeout()]])
                once = service._invoke_model_once

                def fail_and_stop(*args):
                    try:
                        return once(*args)
                    finally:
                        control[field].set()

                service._invoke_model_once = fail_and_stop
                with self.assertRaises(GatewayError) as caught:
                    self.invoke(service, control)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(service.provider.calls, 1)
                self.assertNotIn('model_retries', control)

    def test_deleted_evidence_prevents_retry(self):
        service, control = self.service([[self.timeout()]])
        control['evidence_guard'].side_effect = [None, GatewayError('deleted', 409)]
        with self.assertRaises(GatewayError) as caught:
            self.invoke(service, control)
        self.assertEqual(str(caught.exception), 'deleted')
        self.assertEqual(service.provider.calls, 1)
        self.assertNotIn('model_retries', control)

    def test_execution_change_does_not_restart_request(self):
        service, control = self.service([[self.timeout()]])
        control.update(owner='fixture', run_id='fixture-run', execution_id='old-execution')
        service.repository.transition.side_effect = [{}, None]
        with self.assertRaises(GatewayError) as caught:
            self.invoke(service, control)
        self.assertEqual(caught.exception.code, 'explore_cancelled')
        self.assertTrue(control['stop'].is_set())
        self.assertEqual(service.provider.calls, 1)
        self.assertNotIn('model_retries', control)


if __name__ == '__main__':
    unittest.main()
