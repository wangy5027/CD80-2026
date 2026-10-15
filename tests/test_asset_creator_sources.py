"""Creator consumes actual adopted evidence and keeps portable implementations."""
import base64
import copy
import json
import threading
import unittest
from unittest.mock import patch

from tests import test_asset_apps as fixture
from tests import test_asset_creator as creator_fixture
from tests.test_asset_components import manifest
from backend.app.modules.agent_platform.asset_creator import _legacy, legacy_items
from backend.app.modules.capability_library.components import build_snapshot, invoke_snapshot
from backend.app.modules.capability_library.contracts import canonical, digest
from backend.app.modules.capability_library.packages import file_index, zip_bytes


class ScriptModel:
    def call(self, stage, payload, stopped, on_response):
        if stage == 'review':
            return {'decision': 'accept', 'answer': 'Verified real result.'}
        if stage == 'approve':
            return {'decision': 'approve', 'reason': 'Declared bounded script.'}
        done = [s for s in payload['steps'] if s['status'] == 'succeeded']
        if done:
            return {'type': 'finish', 'answer': 'Done.', 'resultStepId': done[-1]['id'], 'artifactIds': []}
        inputs = next(o['inputs'] for o in payload['observations'] if 'inputs' in o)
        return {'type': 'tool', 'name': 'skill_script', 'arguments': {'path': 'original/scripts/run.py', 'input': inputs},
                'purpose': 'Use actual declared script.'}


class CreatorSourceTests(unittest.TestCase):
    setUp = fixture.AssetAppTests.setUp
    tearDown = fixture.AssetAppTests.tearDown
    post = fixture.AssetAppTests.post
    source = creator_fixture.AssetCreatorTests.source

    def method(self, name):
        value = manifest('skill')
        value['name'] = name
        value['source']['identity'] = 'test/' + name
        value['skill']['scripts'] = [{'path': 'original/scripts/run.py', 'type': 'python_wasi', 'callable': 'run'}]
        files = {'original/SKILL.md': ('---\nname: ' + name + '\ndescription: Saved script method.\n---\nUse scripts/run.py with current n.\n').encode(),
                 'original/scripts/run.py': b'def run(payload): return {"value":payload["n"] + int(open("offset.txt").read())}\n',
                 'original/scripts/offset.txt': b'3', 'original/assets/data.bin': b'\x00\x01\xfe'}
        value['files'] = file_index(files)
        saved = self.w.capabilities.import_package(self.owner, zip_bytes({**files, 'capability.json': canonical(value).encode()}), name + '.zip')
        version = self.w.capabilities.version(self.owner, saved['assetId'], saved['versionId'])
        return {k: version[k] for k in ('assetId', 'versionId', 'configurationId')}

    def parent(self, rid, name, arguments, refs, *, invocation_ids=(), skill_call=None, result=None):
        sid, aid = rid + '-step', rid + '-result'
        raw = canonical(result or {'content': {'value': 5}}).encode()
        artifact = {'id': aid, 'runId': rid, 'stepId': sid, 'name': 'tool-result.json', 'sha256': digest(raw),
                    'size': len(raw), 'sourceRefs': []}
        step = {'id': sid, 'type': 'tool', 'name': name, 'status': 'succeeded', 'evidenceId': sid,
                'inputRefs': [], 'result': {'artifacts': [artifact]}}
        run = {'id': rid, 'status': 'succeeded', 'message': 'Adopt the actual method', 'conversationId': rid,
               'payload': {}, 'loadedRefs': refs, 'steps': [step], 'artifacts': [artifact], 'deliveries': [artifact]}
        evidence = {'id': sid, 'runId': rid, 'stepId': sid, 'status': 'succeeded', 'adopted': True,
                    'reconstructable': True, 'resultArtifactId': aid, 'invocationIds': list(invocation_ids),
                    'skillCallIds': [skill_call] if skill_call else [],
                    'action': {'type': 'tool', 'name': name, 'arguments': arguments, 'inputRefs': []}}
        with self.w.sub_agents.store.transaction() as db:
            self.w.sub_agents.store.put(db, self.owner, 'run', rid, run)
            self.w.sub_agents.store.put(db, self.owner, 'execution_evidence', sid, evidence)
            db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (self.owner, aid, rid, canonical(artifact), raw))
        return rid

    def script_source(self):
        ref, ignored = self.method('adopted-guide'), self.method('loaded-unused-guide')
        rid = 'script-parent'
        def events(messages, *args, **kwargs):
            if messages[-1]['role'] != 'tool':
                yield {'type': 'turn_end', 'tool_calls': [{'id': 'script', 'type': 'function', 'function': {
                    'name': 'run_capability_script', 'arguments': canonical({'skill': 'skill_' + ref['versionId'],
                        'path': 'original/scripts/run.py', 'input': {'n': 2}})}}]}
            else:
                yield {'type': 'token', 'text': 'Actual result: 5.'}
                yield {'type': 'turn_end', 'tool_calls': []}
        with patch.object(self.w.runtime.provider, 'events', events):
            calls = list(self.w.capabilities.chat_events(self.owner, [ref], 'Run the actual script.', [], threading.Event(),
                         lambda response: None, trial=True, task_id=rid))
        invocation = next(c['invocationId'] for c in calls if c.get('type') == 'tool_completed')
        self.parent(rid, 'library_script', {'assetId': ref['assetId'], 'path': 'original/scripts/run.py', 'input': {'n': 2}},
                    [ref, ignored], invocation_ids=[invocation])
        return rid, ref, ignored

    def test_loaded_but_unadopted_skill_is_not_saved_with_source_method(self):
        rid, ref, unused = self.script_source()
        response = self.post('/creator', {'kind': 'skill', 'sourceRunId': rid})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['issues'], [])
        draft = response.json['draft']
        raw = b'\n'.join(f['text'].encode() if 'text' in f else base64.b64decode(f['base64']) for f in draft['files'])
        self.assertIn(b'adopted-guide', raw)
        self.assertNotIn(b'loaded-unused-guide', raw)
        self.assertTrue(draft['authoringProvenance']['nodes'])
        self.assertFalse(draft['authoringProvenance']['historicalEvidenceVerified'])

    def test_actual_standard_script_recovers_complete_resources_and_new_input(self):
        rid, ref, _ = self.script_source()
        response = self.post('/creator', {'sourceRunId': rid})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['issues'], [], response.json)
        node = response.json['definition']['nodes'][0]
        self.assertEqual(node['kind'], 'tool')
        self.assertEqual(node['provenance']['type'], 'modified')
        self.assertEqual(base64.b64decode(node['component']['files']['original/assets/data.bin']), b'\x00\x01\xfe')
        library = self.w.capabilities
        asset = library.asset(self.owner, ref['assetId'])
        asset['deletedAt'] = '2026-10-14'
        library.store.put(self.owner, 'asset', asset['assetId'], asset)
        actual = invoke_snapshot(library, self.owner, node['component'], {'n': 9})
        self.assertEqual(actual['result']['content'], {'value': 12})

    def test_actual_library_skill_stays_dynamic_and_owns_executed_snapshot(self):
        ref = self.method('dynamic-guide')
        snapshot = build_snapshot(self.w.capabilities, self.owner, ref['assetId'], ref['versionId'])
        rid = 'dynamic-parent'
        with patch.object(self.w.sub_agents, 'model', ScriptModel()):
            called = self.w.sub_agents.run_skill(self.owner, snapshot, 'Compute current n.', {'n': 4}, {'schema': {}},
                                                parent_run_id=rid, node_id='method')
        self.assertEqual(called['status'], 'succeeded', called)
        self.parent(rid, 'library_skill', {'assetId': ref['assetId'], 'task': 'Compute current n.', 'input': {'n': 4}},
                    [ref], skill_call=called['skillCallId'], result={'content': called['result'], 'skillCallId': called['skillCallId']})
        asset = self.w.capabilities.asset(self.owner, ref['assetId'])
        asset['deletedAt'] = '2026-10-14'
        self.w.capabilities.store.put(self.owner, 'asset', asset['assetId'], asset)
        response = self.post('/creator', {'sourceRunId': rid})
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['issues'], [], response.json)
        nodes = response.json['definition']['nodes']
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]['kind'], 'skill')
        self.assertEqual(nodes[0]['component']['snapshotDigest'], snapshot['snapshotDigest'])
        self.assertEqual(nodes[0]['task'], 'Compute current n.')
        with patch.object(self.w.sub_agents, 'model', ScriptModel()):
            fresh = self.w.sub_agents.run_skill(self.owner, nodes[0]['component'], nodes[0]['task'], {'n': 12}, {'schema': {}},
                                               parent_run_id='fresh-parent', node_id=nodes[0]['id'])
        self.assertEqual(fresh['result'], {'value': 15}, fresh)

    def test_real_recipe_source_shape_recovers_code_without_inventing_missing_evidence(self):
        rid, code = self.source(evidence=False)
        recipe = {'id': 'old-recipe', 'name': '旧研究方法', 'source': {'kind': 'sub_agent', 'runId': rid}}
        with patch.object(self.service.evolution, 'recipe', return_value=recipe), patch.object(self.service.evolution, 'recipes', return_value=[recipe]):
            doc = _legacy(self.service, self.owner, {'kind': 'recipe', 'id': recipe['id']})
            listing = legacy_items(self.service, self.owner, self.service.evolution)
        self.assertIn('sum', doc['nodes'][0]['code'])
        self.assertTrue(any('完整调用证据缺失' in gap for gap in doc['gaps']))
        self.assertEqual(listing[0]['sourceRunId'], rid)
        with patch.object(self.service.evolution, 'recipe', return_value={**recipe, 'source': {'kind': 'community', 'runId': rid}}):
            public = _legacy(self.service, self.owner, {'kind': 'recipe', 'id': recipe['id']})
        self.assertEqual(public['nodes'], [])
        other = self.other.get('/desktop/api/session').json['user']['id']
        with patch.object(self.service.evolution, 'recipe', return_value=recipe):
            with self.assertRaises(Exception):
                _legacy(self.service, other, {'kind': 'recipe', 'id': recipe['id']})

    def test_revision_and_version_edit_keep_claims_without_inheriting_validation(self):
        rid, _, _ = self.script_source()
        draft = self.post('/creator', {'kind': 'skill', 'sourceRunId': rid}).json['draft']
        modified = copy.deepcopy(draft)
        modified['files'][0]['text'] += '\nDescribe actual output.\n'
        modified['authoringProvenance'] = {'historicalEvidenceVerified': True, 'sourceVerified': True}
        with patch.object(self.w.platform.planner, '_call', return_value=canonical({'draft': modified})):
            response = self.post('/creator', {'kind': 'skill', 'draft': draft, 'instruction': '补充结果说明'})
        self.assertEqual(response.status_code, 200, response.json)
        revised = response.json['draft']
        claims = revised['authoringProvenance']
        self.assertEqual(claims['type'], 'modified')
        self.assertEqual(claims['previous'], draft['authoringProvenance'])
        self.assertFalse(claims['historicalEvidenceVerified'])
        library = self.w.capabilities
        saved_draft = library.draft(self.owner, revised)
        saved = library.save_draft(self.owner, saved_draft['draftId'], {'draftRevision': saved_draft['revision']})
        version = library.version(self.owner, saved['assetId'], saved['versionId'])
        edited = library.draft(self.owner, {'kind': 'skill', 'name': 'edit', 'baseAssetId': saved['assetId'], 'baseVersionId': saved['versionId']})
        self.assertEqual(edited['content']['authoringProvenance']['previous'], claims)
        self.assertFalse(version['authoringProvenance']['historicalEvidenceVerified'])
        self.assertIsNone(library.evidence(self.owner, version))


if __name__ == '__main__':
    unittest.main()
