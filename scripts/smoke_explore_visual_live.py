"""Capture the actual Explore theme with configured live search/news/model services.

Uses a disposable account/database. Cookies pass to the recorder through stdin
and are never written to disk. The recording may consume existing model quota.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import json
import secrets
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/explore-visual/live')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    import requests
    from werkzeug.serving import make_server, WSGIRequestHandler
    from backend.app import create_app
    from backend.app.config import Config
    if not Config.GITEE_AI_API_KEY:
        raise RuntimeError('No configured model credential; no real request performed.')
    args.output.mkdir(parents=True, exist_ok=True)

    class Quiet(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix='explore-visual-live-') as temp:
        app = create_app({'DATA_DIR': temp, 'IMPORT_LEGACY_HISTORY': False,
                          'AGENT_RUNTIME_DIR': str(Path(temp) / 'no-runtime'),
                          'FRONTEND_DIST': str(ROOT / 'frontend/dist')})
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=Quiet)
        app.config['DESKTOP_PORT'] = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True).start()
        origin = f'http://127.0.0.1:{server.server_port}'
        client = requests.Session()
        client.trust_env = False
        try:
            registered = client.post(origin + '/desktop/api/register', json={
                'name': 'Explore 视觉真实验收', 'email': 'visual-live@example.invalid',
                'password': secrets.token_urlsafe(24)}, timeout=15)
            registered.raise_for_status()
            result = subprocess.run(['node', str(ROOT / 'scripts/capture_explore_visual_live.mjs')],
                                    input=json.dumps({'origin': origin, 'output': str(args.output.resolve()),
                                        'cookies': [{'name': c.name, 'value': c.value, 'url': origin} for c in client.cookies]}),
                                    text=True, timeout=650, check=False)
            return result.returncode
        finally:
            server.shutdown()
            server.server_close()
            close = getattr(app.extensions.get('workbench_service'), 'close', None)
            if close:
                close()


if __name__ == '__main__':
    raise SystemExit(main())
