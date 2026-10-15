import copy
import hashlib
import json
import unittest

from backend.app.modules.sub_agent.diagnostics import ModelTrace, bounded_record, redacted


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


class SubAgentDiagnosticsTests(unittest.TestCase):
    def test_nested_keys_do_not_hide_usage_or_mutate_source(self):
        source = {'password': 'pw-value', 'nested': [{'clientSecret': 'secret-value', 'api_key': 'key-value',
                  'accessToken': 'access-value', 'authorization': 'auth-value', 'cookie': 'cookie-value'}],
                  'usage': {'input_tokens': 123, 'output_tokens': 45, 'total_tokens': 168, 'token_count': 168},
                  'tokenizer': 'stock token explanation', 'headers': {'x-public': 'header-value'},
                  'reasoning_content': 'private reasoning', 'responseHeaders': {'x-id': 'other-header'}}
        before = copy.deepcopy(source)
        clean = redacted(source)
        self.assertEqual(source, before)
        self.assertEqual(clean['usage'], source['usage'])
        self.assertEqual(clean['tokenizer'], source['tokenizer'])
        for secret in ('pw-value', 'secret-value', 'key-value', 'access-value', 'auth-value', 'cookie-value',
                       'header-value', 'private reasoning', 'other-header'):
            self.assertNotIn(secret, encoded(clean))
        self.assertNotIn('headers', clean)
        self.assertNotIn('reasoning_content', clean)
        clean['nested'][0]['api_key'] = 'changed'
        self.assertEqual(source, before)

    def test_prefixed_environment_and_camel_api_keys_are_redacted(self):
        keys = ('GITEE_AI_API_KEY', 'CD80_READ_API_KEY', 'providerApiKey', 'providerAPIKey',
                'PROVIDER_APIKEY', 'PROVIDERAPIKEY', 'x-provider-api-key', 'provider.api_key')
        source = {key: 'synthetic-secret-' + str(index) for index, key in enumerate(keys)}
        source.update(message='GITEE_AI_API_KEY=synthetic-secret-text',
                      nested=json.dumps({'body': json.dumps({'CD80_READ_API_KEY': 'synthetic-secret-nested'})}),
                      url='https://example.invalid/?CD80_READ_API_KEY=synthetic-secret-url&code=000001.SZ',
                      usage={'input_tokens': 123, 'output_tokens': 45, 'total_tokens': 168, 'token_count': 168},
                      api_keyboard='ordinary input', tokenizer='ordinary token description')
        before = copy.deepcopy(source)
        clean = redacted(source)
        self.assertNotIn('synthetic-secret-', encoded(clean))
        for key in keys:
            self.assertEqual(clean[key], '[REDACTED]')
        self.assertEqual(clean['usage'], source['usage'])
        self.assertEqual(clean['api_keyboard'], source['api_keyboard'])
        self.assertEqual(clean['tokenizer'], source['tokenizer'])
        self.assertIn('code=000001.SZ', clean['url'])
        self.assertEqual(redacted(clean), clean)
        self.assertEqual(source, before)

    def test_nested_json_and_python_literals_are_redacted(self):
        inner = json.dumps({'apiKey': 'deep-key', 'password': 'deep-password', 'usage': {'input_tokens': 7}})
        nested = json.dumps({'message': inner, 'reasoning_content': 'nested-reasoning',
                             'headers': {'x-id': 'nested-header'}})
        source = {'messages': [{'content': 'Context: ' + json.dumps({'body': nested})}],
                  'response': "{'credentials': {'user': 'nested-user', 'password': 'nested-pass'}}"}
        clean = encoded(redacted(source))
        for secret in ('deep-key', 'deep-password', 'nested-reasoning', 'nested-header', 'nested-user', 'nested-pass'):
            self.assertNotIn(secret, clean)
        self.assertIn('input_tokens', clean)
        # Redaction must leave nested JSON parseable rather than corrupting its
        # escaping while the log crosses multiple snapshot/persistence passes.
        output = redacted(json.dumps({'body': nested}))
        parsed = json.loads(json.loads(output)['body'])
        self.assertEqual(json.loads(parsed['message'])['apiKey'], '[REDACTED]')
        self.assertEqual(redacted(output), output)

    def test_bearer_urls_and_http_header_text(self):
        source = ('Authorization: Bearer bearer-value\nCookie: sid=cookie-first; other=cookie-second\n'
                  'https://alice:url-password@example.invalid/path?access_token=query-secret&code=000001.SZ&limit=5\n'
                  'https://example.invalid/?api%5Fkey=encoded-secret&next=https%3A%2F%2Fbob%3Ainner-password%40internal.invalid%2F\n'
                  'Bearer detached-secret Basic encoded-password token meaning is unchanged')
        clean = redacted(source)
        for secret in ('bearer-value', 'cookie-first', 'cookie-second', 'alice', 'url-password', 'query-secret',
                       'encoded-secret', 'bob', 'inner-password', 'detached-secret', 'encoded-password'):
            self.assertNotIn(secret, clean)
        self.assertIn('code=000001.SZ', clean)
        self.assertIn('limit=5', clean)
        self.assertIn('token meaning is unchanged', clean)
        self.assertEqual(redacted(clean), clean)

    def test_invalid_url_and_fragment_cannot_bypass_credentials_redaction(self):
        raw = 'https://alice:bad-password@[invalid/?token=query-secret#access_token=fragment-secret&state=normal'
        clean = redacted(raw)
        for secret in ('alice', 'bad-password', 'query-secret', 'fragment-secret'):
            self.assertNotIn(secret, clean)
        self.assertIn('state=normal', clean)
        self.assertEqual(redacted(clean), clean)
        callback = 'https://example.invalid/callback#access_token=fixture-secret&state=ok'
        nested = {'message': json.dumps({'body': json.dumps({'url': callback})})}
        cleaned = redacted(nested)
        self.assertNotIn('fixture-secret', encoded(cleaned))
        url = json.loads(json.loads(cleaned['message'])['body'])['url']
        self.assertIn('state=ok', url)
        self.assertEqual(redacted(cleaned), cleaned)

    def test_malformed_response_redaction_and_unchanged_error_context(self):
        raw = '{"type":"code",\n"code":"def run(payload):\\n    return 1", broken JSON near the end'
        self.assertEqual(redacted(raw), raw)
        for raw in ('{"api_key":"unterminated-secret', 'prefix password=bare-secret tail',
                    '{"credentials":{"password":"container-secret","extra":"private-extra"',
                    'prefix {\\"api_key\\":\\"escaped-secret\\"} trailing'):
            with self.subTest(raw=raw):
                clean = redacted(raw)
                for secret in ('unterminated-secret', 'bare-secret', 'container-secret', 'private-extra', 'escaped-secret'):
                    self.assertNotIn(secret, clean)

    def test_json_safe_cycles_bytes_depth_and_unsupported_objects(self):
        class PrivateObject:
            def __repr__(self):
                return 'secret-object-value'
        cycle = []
        cycle.append(cycle)
        deep = 'bottom-private'
        for _ in range(60):
            deep = {'child': deep}
        source = {'cycle': cycle, 'bytes': b'Bearer byte-secret', 'nonFinite': float('nan'),
                  'object': PrivateObject(), 'deep': deep}
        clean = encoded(redacted(source))
        for secret in ('byte-secret', 'secret-object-value', 'bottom-private'):
            self.assertNotIn(secret, clean)
        self.assertIn('circular reference', clean)
        self.assertIn('depth limit', clean)
        self.assertEqual(redacted('\ud800'), r'\ud800')
        encoded(redacted('\ud800')).encode('utf-8')

    def test_trace_fixed_slots_transport_merge_and_detached_snapshots(self):
        trace = ModelTrace()
        self.assertEqual(set(trace.record()), {'request', 'transport', 'response', 'parsed'})
        request = {'messages': [{'role': 'user', 'content': 'business request'}]}
        trace.receive('request', request)
        request['messages'][0]['content'] = 'later mutation'
        trace.receive('transport', {'model': 'fixture', 'parameters': {'max_tokens': 99},
                                    'headers': {'Authorization': 'never-stored'}, 'arbitrary': 'not-whitelisted'})
        trace.receive('transport', {'finishReason': 'stop', 'usage': {'input_tokens': 17}})
        trace.receive('response', {'text': 'partial', 'complete': False})
        trace.receive('response', {'text': '{"type":"finish"}', 'complete': True})
        trace.receive('parsed', {'type': 'finish'})
        trace.receive('unknown', {'secret': 'ignored'})
        record = trace.record()
        self.assertEqual(record['request']['messages'][0]['content'], 'business request')
        self.assertEqual(record['response'], {'text': '{"type":"finish"}', 'complete': True})
        self.assertEqual(record['transport'], {'model': 'fixture', 'parameters': {'max_tokens': 99},
                                              'finishReason': 'stop', 'usage': {'input_tokens': 17}})
        record['parsed']['type'] = 'mutated'
        self.assertEqual(trace.record()['parsed']['type'], 'finish')

    def test_normal_120k_response_is_complete_and_hash_is_redacted(self):
        source = {'response': {'text': 'A' * 120_000, 'complete': True}, 'api_key': 'never-hashed'}
        safe = redacted(source)
        text = encoded(safe)
        record = bounded_record(source)
        self.assertFalse(record['truncated'])
        self.assertEqual(record['value'], safe)
        self.assertEqual(record['originalChars'], len(text))
        self.assertEqual(record['sha256'], hashlib.sha256(text.encode('utf-8')).hexdigest())
        self.assertEqual(record['hashScope'], 'redacted-json')
        self.assertNotIn('never-hashed', encoded(record))

    def test_default_megachar_limit_redacts_before_cutting(self):
        source = {'response': {'text': 'BEGIN ' + 'x' * 1_100_000 + ' password=tail-secret END', 'complete': False}}
        record = bounded_record(source)
        self.assertTrue(record['truncated'])
        self.assertLessEqual(len(encoded(record)), 1_048_576)
        self.assertNotIn('tail-secret', encoded(record))
        self.assertIn('BEGIN', record['value']['head'])
        self.assertIn('END', record['value']['tail'])

    def test_bound_includes_wrapper_and_preserves_locatable_head_tail(self):
        source = {'text': 'FIRST ' + ('中文"\\\n' * 2000) + ' LAST broken-json'}
        text = encoded(redacted(source))
        for maximum in (512, 1024, 4096):
            with self.subTest(maximum=maximum):
                record = bounded_record(source, max_chars=maximum)
                self.assertTrue(record['truncated'])
                self.assertLessEqual(len(encoded(record)), maximum)
                value = record['value']
                self.assertEqual(value['headStart'], 0)
                self.assertTrue(text.startswith(value['head']))
                self.assertEqual(text[value['tailStart']:], value['tail'])
                self.assertIn('FIRST', value['head'])
                self.assertIn('LAST broken-json', value['tail'])
                self.assertEqual(record['originalChars'], len(text))
        for invalid in (511, 0, True, '1024'):
            with self.assertRaises(ValueError):
                bounded_record(source, max_chars=invalid)


if __name__ == '__main__':
    unittest.main()
