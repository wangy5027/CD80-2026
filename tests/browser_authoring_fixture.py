"""Disposable unified-authoring UI fixture: model doubles, real WASI and stores."""
import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get('AUTHORING_BROWSER_PORT', '49010'))
for name in tuple(os.environ):
    if name.startswith('GITEE_AI_'):
        os.environ.pop(name, None)

from flask import jsonify
from werkzeug.serving import make_server
from backend.app import create_app
from backend.app.modules.agent_platform.asset_creator import _extract_definition
from tests.test_asset_apps import sample
from tests.test_authoring_adapters import plan

temp = tempfile.TemporaryDirectory(prefix='cd80-authoring-browser-')
app = create_app({'TESTING': True, 'DATA_DIR': temp.name, 'DESKTOP_PORT': PORT,
                  'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
w = app.extensions['workbench_service']
user, _ = w.auth.register('authoring-browser@example.invalid', 'fixture-password', '创作验收用户')
owner = user['id']


def create_candidate(owner, kind, instruction, *, content=None, **kwargs):
    doc = sample()
    doc['name'] = ('浏览器调整' if '浏览器调整' in instruction else '浏览器创建') + kind.title()
    if kind == 'app':
        return doc
    return _extract_definition({'name': doc['name'], 'description': doc['description'], 'definition': doc}, doc['nodes'], kind)


def test_plan(owner, prepared, **kwargs):
    value = plan('app' if prepared['kind'] == 'app' else 'tool')
    if prepared['kind'] == 'skill':
        for case in value['cases']:
            case['checks'].append({'id': 'review', 'mode': 'manual_review', 'requirement': '合成指标符合原要求',
                                    'payload': {'criteria': '确认价格变化与最大回撤说明符合预期'}})
    return value


def model(stage, payload, stopped, on_response, **kwargs):
    if stage == 'plan':
        message = payload.get('message', '')
        kind = 'skill' if 'skill' in message.lower() or '技能' in message else 'tool' if 'tool' in message.lower() or '工具' in message else 'app'
        return {'mode': 'authoring', 'authoring': {'kind': kind, 'intent': 'create'},
                'assessment': {'needsInformation': False, 'dependentProcessing': False, 'explicitApplication': False}}
    if stage == 'approve':
        return {'decision': 'approve', 'reason': '仅处理合成授权输入。'}
    if stage == 'review':
        return {'decision': 'accept', 'answer': '真实隔离执行结果符合合成指标。'}
    completed = [s for s in payload.get('steps', []) if s.get('status') == 'succeeded' and s['type'] == 'code']
    if completed:
        return {'type': 'finish', 'answer': '已计算实际合成指标。', 'resultStepId': completed[-1]['id'], 'artifactIds': []}
    original = next(o for o in payload['observations'] if 'inputArtifact' in o)
    ref = original['inputArtifact']['id']
    code = ('import json\ndef run(payload):\n'
            f'    with open(payload["files"][{ref!r}]) as f: prices=json.load(f)["prices"]\n'
            '    peak=prices[0]\n    dd=0\n    for p in prices:\n        peak=max(peak,p)\n        dd=max(dd,1-p/peak)\n'
            '    return {"change":round((prices[-1]/prices[0]-1)*100,8),"drawdown":round(dd*100,8),"count":len(prices)}\n')
    return {'type': 'code', 'purpose': '合成价格实际计算', 'reason': '在本候选约定范围内计算', 'inputRefs': [ref], 'code': code}


def never_model(*args, **kwargs):
    raise AssertionError('Real upstream calls are disabled in this disposable fixture')


w.authoring.adapters.create = create_candidate
w.authoring.adapters.plan = test_plan
w.sub_agents.model.call = model
w.runtime.provider.events = never_model
w.platform.planner.provider.events = never_model


@app.get('/__fixture__/state')
def state():
    return jsonify({'synthetic': True, 'realModel': False, 'realWasi': bool(w.platform.executor.status()['available']),
                    'sessions': w.authoring.listing(owner)['items']})


if __name__ == '__main__':
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    print(f'Authoring fixture ready on {PORT}; disposable stores, real WASI, no model network', flush=True)
    try:
        server.serve_forever()
    finally:
        w.close()
        server.server_close()
        temp.cleanup()
