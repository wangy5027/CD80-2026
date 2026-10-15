"""Explicit bounded real-model acceptance in a disposable App workspace.

Synthetic inputs only. No production accounts, records or network data Tools.
Run with --live; evidence is separate from the Ping An Bank business scenario.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--creator-report', help='Reuse an already passed Creator case; do not repeat that model call.')
    args = parser.parse_args()
    from dotenv import dotenv_values
    from backend.app import create_app
    from backend.app.modules.capability_library.components import snapshot_from_package
    from backend.app.modules.capability_library.contracts import digest

    configured = dotenv_values(ROOT / '.env')
    config = {key: os.environ.get(key) or configured.get(key) for key in
              ('GITEE_AI_API_KEY', 'GITEE_AI_BASE_URL', 'GITEE_AI_MODEL')}
    if not config['GITEE_AI_API_KEY']:
        parser.error('No model credential configured; no calls made.')
    report = {'startedAt': datetime.now(timezone.utc).isoformat(), 'realModel': True,
              'realWasi': True, 'syntheticData': True, 'productionWrites': False,
              'productionUserDataRead': False, 'model': config['GITEE_AI_MODEL'], 'passed': False,
              'budget': {'creatorRequests': 0 if args.creator_report else 1, 'fixedModelToolCases': 2,
                         'skillCases': 2, 'secondsPerRun': 240}, 'cases': []}
    destination = ROOT / 'data/asset-authoring-live'
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / ('models-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.json')

    def save():
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    with tempfile.TemporaryDirectory(prefix='cd80-asset-model-') as folder:
        app = create_app({**config, 'TESTING': True, 'DATA_DIR': folder, 'IMPORT_LEGACY_HISTORY': False,
            'CAPABILITY_SKIP_PUBLIC_SEED': True, 'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime'),
            'AGENT_MODEL_MAX_TOKENS': 6000, 'AGENT_MODEL_REASONING_EFFORT': 'low',
            'GITEE_AI_READ_TIMEOUT_SECONDS': 90, 'SUB_AGENT_TIMEOUT': 180})
        workbench = app.extensions['workbench_service']
        service = app.extensions['asset_apps']
        try:
            client = app.test_client()
            owner = client.post('/desktop/api/register', json={'email': 'asset-live@example.invalid',
                'password': 'isolated-synthetic-password'}).json['user']['id']
            if args.creator_report:
                prior_raw = Path(args.creator_report).read_bytes()
                prior = json.loads(prior_raw)
                if not any(c['name'] == 'creator-fixed-code' and c['verification']['status'] == 'passed' for c in prior['cases']):
                    raise RuntimeError('The referenced Creator case did not pass.')
                created = prior['creator']
                report['creatorReuseSource'] = {'name': Path(args.creator_report).name, 'sha256': digest(prior_raw)}
            else:
                created = service.creator(owner, {'instruction':
                    '创建一个仅含固定Python代码的应用，输入n为integer，返回result对象，其value必须是n乘以2。'
                    '不使用任何Tool/Skill，不访问网络、文件，不增加模型步骤。输出绑定result到唯一代码步骤的完整结果。'}, 'live-creator')
            report['creator'] = created
            save()
            draft = created['definition']
            if created['issues'] or len(draft['nodes']) != 1 or draft['nodes'][0]['kind'] != 'code':
                raise RuntimeError('Creator did not deliver the requested single complete fixed-code method: ' +
                                   json.dumps({'issues': created['issues'], 'nodeKinds': [n.get('kind') for n in draft['nodes']]}, ensure_ascii=False))
            if draft['nodes'][0].get('provenance', {}).get('type') != 'generated':
                raise RuntimeError('Generated source incorrectly labelled as historic execution.')
            saved = service.save(owner, {'definition': draft}, 'save-creator')
            verified = service.verify(owner, saved['id'], {'versionId': saved['versionId'], 'cases': [
                {'input': {'n': n}, 'expectation': {'mode': 'json_equals', 'value': {'result': {'value': n * 2}}}}
                for n in (3, 7)]}, 'check-creator')

            def wait_runs(current, label):
                records = []
                for run_id in current['verification']['runIds']:
                    end = time.monotonic() + 240
                    while True:
                        run = service.run(owner, run_id)
                        if run['status'] not in {'queued', 'running'} and run_id not in service.active:
                            break
                        if time.monotonic() > end:
                            service.cancel(owner, run_id, {'revision': run['revision']}, 'cancel-' + run_id)
                            raise RuntimeError('Bounded real-model acceptance deadline reached.')
                        time.sleep(.2)
                    records.append(run)
                    print(json.dumps({'case': label, 'status': run['status'], 'steps': len(run['steps'])}), flush=True)
                detail = service.detail(owner, current['id'])
                case = {'name': label, 'verification': detail['verification'], 'runs': records,
                    'definition': detail['definition'], 'definitionDigest': detail['definitionDigest']}
                for run in records:
                    for step in run['steps']:
                        call_id = step.get('skillCallId') or (step.get('error') or {}).get('details', {}).get('skillCallId')
                        if call_id:
                            case.setdefault('skillEvidence', []).append(workbench.sub_agents.store.run(owner, call_id))
                report['cases'].append(case)
                save()
                if detail['verification']['status'] != 'passed':
                    raise RuntimeError(label + ' failed; actual evidence preserved.')
                return detail, records

            _, records = wait_runs(verified, 'creator-fixed-code')
            if any(s['kind'] != 'code' for r in records for s in r['steps']):
                raise RuntimeError('Creator unexpectedly expanded the fixed execution method.')

            value_schema = {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                            'required': ['value'], 'additionalProperties': False}
            tool_manifest = {'format': 'cd80.capability/1', 'kind': 'tool', 'name': 'Fixed model addition',
                'description': 'Double the integer in code, then use a fixed model step to add one.',
                'source': {'identity': 'test/live-fixed-model', 'format': 'platform'}, 'files': [], 'dependencies': [],
                'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
                'tool': {'inputSchema': {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n']},
                    'outputSchema': value_schema, 'execution': {'type': 'fixed_pipeline', 'protocolVersion': '1', 'steps': [
                        {'id': 'double', 'kind': 'python_wasi', 'path': 'original/double.py', 'callable': 'run', 'outputSchema': value_schema},
                        {'id': 'add_one', 'kind': 'model', 'inputFrom': 'steps.double',
                         'prompt': 'Return exactly one JSON object with integer value equal to the supplied value plus 1. No other fields.',
                         'outputSchema': value_schema}]}}}
            tool_snapshot = snapshot_from_package(tool_manifest,
                {'original/double.py': b'def run(payload):\n    return {"value": payload["n"] * 2}\n'})
            tool_app = {'format': 'cd80.app/2', 'name': '真实固定模型Tool验证',
                'inputSchema': tool_manifest['tool']['inputSchema'], 'rules': ['固定代码乘二，然后固定模型步骤加一'],
                'nodes': [{'id': 'fixed', 'kind': 'tool', 'title': '固定多步骤Tool', 'component': tool_snapshot,
                    'inputBindings': {'n': {'from': 'input', 'path': 'n'}}, 'delivery': {'schema': value_schema}}],
                'outputs': {'result': {'from': 'step', 'stepId': 'fixed', 'path': ''}}, 'gaps': []}
            saved = service.save(owner, {'definition': tool_app}, 'save-fixed-model')
            verified = service.verify(owner, saved['id'], {'versionId': saved['versionId'], 'cases': [
                {'input': {'n': n}, 'expectation': {'mode': 'json_equals', 'value': {'result': {'value': n * 2 + 1}}}}
                for n in (2, 8)]}, 'check-fixed-model')
            wait_runs(verified, 'fixed-model-tool')

            manifest = {'format': 'cd80.capability/1', 'kind': 'skill', 'name': 'Current integer doubling',
                'description': 'Use the declared script on the actual supplied integer.',
                'source': {'identity': 'test/live-double-skill', 'format': 'platform'}, 'files': [], 'dependencies': [],
                'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False},
                'skill': {'entry': 'original/SKILL.md', 'scripts': [{'path': 'original/scripts/double.py', 'type': 'python_wasi', 'callable': 'run'}]}}
            files = {'original/SKILL.md': b'---\nname: current-integer-double\ndescription: Double the current integer using the declared script.\n---\nCall skill_script at original/scripts/double.py with input {"n": the supplied n}. Do not calculate in prose. Finish by selecting the actual script result step; deliver its object unchanged.\n',
                     'original/scripts/double.py': b'def run(payload):\n    return {"value": payload["n"] * 2}\n'}
            snapshot = snapshot_from_package(manifest, files)
            definition = {'format': 'cd80.app/2', 'name': '真实Skill调用与固定计算衔接',
                'description': '合成整数，仅验收模型和方法边界',
                'inputSchema': {'type': 'object', 'properties': {'n': {'type': 'integer'}}, 'required': ['n'], 'additionalProperties': False},
                'rules': ['Use the current input and the declared script; keep the saved App method unchanged.'],
                'nodes': [{'id': 'skill', 'title': '调用通用方法', 'kind': 'skill', 'component': snapshot,
                    'inputBindings': {'n': {'from': 'input', 'path': 'n'}},
                    'task': 'Call the declared script with the provided n and deliver its exact JSON object {value: number}. Select the real successful script result step when finishing.',
                    'delivery': {'schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value'], 'additionalProperties': False}},
                    'checkpoint': False},
                    {'id': 'merge', 'title': '固定后续计算', 'kind': 'code',
                     'code': 'def run(payload):\n    return {"answer": payload["value"] + 1}\n',
                     'inputBindings': {'value': {'from': 'step', 'stepId': 'skill', 'path': 'value'}},
                     'delivery': {'schema': {'type': 'object', 'required': ['answer']}}, 'checkpoint': False}],
                'outputs': {'result': {'from': 'step', 'stepId': 'merge', 'path': ''}}, 'gaps': []}
            saved = service.save(owner, {'definition': definition}, 'save-skill')
            verified = service.verify(owner, saved['id'], {'versionId': saved['versionId'], 'cases': [
                {'input': {'n': n}, 'expectation': {'mode': 'json_equals', 'value': {'result': {'answer': n * 2 + 1}}}}
                for n in (4, 9)]}, 'check-skill')
            _, runs = wait_runs(verified, 'skill-middle-node')
            for run in runs:
                skill_call = workbench.sub_agents.store.run(owner, run['steps'][0]['skillCallId'])
                calls = [s for s in skill_call['steps'] if s.get('name') == 'skill_script' and s['status'] == 'succeeded']
                if not calls:
                    raise RuntimeError('Skill claimed output without executing the required declared script.')
            report.update(passed=True, completedAt=datetime.now(timezone.utc).isoformat())
        except Exception as error:
            message = str(error)
            for key, value in config.items():
                if 'KEY' in key and value:
                    message = message.replace(value, '[REDACTED]')
            report.update(error=message[:3000], completedAt=datetime.now(timezone.utc).isoformat())
        finally:
            workbench.close()
            report['sourceSha256'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                ('backend/app/modules/agent_platform/asset_apps.py', 'backend/app/modules/agent_platform/asset_creator.py',
                 'backend/app/modules/sub_agent/skill_runtime.py', 'scripts/verify_asset_model_live.py')}
            save()
    print(json.dumps({'passed': report['passed'], 'report': str(target), 'error': report.get('error')}, ensure_ascii=False), flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
