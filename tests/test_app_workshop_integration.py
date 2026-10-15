"""Workshop's original flow saves complete, unverified platform Apps."""
import copy
import json
import tempfile
import unittest
from unittest.mock import Mock, patch

from backend.app import create_app
from backend.app.modules.agent_platform.contracts import digest, validate_agent_spec
from backend.app.modules.agent_platform.planner import Planner
from backend.app.modules.app_workshop.routes import app_workshop
from backend.app.modules.app_workshop.service import AppWorkshopService
from backend.app.modules.workbench.errors import GatewayError


def generated_spec():
    schema = {'type': 'object', 'properties': {'amount': {'type': 'number'}},
              'required': ['amount'], 'additionalProperties': False}
    return {'schemaVersion': '1', 'id': 'generated', 'version': '1', 'name': '金额整理',
            'goal': '整理金额', 'description': '处理用户输入的金额', 'prompt': '解释实际计算结果。',
            'inputSchema': schema, 'outputSchema': {'type': 'object'},
            'tools': [{'id': 'double', 'description': '计算两倍金额',
                       'code': 'def run(payload):\n    return {"doubled": payload["amount"] * 2}\n',
                       'inputSchema': schema, 'outputSchema': {'type': 'object'},
                       'permissions': {'network': False}}],
            'skills': [{'id': 'explain', 'content': '只解释实际计算的数值。',
                        'requiredTools': ['double']}],
            'pipeline': [{'id': 'compute', 'kind': 'tool', 'tool': 'double', 'inputFrom': 'run.input'},
                         {'id': 'explain', 'kind': 'model', 'purpose': '解释计算结果',
                          'inputFrom': 'steps.compute.output'}],
            'permissions': {'network': False}}


class Provider:
    model = 'fixture-only'

    def __init__(self):
        self.source_keys = []
        self.recommended = 'strategy-workshop'
        self.calls = []
        self.analysis_override = None
        self.composition_override = None
        self.spec_override = None

    def tokens(self, messages, model, stopped, on_response):
        self.calls.append(copy.deepcopy(messages))
        system = messages[0]['content']
        if '应用工坊需求分析员' in system:
            result = self.analysis_override or {'analysis': {'understanding': '原来你想整理金额。',
                                   'workflow': [{'title': '计算', 'description': '计算实际金额。'}],
                                   'tools': ['计算工具'], 'skills': ['解释结果']},
                      'recommendation': None if self.recommended is None else {
                          'appKey': self.recommended, 'reason': '已有相关能力', 'covered': ['部分计算'],
                          'gaps': ['新规则'], 'inputs': {}}}
        elif '应用工坊组合规划员' in system:
            result = self.composition_override or {'sourceApps': [{'appKey': key, 'reason': '复用计算', 'reuse': ['金额计算']}
                                     for key in self.source_keys],
                      'composition': {'summary': '组合真实计算步骤', 'changes': ['调整规则']}}
        else:
            result = self.spec_override or generated_spec()
        yield json.dumps(result, ensure_ascii=False)


class AppWorkshopIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-app-workshop-final-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.platform = self.app.extensions['agent_platform']
        self.assets = self.app.extensions['asset_apps']
        if 'app_workshop' not in self.app.extensions:
            self.app.extensions['app_workshop'] = AppWorkshopService(self.platform, self.assets)
            self.app.register_blueprint(app_workshop)
        self.service = self.app.extensions['app_workshop']
        self.provider = Provider()
        self.platform.planner = Planner(self.provider)
        self.platform.executor.execute = Mock(side_effect=AssertionError('save must not execute'))
        self.app.extensions['workbench_service'].open_file = Mock(side_effect=AssertionError('save must not read files'))
        self.client = self.app.test_client()
        self.other = self.app.test_client()
        self.assertEqual(self.client.post('/desktop/api/register', json={
            'email': 'workshop@example.invalid', 'password': 'test-password'}).status_code, 200)
        self.assertEqual(self.other.post('/desktop/api/register', json={
            'email': 'other-workshop@example.invalid', 'password': 'test-password'}).status_code, 200)
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.base = '/desktop/api/app-workshop/sessions'

    def tearDown(self):
        self.app.extensions['workbench_service'].close()
        self.temp.cleanup()

    def post(self, session, operation, key=None, **extra):
        return self.client.post(f'{self.base}/{session["id"]}/{operation}', json={
            'revision': session['revision'], 'idempotencyKey': key or operation, **extra})

    def draft(self, key='analyze', auto_save=False):
        analyzed = self.client.post(self.base, json={
            'requirement': '整理金额 100', 'idempotencyKey': key})
        self.assertEqual(analyzed.status_code, 201, analyzed.json)
        # Retain legacy draft fixtures for crash recovery/source-change tests.
        # New user-facing creation is covered separately below.
        if auto_save:
            created = self.post(analyzed.json, 'create', key='create-' + key)
        else:
            with patch.object(self.service, 'save', side_effect=lambda owner, sid, body:
                              {'session': self.service.session(owner, sid)}):
                created = self.post(analyzed.json, 'create', key='create-' + key)
        self.assertEqual(created.status_code, 200, created.json)
        return created.json

    def test_create_immediately_saves_unverified_without_running(self):
        saved = self.draft(auto_save=True)
        self.assertEqual(saved['status'], 'saved')
        detail = self.assets.detail(self.owner, saved['savedAppId'])
        self.assertEqual(detail['verification']['status'], 'unverified')
        self.assertIsNone(detail['enabledVersionId'])
        self.assertEqual(self.assets.runs(self.owner, detail['id']), [])
        replay = self.client.post(f'{self.base}/{saved["id"]}/create', json={
            'revision': 1, 'idempotencyKey': 'create-analyze'})
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json, saved)
        self.assertEqual(len(self.assets.listing(self.owner)), 1)
        self.assertEqual(len(self.provider.calls), 3)
        self.assertEqual(len(saved['modelTraceIds']), 3)
        self.assertEqual(detail['definition']['source']['modelTraceIds'], saved['modelTraceIds'])
        trace_url = '/desktop/api/app-workshop/model-traces/' + saved['modelTraceIds'][0]
        trace = self.client.get(trace_url)
        self.assertEqual(trace.status_code, 200)
        self.assertEqual(trace.json['status'], 'completed')
        self.assertFalse(trace.json['detail']['truncated'])
        self.assertEqual(trace.json['detail']['value']['request']['messages'][0]['role'], 'system')
        self.assertEqual(self.other.get(trace_url).status_code, 404)
        self.platform.executor.execute.assert_not_called()
        self.assertEqual(self.post(saved, 'refine', instructions='修改规则').status_code, 409)

    def test_create_recovers_receipt_failure_without_regenerating(self):
        original = self.service.store.commit
        def interrupt_receipt(*args, **kwargs):
            if kwargs.get('saved') is not None:
                raise GatewayError('fixture interrupted', 503)
            return original(*args, **kwargs)
        analyzed = self.client.post(self.base, json={'requirement': '整理金额', 'idempotencyKey': 'recover'}).json
        with patch.object(self.service.store, 'commit', side_effect=interrupt_receipt):
            first = self.post(analyzed, 'create', key='recover-create')
        self.assertEqual(first.status_code, 503)
        self.assertEqual(len(self.assets.listing(self.owner)), 1)
        second = self.post(analyzed, 'create', key='recover-create')
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json['status'], 'saved')
        self.assertEqual(len(self.assets.listing(self.owner)), 1)
        self.assertEqual(len(self.provider.calls), 3)

    def save(self, draft, key='save'):
        return self.post(draft, 'save', key=key, appDigest=draft['appPreview']['appDigest'])

    def test_complete_definition_and_save_without_execution(self):
        draft = self.draft()
        private = self.service.store.get(self.owner, draft['id'])
        self.assertEqual([node['id'] for node in private['_definition']['nodes']], ['compute', 'explain'])
        self.assertEqual(private['_definition']['format'], 'cd80.app/2')
        self.assertIn('legacy_implementation.py', str(private['_definition']['nodes'][0]['component']['files']))
        self.assertIn('只解释实际计算的数值', str(private['_definition']['nodes'][1]['component']))
        response = self.save(draft)
        self.assertEqual(response.status_code, 200, response.json)
        saved = response.json
        self.assertEqual(saved['session']['savedAppId'], saved['appId'])
        self.assertEqual(saved['detail']['verification']['status'], 'unverified')
        self.assertIsNone(saved['detail']['enabledVersionId'])
        self.assertEqual(saved['detail']['issues'], [])
        self.assertEqual(self.assets.runs(self.owner, saved['appId']), [])
        self.assertEqual(self.platform.repository.list_agents(self.owner), [])
        self.assertEqual(self.save(draft).json['appId'], saved['appId'])
        self.assertEqual(len(self.assets.listing(self.owner)), 1)
        blocked = self.client.post('/desktop/api/asset-apps/' + saved['appId'] + '/enable',
                                   json={'versionId': saved['versionId'], 'enabled': True,
                                         'revision': saved['detail']['revision']},
                                   headers={'Idempotency-Key': 'enable-unverified'})
        self.assertEqual(blocked.status_code, 409, blocked.json)
        self.assertEqual(self.other.get('/desktop/api/asset-apps/' + saved['appId']).status_code, 404)
        self.platform.executor.execute.assert_not_called()

    def test_original_candidate_scope_and_multiple_real_definitions(self):
        personal = validate_agent_spec({**generated_spec(), 'id': 'personal-amount'})
        hidden = validate_agent_spec({**generated_spec(), 'id': 'other-private'})
        self.platform.repository.put_agent(self.owner, personal)
        self.platform.repository.put_agent('another-owner', hidden)
        unrelated = self.assets.save(self.owner, {'definition': {
            'format': 'cd80.app/2', 'name': '平台新版但非工坊来源', 'nodes': []}}, 'unrelated-asset')
        self.provider.source_keys = ['csv-summary@1', 'personal-amount@1']
        draft = self.draft()
        analyzed_payload = json.loads(self.provider.calls[0][1]['content'])
        analyzed_keys = {item['appKey'] for item in analyzed_payload['apps']}
        self.assertIn('strategy-workshop', analyzed_keys)
        self.assertIn('csv-summary@1', analyzed_keys)
        self.assertIn('personal-amount@1', analyzed_keys)
        self.assertNotIn('other-private@1', analyzed_keys)
        self.assertNotIn('workshop-app:' + unrelated['id'], analyzed_keys)
        compose_payload = json.loads(self.provider.calls[1][1]['content'])
        compose_keys = {item['appKey'] for item in compose_payload['apps']}
        self.assertEqual(compose_keys, {'csv-summary@1', 'personal-amount@1'})
        generation_payload = json.loads(self.provider.calls[2][1]['content'])
        self.assertEqual([item['spec'] for item in generation_payload['sourceApps']],
                         [self.platform.agent(self.owner, 'csv-summary@1'), personal])
        self.assertIn('code', generation_payload['sourceApps'][1]['spec']['tools'][0])
        self.assertNotIn('spec', draft['appPreview']['sourceApps'][0])
        self.assertEqual([item['kind'] for item in draft['appPreview']['sourceApps']], ['agent', 'agent'])
        replay = self.post({'id': draft['id'], 'revision': 1}, 'create', key='create-analyze').json
        self.assertEqual(replay['status'], 'saved')
        self.assertEqual(replay['appPreview'], draft['appPreview'])
        self.assertEqual(self.post({'id': draft['id'], 'revision': 1}, 'create',
                                   key='stale-create').status_code, 409)
        self.platform.executor.execute.assert_not_called()

    def test_invalid_model_references_and_failed_generation_preserve_session(self):
        self.provider.analysis_override = {'analysis': {'understanding': '分析需求',
            'workflow': [{'title': '处理', 'description': '计算'}], 'tools': [], 'skills': []},
            'recommendation': {'appKey': 'other-private@1', 'reason': '伪造', 'covered': [],
                               'gaps': [], 'inputs': {}}}
        invalid = self.client.post(self.base, json={'requirement': '分析金额', 'idempotencyKey': 'invalid'})
        self.assertEqual(invalid.status_code, 502, invalid.json)
        self.assertIsNone(self.service.store.current(self.owner))
        self.provider.analysis_override = None
        analyzed = self.client.post(self.base, json={'requirement': '分析金额', 'idempotencyKey': 'invalid'}).json
        self.provider.composition_override = {'sourceApps': [{'appKey': 'strategy-workshop',
            'reason': '冒充通用来源', 'reuse': ['不存在的代码']}],
            'composition': {'summary': '无效', 'changes': []}}
        bad_source = self.post(analyzed, 'create')
        self.assertEqual(bad_source.status_code, 502, bad_source.json)
        self.assertEqual(self.service.session(self.owner, analyzed['id']), analyzed)
        self.provider.composition_override = None
        self.provider.spec_override = {**generated_spec(), 'tools': [{**generated_spec()['tools'][0],
            'code': 'def run(payload):\n return ('}]}
        bad_code = self.post(analyzed, 'create')
        self.assertEqual(bad_code.status_code, 502, bad_code.json)
        self.assertEqual(self.service.session(self.owner, analyzed['id']), analyzed)
        self.provider.spec_override = None
        with patch('backend.app.modules.app_workshop.service.migrate_pipeline',
                   side_effect=ValueError('unsupported fixture conversion')):
            conversion = self.post(analyzed, 'create')
        self.assertEqual(conversion.status_code, 422, conversion.json)
        self.assertEqual(conversion.json['code'], 'app_workshop_incomplete')
        self.assertEqual(self.service.session(self.owner, analyzed['id']), analyzed)
        self.assertEqual(self.post(analyzed, 'create').status_code, 200)
        self.platform.executor.execute.assert_not_called()

    def test_refine_invalidates_private_draft_and_old_revision(self):
        draft = self.draft()
        refined = self.post(draft, 'refine', instructions='增加风险说明')
        self.assertEqual(refined.status_code, 200, refined.json)
        self.assertEqual(refined.json['status'], 'analyzed')
        self.assertEqual(refined.json['clarifications'], ['增加风险说明'])
        self.assertIsNone(refined.json['appPreview'])
        internal = self.service.store.get(self.owner, draft['id'])
        self.assertNotIn('_spec', internal)
        self.assertNotIn('_definition', internal)
        self.assertEqual(self.save(draft).status_code, 409)
        self.assertEqual(self.post(draft, 'create', key='stale-create').status_code, 409)

    def test_generated_wiring_mismatch_is_rejected_before_save(self):
        analyzed = self.client.post(self.base, json={
            'requirement': '把金额和折扣率一起计算', 'idempotencyKey': 'wiring'}).json
        base = generated_spec()
        extra_input = copy.deepcopy(base)
        extra_input['inputSchema'] = copy.deepcopy(extra_input['inputSchema'])
        extra_input['inputSchema']['properties']['discountRate'] = {'type': 'number'}
        extra_input['inputSchema']['required'].append('discountRate')
        self.provider.spec_override = extra_input
        mismatch = self.post(analyzed, 'create', key='wiring-create')
        self.assertEqual(mismatch.status_code, 422, mismatch.json)
        self.assertEqual(mismatch.json['code'], 'app_workshop_incomplete')
        self.assertEqual(self.service.session(self.owner, analyzed['id']), analyzed)

        downstream = copy.deepcopy(base)
        downstream['tools'][0]['outputSchema'] = {'type': 'object',
            'properties': {'doubled': {'type': 'number'}}, 'required': ['doubled'],
            'additionalProperties': False}
        downstream['tools'].append({**copy.deepcopy(base['tools'][0]), 'id': 'discount',
            'inputSchema': {'type': 'object', 'properties': {'discountRate': {'type': 'number'}},
                            'required': ['discountRate'], 'additionalProperties': False}})
        downstream['pipeline'].insert(1, {'id': 'discount', 'kind': 'tool', 'tool': 'discount',
                                          'inputFrom': 'steps.compute.output'})
        self.provider.spec_override = downstream
        mismatch = self.post(analyzed, 'create', key='wiring-create')
        self.assertEqual(mismatch.status_code, 422, mismatch.json)
        self.assertEqual(self.service.session(self.owner, analyzed['id']), analyzed)
        self.provider.spec_override = None
        self.assertEqual(self.post(analyzed, 'create', key='wiring-create').status_code, 200)

    def test_recommended_agent_safe_inputs_and_strategy_handoff(self):
        self.provider.analysis_override = {'analysis': {'understanding': '整理表格',
            'workflow': [{'title': '计算', 'description': '汇总金额'}], 'tools': ['表格'], 'skills': []},
            'recommendation': {'appKey': 'csv-summary@1', 'reason': '已有分组汇总',
                               'covered': ['求和'], 'gaps': [],
                               'inputs': {'groupBy': '行业', 'valueColumn': '金额',
                                          'fileId': 'forged-file', 'unknown': 'ignore'}}}
        analyzed = self.client.post(self.base, json={'requirement': '把表格按行业分组，对金额求和',
            'idempotencyKey': 'agent-handoff'}).json
        result = self.post(analyzed, 'handoff')
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['target'], {'kind': 'agent', 'agentRef': 'csv-summary@1'})
        self.assertEqual(result.json['inputs'], {'groupBy': '行业', 'valueColumn': '金额'})
        self.assertEqual(self.post(analyzed, 'handoff').json, result.json)
        self.provider.analysis_override = None
        strategy = self.client.post(self.base, json={'requirement': '测试均线策略',
            'idempotencyKey': 'strategy-handoff'}).json
        handed = self.post(strategy, 'handoff', key='strategy-use')
        self.assertEqual(handed.json['target'], {'kind': 'strategy-workshop'})
        self.assertEqual(handed.json['requirement'], '测试均线策略')
        self.assertEqual(self.platform.repository.list_runs(self.owner), [])

    def test_authentication_and_private_session_isolation(self):
        unauthenticated = self.app.test_client()
        self.assertEqual(unauthenticated.get(self.base + '/current').status_code, 401)
        draft = self.draft()
        self.assertIsNone(self.other.get(self.base + '/current').json['session'])
        self.assertEqual(self.other.get(self.base + '/' + draft['id']).status_code, 404)
        for operation in ('create', 'save', 'refine', 'handoff'):
            payload = {'revision': draft['revision'], 'idempotencyKey': operation,
                       **({'appDigest': draft['appPreview']['appDigest']} if operation == 'save' else {}),
                       **({'instructions': '伪造补充'} if operation == 'refine' else {})}
            self.assertEqual(self.other.post(f'{self.base}/{draft["id"]}/{operation}',
                                             json=payload).status_code, 404)
        self.assertEqual(self.client.post(self.base, json={'requirement': '分析金额',
            'idempotencyKey': 'forged-owner', 'owner': 'other'}).status_code, 400)

    def test_workshop_source_scope_and_changed_version(self):
        saved = self.save(self.draft()).json
        self.assertNotIn('workshop-app:' + saved['appId'],
                         {item['appKey'] for item in self.service._catalog(self.owner)})
        self.assertEqual(len(self.service._asset_sources(self.owner)), 1)
        self.provider.source_keys = ['workshop-app:' + saved['appId']]
        next_draft = self.draft('second')
        generation_payload = json.loads(self.provider.calls[-1][1]['content'])
        self.assertEqual(generation_payload['sourceApps'][0]['spec'],
                         self.service.store.sources(self.owner)[0]['spec'])
        self.assertIn('code', generation_payload['sourceApps'][0]['spec']['tools'][0])
        self.assertEqual(next_draft['appPreview']['sourceApps'][0]['appId'], saved['appId'])
        self.assertEqual(next_draft['appPreview']['sourceApps'][0]['definitionDigest'],
                         saved['detail']['definitionDigest'])
        self.assertEqual(self.other.get(self.base + '/' + next_draft['id']).status_code, 404)
        changed = copy.deepcopy(saved['detail']['definition'])
        changed['name'] = '后来修改的实现'
        newer = self.assets.save(self.owner, {'definition': changed, 'appId': saved['appId'],
                                               'revision': saved['detail']['revision']}, 'new-version')
        self.assertNotEqual(newer['versionId'], saved['versionId'])
        self.assertEqual(self.service._asset_sources(self.owner), [])
        rejected = self.save(next_draft)
        self.assertEqual(rejected.status_code, 409, rejected.json)
        self.assertEqual(rejected.json['code'], 'source_changed')

    def test_verified_enabled_workshop_app_recommendation_rechecks_current_version(self):
        saved = self.save(self.draft()).json
        app_id, version_id = saved['appId'], saved['versionId']
        key = 'workshop-app:' + app_id
        # Simulate passed verification metadata to isolate candidate gating.
        # The real model/WASI acceptance separately proves verification itself.
        with self.assets.store.transaction() as db:
            version = self.assets.store.get(db, 'versions', self.owner, version_id)
            version['verification'] = {'status': 'passed', 'cases': [{}, {}], 'runIds': [],
                                       'contextDigest': self.assets._context(self.owner, version['definition'])}
            self.assets.store.put(db, 'versions', self.owner, version)
            app = self.assets.store.get(db, 'apps', self.owner, app_id)
            app['enabledVersionId'] = version_id
            self.assets.store.put(db, 'apps', self.owner, app)
        self.assertIn(key, {item['appKey'] for item in self.service._catalog(self.owner)})
        self.provider.recommended = key
        analyzed = self.client.post(self.base, json={'requirement': '整理金额 100',
            'idempotencyKey': 'recommend-enabled'}).json
        self.assertEqual(analyzed['recommendation']['kind'], 'asset-app')
        changed = copy.deepcopy(saved['detail']['definition'])
        changed['name'] += '新版'
        self.assets.save(self.owner, {'definition': changed, 'appId': app_id,
                                      'revision': self.assets.detail(self.owner, app_id)['revision']},
                         'change-recommended')
        self.assertNotIn(key, {item['appKey'] for item in self.service._catalog(self.owner)})
        rejected = self.post(analyzed, 'handoff')
        self.assertEqual(rejected.status_code, 409, rejected.json)
        self.assertEqual(rejected.json['code'], 'recommendation_changed')

    def test_archived_legacy_and_workshop_sources_are_not_reused(self):
        personal = validate_agent_spec({**generated_spec(), 'id': 'personal-archive'})
        self.platform.repository.put_agent(self.owner, personal)
        self.provider.source_keys = ['personal-archive@1']
        legacy_draft = self.draft('legacy-archive')
        with self.platform.repository.transaction() as db:
            db.execute('INSERT INTO agent_metadata VALUES(?,?,?)', (
                self.owner, 'personal-archive@1', json.dumps({'lifecycle': 'archived'})))
        self.assertNotIn('personal-archive@1',
                         {item['appKey'] for item in self.service._catalog(self.owner)})
        rejected = self.save(legacy_draft)
        self.assertEqual(rejected.status_code, 409, rejected.json)
        self.assertEqual(rejected.json['code'], 'source_changed')

        self.provider.source_keys = []
        saved = self.save(self.draft('workshop-archive'), key='save-archive').json
        self.provider.source_keys = ['workshop-app:' + saved['appId']]
        asset_draft = self.draft('asset-archive')
        with self.assets.store.transaction() as db:
            record = self.assets.store.get(db, 'apps', self.owner, saved['appId'])
            record['lifecycle'] = 'archived'
            self.assets.store.put(db, 'apps', self.owner, record)
        self.assertEqual(self.service._asset_sources(self.owner), [])
        rejected = self.save(asset_draft, key='save-archived-source')
        self.assertEqual(rejected.status_code, 409, rejected.json)
        self.assertEqual(rejected.json['code'], 'source_changed')

    def test_platform_saved_before_workshop_receipt_recovers_without_duplicate(self):
        draft = self.draft()
        original = self.service.store.commit
        failed = {'done': False}

        def interrupt_once(*args, **kwargs):
            if kwargs.get('saved') is not None and not failed['done']:
                failed['done'] = True
                raise GatewayError('fixture interrupted between services', 503, 'fixture_interrupted')
            return original(*args, **kwargs)

        with patch.object(self.service.store, 'commit', side_effect=interrupt_once):
            first = self.save(draft)
        self.assertEqual(first.status_code, 503, first.json)
        self.assertEqual(len(self.assets.listing(self.owner)), 1)
        self.assertEqual(self.service.store.get(self.owner, draft['id'])['status'], 'draft_ready')
        self.service.close()
        self.service = AppWorkshopService(self.platform, self.assets)
        self.app.extensions['app_workshop'] = self.service
        self.app.extensions['workbench_service'].app_workshop = self.service
        second = self.save(draft, key='recovery')
        self.assertEqual(second.status_code, 200, second.json)
        self.assertEqual(len(self.assets.listing(self.owner)), 1)
        self.assertEqual(len(self.service.store.sources(self.owner)), 1)
        self.assertEqual(self.service.store.sources(self.owner)[0]['appId'], second.json['appId'])


if __name__ == '__main__':
    unittest.main()
