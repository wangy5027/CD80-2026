"""Publish only exact packages with real execution and explicit effect review evidence."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.modules.capability_library.contracts import digest
from backend.app.modules.capability_library.releases import check_release
from backend.app.modules.capability_library.packages import parse_package
from scripts.validation_suite_helpers import release_records


def main():
    report=json.loads((ROOT/'data/local-financial-verification.json').read_text(encoding='utf-8'))
    if not report.get('executionPassed'):raise RuntimeError('Execution evidence is incomplete; no publication changes made')
    evidence={k:report[k] for k in ('createdAt','system','runnerRevision','model')}
    evidence.update(executionPassed=True,examples=[])
    published=[]
    # Tools precede exact dependencies in the public library.
    records=sorted(report['examples'],key=lambda e:(e['kind']!='tool',e['folder']))
    released=set()
    for record in records:
        path=ROOT/'capabilities/local-financial/packages'/(record['folder']+'.zip')
        manifest,files=parse_package(path.read_bytes(),path.name)
        if digest(manifest)!=record['manifestDigest']:raise RuntimeError('Package changed after execution: '+record['folder'])
        if manifest['kind']=='skill':
            if record.get('review',{}).get('decision')!='accept':raise RuntimeError('Skill review incomplete: '+record['folder'])
            if any(d['contentDigest'] not in released for d in manifest['dependencies']):raise RuntimeError('Dependency evidence incomplete: '+record['folder'])
        expanded=release_records(record,manifest)
        for item in expanded:
            invocations=item.get('invocations',[])
            if manifest['kind']=='skill':
                for dep in manifest['dependencies']:
                    tool=next(e for e in records if e['contentDigest']==dep['contentDigest'])
                    if not any(i.get('assetId')==tool['assetId'] and i.get('versionId')==tool['versionId'] and i.get('status')=='succeeded' for i in invocations):
                        raise RuntimeError('Skill example missing exact dependency invocation: '+record['folder']+'/'+item['id'])
            item['calls']=[{k:i[k] for k in ('invocationId','status','input','result','dataReads') if k in i} for i in invocations]
        candidate={**evidence,'examples':expanded}
        check_release(manifest,candidate)
        evidence['examples'].extend(expanded);released.add(record['contentDigest'])
        published.append({'package':'local-financial/packages/'+path.name,'evidence':'local-financial/release-evidence.json'})
    (ROOT/'capabilities/local-financial/release-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    catalog=ROOT/'capabilities/public-catalog.json'
    existing=json.loads(catalog.read_text(encoding='utf-8'))
    existing['packages']=[i for i in existing['packages'] if not i['package'].startswith('local-financial/packages/')]+published
    catalog.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Published '+str(len(published))+' independently verified packages.')


if __name__=='__main__':main()
