"""Opt-in real-model creation -> execution -> save -> natural-language adaptation.

Only synthetic position CSVs and a disposable account/database are used. The
configured model is called through the unmodified application planner; all
generated computations execute in the actual WASI runtime. No production user
data or financial service is read. At most two runs, two model calls per run,
and 540 seconds per run; failures are retained, never silently retried.
--retry-adaptation explicitly adds one separately reported adaptation attempt
from a prior successful saved definition, imported into a new temporary account.
"""
import argparse
import copy
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SAMPLES = (
    b'portfolio,symbol,quantity,price_cny\nAlpha,AAA,10,12.50\nAlpha,BBB,20,3.25\nBeta,CCC,5,8.40\nBeta,DDD,4,1.50\n',
    b'portfolio,symbol,quantity,price_cny\nAlpha,EEE,8,11.25\nAlpha,FFF,6,5.50\nAlpha,GGG,3,9.00\nGamma,HHH,2,17.75\nGamma,III,10,1.05\n',
)
BASE_GOAL = (
    '创建一个可复用的金融持仓市值汇总应用，并生成实际Python工具及简短Skill说明。'
    '输入是本次上传的合成持仓CSV；UTF-8、逗号分隔、有表头portfolio,symbol,quantity,price_cny。'
    '每行是一条独立持仓，quantity为数量，price_cny为人民币单价；按portfolio分组，'
    '每条持仓市值=quantity乘price_cny，组内市值相加得到组合总持仓市值。'
    '使用Decimal；缺列、空分组、空值、非法数值立即报错，不编造、不丢行。'
    '只使用上传的CSV和Python标准库，不调用行情、搜索、网络或内置查询工具。'
    '唯一运行参数是fileId，业务列名和规则属于应用方法，不增加运行参数。'
    '输出summary.csv，表头严格为portfolio,market_value_cny,position_count，按portfolio升序；'
    'market_value_cny以人民币保留两位小数，position_count为该组合输入行数。'
    '计算Tool返回结构化对象：metric="total_market_value"，values为{组合名:两位小数字符串}，'
    'counts为{组合名:持仓行数整数}，rows为总输入行数，artifacts=["summary.csv"]。'
    '末尾模型解释只基于实际计算，明确数据是合成测试数据、单位人民币，不是实时市场估值。'
    '不能把本次输入值或文件ID硬编码在应用源码中。'
)
ADAPT_GOAL = (
    '请在已保存的总持仓市值应用基础上改编为独立的平均持仓市值应用。'
    '保留CSV格式、按portfolio分组、数量乘单价、Decimal、数据校验、唯一fileId参数和summary.csv输出。'
    '改变金融计算逻辑：每个组合的平均持仓市值=该组合全部持仓市值之和除以该组合持仓行数；'
    '每行权重相同，不是价格均值、不是按数量加权，也不是把所有组合一起平均。'
    '仍输出portfolio,market_value_cny,position_count，但market_value_cny表示平均持仓市值并保留两位小数。'
    'Tool返回metric="mean_market_value"，values为{组合名:两位小数字符串}，'
    'counts为{组合名:持仓行数整数}，rows为总输入行数，artifacts=["summary.csv"]。'
    '同步修改应用名称、方法Skill与解释规则，清楚区分平均值和原总额。'
    '本次上传的是另一份合成持仓CSV；只读取本次授权文件，不沿用旧文件、数据、结果或硬编码值。'
    '不调用行情、搜索、网络、内置查询工具或第三方Python包；原应用与其结果须保留。'
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def expected_result(raw, mean=False):
    groups = defaultdict(list)
    rows = list(csv.DictReader(io.StringIO(raw.decode('utf-8'))))
    for row in rows:
        groups[row['portfolio']].append(Decimal(row['quantity']) * Decimal(row['price_cny']))
    values = {key: sum(items) / (len(items) if mean else 1) for key, items in sorted(groups.items())}
    return {'metric': 'mean_market_value' if mean else 'total_market_value',
            'values': {key: format(value, '.2f') for key, value in values.items()},
            'counts': {key: len(items) for key, items in sorted(groups.items())}, 'rows': len(rows)}


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--live', action='store_true', help='Use ordinary model quota for two bounded application runs.')
    choice.add_argument('--preflight', action='store_true', help='Check runtime/configuration and independent arithmetic without model calls.')
    choice.add_argument('--retry-adaptation', type=Path, metavar='REPORT',
                        help='One explicitly authorized adaptation recheck; import the prior actual saved definition into a fresh account.')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from backend.app import create_app

    configured = dotenv_values(ROOT / '.env')
    keys = ('GITEE_AI_API_KEY', 'GITEE_AI_BASE_URL', 'GITEE_AI_MODEL')
    model_config = {key: os.environ.get(key) or configured.get(key) for key in keys if os.environ.get(key) or configured.get(key)}
    if not model_config.get('GITEE_AI_API_KEY'):
        parser.error('No configured model credential; no requests were sent.')
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output_root = ROOT / 'data/vision-application-live'
    output_root.mkdir(parents=True, exist_ok=True)
    output = output_root / ('application-' + timestamp + '-' + uuid4().hex[:6] + '.json')
    artifacts_root = output.with_suffix('')
    artifacts_root.mkdir()
    report = {'startedAt': datetime.now(timezone.utc).isoformat(), 'realModel': bool(args.live or args.retry_adaptation),
              'realWasi': False, 'productionUserDataRead': False, 'productionDataRead': False,
              'productionWrites': False, 'maximumRuns': 1 if args.retry_adaptation else 2, 'maximumModelCallsPerRun': 2,
              'maxSecondsPerRun': 540, 'model': model_config.get('GITEE_AI_MODEL', 'GLM-5.3'),
              'cases': [], 'modelCalls': [], 'passed': False,
              'sourceSha256': {name: sha((ROOT / name).read_bytes()) for name in (
                  'backend/app/modules/agent_platform/planner.py', 'backend/app/modules/agent_platform/service.py',
                  'backend/app/modules/agent_platform/repository.py', 'backend/app/modules/agent_platform/executor.py',
                  'scripts/verify_vision_application.py')}}
    report_lock = threading.RLock()
    current_case = {}
    app = None

    def sanitize(value):
        text = json.dumps(value, ensure_ascii=False, indent=2)
        text = text.replace(model_config['GITEE_AI_API_KEY'], '[REDACTED]')
        return text

    def save_report():
        with report_lock:
            output.write_text(sanitize(report), encoding='utf-8')

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    def response_json(response, status):
        require(response.status_code == status, f'HTTP {response.status_code}: {response.get_json()}')
        return response.get_json()

    def archive_run(platform, owner, run, case):
        case['run'] = copy.deepcopy(run)
        directory = artifacts_root / case['name']
        directory.mkdir(exist_ok=True)
        case['artifacts'] = []
        for item in run.get('artifacts', []):
            path, meta = platform.artifact(owner, item['id'])
            data = path.read_bytes()
            name = Path(meta['name']).name
            (directory / name).write_bytes(data)
            case['artifacts'].append({'name': name, 'originalName': meta.get('originalName'),
                                      'sha256': sha(data), 'bytes': len(data), 'id': item['id']})
        case['sources'] = []
        for tool in (run.get('spec') or {}).get('tools', []):
            if 'code' in tool:
                data = tool['code'].encode('utf-8')
                name = 'tool-' + tool['id'] + '.py'
                (directory / name).write_bytes(data)
                case['sources'].append({'name': name, 'toolId': tool['id'], 'sha256': sha(data), 'bytes': len(data)})
        save_report()

    def wait(platform, owner, rid, case, expected, started):
        last_state, last_notice = None, 0
        while True:
            run = platform.repository.get(owner, rid)
            elapsed = time.monotonic() - started
            state = (run['status'], len(run['steps']))
            if state != last_state or elapsed - last_notice >= 20:
                print(json.dumps({'case': case['name'], 'status': run['status'], 'steps': len(run['steps']),
                                  'seconds': round(elapsed)}), flush=True)
                last_state, last_notice = state, elapsed
                case.update(status=run['status'], seconds=round(elapsed, 2))
                save_report()
            if elapsed >= 540:
                platform.cancel(owner, rid)
                archive_run(platform, owner, platform.repository.get(owner, rid), case)
                raise RuntimeError('Run exceeded the 540 second acceptance deadline.')
            if run['status'] in expected:
                return run
            if run['status'] in {'failed', 'cancelled', 'interrupted', 'awaiting_input', 'awaiting_authorization'}:
                archive_run(platform, owner, run, case)
                raise RuntimeError(f'Application stopped at {run["status"]}; no repair/retry was performed.')
            time.sleep(.25)

    try:
        with tempfile.TemporaryDirectory(prefix='vision-application-') as temporary:
            try:
                config = {**model_config, 'TESTING': True, 'DATA_DIR': temporary, 'IMPORT_LEGACY_HISTORY': False,
                    'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime'), 'CAPABILITY_SKIP_PUBLIC_SEED': True,
                    'SFC_TOKEN': '', 'SFC_KEY_FILE': '', 'CAPABILITY_MCP_POLICY_FILE': '',
                    'CD80_READ_API_KEY': '', 'CD80_ADMIN_API_KEY': '', 'CD80_DATA_ADMIN_USERS': '',
                    'AGENT_MODEL_MAX_TOKENS': 8192, 'AGENT_MODEL_REASONING_EFFORT': 'low',
                    'GITEE_AI_READ_TIMEOUT_SECONDS': 90}
                app = create_app(config)
                platform = app.extensions['agent_platform']
                report['runtime'] = platform.executor.status()
                require(report['runtime']['available'], 'Real WASI runtime is unavailable.')
                require(expected_result(SAMPLES[0])['values'] == {'Alpha': '190.00', 'Beta': '48.00'}, 'Independent total fixture mismatch.')
                require(expected_result(SAMPLES[1], True)['values'] == {'Alpha': '50.00', 'Gamma': '23.00'}, 'Independent mean fixture mismatch.')
                if args.preflight:
                    report['passed'] = True
                    return 0
                provider = platform.planner.provider
                original_events = provider.events

                def observed_events(messages, model, stopped, on_response, **kwargs):
                    with report_lock:
                        require(sum(call['case'] == current_case['name'] for call in report['modelCalls']) < 2,
                                'Per-run model call budget exceeded.')
                        call = {'case': current_case['name'], 'model': model, 'messages': copy.deepcopy(messages),
                                'events': [], 'status': 'running', 'startedAt': datetime.now(timezone.utc).isoformat()}
                        report['modelCalls'].append(call)
                    save_report()
                    try:
                        for event in original_events(messages, model, stopped, on_response, **kwargs):
                            with report_lock:
                                call['events'].append(copy.deepcopy(event))
                            yield event
                        call['status'] = 'succeeded'
                    except Exception as error:
                        call.update(status='failed', error=str(error))
                        raise
                    finally:
                        call['completedAt'] = datetime.now(timezone.utc).isoformat()
                        save_report()

                provider.events = observed_events  # Observe actual transport only; never substitute responses.
                client = app.test_client()
                account = response_json(client.post('/desktop/api/register', json={
                    'email': 'vision-application@example.invalid', 'password': 'synthetic-acceptance-account'}), 200)
                owner = account['user']['id']
                saved, original_spec = None, None
                baseline_case, derived_path = None, None
                if args.retry_adaptation:
                    from backend.app.modules.agent_platform.contracts import digest
                    derived_path = args.retry_adaptation.resolve()
                    prior_bytes = derived_path.read_bytes()
                    prior = json.loads(prior_bytes)
                    baseline_case = prior['cases'][0]
                    require(prior.get('realModel') and baseline_case.get('passed') and baseline_case.get('name') == 'create_total'
                            and baseline_case.get('run', {}).get('status') == 'succeeded', 'Report has no successful real-model baseline.')
                    source_spec = copy.deepcopy(baseline_case['saved']['spec'])
                    require(source_spec == baseline_case['run']['spec'], 'Stored baseline differs from the actually executed definition.')
                    require(all('code' in tool and 'capabilityRef' not in tool and 'registeredTool' not in tool
                                for tool in source_spec['tools']), 'This recovery accepts only the original inline synthetic-file application.')
                    definition = copy.deepcopy(source_spec)
                    definition.pop('id', None)
                    definition['origin'] = 'generated'
                    package = {'format': 'infinity-application', 'formatVersion': '1', 'kind': 'pipeline',
                        'definition': definition, 'dependencies': [], 'provenance': {
                            'name': source_spec['name'], 'version': source_spec['version'], 'definitionDigest': digest(source_spec)}}
                    report['derivedFrom'] = {'report': str(derived_path), 'sha256': sha(prior_bytes),
                        'case': baseline_case['name'], 'originalAgentRef': baseline_case['saved']['agentRef']}
                    report['budgetRevisionReason'] = ('One separately authorized retry after the original second run received an empty visible model response. '
                        'At most one new run, two model calls and 540 seconds; no original generation is repeated.')
                    report['restorationMethod'] = ('The original temporary database was removed. Build a portable package from its actual successful saved spec '
                        '(same definition fields/code; remove local id; no dependencies), then use the normal import API into a new temporary account. '
                        'This does not recreate or claim the original account history or execution verification.')
                    (artifacts_root / 'recovered-baseline.application.json').write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding='utf-8')
                    saved = response_json(client.post('/desktop/api/agents/import', json={
                        'package': package, 'idempotencyKey': 'recover-real-baseline'}), 201)
                    report['importedBaseline'] = copy.deepcopy(saved)
                    require(saved['validationScope'] == 'structure_only', 'Imported baseline improperly inherited execution verification.')
                    original_spec = copy.deepcopy(saved['spec'])
                    report['restoredDefinitionMatchesSource'] = all(original_spec.get(key) == value for key, value in source_spec.items()
                        if key not in {'id', 'name'})
                    require(report['restoredDefinitionMatchesSource'], 'Import changed the baseline calculation or method.')
                    save_report()
                scenarios = list(enumerate(zip(('create_total', 'adapt_mean'), (BASE_GOAL, ADAPT_GOAL), SAMPLES)))
                if args.retry_adaptation:
                    scenarios = scenarios[1:]
                for index, (name, goal, raw) in scenarios:
                    require(len(report['cases']) < report['maximumRuns'], 'Application run budget exceeded.')
                    baseline_saved = copy.deepcopy(saved)
                    case = {'name': name, 'goal': goal, 'inputCsv': raw.decode('utf-8'), 'inputSha256': sha(raw),
                            'independentExpected': expected_result(raw, bool(index)), 'passed': False}
                    current_case = case
                    report['cases'].append(case)
                    directory = artifacts_root / name
                    directory.mkdir()
                    (directory / 'input.csv').write_bytes(raw)
                    started = time.monotonic()
                    uploaded = response_json(client.post('/desktop/api/files', data={
                        'file': (io.BytesIO(raw), name + '.csv')}, content_type='multipart/form-data'), 201)
                    body = {'mode': 'create', 'goal': goal, 'input': {'fileId': uploaded['id']},
                            'fileIds': [uploaded['id']], 'searchMode': 'off', 'idempotencyKey': name}
                    if saved is not None:
                        body['baseAgentRef'] = saved['agentRef']
                    created = response_json(client.post('/desktop/api/agent-runs', json=body), 202)
                    case['runId'] = created['id']
                    preview = wait(platform, owner, created['id'], case, {'awaiting_confirmation'}, started)
                    case['preview'] = copy.deepcopy(preview)
                    archive_run(platform, owner, preview, case)
                    require(not preview['executionPerformed'] and not preview['steps'], 'Preview executed business code.')
                    require(set(preview['input']) == {'fileId'}, 'Unexpected runtime parameter was invented.')
                    require(preview['allowedFileIds'] == [uploaded['id']], 'Fresh file authorization was not isolated.')
                    require(all('code' in tool and 'registeredTool' not in tool and 'capabilityRef' not in tool
                                for tool in preview['spec']['tools']), 'Generated plan requested an unapproved external Tool.')
                    approval = next(a for a in preview['approvals'] if a['kind'] == 'execute' and a['status'] == 'pending')
                    response_json(client.post(f'/desktop/api/agent-runs/{created["id"]}/approvals/{approval["id"]}',
                        json={'decision': 'approve', 'bindingDigest': approval['bindingDigest'],
                              'idempotencyKey': name + '-approve'}), 200)
                    run = wait(platform, owner, created['id'], case, {'succeeded'}, started)
                    archive_run(platform, owner, run, case)
                    report['realWasi'] = True
                    expected = case['independentExpected']
                    outputs = [step.get('output', {}) for step in run['steps'] if step['kind'] == 'tool' and step['status'] == 'succeeded']
                    case['structuredMatch'] = any(all(value.get(key) == expected[key] for key in expected) for value in outputs)
                    csv_matches = []
                    for artifact in case['artifacts']:
                        if artifact['originalName'] != 'summary.csv':
                            continue
                        data = (directory / artifact['name']).read_bytes()
                        reader = csv.DictReader(io.StringIO(data.decode('utf-8-sig')))
                        rows = list(reader)
                        correct = (reader.fieldnames == ['portfolio', 'market_value_cny', 'position_count']
                            and [row['portfolio'] for row in rows] == sorted(expected['values'])
                            and {row['portfolio']: Decimal(row['market_value_cny']) for row in rows}
                                == {key: Decimal(value) for key, value in expected['values'].items()}
                            and {row['portfolio']: int(row['position_count']) for row in rows} == expected['counts'])
                        csv_matches.append(correct)
                    case['csvMatch'] = bool(csv_matches) and all(csv_matches)
                    case['sourceMatchesExecution'] = all(step.get('codeSha256') == next(
                        source['sha256'] for source in case['sources'] if source['toolId'] == step['toolId'])
                        for step in run['steps'] if step['kind'] == 'tool')
                    require(case['structuredMatch'] and case['csvMatch'] and case['sourceMatchesExecution'],
                            'Independent numerical, CSV or executed-source check failed.')
                    saved = response_json(client.post('/desktop/api/agents', json={
                        'runId': run['id'], 'idempotencyKey': name + '-save'}), 201)
                    case['saved'] = saved
                    case['metadata'] = platform.repository.application_metadata(owner)[saved['agentRef']]
                    case.update(status=run['status'], seconds=round(time.monotonic() - started, 2), passed=True)
                    if index == 0:
                        original_spec = copy.deepcopy(saved['spec'])
                        baseline_case = case
                    else:
                        original = baseline_case
                        case['newApplicationIdentity'] = saved['spec']['id'] != original_spec['id']
                        case['baselineWasPassedToPlanner'] = any('baselineSpec' in call['messages'][-1]['content']
                            and original_spec['id'] in call['messages'][-1]['content'] for call in report['modelCalls'] if call['case'] == name)
                        case['newInputOnly'] = original['run']['input']['fileId'] not in run['input'].values()
                        case['originalDefinitionUnchanged'] = platform.agent(owner, baseline_saved['agentRef']) == original_spec
                        if derived_path is None:
                            old_run = platform.repository.get(owner, original['runId'])
                            case['originalOutputUnchanged'] = old_run['output'] == original['run']['output']
                            case['sourceEvidenceUnchanged'] = case['originalOutputUnchanged']
                        else:
                            case['originalReportUnchanged'] = sha(derived_path.read_bytes()) == report['derivedFrom']['sha256']
                            case['sourceEvidenceUnchanged'] = case['originalReportUnchanged']
                        case['sourceTracked'] = case['metadata']['source'] == {'kind': 'adapt', 'sourceAgentRef': baseline_saved['agentRef']}
                        case['calculationSourceChanged'] = {s['sha256'] for s in case['sources']} != {s['sha256'] for s in original['sources']}
                        require(all(case[key] for key in ('newApplicationIdentity', 'baselineWasPassedToPlanner', 'newInputOnly',
                            'originalDefinitionUnchanged', 'sourceEvidenceUnchanged', 'sourceTracked', 'calculationSourceChanged')),
                            'Adaptation identity, logic or preservation check failed.')
                    save_report()
                report['passed'] = len(report['cases']) == report['maximumRuns'] and all(case['passed'] for case in report['cases'])
                return 0 if report['passed'] else 1
            finally:
                if app is not None:
                    app.extensions['workbench_service'].close()
    except Exception as error:
        report['error'] = str(error)
        if current_case:
            current_case['passed'] = False
        return 1
    finally:
        report['completedAt'] = datetime.now(timezone.utc).isoformat()
        save_report()
        lines = ['# 真实应用创建与自然语言改编', '', f'报告：{output.name}',
                 f'通过：{report["passed"]}；真实模型：{report["realModel"]}；模型调用：{len(report["modelCalls"])}。',
                 '输入为合成金融持仓CSV；临时账号与库；未读取正式数据，未重启服务。', '']
        for case in report['cases']:
            lines.append(f'- {case["name"]}: {case.get("status", "未完成")}；{case.get("seconds", 0)}秒；'
                         f'独立期望{case["independentExpected"]["values"]}；通过{case["passed"]}。')
        if report.get('error'):
            lines.extend(['', '失败保留：' + report['error'].replace(model_config['GITEE_AI_API_KEY'], '[REDACTED]')])
        (artifacts_root / 'summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        print(json.dumps({'report': str(output), 'passed': report['passed'], 'modelCalls': len(report['modelCalls'])}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
