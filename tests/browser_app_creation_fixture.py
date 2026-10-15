"""Isolated browser fixture for the two App creation paths; never executes an App."""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real-model', action='store_true', required=True)
    args = parser.parse_args()
    from dotenv import dotenv_values
    from werkzeug.serving import make_server
    from backend.app import create_app

    configured = dotenv_values(ROOT / '.env')
    config = {key: os.environ.get(key) or value for key, value in configured.items()
              if key.startswith('GITEE_AI_') and value}
    if not config.get('GITEE_AI_API_KEY'):
        raise SystemExit('No configured model key; no requests sent.')
    parent = ROOT / 'data/integration-acceptance'
    parent.mkdir(parents=True, exist_ok=True)
    directory = tempfile.mkdtemp(prefix='app-two-entry-', dir=parent)
    config.update(TESTING=True, DATA_DIR=directory, DESKTOP_PORT=8895,
                  IMPORT_LEGACY_HISTORY=False, CAPABILITY_SKIP_PUBLIC_SEED=True,
                  CD80_ADMIN_API_KEY='', CD80_READ_API_KEY='', SFC_TOKEN='',
                  SFC_KEY_FILE='', CAPABILITY_MCP_POLICY_FILE='')
    app = create_app(config)
    w = app.extensions['workbench_service']
    counts = {'plan': 0, 'validate': 0, 'appRun': 0}

    def forbid(key):
        def reject(*_args, **_kwargs):
            counts[key] += 1
            raise AssertionError('Creation must not execute: ' + key)
        return reject

    w.authoring.adapters.plan = forbid('plan')
    w.authoring.adapters.validate = forbid('validate')
    app.extensions['asset_apps']._new_run = forbid('appRun')
    try:
        client = app.test_client()
        response = client.post('/desktop/api/register', json={
            'email': 'app-creation-qa@example.invalid', 'password': 'Local-Creation-QA-2026!'})
        assert response.status_code in (200, 201), response.status_code
        pointer = ROOT / '.run/app-creation-qa-runtime.json'
        pointer.write_text(json.dumps({'dataDir': directory, 'port': 8895,
            'owner': response.json['user']['id']}, ensure_ascii=False))
        pointer.chmod(0o600)
        server = make_server('127.0.0.1', 8895, app, threaded=True)
        print('Isolated App creation UI ready at http://127.0.0.1:8895/desktop/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    finally:
        (ROOT / '.run/app-creation-execution-guards.json').write_text(json.dumps(counts, indent=2))
        w.close()


if __name__ == '__main__':
    main()
