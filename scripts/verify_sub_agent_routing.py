"""Opt-in real-model routing checks; synthetic metadata, no Tool execution."""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', help='Use the configured model; incurs ordinary API usage.')
    args = parser.parse_args()
    if not args.live:
        parser.error('Pass --live to explicitly run real model requests.')
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env', override=False)
    from backend.app.modules.workbench.graph_runtime import GiteeProvider
    from backend.app.modules.sub_agent.model import Model
    provider = GiteeProvider({'GITEE_AI_API_KEY': os.environ.get('GITEE_AI_API_KEY'),
        'GITEE_AI_BASE_URL': os.environ.get('GITEE_AI_BASE_URL'), 'GITEE_AI_MODEL': os.environ.get('GITEE_AI_MODEL'),
        'GITEE_AI_MAX_TOKENS': 8192, 'GITEE_AI_REASONING_EFFORT': 'low'})
    tools = [{'type': 'function', 'function': {'name': name, 'description': desc,
        'parameters': {'type': 'object', 'properties': {}, 'additionalProperties': True}}}
        for name, desc in [('read_prices', '按证券和日期读取实际行情'), ('read_file', '读取获准的文件内容')]]
    cases = [
        ('short_visualization', '请使用现有金融数据 Tools 和 Skills 分析并可视化平安银行（000001.SZ）2022年的股价走势，计算价格变化率和最大回撤', {'delegate'}, []),
        ('explanation', '请解释什么是最大回撤，不需要查询数据。', {'chat', 'answer'}, []),
        ('single_query', '请查询平安银行2022年1月4日的收盘价。', {'direct'}, []),
        ('nonfinancial', '请读取我提供的销售CSV，检查并处理重复和缺失记录，汇总各地区销售情况并生成图表。', {'delegate'}, []),
        ('application', '请创建一个可重复运行的应用，输入CSV后统计每列缺失值，先给我流程确认。', {'application'}, []),
        ('observed_upgrade', '请整理这份数据并给出可用的汇总结果。', {'delegate'}, [{'summary': '已读取CSV，共120行；有重复键和不一致的日期格式，需要处理后汇总。'}]),
    ]
    def check(case):
        key, message, expected, observations = case
        payload = {'message': message, 'selectedCapabilities': [], 'selectedSkills': [], 'catalog': {'items': [], 'nextOffset': None},
            'builtinTools': tools, 'availableTools': tools, 'history': [], 'priorArtifacts': [], 'answers': [],
            'observations': observations, 'steps': [{'type': 'tool', 'status': 'succeeded', 'name': 'read_file'}] if observations else [],
            'artifacts': [{'id': 'synthetic-input', 'name': 'sales.csv', 'preview': 'region,date,sales'}] if key in {'nonfinancial', 'observed_upgrade'} else []}
        started = time.monotonic()
        try:
            value = Model(provider).call('plan', payload, threading.Event(), lambda response: None)
            ok = value.get('mode') in expected
            if value.get('mode') == 'delegate':
                ok = ok and value.get('assessment', {}).get('dependentProcessing') is True and bool(value.get('goal'))
            return {'case': key, 'expected': sorted(expected), 'passed': ok, 'seconds': round(time.monotonic()-started, 2), 'response': value}
        except Exception as error:
            return {'case': key, 'passed': False, 'error': str(error), 'seconds': round(time.monotonic()-started, 2)}
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in pool.map(check, cases):
            results.append(result)
            print(json.dumps({k: result[k] for k in ('case', 'passed', 'seconds')}, ensure_ascii=False), flush=True)
    out = ROOT / 'data/sub-agent-routing-live.json'
    out.write_text(json.dumps({'model': provider.model, 'realModel': True, 'syntheticMetadata': True, 'toolsExecuted': False, 'cases': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0 if all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
