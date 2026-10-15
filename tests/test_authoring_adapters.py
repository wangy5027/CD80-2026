"""Candidate validation/commit uses isolated databases, WASI and mocked models."""
import copy
import io
import json
import threading
import unittest
from unittest.mock import patch

from tests import test_asset_apps as app_fixtures
from tests import test_asset_creator as creator_fixtures
from backend.app.modules.agent_platform.authoring_adapters import AuthoringAdapters
from backend.app.modules.agent_platform.asset_creator import _extract_definition
from backend.app.modules.capability_library.contracts import CapabilityError, fail

sample = app_fixtures.sample


def plan(kind='app', manual=False):
    cases = []
    for index, (values, expected) in enumerate((([100, 120, 90, 108], {'change': 8, 'drawdown': 25, 'count': 4}),
                                               ([80, 100, 95, 120], {'change': 50, 'drawdown': 5, 'count': 4}))):
        if kind == 'app': expected = {'analysis': expected}
        checks = [{'id': 'expected', 'mode': 'json_equals', 'requirement': '按收盘序列独立计算涨幅及最大回撤', 'payload': {'value': expected}}]
        if manual:
            checks = [{'id': 'shape', 'mode': 'delivery', 'requirement': '实际交付为文字', 'payload': {'schema': {'type': 'string'}}},
                      {'id': 'effect', 'mode': 'manual_review', 'requirement': '解释准确且符合输入', 'payload': {'criteria': '逐项确认解释对应本次输入，未编造事实'}}]
        cases.append({'id': 'case' + str(index), 'label': '合成用例' + str(index),
                      'role': 'original' if index == 0 else 'different_input', 'input': {'prices': values},
                      'task': '解释合成价格序列' + str(index), 'fileRefs': [], 'checks': checks,
                      'basis': {'claim': 'generated', 'description': '合成数据，依规则独立推算'}})
    return {'cases': cases, 'issues': []}


class AuthoringAdapterTests(unittest.TestCase):
    setUp = app_fixtures.AssetAppTests.setUp
    tearDown = app_fixtures.AssetAppTests.tearDown
    source = creator_fixtures.AssetCreatorTests.source

    def adapter(self):
        return AuthoringAdapters(self.w)

    def draft(self, kind):
        doc = sample()
        return _extract_definition({'name': doc['name'], 'description': doc['description'], 'definition': doc}, doc['nodes'], kind)

    def test_app_candidate_can_be_checked_but_direct_commit_cannot_enable(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'app', sample())
        self.assertEqual(prepared['issues'], [])
        checked = adapter.validate(self.owner, prepared, plan())
        self.assertEqual(checked['status'], 'passed', checked)
        self.assertEqual(checked['cases'][1]['result']['analysis']['change'], 50)
        self.assertTrue(checked['cases'][0]['artifacts'])
        self.assertEqual(self.service.listing(self.owner), [])
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        self.assertEqual(self.service.store.listing('versions', self.owner), [])
        review = {'accepted': True, 'reviewDigest': 'reviewed'}
        # The coordinator can wrap evidence with its own immutable record identity.
        wrapped = {**checked, 'id': 'coordinator-id', 'planId': 'plan', 'createdAt': 'wrapped'}
        with self.assertRaises(CapabilityError) as blocked:
            adapter.commit(self.owner, prepared, wrapped, key='one', review=review)
        self.assertEqual(blocked.exception.code, 'VERIFICATION_REQUIRED')
        self.assertEqual(self.service.store.listing('versions', self.owner), [])
        detail = self.service.save(self.owner, {'definition': prepared['content']}, 'saved-first')
        self.assertIsNone(detail['enabledVersionId'])
        self.assertEqual(detail['verification']['status'], 'unverified')
        forged = copy.deepcopy(checked); forged['cases'][0]['result'] = {'invented': True}
        with self.assertRaises(CapabilityError) as blocked:
            adapter.commit(self.owner, prepared, forged, key='fake', review=review)
        self.assertEqual(blocked.exception.code, 'VERIFICATION_REQUIRED')

    def test_tool_snapshot_never_installs_until_review_and_runs_after_enable(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'tool', self.draft('tool'))
        self.assertEqual(prepared['issues'], [])
        checked = adapter.validate(self.owner, prepared, plan('tool'))
        self.assertEqual(checked['status'], 'passed', checked)
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'version'), [])
        saved = adapter.commit(self.owner, prepared, checked, key='tool-one', review={'accepted': True, 'reviewDigest': 'r'})
        self.assertEqual(adapter.commit(self.owner, prepared, checked, key='tool-one', review={'accepted': True, 'reviewDigest': 'r'}), saved)
        asset = self.w.capabilities.asset(self.owner, saved['assetId'])
        version = self.w.capabilities.admit(self.owner, saved['assetId'], saved['versionId'])
        self.assertTrue(asset['enabled'])
        self.assertEqual(version['embeddedSnapshot'], prepared['material']['snapshot'])
        self.assertEqual(len(self.w.capabilities.store.list(self.owner, 'version')), 1)
        other = self.other.get('/desktop/api/session').json['user']['id']
        with self.assertRaises(CapabilityError):
            adapter.commit(other, prepared, checked, key='tool-one', review={'accepted': True, 'reviewDigest': 'r'})

    def test_original_only_plan_can_verify_and_commit_with_saved_independent_case(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'tool', self.draft('tool'))
        proposed = plan('tool')
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(proposed)):
            generated = adapter.plan(self.owner, prepared, instruction='计算涨幅与回撤')
        self.assertEqual(len(generated['cases']), 1)
        self.assertEqual(generated['cases'][0]['role'], 'original')
        checked = adapter.validate(self.owner, prepared, generated)
        self.assertEqual(checked['status'], 'passed', checked)
        saved = adapter.commit(self.owner, prepared, checked, key='single-original',
                               review={'accepted': True, 'reviewDigest': 'r'})
        version = self.w.capabilities.admit(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(len(version['testCases']), 1)
        self.assertEqual(version['testCases'][0]['input'], proposed['cases'][0]['input'])
        self.assertEqual(version['testCases'][0]['expectation'],
                         {'mode': 'json_equals', 'value': proposed['cases'][0]['checks'][0]['payload']['value']})
        # No optional case is required, but an omitted original is still invalid.
        bad = copy.deepcopy(generated)
        bad['cases'][0]['role'] = 'different_input'
        with self.assertRaises(CapabilityError):
            adapter.validate(self.owner, prepared, bad)

    def test_saved_component_case_uses_frozen_checks_not_actual_result(self):
        independent = plan('skill')
        independent['cases'] = independent['cases'][:1]
        independent['cases'][0]['checks'] = [{'mode': 'manual_review', 'payload': {'criteria': '只陈述输入中存在的数字'}}]
        independent['cases'][0]['result'] = {'incorrect': 'must never become an expectation'}
        saved = self.adapter()._saved_component_cases('skill', independent)
        self.assertEqual(saved[0]['expectation']['criteria'], '只陈述输入中存在的数字')
        self.assertNotIn('incorrect', json.dumps(saved))
        independent['cases'][0]['checks'] = [{'mode': 'matching', 'payload': {'expected': True}}]
        self.assertEqual(self.adapter()._saved_component_cases('tool', independent), [])

    def test_edit_target_keeps_exact_version_original_case_through_snapshot_conversion(self):
        from backend.app.modules.agent_platform.authoring_context import target_content
        from backend.app.modules.capability_library.components import install_snapshot
        prepared = self.adapter().prepare(self.owner, 'tool', self.draft('tool'))
        original = [{'label': '指定原任务', 'input': {'prices': [10, 20]}, 'fileIds': [],
                     'expectation': {'mode': 'json_equals', 'value': {'change': 100, 'drawdown': 0, 'count': 2}}}]
        saved = install_snapshot(self.w.capabilities, self.owner, prepared['material']['snapshot'], test_cases=original)
        content, target = target_content(self.w, self.owner, 'tool',
            {'assetId': saved['assetId'], 'baseVersionId': saved['versionId']})
        self.assertEqual(content['testCases'], original)
        self.assertEqual(self.adapter()._content('tool', content)['testCases'], original)
        self.assertEqual(self.adapter().prepare(self.owner, 'tool', content)['content']['testCases'], original)
        self.assertEqual(target['baseVersionId'], saved['versionId'])

    def test_legacy_app_edit_uses_original_expectation_recovered_by_detail(self):
        from backend.app.modules.agent_platform.authoring_context import target_content
        saved = self.service.save(self.owner, {'definition': sample()}, 'legacy-cases')
        original = {'label': '已保存的原例', 'input': {'prices': [10, 20]}, 'fileBindings': {},
                    'expectation': {'mode': 'fields', 'values': {'analysis.change': 100}}}
        with self.service.store.transaction() as db:
            version = self.service.store.get(db, 'versions', self.owner, saved['versionId'])
            version['definition'].pop('testCases', None)
            version['verification']['cases'] = [{**original, 'status': 'failed', 'result': {'wrong': 999}}]
            self.service.store.put(db, 'versions', self.owner, version)
        content, _ = target_content(self.w, self.owner, 'app',
            {'assetId': saved['id'], 'baseVersionId': saved['versionId']})
        self.assertEqual(content['testCases'], [original])
    def test_skill_checks_record_real_run_and_manual_effect_review(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'skill', self.draft('skill'))
        self.assertEqual(prepared['issues'], [])
        with patch.object(self.w.sub_agents, 'run_skill', return_value={'status': 'succeeded', 'result': '合成说明', 'files': {}, 'skillCallId': 'mock-skill'}) as invoke:
            checked = adapter.validate(self.owner, prepared, plan('skill', manual=True))
        self.assertEqual(invoke.call_count, 2)
        self.assertEqual(checked['status'], 'awaiting_review')
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        with self.assertRaises(CapabilityError):
            adapter.commit(self.owner, prepared, checked, key='unreviewed')
        saved = adapter.commit(self.owner, prepared, checked, key='skill-reviewed', review={'accepted': True, 'reviewDigest': 'r'})
        self.assertEqual(self.w.capabilities.asset(self.owner, saved['assetId'])['activeVersionId'], saved['versionId'])

    def test_real_skill_script_candidate_and_changed_model_cannot_commit_stale_evidence(self):
        from backend.app.modules.capability_library.components import snapshot_from_package
        manifest = {'format': 'cd80.capability/1', 'kind': 'skill', 'name': 'sum-values', 'description': '计算本次合成序列合计',
            'source': {'identity': 'test/authoring-sum', 'format': 'platform'}, 'dependencies': [],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': True},
            'skill': {'entry': 'SKILL.md', 'scripts': [{'type': 'python_wasi', 'path': 'scripts/sum.py', 'callable': 'run'}]}}
        snapshot = snapshot_from_package(manifest, {'SKILL.md': b'---\nname: sum-values\ndescription: Sum supplied values.\n---\nUse scripts/sum.py and deliver total.csv.',
            'scripts/sum.py': b'def run(payload):\n    total=sum(payload["values"])\n    open("/output/total.csv","w").write("total\\n"+str(total))\n    return {"total":total}\n'})
        class ScriptModel:
            def call(self, stage, payload, stopped, on_response):
                if stage == 'approve': return {'decision': 'approve', 'reason': '只执行本次合成计算'}
                if stage == 'review': return {'decision': 'accept', 'answer': '实际结果符合要求'}
                done = [s for s in payload['steps'] if s['status'] == 'succeeded']
                if not done:
                    inputs = next(o['inputs'] for o in payload['observations'] if 'inputs' in o)
                    return {'type': 'tool', 'name': 'skill_script', 'arguments': {'path': 'scripts/sum.py', 'input': inputs}}
                return {'type': 'finish', 'answer': '交付实际合计', 'resultStepId': done[-1]['id'],
                        'artifactIds': [a['id'] for a in done[-1]['result']['artifacts'] if a['name'] == 'total.csv']}
        self.w.sub_agents.model = ScriptModel()
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'skill', snapshot)
        tests = plan('skill')
        for index, case in enumerate(tests['cases']):
            case['input'] = {'values': [2, 3, 5] if index == 0 else [7, 11]}
            case['checks'][0]['payload'] = {'value': {'total': 10 if index == 0 else 18}}
            case['checks'].append({'id': 'file', 'mode': 'delivery', 'requirement': '合计CSV文件', 'payload': {'schema': {'type': 'object'}, 'files': [{'name': 'total.csv'}]}})
        checked = adapter.validate(self.owner, prepared, tests)
        self.assertEqual(checked['status'], 'passed', checked)
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        for row in checked['cases']:
            actual = self.w.sub_agents.store.run(self.owner, row['executionRef']['skillCallId'])
            self.assertEqual(actual['status'], 'succeeded')
            self.assertTrue(actual['steps'][0]['evidenceId'])
            self.assertEqual(self.client.get(row['artifacts'][0]['downloadUrl']).status_code, 200)
        provider = self.w.runtime.provider
        original_model = provider.model
        provider.model = 'changed-fixture-model'
        try:
            with self.assertRaises(CapabilityError):
                adapter.commit(self.owner, prepared, checked, key='stale-model', review={'accepted': True, 'reviewDigest': 'r'})
        finally:
            provider.model = original_model
        saved = adapter.commit(self.owner, prepared, checked, key='actual-skill', review={'accepted': True, 'reviewDigest': 'r'})
        provider.model = 'changed-after-enable'
        try:
            with self.assertRaises(CapabilityError):
                self.w.capabilities.admit(self.owner, saved['assetId'], saved['versionId'])
        finally:
            provider.model = original_model

    def test_plan_generated_without_candidate_implementation_or_outputs(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'app', sample())
        proposed = plan()
        proposed['cases'][0]['basis']['claim'] = 'observed'
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(proposed)) as model:
            generated = adapter.plan(self.owner, prepared, instruction='按收盘价计算涨幅和最大回撤')
        payload = model.call_args.args[1]
        self.assertNotIn('def run', json.dumps(payload))
        self.assertNotIn('code', payload)
        self.assertEqual(generated['cases'][0]['checks'][0]['payload']['value']['analysis']['change'], 8)
        self.assertEqual(generated['cases'][0]['basis']['claim'], 'generated')
        invalid = plan(); invalid['cases'][1]['input'] = invalid['cases'][0]['input']
        with self.assertRaises(CapabilityError): adapter.validate(self.owner, prepared, invalid)

    def test_cancel_or_tampering_prevents_execution(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'app', sample())
        stop = threading.Event(); stop.set()
        with patch.object(self.w.platform.executor, 'execute') as execute:
            with self.assertRaises(CapabilityError): adapter.validate(self.owner, prepared, plan(), stopped=stop)
            execute.assert_not_called()
        prepared['material']['definition']['nodes'][0]['code'] += '\n# changed'
        with self.assertRaises(CapabilityError): adapter.validate(self.owner, prepared, plan())

    def test_late_cancel_and_unknown_result_keep_facts_without_continuing_cases(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'app', sample())
        stop = threading.Event()
        def late(*args, **kwargs):
            stop.set()
            return {'result': {'count': 4}, 'files': {}}
        with patch.object(self.w.platform.executor, 'execute', side_effect=late):
            with self.assertRaises(CapabilityError) as caught:
                adapter.validate(self.owner, prepared, plan(), stopped=stop)
        partial = caught.exception.authoringEvidence
        self.assertEqual(partial['status'], 'cancelled')
        self.assertEqual(len(partial['cases']), 1)
        self.assertEqual(partial['cases'][0]['status'], 'cancelled')
        self.assertIn('candidateRunId', partial['cases'][0]['executionRef'])
        tool = adapter.prepare(self.owner, 'tool', self.draft('tool'))
        with patch('backend.app.modules.capability_library.components.invoke_snapshot', return_value={'status': 'unknown', 'invocationId': 'unknown-real-call'}) as invoke:
            checked = adapter.validate(self.owner, tool, plan('tool'))
        self.assertEqual(invoke.call_count, 1)
        self.assertEqual(checked['status'], 'unknown')
        self.assertEqual(checked['cases'][0]['executionRef']['invocationId'], 'unknown-real-call')
        self.assertEqual(checked['cases'][1]['status'], 'not_run')

    def test_metadata_matching_reuses_only_valid_unchanged_execution(self):
        adapter = self.adapter()
        original = adapter.prepare(self.owner, 'app', sample())
        checked = adapter.validate(self.owner, original, plan())
        saved = self.service.save(self.owner, {'definition': original['content']}, 'base')
        with self.service.store.transaction() as db:
            version = self.service.store.get(db, 'versions', self.owner, saved['versionId'])
            version['verification'] = {'id': checked['id'], 'status': 'passed',
                                       'contextDigest': original['runtimeContextDigest'], 'cases': checked['cases']}
            self.service.store.put(db, 'versions', self.owner, version)
        old = self.service.detail(self.owner, saved['id'])
        target = {'assetId': saved['id'], 'baseVersionId': saved['versionId'], 'baseRevision': old['revision']}
        changed = copy.deepcopy(original['content']); changed['description'] = '合成收盘价格序列分析'
        prepared = adapter.prepare(self.owner, 'app', changed, previous=original)
        self.assertEqual(prepared['changeClass'], 'matching')
        matching = plan()
        for index, case in enumerate(matching['cases']):
            case['checks'] = [{'id': 'match', 'mode': 'matching', 'requirement': '仅适用于价格序列分析', 'payload': {'expected': index == 0}}]
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(matching)):
            planned = adapter.plan(self.owner, prepared, context={'target': target})
        self.assertTrue(planned['reuse'])
        with patch.object(self.w.platform.executor, 'execute') as execute, patch.object(self.w.platform.planner, '_call', side_effect=[json.dumps({'selected': True}), json.dumps({'selected': False})]):
            verified = adapter.validate(self.owner, prepared, planned, context={'target': target})
        self.assertEqual(verified['status'], 'passed')
        execute.assert_not_called()
        changed['nodes'][0]['code'] += '\n# behavior changed'
        self.assertEqual(adapter.prepare(self.owner, 'app', changed, previous=original)['changeClass'], 'behavior')

    def test_missing_adopted_source_recommendation_is_inferred_not_old_success(self):
        rid, _ = self.source()
        with self.w.sub_agents.store.transaction() as db:
            run = self.w.sub_agents.store.get(db, self.owner, 'run', rid, True)
            run['deliveries'] = []
            self.w.sub_agents.store.put(db, self.owner, 'run', rid, run)
            evidence = self.w.sub_agents.store.get(db, self.owner, 'execution_evidence', 'adopted', True)
            evidence['adopted'] = False
            self.w.sub_agents.store.put(db, self.owner, 'execution_evidence', 'adopted', evidence)
        from backend.app.modules.agent_platform.asset_creator import _capture
        recovered = _capture(self.service, self.owner, rid, ['adopted'])
        with patch.object(self.w.platform.planner, '_call', side_effect=[json.dumps({'stepIds': ['adopted'], 'reason': '用于文件汇总'}), json.dumps(recovered)]):
            content = self.adapter().create(self.owner, 'app', '保存文件汇总方法', context={'sourceRunId': rid})
        self.assertEqual(content['source']['selectionBasis'], 'inferred_selection')
        self.assertEqual(content['source']['claim'], 'inferred')
        self.assertEqual(self.w.sub_agents.store.run(self.owner, rid)['deliveries'], [])
        self.assertFalse(self.w.sub_agents.execution_evidence(self.owner, rid, 'adopted')['adopted'])

    def test_export_does_not_need_runtime_or_verification(self):
        saved = self.service.save(self.owner, {'definition': sample()}, 'unverified')
        with patch.object(self.service, '_context', side_effect=AssertionError('runtime not available')):
            exported = self.service.export(self.owner, saved['id'])
            imported = self.service.import_app(self.owner, {'package': exported}, 'import-unverified')
        self.assertIsNone(imported['enabledVersionId'])
        self.assertEqual(imported['verification']['status'], 'unverified')

    def test_file_case_uses_owned_original_and_generated_different_input_without_upload(self):
        adapter = self.adapter()
        doc = sample()
        doc.update(inputSchema={'type': 'object'}, rules=['读取JSON数组并求和'], parameterization=[{'name': 'source', 'kind': 'file'}],
                   outputs={'analysis': {'from': 'step', 'stepId': 'sum', 'path': ''}})
        doc['nodes'] = [{'id': 'sum', 'kind': 'code', 'title': '求和', 'checkpoint': False,
            'inputBindings': {'source': {'from': 'file', 'input': 'source'}}, 'delivery': {'schema': {'type': 'object'}},
            'code': 'import json\ndef run(payload):\n    with open(payload["source"]) as f: values=json.load(f)\n    return {"total":sum(values)}\n'}]
        original = self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'[1,2,3]'), 'original.json')}, content_type='multipart/form-data').json
        prepared = adapter.prepare(self.owner, 'app', doc)
        planned = plan()
        for case, expected in zip(planned['cases'], (6, 30)):
            case['input'] = {}
            case['checks'][0]['payload'] = {'value': {'analysis': {'total': expected}}}
        planned['cases'][0]['fileBindings'] = {'source': original['id']}
        planned['cases'][1]['fileFixtures'] = [{'name': 'different.json', 'text': '[10,20]', 'bindAs': 'source'}]
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(planned)) as model:
            default_plan = adapter.plan(self.owner, prepared, instruction='JSON数组求和', context={'fileIds': [original['id']]})
        self.assertEqual(len(default_plan['cases']), 1)
        self.assertEqual(model.call_args.args[1]['authorizedFiles'][0]['preview'], '[1,2,3]')
        # Explicitly supplied optional cases still support isolated data fixtures.
        checked = adapter.validate(self.owner, prepared, planned, context={'fileIds': [original['id']]})
        self.assertEqual(checked['status'], 'passed', checked)
        fixture = checked['cases'][1]['fixtureRefs'][0]
        self.assertEqual(fixture['claim'], 'generated')
        self.assertEqual(self.w.capabilities.store.blob(self.owner, fixture['sha256']), b'[10,20]')
        self.assertEqual(len(self.w.store.get(self.owner)['files']), 1)
        self.assertEqual(self.service.listing(self.owner), [])
        renamed = copy.deepcopy(planned)
        renamed['cases'][1]['fileFixtures'][0]['text'] = '[1,2,3]'
        with self.assertRaises(CapabilityError):
            adapter.validate(self.owner, prepared, renamed, context={'fileIds': [original['id']]})
        unsafe = copy.deepcopy(planned)
        unsafe['cases'][1]['fileFixtures'][0]['name'] = '../escape.json'
        with self.assertRaises(CapabilityError): adapter.validate(self.owner, prepared, unsafe, context={'fileIds': [original['id']]})

    def test_nested_external_write_is_blocked_before_any_candidate_execution(self):
        from backend.app.modules.capability_library.components import snapshot_from_package
        from backend.app.modules.agent_platform.asset_contracts import resource_files
        adapter = self.adapter()
        writer = self.draft('tool')
        writer['manifest']['permissions']['externalEffects'] = 'write'
        dependency = snapshot_from_package(writer['manifest'], resource_files(writer['files']))
        manifest = {'format': 'cd80.capability/1', 'kind': 'skill', 'name': 'unsafe-chain', 'description': '测试嵌套外部写阻断',
            'source': {'identity': 'test/unsafe-chain', 'format': 'platform'},
            'dependencies': [{'sourceIdentity': dependency['manifest']['source']['identity'], 'contentDigest': dependency['contentDigest'], 'required': True, 'reason': '测试依赖'}],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
            'skill': {'entry': 'SKILL.md'}}
        snapshot = snapshot_from_package(manifest, {'SKILL.md': b'---\nname: unsafe-chain\ndescription: Dependency boundary test.\n---\nUse the declared dependency.'}, [dependency])
        with patch.object(self.w.platform.executor, 'execute') as execute, patch.object(self.w.sub_agents, 'run_skill') as skill:
            blocked = adapter.prepare(self.owner, 'skill', snapshot)
            self.assertTrue(blocked['issues'])
            self.assertEqual(adapter.validate(self.owner, blocked, plan('skill'))['status'], 'failed')
            app = sample()
            app['nodes'] = [{'id': 'unsafe', 'kind': 'skill', 'title': '边界测试', 'task': '验证依赖', 'component': snapshot,
                             'inputBindings': {}, 'delivery': {'schema': {}}, 'checkpoint': False}]
            app['outputs'] = {'result': {'from': 'step', 'stepId': 'unsafe', 'path': ''}}
            self.assertTrue(adapter.prepare(self.owner, 'app', app)['issues'])
            self.assertTrue(adapter.prepare(self.owner, 'tool', writer)['issues'])
            execute.assert_not_called(); skill.assert_not_called()

    def test_tool_generated_files_use_read_only_input_mapping(self):
        from backend.app.modules.capability_library.components import snapshot_from_package
        manifest = {'format': 'cd80.capability/1', 'kind': 'tool', 'name': 'sum-file', 'description': '读取本次合成数据求和',
            'source': {'identity': 'test/fixture-tool', 'format': 'platform'}, 'dependencies': [],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': True, 'outputFiles': False},
            'tool': {'inputSchema': {'type': 'object', 'required': ['source'], 'properties': {'source': {'type': 'string'}}},
                     'outputSchema': {'type': 'object'}, 'execution': {'type': 'python_wasi', 'path': 'original/tool.py', 'callable': 'run'}}}
        snapshot = snapshot_from_package(manifest, {'original/tool.py': b'import json\ndef run(payload):\n    with open(payload["source"]) as f: values=json.load(f)\n    return {"total":sum(values)}\n'})
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'tool', snapshot)
        planned = plan('tool')
        for case, data, total in zip(planned['cases'], ('[1,2,3]', '[10,20]'), (6, 30)):
            case['input'] = {'source': '/input/data.json'}
            case['fileFixtures'] = [{'name': 'data.json', 'text': data}]
            case['checks'][0]['payload'] = {'value': {'total': total}}
        checked = adapter.validate(self.owner, prepared, planned)
        self.assertEqual(checked['status'], 'passed', checked)
        self.assertEqual(self.w.store.get(self.owner)['files'], [])
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        self.assertNotEqual(checked['cases'][0]['fixtureRefs'][0]['sha256'], checked['cases'][1]['fixtureRefs'][0]['sha256'])

    def test_skill_request_exhaustion_propagates_without_repair_or_next_case(self):
        class ExhaustedModel:
            calls = 0
            def call(self, *args):
                self.calls += 1
                fail('BUDGET_EXHAUSTED', '本轮模型请求已用完。', 409)
        model = ExhaustedModel()
        self.w.sub_agents.model = model
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'skill', self.draft('skill'))
        with self.assertRaises(CapabilityError) as caught:
            adapter.validate(self.owner, prepared, plan('skill', manual=True))
        self.assertEqual(caught.exception.code, 'BUDGET_EXHAUSTED')
        self.assertEqual(model.calls, 1)
        evidence = caught.exception.authoringEvidence
        self.assertEqual(evidence['status'], 'interrupted')
        self.assertEqual(len(evidence['cases']), 1)
        self.assertIn('skillCallId', evidence['cases'][0]['executionRef'])

    def test_old_skill_validation_worker_inherits_authoring_context(self):
        from backend.app.modules.agent_platform.authoring_budget import _CURRENT, current_budget
        from backend.app.modules.capability_library.skill_validation import model_turn, ValidationBudget
        sentinel, seen = object(), []
        class Provider:
            def events(self, *args, **kwargs):
                seen.append(current_budget())
                yield {'type': 'turn_end'}
        previous = self.w.runtime.provider
        token = _CURRENT.set(sentinel)
        self.w.runtime.provider = Provider()
        try:
            list(model_turn(self.w.capabilities, [], 'fixture', threading.Event(), lambda r: None, [], ValidationBudget(), 1))
        finally:
            self.w.runtime.provider = previous
            _CURRENT.reset(token)
        self.assertEqual(seen, [sentinel])

    def test_unified_creator_preserves_long_request_and_legacy_limit(self):
        from backend.app.modules.agent_platform.asset_creator import create
        instruction = '保持用户约定的业务规则。' * 1100
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(sample())) as model:
            self.adapter().create(self.owner, 'app', instruction)
        self.assertEqual(model.call_args.args[1]['instruction'], instruction)
        with self.assertRaises(CapabilityError):
            create(self.service, self.owner, {'kind': 'app', 'instruction': instruction})

    def test_contradictory_plan_is_rejected_before_execution_and_retained_for_repair(self):
        adapter = self.adapter()
        prepared = adapter.prepare(self.owner, 'app', sample())
        invalid = plan()
        invalid['cases'][0]['checks'].append({'id': 'invented', 'mode': 'fields', 'requirement': '多余自测字段',
                                            'payload': {'values': {'analysis.verified': True}}})
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(invalid)), patch.object(self.w.platform.executor, 'execute') as execute:
            with self.assertRaises(CapabilityError) as caught:
                adapter.plan(self.owner, prepared, instruction='只计算涨幅和回撤')
            execute.assert_not_called()
        self.assertEqual(caught.exception.code, 'TEST_PLAN_INVALID')
        self.assertEqual(caught.exception.authoringPlan['cases'][0]['checks'][-1]['id'], 'invented')
        feedback = {'plan': caught.exception.authoringPlan, 'error': str(caught.exception)}
        with patch.object(self.w.platform.planner, '_call', return_value=json.dumps(plan())) as model:
            adapter.plan(self.owner, prepared, instruction='只计算涨幅和回撤', context={'planRepairFeedback': feedback})
        self.assertIn('planRepairFeedback', model.call_args.args[1])
        self.assertNotIn('actualResults', model.call_args.args[1]['planRepairFeedback'])


if __name__ == '__main__':
    unittest.main()
