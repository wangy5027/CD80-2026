"""Additional asset/report/event branches against actual read-only SQLite."""
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from verify_migration_release import ROOT,read_api,create_app,uvicorn


def main():
    db=sqlite3.connect((ROOT/'cd80-data-api/data/cd80.sqlite3').as_uri()+'?mode=ro',uri=True)
    def one(table,field):
        # Identifiers below are maintained constants, never model/user strings.
        return db.execute('SELECT "'+field+'" FROM "'+table+'" WHERE "'+field+'" IS NOT NULL LIMIT 1').fetchone()[0]
    cases=[]
    for asset,table in [('stock','cd80_daily'),('etf','cd80_etf_fund_daily'),('index','cd80_index_dd')]:
        symbol=one(table,'ts_code')
        for period in ('day','week','month'):
            cases.append(('history',{'code':symbol,'asset':asset,'period':period,'limit':2}))
        cases.append(('close-valuation',{'code':symbol,'asset':asset,'date':'1900-01-01'}))
    for kind,suffix in [('indicator','indicator'),('income','income'),('cashflow','cashflow'),('balance','balancesheet')]:
        cases.append(('financial-statement',{'code':one('cd80_stock_fin_'+suffix,'ts_code'),'kind':kind,'limit':2}))
    for category,table,field in [('stock','cd80_stock_basic','ts_code'),('etf','cd80_etf_basic','ts_code'),('fund','cd80_fund_basic','ts_code'),('industry','cd80_ci_index_member','l1_name')]:
        cases.append(('local-search',{'category':category,'keyword':one(table,field),'limit':2}))
    for event,table,field in [('ipo_listing','cd80_ipo','LISTDATE'),('dividend_ex','cd80_stock_fin_dividend','ex_date'),('dividend_payment','cd80_stock_fin_dividend','pay_date'),('bond_redemption','cd80_bond_cb_redeem','call_date'),('conversion_change','cd80_cb_price_chg','change_date')]:
        raw=str(one(table,field)).removesuffix('.0')
        stamp=raw if '-' in raw else raw[:4]+'-'+raw[4:6]+'-'+raw[6:]
        cases.append(('event-calendar',{'event_type':event,'start_date':stamp,'end_date':stamp,'limit':2}))
    cases.append(('macro-series',{'indicator':'lpr','start_date':'2015-01-01','end_date':'2015-01-10'}))
    row=db.execute('SELECT curve_code,maturity_years,trade_date FROM cd80_yc_cb LIMIT 1').fetchone()
    cases.append(('macro-series',{'indicator':'yield','curve_code':row[0],'maturity_years':row[1],'curve_type':0,'start_date':row[2],'end_date':row[2]}))
    db.close()
    server=uvicorn.Server(uvicorn.Config(read_api(),host='127.0.0.1',port=48997,log_level='error'))
    worker=threading.Thread(target=server.run,daemon=True);worker.start()
    while not server.started:time.sleep(.05)
    report=[]
    with tempfile.TemporaryDirectory(prefix='matrix-') as directory:
        app=create_app({'TESTING':True,'DATA_DIR':directory,'CAPABILITY_SKIP_PUBLIC_SEED':True,'IMPORT_LEGACY_HISTORY':False,'CD80_DATA_API_URL':'http://127.0.0.1:48997','AGENT_RUNTIME_DIR':str(ROOT/'data/agent-runtime')})
        lib=app.extensions['capability_library'];refs={}
        owner=app.test_client().post('/desktop/api/register',json={'email':'matrix@example.invalid','password':'temporary-matrix-password'}).json['user']['id']
        try:
            for name,args in cases:
                try:
                    if name not in refs:
                        path=ROOT/'capabilities/local-financial/migration-candidates'/('cd80-'+name+'-tool.zip')
                        ref=lib.import_package(owner,path.read_bytes(),path.name)
                        refs[name]={**ref,'configurationId':lib.version(owner,ref['assetId'],ref['versionId'])['configurationId']}
                    call=lib.invoke(owner,{**refs[name],'input':args,'fileIds':[]},threading.Event(),trial=True)
                    out=call['result']['content']
                    if args.get('date')=='1900-01-01':assert not out['records']
                    else:assert out['records'],'Expected non-empty real sample'
                    if out.get('pagination',{}).get('next_cursor'):
                        second=lib.invoke(owner,{**refs[name],'input':{**args,'cursor':out['pagination']['next_cursor']},'fileIds':[]},threading.Event(),trial=True)['result']['content']
                        assert second['records']
                    result={'tool':name,'input':args,'status':'passed','rows':len(out['records'])}
                except Exception as error:
                    expected = args.get('category')=='industry' and 'DATA_AMBIGUOUS' in str(error)
                    result={'tool':name,'input':args,'status':'expected_source_rejection' if expected else 'failed','error':str(error)}
                report.append(result);print(json.dumps(result,ensure_ascii=False),flush=True)
        finally:
            app.extensions['workbench_service'].close();server.should_exit=True;worker.join(timeout=5)
    (ROOT/'data/migration-matrix.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
