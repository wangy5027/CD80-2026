"""HTTP authoring and conversation hand-off against real candidate adapters/WASI."""
import copy
import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_asset_apps as asset_fixtures
from tests.test_asset_apps import sample
from tests.test_authoring_adapters import plan
from backend.app.modules.agent_platform.asset_creator import _extract_definition
from backend.app.modules.agent_platform.authoring_budget import current_budget
from backend.app.modules.agent_platform.creation_components import catalog as creation_catalog
from backend.app.modules.capability_library.contracts import canonical
from backend.app.modules.capability_library.packages import zip_bytes


class AuthoringIntegrationTests(unittest.TestCase):
    setUp = asset_fixtures.AssetAppTests.setUp
    tearDown = asset_fixtures.AssetAppTests.tearDown

    def post(self, suffix, body, key=None):
        self.n += 1
        return self.client.post('/desktop/api/authoring-sessions' + suffix, json=body,
                                headers={'Idempotency-Key': key or 'integration-' + str(self.n)})

    def wait(self, session_id):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            response = self.client.get('/desktop/api/authoring-sessions/' + session_id)
            self.assertEqual(response.status_code, 200, response.json)
            if response.json['session']['phase'] not in {'preparing', 'checking', 'validating', 'repairing'}:
                return response.json
            time.sleep(.025)
        self.fail('Authoring did not finish')

    def confirm(self, value):
        session, candidate, review = (value[k] for k in ('session', 'candidate', 'reviewSnapshot'))
        payload = {'revision': session['revision'], 'mode': 'save_and_enable', 'candidateId': candidate['id'],
                   'contentDigest': candidate['contentDigest'], 'reviewSnapshotId': review['id'], 'reviewDigest': review['reviewDigest']}
        if session.get('target'):
            payload['baseRevision'] = session['target']['baseRevision']
        response = self.post('/' + session['id'] + '/confirm', payload, 'confirm-' + session['id'])
        self.assertEqual(response.status_code, 200, response.json)
        return response.json

    def example_component(self, folder, owner=None):
        root = Path(__file__).resolve().parents[1] / 'docs' / 'capability-library' / 'examples' / folder
        files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
        return self.w.capabilities.import_package(owner or self.owner, zip_bytes(files), folder + '.zip')

    def test_real_app_candidates_save_first_and_edit_as_unverified_version(self):
        adapter = self.w.authoring.adapters
        def generated(*_args, **_kwargs):
            budget = current_budget()
            request_id = budget.begin_request('fixture-provider', 'GLM-5.3-flash', request_payload={
                'messages': [{'role': 'system', 'content': '固定系统指令'},
                             {'role': 'user', 'content': 'api_key=fixture-secret; 创建合成应用'}]})
            budget.finish_request(request_id, succeeded=True)
            return sample()
        with patch.object(adapter, 'create', side_effect=generated), patch.object(adapter, 'plan', side_effect=AssertionError('APP save-first must not plan tests')):
            response = self.post('', {'kind': 'app', 'intent': 'create', 'instruction': '创建合成价格指标应用'})
            self.assertEqual(response.status_code, 201, response.json)
            ready = self.wait(response.json['session']['id'])
        self.assertEqual(ready['session']['phase'], 'completed', ready)
        self.assertEqual(ready['evidence'], [])
        result = ready['session']['result']
        asset = self.service.detail(self.owner, result['assetId'])
        self.assertIsNone(asset['enabledVersionId'])
        self.assertEqual(asset['verification']['status'], 'unverified')
        self.assertEqual(len(self.service.listing(self.owner)), 1)
        provenance = asset['authoringProvenance']
        self.assertEqual(provenance['sessionId'], ready['session']['id'])
        self.assertEqual(provenance['candidateId'], ready['candidate']['id'])
        self.assertEqual(len(provenance['modelTraceIds']), 1)
        route = '/desktop/api/authoring-sessions/' + ready['session']['id'] + '/model-traces/' + provenance['modelTraceIds'][0]
        trace = self.client.get(route)
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(trace.json['request']['value']['messages'][0]['content'], '固定系统指令')
        self.assertNotIn('fixture-secret', json.dumps(trace.json))
        self.assertEqual(self.other.get(route).status_code, 404)
        self.assertEqual(self.post('/' + ready['session']['id'] + '/confirm',
                                   {'mode': 'save_and_enable', 'revision': ready['session']['revision']}).status_code, 409)
        modified = copy.deepcopy(sample())
        modified['nodes'][-1]['code'] = 'def run(payload):\n    return {**payload["metrics"],"count":payload["count"],"checked":True}\n'
        with patch.object(adapter, 'create', return_value=modified), patch.object(adapter, 'plan', side_effect=AssertionError('APP save-first must not plan tests')):
            response = self.post('', {'kind': 'app', 'intent': 'edit', 'instruction': '增加checked字段',
                'target': {'assetId': asset['id'], 'baseVersionId': asset['versionId'], 'baseRevision': asset['revision']}})
            self.assertEqual(response.status_code, 201, response.json)
            changed = self.wait(response.json['session']['id'])
        self.assertEqual(changed['session']['phase'], 'completed', changed)
        self.assertNotEqual(changed['session']['result']['versionId'], result['versionId'])
        latest = self.service.detail(self.owner, asset['id'])
        self.assertIsNone(latest['enabledVersionId'])
        self.assertEqual(latest['verification']['status'], 'unverified')
        old = self.service.detail(self.owner, asset['id'], result['versionId'])
        self.assertNotIn('checked', old['definition']['nodes'][-1]['code'])

    def test_edit_saves_unverified_version_without_disabling_verified_old_version(self):
        saved_response = self.client.post('/desktop/api/asset-apps', json={'definition': sample()},
                                          headers={'Idempotency-Key': 'enabled-old-version'})
        self.assertEqual(saved_response.status_code, 201, saved_response.json)
        original = saved_response.json
        cases = [
            {'label': '原预期', 'input': {'prices': [100, 120, 90, 108]},
             'expectation': {'mode': 'fields', 'values': {'analysis.change': 8, 'analysis.drawdown': 25, 'analysis.count': 4}}},
            {'label': '不同输入', 'input': {'prices': [80, 100, 95, 120]},
             'expectation': {'mode': 'fields', 'values': {'analysis.change': 50, 'analysis.drawdown': 5}}},
        ]
        verify_response = self.client.post('/desktop/api/asset-apps/' + original['id'] + '/verify',
            json={'versionId': original['versionId'], 'cases': cases},
            headers={'Idempotency-Key': 'verify-old-version'})
        self.assertEqual(verify_response.status_code, 202, verify_response.json)
        for run_id in verify_response.json['verification']['runIds']:
            asset_fixtures.AssetAppTests.wait_run(self, run_id)
        verified = self.service.detail(self.owner, original['id'])
        self.assertEqual(verified['verification']['status'], 'passed')
        enable_response = self.client.post('/desktop/api/asset-apps/' + original['id'] + '/enable',
            json={'versionId': verified['versionId'], 'enabled': True, 'revision': verified['revision']},
            headers={'Idempotency-Key': 'enable-old-version'})
        self.assertEqual(enable_response.status_code, 200, enable_response.json)
        enabled = enable_response.json
        old_version_id = enabled['versionId']
        modified = copy.deepcopy(sample())
        modified['nodes'][-1]['code'] = 'def run(payload):\n    return {**payload["metrics"], "count": payload["count"], "checked": True}\n'
        adapter = self.w.authoring.adapters
        with patch.object(adapter, 'create', return_value=modified), patch.object(
            adapter, 'plan', side_effect=AssertionError('APP save-first must not plan tests')
        ):
            response = self.post('', {'kind': 'app', 'intent': 'edit', 'instruction': '增加 checked 字段',
                'target': {'assetId': enabled['id'], 'baseVersionId': old_version_id,
                           'baseRevision': enabled['revision']}})
            self.assertEqual(response.status_code, 201, response.json)
            changed = self.wait(response.json['session']['id'])
        self.assertEqual(changed['session']['phase'], 'completed', changed)
        latest = self.service.detail(self.owner, enabled['id'])
        self.assertNotEqual(latest['versionId'], old_version_id)
        self.assertEqual(latest['enabledVersionId'], old_version_id)
        self.assertEqual(latest['verification']['status'], 'unverified')
        self.assertEqual(self.service.detail(self.owner, enabled['id'], old_version_id)['verification']['status'], 'passed')

    def test_natural_language_uses_owned_disabled_components_and_freezes_exact_versions(self):
        tool = self.example_component('decimal-total')
        skill = self.example_component('summary-skill')
        available = creation_catalog(self.w.capabilities, self.owner)
        self.assertEqual({item['kind'] for item in available}, {'tool', 'skill'})
        self.assertTrue(all(item['runtimeStatus'] == {'readiness': 'unverified', 'enabled': False}
                            for item in available))
        self.assertTrue(all('component' not in item and 'files' not in item for item in available))
        other_owner = self.other.get('/desktop/api/session').json['user']['id']
        self.assertEqual(creation_catalog(self.w.capabilities, other_owner), [])
        definition = {'format': 'cd80.app/2', 'name': '求和后摘要', 'description': '合成输入处理',
            'inputSchema': {'type': 'object', 'properties': {'values': {'type': 'array', 'items': {'type': 'string'}}},
                            'required': ['values']}, 'rules': [], 'gaps': [], 'parameterization': [],
            'nodes': [
                {'id': 'sum', 'kind': 'tool', 'title': '精确求和',
                 'componentRef': {'assetId': tool['assetId'], 'versionId': tool['versionId']},
                 'inputBindings': {'values': {'from': 'input', 'path': 'values'}},
                 'delivery': {'schema': {'type': 'object'}}, 'checkpoint': False},
                {'id': 'summary', 'kind': 'skill', 'title': '摘要', 'task': '解释本次求和结果',
                 'componentRef': {'assetId': skill['assetId'], 'versionId': skill['versionId']},
                 'inputBindings': {'total': {'from': 'step', 'stepId': 'sum', 'path': 'total'}},
                 'delivery': {'schema': {'type': 'object'}}, 'checkpoint': False}],
            'outputs': {'summary': {'from': 'step', 'stepId': 'summary', 'path': ''}}}
        calls = []
        def model(system, payload, *_args, **_kwargs):
            calls.append({'system': system, 'payload': payload})
            return json.dumps(definition)
        adapter = self.w.authoring.adapters
        with patch.object(self.w.platform.planner, '_call', side_effect=model), patch.object(
            adapter, 'plan', side_effect=AssertionError('natural-language creation must not plan tests')
        ), patch.object(adapter, 'validate', side_effect=AssertionError('natural-language creation must not execute')):
            body = {'kind': 'app', 'intent': 'create', 'mode': 'prepare',
                    'instruction': '用我的求和工具处理输入，再用摘要方法解释结果'}
            created = self.post('', body, 'nl-component-creation')
            self.assertEqual(created.status_code, 201, created.json)
            ready = self.wait(created.json['session']['id'])
            replay = self.post('', body, 'nl-component-creation')
        self.assertEqual(ready['session']['phase'], 'completed', ready)
        self.assertEqual(replay.json['session']['id'], ready['session']['id'])
        self.assertEqual(len(calls), 1)
        self.assertIn('cd80.app/2', calls[0]['system'])
        self.assertEqual({item['componentRef']['versionId'] for item in calls[0]['payload']['availableComponents']},
                         {tool['versionId'], skill['versionId']})
        app = self.service.detail(self.owner, ready['session']['result']['assetId'])
        self.assertEqual(app['versionId'], ready['session']['result']['versionId'])
        self.assertEqual(app['verification']['status'], 'unverified')
        self.assertIsNone(app['enabledVersionId'])
        self.assertEqual([node['component']['provenance']['versionId'] for node in app['definition']['nodes']],
                         [tool['versionId'], skill['versionId']])
        self.assertTrue(all(node['component']['files'] for node in app['definition']['nodes']))
        tool_snapshot_digest = app['definition']['nodes'][0]['component']['snapshotDigest']
        root = Path(__file__).resolve().parents[1] / 'docs' / 'capability-library' / 'examples' / 'decimal-total'
        files = {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob('*') if path.is_file()}
        manifest = json.loads(files['capability.json'])
        manifest['description'] = '更新后工具说明'
        files['capability.json'] = canonical(manifest).encode()
        newer = self.w.capabilities.import_package(self.owner, zip_bytes(files), 'decimal-total-new.zip')
        self.assertNotEqual(newer['versionId'], tool['versionId'])
        frozen = self.service.detail(self.owner, app['id'])['definition']['nodes'][0]['component']
        self.assertEqual(frozen['snapshotDigest'], tool_snapshot_digest)
        self.assertEqual(frozen['provenance']['versionId'], tool['versionId'])
        self.assertEqual(self.other.get('/desktop/api/asset-apps/' + app['id']).status_code, 404)
        enable = self.client.post('/desktop/api/asset-apps/' + app['id'] + '/enable',
            json={'versionId': app['versionId'], 'enabled': True, 'revision': app['revision']},
            headers={'Idempotency-Key': 'unverified-component-enable'})
        self.assertEqual(enable.status_code, 409)

    def test_natural_language_question_and_empty_definition_resume_without_placeholder(self):
        seen = []
        answers = iter([{'question': '整数列表作为 values 传入吗？'},
                        {'format': 'cd80.app/2', 'name': '空候选'}, sample()])
        def model(_system, payload, *_args, **_kwargs):
            seen.append(payload['instruction'])
            return json.dumps(next(answers))
        with patch.object(self.w.platform.planner, '_call', side_effect=model):
            created = self.post('', {'kind': 'app', 'instruction': '请创建合成整数列表统计应用'}, 'nl-question')
            self.assertEqual(created.status_code, 201, created.json)
            waiting = self.wait(created.json['session']['id'])
            self.assertEqual(waiting['session']['phase'], 'awaiting_input', waiting)
            self.assertEqual(self.service.listing(self.owner), [])
            question = waiting['session']['question']
            self.assertIn('values', question['prompt'])
            response = self.post('/' + waiting['session']['id'] + '/answers',
                {'revision': waiting['session']['revision'], 'questionId': question['id'],
                 'answer': '是，values 是整数列表。'}, 'nl-answer')
            self.assertEqual(response.status_code, 202, response.json)
            empty = self.wait(waiting['session']['id'])
            self.assertEqual(empty['session']['phase'], 'awaiting_input', empty)
            self.assertEqual(self.service.listing(self.owner), [])
            self.assertIn('整数列表作为 values 传入吗', seen[1])
            self.assertIn('values 是整数列表', seen[1])
            second = empty['session']['question']
            response = self.post('/' + waiting['session']['id'] + '/answers',
                {'revision': empty['session']['revision'], 'questionId': second['id'],
                 'answer': '输入 values，输出合计、数量和变化。'}, 'nl-answer-after-empty')
            self.assertEqual(response.status_code, 202, response.json)
            completed = self.wait(waiting['session']['id'])
        self.assertEqual(completed['session']['phase'], 'completed', completed)
        self.assertEqual(len(self.service.listing(self.owner)), 1)
        self.assertEqual(self.service.detail(self.owner, completed['session']['result']['assetId'])['verification']['status'], 'unverified')

    def test_natural_language_cannot_reference_another_account_component(self):
        other_owner = self.other.get('/desktop/api/session').json['user']['id']
        foreign = self.example_component('decimal-total', other_owner)
        definition = {'format': 'cd80.app/2', 'name': '错误引用', 'description': '',
            'inputSchema': {'type': 'object'}, 'rules': [], 'gaps': [], 'parameterization': [],
            'nodes': [{'id': 'sum', 'kind': 'tool', 'title': '求和',
                       'componentRef': {'assetId': foreign['assetId'], 'versionId': foreign['versionId']},
                       'inputBindings': {}, 'delivery': {'schema': {'type': 'object'}}, 'checkpoint': False}],
            'outputs': {'total': {'from': 'step', 'stepId': 'sum', 'path': 'total'}}}
        observed = []
        def model(_system, payload, *_args, **_kwargs):
            observed.append(payload['availableComponents'])
            return json.dumps(definition)
        with patch.object(self.w.platform.planner, '_call', side_effect=model):
            created = self.post('', {'kind': 'app', 'instruction': '使用别人未授权的工具'}, 'nl-foreign-ref')
            waiting = self.wait(created.json['session']['id'])
        self.assertEqual(observed, [[]])
        self.assertEqual(waiting['session']['phase'], 'awaiting_input', waiting)
        self.assertEqual(self.service.listing(self.owner), [])

    def test_app_generate_then_save_only_language_still_creates_unverified_app(self):
        instruction = '生成整数列表统计 APP。生成后只保存为未验证 APP，不运行、不验证、不启用。'
        adapter = self.w.authoring.adapters
        with patch.object(adapter, 'create', return_value=sample()) as generated, patch.object(
            adapter, 'plan', side_effect=AssertionError('save-first APP must not plan tests')
        ), patch.object(adapter, 'validate', side_effect=AssertionError('save-first APP must not run tests')):
            created = self.post('', {'kind': 'app', 'mode': 'prepare', 'instruction': instruction}, 'app-save-only')
            self.assertEqual(created.status_code, 201, created.json)
            completed = self.wait(created.json['session']['id'])
        self.assertEqual(generated.call_count, 1)
        self.assertEqual(completed['session']['mode'], 'prepare')
        self.assertEqual(completed['session']['phase'], 'completed')
        self.assertIsNotNone(completed['session']['result']['assetId'])
        self.assertEqual(completed['evidence'], [])
        asset = self.service.detail(self.owner, completed['session']['result']['assetId'])
        self.assertEqual(asset['verification']['status'], 'unverified')
        self.assertIsNone(asset['enabledVersionId'])

    def test_app_idea_only_and_explicit_draft_remain_unsaved_until_generation_requested(self):
        adapter = self.w.authoring.adapters
        with patch.object(adapter, 'create', side_effect=AssertionError('idea-only must not generate')):
            idea = self.post('', {'kind': 'app', 'mode': 'prepare',
                                  'instruction': '只记录这个想法，暂不生成应用。'}, 'app-record-idea')
            explicit = self.post('', {'kind': 'app', 'mode': 'draft_only',
                                      'instruction': '生成后只保存为未验证 APP，不运行。'}, 'app-explicit-draft')
        for response in (idea, explicit):
            self.assertEqual(response.status_code, 201, response.json)
            self.assertEqual(response.json['session']['mode'], 'draft_only')
            self.assertEqual(response.json['session']['phase'], 'completed')
            self.assertIsNone(response.json['session'].get('result'))
        self.assertEqual(self.service.listing(self.owner), [])
        session = idea.json['session']
        with patch.object(adapter, 'create', return_value=sample()) as generated, patch.object(
            adapter, 'plan', side_effect=AssertionError('save-first APP must not plan tests')
        ):
            response = self.post('/' + session['id'] + '/messages',
                {'revision': session['revision'], 'instruction': '现在生成这个 APP，生成后只保存为未验证版本，不要测试。'},
                'app-create-after-idea')
            self.assertEqual(response.status_code, 202, response.json)
            completed = self.wait(session['id'])
        self.assertEqual(generated.call_count, 1)
        self.assertEqual(completed['session']['mode'], 'prepare')
        self.assertEqual(completed['session']['phase'], 'completed')
        self.assertEqual(len(self.service.listing(self.owner)), 1)

    def test_app_save_only_intent_survives_resume_after_generation_failure(self):
        adapter = self.w.authoring.adapters
        with patch.object(adapter, 'create', side_effect=[RuntimeError('fixture failure'), sample()]) as generated:
            created = self.post('', {'kind': 'app', 'mode': 'prepare',
                'instruction': '创建合成应用，完成后只保存，不要测试。'}, 'app-save-only-retry')
            self.assertEqual(created.status_code, 201, created.json)
            failed = self.wait(created.json['session']['id'])
            self.assertEqual(failed['session']['phase'], 'failed', failed)
            resumed = self.post('/' + failed['session']['id'] + '/continue',
                {'revision': failed['session']['revision'], 'mode': 'resume',
                 'budgetRoundId': failed['session']['budgetRoundId']}, 'app-save-only-resume')
            self.assertEqual(resumed.status_code, 202, resumed.json)
            completed = self.wait(failed['session']['id'])
        self.assertEqual(generated.call_count, 2)
        self.assertEqual(completed['session']['phase'], 'completed', completed)
        self.assertEqual(completed['session']['mode'], 'prepare')
        self.assertEqual(len(self.service.listing(self.owner)), 1)

    def test_real_tool_is_invisible_until_review_then_executable(self):
        doc = sample()
        draft = _extract_definition({'name': doc['name'], 'description': doc['description'], 'definition': doc}, doc['nodes'], 'tool')
        adapter = self.w.authoring.adapters
        with patch.object(adapter, 'create', return_value=draft), patch.object(adapter, 'plan', return_value=plan('tool')):
            created = self.post('', {'kind': 'tool', 'instruction': '创建价格指标工具'})
            ready = self.wait(created.json['session']['id'])
        self.assertEqual(ready['session']['phase'], 'awaiting_review', ready)
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])
        saved = self.confirm(ready)['session']['result']
        version = self.w.capabilities.admit(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(version['manifest']['kind'], 'tool')

    def test_chat_handoff_persists_same_session_and_draft_only_never_runs(self):
        planner = self.w.sub_agents.model
        response = {'mode': 'authoring', 'authoring': {'kind': 'skill', 'intent': 'create', 'mode': 'draft_only'},
                    'assessment': {'explicitApplication': False, 'needsInformation': False, 'dependentProcessing': False}}
        with patch.object(planner, 'call', return_value=response):
            run = self.w.sub_agents.submit(self.owner, 'new', {'message': '先保存一个草稿Skill，不要测试'}, 'chat-author')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                run = self.w.sub_agents.store.run(self.owner, run['id'])
                if run['status'] in {'succeeded', 'failed'}:
                    break
                time.sleep(.02)
        self.assertEqual(run['status'], 'succeeded', run)
        session_id = run['authoringSessionId']
        session = self.w.authoring.detail(self.owner, session_id)
        self.assertEqual(session['session']['phase'], 'completed')
        self.assertEqual(session['budget']['consumed']['modelRequests'], 0)
        task = self.w.get_task(self.owner, run['conversationId'])
        self.assertEqual(task['messages'][-1]['authoringSessionId'], session_id)
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'asset'), [])

    def test_all_source_kinds_owner_checks_and_import_keeps_candidate(self):
        import io
        original = self.client.post('/desktop/api/portable-assets/imports', data={
            'kind': 'app', 'file': (io.BytesIO(json.dumps(sample()).encode()), 'external.json')},
            headers={'Idempotency-Key': 'original'})
        self.assertEqual(original.status_code, 201, original.json)
        record = original.json.get('asset', original.json)
        adapter = self.w.authoring.adapters
        with patch.object(adapter, 'create', side_effect=lambda owner, kind, instruction, **kw: kw['content']), patch.object(adapter, 'plan', return_value=plan()):
            imported = self.post('', {'kind': 'app', 'intent': 'prepare_import', 'instruction': '准备使用导入应用',
                'sourceRefs': [{'kind': 'import_draft', 'reference': {'portableAssetId': record['id']}}]})
            self.assertEqual(imported.status_code, 201, imported.json)
            ready = self.wait(imported.json['session']['id'])
        self.assertEqual(ready['session']['phase'], 'completed', ready)
        self.assertEqual(self.other.get('/desktop/api/authoring-sessions/' + ready['session']['id']).status_code, 404)
        self.assertEqual(self.service.detail(self.owner, ready['session']['result']['assetId'])['verification']['status'], 'unverified')
