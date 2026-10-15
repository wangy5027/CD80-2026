"""Explicit real-model original-case save/verify acceptance in isolated storage."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-live', action='store_true')
    args = parser.parse_args()
    if not args.run_live:
        parser.error('real model usage requires --run-live')
    from dotenv import dotenv_values
    from backend.app import create_app
    settings = dotenv_values(ROOT / '.env')
    parent = ROOT / 'data' / 'integration-acceptance'
    parent.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix='saved-original-live-', dir=parent))
    app = create_app({'TESTING': True, 'DATA_DIR': str(directory / 'data'), 'IMPORT_LEGACY_HISTORY': False,
        'CAPABILITY_SKIP_PUBLIC_SEED': True, 'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime'),
        'GITEE_AI_API_KEY': settings.get('GITEE_AI_API_KEY'),
        'GITEE_AI_BASE_URL': settings.get('GITEE_AI_BASE_URL') or 'https://ai.gitee.com/v1',
        'GITEE_AI_MODEL': 'GLM-5.3-Flash', 'GITEE_AI_READ_TIMEOUT_SECONDS': 180,
        'AGENT_MODEL_MAX_TOKENS': 12288, 'SFC_TOKEN': '', 'SFC_KEY_FILE': ''})
    work = app.extensions['workbench_service']
    client = app.test_client()
    report = {'status': 'running', 'model': 'GLM-5.3-Flash', 'isolatedData': str(directory)}
    def save_report():
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    def request(method, path, body=None):
        response = getattr(client, method)('/desktop/api' + path, json=body,
            headers={'Idempotency-Key': 'original-' + str(time.time_ns())})
        assert response.status_code < 300, (response.status_code, response.json)
        return response.json
    try:
        request('post', '/register', {'email': 'saved-original@example.invalid', 'password': 'fixture-password'})
        prompt = ('创建APP“工作工时汇总”。输入hours为1到20个非负整数的数组，每项不超过24。'
                  '一个固定Python代码节点计算数组总和totalHours和元素数量entryCount，输出summary对象。'
                  '无需文件、模型解释、外部服务或第三方包。保存完整实现，不运行，不启用。'
                  '请同时保存原任务测试用例：hours=[2,3,4]；独立精确预期为'
                  '{"summary":{"totalHours":9,"entryCount":3}}。仅这一个原例为必测，不自动添加第二例。')
        with patch.object(work.asset_apps, '_new_run', side_effect=AssertionError('save must not execute')):
            started = request('post', '/authoring-sessions', {'kind': 'app', 'intent': 'create', 'instruction': prompt})
            identity = started['session']['id']
            deadline = time.monotonic() + 360
            while time.monotonic() < deadline:
                status = request('get', '/authoring-sessions/' + identity)
                if status['session']['phase'] not in {'preparing', 'checking', 'validating', 'repairing'}:
                    break
                time.sleep(.25)
            assert status['session']['phase'] == 'completed', status['session']
        result = status['session']['result']
        saved = request('get', '/asset-apps/' + result['assetId'])
        assert saved['verification']['status'] == 'unverified' and not saved['enabledVersionId']
        case = saved['testCases'][0]
        assert case['input'] == {'hours': [2, 3, 4]}, case
        assert case['expectation'] == {'mode': 'json_equals', 'value': {'summary': {'totalHours': 9, 'entryCount': 3}}}, case
        assert not request('get', '/asset-apps/' + saved['id'] + '/runs')['items']
        checked = request('post', '/asset-apps/' + saved['id'] + '/verify',
                          {'versionId': saved['versionId'], 'cases': [case]})
        assert len(checked['verification']['runIds']) == 1
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            checked = request('get', '/asset-apps/' + saved['id'])
            if checked['verification']['status'] != 'running':
                break
            time.sleep(.1)
        assert checked['verification']['status'] == 'passed', checked['verification']
        enabled = request('post', '/asset-apps/' + saved['id'] + '/enable',
            {'versionId': saved['versionId'], 'revision': checked['revision'], 'enabled': True})
        assert enabled['enabledVersionId'] == saved['versionId']
        report.update(status='passed', appId=saved['id'], versionId=saved['versionId'],
                      savedCase=case, creationRuns=0, verificationRuns=1, enabled=True)
    except Exception as error:
        report.update(status='failed', error=str(error))
        raise
    finally:
        save_report()
        work.close()
        print(json.dumps({'report': str(directory / 'report.json'), 'status': report['status']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
