"""Build migration candidates separately; publication requires new evidence."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.modules.capability_library.packages import file_index, inspect, parse_package, zip_bytes
from backend.app.modules.capability_library.contracts import digest


def string(title, choices=None):
    value = {'type': 'string', 'maxLength': 128, 'title': title}
    if choices:
        value['enum'] = choices
    return value


def build():
    root = ROOT / 'capabilities/local-financial'
    target = root / 'migration-candidates'
    target.mkdir(exist_ok=True)
    base = {'code': string('证券代码'), 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'title': '条数'},
            'offset': {'type': 'integer', 'minimum': 0, 'maximum': 1000, 'title': '跳过条数'}}
    dates = {k: string(v) for k, v in [('date', '指定日期'), ('start_date', '开始日期'), ('end_date', '结束日期')]}
    definitions = [
        ('history', '本地历史日周月线', 'history', 'run', {**base, **dates, 'asset': string('资产', ['stock','etf','index']), 'period': string('周期', ['day','week','month'])}, ['code'], {'code':'600000.SH','period':'week','limit':3}, 'cd80_daily', '查询600000.SH最近3根不复权周线，注明日期、单位和完整性。'),
        ('announcement', '本地公告标题检索', 'local_queries', 'single', {**base, **dates, 'keyword': string('标题关键词')}, ['code'], {'code':'600000.SH','start_date':'2025-07-01','end_date':'2025-07-04','limit':3}, 'cd80_anns_d', '查询600000.SH在2025年7月1日至4日的公告标题与链接，不读取正文。证券只显示代码，不补充任何名称。'),
        ('margin', '本地单证券两融', 'local_queries', 'single', {**base, **dates, 'market': string('源市场代码')}, ['code'], {'code':'600000','date':'2025-07-02'}, 'cd80_margin_data', '查询600000在2025-07-02的融资买入、偿还、净买入及余额，金额保持源表原值。'),
        ('security-search', '本地证券搜索', 'local_queries', 'search', {'keyword': string('名称或代码'), 'asset_types': {'type':'array','maxItems':3,'items':string('资产',['stock','etf','fund'])}, 'limit':base['limit'], 'offset':base['offset']}, ['keyword'], {'keyword':'600000','asset_types':['stock']}, 'local-directory', '搜索代码600000对应的证券，返回候选及市场，不猜测。'),
        ('close-valuation', '本地收盘与估值', 'local_queries', 'valuation', {**base, 'date':dates['date'], 'asset':string('资产',['stock','etf','index'])}, ['code'], {'code':'600000.SH','date':'2025-07-02'}, 'cd80_daily', '查询600000.SH在2025-07-02的历史收盘价和估值，分别标注来源日期。'),
        ('financial-statement', '本地财务报表', 'local_queries', 'finance', {**base,'kind':string('报表',['indicator','income','cashflow','balance']), **{k:string(k) for k in ['start_period','end_period','as_of']}, 'fields':{'type':'array','maxItems':30,'items':string('字段')}}, ['code'], {'code':'603391.SH','kind':'indicator'}, 'cd80_stock_fin_indicator', '读取603391.SH已有财务指标，区分报告期和披露日，不混用报告版本。'),
        ('lhb-detail', '本地龙虎榜明细', 'local_queries', 'single', {**base, **dates, 'market':string('源市场代码')}, ['code'], {'code':'000026','date':'2025-07-02'}, 'cd80_lhb_summary', '读取000026在2025-07-02的龙虎榜明细，最多3条，保留营业部、方向和有效性原始代码，不额外统计或推断字段含义。'),
        ('macro-series', '本地有限宏观指标', 'local_queries', 'macro', {'indicator':string('指标',['cpi','lpr','yield']), 'start_date':dates['start_date'],'end_date':dates['end_date'],'curve_code':string('曲线'),'curve_type':{'type':'integer','minimum':0,'maximum':10,'title':'源曲线类型；存在歧义时必填'},'maturity_years':{'type':'number','minimum':0},'limit':base['limit'],'offset':base['offset']}, ['indicator','start_date','end_date'], {'indicator':'cpi','start_date':'2015-01-01','end_date':'2015-01-31'}, 'cd80_cn_cpi', '查询2015年1月本地CPI；同一期有冲突时停止，不猜版本。'),
        ('event-calendar', '本地有限事件日历', 'local_queries', 'events', {**base,'event_type':string('事件',['ipo_listing','dividend_ex','dividend_payment','bond_redemption','conversion_change']),'start_date':dates['start_date'],'end_date':dates['end_date']}, ['event_type','start_date','end_date'], {'event_type':'ipo_listing','start_date':'1994-05-01','end_date':'1994-05-31'}, 'cd80_ipo', '查询1994年5月本地已有IPO上市事件，不将申购代码称股票代码。'),
        ('local-search', '本地分类目录搜索', 'local_queries', 'search', {'category':string('类别',['stock','etf','fund','industry']),'keyword':string('关键词'),'limit':base['limit'],'offset':base['offset']}, ['category','keyword'], {'category':'stock','keyword':'600000'}, 'local-directory', '在本地股票目录检索600000，返回明确类型，不搜索公告事件。')]
    built, dependencies = [], {}
    shared = {f'original/{name}':(root/'src'/name).read_bytes() for name in ['migration_common.py','history.py','local_queries.py']}

    def save(folder, manifest, files):
        manifest['files'] = file_index(files)
        problems = inspect(manifest, files)
        if problems:
            raise RuntimeError(str(problems))
        raw = zip_bytes({**files, 'capability.json': (json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()})
        parse_package(raw, folder+'.zip')
        (target/(folder+'.zip')).write_bytes(raw)
        built.append({'package':folder+'.zip','kind':manifest['kind'],'manifestDigest':digest(manifest)})
        return {'sourceIdentity':manifest['source']['identity'], 'contentDigest':digest({'manifest':manifest,'files':file_index(files)}), 'required':True, 'reason':'使用已验证的本地读取实现'}

    def manifest(folder, kind, title):
        return {'format':'cd80.capability/1','kind':kind,'name':title,'description':title+'；仅使用明确的本地数据范围。',
                'source':{'identity':'cd80/'+folder,'format':'platform','version':'2.0.0'}, 'files':[], 'dependencies':[],
                'permissions':{'network':'connector_only' if kind=='tool' else 'none','externalEffects':'none','inputFiles':False,'outputFiles':False}}

    for key,title,module,function,props,required,sample,source,prompt in definitions:
        props['cursor'] = string('上一页返回的结果游标；其他查询条件保持一致')
        folder = 'cd80-'+key+'-tool'
        doc = manifest(folder,'tool',title+'工具')
        expression = '_execute(payload)' if function!='single' else '_execute('+repr('lhb' if key=='lhb-detail' else key)+', payload)'
        wrapper = ('from '+module+' import '+function+' as _execute\ndef run(payload):\n'
                   '    try:\n        return '+expression+'\n'
                   '    except ValueError as error:\n'
                   '        message = str(error)\n'
                   '        code = message.split(":", 1)[0]\n'
                   '        if code not in ("DATA_AMBIGUOUS", "UNSUPPORTED_OPERATION", "DATA_INCOMPLETE"):\n'
                   '            code = "INVALID_INPUT"\n'
                   '        return {"error": {"code": code, "message": message[:300]}}\n')
        files = {**shared,'original/tool.py':wrapper.encode()}
        doc['tool'] = {'inputSchema':{'type':'object','properties':props,'required':required,'additionalProperties':False},
            'execution':{'type':'python_connector','protocolVersion':'2','path':'original/tool.py','callable':'run','connections':[{'identity':'cd80.local-data','operations':['query_v2','lookup_v2']}]}}
        doc['examples'] = [{'id':'local-query','title':title+'查询','description':'核对实际来源、日期、记录及限制。','requirements':[], 'filePaths':[], 'input':sample,
            'assertions':[{'path':['source'],'op':'eq','value':source},{'path':['result_schema'],'op':'eq','value':'financial-result/1'},{'path':['records'],'op':'type','value':'array'}]}]
        dependencies[key] = save(folder,doc,files)
        folder = 'cd80-'+key
        doc = manifest(folder,'skill',title)
        doc['dependencies'] = [dependencies[key]]
        instructions = ('实际调用精确依赖工具 cd80/'+folder+'-tool。先确认证券/指标身份，歧义时澄清；不根据记忆补数。\n'
          '核对工具返回的requested、effective_range、as_of、security_as_of、单位、分页和覆盖；缺失与零分开。只使用返回的原始名称和链接。\n'
          '服务失败或数据冲突时停止依赖分析，不改用南财或其他来源。读取完成不表示市场数据完整。\n')
        specific = {'history':'仅不复权日周月；默认最近20个有记录周期；周月按自然周期，未结束与缺数状态分别说明。open仅表示截至数据as_of尚未覆盖周期结束，不代表今天仍处于该周/月；必须写清这一参照日期。',
          'announcement':'证券代码必填，关键词只匹配该证券标题；默认数据截止前30日；不下载正文，不推测公告详情。证券名称和标题必须逐字照录，包含拼音形式；不得补充中文别名，不翻译、不纠正。',
          'margin':'证券必填，只查询单证券，不提供全市场榜单；金额仅标源表原值，单位待核对，禁止换算。',
          'financial-statement':'报告期和披露日期分开；保留源报告版本，不能将累计值称单季。跨期比较必须先匹配同一口径。ann_date/f_ann_date是源披露日期，不是入库时间；相同披露日期不能断言同批导入。',
          'lhb-detail':'保留营业部和交易方向；汇总前核实有效性和重复业务键，不按行数推断参与机构数。没有明确字段字典时不得猜测TRANSDIR、CHGTYPE的枚举含义，只呈现源代码；不得根据金额结构推断买卖方向。',
          'macro-series':'仅CPI、LPR、指定曲线期限；不是南财全指标树。同一期冲突必须说明不可确定。',
          'event-calendar':'区分上市、除权、支付、赎回、转股价变更；申购代码不是股票代码。不推断未来日历。',
          'close-valuation':'仅历史收盘与已有股票估值，各源日期独立，不称实时；不补ETF/指数估值。',
          'security-search':'返回候选及市场，不自动选第一个；不支持事件关键词选股。',
          'local-search':'只搜索股票、ETF、基金和行业名称；行业不是概念主题，不能搜索跨证券公告。'}
        text = '---\nname: '+folder+'\ndescription: '+title+'，按实际本地数据查询并说明覆盖限制。\n---\n\n'+instructions+specific[key]+'\n'
        doc['skill'] = {'entry':'original/SKILL.md'}
        doc['examples'] = [{'id':'local-skill','title':title+'对话','description':'实际调用并解释限制。','requirements':[],'filePaths':[],'prompt':prompt,'criteria':'必须实际调用精确依赖；如实保留日期、单位、空值和不支持范围，不换源。'+specific[key]}]
        save(folder,doc,{'original/SKILL.md':text.encode()})
    folder='cd80-csv-export-tool'
    doc=manifest(folder,'tool','授权金融结果导出CSV')
    doc['permissions'].update(network='none',inputFiles=True,outputFiles=True)
    doc['tool']={'inputSchema':{'type':'object','properties':{'file':string('授权JSON文件名或/input路径；从本次授权清单复制')},'required':['file'],'additionalProperties':False},'execution':{'type':'python_wasi','path':'original/tool.py','callable':'run'}}
    doc['examples']=[{'id':'csv','title':'导出已授权结果','description':'保留前导零和空值','requirements':['授权包内示例JSON'], 'filePaths':['original/sample.json'],'input':{'file':'sample.json'},'expected':{'rows':2,'file':'result.csv','source':'fixture','warnings':[]}}]
    save(folder,doc,{'original/tool.py':(root/'src/csv_export.py').read_bytes(),'original/sample.json':json.dumps({'source':'fixture','records':[{'code':'000001','value':'0.30'},{'code':'000002','value':None}]}).encode()})
    folder='cd80-financial-compute-tool'
    doc=manifest(folder,'tool','授权财务比较与龙虎榜汇总')
    doc['permissions'].update(network='none',inputFiles=True)
    doc['tool']={'inputSchema':{'type':'object','properties':{'file':string('授权JSON文件名或/input路径；从本次授权清单复制'),'operation':string('计算',['financial_compare','lhb_summary']),**{k:string(k) for k in ['base_period','target_period','field']}},'required':['file','operation'],'additionalProperties':False},'execution':{'type':'python_wasi','path':'original/tool.py','callable':'run'}}
    sample={'source':'cd80_stock_fin_indicator','coverage':{'retrieval_complete':True},'records':[{'code':'600000.SH','report_period':p,'source_fields':{'eps':v}} for p,v in [('2024-12-31','0.10'),('2025-12-31','0.13')]]}
    doc['examples']=[{'id':'compare','title':'同口径财务比较','description':'精确小数；输入为独立夹具','requirements':['授权示例文件'],'filePaths':['original/sample.json'],'input':{'file':'sample.json','operation':'financial_compare','base_period':'2024-12-31','target_period':'2025-12-31','field':'eps'},'assertions':[{'path':['records',0,'difference'],'op':'eq','value':'0.03'},{'path':['records',0,'change_percent'],'op':'eq','value':'30.0'}]}]
    dependency=save(folder,doc,{'original/tool.py':(root/'src/financial_compute.py').read_bytes(),'original/sample.json':json.dumps(sample).encode()})
    folder='cd80-financial-compute'
    doc=manifest(folder,'skill','本地财务比较与龙虎榜汇总')
    doc['permissions']['inputFiles']=True
    doc['dependencies']=[dependency]
    doc['skill']={'entry':'original/SKILL.md'}
    doc['examples']=[{'id':'compare-skill','title':'授权文件计算','description':'对已授权结果计算','requirements':['完整本地结果JSON'],'filePaths':['original/sample.json'],'prompt':'对授权sample.json文件，比较600000.SH的eps，从2024-12-31到2025-12-31，实际调用工具计算差额与变化百分比，并明确说明文件未提供单位。','criteria':'使用精确依赖工具读取真实授权文件；差额0.03、百分比30%，保留原单位未知，不推导单季；子任务调用时通过inputRefs授权。'}]
    save(folder,doc,{'original/sample.json':json.dumps(sample).encode(),'original/SKILL.md':('---\nname: cd80-financial-compute\ndescription: 基于授权完整结果比较财务或汇总龙虎榜。\n---\n先取得完整本地结果工件。输出必须说明单位；输入没有单位时明确写单位未知，禁止默认为元。子任务通过inputRefs传递真实文件，不把ID当作内容。调用cd80/cd80-financial-compute-tool，file必须填系统授权清单的sandboxPath或其文件名部分，通常为fileId.json；sample.json只是展示名称，绝不能据此拼接/input/sample.json，也不能猜测ID。财务先明确证券、指标、报告期和唯一版本；累计指标只作相同月日跨年比较，不推导单季。龙虎榜保留原因和方向，只按已核实有效ID去重，不能把营业部行数称机构数。拒绝未完整输入、冲突和未知有效性；不换源、不联网、不改单位。\n').encode()})
    # Upgrade the existing news and presentation capabilities without inventing a source.
    old=root/'packages'
    doc=json.loads((old/'cd80-news-tool/capability.json').read_text(encoding='utf-8'))
    doc['source']['version']='2.0.0'
    doc['tool']['execution']['protocolVersion']='2'
    doc['tool']['inputSchema']['properties']['cursor']=string('上页返回的游标')
    news_code=('from query import execute\nfrom migration_common import result\n'
      'def run(payload):\n'
      '    args = payload["input"]\n'
      '    raw = execute("news", {"input":dict(args,limit=20,offset=0),"responses":payload["responses"]})\n'
      '    if "request" in raw: return raw\n'
      '    data = raw["result"]\n'
      '    out = result("akshare",args,data["records"],warnings=["仅当前来源缓存前20条匹配记录，不是历史新闻全集。"],as_of=data.get("as_of"))\n'
      '    out.update(provider_source=data["provider_source"],cache=data["cache"])\n'
      '    if data.get("warning"): out["warnings"].append({"code":"SOURCE_WARNING","message":data["warning"],"fields":[]})\n'
      '    return {"result":out}\n')
    news_ref=save('cd80-news-tool',doc,{'original/tool.py':news_code.encode(),'original/query.py':(root/'src/query.py').read_bytes(),'original/migration_common.py':(root/'src/migration_common.py').read_bytes()})
    for name in ('cd80-news','cd80-table'):
        doc=json.loads((old/name/'capability.json').read_text(encoding='utf-8'))
        doc['source']['version']='2.0.0'
        if name=='cd80-news':doc['dependencies']=[news_ref]
        text=(old/name/'original/SKILL.md').read_text(encoding='utf-8')+'\n如输入为financial-result/1，读取records并保留units、warnings、coverage和pagination；未完成分页时不得称全量。单位status为source_raw时禁止换算，未知值不能补零。\n'
        save(name,doc,{'original/SKILL.md':text.encode()})
    (target/'index.json').write_text(json.dumps({'status':'unpublished_candidates','packages':built},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Built', len(built), 'migration candidates; public catalog unchanged.')


if __name__ == '__main__':
    build()
