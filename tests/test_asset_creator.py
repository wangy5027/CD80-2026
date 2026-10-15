"""Creator sources are actual executed bytes, not shortened display arguments."""
import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_asset_apps as app_fixtures
from backend.app.modules.capability_library.contracts import canonical, digest


class AssetCreatorTests(unittest.TestCase):
    setUp = app_fixtures.AssetAppTests.setUp
    tearDown = app_fixtures.AssetAppTests.tearDown
    post = app_fixtures.AssetAppTests.post
    save = app_fixtures.AssetAppTests.save

    def source(self, evidence=True, visualize=False):
        code = ("def run(payload):\n    values=open(payload['files']['old-upload']).read().split(',')\n"
                "    total=sum(int(v) for v in values)\n    open('/output/total.csv','w').write('total\\n'+str(total))\n"
                "    return {'total':total}\n")
        if visualize:
            code = code.replace("    return {'total':total}",
                "    from cd80_plot import line_chart\n"
                "    open('/output/chart.png','wb').write(line_chart([str(i) for i in range(len(values))], [int(v) for v in values]))\n"
                "    return {'total':total}")
            # SubAgents stores its actual self-contained bootstrap with the source.
            helper = Path('backend/app/modules/sub_agent/png_plot.py').read_text(encoding='utf-8')
            code = ("import sys as _sys, types as _types\n_cd80_plot = _types.ModuleType('cd80_plot')\n"
                    + 'exec(' + repr(helper) + ", _cd80_plot.__dict__)\n_sys.modules['cd80_plot'] = _cd80_plot\n" + code)
        executed = self.w.platform.executor.execute(code, {'files': {'old-upload': '/input/source.csv'}}, {'source.csv': b'1,2,3'})
        self.assertEqual(executed['result']['total'], 6)
        rid = 'creator-source'
        rows = [('old-upload', 'upload', 'source.csv', b'1,2,3'),
                ('source-code', 'adopted', 'analysis.py', code.encode()),
                ('source-output', 'adopted', 'total.csv', executed['files']['total.csv'])]
        if visualize:
            rows.append(('source-chart', 'adopted', 'chart.png', executed['files']['chart.png']))
        artifacts = [{'id': aid, 'runId': rid, 'stepId': sid, 'name': name, 'sha256': digest(raw),
                      'sourceRefs': ['old-upload'] if sid == 'adopted' else [], 'mediaType': 'text/plain'}
                     for aid, sid, name, raw in rows]
        steps = [{'id': 'failed', 'type': 'code', 'status': 'failed', 'inputRefs': []},
                 {'id': 'unused', 'type': 'code', 'status': 'succeeded', 'inputRefs': [], 'result': {}},
                 {'id': 'adopted', 'type': 'code', 'status': 'succeeded', 'inputRefs': ['old-upload'],
                  'arguments': {'code': 'TRUNCATED DISPLAY ONLY'}, 'result': {'codeArtifactId': 'source-code'}}]
        if evidence:
            steps[-1]['evidenceId'] = 'adopted'
        run = {'id': rid, 'status': 'succeeded', 'message': '读取文件并汇总', 'payload': {}, 'loadedRefs': [],
               'steps': steps, 'artifacts': artifacts, 'deliveries': [artifacts[-1]], 'delegation': {'constraints': ['使用真实本次输入']}}
        with self.w.sub_agents.store.transaction() as db:
            self.w.sub_agents.store.put(db, self.owner, 'run', rid, run)
            for row, meta in zip(rows, artifacts):
                db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (self.owner, row[0], rid, json.dumps(meta), row[3]))
            if evidence:
                self.w.sub_agents.store.put(db, self.owner, 'execution_evidence', 'adopted',
                    {'id': 'adopted', 'runId': rid, 'stepId': 'adopted', 'adopted': True, 'reconstructable': True,
                     'action': {'type': 'code', 'code': code, 'inputRefs': ['old-upload']}, 'executedCode': code})
        return rid, code

    def test_success_dependency_source_preserved_and_new_upload_used(self):
        rid, code = self.source()
        result = self.post('/creator', {'kind': 'app', 'sourceRunId': rid})
        self.assertEqual(result.status_code, 200, result.json)
        doc = result.json['definition']
        saved = self.service.detail(self.owner, result.json['assetId'])
        self.assertEqual(saved['versionId'], result.json['versionId'])
        self.assertEqual(saved['verification']['status'], 'unverified')
        self.assertIsNone(saved['enabledVersionId'])
        self.assertEqual(len(doc['nodes']), 1)
        node = doc['nodes'][0]
        self.assertEqual(node['provenance']['stepId'], 'adopted')
        self.assertEqual(node['provenance']['type'], 'modified')
        self.assertNotIn('TRUNCATED', node['code'])
        self.assertNotIn('old-upload', node['code'])
        self.assertIn("payload['files']['input_1']", node['code'])
        output = self.w.platform.executor.execute(node['code'], {'files': {'input_1': '/input/new.csv'}}, {'new.csv': b'10,20'})
        self.assertEqual(output['result'], {'total': 30})
        self.assertEqual(output['files']['total.csv'], b'total\n30')
        self.assertEqual(self.post('/creator', {'kind': 'app', 'sourceRunId': rid}, client=self.other).status_code, 404)

    def test_missing_evidence_is_a_gap_not_claimed_history(self):
        rid, _ = self.source(evidence=False)
        captured = self.post('/creator', {'sourceRunId': rid})
        self.assertEqual(captured.status_code, 200, captured.json)
        self.assertTrue(captured.json['definition']['gaps'])
        self.assertEqual(self.service.detail(self.owner, captured.json['assetId'])['verification']['status'], 'unverified')
        app = self.save(captured.json['definition'])
        self.assertTrue(app['issues'])
        self.assertEqual(app['verification']['status'], 'unverified')

    def test_saved_visualization_source_runs_with_new_files_without_personal_tool(self):
        rid, code = self.source(visualize=True)
        captured = self.post('/creator', {'sourceRunId': rid}).json['definition']
        self.assertEqual(captured['gaps'], [])
        self.assertEqual([n['kind'] for n in captured['nodes']], ['code'])
        self.assertIn('line_chart', captured['nodes'][0]['code'])
        captured['nodes'][0]['delivery']['files'] = [{'name': 'chart.png', 'mediaType': 'image/png'}]
        saved = self.save(captured)
        uploads = [self.client.post('/desktop/api/files', data={'file': (io.BytesIO(raw), 'prices.csv')},
                    content_type='multipart/form-data').json for raw in (b'1,2,3', b'10,20')]
        cases = [{'input': {}, 'fileBindings': {'input_1': item['id']},
                  'expectation': {'mode': 'fields', 'values': {'step_1.total': expected}}}
                 for item, expected in zip(uploads, (6, 30))]
        checked = self.post('/' + saved['id'] + '/verify', {'versionId': saved['versionId'], 'cases': cases})
        self.assertEqual(checked.status_code, 202, checked.json)
        runs = [app_fixtures.AssetAppTests.wait_run(self, key) for key in checked.json['verification']['runIds']]
        self.assertEqual(self.service.detail(self.owner, saved['id'])['verification']['status'], 'passed')
        charts = [next(a for a in run['artifacts'] if a['name'] == 'chart.png') for run in runs]
        self.assertNotEqual(charts[0]['sha256'], charts[1]['sha256'])
        for chart in charts:
            response = self.client.get(chart['downloadUrl'])
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.data.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_explicit_creator_model_edit_is_idempotent_and_new_code_labelled(self):
        original = app_fixtures.sample()
        changed = copy.deepcopy(original)
        changed['nodes'][1]['code'] += '# reviewed change\n'
        changed['nodes'][1]['provenance'] = {'type': 'historical'}
        def generate(system, payload, *_args, **kwargs):
            kwargs['on_trace']('request', {'model': 'GLM-5.3-flash', 'messages': [
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': payload['instruction']}]})
            return json.dumps(changed)
        with patch.object(self.w.platform.planner, '_call', side_effect=generate) as model:
            body = {'definition': original, 'instruction': 'api_key=creator-secret; 保留算法，补充注释'}
            one = self.post('/creator', body, key='creator-once')
            two = self.post('/creator', body, key='creator-once')
        self.assertEqual(one.status_code, 200, one.json)
        self.assertEqual(one.json, two.json)
        self.assertEqual(model.call_count, 1)
        self.assertEqual(one.json['definition']['nodes'][1]['provenance']['type'], 'modified')
        self.assertEqual(len(self.service.listing(self.owner)), 1)
        saved = self.service.detail(self.owner, one.json['assetId'])
        self.assertEqual(saved['versionId'], one.json['versionId'])
        self.assertEqual(saved['verification']['status'], 'unverified')
        self.assertEqual(saved['definition']['nodes'][1]['provenance']['type'], 'modified')
        traces = saved['authoringProvenance']['creatorTraceIds']
        self.assertEqual(len(traces), 1)
        route = '/desktop/api/asset-apps/' + saved['id'] + '/creator-traces/' + traces[0]
        traced = self.client.get(route)
        self.assertEqual(traced.status_code, 200, traced.json)
        self.assertIn('可复用应用Creator', traced.json['request']['value']['messages'][0]['content'])
        self.assertNotIn('creator-secret', json.dumps(traced.json))
        self.assertEqual(self.other.get(route).status_code, 404)
        # A process can die after the App save but before creator receipt write.
        # The same key must recover the saved version without a second model call.
        with self.service.store.transaction() as db:
            db.execute('UPDATE asset_requests SET result=? WHERE owner=? AND scope=? AND key=?',
                       (canonical({'state': 'failed', 'error': {'code': 'CREATOR_INTERRUPTED',
                           'message': 'fixture restart', 'status': 409}}), self.owner, 'app.creator', 'creator-once'))
        recovered = self.post('/creator', body, key='creator-once')
        self.assertEqual(recovered.status_code, 200, recovered.json)
        self.assertEqual((recovered.json['assetId'], recovered.json['versionId']),
                         (one.json['assetId'], one.json['versionId']))
        self.assertEqual(len(self.service.listing(self.owner)), 1)

    def test_empty_creator_does_not_make_placeholder_app(self):
        draft = self.post('/creator', {}, key='empty-creator')
        self.assertEqual(draft.status_code, 200, draft.json)
        self.assertNotIn('assetId', draft.json)
        self.assertEqual(self.service.listing(self.owner), [])

    def test_extract_multiple_nodes_tool_and_skill_keep_source_and_resources(self):
        doc = app_fixtures.sample()
        doc['nodes'][0]['files'] = [{'path': 'helper.py', 'text': 'def identity(x): return x\n'},
                                    {'path': 'assets/key.bin', 'base64': 'AAEC/w=='}]
        app = self.save(doc)
        tool = self.post('/' + app['id'] + '/extract', {'versionId': app['versionId'], 'kind': 'tool',
            'nodeIds': [n['id'] for n in doc['nodes']]})
        self.assertEqual(tool.status_code, 200, tool.json)
        self.assertEqual(len(tool.json['draft']['manifest']['tool']['execution']['steps']), 3)
        skill = self.post('/' + app['id'] + '/extract', {'versionId': app['versionId'], 'kind': 'skill', 'nodeIds': ['prepare']})
        self.assertEqual(skill.status_code, 200, skill.json)
        files = {f['path']: f for f in skill.json['draft']['files']}
        self.assertEqual(files['original/scripts/prepare/helper.py']['text'], 'def identity(x): return x\n')
        self.assertEqual(files['original/scripts/prepare/assets/key.bin']['base64'], 'AAEC/w==')
        self.assertIn('original/scripts/prepare/__app_entry__.py', files)
        record = self.w.capabilities.draft(self.owner, skill.json['draft'])
        self.assertEqual(record['content']['kind'], 'skill')
        saved = self.w.capabilities.save_draft(self.owner, record['draftId'], {'draftRevision': record['revision']})
        version = self.w.capabilities.version(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(version['validationPolicy'], 'original_and_new')

    def test_task_capture_tool_uses_new_creator_not_single_step_summary(self):
        rid, _ = self.source()
        result = self.post('/creator', {'sourceRunId': rid, 'kind': 'tool'})
        self.assertEqual(result.status_code, 200, result.json)
        self.assertEqual(result.json['draft']['manifest']['tool']['execution']['type'], 'fixed_pipeline')
        content = '\n'.join(f.get('text', '') for f in result.json['draft']['files'])
        self.assertIn('sum(', content)
        self.assertNotIn('TRUNCATED', content)


if __name__ == '__main__':
    unittest.main()
