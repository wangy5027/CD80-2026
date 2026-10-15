import io
import json
from backend.app.modules.capability_library.asset_validation import verify_suite
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.sub_agent.store import now, digest
from backend.app.modules.capability_library.contracts import CapabilityError


class EvolutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-evolution-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.w = self.app.extensions['workbench_service']
        self.e = self.app.extensions['evolution']
        self.client = self.app.test_client()
        self.other = self.app.test_client()
        for client, email in [(self.client, 'author@example.invalid'), (self.other, 'reader@example.invalid')]:
            response = client.post('/desktop/api/register', json={'email': email, 'password': 'test-password'})
            self.assertEqual(response.status_code, 200)
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.other_owner = self.other.get('/desktop/api/session').json['user']['id']
        self.n = 0

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def post(self, path, body, key=None, client=None):
        self.n += 1
        return (client or self.client).post('/desktop/api/evolution' + path, json=body,
               headers={'Idempotency-Key': key or str(self.n)})

    def create(self, **extra):
        response = self.post('/recipes', {'name': '比较方法', 'goalTemplate': '解释{{指标}}在{{场景}}中的含义', **extra})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def finish(self, run_id):
        for _ in range(300):
            run = self.w.sub_agents.store.run(self.owner, run_id)
            if run['status'] in {'succeeded', 'failed', 'cancelled', 'interrupted'}:
                return run
            time.sleep(.02)
        self.fail('Run did not finish')

    def execute(self, recipe, values):
        def model(stage, payload, *args, **kwargs):
            return {'mode': 'answer', 'answer': payload['message'], 'assessment': {'dependentProcessing': False}}
        with patch.object(self.w.sub_agents.model, 'call', model):
            response = self.post('/recipes/' + recipe['id'] + '/run', {'input': values})
            self.assertEqual(response.status_code, 202, response.json)
            run = self.finish(response.json['id'])
        self.assertEqual(run['status'], 'succeeded', run)
        return run

    def component(self, folder='decimal-total', *, owner=None, force_new=False, dependencies=None):
        from backend.app.modules.capability_library.packages import zip_bytes
        root = Path(__file__).resolve().parents[1] / 'docs/capability-library/examples' / folder
        files = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        if dependencies is not None:
            manifest = json.loads(files['capability.json'])
            manifest['dependencies'] = dependencies
            files['capability.json'] = json.dumps(manifest).encode('utf-8')
        owner = owner or self.owner
        imported = self.w.capabilities.import_package(owner, zip_bytes(files), folder + '.zip', force_new=force_new)
        return self.w.capabilities.version(owner, imported['assetId'], imported['versionId'])

    def enable_component(self, version, owner=None):
        owner = owner or self.owner
        lib = self.w.capabilities
        if version['manifest']['kind'] == 'tool':
            job = verify_suite(lib, owner, {**{k: version[k] for k in ('assetId', 'versionId', 'configurationId')},
                'cases': [{'input': {'values': values}, 'fileIds': [],
                    'expectation': {'mode': 'json_equals', 'value': {'total': total}}}
                    for values, total in [(['0.1', '0.2'], '0.3'), (['2', '5'], '7')]]})
            end = time.monotonic() + 30
            while job['status'] in {'queued', 'running'} and time.monotonic() < end:
                time.sleep(.02)
                job = lib.store.get(owner, 'job', job['jobId'])
            self.assertEqual(job['status'], 'completed', job)
            self.assertEqual(job['result']['status'], 'passed', job)
        else:
            # Skill human-review evidence is a fixture; execution below uses the
            # real admission, dependency locks, chat bridge and WASI Tool runner.
            # Represent an already reviewed legacy Skill, not a new import exemption.
            version.pop('validationPolicy', None)
            lib.store.put(owner, 'version', version['versionId'], version)
            vid = 'review-' + version['versionId']
            lib.store.put(owner, 'verification', vid, {'verificationId': vid,
                'versionId': version['versionId'], 'configurationId': version['configurationId'],
                'status': 'passed', 'contextDigest': lib.context_digest(version)})
        asset = lib.asset(owner, version['assetId'])
        lib.update_asset(owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, f'"{asset["revision"]}"')

    def skill_pair(self, owner):
        lib = self.w.capabilities
        first_tool = self.component(owner=owner)
        self.enable_component(first_tool, owner)
        dependency = {'sourceIdentity': first_tool['manifest']['source']['identity'],
                      'contentDigest': first_tool['contentDigest'], 'required': True, 'reason': 'Compute exact decimal totals.'}
        first = self.component('summary-skill', owner=owner, dependencies=[dependency])
        self.enable_component(first, owner)
        asset = lib.asset(owner, first_tool['assetId'])
        lib.update_asset(owner, asset['assetId'], {'enabled': False}, f'"{asset["revision"]}"')
        second_tool = self.component(owner=owner, force_new=True)
        self.enable_component(second_tool, owner)
        second = self.component('summary-skill', owner=owner, force_new=True, dependencies=[dependency])
        self.enable_component(second, owner)
        asset = lib.asset(owner, first_tool['assetId'])
        lib.update_asset(owner, asset['assetId'], {'enabled': True}, f'"{asset["revision"]}"')
        self.assertEqual(first['contentDigest'], second['contentDigest'])
        self.assertNotEqual(first['configurationId'], second['configurationId'])
        self.assertEqual(first['dependencyLocks'][0]['assetId'], first_tool['assetId'])
        self.assertEqual(second['dependencyLocks'][0]['assetId'], second_tool['assetId'])
        return first, second

    def test_parameterized_run_uses_new_values_and_no_old_grants(self):
        recipe = self.create()
        first = self.execute(recipe, {'指标': '收益率', '场景': '案例A'})
        second = self.execute(recipe, {'指标': '回撤', '场景': '案例B'})
        self.assertNotEqual(first['id'], second['id'])
        self.assertNotEqual(first['conversationId'], second['conversationId'])
        self.assertIn('回撤在案例B', second['answer'])
        self.assertNotIn('案例A', second['answer'])
        self.assertEqual(second['payload']['fileIds'], [])
        self.assertEqual(self.post('/recipes/' + recipe['id'] + '/run', {'input': {'指标': '缺失'}}).status_code, 400)

    def test_recipe_run_corrects_application_routing_without_creating_another_app(self):
        recipe = self.create(goalTemplate='运行应用并解释{{指标}}')
        calls = []

        def model(stage, payload, *args, **kwargs):
            self.assertEqual(stage, 'plan')
            self.assertEqual(payload['executionContext']['kind'], 'recipe')
            self.assertEqual(payload['executionContext']['recipeId'], recipe['id'])
            calls.append(payload)
            if len(calls) == 1:
                return {'mode': 'application', 'assessment': {'explicitApplication': True}}
            self.assertTrue(any('不能再次交给应用' in item.get('error', '') for item in payload['observations']))
            return {'mode': 'answer', 'answer': '收益率是收益与投入的比值。', 'assessment': {'explicitApplication': False}}

        with patch.object(self.w.sub_agents.model, 'call', model), \
                patch.object(self.w.platform, '_dispatch', side_effect=AssertionError('must not create or dispatch another app')):
            response = self.post('/recipes/' + recipe['id'] + '/run', {'input': {'指标': '收益率'}})
            self.assertEqual(response.status_code, 202, response.json)
            result = self.finish(response.json['id'])
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['correctionsUsed'], 1)
        self.assertEqual(len(calls), 2)
        self.assertNotIn('application', result)

    def test_recipe_application_loop_exhausts_bounded_corrections(self):
        recipe = self.create()
        with patch.object(self.w.sub_agents.model, 'call', return_value={'mode': 'application', 'assessment': {'explicitApplication': True}}), \
                patch.object(self.w.platform, '_dispatch', side_effect=AssertionError('must not dispatch')):
            response = self.post('/recipes/' + recipe['id'] + '/run', {'input': {'指标': '收益率', '场景': '样例'}})
            self.assertEqual(response.status_code, 202, response.json)
            result = self.finish(response.json['id'])
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['correctionsUsed'], 3)
        self.assertEqual(result['rounds'], 4)
        self.assertNotIn('application', result)

    def test_idempotency_and_ownership(self):
        body = {'name': 'test', 'goalTemplate': '解释{{指标}}'}
        first = self.post('/recipes', body, 'same')
        second = self.post('/recipes', body, 'same')
        self.assertEqual(first.json['id'], second.json['id'])
        self.assertEqual(self.post('/recipes', {**body, 'name': 'changed'}, 'same').status_code, 409)
        rid = first.json['id']
        self.assertEqual(self.other.get('/desktop/api/evolution/recipes/' + rid).status_code, 404)
        self.assertEqual(self.post('/recipes/' + rid + '/run', {'input': {'指标': 'x'}}, client=self.other).status_code, 404)
        self.assertEqual(self.client.post('/desktop/api/evolution/recipes', json=body).status_code, 400)

    def test_run_retry_returns_committed_run_without_rechecking_mutable_state(self):
        recipe = self.create()
        body = {'input': {'指标': '收益率', '场景': '幂等案例'}}
        with patch.object(self.w.sub_agents, 'kick'):
            first = self.post('/recipes/' + recipe['id'] + '/run', body, 'stable-run')
        self.assertEqual(first.status_code, 202, first.json)
        with patch.object(self.e, 'recipe', side_effect=CapabilityError('SOURCE_CHANGED', 'synthetic changed source', 409)), \
                patch.object(self.w.sub_agents, 'submit', side_effect=AssertionError('must not replay execution')):
            repeated = self.post('/recipes/' + recipe['id'] + '/run', body, 'stable-run')
        self.assertEqual(repeated.status_code, 202, repeated.json)
        self.assertEqual(first.json['id'], repeated.json['id'])
        conflict = self.post('/recipes/' + recipe['id'] + '/run', {'input': {'指标': '回撤', '场景': '幂等案例'}}, 'stable-run')
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json['code'], 'IDEMPOTENCY_CONFLICT')

    def test_publication_retry_returns_snapshot_without_rechecking_source(self):
        recipe = self.create()
        self.execute(recipe, {'指标': '收益率', '场景': '模拟'})
        body = {'kind': 'recipe', 'recipeId': recipe['id']}
        preview = self.post('/publications/preview', body).json
        payload = {**body, 'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True}
        first = self.post('/publications', payload, 'stable-release')
        self.assertEqual(first.status_code, 201, first.json)
        with patch.object(self.e, 'preview_publication', side_effect=CapabilityError('SOURCE_CHANGED', 'synthetic changed source', 409)):
            repeated = self.post('/publications', payload, 'stable-release')
        self.assertEqual(repeated.status_code, 201, repeated.json)
        self.assertEqual(repeated.json['id'], first.json['id'])

    def test_export_import_fork_are_independent_and_non_executing(self):
        recipe = self.create()
        package = self.client.get('/desktop/api/evolution/recipes/' + recipe['id'] + '/export').json
        self.assertNotIn('capabilityRefs', package['definition'])
        self.assertNotIn('sourceRunId', json.dumps(package))
        copied = self.post('/recipes/import', {'package': package}, client=self.other)
        self.assertEqual(copied.status_code, 201, copied.json)
        self.assertNotEqual(copied.json['id'], recipe['id'])
        fork = self.post('/recipes/' + recipe['id'] + '/fork', {'name': '修改版', 'goalTemplate': '比较{{变量}}'})
        self.assertEqual(fork.status_code, 201, fork.json)
        self.assertEqual(fork.json['parameters'], ['变量'])
        self.assertEqual(self.e.recipe(self.owner, recipe['id'])['name'], recipe['name'])
        self.assertEqual(self.w.store.get(self.other_owner)['tasks'], [])

    def test_publication_requires_execution_review_and_withdrawal_respects_copies(self):
        recipe = self.create()
        body = {'kind': 'recipe', 'recipeId': recipe['id']}
        self.assertEqual(self.post('/publications/preview', body).status_code, 409)
        self.execute(recipe, {'指标': '收益率', '场景': '模拟'})
        preview = self.post('/publications/preview', body)
        self.assertEqual(preview.status_code, 200, preview.json)
        self.assertEqual(self.post('/publications', {**body, 'snapshotDigest': preview.json['snapshotDigest']}).status_code, 409)
        publish = {**body, 'snapshotDigest': preview.json['snapshotDigest'], 'confirmSharing': True}
        release = self.post('/publications', publish, 'publish')
        self.assertEqual(release.status_code, 201, release.json)
        self.assertEqual(self.post('/publications', publish, 'publish').json['id'], release.json['id'])
        pid = release.json['id']
        items = self.other.get('/desktop/api/evolution/publications').json['items']
        self.assertFalse(items[0]['owned'])
        self.assertNotIn(self.owner, json.dumps(items))
        copied = self.post('/publications/' + pid + '/copy', {}, client=self.other)
        self.assertEqual(copied.status_code, 201, copied.json)
        modern = self.w.asset_apps.detail(self.other_owner, copied.json['assetId'])
        self.assertEqual(modern['versionId'], copied.json['versionId'])
        self.assertEqual(modern['verification']['status'], 'unverified')
        self.assertIsNone(modern['enabledVersionId'])
        self.assertEqual(modern['definition']['source']['package'], self.e.publication_package(self.owner, pid))
        self.assertEqual(modern['provenance']['resolvedBindings'], copied.json['recipe']['capabilityRefs'])
        portable = self.w.asset_apps.export(self.other_owner, copied.json['assetId'])
        self.assertNotIn('resolvedBindings', json.dumps(portable))
        self.assertEqual(self.post('/publications/' + pid + '/withdraw', {}, client=self.other).status_code, 404)
        self.assertEqual(self.post('/publications/' + pid + '/withdraw', {}).status_code, 200)
        self.assertEqual(self.post('/publications/' + pid + '/copy', {}, client=self.other).status_code, 410)
        self.assertEqual(self.other.get('/desktop/api/evolution/recipes/' + copied.json['recipe']['id']).status_code, 200)

    def test_secrets_and_private_paths_block_export(self):
        for template in ['api_key="abcdef1234567890"', '读取 D:/private/result.csv', '打开 /desktop/api/files/secret-id']:
            recipe = self.create(goalTemplate=template)
            response = self.client.get('/desktop/api/evolution/recipes/' + recipe['id'] + '/export')
            self.assertEqual(response.status_code, 403, response.json)

    def test_import_dependency_mismatch_and_template_errors(self):
        missing_name = self.post('/recipes', {'goalTemplate': '说明{{指标}}'})
        self.assertEqual(missing_name.status_code, 400, missing_name.json)
        self.assertEqual(self.post('/recipes', {'name': 'bad', 'goalTemplate': '{{broken'}).status_code, 400)
        self.assertEqual(self.post('/recipes', {'name': 'bad', 'goalTemplate': '{{__import__(x)}}'}).status_code, 400)
        package = {'format': 'infinity-application', 'formatVersion': '1', 'kind': 'recipe',
                   'definition': {'name': 'test', 'goalTemplate': 'hello'},
                   'dependencies': [{'sourceIdentity': 'missing/tool', 'contentDigest': '0' * 64, 'kind': 'tool'}]}
        response = self.post('/recipes/import', {'package': package})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'DEPENDENCY_MISSING')

    def test_conflicting_capability_versions_never_silently_replace_selected_method(self):
        from backend.app.modules.evolution.developer_kit import build
        from backend.app.modules.capability_library.packages import parse_package, zip_bytes
        from backend.app.modules.capability_library.contracts import canonical
        import zipfile
        with zipfile.ZipFile(io.BytesIO(build())) as archive:
            raw = archive.read('capabilities/decimal-total.zip')
        lib = self.w.capabilities
        first = lib.import_package(self.owner, raw, 'first.zip')
        manifest, files = parse_package(raw, 'first.zip')
        manifest['description'] = 'Different method version'
        second = lib.import_package(self.owner, zip_bytes({**files, 'capability.json': canonical(manifest).encode()}),
                                    'second.zip', target=first['assetId'])
        refs = [{k: value[k] for k in ('assetId', 'versionId')} for value in (first, second)]
        response = self.post('/recipes', {'name': '不得替换', 'goalTemplate': '计算{{数值}}', 'capabilityRefs': refs})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'DEPENDENCY_CONFLICT')
        self.assertEqual(self.e.recipes(self.owner), [])
        saved = self.create(capabilityRefs=[refs[0], refs[0]])
        self.assertEqual(saved['capabilityRefs'], [refs[0]])

    def test_duplicate_source_slots_preserve_exact_choices_across_accounts_and_exports(self):
        first, second = self.component(), self.component(force_new=True)
        refs = [{k: version[k] for k in ('assetId', 'versionId')} for version in (first, second)]
        recipe = self.create(capabilityRefs=refs)
        package = self.e.portable(self.owner, recipe['id'])
        self.assertEqual([d['bindingId'] for d in package['dependencies']], ['capability-1', 'capability-2'])
        for local in ('assetId', 'versionId', 'configurationId'):
            self.assertNotIn(local, json.dumps(package))
        ambiguous = self.post('/recipes/import', {'package': package})
        self.assertEqual(ambiguous.status_code, 409)
        self.assertEqual(ambiguous.json['details']['dependency']['bindingId'], 'capability-1')
        # Explicit order and custom portable slot IDs survive another export and a fork.
        for dependency, key in zip(package['dependencies'], ['method-left', 'method-right']):
            dependency['bindingId'] = key
        bindings = dict(zip(['method-left', 'method-right'], reversed(refs)))
        with patch.object(self.w.sub_agents, 'submit', side_effect=AssertionError('import must not run')):
            copied = self.post('/recipes/import', {'package': package, 'dependencyBindings': bindings})
            self.assertEqual(copied.status_code, 201, copied.json)
            self.assertEqual(copied.json['capabilityRefs'], list(reversed(refs)))
            self.assertEqual(self.e.portable(self.owner, copied.json['id']), package)
            fork = self.post('/recipes/' + copied.json['id'] + '/fork', {'name': 'Renamed method'})
            self.assertEqual(fork.json['capabilityBindingIds'], ['method-left', 'method-right'])
            remote_first = self.component(owner=self.other_owner)
            collapsed = self.post('/recipes/import', {'package': package}, client=self.other)
            self.assertEqual(collapsed.status_code, 409, collapsed.json)
            self.assertEqual(collapsed.json['code'], 'DEPENDENCY_BINDING_CONFLICT')
            self.assertEqual(self.e.recipes(self.other_owner), [])
            remote_second = self.component(owner=self.other_owner, force_new=True)
            remote_refs = [{k: v[k] for k in ('assetId', 'versionId')} for v in (remote_second, remote_first)]
            remote_bindings = dict(zip(['method-left', 'method-right'], remote_refs))
            remote = self.post('/recipes/import', {'package': package, 'dependencyBindings': remote_bindings}, client=self.other)
            self.assertEqual(remote.status_code, 201, remote.json)
            self.assertEqual(remote.json['capabilityRefs'], remote_refs)
            self.assertEqual(self.e.portable(self.other_owner, remote.json['id']), package)
            collapsed = self.post('/recipes/import', {'package': package,
                'dependencyBindings': dict.fromkeys(['method-left', 'method-right'], remote_refs[0])}, client=self.other)
            self.assertEqual(collapsed.json['code'], 'DEPENDENCY_BINDING_CONFLICT')

    def test_legacy_dependency_binding_and_invalid_slots_are_explicit(self):
        import copy
        version = self.component()
        ref = {k: version[k] for k in ('assetId', 'versionId')}
        recipe = self.create(capabilityRefs=[ref])
        package = self.e.portable(self.owner, recipe['id'])
        legacy = copy.deepcopy(package)
        legacy['dependencies'][0].pop('bindingId')
        self.component(force_new=True)
        copied = self.post('/recipes/import', {'package': legacy,
            'dependencyBindings': {legacy['dependencies'][0]['sourceIdentity']: ref}})
        self.assertEqual(copied.status_code, 201, copied.json)
        self.assertEqual(copied.json['capabilityRefs'], [ref])
        self.assertEqual(self.e.portable(self.owner, copied.json['id']), package)
        for bad in ('', 'bad:slot', 'a' * 65, None, []):
            malformed = copy.deepcopy(package)
            malformed['dependencies'][0]['bindingId'] = bad
            response = self.post('/recipes/import', {'package': malformed})
            self.assertEqual(response.status_code, 400, response.json)
        for dependencies in ([*legacy['dependencies'], *legacy['dependencies']],
                             [*package['dependencies'], *package['dependencies']],
                             [*legacy['dependencies'], *package['dependencies']]):
            response = self.post('/recipes/import', {'package': {**package, 'dependencies': dependencies}})
            self.assertEqual(response.status_code, 400, response.json)
            self.assertEqual(response.json['code'], 'INVALID_PACKAGE')
        response = self.post('/recipes/import', {'package': package,
            'dependencyBindings': {'capability-1': ref, 'unused-slot': ref}})
        self.assertEqual(response.status_code, 400, response.json)

    def test_same_content_skills_keep_distinct_configs_dependencies_and_runtime_sources(self):
        from backend.app.modules.capability_library.chat_bridge import ChatCapabilities
        author = self.skill_pair(self.owner)
        recipe = self.create(capabilityRefs=[{k: v[k] for k in ('assetId', 'versionId')} for v in author])
        self.execute(recipe, {'指标': 'total', '场景': 'sample'})
        preview = self.post('/publications/preview', {'kind': 'recipe', 'recipeId': recipe['id']}).json
        publication = self.post('/publications', {'kind': 'recipe', 'recipeId': recipe['id'],
            'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True}).json
        receiver = self.skill_pair(self.other_owner)
        bindings = {'capability-' + str(index + 1): {k: v[k] for k in ('assetId', 'versionId')}
                    for index, v in enumerate(reversed(receiver))}
        with patch.object(self.w.sub_agents, 'kick'):
            copied = self.post('/publications/' + publication['id'] + '/copy',
                               {'dependencyBindings': bindings}, client=self.other)
            self.assertEqual(copied.status_code, 201, copied.json)
            saved = copied.json['recipe']
            self.assertEqual(self.w.asset_apps.detail(self.other_owner, copied.json['assetId'])['verification']['status'], 'unverified')
            self.assertEqual(saved['source'], {'kind': 'publication', 'id': publication['id'], 'digest': preview['snapshotDigest']})
            self.assertEqual(saved['capabilityRefs'], list(bindings.values()))
            submitted = self.post('/recipes/' + saved['id'] + '/run',
                                  {'input': {'指标': 'total', '场景': 'new sample'}}, client=self.other)
        self.assertEqual(submitted.status_code, 202, submitted.json)
        bridge = ChatCapabilities(self.w.capabilities, self.other_owner, submitted.json['conversationId'], [], [])
        for ref in saved['capabilityRefs']:
            bridge.load(ref['assetId'], ref['versionId'], lambda event: None)
        self.assertEqual(len(bridge.loaded), 2)
        self.assertEqual(len(bridge.tools), 2)
        self.assertEqual({v['configurationId'] for v in bridge.loaded.values()}, {v['configurationId'] for v in receiver})
        for alias, (tool, sources) in bridge.tools.items():
            result = bridge.execute(alias, {'values': ['2.5', '1.25']}, lambda event: None, None)
            self.assertEqual(result['content'], {'total': '3.75'})
            parent = bridge.loaded[sources[0]['assetId']]
            self.assertEqual(parent['dependencyLocks'][0]['assetId'], tool['assetId'])
            invocation = next(i for i in self.w.capabilities.store.list(self.other_owner, 'invocation')
                              if i['taskId'] == submitted.json['conversationId'] and i['assetId'] == tool['assetId'])
            self.assertEqual(invocation['sourceSkillRefs'], sources)
        self.assertEqual(self.e.portable(self.other_owner, saved['id']), preview['package'])

    def test_recipe_run_rechecks_disable_archive_and_configuration_after_static_import(self):
        version = self.component()
        self.enable_component(version)
        recipe = self.create(capabilityRefs=[{k: version[k] for k in ('assetId', 'versionId')}])
        package = self.e.portable(self.owner, recipe['id'])
        copied = self.post('/recipes/import', {'package': package}).json
        lib = self.w.capabilities
        with patch.object(self.w.sub_agents, 'submit', side_effect=AssertionError('rejected dependency must not submit')):
            revision = lib.runner_revision
            lib.runner_revision += '-changed'
            response = self.post('/recipes/' + copied['id'] + '/run', {'input': {'指标': 'x', '场景': 'y'}})
            self.assertEqual(response.json['code'], 'STALE_CONFIGURATION')
            lib.runner_revision = revision
            for change, expected in [({'enabled': False}, 'CAPABILITY_DISABLED'),
                                     ({'lifecycle': 'archived'}, 'CAPABILITY_BLOCKED')]:
                asset = lib.asset(self.owner, version['assetId'])
                lib.update_asset(self.owner, asset['assetId'], change, f'"{asset["revision"]}"')
                response = self.post('/recipes/' + copied['id'] + '/run', {'input': {'指标': 'x', '场景': 'y'}})
                self.assertEqual(response.status_code, 403, response.json)
                self.assertEqual(response.json['code'], expected)

    def test_capture_source_ownership_and_failed_runs(self):
        recipe = self.create()
        run = self.execute(recipe, {'指标': '收益率', '场景': '样例'})
        response = self.post('/captures/preview', {'runId': run['id'], 'kind': 'recipe'})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['recipe']['sourceRunId'], run['id'])
        self.assertEqual(self.post('/captures/preview', {'runId': run['id'], 'kind': 'recipe'}, client=self.other).status_code, 404)
        self.assertEqual(self.post('/captures/preview', {'runId': run['id'], 'kind': 'tool'}).status_code, 422)
        skill = self.post('/captures/preview', {'runId': run['id'], 'kind': 'skill'})
        self.assertEqual(skill.status_code, 200, skill.json)
        self.assertEqual(self.w.capabilities.store.list(self.owner, 'draft'), [])

    def test_captured_tool_runs_actual_code_on_different_authorized_file(self):
        source = "def run(payload):\n    values = open(payload['files']['old-private-id']).read().split(',')\n    return {'total': sum(int(v) for v in values)}\n"
        old = self.w.sub_agents.executor.execute(source, {'files': {'old-private-id': '/input/old.csv'}}, {'old.csv': b'1,2'})
        self.assertEqual(old['result'], {'total': 3})
        rid, aid = 'source-run', 'source-code'
        meta = {'id': aid, 'name': 'analysis.py', 'sha256': digest(source.encode()), 'mediaType': 'text/x-python'}
        run = {'id': rid, 'status': 'succeeded', 'message': '对输入数据求和', 'payload': {}, 'loadedRefs': [],
               'artifacts': [meta], 'steps': [{'id': 'step1', 'type': 'code', 'status': 'succeeded',
               'inputRefs': ['old-private-id'], 'result': {'codeArtifactId': aid}}]}
        with self.w.sub_agents.store.transaction() as db:
            self.w.sub_agents.store.put(db, self.owner, 'run', rid, run)
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (self.owner, aid, rid, json.dumps(meta), source.encode()))
        response = self.post('/captures/preview', {'runId': rid, 'kind': 'tool'})
        self.assertEqual(response.status_code, 200, response.json)
        draft = response.json['draft']
        self.assertNotIn('old-private-id', json.dumps(draft))
        record = self.w.capabilities.draft(self.owner, draft)
        saved = self.w.capabilities.save_draft(self.owner, record['draftId'], {'draftRevision': record['revision']})
        self.assertEqual(saved['readiness'], 'unverified', saved)
        uploaded = self.client.post('/desktop/api/files', data={'file': (io.BytesIO(b'10,20'), 'new.csv')}, content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 201, uploaded.json)
        file_id = uploaded.json['id']
        result = self.w.capabilities.invoke(self.owner, {**saved, 'input': {'input_1': '/input/' + file_id + '.csv'}, 'fileIds': [file_id]}, trial=True)
        self.assertEqual(result['result']['content'], {'total': 30})
        self.assertFalse(self.w.capabilities.asset(self.owner, saved['assetId'])['enabled'])


if __name__ == '__main__':
    unittest.main()
