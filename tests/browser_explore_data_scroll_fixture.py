"""Disposable Explore file-pool and financial-data dialog scroll fixture.

Starts the real Flask frontend/API on 8896 and the real data FastAPI on 8898.
Every row, file, account and database is synthetic and isolated under
data/integration-acceptance. No model or formal financial service is called.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server

from backend.app import create_app
from backend.app.modules.explore.repository import ExploreRepository, file_id_for, timestamp
from tests.financial_browser_fixture import _start_data_server, ADMIN_KEY, READ_KEY


HOST = '127.0.0.1'
SITE_PORT = 8896
DATA_PORT = 8898
QA_ROOT = ROOT / 'data' / 'integration-acceptance'
ACCOUNT = 'scroll-explore-data@example.invalid'
PASSWORD = 'Local-QA-2026!'
TABLE = 'cd80_scroll_sample'


def available(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def synthetic_database(path):
    """Create a long row and many editable fields without reading source data."""
    fields = [('code', 'TEXT', '演示编号'), ('quantity', 'INTEGER', '演示数量，应为非负整数')]
    fields += [(f'detail_{index:02d}', 'TEXT', f'合成描述字段 {index:02d}；用于长行详情与新增表单滚动')
               for index in range(1, 23)]
    column_sql = ',\n'.join(f'{name} {kind}' for name, kind, _ in fields)
    with sqlite3.connect(path) as db:
        db.executescript(f'''
            CREATE TABLE _datasets (
                name TEXT PRIMARY KEY, title TEXT NOT NULL, row_count INTEGER NOT NULL,
                source_rows INTEGER NOT NULL, source_file TEXT NOT NULL,
                source_sha256 TEXT NOT NULL, encoding TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE _columns (
                table_name TEXT NOT NULL, name TEXT NOT NULL, ordinal INTEGER NOT NULL,
                data_type TEXT NOT NULL, logical_type TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '', PRIMARY KEY(table_name,name)
            );
            CREATE TABLE {TABLE} (
                _row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                {column_sql},
                CHECK(quantity >= 0)
            );
            CREATE TRIGGER {TABLE}_insert AFTER INSERT ON {TABLE}
            BEGIN UPDATE _datasets SET row_count=row_count+1 WHERE name='{TABLE}'; END;
            CREATE TRIGGER {TABLE}_delete AFTER DELETE ON {TABLE}
            BEGIN UPDATE _datasets SET row_count=row_count-1 WHERE name='{TABLE}'; END;
        ''')
        db.executemany('INSERT INTO _columns VALUES(?,?,?,?,?,?)', [
            (TABLE, name, index, kind, 'number' if kind == 'INTEGER' else 'text', description)
            for index, (name, kind, description) in enumerate(fields)
        ])
        db.execute('INSERT INTO _datasets VALUES(?,?,?,?,?,?,?,?)', (
            TABLE, '滚动验收合成表', 0, 1, 'fixture://synthetic-only',
            hashlib.sha256(b'scroll-fixture-v1').hexdigest(), 'utf-8', timestamp()))
        values = ['SCROLL-001', 3] + [
            (f'合成字段 {index:02d}：本行只用于核对长内容能否在弹窗内完整滚动。'
             '它不是行情、交易或用户记录；新增表单可以把数量填为无效文本以观察验证提示。' * 2)
            for index in range(1, 23)
        ]
        db.execute(f'INSERT INTO {TABLE}({",".join(name for name, _, _ in fields)}) '
                   f'VALUES({",".join("?" for _ in fields)})', values)
        return len(fields)


def require_ok(response, label):
    if response.status_code not in {200, 201}:
        raise RuntimeError(f'{label} failed: HTTP {response.status_code} {response.get_json()}')
    return response.get_json()


def seed_explore(app, owner, client):
    session = require_ok(client.post('/desktop/api/explore/sessions', json={
        'title': '文件池滚动验收：合成材料',
    }), 'create Explore session')['session']
    repository = app.extensions['explore_service'].repository
    with repository.transaction() as db:
        for index in range(1, 12):
            origin_key = f'scroll-fixture:{session["id"]}:{index}'
            ExploreRepository._insert_file(
                db, file_id=file_id_for(origin_key), owner=owner, session_id=session['id'],
                origin_key=origin_key, kind='material', origin='user',
                name=f'合成材料-{index:02d}-长文件正文.md',
                content=('# 合成材料\n\n' +
                         '\n'.join(f'{line}. 第 {index} 份材料的可滚动正文，只用于本地界面验收。'
                                   for line in range(1, 35))),
                metadata={'content_kind': 'synthetic_fixture', 'url': None,
                          'published_at': None, 'retrieved_at': timestamp()},
                created_at=timestamp(),
            )
    files = require_ok(client.get(f'/desktop/api/explore/sessions/{session["id"]}/files'),
                       'read Explore file pool')['files']
    if len(files) != 11:
        raise RuntimeError('Explore file pool was not populated')
    return session, files


def main():
    for port in (SITE_PORT, DATA_PORT):
        if not available(port):
            print(f'Port {port} is already in use; no existing process was stopped.', file=sys.stderr)
            return 2
    QA_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='explore-data-scroll-', dir=QA_ROOT) as directory:
        root = Path(directory)
        db_path = root / 'synthetic.sqlite3'
        field_count = synthetic_database(db_path)
        listener = api_server = api_thread = website_server = website = None
        try:
            listener, api_server, api_thread = _start_data_server(db_path, DATA_PORT)
            website = create_app({
                'TESTING': True, 'DESKTOP_PORT': SITE_PORT,
                'DATA_DIR': str(root / 'accounts'), 'FRONTEND_DIST': str(ROOT / 'frontend' / 'dist'),
                'AGENT_RUNTIME_DIR': str(root / 'agent-runtime'), 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                'GITEE_AI_API_KEY': '', 'GITEE_AI_BASE_URL': 'http://127.0.0.1:1/v1',
                'IMPORT_LEGACY_HISTORY': False,
                'CD80_DATA_API_URL': f'http://{HOST}:{DATA_PORT}',
                'CD80_ADMIN_API_KEY': ADMIN_KEY, 'CD80_READ_API_KEY': READ_KEY,
                'CD80_DATA_ADMIN_USERS': '',
            })
            workbench = website.extensions['workbench_service']
            client = website.test_client()
            user = require_ok(client.post('/desktop/api/register', json={
                'email': ACCOUNT, 'password': PASSWORD,
            }), 'register fixture account')['user']
            website.config['CD80_DATA_ADMIN_USERS'] = user['id']
            session, files = seed_explore(website, user['id'], client)
            require_ok(client.get('/desktop/api/financial-data/capabilities'), 'data capabilities')
            table = require_ok(client.get(f'/desktop/api/financial-data/tables/{TABLE}/rows/1'),
                               'read synthetic row')
            if len(table['data']) != field_count + 1:
                raise RuntimeError('synthetic long row was not visible through the website')

            # Any accidental user-triggered model call must fail before network I/O.
            def no_model(*args, **kwargs):
                raise AssertionError('This scroll fixture does not call a model')

            workbench.runtime.provider.events = no_model
            workbench.sub_agents.model.call = no_model
            website.extensions['explore_service'].provider.events = no_model
            website.extensions['explore_service'].provider.json_tokens = no_model
            website_server = make_server(HOST, SITE_PORT, website, threaded=True)
            print(f'Explore/data scroll fixture ready: http://{HOST}:{SITE_PORT}/desktop/', flush=True)
            print(f'PID {os.getpid()} | DATA_DIR {root / "accounts"} | local data API {DATA_PORT}', flush=True)
            print(f'Account {ACCOUNT} | Explore session {session["id"]} | files {len(files)} | '
                  f'table {TABLE} | fields {field_count}', flush=True)
            website_server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if website_server is not None:
                website_server.server_close()
            if website is not None:
                website.extensions['workbench_service'].close()
            if api_server is not None:
                api_server.should_exit = True
            if api_thread is not None:
                api_thread.join(timeout=10)
            if listener is not None:
                listener.close()
    return 0


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda _signal, _frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    raise SystemExit(main())
