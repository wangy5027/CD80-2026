"""Real WASI + real SQLite, in-process HTTP; no production writes or publication."""
import json
import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'cd80-data-api'))
from app.main import create_app as data_app
from backend.app import create_app
from backend.app.modules.capability_library.packages import parse_package
from backend.app.modules.capability_library.assertions import evaluate
from fastapi.testclient import TestClient


def main():
    acceptance_password = os.environ.get("CD80_ACCEPTANCE_PASSWORD")
    if not acceptance_password:
        raise SystemExit("Set CD80_ACCEPTANCE_PASSWORD for the disposable validation account.")
    api = data_app()
    def readonly():
        db = sqlite3.connect((ROOT/'cd80-data-api/data/cd80.sqlite3').as_uri()+'?mode=ro', uri=True, check_same_thread=False)
        db.row_factory = sqlite3.Row
        deadline = time.monotonic()+30
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        try:
            yield db
        finally:
            db.close()
    # Override before any request: normal startup would create an audit table.
    for route in api.routes:
        for dependency in getattr(getattr(route, 'dependant', None), 'dependencies', []):
            if dependency.call.__name__ == 'get_connection':
                api.dependency_overrides[dependency.call] = readonly
            elif dependency.call.__name__ == 'need_read':
                api.dependency_overrides[dependency.call] = lambda: None
    upstream = TestClient(api)  # Deliberately no lifespan startup.
    report = {'createdAt':datetime.now(timezone.utc).isoformat(), 'validation':'real_wasi_real_sqlite_inprocess_http', 'published':False, 'results':[]}
    with tempfile.TemporaryDirectory(prefix='migration-check-') as directory:
        app = create_app({'TESTING':True, 'DATA_DIR':directory, 'CAPABILITY_SKIP_PUBLIC_SEED':True,
            'IMPORT_LEGACY_HISTORY':False, 'AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        lib = app.extensions['capability_library']
        owner = app.test_client().post('/desktop/api/register',json={'email':'migration@example.invalid','password':acceptance_password}).json['user']['id']
        def forward(_session, method, url, **kwargs):
            return upstream.request(method, urlsplit(url).path, json=kwargs.get('json'), params=kwargs.get('params'), headers=kwargs.get('headers'))
        try:
            for path in sorted((ROOT/'capabilities/local-financial/migration-candidates').glob('*.zip')):
                if len(sys.argv) > 1 and sys.argv[1] not in path.name:
                    continue
                manifest, files = parse_package(path.read_bytes(), path.name)
                if manifest['kind'] != 'tool':
                    continue
                sample = manifest['examples'][0]
                try:
                    ref = lib.import_package(owner, path.read_bytes(), path.name)
                    version = lib.version(owner, ref['assetId'], ref['versionId'])
                    task_files = {Path(p).name:files[p] for p in sample.get('filePaths', [])}
                    with patch('requests.Session.request', forward):
                        value = lib.invoke(owner, {**ref, 'configurationId':version['configurationId'], 'input':sample['input'], 'fileIds':[]}, threading.Event(), trial=True, task_files=task_files)
                    content = value['result']['content']
                    if sample.get('assertions') and evaluate(content,sample['assertions']):
                        raise AssertionError('Example assertions failed')
                    if 'expected' in sample and content != sample['expected']:
                        raise AssertionError('Example exact expectation failed')
                    if path.name == 'cd80-csv-export-tool.zip' and not value['result']['artifacts']:
                        raise AssertionError('CSV artifact missing')
                    item = {'package':path.name,'status':'executed','rows':len(content.get('records', [])), 'reads':len(value.get('dataReads', [])), 'result':content}
                except Exception as error:
                    item = {'package':path.name,'status':'failed','error':str(error)}
                    item['logs'] = [i.get('logs') for i in lib.store.list(owner,'invocation') if i.get('status') == 'failed']
                report['results'].append(item)
                item['zipSha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                print(json.dumps({k:v for k,v in item.items() if k!='result'}, ensure_ascii=False), flush=True)
        finally:
            app.extensions['workbench_service'].close()
            upstream.close()
    suffix = '-'+sys.argv[1] if len(sys.argv)>1 else ''
    target = ROOT/('data/financial-migration-verification'+suffix+'.json')
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__ == '__main__':
    main()
