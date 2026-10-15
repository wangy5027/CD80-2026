"""Release evidence: actual localhost HTTP, real WASI and configured real model.

Uses disposable user data and read-only production SQLite. Never publishes.
Skill reviews must be supplied separately after inspecting actual responses.
"""
import io
import json
import platform
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'cd80-data-api')]
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from app.main import create_app as data_app
from fastapi import HTTPException
import uvicorn
from backend.app import create_app
from backend.app.modules.capability_library.packages import parse_package
from backend.app.modules.capability_library.contracts import digest,now
from scripts.validation_suite_helpers import SuiteBlocked, cases_from_examples, run_validation_suite, execution_complete, reusable_evidence


def read_api():
    api=data_app()
    @asynccontextmanager
    async def lifespan(_):
        yield
    api.router.lifespan_context=lifespan
    def readonly():
        db=sqlite3.connect((ROOT/'cd80-data-api/data/cd80.sqlite3').as_uri()+'?mode=ro',uri=True,check_same_thread=False)
        db.row_factory=sqlite3.Row
        deadline=time.monotonic()+30
        db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:yield db
        finally:db.close()
    def no_writes():
        raise HTTPException(403,'Verification server is read only')
    for route in api.routes:
        for dep in getattr(getattr(route,'dependant',None),'dependencies',[]):
            if dep.call.__name__=='get_connection':api.dependency_overrides[dep.call]=readonly
            if dep.call.__name__=='need_admin':api.dependency_overrides[dep.call]=no_writes
            if dep.call.__name__=='need_read':api.dependency_overrides[dep.call]=lambda:None
    return api


def main():
    target=ROOT/'data/migration-release-verification.json'
    resume='--resume' in sys.argv
    report=json.loads(target.read_text(encoding='utf-8')) if resume else {'createdAt':now(),'system':platform.platform(),'examples':[],'failures':[], 'disposableDataDirectory':tempfile.mkdtemp(prefix='migration-release-')}
    server=uvicorn.Server(uvicorn.Config(read_api(),host='127.0.0.1',port=48998,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    if not server.started:raise RuntimeError('Read-only HTTP server failed to start')
    app=create_app({'TESTING':True,'DATA_DIR':report['disposableDataDirectory'],'CAPABILITY_SKIP_PUBLIC_SEED':True,'IMPORT_LEGACY_HISTORY':False,
                    'GITEE_AI_CONNECT_TIMEOUT_SECONDS':30,'GITEE_AI_READ_TIMEOUT_SECONDS':180,'CD80_DATA_API_URL':'http://127.0.0.1:48998','AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
    wb=app.extensions['workbench_service'];lib=app.extensions['capability_library'];client=app.test_client()
    try:
        if resume:
            if report['runnerRevision']!=lib.runner_revision:raise RuntimeError('Runtime changed; fresh evidence required')
            client.post('/desktop/api/login',json={'email':'release@example.invalid','password':'temporary-release-password'})
        else:
            report['owner']=client.post('/desktop/api/register',json={'email':'release@example.invalid','password':'temporary-release-password'}).json['user']['id']
        owner=report['owner']
        report.update(modelOptions={'connect_timeout':30,'read_timeout':180,'generation':'configured_defaults'},runnerRevision=lib.runner_revision,model=wb.model_id,transport='localhost_http_readonly_sqlite_real_model')
        candidates=[]
        for path in (ROOT/'capabilities/local-financial/migration-candidates').glob('*.zip'):
            manifest,files=parse_package(path.read_bytes(),path.name)
            candidates.append((path,manifest,files))
        candidates.sort(key=lambda item:(item[1]['kind']!='tool',item[0].name))
        report_lock=threading.Lock()
        def verify_candidate(candidate):
            path,manifest,files=candidate
            previous=next((r for r in report['examples'] if r['folder']==path.stem),None)
            if resume and previous and previous['manifestDigest']==digest(manifest) and reusable_evidence(previous):
                old=lib.version(owner,previous['assetId'],previous['versionId'])
                if lib.context_digest(old)!=previous.get('configurationDigest'):
                    raise RuntimeError('Connection configuration changed; fresh suite evidence required')
                return
            report['examples']=[r for r in report['examples'] if r['folder']!=path.stem]
            report['failures']=[r for r in report['failures'] if r['folder']!=path.stem]
            try:
                def prepare(sample):
                    file_ids=[]
                    for name in sample.get('filePaths',[]):
                        response=client.post('/desktop/api/files',data={'file':(io.BytesIO(files[name]),Path(name).name)})
                        if response.status_code!=201:raise RuntimeError('File upload failed: '+str(response.json))
                        file_ids.append(response.json['id'])
                    args=dict(sample.get('input',{}))
                    if file_ids and 'file' in args:args['file']=file_ids[0]+Path(sample['filePaths'][0]).suffix
                    return args,file_ids
                cases=cases_from_examples(manifest,prepare)
                ref=lib.import_package(owner,path.read_bytes(),path.name)
                version=lib.version(owner,ref['assetId'],ref['versionId'])
                before={i['invocationId'] for i in lib.store.list(owner,'invocation')}
                checked=run_validation_suite(lib,owner,version,cases,timeout=600)
                record={'folder':path.stem,'exampleIds':[example.get('id') for example in manifest['examples']],'manifestDigest':digest(manifest),'contentDigest':version['contentDigest'],'configurationDigest':lib.context_digest(version),
                        'kind':manifest['kind'],**checked,'assetId':ref['assetId'],'versionId':ref['versionId'],
                        'invocations':[i for i in lib.store.list(owner,'invocation') if i['invocationId'] not in before and (i.get('jobId')==checked['jobId'] if manifest['kind']=='tool' else any(r.get('versionId')==ref['versionId'] for r in i.get('sourceSkillRefs',[])))]}
                report['examples'].append(record)
                if checked['status']=='passed' and execution_complete(checked):
                    asset=lib.asset(owner,ref['assetId'])
                    lib.update_asset(owner,ref['assetId'],{'enabled':True,'activeVersionId':ref['versionId']},'"'+str(asset['revision'])+'"')
                print(json.dumps({'package':path.stem,'status':checked['status']},ensure_ascii=False),flush=True)
            except Exception as error:
                failure={'folder':path.stem,'status':'blocked' if isinstance(error,SuiteBlocked) else 'failed','error':str(error)};report['failures'].append(failure)
                print(json.dumps(failure,ensure_ascii=False),flush=True)
            with report_lock:
                target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        for candidate in candidates:
            if candidate[1]['kind']=='tool':verify_candidate(candidate)
        with ThreadPoolExecutor(max_workers=2) as workers:
            list(workers.map(verify_candidate,[c for c in candidates if c[1]['kind']=='skill']))
        report['executionPassed']=bool(candidates) and len(report['examples'])==len(candidates) and not report['failures'] and all(execution_complete(r) for r in report['examples'])
        report['manualReviewRequired']=any(r.get('manualReviewRequired') for r in report['examples'])
        report['validationPassed']=report['executionPassed'] and not report['manualReviewRequired']
    finally:
        wb.close();server.should_exit=True;thread.join(timeout=5)
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report.get('validationPassed') else 2 if report.get('executionPassed') else 1


if __name__=='__main__':raise SystemExit(main())
