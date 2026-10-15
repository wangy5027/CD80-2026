"""Disposable unified-App acceptance: synthetic inputs, actual WASI, no model."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get('ASSET_APPS_BROWSER_PORT', '49007'))
sys.path.insert(0, str(ROOT))
for key in tuple(os.environ):
    if key.startswith('GITEE_AI_'):
        os.environ.pop(key, None)

from flask import jsonify
from werkzeug.serving import make_server
from backend.app import create_app
from tests.test_asset_apps import sample
from tests.test_asset_components import manifest
from backend.app.modules.capability_library.components import snapshot_from_package
from backend.app.modules.capability_library.contracts import digest

temp = tempfile.TemporaryDirectory(prefix='cd80-asset-apps-browser-')
app = create_app({'TESTING': True, 'DATA_DIR': temp.name, 'DESKTOP_PORT': PORT,
                  'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True})
w = app.extensions['workbench_service']
connections = {}
for account, title in [('author', '应用作者'), ('reader', '接收读者')]:
    user, _ = w.auth.register(f'asset-{account}@example.invalid', 'fixture-password', title)
    connections[account] = w.capabilities.create_connection(user['id'], {
        'name': title + '的隔离连接', 'sourceIdentity': 'fixture/asset-app-data',
        'transport': 'streamable_http', 'endpoint': 'http://127.0.0.1:1/mcp', 'authMode': 'none'})['connectionId']
model_attempts, external_attempts = [], []


def reject_model(*args, **kwargs):
    model_attempts.append('unexpected-model-call')
    raise AssertionError('This acceptance cannot access a model or upstream service')


w.sub_agents.model.call = reject_model
w.runtime.provider.events = reject_model
def reject_external(*args, **kwargs):
    external_attempts.append('unexpected-connector-call')
    raise AssertionError('Configuration acceptance never invokes the connector')


w.capabilities.mcp_client = reject_external
definition = sample()
definition['name'] = '浏览器合成价格方法'
definition['nodes'][0]['checkpoint'] = True
for node in definition['nodes']:
    node['provenance'] = {'type': 'generated', 'description': '验收预置源码，尚未实际验证；仅适用于合成价格'}
connection_manifest = manifest()
connection_manifest['name'] = '隔离外部数据契约'
connection_manifest['permissions']['network'] = 'connector_only'
connection_manifest['tool']['execution'] = {'type': 'mcp_http', 'connectionIdentity': 'fixture/asset-app-data',
                                          'toolName': 'read', 'toolSchemaDigest': 'a' * 64}
connection_definition = {'format': 'cd80.app/2', 'name': '只验收连接配置的草稿', 'description': '不执行外部服务',
    'inputSchema': connection_manifest['tool']['inputSchema'], 'rules': [], 'gaps': [],
    'nodes': [{'id': 'read', 'title': '读取外部数据', 'kind': 'tool',
               'component': snapshot_from_package(connection_manifest, {}),
               'inputBindings': {'n': {'from': 'input', 'path': 'n'}},
               'delivery': {'schema': connection_manifest['tool']['outputSchema']}}],
    'outputs': {'result': {'from': 'step', 'stepId': 'read', 'path': ''}}}
connection_package = {'format': 'cd80.app-package/2', 'definition': connection_definition,
                      'digest': digest(connection_definition)}


@app.get('/__fixture__/state')
def fixture_state():
    return jsonify({'definition': definition, 'modelAttempts': model_attempts, 'externalAttempts': external_attempts,
                    'connectionPackage': connection_package, 'connections': connections,
                    'synthetic': True, 'realModel': False, 'realWasi': True})


if __name__ == '__main__':
    server = make_server('127.0.0.1', PORT, app, threaded=True)
    print(f'Asset App fixture ready on {PORT}; disposable accounts, synthetic data, actual WASI', flush=True)
    try:
        server.serve_forever()
    finally:
        w.close()
        server.server_close()
        temp.cleanup()
