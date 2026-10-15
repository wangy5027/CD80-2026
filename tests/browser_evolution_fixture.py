"""Isolated Explore/Build/Evolve acceptance: real WASI, synthetic data and model."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('EVOLUTION_BROWSER_PORT', '49005'))
sys.path.insert(0, str(ROOT))
for key in tuple(os.environ):
    if key.startswith('GITEE_AI_'):
        os.environ.pop(key, None)

from flask import jsonify
from werkzeug.serving import make_server
from backend.app import create_app
from backend.app.modules.agent_platform.planner import Planner
from tests.test_agent_platform import FixtureProvider
from tests.sub_agent_fixture import seed, FixtureModel, MESSAGE
from backend.app.modules.capability_library import community

temp = tempfile.TemporaryDirectory(prefix='cd80-evolution-browser-')
app = create_app({'TESTING': True, 'DATA_DIR': temp.name, 'DESKTOP_PORT': PORT,
                  'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
w = app.extensions['workbench_service']
author, _ = w.auth.register('evolve-author@example.invalid', 'fixture-password', '研究作者')
reader, _ = w.auth.register('evolve-reader@example.invalid', 'fixture-password', '复用读者')
refs = seed(w.capabilities, author['id'])
w.sub_agents.model = FixtureModel(refs)
original_call = w.sub_agents.model.call


def call(stage, payload, *args, **kwargs):
    if stage == 'plan' and payload['message'].startswith('解释指标'):
        return {'mode': 'answer', 'answer': '本次实际输入：' + payload['message'], 'assessment': {'dependentProcessing': False}}
    if stage == 'act' and payload['message'].startswith('RECOVERY_TWO_STEPS '):
        successful = [step for step in payload['steps'] if step['type'] == 'code' and step['status'] == 'succeeded']
        if len(successful) == 1:
            prior = next(artifact for artifact in payload['artifacts'] if artifact['name'] == 'analysis-result.json')
            return {'type': 'code', 'reason': 'Summarize the prior successful analysis result.', 'inputRefs': [prior['id']],
                    'code': "import json\ndef run(payload):\n    prior = json.load(open(next(iter(payload['files'].values()))))\n    return {'summaryRows': prior['rows']}\n"}
    return original_call(stage, payload, *args)


w.sub_agents.model.call = call


def skill_events(*args, **kwargs):
    yield {'type': 'token', 'text': 'Use sample reader; calculate price change and drawdown; label synthetic. Fixture response for isolated acceptance.'}
    yield {'type': 'turn_end', 'tool_calls': []}


w.runtime.provider.events = skill_events
w.platform.planner = Planner(FixtureProvider())
kit = json.loads((ROOT / 'docs/vision-delivery/examples/csv-summary.application.json').read_text(encoding='utf-8'))


@app.get('/__fixture__/state')
def fixture_state():
    return jsonify({'message': MESSAGE, 'refs': refs, 'package': kit, 'pid': os.getpid()})


@app.post('/__fixture__/seed-catalog')
def seed_catalog():
    """Populate only this disposable fixture through the real publication service."""
    for index in range(101):
        body = {**refs[0], 'title': f'分页验收工具 {index + 1:03d}'}
        checked = community.preview(w.capabilities, author['id'], body)
        community.publish(w.capabilities, author['id'], {**body, 'snapshotDigest': checked['snapshotDigest'], 'confirmSharing': True})
    return jsonify({'seeded': 101})


if __name__ == '__main__':
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    print(f'Evolution fixture ready on {PORT}; model fixture, real WASI', flush=True)
    try:
        server.serve_forever()
    finally:
        w.close()
        server.server_close()
        temp.cleanup()
