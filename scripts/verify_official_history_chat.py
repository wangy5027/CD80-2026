"""Submit one real main-conversation message against the isolated official App.

Run explicitly with --run-live after build_official_history_app.py.  The
isolated account and data directory come from Git-ignored .run metadata.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'official-history-app'
PRIVATE_RUNTIME = ROOT / '.run' / 'official-history-app-runtime.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', help='explicitly call the model and local read-only data service')
    args = parser.parse_args()
    if not args.run_live:
        parser.error('pass --run-live to submit the real main conversation')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    private = json.loads(PRIVATE_RUNTIME.read_text(encoding='utf-8'))
    acceptance = json.loads((EXAMPLE / 'acceptance.json').read_text(encoding='utf-8'))
    if private.get('appId') != acceptance['app']['appId'] or private.get('versionId') != acceptance['app']['versionId']:
        raise RuntimeError('The isolated runtime pointer does not match the latest App acceptance')
    text = (EXAMPLE / '自然语言调用.txt').read_text(encoding='utf-8').splitlines()[0]
    sys.path.insert(0, str(ROOT))
    from backend.app import create_app
    from backend.app.modules.agent_platform.authoring_chat import app_catalog
    app = create_app({'TESTING': True, 'DATA_DIR': private['dataDir'], 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                      'GITEE_AI_API_KEY': os.environ['GITEE_AI_API_KEY'], 'GITEE_AI_MODEL': 'GLM-5.3-Flash',
                      'AGENT_MODEL_MAX_TOKENS': 12288,
                      'GITEE_AI_BASE_URL': os.environ.get('GITEE_AI_BASE_URL', 'https://ai.gitee.com/v1'),
                      'CD80_READ_API_KEY': os.environ['CD80_READ_API_KEY'], 'CD80_ADMIN_API_KEY': '',
                      'CD80_DATA_API_URL': 'http://127.0.0.1:8897',
                      'AGENT_RUNTIME_DIR': str(ROOT / 'data' / 'agent-runtime')})
    try:
        client = app.test_client()
        login = client.post('/desktop/api/login', json={'email': private['email'], 'password': private['password']})
        if login.status_code != 200:
            raise RuntimeError(f'Isolated QA login returned {login.status_code}')
        owner = login.json['user']['id']
        catalog = app_catalog(app.extensions['workbench_service'], owner)
        expected = acceptance['app']
        if not any(x['assetId'] == expected['appId'] and x['versionId'] == expected['enabledVersionId'] for x in catalog):
            raise RuntimeError('The exact App version is not enabled in this isolated runtime')
        submitted = client.post('/desktop/api/conversations/new/messages',
                                json={'message': text, 'searchMode': 'off'},
                                headers={'Idempotency-Key': 'official-history-main-chat-' + uuid4().hex})
        if submitted.status_code != 202:
            raise RuntimeError(f'Main conversation submission returned {submitted.status_code}')
        run_id = submitted.json['id']
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            run = app.extensions['sub_agents'].store.run(owner, run_id)
            if run['status'] in {'succeeded', 'failed', 'partial', 'interrupted', 'cancelled', 'awaiting_user'}:
                break
            time.sleep(.5)
        else:
            raise TimeoutError('Main conversation did not reach a terminal state')
        evidence = {'subAgentRunId': run_id, 'conversationId': run['conversationId'],
                    'status': run['status'], 'kind': run.get('kind'),
                    'assetAppId': run.get('assetAppId'), 'assetAppVersionId': run.get('assetAppVersionId'),
                    'assetAppRunId': run.get('assetAppRunId'),
                    'modelCalls': [{'callId': c['id'], 'stage': c['stage'], 'status': c['status']}
                                   for c in run.get('modelCalls', [])],
                    'answer': run.get('answer') or run.get('finalAnswer') or (run.get('completion') or {}).get('answer'),
                    'error': run.get('error')}
        if evidence['kind'] != 'asset_app' or evidence['assetAppId'] != expected['appId'] or evidence['assetAppVersionId'] != expected['enabledVersionId']:
            raise RuntimeError('The real main conversation did not select the exact enabled App')
        app_run_id = evidence['assetAppRunId']
        while time.monotonic() < deadline:
            app_run = app.extensions['asset_apps'].run(owner, app_run_id)
            if app_run['status'] not in {'queued', 'running'}:
                break
            time.sleep(.5)
        else:
            raise TimeoutError('App run from the main conversation did not finish')
        result = (app_run.get('result') or {}).get('summary') or {}
        evidence['appRun'] = {'runId': app_run_id, 'status': app_run['status'],
                              'input': app_run.get('input'),
                              'stepStatuses': [s.get('status') for s in app_run.get('steps', [])],
                              'source': result.get('source'), 'close': result.get('close'),
                              'marketCompleteness': result.get('marketCompleteness'), 'error': app_run.get('error')}
        if app_run['status'] != 'succeeded' or result.get('source') != 'cd80_daily' or result.get('close') != '14.58':
            raise RuntimeError('The main-conversation App run did not return the accepted real-data result')
        (EXAMPLE / 'chat-acceptance.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n',
                                                       encoding='utf-8')
        print(json.dumps({'status': 'passed', 'subAgentRunId': run_id, 'appRunId': app_run_id,
                          'output': str(EXAMPLE / 'chat-acceptance.json')}, ensure_ascii=False))
    finally:
        app.extensions['workbench_service'].close()


if __name__ == '__main__':
    main()
