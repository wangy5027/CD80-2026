"""Independent numeric fixtures for the migrated package business logic."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'capabilities/local-financial/src'


def module(name):
    spec=importlib.util.spec_from_file_location(name,SOURCE/(name+'.py'))
    value=importlib.util.module_from_spec(spec)
    sys.modules[name]=value
    spec.loader.exec_module(value)
    return value


common=module('migration_common')
history=module('history')
queries=module('local_queries')
compute=module('financial_compute')


class MigrationTests(unittest.TestCase):
    def test_csv_formula_headers_and_whitespace(self):
        csv_export=module('csv_export')
        for value in ('=SUM(A1)',' @x','\n=1','-1','\tplain'):
            self.assertTrue(csv_export.cell(value).startswith("'"))
        self.assertEqual(csv_export.cell('000001'),'000001')
        self.assertEqual(csv_export.cell(None),'')
    def test_comparison_refuses_versions_and_missing_coverage(self):
        args={'operation':'financial_compare','base_period':'2024-12-31','target_period':'2025-12-31','field':'eps'}
        value={'source':'cd80_stock_fin_indicator','coverage':{'retrieval_complete':True},'records':[
            {'code':'600000.SH','report_period':'2024-12-31','source_fields':{'eps':'0.10'}},
            {'code':'600000.SH','report_period':'2025-12-31','source_fields':{'eps':'0.13'}}]}
        row=compute.calculate(args,value)['records'][0]
        self.assertEqual((row['difference'],row['change_percent']),('0.03','30.0'))
        value['records'].append(dict(value['records'][0]))
        with self.assertRaisesRegex(ValueError,'DATA_AMBIGUOUS'):compute.calculate(args,value)
        value['coverage']['retrieval_complete']=False
        with self.assertRaisesRegex(ValueError,'DATA_INCOMPLETE'):compute.calculate(args,value)

    def test_lhb_reason_direction_dedup_and_nulls(self):
        row={'ID':'1','SYMBOL':'000001','EXCHANGE':'SZ','date':'2025-01-02','CHGTYPE':'A','TRANSDIR':'B','ISVALID':'1','BUYAMT':'0.10','SALEAMT':None}
        value={'source':'cd80_lhb_summary','coverage':{'retrieval_complete':True},'records':[row,dict(row)]}
        output=compute.calculate({'operation':'lhb_summary'},value)['records']
        self.assertEqual(len(output),1)
        self.assertEqual(output[0]['source_rows'],1)
        self.assertEqual(output[0]['BUYAMT'],'0.10')
        self.assertIsNone(output[0]['SALEAMT'])
        value['records'].append(dict(row,BUYAMT='0.20'))
        with self.assertRaisesRegex(ValueError,'DATA_AMBIGUOUS'):compute.calculate({'operation':'lhb_summary'},value)

    def test_display_range_uses_selected_page(self):
        value=common.result('fixture',{'limit':1},[{'date':'2025-01-01'},{'date':'2025-02-01'}],['2025-01-01','2025-02-01'])
        self.assertEqual(value['returned_range'],{'start':'2025-01-01','end':'2025-01-01'})

    def test_task_budget_context_restores_on_failure(self):
        from backend.app.modules.capability_library.read_budget import task_reads,charge,current_scope
        calls=[]
        with self.assertRaisesRegex(ValueError,'stop'):
            with task_reads(lambda reads,rows:calls.append((reads,rows)),scope='parent'):
                charge(reads=1,rows=17)
                self.assertEqual(current_scope(),'parent')
                raise ValueError('stop')
        charge(reads=1)
        self.assertIsNone(current_scope())
        self.assertEqual(calls,[(1,17)])

    def test_parent_budget_survives_multiple_calls(self):
        from backend.app.modules.capability_library.read_budget import task_reads,charge
        from backend.app.modules.capability_library.contracts import CapabilityError
        from backend.app.modules.workbench.errors import GatewayError
        consumed={'reads':199,'rows':19999}
        def account(reads,rows):
            if consumed['reads']+reads>200 or consumed['rows']+rows>20000:
                raise GatewayError('budget',429,'READ_BUDGET_EXCEEDED')
            consumed['reads']+=reads;consumed['rows']+=rows
        with task_reads(account,scope='same-parent'):
            charge(reads=1,rows=1)
        with task_reads(account,scope='same-parent'):
            with self.assertRaises(CapabilityError) as caught:charge(reads=1)
            self.assertEqual(caught.exception.code,'READ_BUDGET_EXCEEDED')
            with self.assertRaises(CapabilityError):charge(rows=1)
        self.assertEqual(consumed,{'reads':200,'rows':20000})

    def test_week_candle_missing_volume_and_previous(self):
        rows=[{'trade_date':'20260904','open':'8','high':'8','low':'8','close':'8','vol':'10','amount':'80'},
              {'trade_date':'20260907','open':'10','high':'12','low':'9','close':'11','vol':'100','amount':'1000'},
              {'trade_date':'20260911','open':'11','high':'13','low':'10','close':'12','vol':'200','amount':'2400'}]
        candle=history.candles(rows,'week')[-1]
        self.assertEqual([candle[k] for k in ['date','open','high','low','close','vol','amount','pct_chg']],['2026-09-13','10','13','9','12','300','3400','50.0'])
        rows[-1]['vol']=None
        self.assertIsNone(history.candles(rows,'week')[-1]['vol'])

    def test_conflicting_day_is_not_silently_deduplicated(self):
        rows=[{'trade_date':'20260907','close':'1'},{'trade_date':'20260907','close':'2'}]
        with self.assertRaisesRegex(ValueError,'DATA_AMBIGUOUS'):
            history.candles(rows,'month')

    def test_announcement_default_thirty_days(self):
        raw=[{'ticker':'600000.SH','announcement_date':d,'security_name':'原文','announcement_title':'回购','url':'https://example.invalid'} for d in ['2026-09-01','2026-09-02','2026-10-01']]
        payload={'input':{'code':'600000.SH'},'responses':[{'data':[{'announcement_date':'2026-10-01'}]}, {'data':raw}]}
        value=queries.single('announcement',payload)['result']
        self.assertEqual([r['date'] for r in value['records']],['2026-10-01','2026-09-02'])
        self.assertEqual(value['effective_range']['start'],'2026-09-02')

    def test_margin_decimal_missing_and_market(self):
        row={'SYMBOL':'600000','EXCHANGE':'1','TRADEDATE':'20250702','FINBUYAMT':'0.30','FINREPAYAMT':'0.10','FINBALANCE':None,'FINMRGHBAL':'1'}
        payload={'input':{'code':'600000'},'responses':[{'data':[{'TRADEDATE':'20250702'}]}, {'data':[row]}]}
        self.assertEqual(queries.single('margin',payload)['result']['records'][0]['financing_net_buy'],'0.20')
        row['FINREPAYAMT']='NULL'
        self.assertIsNone(queries.single('margin',payload)['result']['records'][0]['financing_net_buy'])

    def test_finance_excludes_future_disclosure(self):
        payload={'input':{'code':'600000.SH','as_of':'2026-07-15'},'responses':[{'data':[{'ts_code':'600000.SH','ann_date':'20260801','end_date':'20260630','eps':1}]}]}
        self.assertEqual(queries.finance(payload)['result']['records'],[])

    def test_macro_conflict_not_guessed(self):
        payload={'input':{'indicator':'cpi'},'responses':[{'data':[{'month':'201501','nt_val':1},{'month':'201501','nt_val':2}]}]}
        with self.assertRaisesRegex(ValueError,'DATA_AMBIGUOUS'):
            queries.macro(payload)

    def test_plan_continues_cursor(self):
        plans=[common.scan('cd80_daily',['ts_code'])]
        request,data=common.collect(plans,[{'data':[{'ts_code':'1'}],'pagination':{'next_cursor':'opaque'}}])
        self.assertEqual(request['request']['arguments']['cursor'],'opaque')
        request,data=common.collect(plans,[{'data':[1],'pagination':{'next_cursor':'opaque'}},{'data':[2],'pagination':{'next_cursor':None}}])
        self.assertIsNone(request)
        self.assertEqual(data,[[1,2]])

    def test_all_candidate_packages_pass_schema(self):
        from backend.app.modules.capability_library.packages import inspect,parse_package
        paths=list((ROOT/'capabilities/local-financial/migration-candidates').glob('*.zip'))
        self.assertEqual(len(paths),26)
        for path in paths:
            manifest,files=parse_package(path.read_bytes(),path.name)
            self.assertEqual(inspect(manifest,files),[],path.name)

    def test_result_cursor_preserves_rows_and_scope(self):
        from backend.app.modules.capability_library.result_pages import ResultPages
        from backend.app.modules.capability_library.contracts import CapabilityError
        from backend.app.modules.capability_library.read_budget import task_reads
        pages=ResultPages();version={'contentDigest':'package','configurationId':'config'}
        args={'limit':1,'code':'600000.SH'}
        raw=common.result('fixture',args,[{'x':1},{'x':2}],['2025-01-01','2025-01-02'])
        with task_reads(lambda *_:None,scope='task-a'):
            first=pages.page('alice',version,args,raw,[])
            cursor=first['pagination']['next_cursor']
            raw['_page_records'][1]['x']=999
            second=pages.page('alice',version,{**args,'cursor':cursor},trace=[])
            self.assertEqual(second['records'],[{'x':2}])
            self.assertTrue(second['coverage']['retrieval_complete'])
            self.assertEqual(second,pages.page('alice',version,{**args,'cursor':cursor},trace=[]))
            for bad_owner,bad_args in [('bob',args),('alice',dict(args,code='000001.SZ'))]:
                with self.assertRaises(CapabilityError):pages.page(bad_owner,version,{**bad_args,'cursor':cursor})
        with task_reads(lambda *_:None,scope='task-b'):
            with self.assertRaises(CapabilityError):pages.page('alice',version,{**args,'cursor':cursor})


if __name__=='__main__':unittest.main()
