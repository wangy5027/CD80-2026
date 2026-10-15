"""The code example sent to the sub-agent must run with its actual WASI input."""

import json
import threading
import unittest

from backend.app.modules.agent_platform.executor import SandboxError, SandboxExecutor
from backend.app.modules.sub_agent.model import CODE_INPUT_EXAMPLE, Model, PROMPTS


class SubAgentCodeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = SandboxExecutor()
        status = cls.executor.status()
        if not status['available']:
            raise RuntimeError('Real WASI runtime required: ' + status['reason'])

    def test_prompt_example_reads_three_complete_tool_results(self):
        self.assertIn(CODE_INPUT_EXAMPLE, PROMPTS['act'])
        self.assertIn('payload["files"]', PROMPTS['act'])
        self.assertIn('完整绝对路径', PROMPTS['act'])

        counts = (100, 100, 43)
        refs = ['a' * 32, 'b' * 32, 'c' * 32]
        payload = {'files': {ref: '/input/' + ref + '.json' for ref in refs}}
        files = {}
        for page, (ref, count) in enumerate(zip(refs, counts)):
            rows = [{'page': page, 'row': index} for index in range(count)]
            files[ref + '.json'] = json.dumps({'content': {'records': rows}}).encode()

        result = self.executor.execute(CODE_INPUT_EXAMPLE, payload, files)
        self.assertEqual(result['result'], {'rowCount': 243})

    def test_previous_attribute_style_fails_with_real_dict_payload(self):
        with self.assertRaises(SandboxError) as caught:
            self.executor.execute('def run(payload):\n    return {"count": len(payload.files)}', {'files': {}}, {})
        self.assertIn('AttributeError', str(caught.exception) + caught.exception.logs)

    def test_plan_and_act_requests_both_receive_market_and_file_grant_rules(self):
        class Provider:
            model = 'local-prompt-fixture'

            def __init__(self, answer):
                self.answer = answer
                self.system_prompt = None

            def json_tokens(self, messages, model, stopped, on_response):
                self.system_prompt = messages[0]['content']
                yield json.dumps(self.answer, ensure_ascii=False)

            def tokens(self, *_args):
                raise AssertionError('Structured stages must use json_tokens')

        responses = {
            'plan': {'mode': 'chat'},
            'act': {'type': 'ask', 'question': '需要哪一列？'},
        }
        for stage, answer in responses.items():
            with self.subTest(stage=stage):
                provider = Provider(answer)
                self.assertEqual(Model(provider).call(stage, {'message': 'synthetic'},
                    threading.Event(), lambda _response: None), answer)
                prompt = provider.system_prompt
                self.assertIn('code应使用证券目录中含市场后缀的完整代码', prompt)
                self.assertIn('未知市场先用证券搜索核实', prompt)
                self.assertIn('不接收输入文件的Tool必须填[]', prompt)
                self.assertIn('分页cursor或前步结果字段放在arguments', prompt)
                self.assertIn('不能仅为表示步骤依赖就把前步tool-result.json放进inputRefs', prompt)
                self.assertIn('code动作需把本次真实读取的工件全部列入inputRefs', prompt)


if __name__ == '__main__':
    unittest.main()
