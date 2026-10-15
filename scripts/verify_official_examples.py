"""Run every shipped official Skill/Tool example in a disposable environment.

Produces evidence; Skill output still requires reviewer approval before release.
"""
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env', override=False)
from backend.app import create_app
from backend.app.modules.capability_library.contracts import digest, now
from backend.app.modules.capability_library.packages import zip_bytes, parse_package, inspect
from scripts.validation_suite_helpers import SuiteBlocked, cases_from_examples, run_validation_suite, execution_complete


def main():
    report = {'createdAt': now(), 'python': platform.python_version(), 'system': platform.platform(), 'temporaryData': True, 'examples': [], 'failures': []}
    with tempfile.TemporaryDirectory(prefix='official-example-check-') as directory:
        app = create_app({'TESTING': True, 'DATA_DIR': directory, 'IMPORT_LEGACY_HISTORY': False, 'CAPABILITY_SKIP_PUBLIC_SEED': True})
        wb = app.extensions['workbench_service']; lib = app.extensions['capability_library']
        report.update(model=wb.model_id, runnerRevision=lib.runner_revision)
        try:
            client = app.test_client()
            owner = client.post('/desktop/api/register', json={'email':'official-check@example.invalid','password':'temporary-check-only'}).json['user']['id']
            for folder in sorted((ROOT/'docs/capability-library/examples').iterdir()):
                if not folder.is_dir(): continue
                manifest = json.loads((folder/'capability.json').read_text(encoding='utf-8'))
                if manifest['kind'] not in {'skill','tool'}: continue
                package = zip_bytes({p.relative_to(folder).as_posix():p.read_bytes() for p in folder.rglob('*') if p.is_file()})
                manifest, files = parse_package(package, folder.name+'.zip')
                assert not inspect(manifest, files), f'{folder.name}: package issues'
                try:
                    cases = cases_from_examples(manifest)
                    if manifest['kind'] == 'skill' and not wb.runtime.provider.key: raise RuntimeError('No real model configured')
                    ref = lib.import_package(owner, package, folder.name+'.zip')
                    version = lib.version(owner, ref['assetId'], ref['versionId'])
                    result = run_validation_suite(lib, owner, version, cases)
                    report['examples'].append({'folder':folder.name,'exampleIds':[example.get('id') for example in manifest['examples']],'manifestDigest':digest(manifest),'contentDigest':version['contentDigest'],
                        'configurationDigest':lib.context_digest(version),'kind':manifest['kind'], **result})
                    print(json.dumps({'folder':folder.name,'status':result['status'],'manualReviewRequired':result['manualReviewRequired']},ensure_ascii=False),flush=True)
                except Exception as error:
                    report['failures'].append({'folder':folder.name,'status':'blocked' if isinstance(error, SuiteBlocked) else 'failed','error':str(error)})
                    print(json.dumps(report['failures'][-1],ensure_ascii=False),flush=True)
            report['executionPassed']=bool(report['examples']) and not report['failures'] and all(execution_complete(item) for item in report['examples'])
            report['manualReviewRequired']=any(item.get('manualReviewRequired') for item in report['examples'])
            report['validationPassed']=report['executionPassed'] and not report['manualReviewRequired']
        finally:
            wb.close()
            (ROOT/'data/official-example-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report.get('validationPassed') else 2 if report.get('executionPassed') else 1

if __name__=='__main__': raise SystemExit(main())
