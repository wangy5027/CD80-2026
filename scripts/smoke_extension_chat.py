"""Opt-in real model check in a disposable account/database, with real WASI.

Only built-in examples and synthetic/public-domain text are sent. No production
accounts, files, capabilities or conversations are changed.
"""
import json
import hashlib
import platform
from datetime import datetime, timezone
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validation_suite_helpers import run_validation_suite, execution_complete


def main():
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    from backend.app import create_app
    from backend.app.modules.capability_library.packages import zip_bytes
    sources = ['backend/app/modules/capability_library/chat_bridge.py', 'backend/app/modules/workbench/graph_runtime.py', 'backend/app/modules/workbench/service.py', 'scripts/smoke_extension_chat.py', 'scripts/validation_suite_helpers.py']
    report = {'temporaryData': True, 'checks': {}, 'validationSuites': {}, 'passed': False, 'startedAt': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(), 'system': platform.system(),
        'workspaceHashes': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sources}}
    with tempfile.TemporaryDirectory(prefix='cd80-extension-live-') as directory:
        app = create_app({'TESTING': True, 'DATA_DIR': directory, 'IMPORT_LEGACY_HISTORY': False})
        wb = app.extensions['workbench_service']
        lib = app.extensions['capability_library']
        client = app.test_client()
        report['model'] = wb.model_id
        try:
            if not wb.runtime.provider.key:
                raise RuntimeError('Model key not configured')
            owner = client.post('/desktop/api/register', json={'email': 'extension-live@example.invalid', 'password': 'synthetic-only-password'}).json['user']['id']

            def install(folder):
                source = ROOT / 'docs/capability-library/examples' / folder
                package = zip_bytes({p.relative_to(source).as_posix(): p.read_bytes() for p in source.rglob('*') if p.is_file()})
                return lib.import_package(owner, package, folder + '.zip')

            def verify(ref, cases, label):
                version = lib.version(owner, ref['assetId'], ref['versionId'])
                result = run_validation_suite(lib, owner, version, cases)
                report['validationSuites'][label] = result
                if not execution_complete(result): raise RuntimeError('Verification did not complete; inspect per-case suite evidence: ' + result['status'])
                return result

            def enable(ref):
                asset = lib.asset(owner, ref['assetId'])
                lib.update_asset(owner, ref['assetId'], {'enabled': True, 'activeVersionId': ref['versionId']}, f'"{asset["revision"]}"')

            tool = install('decimal-total')
            checked = verify(tool, [
                {'label': '原小数输入', 'input': {'values': ['0.10', '0.20']}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '0.30'}}},
                {'label': '不同整数输入', 'input': {'values': ['2', '5']}, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value': {'total': '7'}}},
            ], 'decimal')
            assert checked['status'] == 'passed'
            enable(tool)
            skill = install('summary-skill')
            checked = verify(skill, [
                {'label': '原反馈资料', 'input': {}, 'fileIds': [], 'expectation': {'mode': 'manual_review', 'prompt': '仅总结：本周收到反馈12条，处理9条，剩余3条等待确认。', 'criteria': '保留12、9、3及对应含义，不虚构原因。'}},
                {'label': '不同库存资料', 'input': {}, 'fileIds': [], 'expectation': {'mode': 'manual_review', 'prompt': '仅总结：本月入库47件，已发出31件，剩余16件尚未分配。', 'criteria': '保留47、31、16及对应含义，不虚构分配原因。'}},
            ], 'summary')
            report['numericFactChecks'] = [{'label': case['label'], 'numbersPresent': all(value in case['result']['content'] for value in facts)}
                for case, facts in zip(checked['cases'], [('12', '9', '3'), ('47', '31', '16')])]
            report['numericFactsPassed'] = all(item['numbersPresent'] for item in report['numericFactChecks'])
            # Presence checks cannot approve meaning, omissions or fabricated causes.
            report['manualReviewRequired'] = checked['manualReviewRequired']
            report['checks']['summary'] = {'status': 'blocked_review_required' if report['numericFactsPassed'] else 'failed_fact_check', 'verificationId': checked['verificationId'],
                'reason': '请逐案审阅实际输出；数字出现不等于人工确认，脚本不自动启用Skill。'}
            cases = {
                'summary': '使用文本总结skill，把下面这段话总结一下汇报给我。如果你找不到skill，就直接回答你找不到。资料：女娲炼石补天，炼成顽石三万六千五百零一块，只用了三万六千五百块，剩下一块弃于青埂峰下。石头通了灵性，因未能补天而自怨自叹。一僧一道谈起红尘荣华，石头动了凡心，苦求携带下凡。二仙告诫红尘乐事不能永远依恃，终是一梦，石头仍执意前往。僧人将大石变成可佩的美玉，携往人间。',
                'decimal': '请调用十进制求和工具，计算0.10与0.20的和。必须真实调用，不能口算替代。',
                'missing': '请使用不存在的星际航线规划skill。如果找不到就直接回答找不到，不要继续任务。',
            }
            for label, query in cases.items():
                if label == 'summary': continue
                response = client.post('/desktop/api/chat', json={'message': query, 'searchMode': 'off'})
                stream = response.data.decode('utf-8')
                task = wb.store.get(owner)['tasks'][0]
                answer = task['messages'][-1].get('content', '')
                checks = {'http': response.status_code, 'errorEvent': 'event: error' in stream,
                    'loadedSkill': 'event: skill_loaded' in stream, 'calledTool': 'event: tool_completed' in stream,
                    'readReference': 'library_resource' in stream, 'answer': answer}
                report['checks'][label] = checks
                print(json.dumps({'case': label, **{k: v for k, v in checks.items() if k != 'answer'}}, ensure_ascii=False), flush=True)
                assert not checks['errorEvent'] and response.status_code == 200
                if label == 'summary': assert checks['loadedSkill'] and checks['readReference']
                if label == 'decimal': assert checks['calledTool'] and '0.30' in answer
                if label == 'missing': assert not checks['loadedSkill'] and not checks['calledTool']
            report['executionPassed'] = all(execution_complete(item) for item in report['validationSuites'].values())
            report['passed'] = False  # Skill enablement/chat remain blocked on actual review.
        finally:
            wb.close()
            path = ROOT / 'data/extension-chat-live-report.json'
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return 2 if report.get('executionPassed') and report.get('numericFactsPassed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
