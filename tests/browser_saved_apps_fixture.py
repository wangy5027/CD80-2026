"""Isolated browser QA for save-first Apps and trusted published capabilities."""
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))

from werkzeug.serving import make_server
from backend.app import create_app
from backend.app.modules.agent_platform.planner import Planner
from backend.app.modules.capability_library.official import copy_closure
from backend.app.modules.capability_library.service import PUBLIC
from backend.app.modules.capability_library.packages import zip_bytes
from backend.app.modules.capability_library.contracts import canonical
from test_app_workshop_integration import Provider
from test_workshop_service import matching_fixture
from test_workshop_customization import ready_reply


def main():
    root = ROOT / 'data' / 'integration-acceptance'
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='saved-app-ui-', dir=root) as data_dir:
        app = create_app({'TESTING': True, 'DATA_DIR': data_dir, 'DESKTOP_PORT': 8895, 'GITEE_AI_API_KEY': 'fixture-only',
                          'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime')})
        workbench = app.extensions['workbench_service']
        try:
            client = app.test_client()
            user = client.post('/desktop/api/register', json={'email': 'saved-app-qa@example.invalid',
                'password': 'Local-QA-2026!'}).json['user']['id']
            platform = app.extensions['agent_platform']
            platform.planner = Planner(Provider())
            service = app.extensions['app_workshop']
            session = service.analyze(user, {'requirement': '把我提供的金额乘二并解释', 'idempotencyKey': uuid4().hex})
            saved = service.create(user, session['id'], {'revision': session['revision'], 'idempotencyKey': uuid4().hex})
            strategy = app.extensions['strategy_workshop']
            strategy.matching.match = matching_fixture
            strategy.rewriting.prepare = lambda *args, **kwargs: ready_reply()
            session = strategy.create_session(user, {'idea': '短期十日均线高于长期三十日均线时持有，反之退出', 'idempotencyKey': uuid4().hex})
            strategy.customization(user, session['id'], {'matchRevision': session['matchRevision'],
                'selectionDigest': session['candidate']['selectionDigest'], 'idempotencyKey': uuid4().hex})
            library = app.extensions['capability_library']
            official = next(a for a in library.store.list(PUBLIC, 'asset') if a['sourceIdentity'] == 'cd80/cd80-history')
            version = library.version(PUBLIC, official['assetId'], official['latestVersionId'])
            copy_closure(library, user, version)
            # The same content uploaded privately must not inherit trust.
            manifest = dict(version['manifest'])
            manifest['name'] = '用户上传的历史查询 Skill'
            library.import_package(user, zip_bytes({**library.files(PUBLIC, version),
                'capability.json': canonical(manifest).encode()}), 'private-skill.zip', force_new=True)
            server = make_server('127.0.0.1', 8895, app, threaded=True)
            print('Saved App QA ready http://127.0.0.1:8895/desktop/ | synthetic account saved-app-qa@example.invalid', flush=True)
            print('Saved App', saved['savedAppId'], flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
        finally:
            workbench.close()


if __name__ == '__main__':
    main()
