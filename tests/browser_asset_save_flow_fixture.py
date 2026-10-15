"""Disposable save-only UI fixture; historical code is executed before UI starts."""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PORT = int(os.environ.get('ASSET_SAVE_BROWSER_PORT', '49009'))
for key in tuple(os.environ):
    if key.startswith('GITEE_AI_'):
        os.environ.pop(key, None)

from flask import jsonify
from werkzeug.serving import make_server
from backend.app import create_app
from backend.app.modules.sub_agent.store import canonical, digest, now
from tests.test_asset_apps import sample

temp = tempfile.TemporaryDirectory(prefix='cd80-save-flow-browser-')
app = create_app({'TESTING': True, 'DATA_DIR': temp.name, 'DESKTOP_PORT': PORT,
                  'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
w = app.extensions['workbench_service']
user, _ = w.auth.register('save-author@example.invalid', 'fixture-password', '保存验收作者')
owner, cid, rid = user['id'], 'save-flow-conversation', 'save-flow-partial'
task_title = '合成成功步骤，原图表未交付'
steps, artifacts, records, byte_rows, executed = [], [], [], [], []
for index in (1, 2, 3):
    sid, aid, code_id = f'actual-{index}', f'result-{index}', f'code-{index}'
    code = ('# complete historical source retained for save-only acceptance\n' * 350
            + f'def run(payload):\n    return {{"value": {index}}}\n')
    result = w.platform.executor.execute(code, {}, {})
    assert result['result'] == {'value': index}
    executed.append(result['result'])
    raw = canonical(result['result']).encode()
    metadata = {'id': aid, 'runId': rid, 'stepId': sid, 'name': 'tool-result.json',
                'sha256': digest(raw), 'size': len(raw), 'sourceRefs': [], 'artifactRole': 'execution_result'}
    code_meta = {**metadata, 'id': code_id, 'name': 'analysis.py', 'size': len(code.encode()), 'sha256': digest(code.encode())}
    step = {'id': sid, 'type': 'code', 'status': 'succeeded', 'evidenceId': sid, 'inputRefs': [],
            'purpose': f'合成固定计算 {index}', 'result': {'codeArtifactId': code_id}, 'startedAt': now(), 'completedAt': now()}
    steps.append(step); artifacts.extend([metadata, code_meta]); byte_rows.extend([(metadata, raw), (code_meta, code.encode())])
    records.append({'id': sid, 'runId': rid, 'stepId': sid, 'status': 'succeeded', 'adopted': None,
                    'adoptionBasis': 'no-explicit-output-dependency', 'reconstructable': True,
                    'resultArtifactId': aid, 'invocationIds': [], 'skillCallIds': [],
                    'action': {'type': 'code', 'code': code, 'inputRefs': []}})
run = {'id': rid, 'conversationId': cid, 'revision': 1, 'kind': 'delegate', 'status': 'partial',
       'message': '请计算合成值并交付图表。', 'answer': '已完成三项合成计算，图表未交付。', 'payload': {},
       'completion': {'status': 'partial', 'missingDeliverables': ['图表'], 'reasons': ['用户要求的图表尚未交付。']},
       'steps': steps, 'artifacts': artifacts, 'deliveries': [], 'loadedRefs': [], 'createdAt': now(), 'updatedAt': now()}
task = {'id': cid, 'title': task_title, 'agentId': 'assistant', 'modelId': w.model_id, 'projectId': '', 'status': 'idle',
        'messages': [{'role': 'user', 'content': run['message'], 'subAgentMessageId': rid + ':user'},
                     {'role': 'assistant', 'content': run['answer'], 'subAgentMessageId': rid + ':assistant'}],
        'createdAt': now(), 'updatedAt': now()}
w.store.update(owner, lambda state: state['tasks'].append(task))
with w.sub_agents.store.transaction() as db:
    w.sub_agents.store.put(db, owner, 'run', rid, run)
    w.sub_agents.store.put(db, owner, 'queue', cid, {'id': cid, 'revision': 1, 'paused': False, 'activeRunId': None, 'items': [rid]})
    for record in records:
        w.sub_agents.store.put(db, owner, 'execution_evidence', record['stepId'], record)
    for meta, raw in byte_rows:
        db.execute('INSERT INTO artifacts VALUES(?,?,?,?,?)', (owner, meta['id'], rid, canonical(meta), raw))

attempts = []
def reject_execution(*args, **kwargs):
    attempts.append('unexpected-execution')
    raise AssertionError('Saving must never call a model, executor, or external connector')

w.sub_agents.model.call = reject_execution
w.runtime.provider.events = reject_execution
w.platform.executor.execute = reject_execution
w.capabilities.executor.execute = reject_execution
w.capabilities.mcp_client = reject_execution
definition = sample()
definition['name'] = '直接保存的合成应用'

@app.get('/__fixture__/state')
def state():
    return jsonify({'definition': definition, 'taskTitle': task_title, 'sourceRunId': rid,
                    'seedExecutedResults': executed, 'executionAttemptsAfterSeed': attempts,
                    'synthetic': True, 'realUpstream': False})

if __name__ == '__main__':
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    print(f'Save-only fixture ready on {PORT}; isolated account and source evidence; execution now forbidden', flush=True)
    try:
        server.serve_forever()
    finally:
        w.close(); server.server_close(); temp.cleanup()
