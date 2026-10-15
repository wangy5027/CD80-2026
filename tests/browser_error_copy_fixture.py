"""Isolated UI error-copy acceptance, no production data or model calls."""
import json
import os
import signal
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
for key in tuple(os.environ):
    if key.startswith('GITEE_AI_'):
        os.environ.pop(key, None)
from werkzeug.serving import make_server
from backend.app import create_app
from backend.app.modules.sub_agent.store import now

PORT = 8895
parent = ROOT / 'data/integration-acceptance'
parent.mkdir(parents=True, exist_ok=True)
data = Path(tempfile.mkdtemp(prefix='error-copy-', dir=parent))
app = create_app({'TESTING': True, 'DATA_DIR': str(data), 'DESKTOP_PORT': PORT,
                  'GITEE_AI_API_KEY': '', 'IMPORT_LEGACY_HISTORY': False,
                  'CAPABILITY_SKIP_PUBLIC_SEED': True, 'CD80_ADMIN_API_KEY': '',
                  'CD80_READ_API_KEY': '', 'SFC_TOKEN': '', 'SFC_KEY_FILE': '',
                  'CAPABILITY_MCP_POLICY_FILE': ''})
w = app.extensions['workbench_service']
client = app.test_client()
registered = client.post('/desktop/api/register', json={'email': 'error-copy-qa@example.invalid', 'password': 'Local-Error-QA-2026!'})
assert registered.status_code in (200, 201)
owner = registered.json['user']['id']
task = {'id': 'error-copy-history', 'title': '错误提示验收', 'agentId': 'assistant',
        'modelId': w.model_id, 'projectId': '', 'status': 'done',
        'error': 'INTERNAL_PRIVATE_TRACE upstream provider timeout at /private/fixture.py:101',
        'messages': [{'role': 'user', 'content': '这是合成测试问题。'},
                     {'role': 'assistant', 'content': '正常内容应该保留。', 'capabilityUsages': [
                         {'type': 'tool_failed', 'name': 'example_tool', 'message': 'INTERNAL_PRIVATE_TRACE tool failure'}]}],
        'createdAt': now(), 'updatedAt': now()}
w.store.update(owner, lambda state: state['tasks'].append(task))
invalid = ROOT / '.run/error-copy-invalid.zip'
with zipfile.ZipFile(invalid, 'w') as archive:
    archive.writestr('readme.txt', 'Synthetic malformed import; no app manifest.')
(ROOT / '.run/error-copy-runtime.json').write_text(json.dumps({'dataDir': str(data), 'port': PORT, 'owner': owner}, ensure_ascii=False))
signal.signal(signal.SIGINT, signal.default_int_handler)
server = make_server('127.0.0.1', PORT, app, threaded=True)
print('Error-copy isolated fixture ready', flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
    w.close()
