"""Portable application ownership, re-use, and capability admission regressions."""
import copy
import io
import json
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tests import test_agent_platform as base
from backend.app.modules.agent_platform.contracts import validate_agent_spec
from backend.app.modules.agent_platform.library_capabilities import reference
from backend.app.modules.capability_library.packages import file_index, zip_bytes
from backend.app.modules.capability_library.asset_validation import verify_suite


class ApplicationLifecycleTests(unittest.TestCase):
    setUp = base.AgentPlatformTests.setUp
    tearDown = base.AgentPlatformTests.tearDown
    upload = base.AgentPlatformTests.upload
    wait = base.AgentPlatformTests.wait
    new_run = base.AgentPlatformTests.new_run
    approve = base.AgentPlatformTests.approve

    def saved(self, spec=None):
        spec = validate_agent_spec(spec or base.fixture_spec())
        spec['id'] = 'saved-' + uuid4().hex
        self.platform.repository.put_agent(self.owner, spec)
        return spec, spec['id'] + '@' + spec['version']

    def portable(self, spec=None):
        saved, ref = self.saved(spec)
        return saved, self.platform.export_application(self.owner, ref)

    def component(self, folder='decimal-total', owner=None, dependencies=None, manifest_changes=None, file_changes=None, force_new=False):
        owner = owner or self.owner
        library = self.app.extensions['capability_library']
        path = Path(__file__).resolve().parents[1] / 'docs/capability-library/examples' / folder
        files = {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}
        if dependencies is not None or manifest_changes or file_changes:
            manifest = json.loads(files['capability.json'])
            if dependencies is not None:
                manifest['dependencies'] = dependencies
            manifest.update(manifest_changes or {})
            if file_changes:
                files.update(file_changes)
                manifest['files'] = file_index({key: value for key, value in files.items() if key not in {'capability.json', 'provenance.json'}})
            files['capability.json'] = json.dumps(manifest, ensure_ascii=False).encode()
        raw = zip_bytes(files)
        imported = library.import_package(owner, raw, 'component.zip', force_new=force_new)
        version = library.version(owner, imported['assetId'], imported['versionId'])
        if folder == 'decimal-total':
            job = verify_suite(library, owner, {**reference(version), 'cases': [
                {'input': {'values': values}, 'fileIds': [],
                 'expectation': {'mode': 'json_equals', 'value': {'total': total}}}
                for values, total in [(['0.10', '0.20'], '0.30'), (['2', '5'], '7')]]})
            end = time.monotonic() + 35
            while job['status'] in {'queued', 'running'} and time.monotonic() < end:
                time.sleep(.02)
                job = library.store.get(owner, 'job', job['jobId'])
            self.assertEqual(job['status'], 'completed', job)
            self.assertEqual(job['result']['status'], 'passed', job)
        else:
            # This suite isolates integration with the manual-review gate; the
            # capability library suite exercises its actual model/review flow.
            # This particular fixture represents an already reviewed legacy Skill.
            version.pop('validationPolicy', None)
            library.store.put(owner, 'version', version['versionId'], version)
            record = {'verificationId': 'fixture-review-' + version['versionId'], 'versionId': version['versionId'],
                'configurationId': version['configurationId'], 'status': 'passed',
                'contextDigest': library.context_digest(version)}
            library.store.put(owner, 'verification', record['verificationId'], record)
        asset = library.asset(owner, version['assetId'])
        library.update_asset(owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, f'"{asset["revision"]}"')
        return library, library.version(owner, version['assetId'], version['versionId'])

    def library_spec(self, version, skill=None):
        spec = base.fixture_spec()
        tool = version['manifest']['tool']
        spec['inputSchema'] = tool['inputSchema']
        spec['outputSchema'] = {'type': 'object', 'required': ['explanation'], 'properties': {'explanation': {'type': 'string'}}}
        spec['tools'] = [{'id': 'aggregate', 'capabilityRef': reference(version), 'inputSchema': tool['inputSchema'],
                          'outputSchema': tool['outputSchema'], 'permissions': {'network': False, 'files': []}}]
        spec['skills'] = []
        if skill:
            library = self.app.extensions['capability_library']
            spec['skills'] = [{'id': 'method', 'capabilityRef': reference(skill),
                'content': library.files(self.owner, skill)[skill['manifest']['skill']['entry']].decode(), 'requiredTools': ['aggregate']}]
        return spec

    def test_roundtrip_other_account_is_personal_and_does_not_execute(self):
        original, package = self.portable()
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'})
        response = other.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'import'})
        self.assertEqual(response.status_code, 201, response.json)
        self.assertNotEqual(response.json['spec']['id'], original['id'])
        self.assertEqual(response.json['validationScope'], 'structure_only')
        reader = other.get('/desktop/api/session').json['user']['id']
        modern = self.app.extensions['workbench_service'].asset_apps.detail(reader, response.json['assetId'])
        self.assertEqual(modern['versionId'], response.json['versionId'])
        self.assertEqual(modern['verification']['status'], 'unverified')
        self.assertIsNone(modern['enabledVersionId'])
        self.assertFalse(self.executor.calls)
        self.assertFalse(self.provider.calls)
        self.assertEqual(len(other.get('/desktop/api/agents').json['agents']), 1)
        self.assertEqual(len(self.platform.repository.list_agents(self.owner)), 1)

    def test_import_and_fork_idempotency_conflicts_and_unique_names(self):
        original, package = self.portable()
        body = {'package': package, 'idempotencyKey': 'import'}
        first = self.client.post('/desktop/api/agents/import', json=body)
        self.assertEqual(first.status_code, 201, first.json)
        self.assertEqual(first.json, self.client.post('/desktop/api/agents/import', json=body).json)
        self.assertEqual(self.client.post('/desktop/api/agents/import', json={**body, 'name': 'changed'}).status_code, 409)
        copied = self.client.post('/desktop/api/agents/fork', json={'agentRef': first.json['agentRef'], 'idempotencyKey': 'fork'})
        self.assertEqual(copied.status_code, 201)
        self.assertNotEqual(copied.json['spec']['id'], first.json['spec']['id'])
        self.assertNotEqual(copied.json['spec']['name'], first.json['spec']['name'])
        self.assertNotEqual(copied.json['assetId'], first.json['assetId'])
        self.assertEqual(self.app.extensions['workbench_service'].asset_apps.detail(
            self.owner, copied.json['assetId'])['verification']['status'], 'unverified')
        self.assertEqual(len(self.platform.repository.list_agents(self.owner)), 3)

    def test_private_definitions_cannot_be_exported_or_forked_by_other_account(self):
        spec, ref = self.saved()
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'})
        self.assertEqual(other.get(f'/desktop/api/agents/{spec["id"]}/versions/1/export').status_code, 404)
        self.assertEqual(other.post('/desktop/api/agents/fork', json={'agentRef': ref, 'idempotencyKey': 'fork'}).status_code, 404)

    def test_imported_application_runs_with_new_input(self):
        original, package = self.portable()
        imported = self.client.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'import'}).json
        run = self.new_run(mode='invoke', agentRef=imported['agentRef'])
        self.assertEqual(run['status'], 'succeeded')
        self.assertEqual(len(self.provider.calls), 1)  # explanation only
        self.assertEqual(self.executor.calls[0][1]['groupBy'], 'category')

    def test_adapt_saved_application_uses_baseline_but_new_identity(self):
        original, ref = self.saved()
        run = self.new_run(baseAgentRef=ref, goal='添加严格的数据口径说明')
        self.assertEqual(run['status'], 'awaiting_confirmation')
        self.assertNotEqual(run['spec']['id'], original['id'])
        payload = json.loads(self.provider.calls[0][1]['content'])
        self.assertEqual(payload['baselineSpec'], original)
        self.assertFalse(self.executor.calls)
        self.assertEqual(self.platform.agent(self.owner, ref), original)

    def test_secrets_and_private_paths_rejected_on_import_and_export(self):
        for text in ('api_key="sk-1234567890abcdefghij"', 'C:\\Users\\private\\data.csv', 'https://x.example/?token=private-auth-value'):
            spec = base.fixture_spec()
            spec['prompt'] = text
            original, ref = self.saved(spec)
            response = self.client.get(f'/desktop/api/agents/{original["id"]}/versions/1/export')
            self.assertEqual(response.status_code, 403, response.json)
        _, package = self.portable()
        package['definition']['tools'][0]['code'] += '\npassword = "private-value"\n'
        self.assertEqual(self.client.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'secret'}).status_code, 403)

    def test_malformed_package_and_dependency_list_rejected(self):
        _, package = self.portable()
        for changes in ({'kind': 'unknown'}, {'dependencies': [{'bad': True}]}, {'definition': {'tools': 'bad'}}):
            response = self.client.post('/desktop/api/agents/import', json={'package': {**package, **changes}, 'idempotencyKey': str(uuid4())})
            self.assertEqual(response.status_code, 400, response.json)

    def test_personal_tool_is_selected_by_planner_and_executes_real_wasi(self):
        library, version = self.component()
        self.provider.spec = self.library_spec(version)
        run = self.new_run(input={'values': ['2.50', '1.25']}, fileIds=[], capabilityRefs=[{'assetId': version['assetId'], 'versionId': version['versionId']}])
        self.assertEqual(run['status'], 'awaiting_confirmation')
        self.approve(run)
        done = self.wait(run['id'], {'succeeded'}, timeout=35)
        self.assertEqual(done['steps'][0]['output'], {'total': '3.75'})
        self.assertTrue(done['steps'][0]['invocationId'])
        self.assertFalse(self.executor.calls)  # library's WASI owns execution
        payload = json.loads(self.provider.calls[0][1]['content'])
        self.assertEqual(payload['availableComponents'][0]['capabilityRef'], reference(version))

    def test_disabled_or_reconfigured_dependencies_block_before_execution(self):
        library, version = self.component()
        self.provider.spec = self.library_spec(version)
        run = self.new_run(input={'values': ['1']}, fileIds=[])
        asset = library.asset(self.owner, version['assetId'])
        library.update_asset(self.owner, asset['assetId'], {'enabled': False}, f'"{asset["revision"]}"')
        self.approve(run)
        done = self.wait(run['id'], {'failed'})
        self.assertEqual(done['error']['code'], 'CAPABILITY_DISABLED')
        self.assertFalse(done['executionPerformed'])

    def test_portable_dependency_strips_local_binding_and_requires_owner_match(self):
        library, version = self.component()
        original, package = self.portable(self.library_spec(version))
        wire = json.dumps(package)
        for field in ('assetId', 'versionId', 'configurationId'):
            self.assertNotIn(field, wire)
        body = {'package': package, 'idempotencyKey': 'dependency-import'}
        imported = self.client.post('/desktop/api/agents/import', json=body)
        self.assertEqual(imported.status_code, 201, imported.json)
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'})
        response = other.post('/desktop/api/agents/import', json=body)
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'application_dependency_unresolved')

    def test_tool_and_skill_with_same_id_bind_independently_in_new_and_legacy_packages(self):
        library, tool = self.component()
        _, skill = self.component('summary-skill')
        spec = self.library_spec(tool, skill)
        spec['skills'][0]['id'] = 'aggregate'
        _, package = self.portable(spec)
        self.assertEqual([d['bindingId'] for d in package['dependencies']], ['tool:aggregate', 'skill:aggregate'])
        _, other_tool = self.component(force_new=True)
        _, other_skill = self.component('summary-skill', force_new=True)
        bindings = {key: {field: version[field] for field in ('assetId', 'versionId')}
                    for key, version in [('tool:aggregate', other_tool), ('skill:aggregate', skill)]}
        response = self.client.post('/desktop/api/agents/import', json={'package': package,
            'idempotencyKey': 'ambiguous-same-id', 'dependencyBindings': {'aggregate': bindings['tool:aggregate']}})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'application_dependency_ambiguous_binding')
        for legacy in (False, True):
            wire = copy.deepcopy(package)
            if legacy:
                for dependency in wire['dependencies']:
                    dependency.pop('bindingId')
            response = self.client.post('/desktop/api/agents/import', json={'package': wire,
                'idempotencyKey': 'scoped-' + str(legacy), 'dependencyBindings': bindings})
            self.assertEqual(response.status_code, 201, response.json)
            imported = response.json
            self.assertEqual(imported['spec']['tools'][0]['capabilityRef'], reference(other_tool))
            self.assertEqual(imported['spec']['skills'][0]['capabilityRef'], reference(skill))
            self.assertEqual(imported['spec']['tools'][0]['id'], imported['spec']['skills'][0]['id'])
            exported = self.platform.export_application(self.owner, imported['agentRef'])
            self.assertEqual(exported['dependencies'], package['dependencies'])
        malformed = copy.deepcopy(package)
        malformed['dependencies'][0]['bindingId'] = 'skill:aggregate'
        response = self.client.post('/desktop/api/agents/import', json={'package': malformed,
            'idempotencyKey': 'spoofed-slot', 'dependencyBindings': bindings})
        self.assertEqual(response.status_code, 400, response.json)
        self.assertEqual(response.json['code'], 'invalid_application_package')
        self.assertFalse(self.platform.repository.list_runs(self.owner))
        self.assertFalse(self.provider.calls)

    def test_legacy_pipeline_binding_and_duplicate_tool_slots_preserve_selected_copies(self):
        library, first = self.component()
        _, second = self.component(force_new=True)
        spec = self.library_spec(first)
        spec['tools'].append({**copy.deepcopy(spec['tools'][0]), 'id': 'second', 'capabilityRef': reference(second)})
        spec['pipeline'].insert(1, {'id': 'second_total', 'kind': 'tool', 'tool': 'second', 'inputFrom': 'run.input'})
        _, package = self.portable(spec)
        legacy = copy.deepcopy(package)
        for dependency in legacy['dependencies']:
            dependency.pop('bindingId')
        bindings = {key: {field: version[field] for field in ('assetId', 'versionId')}
                    for key, version in [('aggregate', second), ('second', first)]}
        imported = self.client.post('/desktop/api/agents/import', json={'package': legacy,
            'idempotencyKey': 'legacy-distinct', 'dependencyBindings': bindings})
        self.assertEqual(imported.status_code, 201, imported.json)
        self.assertEqual([t['capabilityRef']['assetId'] for t in imported.json['spec']['tools']], [second['assetId'], first['assetId']])
        body = {'package': package, 'idempotencyKey': 'double-key',
                'dependencyBindings': {**bindings, 'tool:aggregate': bindings['aggregate']}}
        self.assertEqual(self.client.post('/desktop/api/agents/import', json=body).status_code, 400)
        run = self.new_run(mode='invoke', agentRef=imported.json['agentRef'], input={'values': ['2.5', '1.25']}, fileIds=[])
        self.assertEqual(run['status'], 'succeeded', run)
        self.assertEqual([step['output'] for step in run['steps'][:2]], [{'total': '3.75'}, {'total': '3.75'}])
        invocations = [library.store.get(self.owner, 'invocation', step['invocationId']) for step in run['steps'][:2]]
        self.assertEqual([item['assetId'] for item in invocations], [second['assetId'], first['assetId']])

    def test_skill_snapshot_is_used_and_checked_again_before_explanation(self):
        library, version = self.component()
        _, skill = self.component('summary-skill')
        self.provider.spec = self.library_spec(version, skill)
        run = self.new_run(input={'values': ['1']}, fileIds=[])
        self.approve(run)
        done = self.wait(run['id'], {'succeeded'}, timeout=35)
        self.assertEqual(len(done['loadedSkills']), 1)
        self.assertIn(self.provider.spec['skills'][0]['content'], self.provider.calls[-1][0]['content'])
        resource = next(item for item in skill['manifest']['files'] if item['path'].endswith('references/method.md'))
        reference_text = library.files(self.owner, skill)[resource['path']].decode()
        planned = json.loads(self.provider.calls[0][1]['content'])
        planned_skill = next(item for item in planned['availableComponents'] if item['kind'] == 'skill')
        self.assertEqual(planned_skill['resources'][0]['content'], reference_text)
        explained = json.loads(self.provider.calls[-1][1]['content'])
        self.assertEqual(explained['skillResources'][0]['resources'][0]['content'], reference_text)
        self.assertEqual(explained['skillResources'][0]['capabilityRef'], reference(skill))
        self.assertEqual(done['loadedSkills'][0]['resources'], [resource])
        self.assertEqual(done['loadedSkills'][0]['capabilityRef'], reference(skill))
        invocation = library.store.get(self.owner, 'invocation', done['steps'][0]['invocationId'])
        self.assertEqual(invocation['sourceSkillRefs'], [{'assetId': skill['assetId'], 'versionId': skill['versionId']}])

    def test_real_tool_run_save_export_other_account_rebind_and_execute(self):
        library, version = self.component()
        dependencies = [{key: reference(version)[key] for key in ('sourceIdentity', 'contentDigest')}]
        dependencies[0].update(required=True, reason='精确求和')
        _, skill = self.component('summary-skill', dependencies=dependencies)
        self.provider.spec = self.library_spec(version, skill)
        run = self.new_run(input={'values': ['0.25', '0.75']}, fileIds=[])
        self.approve(run)
        self.wait(run['id'], {'succeeded'}, timeout=35)
        saved = self.client.post('/desktop/api/agents', json={'runId': run['id'], 'idempotencyKey': 'save'}).json
        package = self.platform.export_application(self.owner, saved['agentRef'])
        other = self.app.test_client()
        response = other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'})
        other_owner = response.json['user']['id']
        _, other_version = self.component(owner=other_owner)
        _, other_skill = self.component('summary-skill', owner=other_owner, dependencies=dependencies)
        response = other.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'import'})
        self.assertEqual(response.status_code, 201, response.json)
        imported = response.json
        self.assertEqual(imported['spec']['tools'][0]['capabilityRef'], reference(other_version))
        self.assertEqual(imported['spec']['skills'][0]['capabilityRef'], reference(other_skill))
        self.assertNotEqual(reference(other_version)['assetId'], version['assetId'])
        response = other.post('/desktop/api/agent-runs', json={'mode': 'invoke', 'agentRef': imported['agentRef'],
            'input': {'values': ['4.00', '2.50']}, 'fileIds': [], 'idempotencyKey': 'run'})
        self.assertEqual(response.status_code, 202, response.json)
        end = time.monotonic() + 35
        while time.monotonic() < end:
            done = self.platform.repository.get(other_owner, response.json['id'])
            if done['status'] in {'succeeded', 'failed', 'cancelled'}:
                break
            time.sleep(.02)
        self.assertEqual(done['status'], 'succeeded', done)
        self.assertEqual(done['steps'][0]['output'], {'total': '6.50'})
        self.assertEqual(done['loadedSkills'][0]['capabilityRef'], reference(other_skill))
        self.assertTrue(done['loadedSkills'][0]['resources'])
        explained = json.loads(self.provider.calls[-1][1]['content'])
        self.assertEqual(explained['skillResources'][0]['capabilityRef'], reference(other_skill))
        self.assertTrue(self.platform.repository.has_successful_definition(other_owner, imported['spec']))

    def test_configuration_change_cannot_reuse_saved_application_grant(self):
        library, version = self.component()
        spec, ref = self.saved(self.library_spec(version))
        library.runner_revision = 'different-runtime-revision'
        response = self.client.post('/desktop/api/agent-runs', json={'mode': 'invoke', 'agentRef': ref,
            'input': {'values': ['1']}, 'fileIds': [], 'idempotencyKey': 'stale'})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'STALE_CONFIGURATION')
        self.assertFalse(self.platform.repository.list_runs(self.owner))

    def test_skill_dependency_cannot_be_omitted_or_replaced_with_inline_code(self):
        library, version = self.component()
        dependency = {key: reference(version)[key] for key in ('sourceIdentity', 'contentDigest')}
        _, skill = self.component('summary-skill', dependencies=[{**dependency, 'required': True, 'reason': '精确求和工具'}])
        for replacement in ('unlinked', 'inline'):
            spec = self.library_spec(version, skill)
            if replacement == 'unlinked':
                spec['skills'][0]['requiredTools'] = []
            else:
                spec['tools'][0].pop('capabilityRef')
                spec['tools'][0]['code'] = 'def run(payload):\n    return {"total": "wrong"}'
            _, package = self.portable(spec)
            response = self.client.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': replacement})
            self.assertEqual(response.status_code, 409, response.json)
            self.assertEqual(response.json['code'], 'application_dependency_changed')
        self.assertFalse(self.platform.repository.list_runs(self.owner))

    def test_skill_dependency_catalog_exposes_exact_lock_and_valid_pipeline_runs(self):
        library, version = self.component()
        dependency = {key: reference(version)[key] for key in ('sourceIdentity', 'contentDigest')}
        _, skill = self.component('summary-skill', dependencies=[{**dependency, 'required': True, 'reason': '精确求和工具'}])
        self.provider.spec = self.library_spec(version, skill)
        run = self.new_run(input={'values': ['2.40', '0.60']}, fileIds=[],
                           capabilityRefs=[{'assetId': skill['assetId'], 'versionId': skill['versionId']}])
        payload = json.loads(self.provider.calls[0][1]['content'])
        selected = next(c for c in payload['availableComponents'] if c['kind'] == 'skill')
        self.assertEqual(selected['dependencies'], [reference(version)])
        self.approve(run)
        done = self.wait(run['id'], {'succeeded'}, timeout=35)
        self.assertEqual(done['steps'][0]['output'], {'total': '3.00'})

    def test_adapt_and_revision_save_preserve_source_metadata(self):
        original, ref = self.saved()
        adapted = self.new_run(baseAgentRef=ref, goal='补充严格口径说明')
        self.approve(adapted)
        done = self.wait(adapted['id'], {'succeeded'})
        saved = self.client.post('/desktop/api/agents', json={'runId': done['id'], 'idempotencyKey': 'save-adapt'}).json
        metadata = self.platform.repository.application_metadata(self.owner)[saved['agentRef']]
        self.assertEqual(metadata['source'], {'kind': 'adapt', 'sourceAgentRef': ref})
        self.assertEqual(metadata['validationScope'], 'this_input_only')
        revised = self.new_run(revisionOf=done['id'], goal='再次补充口径')
        self.approve(revised)
        self.wait(revised['id'], {'succeeded'})
        saved_revision = self.client.post('/desktop/api/agents', json={'runId': revised['id'], 'idempotencyKey': 'save-revise'}).json
        metadata = self.platform.repository.application_metadata(self.owner)[saved_revision['agentRef']]
        self.assertEqual(metadata['source'], {'kind': 'revision', 'sourceRunId': done['id'], 'sourceAgentRef': saved['agentRef']})

    def test_skill_disabled_after_tool_execution_blocks_explanation(self):
        library, version = self.component()
        _, skill = self.component('summary-skill')
        self.provider.spec = self.library_spec(version, skill)
        run = self.new_run(input={'values': ['1']}, fileIds=[])
        execute = self.platform._execute_library_tool
        def disable_after_tool(*args, **kwargs):
            result = execute(*args, **kwargs)
            asset = library.asset(self.owner, skill['assetId'])
            library.update_asset(self.owner, asset['assetId'], {'enabled': False}, f'"{asset["revision"]}"')
            return result
        with patch.object(self.platform, '_execute_library_tool', side_effect=disable_after_tool):
            self.approve(run)
            done = self.wait(run['id'], {'failed'}, timeout=35)
        self.assertEqual(done['error']['code'], 'CAPABILITY_DISABLED')
        self.assertEqual(done['steps'][0]['status'], 'succeeded')
        self.assertEqual(len(self.provider.calls), 1)

    def test_dependency_duplicates_require_explicit_binding_and_reject_foreign_or_malformed_ids(self):
        library, version = self.component()
        _, package = self.portable(self.library_spec(version))
        _, duplicate = self.component(force_new=True)
        body = {'package': package, 'idempotencyKey': 'ambiguous'}
        response = self.client.post('/desktop/api/agents/import', json=body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json['code'], 'application_dependency_unresolved')
        binding = {key: duplicate[key] for key in ('assetId', 'versionId')}
        response = self.client.post('/desktop/api/agents/import', json={**body, 'dependencyBindings': {'aggregate': binding}})
        self.assertEqual(response.status_code, 201, response.json)
        self.assertEqual(response.json['spec']['tools'][0]['capabilityRef'], reference(duplicate))
        for invalid in ({**binding, 'assetId': []}, {**binding, 'versionId': None}):
            response = self.client.post('/desktop/api/agents/import', json={**body, 'idempotencyKey': str(uuid4()), 'dependencyBindings': {'aggregate': invalid}})
            self.assertEqual(response.status_code, 400, response.json)
        other = self.app.test_client()
        other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'})
        response = other.post('/desktop/api/agents/import', json={**body, 'dependencyBindings': {'aggregate': binding}})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.platform.repository.list_runs(self.owner))

    def test_saved_application_keeps_exact_version_after_active_version_changes(self):
        library, version = self.component()
        original, ref = self.saved(self.library_spec(version))
        _, replacement = self.component(manifest_changes={'description': '第二版工具说明'})
        self.assertNotEqual(replacement['versionId'], version['versionId'])
        self.assertEqual(library.asset(self.owner, version['assetId'])['activeVersionId'], replacement['versionId'])
        done = self.new_run(mode='invoke', agentRef=ref, input={'values': ['1.10', '2.20']}, fileIds=[])
        invocation = library.store.get(self.owner, 'invocation', done['steps'][0]['invocationId'])
        self.assertEqual(invocation['versionId'], version['versionId'])
        self.assertEqual(done['steps'][0]['output'], {'total': '3.30'})
        changed = copy.deepcopy(original)
        changed['tools'][0]['capabilityRef']['versionId'] = replacement['versionId']
        _, changed_ref = self.saved(changed)
        response = self.client.post('/desktop/api/agent-runs', json={'mode': 'invoke', 'agentRef': changed_ref,
            'input': {'values': ['1']}, 'fileIds': [], 'idempotencyKey': 'changed-version'})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'application_dependency_changed')

    def test_external_starter_export_other_account_import_and_real_wasi_reuse(self):
        from backend.app.modules.agent_platform.executor import SandboxExecutor
        self.platform.executor = SandboxExecutor(self.app.config)
        samples = Path(__file__).resolve().parents[1] / 'docs/vision-delivery/examples'
        package = json.loads((samples / 'csv-summary.application.json').read_text(encoding='utf-8'))
        imported = self.client.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'external'}).json
        self.assertFalse(self.platform.repository.list_runs(self.owner))
        first_file = self.upload((samples / 'sample-a.csv').read_bytes())
        first = self.new_run(mode='invoke', agentRef=imported['agentRef'], fileIds=[first_file['id']],
            input={'fileId': first_file['id'], 'groupBy': 'category', 'valueColumn': 'amount', 'aggregation': 'sum'})
        self.assertEqual(first['steps'][0]['output']['total'], '22')
        self.client.post('/desktop/api/agents', json={'runId': first['id'], 'idempotencyKey': 'save-external'})
        metadata = self.platform.repository.application_metadata(self.owner)[imported['agentRef']]
        self.assertEqual(metadata['source']['kind'], 'import')
        self.assertEqual(metadata['validationScope'], 'this_input_only')
        exported = self.platform.export_application(self.owner, imported['agentRef'])
        self.assertNotIn(first_file['id'], json.dumps(exported))
        other = self.app.test_client()
        owner = other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'}).json['user']['id']
        copied = other.post('/desktop/api/agents/import', json={'package': exported, 'idempotencyKey': 'external'}).json
        self.assertFalse(self.platform.repository.list_runs(owner))
        second_file = other.post('/desktop/api/files', data={'file': (io.BytesIO((samples / 'sample-b.csv').read_bytes()), 'sample.csv')}, content_type='multipart/form-data').json
        second = self.platform.create(owner, {'mode': 'invoke', 'agentRef': copied['agentRef'], 'idempotencyKey': 'run-external',
            'fileIds': [second_file['id']], 'input': {'fileId': second_file['id'], 'groupBy': 'category', 'valueColumn': 'amount', 'aggregation': 'sum'}})
        deadline = time.monotonic() + 35
        while second['status'] not in {'succeeded', 'failed', 'cancelled'} and time.monotonic() < deadline:
            time.sleep(.025)
            second = self.platform.repository.get(owner, second['id'])
        self.assertEqual(second['status'], 'succeeded', second)
        self.assertEqual(second['steps'][0]['output']['total'], '11')
        self.assertNotEqual(copied['agentRef'], imported['agentRef'])
        artifact = second['artifacts'][0]
        response = other.get('/desktop/api/agent-artifacts/' + artifact['id'])
        self.assertIn(b'C,10', response.data)
        response.close()

    def test_publication_evidence_search_is_not_limited_to_latest_runs(self):
        spec, ref = self.saved()
        for index in range(102):
            doc = {'id': str(index), 'taskId': '', 'spec': spec if index == 0 else None,
                   'status': 'succeeded', 'artifacts': []}
            self.platform.repository.create(self.owner, {'number': index}, doc, str(index))
        self.assertEqual(len(self.platform.repository.list_runs(self.owner)), 100)
        self.assertTrue(self.platform.repository.has_successful_definition(self.owner, spec))

    def test_script_skills_are_explicitly_unavailable_for_fixed_applications(self):
        library, version = self.component()
        _, skill = self.component('summary-skill',
            manifest_changes={'skill': {'entry': 'original/SKILL.md', 'scripts': [
                {'type': 'python_wasi', 'path': 'original/scripts/method.py', 'callable': 'run'}]}},
            file_changes={'original/scripts/method.py': b'def run(payload):\n    return payload\n'})
        item = next(c for c in self.platform.components(self.owner) if c['assetId'] == skill['assetId'])
        self.assertFalse(item['available'])
        self.assertIn('脚本', item['reason'])
        _, package = self.portable(self.library_spec(version, skill))
        response = self.client.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'script'})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'application_skill_unsupported')
        self.assertFalse(self.platform.repository.list_runs(self.owner))

    def test_skill_reference_limits_and_encoding_are_not_silently_truncated(self):
        cases = [{'original/references/method.md': data} for data in (b'X' * 12001, b'\xff\xfe\x00\x01')]
        cases.append({f'original/references/part-{number}.md': b'content' for number in range(16)})
        cases.append({f'original/references/part-{number}.md': b'X' * 12000 for number in range(3)})
        for changes in cases:
            library, skill = self.component('summary-skill', file_changes=changes)
            item = next(c for c in self.platform.components(self.owner) if c['assetId'] == skill['assetId'])
            self.assertFalse(item['available'])
            self.assertTrue(item['reason'])
            response = self.client.post('/desktop/api/agent-runs', json={'mode': 'create', 'goal': '组合方法',
                'input': {}, 'fileIds': [], 'capabilityRefs': [{'assetId': skill['assetId'], 'versionId': skill['versionId']}],
                'idempotencyKey': str(uuid4())})
            self.assertEqual(response.status_code, 202)
            done = self.wait(response.json['id'], {'failed'})
            self.assertEqual(done['error']['code'], 'application_dependency_unavailable')
        self.assertFalse(self.provider.calls)

    def test_combined_skill_reference_context_budget_is_enforced_before_invocation(self):
        library, version = self.component()
        resources = {f'original/references/part-{number}.md': b'X' * 10000 for number in range(3)}
        _, first = self.component('summary-skill', file_changes=resources)
        _, second = self.component('summary-skill', file_changes=resources, force_new=True)
        spec = self.library_spec(version, first)
        spec['skills'].append({**spec['skills'][0], 'id': 'other-method', 'capabilityRef': reference(second)})
        _, ref = self.saved(spec)
        response = self.client.post('/desktop/api/agent-runs', json={'mode': 'invoke', 'agentRef': ref,
            'input': {'values': ['1']}, 'fileIds': [], 'idempotencyKey': 'combined-context'})
        self.assertEqual(response.status_code, 409, response.json)
        self.assertEqual(response.json['code'], 'application_skill_unsupported')
        self.assertFalse(self.platform.repository.list_runs(self.owner))
        self.assertFalse(self.provider.calls)

    def test_trusted_publication_source_is_preserved_through_adaptation(self):
        _, package = self.portable()
        other = self.app.test_client()
        owner = other.post('/desktop/api/register', json={'email': 'reader@example.test', 'password': 'long-password'}).json['user']['id']
        source = {'kind': 'publication', 'id': 'published-snapshot', 'digest': 'a' * 64}
        copied = self.platform.import_application(owner, {'package': package, 'idempotencyKey': 'copy'}, source=source)
        metadata = self.platform.repository.application_metadata(owner)[copied['agentRef']]
        for key, value in source.items():
            self.assertEqual(metadata['source'][key], value)
        self.assertFalse(self.platform.repository.list_runs(owner))
        # Caller-supplied metadata remains an ordinary import.
        forged = other.post('/desktop/api/agents/import', json={'package': package, 'idempotencyKey': 'forged', 'source': source})
        self.assertEqual(forged.status_code, 201)
        self.assertEqual(forged.json['source']['kind'], 'import')
        with self.assertRaisesRegex(Exception, '同一请求标识'):
            self.platform.import_application(owner, {'package': package, 'idempotencyKey': 'copy'}, source={**source, 'id': 'different'})
        previous_client, previous_owner = self.client, self.owner
        self.client, self.owner = other, owner
        self.file = self.upload(b'category,amount\nA,2\n')
        try:
            run = self.new_run(baseAgentRef=copied['agentRef'])
            self.approve(run)
            self.wait(run['id'], {'succeeded'})
            saved = other.post('/desktop/api/agents', json={'runId': run['id'], 'idempotencyKey': 'save-adapt'}).json
            adapted_source = self.platform.repository.application_metadata(owner)[saved['agentRef']]['source']
            self.assertEqual(adapted_source, {'kind': 'adapt', 'sourceAgentRef': copied['agentRef']})
            self.assertEqual(self.platform.repository.application_metadata(owner)[adapted_source['sourceAgentRef']]['source']['id'], source['id'])
        finally:
            self.client, self.owner = previous_client, previous_owner

    def test_reference_version_is_pinned_and_changed_bytes_block_explanation(self):
        library, version = self.component()
        path = 'original/references/method.md'
        _, skill = self.component('summary-skill', file_changes={path: b'Original method: preserve units.'})
        self.provider.spec = self.library_spec(version, skill)
        run = self.new_run(input={'values': ['1']}, fileIds=[])
        _, newer = self.component('summary-skill', file_changes={path: b'New method: round all units.'})
        self.approve(run)
        done = self.wait(run['id'], {'succeeded'}, timeout=35)
        explained = json.loads(self.provider.calls[-1][1]['content'])
        self.assertEqual(explained['skillResources'][0]['resources'][0]['content'], 'Original method: preserve units.')
        self.assertEqual(done['loadedSkills'][0]['capabilityRef']['versionId'], skill['versionId'])
        self.assertNotEqual(skill['versionId'], newer['versionId'])
        # Corruption after the tool has run cannot reach the explanation model.
        another = self.new_run(input={'values': ['2']}, fileIds=[],
            capabilityRefs=[{'assetId': skill['assetId'], 'versionId': skill['versionId']},
                            {'assetId': version['assetId'], 'versionId': version['versionId']}])
        read_files, execute = library.files, self.platform._execute_library_tool
        changed = {'value': False}
        def corrupt(owner, current):
            files = read_files(owner, current)
            if changed['value'] and current['versionId'] == skill['versionId']:
                files[path] = b'Unapproved method text.'
            return files
        def execute_then_corrupt(*args, **kwargs):
            result = execute(*args, **kwargs)
            changed['value'] = True
            return result
        calls_before = len(self.provider.calls)
        with patch.object(library, 'files', side_effect=corrupt), patch.object(self.platform, '_execute_library_tool', side_effect=execute_then_corrupt):
            self.approve(another)
            failed = self.wait(another['id'], {'failed'}, timeout=35)
        self.assertEqual(failed['error']['code'], 'application_dependency_changed')
        self.assertEqual(failed['steps'][0]['status'], 'succeeded')
        self.assertEqual(len(self.provider.calls), calls_before)


if __name__ == '__main__':
    unittest.main()
