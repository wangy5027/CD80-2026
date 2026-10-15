"""Coordinator protocol checks with real Model/Planner and local text providers."""
import copy
import json
import threading
import unittest
from unittest.mock import patch

from backend.app.modules.sub_agent.model import Model, validate_response
from backend.app.modules.sub_agent.protocol import MAX_VALIDATION_ERRORS, parse_response
from backend.app.modules.workbench.errors import GatewayError
from tests.sub_agent_fixture import FixtureModel, FOLLOWUP, MESSAGE


class TextProvider:
    model = 'protocol-fixture'

    def __init__(self, text):
        self.text = text
        self.calls = []
        self.closed = False

    def tokens(self, messages, model, stopped, on_response):
        raise AssertionError('Structured model requests must use json_tokens.')

    def json_tokens(self, messages, model, stopped, on_response):
        self.calls.append(copy.deepcopy(messages))
        try:
            # Preserve streaming across syntax boundaries.
            middle = len(self.text) // 2
            yield self.text[:middle]
            yield self.text[middle:]
        finally:
            self.closed = True


class SubAgentProtocolTests(unittest.TestCase):
    def call(self, stage, value):
        provider = TextProvider(value if isinstance(value, str) else json.dumps(value))
        result = Model(provider).call(stage, {'message': 'synthetic task'}, threading.Event(), lambda _value: None)
        self.assertEqual(len(provider.calls), 1)
        self.assertTrue(provider.closed)
        return result

    def assert_invalid(self, stage, value, code, *, raw=False):
        with self.assertRaises(GatewayError) as caught:
            if raw:
                self.call(stage, value)
            else:
                validate_response(stage, value)
        error = caught.exception
        self.assertEqual(error.status, 502)
        self.assertEqual(error.code, code)
        self.assertEqual(error.diagnostics['stage'], stage)
        self.assertEqual(error.validationErrors, error.diagnostics['validationErrors'])
        self.assertLessEqual(len(error.validationErrors), MAX_VALIDATION_ERRORS)
        return error

    def test_each_stage_uses_real_structured_model_and_preserves_value(self):
        examples = (
            ('plan', {'mode': 'chat'}),
            ('plan', {'mode': 'answer', 'answer': 'Observed result.'}),
            ('plan', {'mode': 'ask', 'question': 'Which range?'}),
            ('plan', {'mode': 'application', 'assessment': {'explicitApplication': True}}),
            ('plan', {'mode': 'delegate', 'goal': 'Analyze actual rows', 'allowTemporaryCode': True}),
            ('plan', {'mode': 'direct', 'action': {'type': 'tool', 'name': 'sample', 'arguments': {}}}),
            ('act', {'type': 'tool', 'name': 'sample', 'arguments': {}, 'inputRefs': []}),
            ('act', {'type': 'read', 'artifactId': 'actual-artifact'}),
            ('act', {'type': 'code', 'code': 'def run(payload):\n    return {}', 'reason': 'Method gap'}),
            ('act', {'type': 'ask', 'question': 'Which column?'}),
            ('act', {'type': 'finish', 'answer': 'No data; cannot calculate.', 'artifactIds': []}),
            ('approve', {'decision': 'approve', 'reason': 'Within delegated scope'}),
            ('approve', {'decision': 'reject', 'reason': 'Outside delegated scope'}),
            ('approve', {'decision': 'ask', 'question': 'Which input?'}),
            ('review', {'decision': 'accept', 'answer': 'Verified observed values.'}),
            ('review', {'decision': 'revise', 'reason': 'Missing requested result'}),
            ('review', {'decision': 'fail', 'reason': 'Required data unavailable'}),
        )
        for stage, value in examples:
            with self.subTest(stage=stage, value=value):
                self.assertEqual(self.call(stage, value), value)

    def test_existing_fixture_contracts_remain_valid(self):
        fixture = FixtureModel([{'assetId': 'reader', 'versionId': 'reader-version'}])
        tool = {'type': 'tool', 'status': 'succeeded'}
        code = {'type': 'code', 'status': 'succeeded',
                'result': {'content': {'rows': 5, 'change': '0.2', 'maxDrawdown': '0.25'}}}
        examples = [
            ('plan', {'message': MESSAGE}),
            ('plan', {'message': FOLLOWUP, 'priorArtifacts': [{'name': 'analysis-result.json', 'preview': '{"rows":5}'}]}),
            ('act', {'steps': []}),
            ('act', {'steps': [tool], 'artifacts': [{'id': 'input', 'name': 'sample.csv'}], 'correctionsUsed': 0}),
            ('act', {'steps': [tool, code], 'artifacts': [], 'correctionsUsed': 3}),
            ('approve', {'steps': [tool]}),
            ('review', {'platformChecks': {'missingExtensions': ['.csv']}}),
            ('review', {'platformChecks': {'missingExtensions': []}, 'steps': [tool, code]}),
        ]
        for stage, payload in examples:
            with self.subTest(stage=stage, payload=payload):
                value = fixture.call(stage, payload, threading.Event(), lambda _value: None)
                original = copy.deepcopy(value)
                self.assertIs(validate_response(stage, value), value)
                self.assertEqual(value, original)

    def test_required_capabilities_are_validated_in_every_plan_mode(self):
        examples = [{'mode': 'chat'}, {'mode': 'application'}, {'mode': 'answer', 'answer': 'Observed result'},
                    {'mode': 'ask', 'question': 'Which input?'}, {'mode': 'delegate', 'goal': 'Analyze rows'},
                    {'mode': 'direct', 'action': {'type': 'tool', 'name': 'sample', 'arguments': {}}}]
        for example in examples:
            for required in (None, [{}], [False], 'sample'):
                with self.subTest(mode=example['mode'], required=required):
                    error = self.assert_invalid('plan', json.dumps({**example, 'requiredCapabilities': required}),
                                                'MODEL_SCHEMA_INVALID', raw=True)
                    self.assertEqual(error.validationErrors[0]['path'], '$.requiredCapabilities')
            value = {**example, 'requiredCapabilities': ['sample']}
            self.assertEqual(self.call('plan', value), value)

    def test_single_json_fence_and_raw_code_whitespace_use_shared_parser(self):
        value = {'type': 'code', 'reason': 'Use actual input',
                 'code': 'def run(payload):\r\n\treturn {"text": "a\\nb"}\n'}
        raw = json.dumps(value)
        self.assertEqual(self.call('act', '```json\n' + raw + '\n```'), value)
        raw = raw.replace('\\r', '\r').replace('\\n', '\n', 1).replace('\\t', '\t')
        self.assertEqual(self.call('act', raw), value)

    def test_invalid_json_is_typed_without_repair_or_raw_value_leaks(self):
        values = ['{"type":"finish","answer":"SECRET_VALUE",',
                  'prose ```json\n{"type":"finish"}\n```',
                  '{"type":"tool","type":"finish"}',
                  '{"type":"tool","name":"sample","arguments":{"value":NaN}}',
                  '{"type":"finish"} {"type":"ask"}',
                  '```json\n{"type":"finish"}\n```\ntrailing']
        for raw in values:
            with self.subTest(raw=raw):
                error = self.assert_invalid('act', raw, 'MODEL_JSON_INVALID', raw=True)
                self.assertEqual(error.diagnostics['responseLength'], len(raw.strip()))
                self.assertNotIn('SECRET_VALUE', str(error) + json.dumps(error.diagnostics))

    def test_parse_location_describes_original_response_without_excerpt(self):
        raw = '```json\n{\n"type": "finish",\n"answer": }\n```'
        error = self.assert_invalid('act', raw, 'MODEL_JSON_INVALID', raw=True)
        position = raw.index('}')
        self.assertEqual(error.diagnostics['parsePosition'], position)
        self.assertEqual(error.diagnostics['parseLine'], 4)
        self.assertEqual(error.diagnostics['parseColumn'], 11)
        self.assertEqual(error.diagnostics['parsePositionBasis'], 'original_response')
        self.assertNotIn('raw', error.diagnostics)

    def test_valid_json_with_wrong_root_is_schema_failure(self):
        for raw in ('[]', 'null', '42', 'true', '"text"', '```json\n[]\n```'):
            with self.subTest(raw=raw):
                self.assert_invalid('plan', raw, 'MODEL_SCHEMA_INVALID', raw=True)
        for raw in ('[NaN]', '[{"a":1,"a":2}]'):
            with self.subTest(raw=raw):
                self.assert_invalid('plan', raw, 'MODEL_JSON_INVALID', raw=True)

    def test_valid_json_with_invalid_branch_fields_is_schema_failure(self):
        invalid = [
            ('plan', {}), ('plan', {'mode': 'unknown-secret-mode'}),
            ('plan', {'mode': 'answer'}), ('plan', {'mode': 'ask', 'question': ''}),
            ('plan', {'mode': 'chat', 'assessment': ['SECRET_VALUE']}),
            ('plan', {'mode': 'chat', 'assessment': {'dependentProcessing': 'false'}}),
            ('plan', {'mode': 'delegate', 'goal': 'ok', 'allowTemporaryCode': 1}),
            ('plan', {'mode': 'delegate', 'goal': 'ok', 'requiredCapabilities': 'tool'}),
            ('plan', {'mode': 'direct', 'action': {'type': 'code', 'code': 'x'}}),
            ('plan', {'mode': 'direct', 'action': {'type': 'read'}}),
            ('plan', {'mode': 'chat', 'preferredCapabilities': [{'assetId': 'private-id'}]}),
            ('act', {'type': 'tool', 'name': 'sample', 'arguments': []}),
            ('act', {'type': 'tool', 'arguments': {}}),
            ('act', {'type': 'code', 'code': '', 'reason': 'gap'}),
            ('act', {'type': 'read', 'artifactId': 3}),
            ('act', {'type': 'finish', 'answer': 'ok', 'artifactIds': [False]}),
            ('act', {'type': 'tool', 'name': 'sample', 'arguments': {}, 'inputRefs': ['x'] * 17}),
            ('approve', {'decision': 'accept', 'reason': 'wrong stage'}),
            ('approve', {'decision': 'ask'}),
            ('review', {'decision': 'approve'}), ('review', {'decision': 'revise'}),
            ('review', {'decision': 'accept', 'answer': {}}),
        ]
        for stage, value in invalid:
            with self.subTest(stage=stage, value=value):
                error = self.assert_invalid(stage, value, 'MODEL_SCHEMA_INVALID')
                diagnostics = str(error) + json.dumps(error.diagnostics)
                for secret in ('SECRET_VALUE', 'private-id', 'unknown-secret-mode'):
                    self.assertNotIn(secret, diagnostics)
                # Exercise parsing and validation together, not only direct stubs.
                self.assert_invalid(stage, json.dumps(value), 'MODEL_SCHEMA_INVALID', raw=True)

    def test_schema_diagnostics_are_bounded_and_stubs_cannot_return_non_json_values(self):
        error = self.assert_invalid('plan', {'mode': 'delegate', 'preferredCapabilities': [{}] * 100},
                                    'MODEL_SCHEMA_INVALID')
        self.assertEqual(len(error.validationErrors), MAX_VALIDATION_ERRORS)
        self.assertLess(len(json.dumps(error.diagnostics)), 1500)
        self.assert_invalid('act', {'type': 'tool', 'name': 'sample', 'arguments': {'value': float('nan')}},
                            'MODEL_SCHEMA_INVALID')
        self.assert_invalid('plan', [], 'MODEL_SCHEMA_INVALID')

    def test_optional_assessment_does_not_change_business_or_permission_checks(self):
        for assessment in (None, {}, {'explicitApplication': False}):
            value = {'mode': 'application', 'assessment': assessment}
            self.assertIs(validate_response('plan', value), value)
        # Syntactic validation grants no capability and does not remove an
        # impossible required identity; admission remains the service's job.
        value = {'mode': 'delegate', 'goal': 'test', 'requiredCapabilities': ['cd80_plot']}
        self.assertIs(validate_response('plan', value), value)

    def test_model_only_forwards_nonempty_trace_option_and_emits_parsed_value(self):
        value = {'mode': 'answer', 'answer': 'Actual observed result'}
        model = Model(TextProvider(json.dumps(value)))
        with patch.object(model.planner, '_call', wraps=model.planner._call) as called:
            self.assertEqual(model.call('plan', {}, threading.Event(), lambda _value: None), value)
        self.assertNotIn('on_trace', called.call_args.kwargs)
        events = []
        callback = lambda event, detail: events.append((event, detail))
        # The observer boundary is independent of provider trace implementation.
        with patch.object(model.planner, '_call', return_value=json.dumps(value)) as called:
            self.assertEqual(model.call('plan', {}, threading.Event(), lambda _value: None, on_trace=callback), value)
        self.assertIs(called.call_args.kwargs['on_trace'], callback)
        self.assertEqual(events, [('parsed', value)])
        for invalid in ('{"mode":', '{"mode":"answer"}'):
            events.clear()
            with patch.object(model.planner, '_call', return_value=invalid), self.assertRaises(GatewayError):
                model.call('plan', {}, threading.Event(), lambda _value: None, on_trace=callback)
            self.assertEqual(events, [] if invalid == '{"mode":' else [('parsed', {'mode': 'answer'})])

    def test_upstream_failures_and_cancellation_are_not_reclassified_as_json(self):
        model = Model(TextProvider('{"mode":"chat"}'))
        failure = GatewayError('upstream request rejected', 401, 'provider_auth')
        with patch.object(model.planner, '_call', side_effect=failure), self.assertRaises(GatewayError) as caught:
            model.call('plan', {}, threading.Event(), lambda _value: None)
        self.assertIs(caught.exception, failure)
        stopped = threading.Event()
        stopped.set()
        with self.assertRaises(GatewayError) as caught:
            model.call('plan', {}, stopped, lambda _value: None)
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(model.planner.provider.calls, [])

    def test_non_text_parser_input_is_typed(self):
        for value in (None, {}, b'{}'):
            with self.subTest(value=value), self.assertRaises(GatewayError) as caught:
                parse_response('act', value)
            self.assertEqual(caught.exception.code, 'MODEL_JSON_INVALID')


if __name__ == '__main__':
    unittest.main()
