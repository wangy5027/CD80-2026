"""Disposable accounts and a deterministic model; only official data is read live."""
import json
from pathlib import Path
import signal
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from flask import abort, jsonify, request
from werkzeug.serving import make_server
from backend.app import create_app


class DataModel:
    def __init__(self):
        self.calls = []

    def call(self, stage, payload, stopped, on_response):
        assert stage == 'plan', stage
        artifacts = payload.get('artifacts', [])
        inputs = [a for a in artifacts if a.get('sourceRefs')]
        assert inputs, 'No selected input file reached the planner'
        for item in inputs:
            assert item.get('preview'), 'No readable preview reached the planner'
        self.calls.append({'names': [a['name'] for a in inputs],
                           'fileIds': [a['sourceRefs'][0] for a in inputs],
                           'sizes': [a['size'] for a in inputs],
                           'previews': [a['preview'] for a in inputs]})
        return {'mode': 'answer', 'answer': '已引用所选数据文件：' + '、'.join(a['name'] for a in inputs) + '。本次收到有界数据预览。'}


if __name__ == '__main__':
    env = dotenv_values(ROOT / 'cd80-data-api/.env')
    key = env.get('CD80_READ_API_KEY') or env.get('CD80_ADMIN_API_KEY') or ''
    with tempfile.TemporaryDirectory(prefix='personal-data-browser-') as temp:
        app = create_app({'TESTING': True, 'DATA_DIR': temp, 'DESKTOP_PORT': 49023,
                          'FRONTEND_DIST': str(ROOT / 'frontend/dist'), 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                          'GITEE_AI_API_KEY': 'fixture-only-no-network',
                          'CD80_DATA_API_URL': 'http://127.0.0.1:8897',
                          'CD80_READ_API_KEY': key, 'CD80_ADMIN_API_KEY': '', 'CD80_DATA_ADMIN_USERS': ''})
        workbench = app.extensions['workbench_service']
        for email in ['data-owner@example.invalid', 'data-other@example.invalid']:
            workbench.auth.register(email, 'fixture-password', '数据上传验收')
        model = DataModel()
        app.extensions['sub_agents'].model = model

        @app.before_request
        def readonly_official_data():
            if request.path.startswith('/desktop/api/financial-data/'):
                if request.method not in {'GET', 'HEAD'} and not (request.method == 'POST' and request.path.endswith('/query')):
                    abort(403)

        @app.get('/__personal_data_fixture__/state')
        def state():
            return jsonify({'fixture': True, 'temporaryAccounts': True, 'realModel': False,
                            'calls': model.calls})

        def stop(_signal, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server = make_server('127.0.0.1', 49023, app, threaded=True)
        print(json.dumps({'ready': True, 'port': 49023, 'realModel': False}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            workbench.close()
