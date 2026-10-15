"""Timeout tests use isolated records, real WASI Tools and deterministic model transports."""
import copy
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library.contracts import canonical
from backend.app.modules.capability_library.asset_validation import verify_suite
from backend.app.modules.capability_library.packages import file_index, zip_bytes
from backend.app.modules.capability_library.skill_validation import ValidationBudget, collect_skill, model_turn
from backend.app.modules.workbench.errors import GatewayError

ROOT = Path(__file__).resolve().parents[1]


class Model:
    def __init__(self, actions):
        self.actions, self.calls = actions, []

    def events(self, messages, model, stopped, on_response, *, tools=None):
        index = len(self.calls)
        self.calls.append(copy.deepcopy(messages))
        yield from self.actions[index](messages, stopped, on_response, tools)


def timeout(messages, stopped, response, tools):
    yield {'type': 'token', 'text': 'unaccepted partial'}
    raise GatewayError('模型连接或响应超时，请稍后重试。', 504)


def answer(messages, stopped, response, tools):
    yield {'type': 'token', 'text': 'verified actual total'}
    yield {'type': 'turn_end', 'tool_calls': []}


def tool(messages, stopped, response, tools):
    name = next(t['function']['name'] for t in tools if t['function']['name'].startswith('cap_'))
    yield {'type': 'turn_end', 'tool_calls': [{'id': 'actual-call', 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps({'values': ['0.10', '0.20']})}}]}


class ModelBudgetTests(unittest.TestCase):
    def library(self, actions):
        provider = Model(actions)
        return SimpleNamespace(validation_model_slots=threading.BoundedSemaphore(2),
            workbench=SimpleNamespace(runtime=SimpleNamespace(provider=provider))), provider

    def stream(self, lib, stop=None, budget=None):
        return model_turn(lib, [{'role': 'user', 'content': 'small input'}], 'test',
                          stop or threading.Event(), lambda _: None, [], budget or ValidationBudget(), 1)

    def assert_slots_released(self, lib):
        self.assertTrue(lib.validation_model_slots.acquire(timeout=1))
        self.assertTrue(lib.validation_model_slots.acquire(timeout=1))
        lib.validation_model_slots.release(); lib.validation_model_slots.release()

    def test_partial_text_and_tool_envelope_before_timeout_are_never_accepted(self):
        def partial(messages, stopped, response, tools):
            yield {'type': 'token', 'text': 'bad text'}
            yield {'type': 'turn_end', 'tool_calls': [{'id': 'never-dispatch'}]}
            raise GatewayError('timeout', 504)
        lib, provider = self.library([partial, answer])
        events = list(self.stream(lib))
        self.assertEqual([e['text'] for e in events if e['type'] == 'token'], ['verified actual total'])
        self.assertEqual([e['tool_calls'] for e in events if e['type'] == 'turn_end'], [[]])
        self.assertEqual(provider.calls[0], provider.calls[1])
        failure = next(e['request'] for e in events if e['type'] == 'validation_model' and e['request']['status'] == 'failed')
        self.assertNotIn('partialOutput', failure)
        self.assertEqual(len(failure['partialOutputDigest']), 64)
        self.assertFalse(failure['partialOutputAccepted'])
        self.assert_slots_released(lib)

    def test_silent_heartbeat_stops_at_absolute_deadline(self):
        aborted, finished = threading.Event(), threading.Event()
        def heartbeat(messages, stopped, response, tools):
            response(SimpleNamespace(abort=aborted.set))
            while not stopped.is_set() and not aborted.wait(.005):
                pass  # SSE heartbeat/reasoning emits no outer event.
            finished.set()
            if False: yield
        lib, provider = self.library([heartbeat])
        start = time.monotonic()
        with self.assertRaisesRegex(GatewayError, '整体时间上限'):
            list(self.stream(lib, budget=ValidationBudget(seconds=.12)))
        self.assertLess(time.monotonic()-start, .6)
        self.assertTrue(finished.wait(1)); self.assertTrue(aborted.is_set())
        self.assertEqual(len(provider.calls), 1); self.assert_slots_released(lib)

    def test_close_after_initial_event_aborts_and_releases_slot(self):
        entered, aborted = threading.Event(), threading.Event()
        def waiting(messages, stopped, response, tools):
            response(SimpleNamespace(abort=aborted.set)); entered.set()
            stopped.wait(1)
            if False: yield
        lib, _ = self.library([waiting])
        stream = self.stream(lib)
        next(stream); self.assertTrue(entered.wait(1)); stream.close()
        self.assertTrue(aborted.wait(1)); self.assert_slots_released(lib)

    def test_persist_failure_closes_nested_generator(self):
        def waiting(messages, stopped, response, tools):
            stopped.wait(1)
            if False: yield
        lib, _ = self.library([waiting])
        lib.chat_events = lambda *args, **kwargs: self.stream(lib)
        with self.assertRaisesRegex(RuntimeError, 'disk failed'):
            collect_skill(lib, 'owner', {'assetId': 'a', 'versionId': 'v'}, 'prompt', [],
                threading.Event(), {}, lambda: (_ for _ in ()).throw(RuntimeError('disk failed')))
        self.assert_slots_released(lib)

    def test_cancel_before_headers_has_no_late_record_writes_or_retry(self):
        release, entered, aborted = threading.Event(), threading.Event(), threading.Event()
        def late(messages, stopped, response, tools):
            entered.set(); release.wait(2)
            response(SimpleNamespace(abort=aborted.set))
            yield {'type': 'token', 'text': 'late text'}
            yield {'type': 'turn_end', 'tool_calls': [{'id': 'late-tool'}]}
        lib, provider = self.library([late]); stop = threading.Event()
        lib.chat_events = lambda *args, **kwargs: self.stream(lib, stop)
        rows, record, errors = [], {}, []
        def consume():
            try: collect_skill(lib, 'o', {'assetId': 'a', 'versionId': 'v'}, 'p', [], stop,
                               record, lambda: rows.append(copy.deepcopy(record)))
            except GatewayError as error: errors.append(error)
        thread = threading.Thread(target=consume); thread.start()
        self.assertTrue(entered.wait(1)); stop.set(); thread.join(1)
        self.assertFalse(thread.is_alive()); self.assertEqual(errors[0].code, 'CANCELLED')
        before = copy.deepcopy(rows); release.set(); self.assertTrue(aborted.wait(1))
        self.assert_slots_released(lib); self.assertEqual(rows, before)
        self.assertEqual(len(provider.calls), 1); self.assertEqual(record['executionEvidence'], [])

    def test_authentication_errors_do_not_retry(self):
        def auth(*args):
            raise GatewayError('认证失败', 401)
            yield
        lib, provider = self.library([auth])
        with self.assertRaisesRegex(GatewayError, '认证失败'): list(self.stream(lib))
        self.assertEqual(len(provider.calls), 1); self.assert_slots_released(lib)

    def test_waiting_for_slot_cannot_start_a_model_after_deadline(self):
        lib, provider = self.library([answer]); budget = ValidationBudget(seconds=.015)
        lib.validation_model_slots.acquire(); lib.validation_model_slots.acquire()
        timer = threading.Timer(.03, lib.validation_model_slots.release); timer.start()
        try:
            with self.assertRaises(GatewayError) as raised: list(self.stream(lib, budget=budget))
            self.assertEqual(raised.exception.code, 'VALIDATION_DEADLINE')
            self.assertEqual(provider.calls, [])
        finally:
            timer.join(); lib.validation_model_slots.release()
        self.assert_slots_released(lib)

    def test_request_budget_is_shared_and_context_limit_precedes_requests(self):
        lib, provider = self.library([answer, answer]); budget = ValidationBudget(requests=1)
        list(self.stream(lib, budget=budget))
        with self.assertRaises(GatewayError) as raised: list(self.stream(lib, budget=budget))
        self.assertEqual(raised.exception.code, 'MODEL_REQUEST_LIMIT'); self.assertEqual(len(provider.calls), 1)
        with self.assertRaises(GatewayError) as raised:
            list(model_turn(lib, [{'content': 'x'*180001}], 'm', threading.Event(), lambda _: None, [], ValidationBudget(), 1))
        self.assertEqual(raised.exception.code, 'MODEL_CONTEXT_TOO_LARGE'); self.assertEqual(len(provider.calls), 1)


class ValidationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cap-validation-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'GITEE_AI_API_KEY': 'test-only',
            'CAPABILITY_SKIP_PUBLIC_SEED': True, 'AGENT_RUNTIME_DIR': str(ROOT/'data/agent-runtime')})
        self.lib = self.app.extensions['capability_library']; self.w = self.app.extensions['workbench_service']
        self.addCleanup(self.w.close)
        self.owner = 'isolated-validation-owner'
        imported = self.lib.import_package(self.owner, self.package('decimal-total'), 'tool.zip')
        version = self.lib.version(self.owner, imported['assetId'], imported['versionId'])
        self.tool_body = {k: version[k] for k in ('assetId', 'versionId', 'configurationId')}
        cases = [{'input': {'values': values}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': total}}}
                 for values, total in [(['0.10', '0.20'], '0.30'), (['1.20', '2.30'], '3.50')]]
        outcome = self.wait(verify_suite(self.lib, self.owner, {**self.tool_body, 'cases': cases}))
        self.assertEqual(outcome['status'], 'completed', outcome)
        asset = self.lib.asset(self.owner, version['assetId'])
        self.lib.update_asset(self.owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, '"'+str(asset['revision'])+'"')
        def edit(manifest):
            manifest['dependencies'] = [{'sourceIdentity': version['manifest']['source']['identity'], 'contentDigest': version['contentDigest'], 'required': True, 'reason': 'sum actual values'}]
        skill = self.lib.import_package(self.owner, self.package('summary-skill', edit), 'skill.zip')
        self.skill = self.lib.version(self.owner, skill['assetId'], skill['versionId'])

    def package(self, name, edit=None):
        path = ROOT/'docs/capability-library/examples'/name
        files = {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}
        if edit:
            manifest = json.loads(files['capability.json']); edit(manifest)
            files['capability.json'] = canonical(manifest).encode()
        return zip_bytes(files)

    def wait(self, job):
        end = time.monotonic()+20
        while job['jobId'] in self.lib.running and time.monotonic() < end:
            time.sleep(.01)
        self.assertNotIn(job['jobId'], self.lib.running)
        return self.lib.store.get(self.owner, 'job', job['jobId'])

    def verify_legacy(self, actions):
        # Explicit migration-before-policy fixture: exercises existing users' single-case endpoint.
        self.skill.pop('validationPolicy', None)
        self.lib.store.put(self.owner, 'version', self.skill['versionId'], self.skill)
        self.w.runtime.provider = Model(actions)
        body = {k: self.skill[k] for k in ('assetId', 'versionId', 'configurationId')}
        started = self.lib.verify(self.owner, {**body, 'input': {}, 'fileIds': [],
            'expectation': {'mode': 'manual_review', 'prompt': 'Sum 0.10 and 0.20 with actual Tool.', 'criteria': 'actual total is 0.30'}})
        job = self.wait(started)
        return job, self.lib.store.get(self.owner, 'verification', started['verificationId'])

    def test_model_retry_after_real_tool_preserves_evidence_without_replay(self):
        job, record = self.verify_legacy([tool, timeout, answer])
        self.assertEqual(job['status'], 'completed', job); self.assertEqual(record['status'], 'awaiting_review')
        self.assertEqual(record['result']['content'], 'verified actual total')
        self.assertEqual(len(record['executionEvidence']), 1)
        invocation = self.lib.store.get(self.owner, 'invocation', record['executionEvidence'][0]['invocationId'])
        self.assertEqual(invocation['result']['content'], {'total': '0.30'})
        self.assertEqual([r['status'] for r in record['modelRequests']], ['succeeded','failed','succeeded'])
        self.assertEqual(record['modelRequests'][1]['contextDigest'], record['modelRequests'][2]['contextDigest'])
        self.assertEqual(self.w.runtime.provider.calls[1], self.w.runtime.provider.calls[2])
        self.assertFalse(self.lib.asset(self.owner, self.skill['assetId'])['enabled'])

    def test_exhausted_timeout_keeps_real_tool_and_cannot_pass_review(self):
        job, record = self.verify_legacy([tool, timeout, timeout])
        self.assertEqual(job['status'], 'failed'); self.assertEqual(job['error']['code'], 'MODEL_TIMEOUT')
        self.assertEqual(record['status'], 'failed'); self.assertTrue(record['result']['incomplete'])
        self.assertEqual(record['error']['completedToolCalls'], 1); self.assertIn('completedAt', record)
        self.assertEqual(len(record['modelRequests']), 3)
        with self.assertRaises(GatewayError): self.lib.review(self.owner, record['verificationId'], {'decision':'accept'})

    def test_new_suite_shares_model_request_budget_across_cases(self):
        self.w.runtime.provider = Model([answer, answer, answer])
        body = {k: self.skill[k] for k in ('assetId', 'versionId', 'configurationId')}
        cases = [{'input': {'n': n}, 'fileIds': [], 'expectation': {'mode': 'manual_review', 'prompt': 'Summarize '+str(n), 'criteria': 'Correct'}} for n in (1,2)]
        with patch('backend.app.modules.capability_library.skill_validation.ValidationBudget', return_value=ValidationBudget(requests=1)):
            started = verify_suite(self.lib, self.owner, {**body, 'cases': cases}); job = self.wait(started)
        record = self.lib.store.get(self.owner, 'verification', started['verificationId'])
        self.assertEqual(job['error']['code'], 'MODEL_REQUEST_LIMIT')
        self.assertEqual([c['status'] for c in record['cases']], ['awaiting_review','failed'])
        self.assertEqual(len(self.w.runtime.provider.calls), 1)


if __name__ == '__main__': unittest.main()
