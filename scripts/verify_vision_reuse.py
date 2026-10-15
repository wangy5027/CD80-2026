"""Opt-in real-model research -> saved recipe -> changed-input acceptance.

Uses a fresh temporary account/database, the official local history Tool and
read-only loopback financial data. No production user history is opened.
Only model configuration and existing data-read authentication are copied from
.env; credentials stay in backend configuration and are never reported.
At most two real tasks; ordinary/edge runs allow twelve model calls and 360
seconds per task, full analysis allows eighteen calls and 540 seconds.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from decimal import Decimal
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validation_suite_helpers import run_validation_suite, execution_complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--live', action='store_true', help='Run two bounded real-model tasks using ordinary API quota.')
    choice.add_argument('--live-analysis', action='store_true', help='Two real delegated tasks: query, compute, plot, review and reuse with changed dates (540 seconds each).')
    choice.add_argument('--live-edge-cases', action='store_true', help='Two real-model boundary tasks: stop on actual empty data, and stop when the named Tool is absent (360 seconds each).')
    choice.add_argument('--preflight', action='store_true', help='Check configuration and actual Tool read/verification only; no model calls.')
    choice.add_argument('--recheck-completeness', action='store_true', help='One additional real task to recheck the discovered calendar-evidence defect after its fix.')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from backend.app import create_app
    from backend.app.modules.capability_library.official import copy_version
    from backend.app.modules.capability_library.contracts import canonical

    configured = dotenv_values(ROOT / '.env')
    model_keys = ('GITEE_AI_API_KEY', 'GITEE_AI_BASE_URL', 'GITEE_AI_MODEL')
    model_config = {key: os.environ.get(key) or configured.get(key) for key in model_keys if os.environ.get(key) or configured.get(key)}
    data_config = dotenv_values(ROOT / 'cd80-data-api/.env')
    read_key = (os.environ.get('CD80_READ_API_KEY') or configured.get('CD80_READ_API_KEY') or data_config.get('CD80_READ_API_KEY') or
                os.environ.get('CD80_ADMIN_API_KEY') or configured.get('CD80_ADMIN_API_KEY') or data_config.get('CD80_ADMIN_API_KEY') or '')
    if not model_config.get('GITEE_AI_API_KEY'):
        parser.error('No configured model credential; no requests were sent.')
    task_seconds = 540 if args.live_analysis else 360
    started_at = datetime.now(timezone.utc).isoformat()
    report = {'startedAt': started_at, 'realModel': not args.preflight, 'productionUserDataRead': False, 'productionWrites': False,
              'model': model_config.get('GITEE_AI_MODEL', 'GLM-5.3'), 'maximumTasks': 1 if args.recheck_completeness else 2,
              'maxSecondsPerTask': task_seconds, 'cases': [], 'passed': False,
              'sourceSha256': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                  'backend/app/modules/sub_agent/model.py', 'backend/app/modules/sub_agent/service.py',
                  'backend/app/modules/evolution/service.py', 'scripts/verify_vision_reuse.py', 'scripts/validation_suite_helpers.py')}}
    output_dir = ROOT / 'data' / 'vision-reuse-live'
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / ((('analysis-' if args.live_analysis else 'edge-cases-') + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json') if (args.live_analysis or args.live_edge_cases) else
                           'completeness-recheck.json' if args.recheck_completeness else 'report.json' if args.live else 'preflight.json')
    if args.recheck_completeness:
        report['budgetRevisionReason'] = 'After two completed tasks exposed an unsupported missing-trading-day claim, one additional same-scope task is authorized to validate the evidence-constraint fix.'
    app = None

    def clean_error(error):
        value = str(error)
        for name, secret in model_config.items():
            if 'KEY' in name and secret:
                value = value.replace(secret, '[REDACTED]')
        if read_key:
            value = value.replace(read_key, '[REDACTED]')
        return value[:2000]

    def save_report():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    @contextmanager
    def isolated_workspace():
        with tempfile.TemporaryDirectory(prefix='vision-live-', dir=output_dir) as directory:
            try:
                yield directory
            finally:
                if app is not None:
                    app.extensions['workbench_service'].close()

    def await_run(service, owner, run, case_name):
        started = time.monotonic()
        last_status, last_notice = None, 0
        while True:
            run = service.store.run(owner, run['id'])
            elapsed = time.monotonic() - started
            state = (run['status'], len(run['steps']))
            if state != last_status or elapsed - last_notice >= 20:
                print(json.dumps({'case': case_name, 'status': run['status'], 'steps': len(run['steps']), 'seconds': round(elapsed)}, ensure_ascii=True), flush=True)
                last_status, last_notice = state, elapsed
            if run['status'] in {'succeeded', 'partial', 'failed', 'cancelled', 'interrupted', 'awaiting_user'}:
                break
            if elapsed > task_seconds + 10:
                service.command(owner, run['conversationId'], run['id'], 'cancel', {'revision': run['revision']}, str(uuid4()))
                raise RuntimeError('Real task exceeded the bounded acceptance deadline.')
            time.sleep(.5)
        library = app.extensions['capability_library']
        invocations = [v for v in library.store.list(owner, 'invocation') if v.get('taskId') == run['conversationId']]
        case = {'case': case_name, 'status': run['status'], 'kind': run['kind'], 'seconds': round(elapsed, 2),
                'error': run.get('error'), 'question': run.get('question'), 'answer': run.get('answer'),
                'executedTools': [{'input': v['input'], 'status': v['status'], 'result': v.get('result', {}).get('content')} for v in invocations],
                'steps': [{'type': s.get('type'), 'name': s.get('name'), 'status': s.get('status'), 'error': s.get('error')} for s in run['steps']],
                'correctionsUsed': run.get('correctionsUsed'), 'modelCalls': run.get('modelCalls', []),
                'reviews': run.get('reviews'), 'passed': run['status'] == 'succeeded' and any(v['status'] == 'succeeded' for v in invocations)}
        report['cases'].append(case)
        if args.live_edge_cases:
            case['artifacts'] = [{'name': item.get('name'), 'id': item['id']} for item in run.get('artifacts', [])]
            computed = [step for step in run['steps'] if step.get('type') == 'code' and step['status'] == 'succeeded']
            case['boundaryChecks'] = {'noSuccessfulComputation': not computed,
                'noDeliveries': not run.get('deliveries'), 'notApplication': run['kind'] != 'application',
                'responsePresent': bool(run.get('answer') or run.get('question')),
                'noFailure': run['status'] in {'succeeded', 'partial', 'awaiting_user'}}
            if case_name == 'actual_empty_data':
                actual = [v for v in invocations if v['status'] == 'succeeded']
                case['boundaryChecks']['actualEmptyIntervalObserved'] = bool(actual) and all(
                    v['input'].get('start_date') == '2099-01-01' and v['input'].get('end_date') == '2099-01-02'
                    and v.get('result', {}).get('content', {}).get('records') == [] for v in actual)
            else:
                case['boundaryChecks']['noSubstituteInvocation'] = not invocations
                case['boundaryChecks']['noOtherToolExecuted'] = not any(
                    s.get('type') == 'tool' and s['status'] == 'succeeded'
                    and s.get('name') not in {'library_catalog', 'library_load'} for s in run['steps'])
            case['passed'] = all(case['boundaryChecks'].values())
            case['manualReviewRequired'] = 'Inspect the actual answer/question for a clear missing-data or missing-Tool explanation, without fabricated metrics or claims of completed analysis.'
        for invocation in invocations:
            rows = invocation.get('result', {}).get('content', {}).get('records', [])
            if rows and all('date' in row and 'close' in row for row in rows):
                ordered = sorted(rows, key=lambda row: row['date'])
                first, last = ordered[0], ordered[-1]
                case['independentNumericalCheck'] = {'firstDate': first['date'], 'lastDate': last['date'],
                    'firstClose': first['close'], 'lastClose': last['close'], 'rows': len(rows),
                    'priceChangePercent': str((Decimal(last['close']) / Decimal(first['close']) - 1) * 100)}
                peak, max_drawdown = Decimal(first['close']), Decimal(0)
                for row in ordered:
                    close = Decimal(row['close'])
                    peak = max(peak, close)
                    max_drawdown = max(max_drawdown, (peak - close) / peak * 100)
                case['independentNumericalCheck']['maxDrawdownPercent'] = str(max_drawdown)
        if args.live_analysis:
            code_steps = [s for s in run['steps'] if s['type'] == 'code' and s['status'] == 'succeeded']
            results = [s.get('result', {}).get('content', {}) for s in code_steps]
            expected = case.get('independentNumericalCheck', {})
            def matches(value):
                try:
                    return (int(value['rows']) == expected['rows'] and
                        abs(Decimal(str(value['priceChangePercent'])) - Decimal(expected['priceChangePercent'])) < Decimal('0.0001') and
                        abs(Decimal(str(value['maxDrawdownPercent'])) - Decimal(expected['maxDrawdownPercent'])) < Decimal('0.0001'))
                except (KeyError, ValueError, ArithmeticError, TypeError):
                    return False
            case['computedResults'] = results
            case['numericalMatch'] = any(matches(value) for value in results)
            case['artifacts'] = []
            extensions = set()
            artifact_dir = output.with_suffix('') / case_name
            artifact_dir.mkdir(parents=True, exist_ok=True)
            csv_matches = False
            for meta in run.get('deliveries', []):
                saved_meta, data = service.store.artifact(owner, meta['id'])
                name = Path(saved_meta['name']).name
                (artifact_dir / name).write_bytes(data)
                extension = Path(name).suffix.lower()
                extensions.add(extension)
                if extension == '.csv':
                    try:
                        rows = list(csv.DictReader(io.StringIO(data.decode('utf-8-sig'))))
                        values = sorted((r['date'], Decimal(r['close'])) for r in rows)
                        actual = sorted((r['date'], Decimal(r['close'])) for v in invocations if v['status'] == 'succeeded'
                                        for r in v.get('result', {}).get('content', {}).get('records', []))
                        csv_matches = csv_matches or values == actual
                    except (KeyError, ValueError, UnicodeError, ArithmeticError):
                        pass
                case['artifacts'].append({'name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                    'pngSignature': data.startswith(b'\x89PNG\r\n\x1a\n') if extension == '.png' else None})
            case['csvMatchesActualToolRows'] = csv_matches
            case['passed'] = (case['passed'] and run['kind'] == 'delegate' and bool(code_steps) and
                              case['numericalMatch'] and csv_matches and {'.png', '.csv', '.py'} <= extensions and
                              any(a.get('pngSignature') for a in case['artifacts']) and
                              any(r.get('decision') == 'accept' for r in run.get('reviews', [])))
        save_report()
        if not case['passed']:
            raise RuntimeError(f"{case_name} failed its execution, artifact or independent numerical checks: {run['status']}.")
        return run

    try:
        with isolated_workspace() as isolated:
            config = {**model_config, 'TESTING': True, 'DATA_DIR': isolated, 'IMPORT_LEGACY_HISTORY': False,
                      'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime'), 'CAPABILITY_SKIP_PUBLIC_SEED': False,
                      'CD80_DATA_API_URL': 'http://127.0.0.1:8897', 'CD80_ADMIN_API_KEY': '', 'CD80_READ_API_KEY': read_key,
                      'CD80_DATA_ADMIN_USERS': '', 'SFC_TOKEN': '', 'SFC_KEY_FILE': '', 'CAPABILITY_MCP_POLICY_FILE': '',
                      'SUB_AGENT_TIMEOUT': task_seconds, 'AGENT_MODEL_MAX_TOKENS': 6000, 'AGENT_MODEL_REASONING_EFFORT': 'low',
                      'GITEE_AI_READ_TIMEOUT_SECONDS': 90}
            app = create_app(config)
            client = app.test_client()
            account = client.post('/desktop/api/register', json={'email': 'vision-live@example.invalid', 'password': 'synthetic-acceptance-account'}).json
            owner = account['user']['id']
            library = app.extensions['capability_library']
            public = '__platform_public__'
            source = next(v for v in library.store.list(public, 'version') if v['manifest']['source']['identity'] == 'cd80/cd80-history-tool')
            copied = copy_version(library, owner, source)
            version = library.version(owner, copied['assetId'], copied['versionId'])
            cases = [{'label': '原请求5条' if limit == 5 else '新请求3条',
                       'input': {'code': '600000.SH', 'period': 'day', 'limit': limit}, 'fileIds': [],
                       'expectation': {'mode': 'assertions', 'assertions': [
                           {'path': ['result_schema'], 'op': 'eq', 'value': 'financial-result/1'},
                           {'path': ['records'], 'op': 'minItems', 'value': 3}]}} for limit in (5, 3)]
            verified = run_validation_suite(library, owner, version, cases, timeout=150)
            report['toolVerificationSuite'] = verified
            save_report()
            if not execution_complete(verified) or verified['status'] != 'passed':
                raise RuntimeError('Official Tool double-input verification failed; inspect per-case evidence.')
            report['toolRowLimitChecks'] = [{'limit': case['input']['limit'], 'rows': len(case['result']['content']['records']),
                'passed': 3 <= len(case['result']['content']['records']) <= case['input']['limit']} for case in verified['cases']]
            if not all(case['passed'] for case in report['toolRowLimitChecks']):
                raise RuntimeError('Actual returned rows do not satisfy both the three-row requirement and the selected limit; no enablement.')
            records = verified['cases'][0]['result']['content']['records']
            dates = sorted(set(str(row.get('date') or row.get('trade_date')) for row in records))
            if len(dates) < 3 or any(date == 'None' for date in dates):
                raise RuntimeError('Not enough real dated records; acceptance stopped without widening the data source.')
            report['toolPreflight'] = {'passed': True, 'sourceIdentity': source['manifest']['source']['identity'],
                                       'rows': len(records), 'dates': dates}
            asset = library.asset(owner, version['assetId'])
            library.update_asset(owner, asset['assetId'], {'enabled': True, 'activeVersionId': version['versionId']}, f'"{asset["revision"]}"')
            print(json.dumps({'preflightPassed': True, 'model': report['model'], 'dates': dates, 'realModel': not args.preflight}), flush=True)
            if args.preflight:
                report['passed'] = True
                save_report()
                return 0
            refs = [{'assetId': version['assetId'], 'versionId': version['versionId']}]
            service = app.extensions['sub_agents']
            service.max_rounds = 18 if args.live_analysis else 12
            if args.live_edge_cases:
                empty_input = {'code': '600000.SH', 'period': 'day', 'start_date': '2099-01-01', 'end_date': '2099-01-02', 'limit': 100}
                empty = library.invoke(owner, {'assetId': version['assetId'], 'versionId': version['versionId'],
                    'configurationId': version['configurationId'], 'input': empty_input, 'fileIds': []})
                assert empty['status'] == 'succeeded' and empty['result']['content']['records'] == [], 'The proposed empty interval is not actually empty.'
                report['emptyDataPreflight'] = {'input': empty_input, 'rows': 0, 'source': empty['result']['content'].get('source')}
                task = ('这是本地数据覆盖检查。必须调用所选本地历史日线工具，查询600000.SH从2099-01-01至2099-01-02（含首尾），最多100条。'
                    '日期已确认，即使区间是未来也直接按本地库实际返回判断，不需先确认日期。若实际少于两条记录，明确说明数据不足并停止，'
                    '不要计算收益率或回撤，不生成数值、图表或文件，不把空数据当成零收益，不猜测缺口原因。'
                    '禁止替换区间、使用其他工具、联网、临时代码补造数据或创建应用。')
                run = service.submit(owner, 'new', {'message': task, 'capabilityRefs': refs, 'fileIds': [], 'searchMode': 'off'}, 'edge-empty-data')
                await_run(service, owner, run, 'actual_empty_data')
                missing_identity = 'vision-missing-volume-tool'
                assert not any(missing_identity in str(v.get('manifest', {}).get('source', {}).get('identity')) for v in library.store.list(owner, 'version'))
                task = ('只使用名为vision-missing-volume-tool的Tool计算600000.SH在2026-09-23至2026-09-30的成交量变化。'
                    '这是强制工具要求，不允许使用其他工具、联网或临时代码替代，也不允许安装或启用新能力。'
                    '若当前目录没有该Tool，明确说明缺失并停止，不要生成数字或文件，不创建、保存或运行应用。')
                run = service.submit(owner, 'new', {'message': task, 'capabilityRefs': [], 'fileIds': [], 'searchMode': 'off'}, 'edge-missing-tool')
                await_run(service, owner, run, 'missing_named_tool')
                report['passed'] = all(case['passed'] for case in report['cases'])
                report['manualReviewRequired'] = 'Both boundary guards passed only if no computation or substitute execution occurred. Read both responses before making a semantic quality claim.'
                save_report()
                return 0 if report['passed'] else 1
            template = ('使用所选本地历史日线工具，查询600000.SH从{{开始日期}}至{{结束日期}}（含首尾）的实际日线。'
                        '根据不复权收盘价计算首尾价格变化率，列出实际首尾日期、价格、计算式和样本条数。'
                        '必须调用工具，最多100条；不存在数据则停止并明确说明。只完成本次分析，不创建应用。')
            if args.live_analysis:
                template = ('使用所选本地历史日线工具，分析600000.SH从{{开始日期}}至{{结束日期}}（含首尾）的实际日线，最多100条。'
                    '取数后基于实际返回文件计算首尾价格变化率和区间样本最大回撤，并制作收盘价折线图；'
                    '数据不足则停止，交易日完整性未知要明确说明，不对无记录日期猜测原因。'
                    '交付PNG折线图、实际用于计算的CSV（date和close列）和执行过的Python代码，以及中文结果说明。'
                    '计算使用Decimal；计算步骤的JSON结果包含rows、priceChangePercent、maxDrawdownPercent（三者均按实际数据，后两项为百分数）。'
                    '优先用已选工具取数，无现成计算或绘图能力时可申请隔离临时代码，读取实际工件，不转抄数据数组。'
                    '禁止联网搜索、安装依赖、写数据库或补造数据。只执行本次研究，不创建应用。')
            initial = template.replace('{{开始日期}}', dates[0]).replace('{{结束日期}}', dates[-1])
            run = service.submit(owner, 'new', {'message': initial, 'capabilityRefs': refs, 'fileIds': [], 'searchMode': 'off'}, 'live-initial')
            run = await_run(service, owner, run, 'initial_analysis')
            if args.recheck_completeness:
                report['passed'] = True
                report['manualReviewRequired'] = 'Verify that the answer states unknown market coverage and does not label absent dates as missing trading days without evidence.'
                save_report()
                return 0
            evolution = app.extensions['evolution']
            recipe = evolution.create(owner, {'name': '实际日线首尾变化率', 'description': '实际调用本地日线，重新计算所选区间的首尾价格变化。',
                'goalTemplate': template, 'constraints': ['使用不复权收盘价；不得把价格变化率写成含分红总收益。', '缺失数据时停止，不编造，不切换其他上游。'],
                'capabilityRefs': refs, 'sourceRunId': run['id']}, 'live-save-recipe')
            second = evolution.run(owner, recipe['id'], {'input': {'开始日期': dates[1], '结束日期': dates[-1]}, 'fileIds': [], 'searchMode': 'off'}, 'live-recipe-run')
            second = await_run(service, owner, second, 'recipe_changed_dates')
            report['recipe'] = {'parameters': recipe['parameters'], 'changedStartDate': dates[1],
                                'separateConversation': run['conversationId'] != second['conversationId'],
                                'executionContext': second.get('executionContext')}
            tool_inputs = [value['input'] for value in report['cases'][-1]['executedTools'] if value['status'] == 'succeeded']
            report['changedInputObserved'] = any(value.get('start_date') == dates[1] for value in tool_inputs)
            report['passed'] = report['recipe']['separateConversation'] and report['changedInputObserved']
            save_report()
            return 0 if report['passed'] else 1
    except Exception as error:
        report['error'] = {'type': type(error).__name__, 'message': clean_error(error)}
        save_report()
        print(json.dumps({'passed': False, 'error': report['error']}, ensure_ascii=True), flush=True)
        return 1
    finally:
        save_report()
        print(json.dumps({'report': str(output), 'passed': report['passed']}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
