"""Build and live-check the official historical-data App in an isolated account.

Run explicitly with --run-live.  It reads the existing 8897 financial service,
uses GLM-5.3-Flash for the frozen Skill step, and never writes production users.
"""
import argparse
import hashlib
import json
import os
import secrets
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'examples' / 'official-history-app'
PRIVATE_RUNTIME = ROOT / '.run' / 'official-history-app-runtime.json'
INPUTS = (
    ('2025-07-02', '14.2'),
    ('2025-07-03', '14.18'),
    ('2025-07-04', '14.58'),
)
CODE = '''def run(payload):
    history = payload["history"]
    if history.get("source") != "cd80_daily" or not history.get("records"):
        raise ValueError("本地历史数据未返回指定日线，不能生成结论")
    record = history["records"][0]
    if record.get("code") != payload["code"] or record.get("date") != payload["date"]:
        raise ValueError("历史日线的证券或日期与用户请求不一致")
    return {
        "source": history["source"], "code": record["code"],
        "date": record["date"], "close": record["close"],
        "unit": history.get("units", {}).get("prices", "元"),
        "marketCompleteness": record.get("market_completeness", "unknown"),
        "asOf": history.get("as_of"),
        "analysis": payload["analysis"],
    }
'''


def definition(tool, skill):
    return {
        'format': 'cd80.app/2',
        'name': '本地历史日线分析',
        'description': '调用平台官方本地历史日线 Tool 和 Skill，输出真实日线、来源与审慎解释。仅查本地数据，不代表实时行情。',
        'inputSchema': {'type': 'object', 'properties': {
            'code': {'type': 'string', 'pattern': '^[0-9]{6}\\.(SH|SZ|BJ)$'},
            'date': {'type': 'string', 'pattern': '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'},
            'asset': {'type': 'string', 'enum': ['stock', 'etf', 'index']}},
            'required': ['code', 'date', 'asset'], 'additionalProperties': False},
        'rules': ['只使用本次平台本地历史日线 Tool 的实际输出，不编造行情。',
                  '保留来源、日期、单位和数据参照日期；交易日完整性未知时明确写未知。',
                  '本应用是历史数据查询，不构成实时行情或投资建议。'],
        'gaps': [],
        'nodes': [
            {'id': 'history', 'kind': 'tool', 'title': '读取本地历史日线',
             'componentRef': tool,
             'inputBindings': {'code': {'from': 'input', 'path': 'code'},
                               'date': {'from': 'input', 'path': 'date'},
                               'asset': {'from': 'input', 'path': 'asset'},
                               'limit': {'from': 'literal', 'value': 3}},
             'delivery': {'schema': {'type': 'object', 'required': ['source', 'records', 'units', 'as_of'],
                                     'properties': {'source': {'const': 'cd80_daily'},
                                                    'records': {'type': 'array', 'minItems': 1}}}},
             'checkpoint': False},
            {'id': 'explain', 'kind': 'skill', 'title': '按官方方法解释日线',
             'componentRef': skill,
             'task': ('只调用一次本 Skill 冻结的依赖 Tool，使用输入的 code、date、asset 核对本地日线。'
                      '工具成功后用它的真实成功步骤交付 JSON，并写一句中文说明收盘价、单位、来源、日期；'
                      '交易日完整性未知就写未知。证券和日期已明确，不要追问、额外查询或编造数据。'),
             'inputBindings': {'code': {'from': 'input', 'path': 'code'},
                               'date': {'from': 'input', 'path': 'date'},
                               'asset': {'from': 'input', 'path': 'asset'},
                               'history': {'from': 'step', 'stepId': 'history', 'path': ''}},
             'delivery': {'schema': {'type': 'object'}, 'requiredTools': ['tool_1']},
             'checkpoint': False},
            {'id': 'summary', 'kind': 'code', 'title': '校对并汇总实际结果',
             'code': CODE,
             'inputBindings': {'history': {'from': 'step', 'stepId': 'history', 'path': ''},
                               'analysis': {'from': 'step', 'stepId': 'explain', 'path': ''},
                               'code': {'from': 'input', 'path': 'code'},
                               'date': {'from': 'input', 'path': 'date'}},
             'delivery': {'schema': {'type': 'object', 'required': [
                 'source', 'code', 'date', 'close', 'unit', 'marketCompleteness', 'asOf', 'analysis']}},
             'checkpoint': False},
        ],
        'outputs': {'summary': {'from': 'step', 'stepId': 'summary', 'path': ''},
                    'history': {'from': 'step', 'stepId': 'history', 'path': ''},
                    'explanation': {'from': 'step', 'stepId': 'explain', 'path': ''}},
    }


def wait_run(service, owner, run_id, timeout=420):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = service.run(owner, run_id)
        if run['status'] not in {'queued', 'running'} and run_id not in service.active:
            return run
        time.sleep(.2)
    raise TimeoutError('App run exceeded the bounded local acceptance window')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true', help='explicitly call 8897 and GLM-5.3-Flash')
    args = parser.parse_args()
    if not args.run_live:
        parser.error('pass --run-live to create a real isolated acceptance run')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    if not os.environ.get('GITEE_AI_API_KEY') or not os.environ.get('CD80_READ_API_KEY'):
        raise RuntimeError('The existing local model and financial read configuration must be available')
    import sys
    sys.path.insert(0, str(ROOT))
    from backend.app import create_app
    from backend.app.modules.capability_library.catalog import seed_catalog
    from backend.app.modules.capability_library.official import copy_closure
    from backend.app.modules.capability_library.service import PUBLIC

    data_parent = ROOT / 'data' / 'integration-acceptance'
    data_parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(tempfile.mkdtemp(prefix='official-history-app-', dir=data_parent))
    email = 'official-history-app-' + uuid4().hex[:12] + '@example.invalid'
    password = secrets.token_urlsafe(24)
    config = {'TESTING': True, 'DATA_DIR': str(data_dir), 'CAPABILITY_SKIP_PUBLIC_SEED': True,
              'GITEE_AI_API_KEY': os.environ['GITEE_AI_API_KEY'], 'GITEE_AI_MODEL': 'GLM-5.3-Flash',
              'AGENT_MODEL_MAX_TOKENS': 12288,
              'GITEE_AI_BASE_URL': os.environ.get('GITEE_AI_BASE_URL', 'https://ai.gitee.com/v1'),
              'CD80_READ_API_KEY': os.environ['CD80_READ_API_KEY'], 'CD80_ADMIN_API_KEY': '',
              'CD80_DATA_API_URL': 'http://127.0.0.1:8897',
              'AGENT_RUNTIME_DIR': str(ROOT / 'data' / 'agent-runtime')}
    app = create_app(config)
    service = app.extensions['asset_apps']
    library = app.extensions['capability_library']
    client = app.test_client()
    user = client.post('/desktop/api/register', json={
        'email': email, 'password': password}).json['user']
    owner = user['id']
    PRIVATE_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_RUNTIME.write_text(json.dumps({'dataDir': str(data_dir), 'email': email,
                                           'password': password}, ensure_ascii=False, indent=2) + '\n')
    PRIVATE_RUNTIME.chmod(0o600)
    evidence = {'runKind': 'isolated-real-model-and-readonly-data',
                'model': 'GLM-5.3-Flash', 'dataApi': 'http://127.0.0.1:8897',
                'startedAt': datetime.now().astimezone().isoformat(), 'cases': []}
    try:
        published = seed_catalog(library)
        if len(published) != 26 or any(item['status'] != 'published' for item in published):
            raise RuntimeError('Official catalog did not publish all 26 exact versions')
        source = next(a for a in library.store.list(PUBLIC, 'asset')
                      if a['sourceIdentity'] == 'cd80/cd80-history')
        source_version = library.version(PUBLIC, source['assetId'], source['latestVersionId'])
        copied = copy_closure(library, owner, source_version)
        if len(copied['dependencies']) != 1 or copied['readiness'] != 'ready':
            raise RuntimeError('Official Skill or dependency was not admitted as ready')
        tool = copied['dependencies'][0]
        tool_version = library.version(owner, tool['assetId'], tool['versionId'])
        if tool_version['manifest']['source']['identity'] != 'cd80/cd80-history-tool' or tool_version['readiness'] != 'ready':
            raise RuntimeError('The exact historical Tool dependency was not ready')
        for item in (tool, copied):
            asset = library.asset(owner, item['assetId'])
            library.update_asset(owner, item['assetId'],
                                 {'enabled': True, 'activeVersionId': item['versionId']},
                                 f'"{asset["revision"]}"')
        evidence['official'] = {'skillPublicAssetId': source['assetId'],
                                'skillPublicVersionId': source_version['versionId'],
                                'skillContentDigest': source_version['contentDigest'],
                                'toolPublicContentDigest': source_version['manifest']['dependencies'][0]['contentDigest'],
                                'personalSkillAdmission': copied['admission'],
                                'personalToolAdmission': tool_version['admission'],
                                'personalVerificationCount': len(library.store.list(owner, 'verification'))}
        doc = definition({'assetId': tool['assetId'], 'versionId': tool['versionId']},
                         {'assetId': copied['assetId'], 'versionId': copied['versionId']})
        app_detail = service.save(owner, {'definition': doc}, 'official-history-app-save')
        if app_detail['issues'] or app_detail['verification']['status'] != 'unverified' or app_detail['enabledVersionId']:
            raise RuntimeError('App draft did not remain unverified and disabled')
        evidence['app'] = {'appId': app_detail['id'], 'versionId': app_detail['versionId'],
                           'savedStatus': app_detail['verification']['status'],
                           'savedEnabledVersionId': app_detail['enabledVersionId']}
        private = json.loads(PRIVATE_RUNTIME.read_text(encoding='utf-8'))
        private.update(appId=app_detail['id'], versionId=app_detail['versionId'])
        PRIVATE_RUNTIME.write_text(json.dumps(private, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        PRIVATE_RUNTIME.chmod(0o600)
        cases = [{'label': '原预期' if i == 0 else '不同输入',
                  'input': {'code': '600000.SH', 'date': day, 'asset': 'stock'},
                  'expectation': {'mode': 'fields', 'values': {
                      'summary.source': 'cd80_daily', 'summary.code': '600000.SH',
                      'summary.date': day, 'summary.close': close,
                      'summary.marketCompleteness': 'unknown'}}}
                 for i, (day, close) in enumerate(INPUTS[:2])]
        verification = service.verify(owner, app_detail['id'],
                                      {'versionId': app_detail['versionId'], 'cases': cases},
                                      'official-history-app-verify')
        for run_id in verification['verification']['runIds']:
            run = wait_run(service, owner, run_id)
            evidence['cases'].append(summarize_run(app, owner, run))
            if run['status'] != 'succeeded':
                raise RuntimeError(f'Verification run {run_id} ended {run["status"]}: {run.get("error")}')
        verified = service.detail(owner, app_detail['id'])
        evidence['app']['verificationStatus'] = verified['verification']['status']
        if verified['verification']['status'] != 'passed':
            raise RuntimeError('Two real-input App verification did not pass')
        enabled = service.enable(owner, app_detail['id'],
                                 {'versionId': app_detail['versionId'], 'enabled': True,
                                  'revision': verified['revision']}, 'official-history-app-enable')
        if enabled['enabledVersionId'] != app_detail['versionId']:
            raise RuntimeError('Validated App was not enabled')
        evidence['app']['enabledVersionId'] = enabled['enabledVersionId']
        third = service.create_run(owner, app_detail['id'],
                                   {'versionId': app_detail['versionId'],
                                    'input': {'code': '600000.SH', 'date': INPUTS[2][0], 'asset': 'stock'}},
                                   'official-history-app-third-input')
        final_run = wait_run(service, owner, third['id'])
        evidence['cases'].append(summarize_run(app, owner, final_run))
        if final_run['status'] != 'succeeded' or final_run['result']['summary']['close'] != INPUTS[2][1]:
            raise RuntimeError(f'Third real-input run did not succeed: {final_run.get("error")}')
        package = service.export(owner, app_detail['id'], app_detail['versionId'])
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / 'application.json').write_text(json.dumps(package, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (OUTPUT / 'app.json').write_text(json.dumps(package['definition'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        with zipfile.ZipFile(OUTPUT / 'app.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(OUTPUT / 'app.json', 'app.json')
        evidence['artifacts'] = {name: {'sha256': sha256((OUTPUT / name).read_bytes()),
                                        'size': (OUTPUT / name).stat().st_size}
                                 for name in ('application.json', 'app.json', 'app.zip')}
        evidence['completedAt'] = datetime.now().astimezone().isoformat()
        (OUTPUT / 'acceptance.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'status': 'passed', 'appId': app_detail['id'],
                          'verification': 'passed', 'runIds': [x['runId'] for x in evidence['cases']],
                          'output': str(OUTPUT)}, ensure_ascii=False))
    except BaseException as error:
        evidence['error'] = {'type': type(error).__name__, 'message': str(error)[:1000]}
        OUTPUT.mkdir(parents=True, exist_ok=True)
        (OUTPUT / 'acceptance-attempt.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        raise
    finally:
        app.extensions['workbench_service'].close()


def summarize_run(app, owner, run):
    ids = []
    for step in run['steps']:
        if step.get('skillCallId'):
            skill = app.extensions['sub_agents'].store.run(owner, step['skillCallId'])
            ids = [{'callId': item['id'], 'stage': item['stage'], 'status': item['status']}
                   for item in skill.get('modelCalls', [])]
    summary = (run.get('result') or {}).get('summary') or {}
    return {'runId': run['id'], 'mode': run['mode'], 'status': run['status'],
            'inputDate': run['input']['date'], 'stepStatuses': [s['status'] for s in run['steps']],
            'toolInvocationIds': [s.get('invocationId') for s in run['steps'] if s.get('invocationId')],
            'skillCallIds': [s.get('skillCallId') for s in run['steps'] if s.get('skillCallId')],
            'modelCalls': ids, 'source': summary.get('source'), 'close': summary.get('close'),
            'marketCompleteness': summary.get('marketCompleteness'), 'error': run.get('error')}


if __name__ == '__main__':
    main()
