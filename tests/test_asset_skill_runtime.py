import copy
import json
import tempfile
import threading
import unittest

from backend.app import create_app
from backend.app.modules.sub_agent.store import Store


SKILL = {'name': '计算与交付', 'entry': 'SKILL.md', 'files': {'SKILL.md': '# 方法\n调用工具后实际计算并保存CSV。'},
         'tools': [{'name': 'calculate', 'description': '合成计算工具', 'inputSchema': {'type': 'object'}}]}
DELIVERY = {'schema': {'type': 'object', 'required': ['total'], 'properties': {'total': {'type': 'number'}}},
            'files': [{'name': 'report.csv', 'mediaType': 'text/csv'}], 'evidenceFields': ['total']}


class LoopModel:
    def __init__(self, ask=False, invalid=False):
        self.calls = []
        self.ask, self.invalid = ask, invalid

    def call(self, stage, payload, stopped, on_response):
        self.calls.append(stage)
        if stage == 'approve':
            return {'decision': 'approve', 'reason': '只处理已授权实际输入。'}
        if stage == 'review':
            return {'decision': 'accept', 'answer': '实际结果已核对。'}
        if self.ask and not any('userAnswer' in o for o in payload['observations']):
            return {'type': 'ask', 'question': '确认继续计算？'}
        if self.invalid:
            return {'type': 'finish', 'answer': '自称算好了', 'result': {'total': 999}, 'artifactIds': []}
        steps = payload['steps']
        tool = next((s for s in steps if s['type'] == 'tool' and s['status'] == 'succeeded'), None)
        if not tool:
            return {'type': 'tool', 'name': 'calculate', 'arguments': {'values': list(range(25)), 'note': 'x' * 1200}, 'purpose': '真实计算'}
        code = next((s for s in steps if s['type'] == 'code' and s['status'] == 'succeeded'), None)
        if not code:
            ref = tool['result']['artifacts'][0]['id']
            source = ("import json\ndef run(payload):\n"
                      f"    with open(payload['files'][{ref!r}]) as f:\n        data=json.load(f)['content']\n"
                      "    with open('/output/report.csv','w') as f:\n        f.write('total\\n'+str(data['total'])+'\\n')\n"
                      "    return {'total': data['total']}\n")
            return {'type': 'code', 'reason': '按真实计算结果生成交付', 'inputRefs': [ref], 'code': source}
        return {'type': 'finish', 'answer': '计算完成', 'resultStepId': code['id'],
                'artifactIds': [a['id'] for a in code['result']['artifacts'] if a['name'] == 'report.csv']}


class AssetSkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='asset-skill-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name, 'GITEE_AI_API_KEY': 'fixture-only',
                               'CAPABILITY_SKIP_PUBLIC_SEED': True})
        self.client = self.app.test_client()
        self.client.post('/desktop/api/register', json={'email': 'skill@example.invalid', 'password': 'test-password'})
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.w = self.app.extensions['workbench_service']
        self.s = self.app.extensions['sub_agents']
        self.s.model = LoopModel()
        self.tool_calls = []

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def invoke(self, name, arguments, files, stopped):
        self.tool_calls.append(copy.deepcopy(arguments))
        return {'invocationId': 'fixture-invocation', 'status': 'succeeded',
                'result': {'content': {'total': sum(arguments['values'])}, 'sources': [], 'artifacts': []}, 'files': {}}

    def run_skill(self, **options):
        args = dict(parent_run_id='app-run-a', node_id='analysis', request_id='skill-call-a', invoke_tool=self.invoke)
        args.update(options)
        return self.s.run_skill(self.owner, SKILL, '计算并生成CSV', {'symbol': 'SYNTHETIC'}, DELIVERY, **args)

    def test_real_wasi_multistep_and_complete_evidence(self):
        self.assertTrue(self.s.executor.status()['available'])
        result = self.run_skill()
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['result'], {'total': 300})
        self.assertEqual(result['files']['report.csv'], b'total\n300\n')
        self.assertEqual(self.w.store.get(self.owner)['tasks'], [])
        tool, code = result['steps']
        self.assertEqual(len(tool['arguments']['values']), 10)
        complete = self.s.execution_evidence(self.owner, result['skillCallId'], tool['id'])
        self.assertEqual(complete['action']['arguments'], self.tool_calls[0])
        self.assertEqual(complete['invocationIds'], ['fixture-invocation'])
        self.assertEqual(complete['parentRunId'], 'app-run-a')
        self.assertTrue(complete['reconstructable'])
        self.assertTrue(complete['adopted'])
        actual = self.s.execution_evidence(self.owner, result['skillCallId'], code['id'])
        self.assertIn('_cd80_plot', actual['executedCode'])
        self.assertEqual(actual['inputRefs'], code['inputRefs'])
        self.assertIsNotNone(actual['codeArtifactId'])
        response = self.client.get('/desktop/api/sub-agent-runs/' + result['skillCallId'] + '/execution-evidence/' + code['id'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['codeSha256'], actual['codeSha256'])

    def test_json_only_skill_selected_internal_result_is_not_file_delivery(self):
        skill = copy.deepcopy(SKILL)
        skill['permissions'] = {'inputFiles': False, 'outputFiles': False}

        class SelectActualResult:
            def call(inner, stage, payload, stopped, on_response):
                if stage == 'review':
                    return {'decision': 'accept', 'answer': '实际 JSON 已交付。'}
                completed = [step for step in payload['steps'] if step['status'] == 'succeeded']
                if not completed:
                    return {'type': 'tool', 'name': 'calculate', 'arguments': {'values': [2, 3, 5]}}
                return {'type': 'finish', 'answer': '选择实际结果依据。',
                        'artifactIds': [completed[-1]['result']['artifacts'][0]['id']]}

        self.s.model = SelectActualResult()
        result = self.s.run_skill(self.owner, skill, '只返回实际 JSON', {},
            {'schema': {'type': 'object', 'properties': {'total': {'const': 10}}, 'required': ['total']}},
            parent_run_id='json-app', node_id='json-node', invoke_tool=self.invoke)
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['result'], {'total': 10})
        self.assertEqual(result['files'], {})
        self.assertEqual(result['artifacts'], [])
        run = self.s.store.run(self.owner, result['skillCallId'])
        stored = next(a for a in run['artifacts'] if a['artifactRole'] == 'execution_result')
        self.assertEqual(run['outputSource'], {'resultArtifactId': stored['id']})
        self.assertEqual(run['correctionsUsed'], 0)

    def test_terminal_replay_does_not_execute_again(self):
        first = self.run_skill()
        second = self.run_skill()
        self.assertEqual(first['skillCallId'], second['skillCallId'])
        self.assertEqual(second['files'], first['files'])
        self.assertEqual(len(self.tool_calls), 1)
        with self.assertRaises(Exception) as caught:
            self.s.run_skill(self.owner, SKILL, '改变任务', {}, DELIVERY, parent_run_id='app-run-a', node_id='analysis', request_id='skill-call-a')
        self.assertEqual(caught.exception.status, 409)

    def test_fabricated_result_cannot_pass_platform_delivery(self):
        self.s.model = LoopModel(invalid=True)
        result = self.run_skill()
        self.assertEqual(result['status'], 'failed')
        record = self.s.store.run(self.owner, result['skillCallId'])
        self.assertEqual(record['correctionsUsed'], 3)
        self.assertNotIn('review', self.s.model.calls)
        self.assertFalse(result['files'])

    def test_question_resumes_same_call_with_revision(self):
        self.s.model = LoopModel(ask=True)
        first = self.run_skill()
        self.assertEqual(first['status'], 'awaiting_user')
        run = self.s.store.run(self.owner, first['skillCallId'])
        answer = {'questionId': run['question']['id'], 'revision': run['revision'], 'answer': '继续'}
        second = self.run_skill(answer=answer)
        self.assertEqual(second['skillCallId'], first['skillCallId'])
        self.assertEqual(second['status'], 'succeeded', second)
        self.assertEqual(len(self.tool_calls), 1)

    def test_cancel_does_not_deliver_late_tool_result(self):
        stop = threading.Event()
        def late(*args):
            stop.set()
            return {'result': {'total': 5}, 'files': {}}
        result = self.run_skill(stopped=stop, invoke_tool=late)
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['files'])
        self.assertFalse(any(s['status'] == 'succeeded' for s in result['steps']))

    def test_scoped_skill_cannot_invoke_ambient_tool(self):
        class EscapeModel:
            def call(self, *args):
                return {'type': 'tool', 'name': 'search', 'arguments': {'query': 'do not execute'}}
        self.s.model = EscapeModel()
        result = self.run_skill()
        self.assertEqual(result['status'], 'failed')
        self.assertFalse(self.tool_calls)
        self.assertTrue(all(step['status'] == 'failed' for step in result['steps']))

    def test_snapshot_resource_and_declared_script_execute(self):
        snapshot = {'name': 'Script skill', 'entry': 'SKILL.md', 'files': {'SKILL.md': '# Script',
            'references/rule.txt': 'multiply by two', 'scripts/calc.py': 'def run(payload):\n    return {"total": payload["value"] * 2}\n'},
            'scripts': [{'path': 'scripts/calc.py'}]}
        class ScriptModel:
            def call(self, stage, payload, *args):
                if stage == 'review':
                    return {'decision': 'accept', 'answer': '脚本结果通过'}
                if not payload['steps']:
                    return {'type': 'tool', 'name': 'skill_resource', 'arguments': {'path': 'references/rule.txt'}}
                if len(payload['steps']) == 1:
                    return {'type': 'tool', 'name': 'skill_script', 'arguments': {'path': 'scripts/calc.py', 'input': {'value': 9}}}
                return {'type': 'finish', 'answer': '完成', 'resultStepId': payload['steps'][-1]['id'], 'artifactIds': []}
        self.s.model = ScriptModel()
        result = self.s.run_skill(self.owner, snapshot, '按规则计算', {}, {'schema': DELIVERY['schema']}, parent_run_id='p', node_id='n')
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['result'], {'total': 18})

    def test_owner_isolation_and_restart_never_replays(self):
        self.s.model = LoopModel(ask=True)
        first = self.run_skill()
        with self.assertRaises(Exception) as caught:
            self.s.store.run('different-owner', first['skillCallId'])
        self.assertEqual(caught.exception.status, 404)
        self.s.store = Store(self.temp.name)
        restarted = self.run_skill()
        self.assertEqual(restarted['status'], 'interrupted')
        self.assertFalse(self.tool_calls)

    def test_credential_inputs_and_unsafe_snapshot_paths_rejected(self):
        with self.assertRaises(Exception):
            self.s.run_skill(self.owner, SKILL, 'task', {'api_key': 'must-not-persist'}, {}, parent_run_id='p', node_id='n')
        invalid = {**SKILL, 'files': {'../SKILL.md': '# unsafe'}}
        with self.assertRaises(Exception):
            self.s.run_skill(self.owner, invalid, 'task', {}, {}, parent_run_id='p', node_id='n')

    def test_complete_component_snapshot_invokes_frozen_tool(self):
        from backend.app.modules.capability_library.components import snapshot_from_package
        tool_manifest = {'format': 'cd80.capability/1', 'kind': 'tool', 'name': 'frozen sum', 'description': '实际冻结求和',
            'source': {'identity': 'test/frozen-sum', 'format': 'platform'}, 'dependencies': [],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
            'tool': {'inputSchema': {'type': 'object', 'properties': {'values': {'type': 'array', 'items': {'type': 'number'}}},
                                    'required': ['values'], 'additionalProperties': False},
                     'execution': {'type': 'python_wasi', 'path': 'tool.py', 'callable': 'run'}}}
        tool = snapshot_from_package(tool_manifest, {'tool.py': b'def run(payload):\n    return {"total": sum(payload["values"])}\n'})
        skill_manifest = {'format': 'cd80.capability/1', 'kind': 'skill', 'name': 'frozen method', 'description': '冻结求和方法',
            'source': {'identity': 'test/frozen-method', 'format': 'platform'},
            'dependencies': [{'sourceIdentity': 'test/frozen-sum', 'contentDigest': tool['contentDigest'], 'required': True,
                              'reason': '使用固定计算实现'}],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
            'skill': {'entry': 'SKILL.md'}}
        skill = snapshot_from_package(skill_manifest, {'SKILL.md': b'---\nname: frozen-method\ndescription: Use the sum tool.\n---\nUse the frozen sum tool.'}, [tool])
        runtime, owner = self.s, self.owner
        class FrozenModel:
            def call(self, stage, payload, *args):
                if stage == 'review':
                    return {'decision': 'accept', 'answer': '真实冻结工具输出正确'}
                if not payload['steps']:
                    return {'type': 'tool', 'name': 'tool_1', 'arguments': {'values': [2, 7, 11]}}
                step = payload['steps'][0]
                for _ in range(2):
                    runtime._execution_event(owner, step['skillCallId'], {'stepId': step['id']},
                        {'type': 'tool_completed', 'skillCallId': step['skillCallId']})
                return {'type': 'finish', 'answer': '完成', 'resultStepId': payload['steps'][0]['id'], 'artifactIds': []}
        self.s.model = FrozenModel()
        result = self.s.run_skill(self.owner, skill, '按冻结方法求和', {}, {'schema': DELIVERY['schema']},
                                  parent_run_id='snapshot-parent', node_id='frozen')
        self.assertEqual(result['status'], 'succeeded', result)
        self.assertEqual(result['result'], {'total': 20})
        self.assertEqual(self.s.store.skill_snapshot(self.owner, result['skillCallId']), skill)
        with self.assertRaises(Exception) as wrong_owner:
            self.s.store.skill_snapshot('other-owner', result['skillCallId'])
        self.assertEqual(wrong_owner.exception.status, 404)
        evidence = self.s.execution_evidence(self.owner, result['skillCallId'], result['steps'][0]['id'])
        self.assertEqual(len(evidence['invocationIds']), 1)
        invocation = self.w.capabilities.store.get(self.owner, 'invocation', evidence['invocationIds'][0])
        self.assertEqual(invocation['input'], {'values': [2, 7, 11]})
        linked = self.s.execution_evidence(self.owner, result['skillCallId'], result['steps'][0]['id'])
        self.assertEqual(linked['skillCallIds'], [result['skillCallId']])
        events = self.s.store.events(self.owner, result['skillCallId'])
        linked_events = [e for e in events if e['payload'].get('skillCallId') == result['skillCallId']]
        self.assertEqual(len(linked_events), 2)

    def test_redacted_evidence_is_explicitly_not_reconstructable(self):
        from backend.app.modules.sub_agent.evidence import begin
        run = {'id': 'evidence-run'}
        action = {'type': 'tool', 'name': 'example', 'arguments': {'values': list(range(60)), 'api_key': 'private-value'}}
        begin(self.s, self.owner, run, {'id': 'evidence-step'}, action)
        with self.s.store.transaction() as db:
            record = self.s.store.get(db, self.owner, 'execution_evidence', 'evidence-step', True)
        self.assertFalse(record['reconstructable'])
        self.assertEqual(record['action']['arguments']['values'], list(range(60)))
        self.assertNotIn('private-value', json.dumps(record))

    def test_delivery_filename_and_evidence_field_checked_before_review(self):
        requirements = copy.deepcopy(DELIVERY)
        requirements['files'] = [{'name': 'missing.csv'}]
        self.s.model = LoopModel()
        result = self.s.run_skill(self.owner, SKILL, '完整交付', {}, requirements,
            parent_run_id='p', node_id='missing', invoke_tool=self.invoke)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(self.tool_calls), 1)
        self.assertNotIn('review', self.s.model.calls)
        requirements = {'schema': DELIVERY['schema'], 'evidenceFields': ['missing.source']}
        result = self.s.run_skill(self.owner, SKILL, '完整依据', {}, requirements,
            parent_run_id='p', node_id='evidence-field', invoke_tool=self.invoke)
        self.assertEqual(result['status'], 'failed')

    def test_skill_file_permissions_cannot_be_bypassed_by_temporary_code(self):
        skill = copy.deepcopy(SKILL)
        skill['permissions'] = {'inputFiles': False, 'outputFiles': False}
        with self.assertRaises(Exception) as error:
            self.s.run_skill(self.owner, skill, '输入文件受限', {}, {}, parent_run_id='p', node_id='input-denied',
                             files={'private.csv': b'value\n1\n'}, invoke_tool=self.invoke)
        self.assertEqual(error.exception.code, 'SKILL_FILE_NOT_GRANTED')
        self.assertFalse(self.tool_calls)
        result = self.s.run_skill(self.owner, skill, '临时代码输出文件受限', {}, DELIVERY,
                                  parent_run_id='p', node_id='output-denied', invoke_tool=self.invoke)
        self.assertEqual(result['status'], 'failed')
        run = self.s.store.run(self.owner, result['skillCallId'])
        self.assertEqual(run['correctionsUsed'], 3)
        self.assertFalse(any(a['name'] == 'report.csv' for a in run['artifacts']))
        self.assertTrue(any('临时代码不能绕过' in s.get('error', '') for s in run['steps']))

    def test_failed_invocation_is_linked_to_its_actual_step(self):
        from backend.app.modules.workbench.errors import GatewayError
        def failure(*args):
            error = GatewayError('实际工具失败。', 422)
            error.invocationId = 'actual-failed-invocation'
            raise error
        result = self.run_skill(invoke_tool=failure)
        self.assertEqual(result['status'], 'failed')
        for step in result['steps']:
            record = self.s.execution_evidence(self.owner, result['skillCallId'], step['id'])
            self.assertEqual(record['invocationIds'], ['actual-failed-invocation'])
            self.assertEqual(record['status'], 'failed')

    def test_failed_component_script_has_durable_invocation_link(self):
        from backend.app.modules.capability_library.components import snapshot_from_package
        manifest = {'format': 'cd80.capability/1', 'kind': 'skill', 'name': 'failed-script', 'description': 'Expected failure.',
            'source': {'identity': 'test/failed-script', 'format': 'platform'}, 'dependencies': [],
            'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
            'skill': {'entry': 'SKILL.md', 'scripts': [{'path': 'fail.py', 'type': 'python_wasi', 'callable': 'run'}]}}
        snapshot = snapshot_from_package(manifest, {'SKILL.md': b'---\nname: failed-script\ndescription: Run declared script.\n---\nRun fail.py.',
            'fail.py': b'def run(payload):\n    raise ValueError("expected fixture error")\n'})
        class FailedScriptModel:
            def call(self, stage, payload, *args):
                return {'type': 'tool', 'name': 'skill_script', 'arguments': {'path': 'fail.py', 'input': {}}}
        self.s.model = FailedScriptModel()
        result = self.s.run_skill(self.owner, snapshot, '实际执行失败脚本', {}, {}, parent_run_id='p', node_id='failure')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(result['steps']), 4)
        for step in result['steps']:
            record = self.s.execution_evidence(self.owner, result['skillCallId'], step['id'])
            self.assertEqual(len(record['invocationIds']), 1)
            invocation = self.w.capabilities.store.get(self.owner, 'invocation', record['invocationIds'][0])
            self.assertEqual(invocation['status'], 'failed')
            self.assertEqual(invocation['componentContext']['stepId'], step['id'])
            self.assertEqual(invocation['componentContext']['skillCallId'], result['skillCallId'])
            self.assertIn('raise ValueError', record['executedCode'])
            self.assertEqual(record['sourcePath'], 'fail.py')


if __name__ == '__main__':
    unittest.main()
