"""Build self-contained public candidates; does not publish or enable anything."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app.modules.capability_library.packages import file_index, zip_bytes, inspect, parse_package
from backend.app.modules.capability_library.contracts import digest


def build():
    root = ROOT/'capabilities/local-financial'
    common = (root/'src/query.py').read_bytes()
    output = root/'packages'
    output.mkdir(parents=True, exist_ok=True)
    refs = {}
    names = {'history':'本地历史日线','announcement':'本地公告列表','margin':'本地融资融券','news':'已接入财经新闻'}
    prompts = {'history':'查询600000.SH在2025-07-02的不复权股票日线，列出价格、成交量和单位。',
        'announcement':'查询600000.SH在2025-07-01至2025-07-04的公告列表，最多3条，只列标题日期和原文链接。',
        'margin':'查询600000在2025-07-02的融资买入、偿还和净买入，并说明数据日期。',
        'news':'读取东方财富最新3条财经新闻，说明来源和缓存状态；如果读取失败请直接说明，不换来源。'}
    samples = {'history':{'code':'600000.SH','date':'2025-07-02','asset':'stock','limit':3},
        'announcement':{'code':'600000.SH','start_date':'2025-07-01','end_date':'2025-07-04','limit':3},
        'margin':{'code':'600000','date':'2025-07-02','limit':3},'news':{'source':'eastmoney','limit':3}}
    def save(folder, kind, title, original, extra, examples, deps=None):
        manifest={'format':'cd80.capability/1','kind':kind,'name':title,'description':title+'；只使用明确接入数据，注明来源日期和缺口。',
            'source':{'identity':'cd80/'+folder,'format':'platform','version':'1.0.0'},'files':file_index(original),
            'dependencies':deps or [],'permissions':{'network':'connector_only' if kind=='tool' else 'none','externalEffects':'none','inputFiles':False,'outputFiles':False},
            kind:extra,'examples':examples}
        assert not inspect(manifest,original),inspect(manifest,original)
        files={**original,'capability.json':(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()}
        target=output/folder;target.mkdir(exist_ok=True)
        for path,content in files.items():
            dest=target/path;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(content)
        package=zip_bytes(files);parse_package(package,folder+'.zip')
        (output/(folder+'.zip')).write_bytes(package)
        return {'sourceIdentity':manifest['source']['identity'],'contentDigest':digest({'manifest':manifest,'files':file_index(original)}),'required':True,'reason':'读取已接入的业务数据'}
    for kind,title in names.items():
        properties={'limit':{'type':'integer','minimum':1,'maximum':20 if kind=='news' else 100,'title':'条数'},'offset':{'type':'integer','minimum':0,'maximum':1000,'title':'跳过条数'}}
        if kind=='news': properties.update(source={'type':'string','enum':['eastmoney','cls'],'title':'新闻来源'},keyword={'type':'string','maxLength':80,'title':'关键词'})
        else:
            properties['code']={'type':'string','maxLength':16,'title':'证券代码','description':'两融使用6位数字；其他查询使用交易所后缀，例如600000.SH。'}
            for field in ('date','start_date','end_date'): properties[field]={'type':'string','minLength':10,'maxLength':10,'title':{'date':'指定日期','start_date':'开始日期','end_date':'结束日期'}[field],'description':'YYYY-MM-DD；指定日期与区间互斥。'}
            if kind=='history':properties['asset']={'type':'string','enum':['stock','etf','index'],'title':'资产类型'}
            if kind=='announcement':properties['keyword']={'type':'string','maxLength':80,'title':'标题关键词'}
        assertions=[{'path':['source'],'op':'eq','value':{'history':'cd80_daily','announcement':'cd80_anns_d','margin':'cd80_margin_data','news':'akshare'}[kind]},
            {'path':['records'],'op':'minItems','value':1}, {'path':['records'],'op':'eachType','value':'object'}]
        if kind=='news':assertions += [{'path':['provider_source'],'op':'eq','value':'eastmoney'},{'path':['records',0],'op':'requiredKeys','value':['title','published_at','summary']},{'path':['records',0,'published_at'],'op':'date','value':'iso-datetime'},{'path':['cache'],'op':'requiredKeys','value':['stale','fetched_at']}]
        else:assertions += [{'path':['records',0,'code'],'op':'eq','value':samples[kind]['code']},{'path':['records',0,'date'],'op':'date','value':'iso-date'},{'path':['as_of'],'op':'date','value':'iso-date'}]
        if kind=='margin':assertions += [{'path':['records',0,'financing_net_buy'],'op':'eq','value':'59194128.0000'}]
        example={'id':'real-query','title':title+'真实读取','description':'实际调用已接入接口，按字段与业务口径核对。','requirements':['保存个人工具副本后运行示例，通过后主动启用。'],'filePaths':[], 'input':samples[kind],'assertions':assertions,'prompt':prompts[kind]}
        original={'original/tool.py':('from query import execute\ndef run(payload):\n    return execute('+repr(kind)+', payload)\n').encode(),'original/query.py':common}
        conn='cd80.news' if kind=='news' else 'cd80.local-data'
        refs[kind]=save('cd80-'+kind+'-tool','tool',title+'工具',original,{'inputSchema':{'type':'object','properties':properties,'required':[] if kind=='news' else ['code'],'additionalProperties':False},'execution':{'type':'python_connector','path':'original/tool.py','callable':'run','connections':[{'identity':conn,'operations':['latest' if kind=='news' else 'query']}] }},[example])
    for kind,title in names.items():
        skill='cd80-'+kind
        text='---\nname: '+skill+'\ndescription: '+title+'。用户需要该类数据时使用，依赖明确的本地查询工具。\n---\n\n# '+title+'\n\n实际调用依赖工具 cd80/'+skill+'-tool，不能用模型记忆替代。\n证券不明确时先澄清；日期按用户要求，未指定使用数据集最新可用日期，不将历史数据称实时。\n工具失败则说明失败并停止依赖分析，不调用南财或自行换来源。\n以对话表格展示结果，保留来源、日期、单位、分页与缺失说明。公告只列标题和链接，不编造正文或摘要。新闻保留原来源、发布时间和缓存过期标识。两融不混淆融资余额与两融总余额。\n'
        if kind == 'margin':
            text+='\n金额单位尚未核验。必须原样注明“源表金额原值，单位待核对”，表头也只能写“源表原值”。禁止自行标注元、万元、亿元或人民币，禁止换算，用户要求换算时说明条件不足。交易所内部代码只原样展示，不猜测对应名称。净买入只做融资买入减融资偿还；任一字段缺失时结果必须缺失。\n'
        if kind == 'announcement':
            text+='\n公告标题和证券名称属于原始数据，必须逐字保留，不纠正拼写、不翻译、不替换音译英文；即使看起来异常也照录并提示为源表原文。链接必须使用工具实际返回URL，不推测公告正文。\n'
        example={'id':'real-skill','title':title+'对话示例','description':'调用依赖工具，按实际数据回答。','requirements':['先复制并验证启用依赖Tool，再复制本Skill。'],'filePaths':[],'prompt':prompts[kind],'criteria':'必须实际调用依赖Tool；数值与返回一致；注明来源和日期及必要单位；不补造缺失数据，不调用南财，不把历史数据当实时。新闻读取失败不算本次发布成功。'+('两融金额只能标注源表原值/单位待核对，不能猜测元或进行换算。' if kind=='margin' else '')}
        save(skill,'skill',title,{'original/SKILL.md':text.encode()}, {'entry':'original/SKILL.md'},[example],[refs[kind]])
    text='---\nname: cd80-table\ndescription: 将当前对话中已有金融数据整理为表格，保留单位、日期和来源；不负责取数或生成图片文件。\n---\n\n只使用已提供的数据，按用户比较目的组织列。保留证券代码前导零。金额和数量标明单位，空值写未提供，不补零。数据日期、来源和覆盖不足说明放在表格附近。输入缺少单位时注明未提供，不猜测。不同日期不能假装同一时点。输出对话内表格，不截图，不导出文件。\n'
    save('cd80-table','skill','金融对话表格',{'original/SKILL.md':text.encode()},{'entry':'original/SKILL.md'},[{'id':'table-basic','title':'保留缺失值的表格','description':'整理给定数据，不联网。','requirements':[],'filePaths':[],'prompt':'整理为表格：来源本地查询，日期2025-07-02，融资买入单位元；600000为100，000001未提供。','criteria':'保留600000和000001、日期和来源；100单位元；000001标未提供，不能补零；只输出对话表格，不生成文件。'}])
    print('Built 4 tools and 5 skills (not published).')


if __name__=='__main__':build()
