"""Disposable test-case UX acceptance; actual WASI, no production account."""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flask import jsonify
from werkzeug.serving import make_server
from backend.app import create_app
from backend.app.modules.capability_library.components import snapshot_from_package, install_snapshot
from tests.test_asset_apps import sample
from tests.test_asset_components import manifest


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='cd80-original-cases-') as directory:
        app = create_app({'TESTING': True, 'DATA_DIR': directory, 'DESKTOP_PORT': 49011,
                          'GITEE_AI_API_KEY': 'fixture-only', 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                          'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime')})
        w = app.extensions['workbench_service']
        user, _ = w.auth.register('original-case@example.invalid', 'fixture-password', '原用例验收')
        doc = sample()
        doc['name'] = '原用例预填 APP'
        app_case = {'label': '原任务用例', 'input': {'prices': [100, 120, 90, 108]}, 'fileBindings': {},
                    'expectation': {'mode': 'json_equals', 'value': {'analysis': {'change': 8, 'drawdown': 25, 'count': 4}}}}
        saved_app = w.asset_apps.save(user['id'], {'definition': doc, 'testCases': [app_case]}, 'fixture-app')
        records = {'app': saved_app}
        for kind in ('tool', 'skill'):
            info = manifest(kind)
            info['name'] = '原用例预填 ' + kind.title()
            info['source']['identity'] = 'test/original-' + kind
            if kind == 'tool':
                files = {'original/tool.py': b'def run(payload):\n    return {"value":payload["n"]*2}\n'}
                case = {'label': '原任务用例', 'input': {'n': 7}, 'fileIds': [],
                        'expectation': {'mode': 'json_equals', 'value': {'value': 14}}}
            else:
                files = {'original/SKILL.md': b'---\nname: original-case-skill\ndescription: Summarize supplied facts.\n---\nSummarize only the supplied facts, without invention.\n'}
                case = {'label': '原任务用例', 'input': {'facts': ['项目已完成设计', '测试尚未开始']}, 'fileIds': [],
                        'expectation': {'mode': 'manual_review', 'prompt': '将这两条项目进展整理成简短说明。',
                                        'criteria': '须保留已完成设计、尚未测试两项事实；不得宣称测试通过。'}}
            records[kind] = install_snapshot(w.capabilities, user['id'], snapshot_from_package(info, files), test_cases=[case])

        @app.get('/__fixture__/state')
        def state():
            return jsonify(records)

        server = make_server('127.0.0.1', 49011, app, threaded=True)
        print(json.dumps({'port': 49011, 'isolated': True, 'realWasi': True}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
            w.close()
