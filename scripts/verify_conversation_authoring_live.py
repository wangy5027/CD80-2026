"""Opt-in real model smoke test with synthetic inputs and disposable account data."""
import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SKILL_METHOD_ANSWER = (
    '实现方法：将本 Skill 做成离线整数汇总步骤，接收 JSON 对象中的 numbers；'
    '它是至少两个整数组成的数组。用隔离 Python 顺序遍历该数组，'
    '把每个整数（含负数与0）累加为 total，并以数组长度为 count；'
    '返回恰好包含 total 和 count 两个整数的 JSON 对象，不舍入，'
    '不读取文件、不访问网络或外部业务系统。'
    '例如 [3,7,10] 应得到 {"total":20,"count":3}，'
    '[-5,0,12,-3,8] 应得到 {"total":12,"count":5}。'
    '这是对原业务方法的澄清，仍按平台原流程做两组不同输入的隔离验证，待我审阅后保存。'
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', required=True)
    parser.add_argument('--kind', choices=['app', 'tool', 'skill', 'all'], default='all')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from backend.app import create_app
    configured = dotenv_values(ROOT / '.env')
    config = {key: os.environ.get(key) or value for key, value in configured.items()
              if key.startswith('GITEE_AI_') and value}
    config['GITEE_AI_API_KEY'] = os.environ.get('GITEE_AI_API_KEY') or config.get('GITEE_AI_API_KEY', '')
    if not config['GITEE_AI_API_KEY']:
        raise SystemExit('Real model key unavailable; no model request sent.')
    output_dir = ROOT / 'data/conversation-authoring-live'
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {'realModel': True, 'realWasi': True, 'syntheticInputs': True,
              'productionDataRead': False, 'productionWrites': False, 'results': []}
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    output = output_dir / (stamp + '.json')
    with tempfile.TemporaryDirectory(prefix='authoring-live-') as directory:
        config.update(TESTING=True, DATA_DIR=directory, IMPORT_LEGACY_HISTORY=False,
                      CAPABILITY_SKIP_PUBLIC_SEED=True, CD80_ADMIN_API_KEY='', CD80_READ_API_KEY='',
                      SFC_TOKEN='', SFC_KEY_FILE='', CAPABILITY_MCP_POLICY_FILE='')
        app = create_app(config)
        w = app.extensions['workbench_service']
        user, _ = w.auth.register('authoring-live@example.invalid', 'isolated-fixture-password', '隔离验证')
        owner = user['id']
        try:
            for kind in ['app', 'tool', 'skill'] if args.kind == 'all' else [args.kind]:
                instruction = ('创建一个' + kind + '，名称为整数汇总验收。输入为JSON对象，只有一个字段numbers，'
                    '是至少两个整数组成的数组。输出为JSON对象，恰好包含total和count两个整数，'
                    '分别是所有输入整数之和与元素个数。负数和0正常参与计算。不做舍入。'
                    '不访问网络或外部业务系统，不需要用户文件。使用隔离Python计算并验证两组不同输入，'
                    '包含一组负数和0的情况。最后让我查看结果后保存。')
                detail = w.authoring.create(owner, {'kind': kind, 'instruction': instruction}, 'live-' + kind)
                session_id, prior, deadline = detail['session']['id'], None, time.monotonic() + 660
                clarifications = []
                while True:
                    while detail['session']['phase'] in {'preparing', 'checking', 'validating', 'repairing'}:
                        phase = detail['session']['phase']
                        if phase != prior:
                            print(kind + ': ' + phase, flush=True)
                            prior = phase
                        if time.monotonic() > deadline:
                            raise RuntimeError('Authoring worker exceeded bounded test deadline')
                        time.sleep(.5)
                        detail = w.authoring.detail(owner, session_id)
                    question = detail['session'].get('question')
                    if (kind == 'skill' and not clarifications and detail['session']['phase'] == 'awaiting_input'
                            and isinstance(question, dict) and question.get('prompt') == '缺少可创作的实现或方法。'):
                        clarifications.append({'question': question['prompt'], 'answer': SKILL_METHOD_ANSWER,
                                               'revisionBefore': detail['session']['revision']})
                        print(kind + ': answering ' + json.dumps(clarifications[-1], ensure_ascii=False), flush=True)
                        detail = w.authoring.command(owner, session_id, 'answers', {
                            'revision': detail['session']['revision'], 'questionId': question['id'],
                            'answer': SKILL_METHOD_ANSWER}, 'live-skill-method-answer')
                        clarifications[-1]['revisionAfter'] = detail['session']['revision']
                        continue
                    break
                result = {'kind': kind, 'phase': detail['session']['phase'], 'budget': detail['budget']['consumed'],
                          'question': detail['session'].get('question'), 'error': detail['session'].get('error'),
                          'candidate': detail.get('candidate'), 'testPlan': detail.get('testPlan'),
                          'clarifications': clarifications,
                          'evidence': [{k: v for k, v in e.items() if k in {'status', 'caseResults', 'issues', 'scope', 'invalidPlan'}} for e in detail['evidence']]}
                if result['phase'] == 'awaiting_review':
                    candidate, review = detail['candidate'], detail['reviewSnapshot']
                    saved = w.authoring.confirm(owner, session_id, {'revision': detail['session']['revision'],
                        'mode': 'save_and_enable', 'candidateId': candidate['id'], 'contentDigest': candidate['contentDigest'],
                        'reviewSnapshotId': review['id'], 'reviewDigest': review['reviewDigest']}, 'confirm-' + kind)
                    result.update(phase=saved['session']['phase'], commit=saved['commit']['stage'])
                report['results'].append(result)
                report['passed'] = all(item['phase'] == 'completed' for item in report['results'])
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(kind + ': ' + result['phase'] + ' ' + json.dumps(result['budget']), flush=True)
        finally:
            w.close()
    print(str(output), flush=True)
    if not report.get('passed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
