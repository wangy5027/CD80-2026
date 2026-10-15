"""Explicit source selection never invents adoption or requires personal Skills."""
import copy
import json
import unittest
from unittest.mock import patch

from tests import test_asset_apps as app_fixture
from backend.app.modules.capability_library.components import invoke_snapshot
from backend.app.modules.capability_library.contracts import canonical, digest
from backend.app.modules.workbench.tool_registry import ToolResult


class SourceSelectionTests(unittest.TestCase):
    setUp = app_fixture.AssetAppTests.setUp
    tearDown = app_fixture.AssetAppTests.tearDown
    post = app_fixture.AssetAppTests.post

    def source(self, *, status='succeeded', adopted=None, code=False, dependency=False):
        rid = 'selected-source'
        records, artifacts, rows, steps = [], [], [], []
        for index, (name, arguments) in enumerate([
                ('sfc_quotes', {'action': 'quote', 'code': '000001'}),
                ('sfc_margin', {'code': '000001', 'limit': 5, 'sort': 'net_margin'}),
                ('sfc_quotes', {'action': 'quote', 'code': '600000'})], 1):
            sid, aid = 'source-' + str(index), 'result-' + str(index)
            inputs = ['result-1'] if dependency and index == 2 else []
            action = {'type': 'tool', 'name': name, 'arguments': arguments, 'inputRefs': inputs}
            step = {'id': sid, 'type': 'tool', 'name': name, 'status': 'succeeded', 'evidenceId': sid,
                    'inputRefs': inputs, 'purpose': 'Actual ' + str(index)}
            raw = canonical({'content': {'fixture': index}}).encode()
            meta = {'id': aid, 'runId': rid, 'stepId': sid, 'name': 'tool-result.json',
                    'sha256': digest(raw), 'size': len(raw), 'sourceRefs': inputs, 'artifactRole': 'execution_result'}
            if code:
                implementation = 'def run(payload): return {"value":' + str(index) + '}\n'
                # This fixture actually runs each stored historical source through WASI.
                actual = self.w.platform.executor.execute(implementation, {}, {})
                self.assertEqual(actual['result'], {'value': index})
                step.update(type='code', result={'codeArtifactId': 'code-' + str(index)})
                action = {'type': 'code', 'code': implementation, 'inputRefs': []}
                code_meta = {**meta, 'id': 'code-' + str(index), 'name': 'analysis.py', 'sha256': digest(implementation.encode())}
                artifacts.append(code_meta); rows.append((code_meta, implementation.encode()))
            else:
                step['result'] = {'artifacts': [meta]}
            records.append({'id': sid, 'runId': rid, 'stepId': sid, 'status': 'succeeded',
                'adopted': adopted, 'adoptionBasis': 'no-explicit-output-dependency' if adopted is None else 'fixture-explicit-adoption',
                'reconstructable': True, 'resultArtifactId': aid, 'invocationIds': [], 'skillCallIds': [], 'action': action})
            steps.append(step); artifacts.append(meta); rows.append((meta, raw))
        steps.append({'id': 'failed-attempt', 'type': 'tool', 'name': 'sfc_quotes', 'status': 'failed', 'inputRefs': []})
        run = {'id': rid, 'status': status, 'message': 'Historical analysis and visualization', 'payload': {},
               'loadedRefs': [], 'steps': steps, 'artifacts': artifacts, 'deliveries': []}
        with self.w.sub_agents.store.transaction() as db:
            self.w.sub_agents.store.put(db, self.owner, 'run', rid, run)
            for record in records:
                self.w.sub_agents.store.put(db, self.owner, 'execution_evidence', record['stepId'], record)
            for meta, raw in rows:
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (self.owner, meta['id'], rid, canonical(meta), raw))
        return rid

    def test_text_only_result_returns_candidates_without_default_adoption_or_generic_failure(self):
        rid = self.source()
        for kind in ('app', 'skill', 'tool'):
            response = self.post('/creator', {'kind': kind, 'sourceRunId': rid})
            self.assertEqual(response.status_code, 200, response.json)
            result = response.json
            self.assertTrue(result['selectionRequired']); self.assertEqual(result['selectedStepIds'], [])
            self.assertEqual([c['stepId'] for c in result['candidateSteps']], ['source-1','source-2','source-3'])
            self.assertTrue(all(c['adopted'] is None for c in result['candidateSteps']))
            self.assertIsNone(result['source']['objectiveCompleted'])
            if kind == 'app': self.assertEqual(result['definition']['nodes'], [])
            else: self.assertIsNone(result['draft'])

    def test_selected_builtin_preserves_actual_parameters_and_current_controlled_adapter(self):
        rid = self.source()
        entry = self.w.runtime.tools.get('sfc_quotes')
        with patch.object(entry, 'handler', side_effect=AssertionError('capture must not execute')):
            result = self.post('/creator', {'kind': 'app', 'sourceRunId': rid, 'stepIds': ['source-1']}).json
        self.assertFalse(result['selectionRequired']); self.assertEqual(result['selectedStepIds'], ['source-1'])
        self.assertEqual(result['issues'], [], result)
        node = result['definition']['nodes'][0]
        self.assertEqual(node['component']['manifest']['tool']['execution'], {'type':'builtin','handlerId':'sfc_quotes'})
        self.assertEqual(node['provenance']['type'], 'modified')
        self.assertFalse(node['provenance']['historicalImplementationFrozen'])
        self.assertEqual(result['definition']['inputSchema']['properties']['step_1_code']['default'], '000001')
        calls = []
        def handler(arguments, context):
            calls.append(arguments); return ToolResult({'code': arguments['code']}, [])
        with patch.object(entry, 'available', return_value=True), patch.object(entry, 'handler', side_effect=handler):
            actual = invoke_snapshot(self.w.capabilities, self.owner, node['component'], {'action':'quote','code':'600000'})
        self.assertEqual(actual['result']['content']['code'], '600000')
        self.assertEqual(calls, [{'action':'quote','code':'600000'}])
        with patch.object(entry, 'available', return_value=False):
            with self.assertRaises(Exception): invoke_snapshot(self.w.capabilities, self.owner, node['component'], {'code':'600000'})
        for kind in ('skill','tool'):
            response = self.post('/creator', {'kind':kind, 'sourceRunId':rid, 'stepIds':['source-1']})
            self.assertEqual(response.status_code, 200, response.json)
            draft = response.json['draft']; self.assertTrue(draft['dependencySnapshots'])
            self.assertEqual(response.json['selectedStepIds'], ['source-1'])
            saved_draft = self.w.capabilities.draft(self.owner, draft)
            with patch.object(entry, 'handler', side_effect=AssertionError('save must not execute')):
                saved = self.w.capabilities.save_draft(self.owner, saved_draft['draftId'], {'draftRevision':saved_draft['revision']})
            version = self.w.capabilities.version(self.owner, saved['assetId'], saved['versionId'])
            self.assertEqual(version['validationPolicy'], 'original_and_new')
            self.assertFalse(self.w.capabilities.asset(self.owner, saved['assetId'])['enabled'])
            self.assertIsNone(self.w.capabilities.evidence(self.owner, version))

    def test_explicit_selection_keeps_successful_predecessors_but_not_other_attempts(self):
        rid = self.source(dependency=True)
        result = self.post('/creator', {'sourceRunId':rid, 'stepIds':['source-2']}).json
        self.assertEqual(result['selectedStepIds'], ['source-1','source-2'])
        self.assertEqual([n['provenance']['stepId'] for n in result['definition']['nodes']], ['source-1','source-2'])
        self.assertEqual(result['candidateSteps'][1]['inputStepIds'], ['source-1'])
        self.assertEqual(self.post('/creator', {'sourceRunId':rid,'stepIds':['failed-attempt']}).status_code, 422)
        self.assertEqual(self.post('/creator', {'sourceRunId':rid,'stepIds':['source-1']}, client=self.other).status_code, 404)

    def test_partial_success_is_selectable_without_claiming_original_goal_completed(self):
        rid = self.source(status='partial')
        for status in ('partial','failed'):
            with self.w.sub_agents.store.transaction() as db:
                run = self.w.sub_agents.store.get(db,self.owner,'run',rid,True); run['status']=status
                self.w.sub_agents.store.put(db,self.owner,'run',rid,run)
            result = self.post('/creator', {'kind':'skill','sourceRunId':rid,'stepIds':['source-1']}).json
            self.assertIsNotNone(result['draft']); self.assertFalse(result['source']['objectiveCompleted'])
            self.assertTrue(any('原任务未完整完成' in warning for warning in result['warnings']))
            stored = self.w.sub_agents.execution_evidence(self.owner, rid, 'source-1')
            self.assertIsNone(stored['adopted'])

    def test_no_steps_and_redacted_inputs_have_specific_nonexecuting_preview(self):
        rid = self.source()
        with self.w.sub_agents.store.transaction() as db:
            record = self.w.sub_agents.store.get(db,self.owner,'execution_evidence','source-1',True)
            record['reconstructable']=False; self.w.sub_agents.store.put(db,self.owner,'execution_evidence','source-1',record)
        result = self.post('/creator', {'kind':'skill','sourceRunId':rid,'stepIds':['source-1']}).json
        self.assertIsNone(result['draft']); self.assertFalse(result['candidateSteps'][0]['reconstructable'])
        self.assertTrue(result['candidateSteps'][0]['issues'])
        with self.w.sub_agents.store.transaction() as db:
            run = self.w.sub_agents.store.get(db,self.owner,'run',rid,True); run['steps']=[];run['artifacts']=[]
            self.w.sub_agents.store.put(db,self.owner,'run',rid,run)
        result = self.post('/creator', {'kind':'tool','sourceRunId':rid}).json
        self.assertIsNone(result['draft']); self.assertEqual(result['candidateSteps'], [])
        self.assertTrue(any('没有实际成功' in warning for warning in result['warnings']))

    def test_live_source_rejected_and_completion_object_and_legacy_string_supported(self):
        rid = self.source()
        for status in ('running', 'planning', 'queued', 'awaiting_user', 'awaiting_approval', 'cancelling'):
            with self.w.sub_agents.store.transaction() as db:
                run = self.w.sub_agents.store.get(db,self.owner,'run',rid,True); run['status']=status
                self.w.sub_agents.store.put(db,self.owner,'run',rid,run)
            response = self.post('/creator', {'sourceRunId':rid,'stepIds':['source-1']})
            self.assertEqual(response.status_code,409,response.json)
            self.assertEqual(response.json['code'],'SOURCE_NOT_FINISHED')
        for completion, expected in (({'status':'complete','missingDeliverables':[],'reasons':[]},True),
                ({'status':'partial','missingDeliverables':['chart'],'reasons':['missing chart']},False),
                ('complete',True),('partial',False),(None,None)):
            with self.w.sub_agents.store.transaction() as db:
                run = self.w.sub_agents.store.get(db,self.owner,'run',rid,True)
                run.update(status='succeeded',completion=completion)
                self.w.sub_agents.store.put(db,self.owner,'run',rid,run)
            response=self.post('/creator', {'sourceRunId':rid,'stepIds':['source-1']})
            self.assertEqual(response.status_code,200,response.json)
            self.assertIs(response.json['source']['objectiveCompleted'],expected)

    def test_code_with_predecessor_file_preserves_source_selection_object(self):
        rid=self.source(code=True,dependency=True)
        response=self.post('/creator',{'sourceRunId':rid,'stepIds':['source-2']})
        self.assertEqual(response.status_code,200,response.json)
        doc=response.json['definition']
        self.assertEqual(doc['source']['runId'],rid)
        self.assertEqual(doc['source']['selectedStepIds'],['source-1','source-2'])
        self.assertEqual(doc['nodes'][1]['inputBindings']['input_1'],
                         {'from':'artifact','stepId':'step_1','name':'tool-result.json'})

    def test_invalid_source_metadata_is_unverified_draft_and_explicit_false_adoption_still_selectable(self):
        rid=self.source(code=True,adopted=False)
        preview=self.post('/creator',{'sourceRunId':rid}).json
        self.assertTrue(preview['selectionRequired'])
        self.assertTrue(all(row['adopted'] is False for row in preview['candidateSteps']))
        response=self.post('/creator',{'sourceRunId':rid,'stepIds':['source-1']})
        self.assertEqual(response.status_code,200,response.json)
        original=response.json['definition']
        for index,origin in enumerate(('invalid-source',None,{'runId':rid,'selectedStepIds':[{}]})):
            doc=copy.deepcopy(original);doc['source']=origin
            saved=self.service.save(self.owner,{'definition':doc},'invalid-source-'+str(index))
            self.assertFalse(saved['definition']['nodes'][0]['provenance']['sourceVerified'])
            self.assertEqual(saved['definition']['nodes'][0]['provenance']['type'],'modified')

    def test_selected_unadopted_exact_code_keeps_verified_source_for_multiple_nodes(self):
        rid = self.source(code=True)
        response = self.post('/creator', {'sourceRunId':rid,'stepIds':['source-1','source-2','source-3']})
        self.assertEqual(response.status_code,200,response.json)
        doc=response.json['definition']; self.assertEqual(doc['source']['selectionBasis'],'explicit_steps')
        saved=self.service.save(self.owner,{'definition':doc},'selected-original')
        self.assertTrue(all(n['provenance']['sourceVerified'] for n in saved['definition']['nodes']))
        self.assertTrue(all(n['provenance']['type']=='historical' for n in saved['definition']['nodes']))
        self.assertEqual(saved['definition']['nodes'][2]['id'],'step_3')
        self.assertIsNone(self.w.sub_agents.execution_evidence(self.owner,rid,'source-1')['adopted'])
        # Keeping only step_3 does not renumber its original source comparison.
        subset=copy.deepcopy(doc);subset['nodes']=subset['nodes'][2:];subset['outputs']={'step_3':subset['outputs']['step_3']}
        kept=self.service.save(self.owner,{'definition':subset},'selected-third')
        self.assertTrue(kept['definition']['nodes'][0]['provenance']['sourceVerified'])
        forged=copy.deepcopy(doc);forged['nodes'][0]['code']='def run(payload): return {"value":999}\n'
        changed=self.service.save(self.owner,{'definition':forged},'changed-original')
        self.assertFalse(changed['definition']['nodes'][0]['provenance']['sourceVerified'])
        other=self.other.get('/desktop/api/session').json['user']['id']
        cross=self.service.save(other,{'definition':doc},'foreign-original')
        self.assertTrue(all(not n['provenance']['sourceVerified'] for n in cross['definition']['nodes']))
        # The normal adopted path must retain all independent node identities too.
        with self.w.sub_agents.store.transaction() as db:
            for sid in ('source-1','source-2','source-3'):
                record=self.w.sub_agents.store.get(db,self.owner,'execution_evidence',sid,True)
                record['adopted']=True
                self.w.sub_agents.store.put(db,self.owner,'execution_evidence',sid,record)
        adopted=self.post('/creator',{'sourceRunId':rid}).json['definition']
        checked=self.service.save(self.owner,{'definition':adopted},'adopted-multiple')
        self.assertEqual([n['id'] for n in checked['definition']['nodes']],['step_1','step_2','step_3'])
        self.assertTrue(all(n['provenance']['sourceVerified'] for n in checked['definition']['nodes']))


if __name__ == '__main__': unittest.main()
