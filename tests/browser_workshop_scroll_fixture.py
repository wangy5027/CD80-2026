"""Isolated, model-free application-workshop pages for long-content scroll QA.

Run from the final project with ``.venv/bin/python -u tests/browser_workshop_scroll_fixture.py``.
The normal frontend and workshop routes serve two accounts: one stays at step 2,
the other has a long generated preview at step 3. No App is saved or executed.
"""

import copy
import json
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server

from backend.app import create_app
from backend.app.modules.agent_platform.contracts import validate_agent_spec
from backend.app.modules.agent_platform.planner import Planner
from tests.test_app_workshop_integration import Provider, generated_spec


HOST = '127.0.0.1'
PORT = 8895
QA_ROOT = ROOT / 'data' / 'integration-acceptance'
PASSWORD = 'Local-QA-2026!'
ANALYSIS_ACCOUNT = 'scroll-analysis@example.invalid'
PREVIEW_ACCOUNT = 'scroll-preview@example.invalid'


def paragraphs(topic, count):
    return ' '.join(
        f'{index + 1}．{topic}。请区分用户随后提供的输入、实际可计算的步骤与尚未验证的解释，'
        '逐项说明边界，避免把演示内容当成真实行情或已经运行的结果。'
        for index in range(count)
    )


def long_analysis():
    return {
        'analysis': {
            'understanding': '原来你想把自己提供的金额和价格示例整理成可复核的处理流程。' + paragraphs('理解合成输入', 10),
            'workflow': [
                {'title': f'处理环节 {index + 1}', 'description': paragraphs(f'第 {index + 1} 个环节的输入和输出', 3)}
                for index in range(8)
            ],
            'tools': [f'合成工具 {index + 1}：' + paragraphs('检查输入类型与步骤依赖', 1) for index in range(7)],
            'skills': [f'处理能力 {index + 1}：' + paragraphs('解释计算边界与结果来源', 1) for index in range(7)],
        },
        'recommendation': {
            'appKey': 'strategy-workshop',
            'reason': '策略工坊只能覆盖部分规则表达与诊断。' + paragraphs('推荐范围及未覆盖内容', 10),
            'covered': [f'可覆盖项 {index + 1}：' + paragraphs('已有规则处理能力', 1) for index in range(6)],
            'gaps': [f'仍需独立实现 {index + 1}：' + paragraphs('用户自带金额示例的处理', 1) for index in range(6)],
            'inputs': {},
        },
    }


def long_spec():
    spec = copy.deepcopy(generated_spec())
    spec['name'] = '合成金额长预览验收应用'
    spec['goal'] = '整理用户之后提供的合成金额。' + paragraphs('应用目标与结果边界', 5)
    spec['description'] = paragraphs('应用说明、输入示例、结果检查与限制', 10)
    spec['tools'][0]['description'] = paragraphs('实际计算步骤说明', 7)
    spec['pipeline'][1]['purpose'] = paragraphs('对实际计算值做来源说明', 7)
    spec['inputSchema']['properties']['amount']['description'] = paragraphs('由用户输入的金额', 3)
    spec['outputSchema'] = {'type': 'object', 'properties': {
        f'item{index + 1}': {'type': 'string', 'title': f'输出项目 {index + 1}',
                             'description': paragraphs('仅在运行后由结果解释生成', 2)}
        for index in range(4)
    }}
    return spec


class ScrollProvider(Provider):
    """Return deterministic long text while preserving the real planner contract."""

    def __init__(self):
        super().__init__()
        self.calls_by_stage = {'analysis': 0, 'composition': 0, 'generation': 0}

    def tokens(self, messages, model, stopped, on_response):
        system = messages[0]['content']
        if '应用工坊需求分析员' in system:
            self.calls_by_stage['analysis'] += 1
            result = long_analysis()
        elif '应用工坊组合规划员' in system:
            self.calls_by_stage['composition'] += 1
            payload = json.loads(messages[1]['content'])
            source = next((item for item in payload['apps'] if item['appKey'] == 'scroll-source@1'), None)
            result = {
                'sourceApps': ([{'appKey': source['appKey'],
                                'reason': paragraphs('复用已保存的合成金额计算实现', 7),
                                'reuse': [paragraphs(f'来源能力 {index + 1}', 1) for index in range(4)]}]
                               if source else []),
                'composition': {'summary': paragraphs('将来源计算和新解释组合成独立应用', 8),
                                'changes': [paragraphs(f'组合调整 {index + 1}', 1) for index in range(6)]},
            }
        else:
            self.calls_by_stage['generation'] += 1
            result = long_spec()
        yield json.dumps(result, ensure_ascii=False)


def require_ok(response, label):
    if response.status_code not in {200, 201}:
        raise RuntimeError(f'{label} failed: HTTP {response.status_code} {response.get_json()}')
    return response.get_json()


def seed_account(app, email, *, preview=False):
    client = app.test_client()
    require_ok(client.post('/desktop/api/register', json={
        'email': email, 'password': PASSWORD,
    }), f'register {email}')
    session = require_ok(client.post('/desktop/api/app-workshop/sessions', json={
        'requirement': ('我会自己提供合成金额和价格示例，请分析处理步骤与已有应用差距；'
                        '这些内容只用于应用工坊长页面滚动验收，不涉及真实投资数据。'),
        'idempotencyKey': f'scroll-analyze-{email}',
    }), f'analyze {email}')
    if preview:
        session = require_ok(client.post(f'/desktop/api/app-workshop/sessions/{session["id"]}/create', json={
            'revision': session['revision'], 'idempotencyKey': f'scroll-create-{email}',
        }), f'create {email}')
        if not session.get('appPreview', {}).get('sourceApps'):
            raise RuntimeError('preview has no source App section')
    else:
        if session['status'] != 'analyzed' or session.get('appPreview'):
            raise RuntimeError('analysis account did not remain at step 2')
    return session


def port_available():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, PORT))
        except OSError:
            return False
    return True


def main():
    if not port_available():
        print(f'Port {PORT} is already in use; fixture did not start or stop any process.', file=sys.stderr)
        return 2
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='workshop-scroll-', dir=QA_ROOT) as data_dir:
        app = create_app({'TESTING': True, 'DATA_DIR': data_dir, 'DESKTOP_PORT': PORT,
                          'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        workbench = app.extensions['workbench_service']
        platform = app.extensions['agent_platform']
        provider = ScrollProvider()
        platform.planner = Planner(provider)

        def unexpected_execution(*args, **kwargs):
            raise AssertionError('Scroll fixture must not invoke a real model or run an App')

        workbench.runtime.provider.events = unexpected_execution
        workbench.sub_agents.model.call = unexpected_execution
        platform.executor.execute = unexpected_execution
        try:
            analysis = seed_account(app, ANALYSIS_ACCOUNT)
            preview_user = require_ok(app.test_client().post('/desktop/api/register', json={
                'email': PREVIEW_ACCOUNT, 'password': PASSWORD,
            }), 'register preview account')
            owner = preview_user['user']['id']
            source = copy.deepcopy(generated_spec())
            source.update(id='scroll-source', name='已保存的合成金额来源应用',
                          description='仅供长预览来源说明使用的合成计算实现。')
            platform.repository.put_agent(owner, validate_agent_spec(source))

            # The account already exists; its own client logs in through the real route.
            client = app.test_client()
            require_ok(client.post('/desktop/api/login', json={
                'email': PREVIEW_ACCOUNT, 'password': PASSWORD,
            }), 'login preview account')
            preview = require_ok(client.post('/desktop/api/app-workshop/sessions', json={
                'requirement': '请把我以后输入的合成金额做两倍计算，复用我已有的金额来源应用，详细解释结果。',
                'idempotencyKey': 'scroll-preview-analyze',
            }), 'analyze preview account')
            preview = require_ok(client.post(f'/desktop/api/app-workshop/sessions/{preview["id"]}/create', json={
                'revision': preview['revision'], 'idempotencyKey': 'scroll-preview-create',
            }), 'create preview account')
            if not preview.get('appPreview', {}).get('sourceApps'):
                raise RuntimeError('preview has no source App section')
            if provider.calls_by_stage != {'analysis': 2, 'composition': 1, 'generation': 1}:
                raise RuntimeError(f'unexpected fixture model calls: {provider.calls_by_stage}')

            try:
                server = make_server(HOST, PORT, app, threaded=True)
            except OSError as error:
                print(f'Port {PORT} became unavailable: {error}', file=sys.stderr)
                return 2
            print(f'Workshop scroll fixture ready: http://{HOST}:{PORT}/desktop/', flush=True)
            print(f'PID {__import__("os").getpid()} | DATA_DIR {data_dir}', flush=True)
            print(f'Step 2 {ANALYSIS_ACCOUNT} session {analysis["id"]} | '
                  f'Step 3 {PREVIEW_ACCOUNT} session {preview["id"]}', flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        finally:
            workbench.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
