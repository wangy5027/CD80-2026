"""Persistent budget boundaries, concurrency and interruption without model calls."""
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import requests

from backend.app.modules.agent_platform.authoring_budget import BudgetContext, current_budget, new_round
from backend.app.modules.agent_platform.authoring_store import AuthoringStore
from backend.app.modules.agent_platform.repository import Repository
from backend.app.modules.capability_library.contracts import CapabilityError
from backend.app.modules.workbench.errors import GatewayError
from backend.app.modules.workbench.graph_runtime import GiteeProvider


class AuthoringBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-authoring-budget-')
        self.store = AuthoringStore(Repository(self.temp.name))
        self.round = new_round('session')
        with self.store.transaction() as db:
            self.store.put(db, 'budget_rounds', 'owner', self.round)

    def tearDown(self):
        self.temp.cleanup()

    def context(self, clock=None):
        options = {'clock': clock} if clock is not None else {}
        return BudgetContext(self.store, 'owner', 'session', self.round['id'], 'attempt', **options)

    def current(self):
        return self.store.read('budget_rounds', 'owner', self.round['id'])

    def test_32nd_request_finishes_and_33rd_is_blocked_failed_requests_still_count(self):
        with self.context(clock=lambda: 0).bind() as budget:
            self.assertIs(current_budget(), budget)
            for index in range(32):
                request_id = budget.begin_request('provider', 'model')
                budget.finish_request(request_id, succeeded=index == 31)
            budget.check()
            with self.assertRaises(CapabilityError) as caught:
                budget.begin_request('provider', 'model')
            self.assertEqual(caught.exception.code, 'BUDGET_EXHAUSTED')
        self.assertIsNone(current_budget())
        self.assertEqual(self.current()['consumed']['modelRequests'], 32)
        records = self.store.listing('model_requests', 'owner', 'session')
        self.assertEqual(sum(row['state'] == 'failed' for row in records), 31)
        self.assertEqual(sum(row['state'] == 'succeeded' for row in records), 1)

    def test_parallel_request_reservations_cannot_overspend_shared_allowance(self):
        with self.context(clock=lambda: 0).bind() as budget:
            def submit(_):
                try:
                    return budget.begin_request('parallel-provider', 'model')
                except CapabilityError as error:
                    self.assertEqual(error.code, 'BUDGET_EXHAUSTED')
                    return None
            with ThreadPoolExecutor(max_workers=8) as pool:
                results = list(pool.map(submit, range(40)))
        self.assertEqual(len([result for result in results if result]), 32)
        self.assertEqual(self.current()['consumed']['modelRequests'], 32)
        self.assertEqual(len(self.store.listing('model_requests', 'owner', 'session')), 32)

    def test_second_repair_can_complete_but_third_cannot_start(self):
        with self.context(clock=lambda: 0).bind() as budget:
            budget.repair('candidate1', ['evidence1'])
            budget.repair('candidate2', ['evidence2'])
            request_id = budget.begin_request('repair', 'model')
            budget.finish_request(request_id, succeeded=True)
            budget.check()
            with self.assertRaises(CapabilityError) as caught:
                budget.repair('candidate3', ['evidence3'])
            self.assertEqual(caught.exception.code, 'BUDGET_EXHAUSTED')
        self.assertEqual(self.current()['consumed']['repairRounds'], 2)
        self.assertEqual(len(self.store.listing('repair_rounds', 'owner', 'session')), 2)

    def test_activity_accumulates_but_waiting_outside_binding_does_not(self):
        clock = [10.0]
        with self.context(clock=lambda: clock[0]).bind():
            clock[0] += 2
        self.assertEqual(self.current()['consumed']['activeMilliseconds'], 2000)
        clock[0] += 300
        with self.context(clock=lambda: clock[0]).bind():
            clock[0] += 3
        self.assertEqual(self.current()['consumed']['activeMilliseconds'], 5000)
        self.assertNotIn('activeSince', self.current())

    def test_deadline_aborts_inflight_response_and_prevents_more_work(self):
        with self.store.transaction() as db:
            budget = self.store.get(db, 'budget_rounds', 'owner', self.round['id'])
            budget['consumed']['activeMilliseconds'] = 599970
            self.store.put(db, 'budget_rounds', 'owner', budget)
        aborted = threading.Event()
        response = type('InFlightResponse', (), {'abort': lambda _: aborted.set()})()
        with self.context().bind() as budget:
            budget.response(response)
            self.assertTrue(aborted.wait(1), 'deadline did not abort the in-flight response')
            with self.assertRaises(CapabilityError) as caught:
                budget.begin_request('provider', 'model')
            self.assertEqual(caught.exception.code, 'AUTHORING_DEADLINE')
        self.assertEqual(self.current()['consumed']['activeMilliseconds'], 600000)
        self.assertEqual(self.current()['consumed']['modelRequests'], 0)

    def test_new_round_never_carries_unused_balance(self):
        previous = self.current()
        previous['consumed'].update(modelRequests=31, repairRounds=1, activeMilliseconds=1234)
        following = new_round('session', previous, 'confirmed-extension')
        self.assertEqual(following['number'], 2)
        self.assertEqual(following['previousRoundId'], previous['id'])
        self.assertEqual(following['limits'], previous['limits'])
        self.assertEqual(following['consumed'], {'modelRequests': 0, 'repairRounds': 0, 'activeMilliseconds': 0})

    def test_actual_provider_hooks_share_32_requests_across_streaming_entrypoints(self):
        provider = GiteeProvider({'TESTING': True, 'GITEE_AI_API_KEY': 'fixture-only'})
        responses = []

        class Response:
            headers = {'Content-Type': 'text/event-stream'}
            raw = None

            def __init__(self, status=200):
                self.status_code, self.closed = status, False

            def iter_lines(self, **_):
                yield b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}],"usage":{"total_tokens":7}}'
                yield b''
                yield b'data: [DONE]'
                yield b''

            def close(self):
                self.closed = True

        def network(*_, **__):
            response = Response(429 if len(responses) == 31 else 200)
            responses.append(response)
            return response

        with self.context(clock=lambda: 0).bind() as budget, patch(
                'backend.app.modules.workbench.graph_runtime.requests.post', side_effect=network) as post:
            for index in range(31):
                args = ([{'role': 'user', 'content': 'fixture'}], 'model-' + str(index % 2), budget, lambda _: None)
                if index % 4 == 0:
                    self.assertEqual(list(provider.events(*args))[-1]['type'], 'turn_end')
                elif index % 4 == 1:
                    self.assertEqual(''.join(provider.tokens(*args)), 'ok')
                elif index % 4 == 2:
                    self.assertEqual(''.join(provider.json_tokens(*args)), 'ok')
                else:
                    self.assertEqual(''.join(provider.traced_json_tokens(*args, on_trace=lambda *_: None)), 'ok')
            with self.assertRaises(GatewayError):
                list(provider.events(*args))
            with self.assertRaises(CapabilityError) as caught:
                list(provider.events(*args))
            self.assertEqual(caught.exception.code, 'BUDGET_EXHAUSTED')
            self.assertEqual(post.call_count, 32, '33rd model turn reached the network')
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(self.current()['consumed']['modelRequests'], 32)
        calls = self.store.listing('model_requests', 'owner', 'session')
        self.assertEqual(sum(call['state'] == 'succeeded' for call in calls), 31)
        self.assertEqual(sum(call['state'] == 'failed' for call in calls), 1)
        self.assertEqual(sum(call.get('usage', {}).get('total_tokens', 0) for call in calls), 31 * 7)

    def test_actual_provider_deadline_aborts_waiting_stream_and_records_unknown(self):
        with self.store.transaction() as db:
            record = self.store.get(db, 'budget_rounds', 'owner', self.round['id'])
            record['consumed']['activeMilliseconds'] = 599900
            self.store.put(db, 'budget_rounds', 'owner', record)
        provider = GiteeProvider({'TESTING': True, 'GITEE_AI_API_KEY': 'fixture-only'})
        shutdown, closed = threading.Event(), threading.Event()

        class Socket:
            def shutdown(self, _):
                shutdown.set()

            def detach(self):
                return -1

        class Response:
            status_code = 200
            headers = {'Content-Type': 'text/event-stream'}
            raw = SimpleNamespace(_connection=SimpleNamespace(sock=Socket()))

            def iter_lines(self, **_):
                if not shutdown.wait(2):
                    raise AssertionError('authoring deadline failed to interrupt provider stream')
                raise requests.ConnectionError('fixture connection shutdown')
                yield b''

            def close(self):
                closed.set()

        started = time.monotonic()
        with self.context().bind() as budget, patch(
                'backend.app.modules.workbench.graph_runtime.requests.post', return_value=Response()) as post:
            with self.assertRaises(GatewayError):
                list(provider.events([{'role': 'user', 'content': 'fixture'}], 'fixture', budget, lambda _: None))
            self.assertTrue(shutdown.is_set())
            self.assertTrue(closed.is_set())
            self.assertLess(time.monotonic() - started, 1.5)
            connect_timeout, read_timeout = post.call_args.kwargs['timeout']
            self.assertLessEqual(connect_timeout, .1)
            self.assertLessEqual(read_timeout, .1)
            self.assertEqual(budget.responses, set())
        calls = self.store.listing('model_requests', 'owner', 'session')
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['state'], 'unknown')
        self.assertEqual(self.current()['consumed'], {'activeMilliseconds': 600000, 'modelRequests': 1, 'repairRounds': 0})

    def test_actual_provider_connection_failure_is_counted_without_auto_retry(self):
        provider = GiteeProvider({'TESTING': True, 'GITEE_AI_API_KEY': 'fixture-only'})
        with self.context(clock=lambda: 0).bind() as budget, patch(
                'backend.app.modules.workbench.graph_runtime.requests.post', side_effect=requests.Timeout) as post:
            with self.assertRaises(GatewayError):
                list(provider.tokens([{'role': 'user', 'content': 'fixture'}], 'fixture', budget, lambda _: None))
            self.assertEqual(post.call_count, 1)
        self.assertEqual(self.current()['consumed']['modelRequests'], 1)
        self.assertEqual(self.store.listing('model_requests', 'owner', 'session')[0]['state'], 'unknown')

    def test_actual_provider_messages_are_private_bounded_and_credential_redacted(self):
        provider = GiteeProvider({'TESTING': True, 'GITEE_AI_API_KEY': 'fixture-only'})
        messages = [{'role': 'system', 'content': '你是创作系统，按用户要求生成实现。'},
                    {'role': 'user', 'content': 'name=demo; api_key=fixture-secret; 请创建应用'}]
        with self.context(clock=lambda: 0).bind() as budget, patch(
                'backend.app.modules.workbench.graph_runtime.requests.post', side_effect=requests.Timeout):
            with self.assertRaises(GatewayError):
                list(provider.events(messages, 'GLM-5.3-flash', budget, lambda _: None))
        calls = self.store.listing('model_requests', 'owner', 'session')
        self.assertEqual(len(calls), 1)
        trace = self.store.read('model_traces', 'owner', calls[0]['traceId'])
        self.assertEqual(trace['request']['value']['messages'][0], messages[0])
        self.assertIn('[REDACTED]', trace['request']['value']['messages'][1]['content'])
        self.assertNotIn('fixture-secret', str(trace))
        self.assertNotIn('fixture-only', str(trace))
        self.assertIsNone(self.store.read('model_traces', 'another-owner', calls[0]['traceId'], required=False))

    def test_provider_partial_stream_network_failure_remains_unknown(self):
        provider = GiteeProvider({'TESTING': True, 'GITEE_AI_API_KEY': 'fixture-only'})

        class Response:
            status_code = 200
            headers = {'Content-Type': 'text/event-stream'}
            raw = None

            def __init__(self, failure):
                self.failure, self.closed = failure, False

            def iter_lines(self, **_):
                yield b'data: {"choices":[{"delta":{"content":"partial"}}]}'
                yield b''
                if self.failure is not None:
                    raise self.failure

            def close(self):
                self.closed = True

        with self.context(clock=lambda: 0).bind() as budget:
            for failure in (requests.Timeout('fixture stream timeout'), requests.ConnectionError('fixture disconnect'), None):
                with self.subTest(failure=type(failure).__name__):
                    response = Response(failure)
                    with patch('backend.app.modules.workbench.graph_runtime.requests.post', return_value=response) as post:
                        with self.assertRaises(GatewayError):
                            list(provider.tokens([{'role': 'user', 'content': 'fixture'}], 'fixture', budget, lambda _: None))
                        self.assertEqual(post.call_count, 1)
                    self.assertTrue(response.closed)
                    self.assertFalse(budget.is_set(), 'fixture should fail before the budget expires')
                    calls = self.store.listing('model_requests', 'owner', 'session')
                    self.assertTrue(all(call['state'] == 'unknown' for call in calls), calls)
        self.assertEqual(self.current()['consumed']['modelRequests'], 3)


if __name__ == '__main__':
    unittest.main()
