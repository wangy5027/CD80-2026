"""Read-only actual local data checks in a disposable capability account."""
import json
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env',override=False)
from backend.app import create_app


def main():
    results=[]
    with tempfile.TemporaryDirectory(prefix='financial-edges-') as directory:
        app=create_app({'TESTING':True,'DATA_DIR':directory,'CAPABILITY_SKIP_PUBLIC_SEED':True,'AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        lib=app.extensions['capability_library'];wb=app.extensions['workbench_service']
        try:
            owner=app.test_client().post('/desktop/api/register',json={'email':'edges@example.invalid','password':'temporary-test-only'}).json['user']['id']
            imported={}
            def call(kind,arguments):
                if kind not in imported:
                    path=ROOT/'capabilities/local-financial/packages'/('cd80-'+kind+'-tool.zip')
                    imported[kind]=lib.import_package(owner,path.read_bytes(),path.name)
                ref=imported[kind];version=lib.version(owner,ref['assetId'],ref['versionId'])
                return lib.invoke(owner,{'assetId':ref['assetId'],'versionId':ref['versionId'],'configurationId':version['configurationId'],'input':arguments,'fileIds':[]},trial=True)['result']['content']
            for asset,code,date in [('stock','600000.SH','2025-07-02'),('etf','510300.SH','2025-07-02'),('index','000001.SH','2025-05-07')]:
                value=call('history',{'asset':asset,'code':code,'date':date,'limit':3})
                assert value['records'] and all(r['code']==code and r['date']==date for r in value['records']),value
                results.append({'case':asset+' exact date','result':value})
            value=call('history',{'code':'600000.SH','date':'2099-01-01'})
            assert not value['records'] and value['warning']
            results.append({'case':'future date no fallback','result':value})
            value=call('history',{'code':'600000.SH'})
            assert value['records'] and all(r['date']==value['as_of'] for r in value['records'])
            results.append({'case':'default dataset latest','result':value})
            a=call('announcement',{'code':'600000.SH','start_date':'2025-07-01','end_date':'2025-07-04','limit':1})
            b=call('announcement',{'code':'600000.SH','start_date':'2025-07-01','end_date':'2025-07-04','limit':1,'offset':1})
            assert a['records'] and b['records'] and a['records'][0]['url']!=b['records'][0]['url'] and a['pagination']['has_more']
            results.append({'case':'announcement pagination','pages':[a,b]})
            value=call('margin',{'code':'000000','date':'2025-07-02'})
            assert not value['records']
            results.append({'case':'missing security no fabricated zero','result':value})
        finally:wb.close()
    (ROOT/'data/local-financial-edges.json').write_text(json.dumps({'passed':True,'checks':results},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Real local data edge checks passed: '+str(len(results)))


if __name__=='__main__':main()
