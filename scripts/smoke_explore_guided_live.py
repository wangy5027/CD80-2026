"""Real-provider smoke for Explore retrieval and one-click clarification.

Uses disposable accounts/database and public test questions. Calls the configured
news, web-search and model services, consuming their normal quota. Test actions
explicitly choose a question; clear requests proceed directly to report generation.
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
    parser.add_argument('--output', type=Path, default=ROOT / 'data/explore-guided-fix/live')
    parser.add_argument('--screenshot', action='store_true')
    parser.add_argument('--modules', nargs='+', choices=['event', 'relation', 'history'],
                        default=['event', 'relation', 'history'], help='Run only the selected real-provider scenarios.')
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
        result.update(failure='Existing model configuration is missing; no live model call performed.')
        save('result.json', result)
        return 2

    class QuietHandler(WSGIRequestHandler):
        def log(self, *_args, **_kwargs):
            pass

    with tempfile.TemporaryDirectory(prefix='ficagent-explore-guided-live-') as temporary:
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
                # All inputs are this script's public test questions/news. Never
                # record cookies, keys, provider configuration or account data.
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

        def screenshot(session_id, run, module, phase):
            if not args.screenshot:
                return
            subprocess.run(['node', str(ROOT / 'scripts/capture_explore_live.mjs')],
                           input=json.dumps({'origin': origin, 'session_id': session_id,
                               'run_id': run['id'], 'phase': phase, 'materials': True,
                               'cookies': [{'name': c.name, 'value': c.value, 'url': origin} for c in client.cookies],
                               'output': str((args.output / f'{module}-{phase}.png').resolve())}),
                           text=True, check=True, timeout=50)

        try:
            assert client.get(base + '/explore/sessions', timeout=5).status_code == 401
            api('POST', '/register', json={'name': 'Explore 澄清真实验收',
                'email': 'explore-guided-live@example.invalid', 'password': secrets.token_urlsafe(24)})
            news = api('GET', '/news?source=eastmoney&limit=5')
            assert news['items'], 'Real news endpoint returned no materials.'
            result['news'] = {'source': news['source'], 'rows_in_window': news['pagination']['total'],
                              'fetched_at': news['cache']['fetched_at']}
            questions = [
                ('event', '半导体最近发生了什么？我还没有确定要研究哪一件事。'),
                ('relation', '最近半导体新闻里，这些事情有什么关系？我还没有选定具体比较的主体。'),
                ('history', '最近半导体这些事以前发生过吗？我还没有确定具体事件或待验证判断。'),
            ]
            for module, question in questions:
                if module not in args.modules:
                    continue
                started = time.monotonic()
                record = {'module': module, 'question': question, 'ok': False}
                result['runs'].append(record)
                session = api('POST', '/explore/sessions', json={'title': f'{module} 澄清真实烟测'})['session']
                payload = {'module': module, 'question': question, 'idempotency_key': secrets.token_hex(16)}
                path = f"/explore/sessions/{session['id']}/runs"
                run = api('POST', path, json=payload)['run']
                record['run_id'] = run['id']
                assert api('POST', path, json=payload)['run']['id'] == run['id']
                run = wait(run)
                save(f'{module}-clarification.json', run)
                assert run['status'] == 'awaiting_clarification', f"{module}: ambiguous input ended {run['status']}"
                assert not run.get('report') and calls[(module, 'report')] == 0
                materials = {item['id']: item for item in run['materials']}
                external = {key for key, item in materials.items() if item['kind'] != 'user'}
                assert external, f'{module}: no real external search/news materials'
                options = run['review']['options']
                assert options and any(set(option['source_refs']) & external for option in options)
                assert all(set(option['source_refs']) <= materials.keys() for option in options)
                baseline = dict(calls)
                for _ in range(3):
                    restored = api('GET', f"/explore/runs/{run['id']}")['run']
                    assert restored['status'] == run['status'] and restored['materials'] == run['materials']
                    time.sleep(.1)
                assert dict(calls) == baseline
                record.update(no_report_before_clarification=True, refresh_read_only=True,
                              grounded_options=len(options), first_material_count=len(materials))
                screenshot(session['id'], run, module, 'clarification')
                print(json.dumps({'module': module, 'phase': 'clarification_verified', 'materials': len(materials)}, ensure_ascii=False), flush=True)

                option = next(item for item in options if set(item['source_refs']) & external)
                before_clarify = calls[(module, 'clarify')]
                body = {'idempotency_key': secrets.token_hex(16), 'revision': run['review']['revision'],
                        'option_id': option['id']}
                path = f"/explore/runs/{run['id']}/clarify"
                run = api('POST', path, json=body)['run']
                assert api('POST', path, json=body)['run']['id'] == run['id']
                run = wait(run)
                save(f'{module}-final-run.json', run)
                record.update(status=run['status'], error=run.get('error'), clarification_rounds=1,
                              elapsed_seconds=round(time.monotonic() - started, 2))
                assert run['status'] in {'partial', 'succeeded'}, f"{module}: report ended {run['status']}: {run.get('error')}"
                assert calls[(module, 'clarify')] == before_clarify, 'Choosing a direction repeated intent checking.'
                assert calls[(module, 'report')] == 1
                assert len(run['clarification_history']) == 1
                report = run['report']
                sent = report_payloads[module]['sources']
                assert {item['id']: item['text_used'] for item in sent} == {item['id']: item['text_used'] for item in report['sources']}
                assert any(item['kind'] == 'web_search' for item in report['sources'])
                download = client.get(base + f"/explore/reports/{report['id']}/markdown", timeout=10)
                assert download.status_code == 200 and download.text.strip()
                save(f'{module}-report.json', report)
                (args.output / f'{module}-report.md').write_text(download.text, encoding='utf-8')
                screenshot(session['id'], run, module, 'report')
                restored = api('GET', f"/explore/sessions/{session['id']}")
                assert len(restored['runs']) == 1 and calls[(module, 'report')] == 1
                record.update(ok=True, exact_material_text=True, option_direct_execution=True,
                              no_report_before_clarification=True, idempotent_create_clarify=True,
                              model_clarify_calls=calls[(module, 'clarify')], model_report_calls=calls[(module, 'report')],
                              source_count=len(report['sources']), report_id=report['id'],
                              web_source_count=sum(item['kind'] == 'web_search' for item in report['sources']),
                              case_count=len(report.get('cases') or []), limitations=report['limitations'])
                save('result.json', result)
                print(json.dumps(record, ensure_ascii=False), flush=True)
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
