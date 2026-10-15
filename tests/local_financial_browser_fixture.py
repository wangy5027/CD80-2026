"""Disposable browser fixture: deterministic news, real package/WASI execution."""
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app import create_app
from werkzeug.serving import make_server

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='financial-ui-') as directory:
        app=create_app({'TESTING':True,'DATA_DIR':directory,'DESKTOP_PORT':48996,
            'CAPABILITY_SKIP_PUBLIC_SEED':True,'GITEE_AI_API_KEY':'','AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        from backend.app.modules.capability_library.catalog import seed_catalog
        seed_catalog(app.extensions['capability_library'])
        @app.post('/__fixture__/official-update')
        def fixture_update():
            from unittest.mock import patch
            from backend.app.modules.capability_library.contracts import canonical
            from backend.app.modules.capability_library.packages import zip_bytes
            lib=app.extensions['capability_library'];public='__platform_public__'
            asset=next(a for a in lib.store.list(public,'asset') if a['sourceIdentity']=='cd80/cd80-news')
            version=lib.version(public,asset['assetId'],asset['latestVersionId'])
            manifest={**version['manifest'],'description':'隔离环境新版新闻方法'}
            with patch('backend.app.modules.capability_library.releases.check_release'):
                return lib.import_package(public,zip_bytes({**lib.files(public,version),'capability.json':canonical(manifest).encode()}),'fixture-update.zip')
        @app.post('/__fixture__/runtime-change')
        def fixture_runtime_change():
            app.extensions['capability_library'].runner_revision+='fixture-change'
            return {'changed':True}
        app.extensions['news_service'].get_news=lambda **kwargs:{'source':'eastmoney',
            'items':[{'title':'隔离测试新闻','summary':'测试内容','published_at':'2026-10-13T09:00:00+08:00'}],
            'latest_published_at':'2026-10-13T09:00:00+08:00','cache':{'stale':False,'fetched_at':'2026-10-13T09:00:00+08:00'},
            'pagination':{'total':1,'has_more':False},'warning':None}
        server=make_server('127.0.0.1',48996,app,threaded=True)
        print('Isolated financial UI fixture: 48996',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close();app.extensions['workbench_service'].close()
