"""Real news/search/model smoke for direct clarification and session file context.

Uses a disposable account/database and public news only. The provider observer
records actual model requests/replies without replacing responses. Credentials
and cookies are never saved. This command consumes the configured model quota.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/explore-pool/live')
    parser.add_argument('--screenshot', action='store_true')
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    import requests
    from werkzeug.serving import WSGIRequestHandler, make_server
    from backend.app import create_app
    from backend.app.config import Config

    args.output.mkdir(parents=True, exist_ok=True)

    def save(name, value):
        (args.output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    result = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),
              'real_news': True, 'real_web_search': True, 'real_model': True,
              'temporary_accounts_and_database': True, 'model': Config.GITEE_AI_MODEL,
              'runs': [], 'ok': False}
    if not Config.GITEE_AI_API_KEY:
        result['failure'] = 'Existing model configuration is missing; no live model call performed.'
        save('result.json', result)
        return 2

    class QuietHandler(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix='ficagent-explore-pool-live-') as temporary:
        app = create_app({'DATA_DIR': temporary, 'IMPORT_LEGACY_HISTORY': False,
                          'AGENT_RUNTIME_DIR': str(Path(temporary) / 'no-tool-runtime'),
                          'FRONTEND_DIST': str(ROOT / 'frontend/dist')})
        service = app.extensions['explore_service']
        underlying = service.provider
        calls, report_payloads = Counter(), {}
        calls_lock = threading.Lock()

        class ObservedProvider:
            model = underlying.model

            def json_tokens(self, messages, model, stopped, on_response):
                payload = json.loads(messages[1]['content'])
                module, task = payload['module'], payload['task']
                with calls_lock:
                    calls[(module, task)] += 1
                    number = calls[(module, task)]
                    if task == 'report':
                        report_payloads[module] = payload
                save(f'{module}-{task}-{number}-input.json', payload)
                parts = []
                try:
                    for part in underlying.json_tokens(messages, model, stopped, on_response):
                        parts.append(part)
                        yield part
                finally:
                    (args.output / f'{module}-{task}-{number}-output.txt').write_text(''.join(parts), encoding='utf-8')

            tokens = json_tokens

        service.provider = ObservedProvider()
        server = make_server('127.0.0.1', 0, app, threaded=True, request_handler=QuietHandler)
        app.config['DESKTOP_PORT'] = server.server_port
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f'http://127.0.0.1:{server.server_port}'
        base = origin + '/desktop/api'
        client = requests.Session()
        client.trust_env = False

        def api(method, path, **kwargs):
            response = client.request(method, base + path, timeout=30, **kwargs)
            if not response.ok:
                raise RuntimeError(f'{method} {path}: HTTP {response.status_code}')
            return response.json()

        def wait(run):
            deadline = time.monotonic() + 400
            while run['status'] in {'queued', 'running'}:
                if time.monotonic() > deadline:
                    api('POST', f"/explore/runs/{run['id']}/cancel", json={})
                    raise RuntimeError('Live phase deadline exceeded; cancellation requested.')
                time.sleep(1)
                run = api('GET', f"/explore/runs/{run['id']}")['run']
            return run

        def screenshot(session_id, run, name, phase='report'):
            if args.screenshot:
                subprocess.run(['node', str(ROOT / 'scripts/capture_explore_live.mjs')],
                               input=json.dumps({'origin': origin, 'session_id': session_id,
                                   'run_id': run['id'], 'phase': phase, 'materials': True, 'pool': True,
                                   'cookies': [{'name': c.name, 'value': c.value, 'url': origin} for c in client.cookies],
                                   'output': str((args.output / f'{name}.png').resolve())}),
                               text=True, check=True, timeout=60)

        def create_run(session_id, module, question, **extra):
            path = f'/explore/sessions/{session_id}/runs'
            body = {'module': module, 'question': question, 'idempotency_key': secrets.token_hex(16), **extra}
            run = api('POST', path, json=body)['run']
            assert api('POST', path, json=body)['run']['id'] == run['id'], 'Creation replay made a second run.'
            return wait(run)

        def clarify(run, **choice):
            path = f"/explore/runs/{run['id']}/clarify"
            body = {'idempotency_key': secrets.token_hex(16), 'revision': run['review']['revision'], **choice}
            run = api('POST', path, json=body)['run']
            assert api('POST', path, json=body)['run']['id'] == run['id'], 'Clarification replay made a second run.'
            return wait(run)

        def assert_clarification(run, module):
            save(f'{module}-clarification.json', run)
            assert run['status'] == 'awaiting_clarification', f"{module}: ambiguous input ended {run['status']}"
            assert not run.get('report') and calls[(module, 'report')] == 0
            materials = {item['id']: item for item in run['materials']}
            external = {key for key, item in materials.items() if item['kind'] != 'user'}
            assert external, f'{module}: no real external news/search results'
            options = run['review']['options']
            assert len(options) == 3, f'{module}: expected three actionable options'
            assert all(set(option['source_refs']) <= materials.keys() for option in options)
            assert any(set(option['source_refs']) & external for option in options)
            before = dict(calls)
            for _ in range(3):
                restored = api('GET', f"/explore/runs/{run['id']}")['run']
                assert restored['status'] == run['status'] and restored['materials'] == run['materials']
            assert dict(calls) == before, 'Reading clarification called a provider.'
            print(json.dumps({'module': module, 'phase': 'clarification_verified', 'options': 3}, ensure_ascii=False), flush=True)

        def completed(run, module, started):
            save(f'{module}-final-run.json', run)
            assert run['status'] in {'partial', 'succeeded'}, f"{module}: report ended {run['status']}: {run.get('error')}"
            assert calls[(module, 'report')] == 1
            report = run['report']
            assert report['module'] == module
            sent = report_payloads[module]['sources']
            assert {item['id']: item['text_used'] for item in sent} == {item['id']: item['text_used'] for item in report['sources']}
            assert any(item['kind'] == 'web_search' for item in report['sources'])
            download = client.get(base + f"/explore/reports/{report['id']}/markdown", timeout=10)
            assert download.status_code == 200 and download.text.strip()
            save(f'{module}-report.json', report)
            (args.output / f'{module}-report.md').write_text(download.text, encoding='utf-8')
            record = {'module': module, 'run_id': run['id'], 'report_id': report['id'],
                      'status': run['status'], 'elapsed_seconds': round(time.monotonic() - started, 2),
                      'model_clarify_calls': calls[(module, 'clarify')], 'model_report_calls': calls[(module, 'report')],
                      'source_count': len(report['sources']), 'case_count': len(report.get('cases') or []),
                      'limitations': report['limitations'], 'ok': True}
            result['runs'].append(record)
            save('result.json', result)
            print(json.dumps(record, ensure_ascii=False), flush=True)
            return report

        try:
            assert client.get(base + '/explore/sessions', timeout=5).status_code == 401
            api('POST', '/register', json={'name': 'Explore 文件池真实验收',
                'email': 'explore-pool-live@example.invalid', 'password': secrets.token_urlsafe(24)})
            news = api('GET', '/news?source=eastmoney&limit=5')
            assert news['items'], 'Real news endpoint returned no materials.'
            result['news'] = {'source': news['source'], 'rows_in_window': news['pagination']['total'],
                              'fetched_at': news['cache']['fetched_at']}
            session = api('POST', '/explore/sessions', json={'title': '三模块共享文件真实烟测'})['session']
            sid = session['id']
            pool_path = f'/explore/sessions/{sid}/files'
            context_path = f'/explore/sessions/{sid}/context'

            started = time.monotonic()
            run = create_run(sid, 'event', '半导体最近发生了什么？我还没有确定要研究哪一件事。')
            assert_clarification(run, 'event')
            files = api('GET', pool_path)['files']
            assert files, 'Retrieved materials were not saved to the session pool.'
            save('pool-before-report.json', files)
            screenshot(sid, run, 'event-clarification', 'clarification')
            initial_calls = calls[('event', 'clarify')]
            option = run['review']['options'][0]
            run = clarify(run, option_id=option['id'])
            assert calls[('event', 'clarify')] == initial_calls, 'Selecting an explicit option re-entered the intent model.'
            event_report = completed(run, 'event', started)
            screenshot(sid, run, 'event-report')
            result['option_direct_execution'] = True

            assert event_report.get('file_id'), 'Completed report has no session pool file.'
            files = api('GET', pool_path)['files']
            assert any(item['id'] == event_report['file_id'] and item['kind'] == 'report' for item in files)
            material_file = next(item for item in files if item['origin'] == 'web_search')
            selected_ids = [event_report['file_id'], material_file['id']]
            save('pool-after-event.json', files)
            for file_id in selected_ids:
                detail = api('GET', pool_path + '/' + file_id)['file']
                download = client.get(base + pool_path + '/' + file_id + '/download', timeout=10)
                assert download.status_code == 200 and download.text == detail['content']
                assert download.headers.get('X-Content-Type-Options') == 'nosniff'
                assert 'attachment' in download.headers.get('Content-Disposition', '')
                assert detail['name'].endswith('.md')
            result['pool_preview_and_download'] = True

            # Saved module selection is used when the new run omits overrides.
            selection = {'module': 'relation', 'mode': 'manual', 'file_ids': selected_ids}
            api('PUT', context_path, json=selection)
            preferences = api('GET', context_path)['contexts']
            assert preferences['relation'] == selection
            assert preferences['event']['mode'] == 'auto'
            started = time.monotonic()
            question = (f"请以发现关联为主，结合所选文件研究具体事件“{event_report['title']}”："
                        '列出事件涉及的企业、供应商与下游客户关系，解释影响方向、机制、条件及来源；'
                        '缺少具体主体证据就明确说明，不需要先给我选择方向。')
            run = create_run(sid, 'relation', question, source_report_id=event_report['id'])
            assert run['context_selection'] == selection
            assert [item['file_id'] for item in run['context_files']] == selected_ids
            assert run.get('context_snapshot') == [], 'Manual context unexpectedly included an ancestor report.'
            relation_report = completed(run, 'relation', started)
            assert [item['file_id'] for item in report_payloads['relation']['context_files']] == selected_ids
            assert report_payloads['relation'].get('context') == [], 'Unselected ancestry leaked into the report model.'
            screenshot(sid, run, 'relation-report')
            result['cross_module_manual_context'] = True

            # A later module uses a single material, not its source report. The
            # explicit source_report_id records branching, not hidden model context.
            history_selection = {'module': 'history', 'mode': 'manual', 'file_ids': [material_file['id']]}
            api('PUT', context_path, json=history_selection)
            started = time.monotonic()
            run = create_run(sid, 'history', '最近半导体这些事以前发生过吗？我还没有确定具体事件或待验证判断。',
                             source_report_id=relation_report['id'])
            assert_clarification(run, 'history')
            screenshot(sid, run, 'history-clarification', 'clarification')
            custom = (f"待验证判断：具体新闻事件“{event_report['title']}”对半导体上下游产业的影响，"
                      '是否取决于实际供需传导而非仅由市场情绪决定？请查找至少90天前的可得案例，'
                      '对照事件背景、传导机制及差异；只做定性检验，证据不足请明确说明，不编造收益率。')
            before_custom = calls[('history', 'clarify')]
            run = clarify(run, question=custom)
            assert calls[('history', 'clarify')] == before_custom + 1, 'Custom input did not receive one fresh intent check.'
            history_report = completed(run, 'history', started)
            assert [item['file_id'] for item in report_payloads['history']['context_files']] == [material_file['id']]
            assert report_payloads['history'].get('context') == [], 'History received an unselected report.'
            assert run['context_selection'] == history_selection
            screenshot(sid, run, 'history-report')
            result['custom_clear_direct_execution'] = True

            # File rename/removal changes future choices, never the saved input
            # of a finished report. Reads/downloads do not call any model.
            immutable = relation_report['context_files']
            before = dict(calls)
            api('PATCH', pool_path + '/' + material_file['id'], json={'name': '已核对的半导体搜索摘要.md'})
            assert api('GET', pool_path + '/' + material_file['id'])['file']['name'] == '已核对的半导体搜索摘要.md'
            api('DELETE', pool_path + '/' + material_file['id'])
            assert client.get(base + pool_path + '/' + material_file['id'], timeout=10).status_code == 404
            restored_report = api('GET', f"/explore/reports/{relation_report['id']}")['report']
            assert restored_report['context_files'] == immutable
            assert api('GET', context_path)['contexts']['history']['file_ids'] == []
            restored = api('GET', f'/explore/sessions/{sid}')
            assert len(restored['runs']) == 3
            assert {item['id'] for item in restored['runs']} == {item['run_id'] for item in result['runs']}
            assert dict(calls) == before
            save('pool-final.json', api('GET', pool_path)['files'])
            save('context-final.json', api('GET', context_path))
            result.update(immutable_context_snapshot=True, refresh_read_only=True,
                          one_session_three_modules=True, idempotent_create_and_clarify=True)
            result['ok'] = True
        except Exception as error:
            result['failure'] = str(error)
        finally:
            client.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            app.extensions['workbench_service'].close()
            result['model_calls'] = {f'{module}:{task}': count for (module, task), count in calls.items()}
            save('result.json', result)
    print(json.dumps({'ok': result['ok'], 'result': str(args.output / 'result.json'), 'failure': result.get('failure')}, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
