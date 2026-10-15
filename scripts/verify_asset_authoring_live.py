"""Opt-in read-only AC-AS-027 data preflight; no model or production writes.

Uses a temporary account/DATA_DIR and the complete official Tool snapshot.
Only the configured loopback data service is read. The initial official Tool
trial allows five requests. --targeted continues its report with exact code/date
filters on the same declared connection, with 40 cumulative requests at most.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preflight', action='store_true', required=True,
                        help='Read actual 000001.SZ 2025 daily data; never call a model.')
    parser.add_argument('--targeted', action='store_true', help='Use exact date-filtered query_v2 on the same local connection.')
    parser.add_argument('--prior-report', type=Path, help='Required with --targeted; carries the previous request count forward.')
    options = parser.parse_args()
    if options.targeted != bool(options.prior_report):
        parser.error('--targeted and --prior-report must be supplied together.')
    from dotenv import dotenv_values
    from backend.app import create_app
    from backend.app.modules.capability_library.components import build_snapshot, invoke_snapshot
    from backend.app.modules.capability_library.contracts import digest, fail
    from backend.app.modules.capability_library.official import copy_version, PUBLIC
    from backend.app.modules.capability_library.read_budget import task_reads
    from backend.app.modules.sub_agent.diagnostics import redacted

    configured = dotenv_values(ROOT / '.env')
    data_config = dotenv_values(ROOT / 'cd80-data-api/.env')
    read_key = next((value for name in ('CD80_READ_API_KEY', 'CD80_ADMIN_API_KEY')
                     for value in (os.environ.get(name), configured.get(name), data_config.get(name)) if value), '')
    output_dir = ROOT / 'data/asset-authoring-live'
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / ('preflight-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')
    query = {'code': '000001.SZ', 'period': 'day', 'start_date': '2025-01-01', 'end_date': '2025-12-31', 'limit': 100}
    previous = json.loads(options.prior_report.read_text(encoding='utf-8')) if options.prior_report else {}
    if previous and (previous.get('query') != query or previous.get('realModel') is not False
                     or type(previous.get('sourceReads')) is not int or not 0 <= previous['sourceReads'] < 40):
        parser.error('Prior report must describe this exact read-only query and have remaining request budget.')
    prior_reads = previous.get('sourceReads', 0)
    read_limit = 40 if options.targeted else 5
    report = {'format': 'cd80.asset-authoring-preflight/1', 'startedAt': datetime.now(timezone.utc).isoformat(),
              'realModel': False, 'productionUserDataRead': False, 'productionWrites': False,
              'source': 'date-scoped query_v2 using official Tool connection declaration' if options.targeted else
                        'official Tool snapshot via loopback read-only connector', 'query': query,
              'officialToolExecutionCompleted': False, 'sourceReadLimit': read_limit, 'resultPageLimit': 40 if options.targeted else 5,
              'priorSourceReads': prior_reads, 'sourceReads': prior_reads, 'sourceRows': 0,
              'priorReport': {'name': options.prior_report.name, 'sha256': digest(options.prior_report.read_bytes())} if previous else None,
              'pages': [], 'records': [], 'passed': False,
              'sourceSha256': {str(path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
                  Path('scripts/verify_asset_authoring_live.py'), Path('backend/app/modules/capability_library/components.py'),
                  Path('backend/app/modules/capability_library/data_connections.py'))}}

    def account(reads, rows):
        if report['sourceReads'] + reads > read_limit:
            fail('READ_BUDGET_EXCEEDED', 'Read-only preflight stopped before exceeding its cumulative request limit.', 429)
        report['sourceReads'] += reads
        report['sourceRows'] += rows

    def save():
        # Drop runtime cursors and connection authority from the portable report.
        text = json.dumps(redacted(report), ensure_ascii=False, indent=2)
        if read_key:
            text = text.replace(read_key, '[REDACTED]')
        output.write_text(text, encoding='utf-8')

    app = None
    try:
        with tempfile.TemporaryDirectory(prefix='isolated-', dir=output_dir) as directory:
            try:
                config = {'TESTING': True, 'DATA_DIR': directory, 'IMPORT_LEGACY_HISTORY': False,
                    'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime'), 'CAPABILITY_SKIP_PUBLIC_SEED': False,
                    'CD80_DATA_API_URL': 'http://127.0.0.1:8897', 'CD80_READ_API_KEY': read_key,
                    'CD80_ADMIN_API_KEY': '', 'CD80_DATA_ADMIN_USERS': '', 'SFC_TOKEN': '', 'SFC_KEY_FILE': '',
                    'CAPABILITY_MCP_POLICY_FILE': '', 'GITEE_AI_API_KEY': ''}
                app = create_app(config)
                client = app.test_client()
                account_response = client.post('/desktop/api/register', json={
                    'email': 'asset-preflight@example.invalid', 'password': 'temporary-preflight-only'})
                if account_response.status_code != 200:
                    raise RuntimeError('Temporary account creation failed.')
                owner = account_response.json['user']['id']
                library = app.extensions['capability_library']
                source = next(v for v in library.store.list(PUBLIC, 'version')
                              if v['manifest']['source']['identity'] == 'cd80/cd80-history-tool')
                copied = copy_version(library, owner, source)
                snapshot = build_snapshot(library, owner, copied['assetId'], copied['versionId'])
                manifest = snapshot['manifest']
                report['component'] = {'sourceIdentity': manifest['source']['identity'],
                    'contentDigest': snapshot['contentDigest'], 'snapshotDigest': snapshot['snapshotDigest'],
                    'files': manifest['files'], 'executionType': manifest['tool']['execution']['type'],
                    'protocolVersion': manifest['tool']['execution'].get('protocolVersion')}
                # Preflight is an explicit read trial, not asset validation or enablement.
                parameters = dict(query)
                if options.targeted:
                    arguments = {'table': 'cd80_daily', 'columns': ['ts_code', 'trade_date', 'open', 'high', 'low',
                        'close', 'pre_close', 'pct_chg', 'vol', 'amount'], 'filters': [
                            {'field': 'ts_code', 'op': 'eq', 'value': query['code']},
                            {'field': 'trade_date', 'op': 'date_gte', 'value': query['start_date']},
                            {'field': 'trade_date', 'op': 'date_lte', 'value': query['end_date']}],
                        'order_by': [{'field': 'trade_date', 'direction': 'asc'}], 'page_size': 100}
                    report['sourceQuery'] = arguments
                    report['calendarEvidenceGap'] = '当前平台声明的本地表目录没有交易日历；未查询其他证券或日期范围。'
                    with task_reads(account, scope='asset-targeted-preflight-' + str(uuid4())):
                        request_args = dict(arguments)
                        for page_number in range(1, 41 - prior_reads):
                            account(1, 0)  # This direct connection path has no run_connected charge wrapper.
                            value = library.data_connections.call(owner, manifest['tool']['execution']['connections'],
                                {'connection': 'cd80.local-data', 'operation': 'query_v2', 'arguments': request_args}, threading.Event())
                            rows = value.get('data', [])
                            account(0, len(rows))
                            pagination = value.get('pagination', {})
                            report['pages'].append({'page': page_number, 'rows': len(rows), 'snapshot': value.get('snapshot'),
                                'coverage': value.get('coverage'), 'hasMore': bool(pagination.get('has_more')),
                                'requestDigest': digest(request_args), 'responseDigest': digest(value)})
                            report['records'].extend(rows)
                            if not pagination.get('has_more'):
                                break
                            if not pagination.get('next_cursor'):
                                raise RuntimeError('Source claims more records without a continuation cursor.')
                            request_args = {**arguments, 'cursor': pagination['next_cursor']}
                else:
                    with task_reads(account, scope='asset-preflight-' + str(uuid4())):
                        for page_number in range(1, 6):
                            invocation = invoke_snapshot(library, owner, snapshot, parameters, stopped=threading.Event(),
                                context={'kind': 'asset_authoring_preflight', 'case': 'AC-AS-027', 'page': page_number})
                            if invocation.get('status') != 'succeeded':
                                raise RuntimeError('Official snapshot invocation did not succeed.')
                            content = invocation['result']['content']
                            rows = content.get('records', [])
                            page = {'page': page_number, 'rows': len(rows), 'resultSchema': content.get('result_schema'),
                                'source': content.get('source'), 'returnedRange': content.get('returned_range'),
                                'coverage': content.get('coverage'), 'units': content.get('units'), 'warnings': content.get('warnings'),
                                'sources': content.get('sources'), 'asOf': content.get('as_of'),
                                'securityAsOf': content.get('security_as_of'), 'hasMore': bool(content.get('pagination', {}).get('has_more')),
                                'actualTrace': invocation.get('dataReads', [])}
                            report['pages'].append(page)
                            report['records'].extend(rows)
                            pagination = content.get('pagination', {})
                            if not pagination.get('has_more'):
                                break
                            if pagination.get('next_cursor'):
                                parameters = {**query, 'cursor': pagination['next_cursor']}
                            elif pagination.get('next_offset') is not None:
                                parameters = {**query, 'offset': pagination['next_offset']}
                            else:
                                raise RuntimeError('Source claims more records without an actual continuation token.')
                    report['officialToolExecutionCompleted'] = True
                rows = report['records']
                def stamp(row):
                    raw = str(row.get('date') or row.get('trade_date')).strip().removesuffix('.0')
                    raw = raw[:4] + '-' + raw[4:6] + '-' + raw[6:] if len(raw) == 8 and raw.isdigit() else raw
                    return date.fromisoformat(raw).isoformat()
                dates = [stamp(row) for row in rows]
                def valid_price(row):
                    try:
                        value = Decimal(str(row.get('close')))
                        return value.is_finite() and value > 0
                    except (ValueError, InvalidOperation):
                        return False
                complete_read = bool(report['pages']) and not report['pages'][-1]['hasMore']
                prices_valid = all(valid_price(row) for row in rows)
                basic_available = len(rows) >= 2 and prices_valid and len(set(dates)) == len(rows)
                report['summary'] = {'rows': len(rows), 'distinctDates': len(set(dates)),
                    'firstDate': min(dates) if dates else None, 'lastDate': max(dates) if dates else None,
                    'retrievalComplete': complete_read, 'allDatesInRequestedRange': all('2025-01-01' <= day <= '2025-12-31' for day in dates),
                    'priceBasis': '本地cd80_daily未复权价格，沿用官方股票日线字段口径；未引入复权或填补缺失记录。',
                    'marketCompleteness': 'unknown', 'calendarEvidenceObtained': False,
                    'finitePositiveClosePrices': prices_valid, 'basicAnalysisDataAvailable': basic_available,
                    'analysisScope': '仅对实际返回记录计算；不能据此声称覆盖全年所有应有交易日。',
                    'units': {'prices': '元', 'vol': '手', 'amount': '千元', 'pct_chg': '百分比'},
                    'unitsBasis': '官方股票日线 Tool 的字段口径；本次未另外核验源供应商。'}
                report['passed'] = complete_read
                report['ac027'] = {'status': 'blocked_no_records' if not rows else 'blocked_unverified_coverage',
                    'basicNumericalAnalysisCanProceed': basic_available,
                    'wholeYearAcceptanceCanProceed': False,
                    'reason': '实际区间无记录，不能计算、绘图或声称全年结果。' if not rows else
                              '已取得实际记录，但该Tool未提供交易日历或全年市场覆盖证明；分页完成不等于全年完整。',
                    'realModelStageStarted': False}
            finally:
                if app is not None:
                    app.extensions['workbench_service'].close()
    except Exception as error:
        report['error'] = {'code': getattr(error, 'code', type(error).__name__), 'message': str(error)[:1500]}
        report['ac027'] = {'status': 'blocked_preflight_error', 'wholeYearAcceptanceCanProceed': False,
                          'realModelStageStarted': False, 'reason': '预检失败，未扩大数据源或调用预算。'}
    finally:
        report['sourceReadsThisRun'] = report['sourceReads'] - prior_reads
        report['completedAt'] = datetime.now(timezone.utc).isoformat()
        save()
    print(json.dumps({'report': str(output), 'passed': report['passed'], 'sourceReads': report['sourceReads'],
                      'rows': len(report['records']), 'ac027': report['ac027']}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
