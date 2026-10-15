"""Publish exact, executed, reviewed candidates; never upgrades personal copies."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.modules.capability_library.packages import parse_package,file_index
from backend.app.modules.capability_library.contracts import digest
from backend.app.modules.capability_library.releases import check_release
from scripts.validation_suite_helpers import release_records


def main():
    report=json.loads((ROOT/'data/migration-release-verification.json').read_text(encoding='utf-8'))
    reviews=json.loads((ROOT/'data/migration-skill-reviews.json').read_text(encoding='utf-8'))
    if not report.get('executionPassed'):raise RuntimeError('Execution evidence is incomplete')
    evidence={k:report[k] for k in ('createdAt','runnerRevision','system','model','transport','modelOptions','executionPassed')}
    evidence['examples']=[]
    packages=[]
    for path in (ROOT/'capabilities/local-financial/migration-candidates').glob('*.zip'):
        manifest,files=parse_package(path.read_bytes(),path.name)
        record=next(r for r in report['examples'] if r['folder']==path.stem and r['manifestDigest']==digest(manifest))
        if record['contentDigest']!=digest({'manifest':manifest,'files':file_index(files)}):raise RuntimeError('Content digest mismatch')
        record=dict(record)
        if manifest['kind']=='skill':
            review=reviews.get(path.stem,{})
            if review.get('manifestDigest')!=digest(manifest):raise RuntimeError('Missing exact-version review: '+path.stem)
            record['review']=review
        expanded=release_records(record,manifest)
        if manifest['kind']=='skill':
            for item in expanded:
                for dependency in manifest.get('dependencies',[]):
                    tool=next((r for r in report['examples'] if r['kind']=='tool' and r['contentDigest']==dependency['contentDigest']),None)
                    if not tool or not any(i.get('status')=='succeeded' and i.get('assetId')==tool['assetId'] and i.get('versionId')==tool['versionId'] for i in item.get('invocations',[])):
                        raise RuntimeError('No actual exact dependency invocation for example: '+path.stem+'/'+item['id'])
        evidence['examples'].extend(expanded)
        packages.append((path,manifest))
    if len(packages)!=26 or len(evidence['examples'])!=sum(len(manifest.get('examples',[])) for _,manifest in packages):raise RuntimeError('Incomplete release set or per-example evidence')
    for _,manifest in packages:check_release(manifest,evidence)
    packages.sort(key=lambda p:(p[1]['kind']!='tool',p[0].name))
    release=ROOT/'capabilities/local-financial/releases'/digest([m for _,m in packages])[:16]
    release.mkdir(parents=True,exist_ok=True)
    evidence_path=release/'evidence.json'
    for path,_ in packages:
        destination=release/path.name
        if destination.exists() and destination.read_bytes()!=path.read_bytes():raise RuntimeError('Immutable release collision')
        destination.write_bytes(path.read_bytes())
    evidence_path.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    catalog_path=ROOT/'capabilities/public-catalog.json'
    catalog=json.loads(catalog_path.read_text(encoding='utf-8'))
    backup=ROOT/'capabilities/local-financial/pre-migration-catalog.json'
    if not backup.exists():backup.write_bytes(catalog_path.read_bytes())
    identities={m['source']['identity'] for _,m in packages}
    retained=[]
    for item in catalog['packages']:
        manifest,_=parse_package((ROOT/'capabilities'/item['package']).read_bytes(),Path(item['package']).name)
        if manifest['source']['identity'] not in identities:retained.append(item)
    packages.sort(key=lambda p:(p[1]['kind']!='tool',p[0].name))
    catalog['packages']=retained+[{'package':(release/p.name).relative_to(ROOT/'capabilities').as_posix(),'evidence':evidence_path.relative_to(ROOT/'capabilities').as_posix()} for p,_ in packages]
    catalog['retiredCapabilities']=sorted(set(catalog.get('retiredCapabilities',[]))|{'sfc_history','sfc-history','sfc_announcements','sfc-announcement'})
    pending=catalog_path.with_suffix('.pending.json')
    pending.write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    pending.replace(catalog_path)
    print('Published catalog:',len(packages),'exact verified packages;',release.relative_to(ROOT))


if __name__=='__main__':main()
