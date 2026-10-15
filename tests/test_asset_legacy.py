"""Legacy fixed implementations migrate without inventing execution evidence."""
import base64
import copy
import io
import json
import unittest
from unittest.mock import patch

from tests import test_asset_apps as fixture
from tests.test_asset_components import manifest
from backend.app.modules.agent_platform.asset_contracts import definition
from backend.app.modules.agent_platform.asset_legacy import migrate_pipeline
from backend.app.modules.capability_library.components import invoke_snapshot, runtime_digest
from backend.app.modules.capability_library.contracts import CapabilityError, canonical
from backend.app.modules.capability_library.packages import file_index, zip_bytes
from backend.app.modules.workbench.tool_registry import ToolResult


def tool(name, code, inputs=None, output=None, files=()):
    return {'id': name, 'version': '1', 'code': code, 'inputSchema': inputs or {'type': 'object'},
            'outputSchema': output or {'type': 'object'}, 'permissions': {'network': False, 'files': list(files)}}


def spec(tools, pipeline, inputs=None):
    return {'schemaVersion': '1', 'id': 'old-fixed', 'version': '1', 'name': '旧固定计算', 'goal': '保留固定计算',
            'prompt': '只解释实际计算；结果含额外输入。', 'inputSchema': inputs or {'type': 'object'},
            'outputSchema': {'type': 'object'}, 'tools': tools, 'skills': [], 'pipeline': pipeline,
            'permissions': {'network': False, 'files': []}}


class ExplainProvider:
    def __init__(self):
        self.calls = []

    def events(self, messages, model, stopped, on_response, tools=None):
        payload = json.loads(messages[-1]['content'])
        self.calls.append({'system': messages[0]['content'], 'payload': payload, 'tools': tools})
        value = payload['stepOutputs']['join']['total']
        yield {'type': 'token', 'text': canonical({'explanation': '实际合计：' + str(value)})}
        yield {'type': 'turn_end', 'tool_calls': []}


class AssetLegacyTests(unittest.TestCase):
    setUp = fixture.AssetAppTests.setUp
    tearDown = fixture.AssetAppTests.tearDown
    post = fixture.AssetAppTests.post
    save = fixture.AssetAppTests.save
    wait_run = fixture.AssetAppTests.wait_run
    enable = fixture.AssetAppTests.enable

    def convert(self, saved):
        with patch.object(self.w.platform, 'agent', return_value=saved):
            response = self.post('/creator', {'legacy': {'kind': 'pipeline', 'agentRef': 'fixture-old'}})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['issues'], [], response.json)
        return response.json['definition']

    def test_multisource_full_objects_and_model_resources_survive_original_deletion(self):
        library = self.w.capabilities
        entry = '---\nname: fixed-guide\ndescription: A saved explanatory method.\n---\nKeep the real totals.\n'
        imported = library.import_package(self.owner, zip_bytes({'SKILL.md': entry.encode(),
            'references/guide.md': b'Explain both original inputs and actual computed totals.',
            'assets/palette.bin': b'\x00\x01\xfe'}), 'fixed-guide.zip')
        version = library.version(self.owner, imported['assetId'], imported['versionId'])
        saved = spec([tool('first', 'def run(payload): return {"value":payload["n"] * 2 + payload["bonus"]}\n'),
                      tool('join', 'def run(payload): return {"total":payload["inputs"][0]["n"] + payload["inputs"][1]["value"]}\n')],
                     [{'id': 'first', 'kind': 'tool', 'tool': 'first', 'inputFrom': 'run.input'},
                      {'id': 'join', 'kind': 'tool', 'tool': 'join', 'inputFrom': ['run.input', 'steps.first']},
                      {'id': 'explain', 'kind': 'model', 'inputFrom': 'steps.join'}],
                     {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n']})
        saved['skills'] = [{'id': 'saved-guide', 'version': '1', 'content': entry, 'capabilityRef': {
            **{k: version[k] for k in ('assetId', 'versionId', 'configurationId', 'contentDigest')},
            'kind': 'skill', 'sourceIdentity': version['manifest']['source']['identity']}}]
        provider = ExplainProvider()
        with patch.object(self.w.runtime, 'provider', provider):
            doc = self.convert(saved)
            self.assertEqual(provider.calls, [])
            self.assertTrue(all(n['provenance']['type'] == 'modified' for n in doc['nodes']))
            frozen = doc['nodes'][-1]['component']
            self.assertEqual(base64.b64decode(frozen['files']['original/skills/1/package/original/assets/palette.bin']), b'\x00\x01\xfe')
            source = library.asset(self.owner, imported['assetId'])
            source['deletedAt'] = '2026-10-14'
            library.store.put(self.owner, 'asset', source['assetId'], source)
            app = self.save(doc)
            result = self.post('/' + app['id'] + '/verify', {'versionId': app['versionId'], 'cases': [
                {'input': {'n': 2, 'bonus': 3}, 'expectation': {'mode': 'fields', 'values': {'result.explanation': '实际合计：9', 'step_2.total': 9}}},
                {'input': {'n': 4, 'bonus': 1}, 'expectation': {'mode': 'fields', 'values': {'result.explanation': '实际合计：13', 'step_2.total': 13}}}]})
            self.assertEqual(result.status_code, 202, result.json)
            for rid in result.json['verification']['runIds']:
                run = self.wait_run(rid)
                self.assertEqual(run['status'], 'succeeded', run.get('error'))
            app = self.service.detail(self.owner, app['id'])
            self.assertEqual(app['verification']['status'], 'passed', app['verification'])
            self.enable(app)
        self.assertEqual(len(provider.calls), 2)
        for call in provider.calls:
            self.assertEqual(call['tools'], [])
            self.assertIn(saved['prompt'], call['system'])
            self.assertIn(entry, call['system'])
            self.assertIn('bonus', call['payload']['input'])
            self.assertEqual(set(call['payload']['stepOutputs']), {'first', 'join'})
            self.assertIn('actual computed totals', call['payload']['skillResources'][0]['resources'][0]['content'])

    def test_registered_tool_current_contract_sources_and_revocation(self):
        entry = self.w.runtime.tools.get('web_search')
        calls = []
        def run(arguments, context):
            calls.append(arguments)
            return ToolResult({'query': arguments['query']}, [{'title': 'Fixture', 'url': 'https://example.invalid'}])
        registered = {'id': 'search', 'version': '1', 'registeredTool': 'web_search', 'inputSchema': copy.deepcopy(entry.parameters),
                      'outputSchema': {'type': 'object', 'required': ['query', 'sources']}, 'permissions': {'network': False, 'files': []}}
        saved = spec([registered], [{'id': 'lookup', 'kind': 'tool', 'tool': 'search', 'inputFrom': 'run.input'}], entry.parameters)
        with patch.object(entry, 'available', return_value=True), patch.object(entry, 'handler', side_effect=run):
            doc = self.convert(saved)
            self.assertEqual(calls, [])
            snapshot = doc['nodes'][0]['component']
            result = invoke_snapshot(self.w.capabilities, self.owner, snapshot, {'source_1': {'query': 'fixture new input'}})
            self.assertEqual(result['result']['content']['sources'][0]['title'], 'Fixture')
            self.assertEqual(calls, [{'query': 'fixture new input'}])
            with patch.object(entry, 'available', return_value=False):
                with self.assertRaises(CapabilityError):
                    runtime_digest(self.w.capabilities, self.owner, snapshot)
        changed = copy.deepcopy(saved)
        changed['tools'][0]['inputSchema']['properties']['removed'] = {'type': 'string'}
        gap = migrate_pipeline(self.service, self.owner, changed)
        self.assertIn('当前接口', gap['gaps'][0])

    def test_file_field_and_literal_grants_use_current_bytes_and_no_historical_ids(self):
        for literal in (False, True):
            with self.subTest(literal=literal):
                old = 'old-private-file-123'
                grant = old if literal else 'fileId'
                code = ('def run(payload):\n    path=payload["filePaths"]["' + old + '"]\n' if literal else
                        'def run(payload):\n    path=payload["filePaths"][payload["fileId"]]\n    assert path == payload["filePath"]\n')
                code += '    values=open(path).read().split(",")\n    total=sum(int(v) for v in values)\n    open("/output/total.csv", "w").write("total\\n"+str(total))\n    return {"total":total,"artifacts":["total.csv"]}\n'
                schema = {'type': 'object', 'properties': {'fileId': {'type': 'string', 'default': old, 'const': old}},
                          'required': ['fileId'], 'additionalProperties': False} if not literal else {'type': 'object'}
                saved = spec([tool('read', code, schema, files=[grant])],
                             [{'id': 'read', 'kind': 'tool', 'tool': 'read', 'inputFrom': 'run.input'}], schema)
                doc = self.convert(saved)
                self.assertNotIn(old, canonical(doc))
                snapshot = doc['nodes'][0]['component']
                material = b'\n'.join(base64.b64decode(raw) for raw in snapshot['files'].values())
                self.assertNotIn(old.encode(), material)
                result = invoke_snapshot(self.w.capabilities, self.owner, snapshot,
                    {'source_1': {}, 'grant_1': '/input/current.csv'}, files={'current.csv': b'10,20,7'})
                self.assertEqual(result['result']['content'], {'total': 37, 'artifacts': ['total.csv']})
                self.assertEqual(result['files'], {'total.csv': b'total\n37'})
                with self.assertRaises(CapabilityError):
                    invoke_snapshot(self.w.capabilities, self.owner, snapshot,
                        {'source_1': {}, 'grant_1': '/input/current.csv'}, files={})

    def test_missing_dependency_is_an_explicit_gap_and_cannot_drop_downstream_input(self):
        saved = spec([{'id': 'unknown', 'registeredTool': 'not-approved', 'inputSchema': {'type': 'object'},
                       'outputSchema': {'type': 'object'}, 'permissions': {'files': []}},
                      tool('next', 'def run(payload): return payload\n')],
                     [{'id': 'missing', 'kind': 'tool', 'tool': 'unknown', 'inputFrom': 'run.input'},
                      {'id': 'next', 'kind': 'tool', 'tool': 'next', 'inputFrom': ['run.input', 'steps.missing']}])
        doc = migrate_pipeline(self.service, self.owner, saved)
        self.assertEqual(len(doc['gaps']), 2)
        self.assertIn('not-approved', doc['gaps'][0])
        self.assertIn('前序步骤 missing', doc['gaps'][1])
        self.assertTrue(definition(doc)[1])

    def test_exact_library_tool_survives_deletion_and_long_prompt_is_not_truncated(self):
        library = self.w.capabilities
        value = manifest()
        files = {'original/tool.py': b'def run(payload): return {"value": payload["n"] * 2}\n'}
        value['files'] = file_index(files)
        imported = library.import_package(self.owner, zip_bytes({**files, 'capability.json': canonical(value).encode()}), 'fixed-tool.zip')
        version = library.version(self.owner, imported['assetId'], imported['versionId'])
        ref = {**{key: version[key] for key in ('assetId', 'versionId', 'configurationId', 'contentDigest')},
               'kind': 'tool', 'sourceIdentity': value['source']['identity']}
        item = {'id': 'frozen', 'capabilityRef': ref, 'inputSchema': value['tool']['inputSchema'],
                'outputSchema': value['tool']['outputSchema'], 'permissions': {'files': []}}
        saved = spec([item], [{'id': 'frozen', 'kind': 'tool', 'tool': 'frozen', 'inputFrom': 'run.input'},
                             {'id': 'explain', 'kind': 'model', 'inputFrom': 'steps.frozen'}], value['tool']['inputSchema'])
        saved['prompt'] = '既有固定说明。' * 1500
        saved['skills'] = [{'id': 'long-guide', 'version': '1', 'content': '完整已有说明。' * 1500}]
        doc = self.convert(saved)
        model_prompt = doc['nodes'][-1]['component']['manifest']['tool']['execution']['steps'][-1]['prompt']
        self.assertGreater(len(model_prompt), 16000)
        self.assertIn(saved['skills'][0]['content'], model_prompt)
        source = library.asset(self.owner, imported['assetId'])
        source['deletedAt'] = '2026-10-14'
        library.store.put(self.owner, 'asset', source['assetId'], source)
        result = invoke_snapshot(library, self.owner, doc['nodes'][0]['component'], {'source_1': {'n': 11}})
        self.assertEqual(result['result']['content'], {'value': 22})

    def test_file_binding_roundtrip_uses_two_new_uploads_and_model_actual_metadata(self):
        schema = {'type': 'object', 'properties': {'fileId': {'type': 'string', 'default': 'historical-upload'}},
                  'required': ['fileId'], 'additionalProperties': False}
        saved = spec([tool('read', 'def run(payload): return {"total":sum(int(x) for x in open(payload["filePath"]).read().split(","))}\n',
                           schema, files=['fileId'])],
                     [{'id': 'join', 'kind': 'tool', 'tool': 'read', 'inputFrom': 'run.input'},
                      {'id': 'summary', 'kind': 'model', 'inputFrom': 'steps.join'}], schema)
        doc = self.convert(saved)
        provider = ExplainProvider()
        first = self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'1,2'), 'numbers.csv')}).json
        second = self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'10,20'), 'other.csv')}).json
        app = self.save(doc)
        with patch.object(self.w.runtime, 'provider', provider):
            response = self.post('/' + app['id'] + '/verify', {'versionId': app['versionId'], 'cases': [
                {'input': {}, 'fileBindings': {'legacy_file_1': first['id']}, 'expectation': {'mode': 'fields', 'values': {'result.explanation': '实际合计：3'}}},
                {'input': {}, 'fileBindings': {'legacy_file_1': second['id']}, 'expectation': {'mode': 'fields', 'values': {'result.explanation': '实际合计：30'}}}]})
            self.assertEqual(response.status_code, 202, response.json)
            for rid in response.json['verification']['runIds']:
                current = self.wait_run(rid)
                self.assertEqual(current['status'], 'succeeded', current.get('error'))
        self.assertEqual(self.service.detail(self.owner, app['id'])['verification']['status'], 'passed')
        self.assertEqual({c['payload']['input']['fileId'] for c in provider.calls}, {first['id'], second['id']})
        self.assertEqual({c['payload']['files'][0]['size'] for c in provider.calls}, {3, 5})
        self.assertTrue(all(c['payload']['files'][0]['name'] == 'legacy_file_1' for c in provider.calls))


if __name__ == '__main__':
    unittest.main()
