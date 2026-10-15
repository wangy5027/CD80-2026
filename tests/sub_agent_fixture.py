"""Deterministic model fixture; actual capability admission and WASI execution."""
import json
import time
from unittest.mock import patch
from backend.app.modules.capability_library.packages import zip_bytes, file_index
from backend.app.modules.capability_library.asset_validation import verify_suite

CSV = b'symbol,date,close\n000001.SZ,2022-01-04,10.00\n000001.SZ,2022-01-05,12.00\n000001.SZ,2022-01-06,9.00\n000001.SZ,2022-01-07,11.00\n000001.SZ,2022-01-10,12.00\n'
MESSAGE = '分析平安银行000001.SZ的5条2022年合成样例，优先使用读取工具和分析Skill，缺能力时申请临时代码。计算首末价格变化率和最大回撤，交付PNG、CSV、Python及说明；不联网、不安装、不修改源数据、不保存新能力。'
FOLLOWUP = '上一项完成后，只告诉我刚才CSV有多少条数据，不要重新取数或生成文件。'
READER = '''from pathlib import Path
def run(payload):
    lines = Path(__file__).with_name('sample.csv').read_bytes().splitlines(keepends=True)
    data = b''.join(lines[:payload.get('maxRows', 5) + 1])
    open('/output/sample.csv','wb').write(data)
    return {'rows':len(data.splitlines())-1,'source':'synthetic-fixture','synthetic':True}
'''
CODE = '''import csv, io, json
from cd80_plot import line_chart
from decimal import Decimal
def run(payload):
    raw = open(next(iter(payload['files'].values())), 'rb').read()
    rows = sorted(csv.DictReader(io.StringIO(raw.decode())), key=lambda r:r['date'])
    values = [Decimal(r['close']) for r in rows]
    peak=values[0]; drops=[]
    for value in values:
        peak=max(peak,value); drops.append((peak-value)/peak)
    result={'rows':len(rows),'change':str(values[-1]/values[0]-1),'maxDrawdown':str(max(drops)),
            'prices':[str(v) for v in values],'dates':[r['date'] for r in rows],'synthetic':True}
    png=line_chart(result['dates'],values,title='SYNTHETIC SAMPLE',unit='CNY')
    open('/output/chart.png','wb').write(png)
    open('/output/used.csv','wb').write(raw)
    return result
'''


def wait_job(lib, owner, job):
    until = time.monotonic() + 30
    while job['status'] in {'queued', 'running', 'cancelling'} and time.monotonic() < until:
        time.sleep(.02)
        job = lib.store.get(owner, 'job', job['jobId'])
    if job['status'] != 'completed':
        raise AssertionError(job)
    return job['result']


def seed(lib, owner):
    refs = []
    for kind in ['tool', 'skill']:
        files = {'original/tool.py': READER.encode(), 'original/sample.csv': CSV} if kind == 'tool' else {
            'original/SKILL.md': b'---\nname: sample-analysis-guide\ndescription: Analyze synthetic price samples.\n---\nUse the sample reader first. Calculate change and drawdown. Label synthetic data. Request temporary code when necessary.\n'}
        manifest = {'format':'cd80.capability/1', 'kind':kind, 'name':'sample-reader' if kind=='tool' else 'sample-guide',
            'description':'Synthetic financial sample '+kind, 'source':{'identity':'fixture/'+kind,'format':'platform'},
            'dependencies':[], 'permissions':{'network':'none','externalEffects':'none','inputFiles':False,'outputFiles':kind=='tool'}, 'files':file_index(files)}
        if kind == 'tool':
            manifest['tool']={'inputSchema':{'type':'object','properties':{'maxRows':{'type':'integer','minimum':1,'maximum':5}},'additionalProperties':False},
                'execution':{'type':'python_wasi','path':'original/tool.py','callable':'run'}}
        else:
            manifest['skill']={'entry':'original/SKILL.md'}
        imported = lib.import_package(owner, zip_bytes({**files,'capability.json':json.dumps(manifest).encode()}), kind+'.zip')
        version = lib.version(owner,imported['assetId'],imported['versionId'])
        body = {**{k:version[k] for k in ('assetId','versionId','configurationId')},'input':{},'fileIds':[]}
        if kind == 'tool':
            cases = [{'input': args, 'fileIds': [], 'expectation': {'mode': 'json_equals', 'value':
                {'rows': count, 'source': 'synthetic-fixture', 'synthetic': True}}} for args, count in [({}, 5), ({'maxRows': 2}, 2)]]
            checked=wait_job(lib,owner,verify_suite(lib,owner,{**body,'cases':cases}))
            assert checked['status']=='passed',checked
        else:
            def events(messages,*args,**kwargs):
                rows = json.loads(messages[-1]['content'].split('本次明确输入：')[-1])['sampleRows']
                yield {'type':'token','text':f'Use sample reader for {rows} rows; calculate price change and drawdown; label synthetic.'}
                yield {'type':'turn_end','tool_calls':[]}
            cases = [{'input': {'sampleRows': rows}, 'fileIds': [], 'expectation': {'mode': 'manual_review',
                'prompt': f'Explain the method for {rows} sample rows.', 'criteria': f'Reader, {rows} rows, change, drawdown and synthetic label.'}} for rows in (5, 2)]
            with patch.object(lib.workbench.runtime.provider,'events',events):
                checked=wait_job(lib,owner,verify_suite(lib,owner,{**body,'cases':cases}))
            assert '5 rows' in checked['cases'][0]['result']['content'], checked
            assert '2 rows' in checked['cases'][1]['result']['content'], checked
            lib.review(owner,checked['verificationId'],{'decision':'accept','comment':'Fixture output includes all four required method points.'})
        asset=lib.asset(owner,imported['assetId'])
        lib.update_asset(owner,asset['assetId'],{'enabled':True,'activeVersionId':version['versionId']},f'"{asset["revision"]}"')
        refs.append({k:version[k] for k in ('assetId','versionId')})
    return refs


class FixtureModel:
    def __init__(self, refs):
        self.refs = refs
        self.calls=[]
        self.always_fail=False

    def call(self, stage, payload, stopped, on_response):
        self.calls.append((stage,payload.get('message')))
        if stage=='plan':
            if payload['message']==FOLLOWUP:
                data=next(a for a in payload['priorArtifacts'] if a['name']=='analysis-result.json')
                assert json.loads(data['preview'])['rows']==5
                return {'mode':'answer','answer':'刚才用于计算的CSV有5条数据。'}
            return {'mode':'delegate','goal':payload['message'],'deliverables':['PNG','CSV','Python','analysis'],
                'acceptanceCriteria':['20% change','25% drawdown','synthetic label','actual code download'],
                'constraints':['no network','no installation','no source mutations'], 'preferredCapabilities':self.refs,
                'requiredCapabilities':[],'allowTemporaryCode':True,'requiredExtensions':['.png','.csv','.py']}
        if stage=='approve':
            assert any(s['type']=='tool' and s['status']=='succeeded' for s in payload['steps'])
            return {'decision':'approve','reason':'Specified reader and guide used; only read sample and create local artifacts.'}
        if stage=='act':
            if not any(s['type']=='tool' and s['status']=='succeeded' for s in payload['steps']):
                return {'type':'tool','name':'cap_'+self.refs[0]['versionId'],'arguments':{}}
            done=any(s['type']=='code' and s['status']=='succeeded' for s in payload['steps'])
            if done:
                assets=payload['artifacts']
                ids=[a['id'] for a in assets if a['name'] in {'chart.png','used.csv','analysis.py'} and (payload['correctionsUsed']==3 or a['name']!='analysis.py')]
                return {'type':'finish','artifactIds':ids,'answer':'Synthetic sample analysis.'}
            data=next(a for a in payload['artifacts'] if a['name']=='sample.csv')
            code='def run(payload)\n    return {}' if payload['correctionsUsed']==0 or self.always_fail else CODE.replace("r['close']","r['close_price']") if payload['correctionsUsed']==1 else CODE
            return {'type':'code','reason':'No executable analysis/plot capability; isolated standard-library code only.', 'inputRefs':[data['id']],'code':code}
        if stage=='review':
            if payload['platformChecks']['missingExtensions']:
                return {'decision':'revise','reason':'缺少要求交付的Python代码下载。'}
            result=next(s['result']['content'] for s in payload['steps'] if s['type']=='code' and s['status']=='succeeded')
            assert result['rows']==5 and result['change']=='0.2' and result['maxDrawdown']=='0.25'
            return {'decision':'accept','answer':'5条合成样例，2022-01-04至2022-01-10。价格变化率20.00%，最大回撤25.00%。不是全年真实行情或投资收益。横轴为日期，纵轴为价格（元）。'}
        raise AssertionError(stage)
