"""Real services and model, disposable account/library; never enables a real user's assets."""
import json
import os
import platform
import sys
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env', override=False)
from backend.app import create_app
from backend.app.modules.capability_library.contracts import digest, now
from scripts.validation_suite_helpers import SuiteBlocked, cases_from_examples, run_validation_suite, execution_complete, reusable_evidence


def main():
    acceptance_password = os.environ.get("CD80_ACCEPTANCE_PASSWORD")
    if not acceptance_password:
        raise SystemExit("Set CD80_ACCEPTANCE_PASSWORD for the disposable validation account.")
    target = ROOT/'data/local-financial-verification.json'
    resume = '--resume' in sys.argv
    report = json.loads(target.read_text(encoding='utf-8')) if resume else {'createdAt':now(), 'system':platform.platform(), 'temporaryData':True, 'examples':[], 'failures':[]}
    with nullcontext(report['disposableDataDirectory'] if resume else tempfile.mkdtemp(prefix='local-financial-verify-')) as directory:
        report['disposableDataDirectory']=directory
        app = create_app({'TESTING':True, 'DATA_DIR':directory, 'IMPORT_LEGACY_HISTORY':False,
                          'CAPABILITY_SKIP_PUBLIC_SEED':True, 'AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        wb = app.extensions['workbench_service']; lib = app.extensions['capability_library']
        if resume and report.get('runnerRevision')!=lib.runner_revision:
            wb.close()
            raise RuntimeError('Runtime changed; run a fresh validation instead of resuming old evidence')
        report.update(model=wb.model_id, runnerRevision=lib.runner_revision)
        try:
            owner = report['owner'] if resume else app.test_client().post('/desktop/api/register',json={'email':'verify@example.invalid','password':acceptance_password}).json['user']['id']
            report['owner']=owner
            folders = list((ROOT/'capabilities/local-financial/packages').glob('*/capability.json'))
            folders.sort(key=lambda p: (json.loads(p.read_text(encoding='utf-8'))['kind']!='tool',p.parent.name))
            for path in folders:
                manifest=json.loads(path.read_text(encoding='utf-8')); name=path.parent.name
                existing=next((e for e in report['examples'] if e['folder']==name),None)
                if resume and existing and existing['manifestDigest']==digest(manifest) and reusable_evidence(existing):
                    previous=lib.version(owner,existing['assetId'],existing['versionId'])
                    if lib.configuration(owner,previous)['configurationId']!=previous['configurationId'] or lib.context_digest(previous)!=existing.get('configurationDigest'):
                        raise RuntimeError('Connection configuration changed; run a fresh validation')
                    continue
                report['examples']=[e for e in report['examples'] if e['folder']!=name]
                report['failures']=[e for e in report['failures'] if e['folder']!=name]
                try:
                    cases=cases_from_examples(manifest)
                    ref=lib.import_package(owner,path.parent.with_suffix('.zip').read_bytes(),name+'.zip')
                    version=lib.version(owner,ref['assetId'],ref['versionId'])
                    before={i['invocationId'] for i in lib.store.list(owner,'invocation')}
                    result=run_validation_suite(lib,owner,version,cases)
                    record={'folder':name,'exampleIds':[example.get('id') for example in manifest['examples']],'manifestDigest':digest(manifest),'contentDigest':version['contentDigest'],
                            'configurationDigest':lib.context_digest(version),'kind':manifest['kind'],**result}
                    record.update(assetId=ref['assetId'],versionId=ref['versionId'],verificationId=result['verificationId'])
                    record['invocations']=[i for i in lib.store.list(owner,'invocation') if i['invocationId'] not in before]
                    report['examples'].append(record)
                    if result['status']=='passed' and execution_complete(result):
                        asset=lib.asset(owner,ref['assetId'])
                        lib.update_asset(owner,ref['assetId'],{'enabled':True,'activeVersionId':ref['versionId']},'"'+str(asset['revision'])+'"')
                    print(json.dumps({'package':name,'status':result['status']},ensure_ascii=False),flush=True)
                except Exception as error:
                    report['failures'].append({'folder':name,'status':'blocked' if isinstance(error,SuiteBlocked) else 'failed','error':str(error)})
                    print(json.dumps(report['failures'][-1],ensure_ascii=False),flush=True)
                target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            report['executionPassed']=bool(report['examples']) and all(execution_complete(x) for x in report['examples']) and not report['failures']
            report['manualReviewRequired']=any(x.get('manualReviewRequired') for x in report['examples'])
            report['validationPassed']=report['executionPassed'] and not report['manualReviewRequired']
        finally:
            wb.close()
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report.get('validationPassed') else 2 if report.get('executionPassed') else 1


if __name__=='__main__': raise SystemExit(main())
