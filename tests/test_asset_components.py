"""Isolated standard Skill, immutable component, and fixed Tool execution tests."""
import base64
import copy
import io
import json
import tempfile
import threading
import unittest
import zipfile
import time
from pathlib import Path
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.capability_library.components import (
    build_snapshot, snapshot_from_package, validate_snapshot, runtime_digest,
    invoke_snapshot, skill_material, export_snapshot, bind_snapshot,
    install_snapshot, tool_draft_from_nodes,
)
from backend.app.modules.capability_library.contracts import CapabilityError, canonical, digest
from backend.app.modules.capability_library.packages import file_index, zip_bytes, parse_package
from backend.app.modules.workbench.errors import GatewayError

ROOT = Path(__file__).resolve().parents[1]


def manifest(kind='tool'):
    value = {'format': 'cd80.capability/1', 'kind': kind, 'name': 'test-component', 'description': 'Isolated component',
             'source': {'identity': 'test/components', 'format': 'platform'}, 'files': [], 'dependencies': [],
             'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False}}
    if kind == 'tool':
        value['tool'] = {'inputSchema': {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n'],
                                       'additionalProperties': False},
                         'outputSchema': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                                          'required': ['value'], 'additionalProperties': False},
                         'execution': {'type': 'python_wasi', 'path': 'original/tool.py', 'callable': 'run'}}
    else:
        value['skill'] = {'entry': 'original/SKILL.md'}
    return value


class Provider:
    def __init__(self, result):
        self.result, self.calls = result, []

    def events(self, messages, model, stopped, on_response, tools=None):
        self.calls.append({'messages': messages, 'model': model, 'tools': tools})
        yield {'type': 'token', 'text': canonical(self.result)}
        yield {'type': 'turn_end', 'tool_calls': []}


class FrozenToolSkillModel:
    def call(self, stage, payload, stopped, on_response):
        if stage == 'approve':
            return {'decision': 'approve', 'reason': 'Only the frozen tool and current input.'}
        if stage == 'review':
            return {'decision': 'accept', 'answer': 'Checked actual output.'}
        complete = [s for s in payload['steps'] if s['status'] == 'succeeded']
        if complete:
            return {'type': 'finish', 'answer': 'Done', 'resultStepId': complete[-1]['id'], 'artifactIds': []}
        initial = next(o for o in payload['observations'] if 'inputs' in o)
        return {'type': 'tool', 'name': 'tool_1', 'arguments': {'n': initial['inputs']['n']}, 'purpose': 'Use frozen implementation.'}


class AssetComponentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-components-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'GITEE_AI_API_KEY': 'test-only',
                              'CAPABILITY_SKIP_PUBLIC_SEED': True, 'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime')})
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'components@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.w = self.app.extensions['workbench_service']
        self.lib = self.app.extensions['capability_library']

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def imported(self, value=None, files=None):
        value = value or manifest()
        files = files or {'original/tool.py': b'def run(payload): return {"value": payload["n"] * 2}\n'}
        value['files'] = file_index(files)
        result = self.lib.import_package(self.owner, zip_bytes({**files, 'capability.json': canonical(value).encode()}), 'component.zip')
        return result, self.lib.version(self.owner, result['assetId'], result['versionId'])

    def wait_job(self, job):
        for _ in range(200):
            value = self.lib.store.get(self.owner, 'job', job['jobId'])
            if value['status'] in {'completed', 'failed', 'cancelled', 'interrupted'}:
                return value
            time.sleep(.05)
        self.fail('isolated job timed out')

    def test_extracted_fixed_tool_owns_dependencies_and_resources(self):
        imported, _ = self.imported()
        snap = build_snapshot(self.lib, self.owner, imported['assetId'], imported['versionId'])
        nodes = [{'id': 'call', 'kind': 'tool', 'component': snap,
                  'inputBindings': {'n': {'from': 'input', 'path': 'n'}},
                  'delivery': {'schema': manifest()['tool']['outputSchema']}},
                 {'id': 'adjust', 'kind': 'code', 'code': 'def run(payload): return {"value": payload["n"] + int(open("offset.txt").read())}\n',
                  'files': [{'path': 'offset.txt', 'base64': base64.b64encode(b'3').decode()}],
                  'inputBindings': {'n': {'from': 'step', 'stepId': 'call', 'path': 'value'}},
                  'delivery': {'schema': manifest()['tool']['outputSchema']}}]
        definition = {'name': 'extracted', 'description': 'Fixed component closure',
                      'inputSchema': manifest()['tool']['inputSchema'], 'nodes': nodes}
        with self.assertRaises(CapabilityError) as gap:
            tool_draft_from_nodes(definition, nodes[1:])
        self.assertEqual(gap.exception.code, 'SOURCE_NEEDS_EDIT')
        content = tool_draft_from_nodes(definition, nodes)
        draft = self.lib.draft(self.owner, content)
        saved = self.lib.save_draft(self.owner, draft['draftId'], {'draftRevision': draft['revision']})
        version = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(version['readiness'], 'unverified')
        self.assertEqual(version['validationPolicy'], 'original_and_new')
        source = self.lib.asset(self.owner, imported['assetId'])
        source['deletedAt'] = '2026-10-14'
        self.lib.store.put(self.owner, 'asset', source['assetId'], source)
        call = {**saved, 'configurationId': version['configurationId'], 'input': {'n': 4}, 'fileIds': []}
        self.assertEqual(self.lib.invoke(self.owner, call, trial=True)['result']['content'], {'value': 11})
        package = self.lib.export(self.owner, {**saved, 'format': 'platform'})
        imported_again = self.lib.import_package(self.owner, self.lib.store.blob(self.owner, package['id']), 'component.json')
        second = self.lib.version(self.owner, imported_again['assetId'], imported_again['versionId'])
        self.assertEqual(len(second['embeddedSnapshot']['dependencies']), 1)
        self.assertFalse(self.lib.asset(self.owner, second['assetId'])['enabled'])
        self.assertEqual(self.lib.invoke(self.owner, {**imported_again, 'configurationId': second['configurationId'],
            'input': {'n': 5}, 'fileIds': []}, trial=True)['result']['content'], {'value': 13})

    def test_extracted_file_and_artifact_bindings_use_actual_current_bytes(self):
        nodes = [{'id': 'load', 'kind': 'code', 'code': 'def run(payload):\n    value = int(open(payload["files"]["csv"]).read())\n    open("/output/value.txt", "w").write(str(value * 2))\n    return {"value": value}\n',
                  'inputBindings': {'csv': {'from': 'file', 'input': 'csv'}}, 'delivery': {'schema': {}}},
                 {'id': 'consume', 'kind': 'code', 'code': 'def run(payload): return {"value": int(open(payload["files"]["result"]).read())}\n',
                  'inputBindings': {'result': {'from': 'artifact', 'stepId': 'load', 'name': 'value.txt'}},
                  'delivery': {'schema': manifest()['tool']['outputSchema']}}]
        draft = tool_draft_from_nodes({'name': 'files', 'nodes': nodes, 'inputSchema': {'type': 'object'}}, nodes)
        files = {v['path']: v['text'].encode() for v in draft['files']}
        snapshot = snapshot_from_package(draft['manifest'], files)
        output = invoke_snapshot(self.lib, self.owner, snapshot, {'file_csv': '/input/current.txt'}, files={'current.txt': b'12'})
        self.assertEqual(output['result']['content'], {'value': 24})
        with self.assertRaises(CapabilityError):
            invoke_snapshot(self.lib, self.owner, snapshot, {'file_csv': '/input/missing.txt'}, files={'current.txt': b'12'})

    def test_fixed_delivery_gate_blocks_later_steps_and_keeps_failure_trace(self):
        node = {'id': 'source', 'kind': 'code', 'code': 'def run(payload): return {"value": 4}\n',
                'inputBindings': {}, 'delivery': {'schema': {}, 'files': [{'name': 'required.csv'}], 'evidenceFields': ['value']}}
        later = {'id': 'later', 'kind': 'code', 'code': 'def run(payload): return {"value": 99}\n',
                 'inputBindings': {}, 'delivery': {'schema': {}}}
        content = tool_draft_from_nodes({'name': 'delivery', 'nodes': [node, later]}, [node, later])
        snapshot = snapshot_from_package(content['manifest'], {v['path']: v['text'].encode() for v in content['files']})
        with self.assertRaises(CapabilityError) as error:
            invoke_snapshot(self.lib, self.owner, snapshot, {}, context={'parentRunId': 'isolated', 'nodeId': 'delivery'})
        self.assertEqual(error.exception.code, 'DELIVERY_INVALID')
        trace = self.lib.store.get(self.owner, 'invocation', error.exception.invocationId)
        self.assertEqual(len(trace['fixedSteps']), 1)
        self.assertEqual(trace['componentContext']['nodeId'], 'delivery')
        self.assertEqual(trace['status'], 'failed')

    def test_fixed_delivery_rejects_empty_and_malformed_actual_files(self):
        for raw in (b'', b'not-json'):
            with self.subTest(raw=raw):
                node = {'id': 'source', 'kind': 'code', 'code': 'def run(payload):\n    open("/output/report.json", "wb").write(' + repr(raw) + ')\n    return {"value": 4}\n',
                        'inputBindings': {}, 'delivery': {'files': ['report.json']}}
                later = {'id': 'later', 'kind': 'code', 'code': 'def run(payload): return {}\n', 'inputBindings': {}}
                content = tool_draft_from_nodes({'name': 'actual files', 'nodes': [node, later]}, [node, later])
                snapshot = snapshot_from_package(content['manifest'], {v['path']: v['text'].encode() for v in content['files']})
                with self.assertRaises(CapabilityError) as error:
                    invoke_snapshot(self.lib, self.owner, snapshot, {})
                self.assertEqual(error.exception.code, 'DELIVERY_INVALID')
                trace = self.lib.store.get(self.owner, 'invocation', error.exception.invocationId)
                self.assertEqual(len(trace['fixedSteps']), 1)

    def test_current_recipient_connection_and_permission_revocation(self):
        connection = self.lib.create_connection(self.owner, {'name': 'test-current', 'sourceIdentity': 'test/current-mcp',
            'transport': 'streamable_http', 'endpoint': 'http://127.0.0.1:19999/mcp', 'authMode': 'none'})
        value = manifest()
        value['permissions']['network'] = 'connector_only'
        schema_hash = 'a' * 64
        value['tool']['execution'] = {'type': 'mcp_http', 'connectionIdentity': connection['sourceIdentity'],
            'toolName': 'read', 'toolSchemaDigest': schema_hash}
        snapshot = snapshot_from_package(value, {})
        self.lib.policy[connection['endpoint']] = {'maintainer': 'test', 'evidence': 'isolated', 'tools': {'read': schema_hash}}
        first = runtime_digest(self.lib, self.owner, snapshot)
        connection['credentialRevision'] += 1
        self.lib.store.put(self.owner, 'connection', connection['connectionId'], connection)
        self.assertNotEqual(first, runtime_digest(self.lib, self.owner, snapshot))
        self.lib.policy.clear()
        with self.assertRaises(CapabilityError):
            invoke_snapshot(self.lib, self.owner, snapshot, {'n': 1})
        # A foreign owner's connection ID is never accepted as a binding.
        with self.assertRaises(CapabilityError):
            bind_snapshot(self.lib, self.owner, snapshot, {'test/current-mcp': 'other-owner-connection'})

    def test_two_real_cases_gate_enablement_and_new_input(self):
        from backend.app.modules.capability_library.asset_validation import verify_suite
        saved = install_snapshot(self.lib, self.owner, snapshot_from_package(manifest(),
            {'original/tool.py': b'def run(payload): return {"value": payload["n"] * 2}\n'}))
        version = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        request = {**saved, 'configurationId': version['configurationId']}
        original = {'label': 'original', 'input': {'n': 3}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'value': 6}}}
        with self.assertRaises(CapabilityError):
            verify_suite(self.lib, self.owner, {**request, 'cases': [original, {**original, 'label': 'renamed'}]})
        with self.assertRaises(CapabilityError):
            self.lib.verify(self.owner, {**request, **original})
        next_case = {'label': 'new', 'input': {'n': 5}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'value': 10}}}
        job = self.wait_job(verify_suite(self.lib, self.owner, {**request, 'cases': [original, next_case]}))
        self.assertEqual(job['status'], 'completed', job)
        evidence = self.lib.store.get(self.owner, 'verification', job['verificationId'])
        self.assertEqual(evidence['status'], 'passed')
        self.assertTrue(all(c.get('invocationId') for c in evidence['cases']))
        asset = self.lib.asset(self.owner, saved['assetId'])
        self.lib.update_asset(self.owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, '"' + str(asset['revision']) + '"')
        self.assertEqual(self.lib.invoke(self.owner, {**request, 'input': {'n': 8}, 'fileIds': []})['result']['content'], {'value': 16})
        self.lib.runner_revision = 'changed_after_verification'
        with self.assertRaises(CapabilityError):
            self.lib.invoke(self.owner, {**request, 'input': {'n': 8}, 'fileIds': []})

    def test_failed_new_case_cannot_be_manually_promoted(self):
        from backend.app.modules.capability_library.asset_validation import verify_suite
        saved = install_snapshot(self.lib, self.owner, snapshot_from_package(manifest(),
            {'original/tool.py': b'def run(payload): return {"value": 6}\n'}))
        version = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        request = {**saved, 'configurationId': version['configurationId'], 'cases': [
            {'input': {'n': 3}, 'expectation': {'mode': 'json_equals', 'value': {'value': 6}}},
            {'input': {'n': 5}, 'expectation': {'mode': 'json_equals', 'value': {'value': 10}}}]}
        job = self.wait_job(verify_suite(self.lib, self.owner, request))
        record = self.lib.store.get(self.owner, 'verification', job['verificationId'])
        self.assertEqual([c['status'] for c in record['cases']], ['passed', 'failed'])
        self.assertEqual(record['status'], 'failed')
        self.assertIsNone(self.lib.evidence(self.owner, version))
        with self.assertRaises(CapabilityError):
            self.lib.review(self.owner, record['verificationId'], {'decision': 'accept'})

    def test_owned_closure_publishes_and_copies_after_source_removed(self):
        from backend.app.modules.capability_library.asset_validation import verify_suite
        from backend.app.modules.capability_library.community import preview, publish, copy_release
        imported, _ = self.imported()
        dependency = build_snapshot(self.lib, self.owner, imported['assetId'], imported['versionId'])
        value = manifest()
        value['source']['identity'] = 'test/composite'
        value['dependencies'] = [{'sourceIdentity': 'test/components', 'contentDigest': dependency['contentDigest'],
                                  'required': True, 'reason': 'Fixed tool dependency'}]
        value['tool']['execution'] = {'type': 'fixed_pipeline', 'protocolVersion': '1', 'steps': [
            {'id': 'read', 'kind': 'tool', 'sourceIdentity': 'test/components', 'contentDigest': dependency['contentDigest'],
             'outputSchema': value['tool']['outputSchema']}]}
        saved = install_snapshot(self.lib, self.owner, snapshot_from_package(value, {}, [dependency]))
        original = self.lib.asset(self.owner, imported['assetId'])
        original['deletedAt'] = '2026-10-14'
        self.lib.store.put(self.owner, 'asset', original['assetId'], original)
        version = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        job = self.wait_job(verify_suite(self.lib, self.owner, {**saved, 'configurationId': version['configurationId'], 'cases': [
            {'input': {'n': 1}, 'expectation': {'mode': 'json_equals', 'value': {'value': 2}}},
            {'input': {'n': 9}, 'expectation': {'mode': 'json_equals', 'value': {'value': 18}}}]}))
        self.assertEqual(job['status'], 'completed', job)
        inspection = preview(self.lib, self.owner, saved)
        self.assertEqual(len(inspection['dependencies']), 1)
        released = publish(self.lib, self.owner, {**saved, 'snapshotDigest': inspection['snapshotDigest'], 'confirmSharing': True})
        recipient = self.app.test_client()
        recipient.post('/desktop/api/register', json={'email': 'recipient@example.invalid', 'password': 'test-password'})
        other = recipient.get('/desktop/api/session').json['user']['id']
        copied = copy_release(self.lib, other, released['releaseId'], {})
        received = self.lib.version(other, copied['assetId'], copied['versionId'])
        self.assertEqual(received['validationPolicy'], 'original_and_new')
        self.assertEqual(len(received['embeddedSnapshot']['dependencies']), 1)
        self.assertIsNone(self.lib.evidence(other, received))
        self.assertFalse(self.lib.asset(other, copied['assetId'])['enabled'])
        result = self.lib.invoke(other, {**copied, 'configurationId': received['configurationId'], 'input': {'n': 12}, 'fileIds': []}, trial=True)
        self.assertEqual(result['result']['content'], {'value': 24})

    def test_new_import_policy_keeps_existing_historical_version_unchanged(self):
        imported, version = self.imported()
        self.assertEqual(version['validationPolicy'], 'original_and_new')
        with self.assertRaises(CapabilityError) as error:
            self.lib.verify(self.owner, {**imported, 'configurationId': version['configurationId'], 'input': {'n': 2},
                'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'value': 4}}})
        self.assertEqual(error.exception.code, 'VALIDATION_SUITE_REQUIRED')
        # Explicit migration-before-cutover fixture, not a new-import bypass.
        version.pop('validationPolicy')
        self.lib.store.put(self.owner, 'version', version['versionId'], version)
        duplicate, existing = self.imported()
        self.assertEqual(duplicate['versionId'], version['versionId'])
        self.assertNotIn('validationPolicy', existing)
        historical = self.wait_job(self.lib.verify(self.owner, {**imported, 'configurationId': version['configurationId'],
            'input': {'n': 2}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'value': 4}}}))
        self.assertEqual(self.lib.store.get(self.owner, 'verification', historical['verificationId'])['status'], 'passed')

    def test_self_contained_skill_validation_catalog_and_main_chat_use_frozen_tool(self):
        from backend.app.modules.capability_library.asset_validation import verify_suite
        from backend.app.modules.capability_library.chat_bridge import ChatCapabilities
        from backend.app.modules.agent_platform.library_capabilities import catalog
        imported, _ = self.imported()
        dependency = build_snapshot(self.lib, self.owner, imported['assetId'], imported['versionId'])
        value = manifest('skill')
        value['source']['identity'] = 'test/self-contained-method'
        value['dependencies'] = [{'sourceIdentity': 'test/components', 'contentDigest': dependency['contentDigest'],
                                  'required': True, 'reason': 'Frozen required implementation'}]
        saved = install_snapshot(self.lib, self.owner, snapshot_from_package(value,
            {'original/SKILL.md': b'---\nname: self-contained-method\ndescription: Use the frozen tool to double n.\n---\nCall the bound tool and deliver its actual result.\n'}, [dependency]))
        source = self.lib.asset(self.owner, imported['assetId'])
        source['deletedAt'] = '2026-10-14'
        self.lib.store.put(self.owner, 'asset', source['assetId'], source)
        version = self.lib.version(self.owner, saved['assetId'], saved['versionId'])
        self.w.sub_agents.model = FrozenToolSkillModel()
        job = self.wait_job(verify_suite(self.lib, self.owner, {**saved, 'configurationId': version['configurationId'], 'cases': [
            {'input': {'n': 2}, 'expectation': {'mode': 'manual_review', 'prompt': 'Double the input.', 'criteria': 'value equals 4'}},
            {'input': {'n': 7}, 'expectation': {'mode': 'manual_review', 'prompt': 'Double the input.', 'criteria': 'value equals 14'}}]}))
        checked = self.lib.store.get(self.owner, 'verification', job['verificationId'])
        self.assertEqual(checked['status'], 'awaiting_review', checked)
        self.assertTrue(all(c['executionEvidence'][0]['skillCallId'] for c in checked['cases']))
        self.lib.review(self.owner, checked['verificationId'], {'decision': 'accept'})
        asset = self.lib.asset(self.owner, saved['assetId'])
        self.lib.update_asset(self.owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, '"' + str(asset['revision']) + '"')
        entry = catalog(self.lib, self.owner, [{'assetId': saved['assetId'], 'versionId': saved['versionId']}], snapshots=True)[0]
        self.assertTrue(entry['skillRunRequired'])
        self.assertEqual(entry['componentSnapshot']['dependencies'][0]['contentDigest'], dependency['contentDigest'])
        self.w.store.update(self.owner, lambda state: state['tasks'].append({'id': 'test-main-chat', 'capabilityLocks': {}}))
        bridge = ChatCapabilities(self.lib, self.owner, 'test-main-chat', [], [])
        events = []
        loaded = bridge.load(saved['assetId'], saved['versionId'], events.append)
        self.assertEqual(loaded['tool'], 'library_skill')
        self.assertEqual(bridge.tools, {})
        result = bridge.execute('library_skill', {'assetId': saved['assetId'], 'task': 'Double n.', 'input': {'n': 11}}, events.append, threading.Event())
        self.assertEqual(result['content'], {'value': 22})
        self.assertTrue(result['skillCallId'])

    def test_standard_skill_roundtrip_preserves_unsupported_files_and_binary_assets(self):
        files = {'SKILL.md': b'---\nname: portable-skill\ndescription: Portable method\n---\nRead references/method.md and use scripts/example.js.\n',
                 'references/method.md': b'Keep relative links to ../assets/pixel.bin.\n',
                 'scripts/example.js': b'export const sample = 1;\n', 'assets/pixel.bin': bytes(range(256))}
        imported = self.lib.import_package(self.owner, zip_bytes({'portable-skill/' + k: v for k, v in files.items()}), 'standard.zip')
        version = self.lib.version(self.owner, imported['assetId'], imported['versionId'])
        self.assertEqual(version['contentValidity'], 'valid')
        self.assertEqual(version['runtimeReadiness'], 'blocked')
        exported = self.lib.export(self.owner, {**imported, 'format': 'agent_skills'})
        with zipfile.ZipFile(io.BytesIO(self.lib.store.blob(self.owner, exported['id']))) as archive:
            self.assertEqual({name: archive.read(name) for name in archive.namelist()}, files)
        self.assertFalse(self.lib.asset(self.owner, imported['assetId'])['enabled'])

    def test_created_skill_standard_export_omits_platform_metadata(self):
        value = manifest('skill')
        files = {'original/SKILL.md': b'---\nname: authored-skill\ndescription: Authored method\n---\nUse references/detail.md.\n',
                 'original/references/detail.md': b'Important method.'}
        draft = self.lib.draft(self.owner, {'kind': 'skill', 'name': 'authored-skill', 'description': 'method',
            'manifest': value, 'files': [{'path': p, 'text': b.decode()} for p, b in files.items()]})
        imported = self.lib.save_draft(self.owner, draft['draftId'], {'draftRevision': draft['revision']})
        output = self.lib.export(self.owner, {**imported, 'format': 'agent_skills'})
        with zipfile.ZipFile(io.BytesIO(self.lib.store.blob(self.owner, output['id']))) as archive:
            self.assertEqual(set(archive.namelist()), {'SKILL.md', 'references/detail.md'})
            self.assertEqual(archive.read('SKILL.md'), files['original/SKILL.md'])

    def test_standard_ancillary_manifest_name_is_not_misread(self):
        files = {'SKILL.md': b'---\nname: ancillary\ndescription: ancillary file\n---\nRead capability.json.\n',
                 'capability.json': b'{"example":true}'}
        value, original = parse_package(zip_bytes(files), 'skill.zip')
        self.assertEqual(value['kind'], 'skill')
        self.assertEqual(original['original/capability.json'], files['capability.json'])

    def test_snapshot_real_wasi_survives_source_deletion_and_original_mutation(self):
        imported, version = self.imported()
        snapshot = build_snapshot(self.lib, self.owner, imported['assetId'], imported['versionId'])
        environment = runtime_digest(self.lib, self.owner, snapshot)
        original = self.lib.asset(self.owner, imported['assetId'])
        original.update(deletedAt='2026-10-14', enabled=False, lifecycle='archived')
        self.lib.store.put(self.owner, 'asset', imported['assetId'], original)
        # Simulate complete source byte removal; the snapshot still owns its bytes.
        for item in version['manifest']['files']:
            self.lib.store.db.execute('DELETE FROM capability_blobs WHERE owner=? AND id=?', (self.owner, item['sha256']))
        result = invoke_snapshot(self.lib, self.owner, snapshot, {'n': 7}, context={'parentRunId': 'run-isolated', 'nodeId': 'double'})
        self.assertEqual(result['result']['content'], {'value': 14})
        self.assertEqual(result['files'], {})
        self.assertEqual(result['componentContext']['nodeId'], 'double')
        self.assertEqual(environment, runtime_digest(self.lib, self.owner, snapshot))
        self.assertNotIn('files', self.lib.store.get(self.owner, 'invocation', result['invocationId']))

    def test_snapshot_resource_and_returned_file_bytes(self):
        value = manifest()
        value['permissions']['outputFiles'] = True
        code = b'def run(payload):\n    factor = int(open("factor.txt").read())\n    value = payload["n"] * factor\n    open("/output/value.txt", "w").write(str(value))\n    return {"value": value}\n'
        snapshot = snapshot_from_package(value, {'original/tool.py': code, 'original/factor.txt': b'3'})
        result = invoke_snapshot(self.lib, self.owner, snapshot, {'n': 9})
        self.assertEqual(result['result']['content'], {'value': 27})
        self.assertEqual(result['files'], {'value.txt': b'27'})

    def test_tampering_missing_dependency_and_bad_owner_rejected(self):
        imported, _ = self.imported()
        snapshot = build_snapshot(self.lib, self.owner, imported['assetId'], imported['versionId'])
        tampered = copy.deepcopy(snapshot)
        tampered['files']['original/tool.py'] = base64.b64encode(b'changed').decode()
        with self.assertRaises(CapabilityError):
            validate_snapshot(tampered)
        with self.assertRaises(Exception):
            invoke_snapshot(self.lib, 'nonexistent-account', snapshot, {'n': 1})
        value = manifest('skill')
        value['dependencies'] = [{'sourceIdentity': 'test/components', 'contentDigest': snapshot['contentDigest'],
                                  'required': True, 'reason': 'Required tool'}]
        files = {'original/SKILL.md': b'---\nname: method\ndescription: method\n---\nUse the tool.\n'}
        with self.assertRaises(CapabilityError):
            snapshot_from_package(value, files)
        skill = snapshot_from_package(value, files, [snapshot])
        material = skill_material(skill)
        self.assertEqual(material['dependencies'][0]['contentDigest'], snapshot['contentDigest'])
        self.assertEqual(material['files'], files)
        portable = export_snapshot(skill)
        self.assertEqual(portable['dependencies'][0]['provenance'], {'contentDigest': snapshot['contentDigest']})
        self.assertEqual(bind_snapshot(self.lib, self.owner, portable), portable)

    def test_platform_permission_and_runtime_revision_rechecked(self):
        value = manifest()
        value['permissions']['externalEffects'] = 'write'
        snapshot = snapshot_from_package(value, {'original/tool.py': b'def run(payload): return {"value":1}\n'})
        with self.assertRaises(CapabilityError):
            invoke_snapshot(self.lib, self.owner, snapshot, {'n': 1})
        good = snapshot_from_package(manifest(), {'original/tool.py': b'def run(payload): return {"value":1}\n'})
        before = runtime_digest(self.lib, self.owner, good)
        self.lib.runner_revision = 'new-runner'
        self.assertNotEqual(before, runtime_digest(self.lib, self.owner, good))

    def test_fixed_pipeline_runs_python_model_python_without_model_planning(self):
        value = manifest()
        schema = value['tool']['outputSchema']
        value['tool']['execution'] = {'type': 'fixed_pipeline', 'protocolVersion': '1', 'steps': [
            {'id': 'first', 'kind': 'python_wasi', 'path': 'original/first.py', 'callable': 'run', 'outputSchema': schema},
            {'id': 'explain', 'kind': 'model', 'inputFrom': 'steps.first', 'prompt': 'Add one to value.', 'outputSchema': schema},
            {'id': 'last', 'kind': 'python_wasi', 'inputFrom': 'steps.explain', 'path': 'original/last.py', 'callable': 'run', 'outputSchema': schema}]}
        files = {'original/first.py': b'def run(payload): return {"value": payload["n"] * 2}\n',
                 'original/last.py': b'def run(payload): return {"value": payload["value"] * 3}\n'}
        snapshot = snapshot_from_package(value, files)
        provider = Provider({'value': 5})
        with patch.object(self.w.runtime, 'provider', provider):
            output = invoke_snapshot(self.lib, self.owner, snapshot, {'n': 2})
        self.assertEqual(output['result']['content'], {'value': 15})
        self.assertEqual(provider.calls[0]['tools'], [])
        self.assertEqual(json.loads(provider.calls[0]['messages'][1]['content']), {'value': 4})
        self.assertEqual([s['status'] for s in output['fixedSteps']], ['succeeded'] * 3)
        bad = Provider({'unexpected': 'new workflow'})
        with patch.object(self.w.runtime, 'provider', bad), self.assertRaises(CapabilityError):
            invoke_snapshot(self.lib, self.owner, snapshot, {'n': 2})
        failed = self.lib.store.list(self.owner, 'invocation')[0]
        self.assertEqual(len(failed['fixedSteps']), 2)
        self.assertEqual(failed['status'], 'failed')

    def test_fixed_model_timeout_and_cancellation_keep_completed_wasi_evidence(self):
        value = manifest()
        schema = value['tool']['outputSchema']
        value['tool']['execution'] = {'type': 'fixed_pipeline', 'protocolVersion': '1', 'steps': [
            {'id': 'calculate', 'kind': 'python_wasi', 'path': 'original/calculate.py', 'callable': 'run',
             'outputSchema': schema},
            {'id': 'explain', 'kind': 'model', 'inputFrom': 'steps.calculate', 'prompt': 'Explain the value.',
             'outputSchema': schema}]}
        snapshot = snapshot_from_package(value, {
            'original/calculate.py': b'def run(payload): return {"value": payload["n"] * 2}\n'})

        class FailingProvider:
            def __init__(self, error, *, cancel=False, expire=None):
                self.error, self.cancel, self.expire = error, cancel, expire

            def events(self, messages, model, stopped, on_response, tools=None):
                if self.cancel:
                    stopped.set()
                if self.expire is not None:
                    self.expire.monotonic.return_value = 121
                if self.error is None:
                    yield {'type': 'token', 'text': '{"value": 4}'}
                    return
                raise self.error

        cases = [
            ('gateway-504', GatewayError('upstream timed out', 504), 'MODEL_TIMEOUT', 'failed'),
            ('cancel-beats-504', GatewayError('aborted response', 504), 'CANCELLED', 'cancelled'),
            ('deadline-before-token', None, 'MODEL_TIMEOUT', 'failed'),
            ('deadline-after-provider-error', RuntimeError('connection closed'), 'MODEL_TIMEOUT', 'failed'),
            ('genuine-provider-failure', RuntimeError('connection closed'), 'EXECUTION_FAILED', 'failed'),
        ]
        for name, upstream_error, expected_code, expected_status in cases:
            with self.subTest(name=name):
                stopped = threading.Event()
                if name.startswith('deadline-'):
                    with patch('backend.app.modules.capability_library.fixed_pipeline.time') as clock:
                        clock.monotonic.return_value = 0
                        with patch.object(self.w.runtime, 'provider', FailingProvider(upstream_error, expire=clock)):
                            with self.assertRaises(CapabilityError) as raised:
                                invoke_snapshot(self.lib, self.owner, snapshot, {'n': 2}, stopped=stopped)
                else:
                    with patch.object(self.w.runtime, 'provider', FailingProvider(
                            upstream_error, cancel=name == 'cancel-beats-504')):
                        with self.assertRaises(CapabilityError) as raised:
                            invoke_snapshot(self.lib, self.owner, snapshot, {'n': 2}, stopped=stopped)
                self.assertEqual(raised.exception.code, expected_code)
                trace = self.lib.store.get(self.owner, 'invocation', raised.exception.invocationId)
                self.assertEqual(trace['status'], expected_status)
                self.assertEqual(trace['error']['code'], expected_code)
                self.assertEqual([step['status'] for step in trace['fixedSteps']],
                                 ['succeeded', expected_status])
                self.assertEqual(trace['fixedSteps'][0]['output'], {'value': 4})
                self.assertEqual(trace['fixedSteps'][1]['errorCode'],
                                 'INVALID_OUTPUT' if expected_code == 'EXECUTION_FAILED' else expected_code)


if __name__ == '__main__':
    unittest.main()
