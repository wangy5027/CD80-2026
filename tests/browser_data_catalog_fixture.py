"""Temporary login UI reading the real local data service without write rights."""
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from flask import abort, request
from werkzeug.serving import make_server
from backend.app import create_app


if __name__ == '__main__':
    config = dotenv_values(ROOT / 'cd80-data-api/.env')
    read_key = os.environ.get('CD80_READ_API_KEY') or config.get('CD80_READ_API_KEY') or os.environ.get('CD80_ADMIN_API_KEY') or config.get('CD80_ADMIN_API_KEY') or ''
    with tempfile.TemporaryDirectory(prefix='data-catalog-ui-') as temp:
        app = create_app({'TESTING': True, 'DATA_DIR': temp, 'DESKTOP_PORT': 49021,
                          'FRONTEND_DIST': str(ROOT / 'frontend/dist'), 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                          'GITEE_AI_API_KEY': '', 'CD80_DATA_API_URL': 'http://127.0.0.1:8897',
                          'CD80_READ_API_KEY': read_key, 'CD80_ADMIN_API_KEY': '', 'CD80_DATA_ADMIN_USERS': ''})
        workbench = app.extensions['workbench_service']
        workbench.auth.register('catalog-reader@example.invalid', 'fixture-password', '目录验收')

        @app.before_request
        def readonly_data():
            if request.path.startswith('/desktop/api/financial-data/'):
                if request.method not in {'GET', 'HEAD'} and not (request.method == 'POST' and request.path.endswith('/query')):
                    abort(403)

        def stop(_signum, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server = make_server('127.0.0.1', 49021, app, threaded=True)
        print(json.dumps({'ready': True, 'port': 49021, 'isolatedAccount': True, 'readOnlyRealData': True}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            workbench.close()
