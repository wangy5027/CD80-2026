"""Capture exact successful evidence and exercise portable reuse with real WASI."""
import ast
import copy
import io
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.modules.capability_library.contracts import CapabilityError
from backend.app.modules.capability_library.asset_validation import verify_suite
from backend.app.modules.capability_library.sharing import assert_shareable
from tests import test_capability_community as community_fixture


class CaptureDependenciesTests(unittest.TestCase):
    def setUp(self):
        self.f = community_fixture.CommunityCapabilityTests()
        self.f.setUp()
        self.lib, self.w, self.owner = self.f.lib, self.f.w, self.f.owner

    def tearDown(self):
        self.f.tearDown()

    def preview(self, run, kind='skill', step=None):
        body = {'runId': run['id'], 'kind': kind}
        if step:
            body['stepId'] = step
        return self.f.client.post('/desktop/api/evolution/captures/preview', json=body)

    def version(self, owner, saved):
        return self.lib.version(owner, saved['assetId'], saved['versionId'])

    def enable(self, owner, version):
        asset = self.lib.asset(owner, version['assetId'])
        self.lib.update_asset(owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']},
                              '"' + str(asset['revision']) + '"')

    def stored_run(self, refs, steps):
        run = {'id': 'fixture-capture-run', 'status': 'succeeded', 'message': 'Reusable fixture method',
               'payload': {}, 'loadedRefs': [{k: r[k] for k in ('assetId', 'versionId')} for r in refs],
               'artifacts': [], 'steps': steps}
        with self.w.sub_agents.store.transaction() as db:
            self.w.sub_agents.store.put(db, self.owner, 'run', run['id'], run)
        return run

    def verify_tool(self, owner, client, version, values, result_key='combined'):
        cases = []
        for current in (values, [v + 2 for v in values]):
            ids = []
            for index, value in enumerate(current):
                raw = json.dumps({'content': {'total': str(value)}}).encode()
                response = client.post('/desktop/api/files', data={'file': (io.BytesIO(raw), f'new-{index}.json')}, content_type='multipart/form-data')
                self.assertEqual(response.status_code, 201, response.json)
                ids.append(response.json['id'])
            cases.append({'input': {'input_' + str(i + 1): '/input/' + key + '.json' for i, key in enumerate(ids)},
                'fileIds': ids, 'expectation': {'mode': 'json_equals', 'value': {result_key: str(sum(current))}}})
        reference = {k: version[k] for k in ('assetId', 'versionId', 'configurationId')}
        result = self.f.wait(verify_suite(self.lib, owner, {**reference, 'cases': cases}), owner)
        self.assertEqual(result['status'], 'passed', result)
        self.enable(owner, version)
        return {**reference, 'input': cases[0]['input'], 'fileIds': cases[0]['fileIds']}

    def verify_skill(self, owner, version):
        def events(*_args, **_kwargs):
            yield {'type': 'token', 'text': 'Use the exact subtotal tool twice, then combine current results.'}
            yield {'type': 'turn_end', 'tool_calls': []}
        def skill_model(stage, payload, *_args, **_kwargs):
            if stage == 'approve':
                return {'decision': 'approve', 'reason': 'Use the frozen required subtotal implementation.'}
            if stage == 'review':
                return {'decision': 'accept', 'answer': 'Reviewed real subtotal for current inputs.'}
            complete = [s for s in payload['steps'] if s['status'] == 'succeeded']
            if complete:
                return {'type': 'finish', 'answer': 'Current subtotal delivered.', 'resultStepId': complete[-1]['id'], 'artifactIds': []}
            initial = next(o for o in payload['observations'] if 'inputs' in o)
            return {'type': 'tool', 'name': 'tool_1', 'arguments': {'values': [str(n) for n in initial['inputs']['values']]},
                    'purpose': 'Calculate the current subtotal with the saved dependency.'}
        body = {**{k: version[k] for k in ('assetId', 'versionId', 'configurationId')}, 'cases': [
            {'input': {'values': values}, 'fileIds': [], 'expectation': {'mode': 'manual_review',
                'prompt': 'Explain the method for ' + str(values), 'criteria': 'Use the subtotal tool and current inputs.'}}
            for values in ([1, 2], [3, 5])]}
        with patch.object(self.w.runtime.provider, 'events', events), patch.object(self.w.sub_agents.model, 'call', skill_model):
            result = self.f.wait(verify_suite(self.lib, owner, body), owner)
        self.lib.review(owner, result['verificationId'], {'decision': 'accept'})
        self.enable(owner, version)

    def export_bytes(self, owner, client, version):
        exported = self.lib.export(owner, {**version, 'format': 'platform'})
        response = client.get(exported['downloadUrl'])
        self.assertEqual(response.status_code, 200)
        return response.data

    def save(self, draft):
        saved = self.lib.draft(self.owner, draft)
        return self.version(self.owner, self.lib.save_draft(self.owner, saved['draftId'], {'draftRevision': saved['revision']}))

    def execute_steps(self, tool, skill):
        self.enable(self.owner, self.version(self.owner, skill))
        def model(stage, payload, *_args, **_kwargs):
            if stage == 'plan':
                return {'mode': 'delegate', 'goal': 'Combine two subtotals and multiply by ten',
                        'constraints': [], 'requiredCapabilities': [], 'allowTemporaryCode': True}
            if stage == 'approve':
                return {'decision': 'approve', 'reason': 'Isolated fixture'}
            if stage == 'review':
                return {'decision': 'accept', 'answer': 'Fixture result 33.00'}
            steps = payload['steps']
            if len(steps) < 2:
                return {'type': 'tool', 'name': 'cap_' + tool['versionId'], 'arguments': {'values': ['0.10', '0.20'] if not steps else ['1', '2']}, 'purpose': 'Calculate a subtotal'}
            if len(steps) == 2:
                return {'type': 'code', 'inputRefs': [], 'code': "def run(payload):\n    raise ValueError('failed attempt')\n", 'purpose': 'Failed attempt', 'reason': 'Exercise preserved execution failure'}
            if len(steps) == 3:
                return {'type': 'code', 'inputRefs': [], 'code': 'def run(payload)\n    return {}', 'purpose': 'Another failed attempt', 'reason': 'Exercise failed syntax evidence'}
            if len(steps) == 4:
                ids = [step['result']['artifacts'][0]['id'] for step in steps[:2]]
                code = ("import json\nfrom decimal import Decimal\ndef run(payload):\n"
                        "    a=Decimal(json.load(open(payload['files'][%r]))['content']['total'])\n"
                        "    b=Decimal(json.load(open(payload['files'][%r]))['content']['total'])\n"
                        "    total=a+b\n    open('/output/intermediate.txt','w').write(str(total))\n"
                        "    return {'combined':str(total)}\n") % tuple(ids)
                return {'type': 'code', 'inputRefs': ids, 'code': code, 'purpose': 'Combine actual tool files', 'reason': 'Use both actual current subtotals'}
            if len(steps) == 5:
                prior = next(item for item in payload['artifacts'] if item['name'] == 'intermediate.txt')
                code = ("from decimal import Decimal\ndef run(payload):\n"
                        "    value=Decimal(open(payload['files'][%r]).read())\n    return {'scaled':str(value*10)}\n") % prior['id']
                return {'type': 'code', 'inputRefs': [prior['id']], 'code': code, 'purpose': 'Scale the intermediate result', 'reason': 'Use prior actual output'}
            return {'type': 'finish', 'answer': 'Combined and scaled actual values.', 'artifactIds': [item['id'] for item in payload['artifacts'] if item.get('stepId') == steps[-1]['id']]}
        service = self.w.sub_agents
        with patch.object(service.model, 'call', model):
            run = service.submit(self.owner, 'new', {'message': 'Combine two subtotals and multiply by ten',
                'capabilityRefs': [{k: skill[k] for k in ('assetId', 'versionId')}], 'fileIds': [], 'searchMode': 'off'}, 'capture-chain')
            end = time.monotonic() + 30
            while time.monotonic() < end:
                run = service.store.run(self.owner, run['id'])
                if run['status'] in {'succeeded', 'failed', 'cancelled'}:
                    break
                time.sleep(.02)
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual(run['steps'][-1]['result']['content'], {'scaled': '33.00'})
        return run

    def test_actual_multistep_capture_export_import_verify_publish_copy_and_new_inputs(self):
        tool, skill = self.f.skill_with_tool()
        run = self.execute_steps(tool, skill)
        self.assertEqual([step['status'] for step in run['steps']], ['succeeded', 'succeeded', 'failed', 'failed', 'succeeded', 'succeeded'])
        self.assertEqual([self.version(self.owner, r)['manifest']['kind'] for r in run['loadedRefs']], ['skill'])
        captured = self.preview(run).json
        expected = self.version(self.owner, skill)['manifest']['dependencies'][0]
        self.assertEqual([(d['sourceIdentity'], d['contentDigest'], d['required']) for d in captured['draft']['manifest']['dependencies']],
                         [(expected['sourceIdentity'], expected['contentDigest'], True)])
        skill_version = self.save(captured['draft'])
        raw = self.export_bytes(self.owner, self.f.client, skill_version)
        imported = self.lib.import_package(self.f.reader, raw, 'component.json')
        self.assertEqual(imported['readiness'], 'unverified')
        # Full authored exports preserve frozen dependencies without enabling them.
        original_tool = self.version(self.owner, tool)
        tool_raw = self.export_bytes(self.owner, self.f.client, original_tool)
        prepared = self.version(self.f.reader, self.lib.import_package(self.f.reader, tool_raw, 'dependency.zip'))
        body = {**{k: prepared[k] for k in ('assetId', 'versionId', 'configurationId')}, 'input': {'values': ['7', '8']}, 'fileIds': []}
        checked = self.f.wait(verify_suite(self.lib, self.f.reader, {**body, 'cases': [
            {'input': {'values': values}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': total}}}
            for values, total in [(['7', '8'], '15'), (['2', '4'], '6')]]}), self.f.reader)
        self.assertEqual(checked['status'], 'passed')
        self.enable(self.f.reader, prepared)
        imported_version = self.lib.check(self.f.reader, self.version(self.f.reader, imported))
        self.verify_skill(self.f.reader, imported_version)
        public = self.f.post('/preview', {k: imported_version[k] for k in ('assetId', 'versionId')}, client=self.f.other)
        self.assertEqual(public.status_code, 200, public.json)
        self.assertEqual(len(public.json['dependencies']), 1)
        released = self.f.post('', {**{k: imported_version[k] for k in ('assetId', 'versionId')},
            'snapshotDigest': public.json['snapshotDigest'], 'confirmSharing': True}, client=self.f.other)
        self.assertEqual(released.status_code, 201, released.json)
        recipient = self.f.app.test_client()
        owner = recipient.post('/desktop/api/register', json={'email': 'capture-copy@example.invalid', 'password': 'test-password'}).json['user']['id']
        copied = self.f.post('/' + released.json['releaseId'] + '/copy', client=recipient)
        self.assertEqual(copied.status_code, 201, copied.json)
        copied_skill = self.version(owner, copied.json)
        self.assertEqual(len(copied_skill['embeddedSnapshot']['dependencies']), 1)
        self.assertEqual(copied_skill['manifest']['dependencies'], imported_version['manifest']['dependencies'])
        self.assertEqual(copied_skill['admission']['source'], 'community')
        self.assertFalse(copied_skill['admission']['validationRequired'])
        self.assertEqual(copied_skill['readiness'], 'ready')
        for asset in self.lib.store.list(owner, 'asset'):
            self.assertFalse(asset['enabled'])
            self.assertEqual(asset['readiness'], 'ready')
            version = self.version(owner, {'assetId': asset['assetId'], 'versionId': asset['latestVersionId']})
            self.assertEqual(version['admission']['source'], 'community')
            self.assertFalse(version['admission']['validationRequired'])
        self.assertEqual(self.lib.store.list(owner, 'verification'), [])
        default = self.preview(run, 'tool').json
        self.assertEqual(default['selectedStepId'], run['steps'][-1]['id'])
        self.assertEqual([item['stepId'] for item in default['steps']], [run['steps'][4]['id'], run['steps'][5]['id']])
        self.assertEqual([item['inputCount'] for item in default['steps']], [2, 1])
        self.assertEqual(default['steps'][0]['inputs'][0]['sourceType'], 'tool')
        self.assertEqual(default['steps'][1]['inputs'][0]['sourceType'], 'code')
        self.assertTrue(any('不自动组合' in warning for warning in default['warnings']))
        self.assertEqual(self.preview(run, 'tool', run['steps'][2]['id']).json['code'], 'NO_REUSABLE_CODE')
        chosen = self.preview(run, 'tool', run['steps'][4]['id']).json
        self.assertEqual(chosen['selectedStepId'], run['steps'][4]['id'])
        self.assertNotIn('steps', chosen['draft'])
        self.assertNotIn('original/cd80_plot.py', [item['path'] for item in chosen['draft']['files']])
        original_source = self.w.sub_agents.store.artifact(self.owner, run['steps'][4]['result']['codeArtifactId'])[1].decode()
        saved_source = next(item['text'] for item in chosen['draft']['files'] if item['path'] == 'original/source.py')
        original_helper = next(node.args[0].value for node in ast.walk(ast.parse(original_source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'exec')
        saved_helper = next(node.args[0].value for node in ast.walk(ast.parse(saved_source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'exec')
        self.assertEqual(saved_helper, original_helper)
        for old in run['artifacts']:
            self.assertNotIn(old['id'], json.dumps(chosen['draft']))
        tool_version = self.save(chosen['draft'])
        tool_raw = self.export_bytes(self.owner, self.f.client, tool_version)
        restored = self.version(self.f.reader, self.lib.import_package(self.f.reader, tool_raw, 'component.json'))
        body = self.verify_tool(self.f.reader, self.f.other, restored, [10, 20])
        self.assertEqual(self.lib.invoke(self.f.reader, body)['result']['content'], {'combined': '30'})
        public = self.f.post('/preview', {k: restored[k] for k in ('assetId', 'versionId')}, client=self.f.other)
        self.assertEqual(public.status_code, 200, public.json)
        release = self.f.post('', {**{k: restored[k] for k in ('assetId', 'versionId')},
            'snapshotDigest': public.json['snapshotDigest'], 'confirmSharing': True}, client=self.f.other)
        self.assertEqual(release.status_code, 201, release.json)
        copied = self.f.post('/' + release.json['releaseId'] + '/copy', client=recipient)
        self.assertEqual(copied.status_code, 201, copied.json)
        local = self.version(owner, copied.json)
        self.assertFalse(self.lib.asset(owner, local['assetId'])['enabled'])
        self.assertEqual(local['readiness'], 'ready')
        self.assertEqual(local['admission']['source'], 'community')
        self.assertEqual(self.lib.store.list(owner, 'verification'), [])
        # An exact public copy is eligible for explicit enablement and new-input
        # execution without a recipient-side verification suite.
        self.enable(owner, local)
        ids = []
        for index, value in enumerate((7, 8), 1):
            raw = json.dumps({'content': {'total': str(value)}}).encode()
            response = recipient.post('/desktop/api/files',
                data={'file': (io.BytesIO(raw), f'copied-input-{index}.json')},
                content_type='multipart/form-data')
            self.assertEqual(response.status_code, 201, response.json)
            ids.append(response.json['id'])
        request = {**{k: local[k] for k in ('assetId', 'versionId', 'configurationId')},
                   'input': {'input_1': '/input/' + ids[0] + '.json',
                             'input_2': '/input/' + ids[1] + '.json'},
                   'fileIds': ids}
        self.assertEqual(self.lib.invoke(owner, request)['result']['content'], {'combined': '15'})
        self.assertEqual(self.lib.store.list(owner, 'verification'), [])
        # Copied packages carry algorithms, not the original result 3.30 or its grants.
        self.assertEqual(self.lib.files(owner, local), self.lib.files(self.f.reader, restored))

    def test_legacy_plot_import_without_recorded_helper_is_explicitly_rejected(self):
        source = b'from cd80_plot import line_chart\ndef run(payload):\n    return {}\n'
        run = self.stored_run([], [{'id': 'code', 'type': 'code', 'status': 'succeeded', 'inputRefs': [],
                                   'result': {'codeArtifactId': 'old-source'}}])
        meta = {'id': 'old-source', 'name': 'analysis.py'}
        run['artifacts'] = [meta]
        with self.w.sub_agents.store.transaction() as db:
            self.w.sub_agents.store.put(db, self.owner, 'run', run['id'], run)
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (self.owner, meta['id'], run['id'], json.dumps(meta), source))
        response = self.preview(run, 'tool')
        self.assertEqual(response.status_code, 422, response.json)
        self.assertEqual(response.json['code'], 'SOURCE_NEEDS_EDIT')

    def test_only_successful_bound_tool_calls_become_dependencies(self):
        first = self.f.validated()
        def renamed(manifest, _files):
            manifest['source']['identity'] = 'fixture/second-tool'
        second = self.f.validated(edit=renamed)
        run = self.stored_run([first, second], [
            {'id': 'failed', 'type': 'tool', 'name': 'cap_' + first['versionId'], 'status': 'failed'},
            {'id': 'success', 'type': 'tool', 'name': 'cap_' + second['versionId'], 'status': 'succeeded'}])
        response = self.preview(run)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual([d['sourceIdentity'] for d in response.json['draft']['manifest']['dependencies']], ['fixture/second-tool'])
        run = self.stored_run([first, second], [])
        self.assertEqual(self.preview(run).json['draft']['manifest']['dependencies'], [])
        run = self.stored_run([first], [{'id': 'unbound', 'type': 'tool', 'name': 'cap_' + second['versionId'], 'status': 'succeeded'}])
        self.assertEqual(self.preview(run).json['code'], 'SOURCE_NEEDS_EDIT')

    def test_identical_tool_copies_merge_only_when_configurations_match(self):
        first = self.f.validated()
        raw = self.f.sample()
        second = self.lib.import_package(self.owner, raw, 'same-content.zip', force_new=True)
        first_version, second_version = self.version(self.owner, first), self.version(self.owner, second)
        self.assertEqual(first_version['contentDigest'], second_version['contentDigest'])
        self.assertEqual(first_version['configurationId'], second_version['configurationId'])
        run = self.stored_run([first, second], [
            {'id': 'first', 'type': 'tool', 'name': 'cap_' + first['versionId'], 'status': 'succeeded'},
            {'id': 'second', 'type': 'tool', 'name': 'cap_' + second['versionId'], 'status': 'succeeded'}])
        response = self.preview(run)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json['draft']['manifest']['dependencies']), 1)
        # Model an existing exact binding with a different connection/dependency
        # configuration; content identity alone cannot represent both in one Skill.
        second_version['configurationId'] = 'config_different_source_binding'
        self.lib.store.put(self.owner, 'version', second_version['versionId'], second_version)
        response = self.preview(run)
        self.assertEqual(response.status_code, 422, response.json)
        self.assertEqual(response.json['code'], 'SOURCE_NEEDS_EDIT')
        self.assertIn('配置', response.json['error'])

    def test_unsupported_optional_nested_and_missing_locks_are_not_silently_dropped(self):
        tool, skill = self.f.skill_with_tool()
        original = self.version(self.owner, skill)
        for change in ('optional', 'missing', 'mismatched', 'nested'):
            version = copy.deepcopy(original)
            if change == 'optional':
                version['manifest']['dependencies'][0]['required'] = False
            elif change == 'missing':
                version['dependencyLocks'] = []
            elif change == 'mismatched':
                version['manifest']['dependencies'][0]['contentDigest'] = '0' * 64
            else:
                version['dependencyLocks'] = [{k: original[k] for k in ('assetId', 'versionId', 'configurationId')}]
            self.lib.store.put(self.owner, 'version', version['versionId'], version)
            run = self.stored_run([skill], [{'id': 'call', 'type': 'tool', 'name': 'cap_' + tool['versionId'], 'status': 'succeeded'}])
            with self.subTest(change=change):
                response = self.preview(run)
                self.assertEqual(response.status_code, 422, response.json)
                self.assertEqual(response.json['code'], 'SOURCE_NEEDS_EDIT')
        self.lib.store.put(self.owner, 'version', original['versionId'], original)
        run = self.stored_run([skill], [{'id': 'script', 'type': 'tool', 'name': 'library_script', 'arguments': {'assetId': skill['assetId']}, 'status': 'succeeded'}])
        self.assertEqual(self.preview(run).json['code'], 'SOURCE_NEEDS_EDIT')

    def test_used_skill_keeps_all_required_exact_tools_and_rejects_conflicting_sources(self):
        tool, skill = self.f.skill_with_tool()
        def renamed(manifest, _files):
            manifest['source']['identity'] = 'fixture/other-required'
        other = self.f.validated(edit=renamed)
        version, dependency = self.version(self.owner, skill), self.version(self.owner, other)
        version['manifest']['dependencies'].append({'sourceIdentity': dependency['manifest']['source']['identity'],
            'contentDigest': dependency['contentDigest'], 'required': True, 'reason': 'Required by source method'})
        version['dependencyLocks'].append({k: dependency[k] for k in ('assetId', 'versionId', 'configurationId')})
        self.lib.store.put(self.owner, 'version', version['versionId'], version)
        run = self.stored_run([skill], [{'id': 'call', 'type': 'tool', 'name': 'cap_' + tool['versionId'], 'status': 'succeeded'}])
        response = self.preview(run)
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(len(response.json['draft']['manifest']['dependencies']), 2)
        run = self.stored_run([skill], [{'id': 'resource', 'type': 'tool', 'name': 'library_resource',
            'arguments': {'assetId': skill['assetId']}, 'status': 'succeeded'}])
        self.assertEqual(len(self.preview(run).json['draft']['manifest']['dependencies']), 2)
        def changed(manifest, _files):
            manifest['description'] = 'A different content version of the same tool source'
        conflict = self.version(self.owner, self.f.validated(edit=changed))
        version['manifest']['dependencies'].append({'sourceIdentity': conflict['manifest']['source']['identity'],
            'contentDigest': conflict['contentDigest'], 'required': True, 'reason': 'Conflicting historical dependency'})
        version['dependencyLocks'].append({k: conflict[k] for k in ('assetId', 'versionId', 'configurationId')})
        self.lib.store.put(self.owner, 'version', version['versionId'], version)
        self.assertEqual(self.preview(run).json['code'], 'SOURCE_NEEDS_EDIT')


class CapturePythonSharingTests(unittest.TestCase):
    def test_executed_bootstrap_and_png_bytes_are_shareable(self):
        helper = (Path(__file__).resolve().parents[1] / 'backend/app/modules/sub_agent/png_plot.py').read_text(encoding='utf-8')
        source = ("import sys as _sys, types as _types\n_cd80_plot=_types.ModuleType('cd80_plot')\nexec(" + repr(helper)
                  + ",_cd80_plot.__dict__)\n_sys.modules['cd80_plot']=_cd80_plot\ndef run(payload): return {}\n")
        # Capture reformats the executed source; embedded \\x89PNG\\r\\n must not be read as UNC.
        assert_shareable({}, {'original/source.py': (ast.unparse(ast.parse(source)) + '\n').encode()})
        assert_shareable({}, {'original/png.py': b"data=b'\\x89PNG\\r\\n\\x1a\\n'\n"})

    def test_private_literals_comments_bytes_embedded_source_and_secrets_remain_blocked(self):
        cases = [
            'value = r"C:\\private\\sample.csv"',
            'value = r"\\\\server\\share\\sample.csv"',
            '# C:\\private\\sample.csv\ndef run(payload): return {}',
            '# \\\\server\\share\\sample.csv\ndef run(payload): return {}',
            'value = br"\\\\server\\share\\sample.csv"',
            'value = b"\\x43:\\x5cprivate\\x5cdata.csv"',
            'value = "/desktop/api/sub-agent-artifacts/old-result"',
            'value = "/desktop/api/files/old-input"',
            'value = "project-file:private.csv"',
            'exec(' + repr('value = r"C:\\private\\sample.csv"') + ')',
            'invalid syntax C:\\private\\sample.csv',
        ]
        for source in cases:
            with self.subTest(source=source), self.assertRaises(CapabilityError) as raised:
                assert_shareable({}, {'original/source.py': source.encode()})
            self.assertEqual(raised.exception.code, 'SHARING_PRIVATE_RESOURCE')
        for source in ['# api_key="actual-secret-value"', 'api_key="actual-secret-value"']:
            with self.subTest(source=source), self.assertRaises(CapabilityError) as raised:
                assert_shareable({}, {'original/source.py': source.encode()})
            self.assertEqual(raised.exception.code, 'SHARING_CONTAINS_SECRET')

    def test_embedded_source_has_depth_and_size_bounds(self):
        source = 'value = 1'
        for _ in range(10):
            source = 'exec(' + repr(source) + ')'
        with self.assertRaises(CapabilityError) as raised:
            assert_shareable({}, {'original/source.py': source.encode()})
        self.assertEqual(raised.exception.code, 'SHARING_UNREVIEWABLE_FILE')
        with self.assertRaises(CapabilityError) as raised:
            assert_shareable({}, {'original/source.py': ('#' + 'x' * (4 * 1024 * 1024)).encode()})
        self.assertEqual(raised.exception.status, 413)


if __name__ == '__main__':
    unittest.main()
