"""Read-only current status, explicit official upgrades and recoverable deletion."""
from .contracts import fail, now

PUBLIC='__platform_public__'


def personal_copies(lib,owner,version):
    """Find exact content versions, including older versions of current assets."""
    assets={a['assetId']:a for a in lib.store.list(owner,'asset')
            if not a.get('deletedAt') and a['kind']==version['manifest']['kind']}
    source=version['manifest']['source']['identity']
    matches=[]
    for candidate in lib.store.list(owner,'version'):
        asset=assets.get(candidate['assetId'])
        if (asset and candidate['contentDigest']==version['contentDigest']
                and candidate['manifest']['source']['identity']==source):
            matches.append({'assetId':asset['assetId'],'versionId':candidate['versionId'],'name':asset['name']})
    return sorted(matches,key=lambda item:(item['assetId'],item['versionId']))


def public_view(lib,owner,asset):
    from .admission import policy
    version=lib.version(PUBLIC,asset['assetId'],asset['latestVersionId'])
    copies=personal_copies(lib,owner,version)
    return {**asset,'personalCopies':copies,'owned':bool(copies),
            'admission':policy(lib,PUBLIC,version)}


def version_view(lib, owner, version):
    from .admission import policy
    from .test_cases import version_test_cases
    current=lib.configuration(owner,version)['configurationId']
    stale=current!=version.get('configurationId')
    if not stale:
        for dep in version.get('dependencyLocks',[]):
            dv=lib.version(owner,dep['assetId'],dep['versionId'])
            if lib.configuration(owner,dv)['configurationId']!=dep['configurationId']:stale=True
    admission=policy(lib,owner,version)
    readiness=version['readiness']
    if stale:
        readiness='needs_configuration' if readiness=='ready' else 'unverified'
    elif readiness=='verified' and not lib.evidence(owner,version):
        readiness='ready' if not admission['validationRequired'] else 'unverified'
    elif readiness=='ready' and admission['validationRequired']:
        readiness='unverified'
    return {**version,'configurationStale':stale,'admission':admission,'readiness':readiness,
            'testCases':version_test_cases(lib,owner,version)}


def asset_view(lib,owner,asset):
    view=dict(asset)
    version=lib.version(owner,asset['assetId'],asset.get('activeVersionId') or asset['latestVersionId'])
    current=version_view(lib,owner,version)
    view.update(readiness=current['readiness'],configurationStale=current['configurationStale'],
                admission=current['admission'])
    provenance=asset.get('provenance',{})
    if provenance.get('publicAssetId'):
        public=lib.store.get(PUBLIC,'asset',provenance['publicAssetId'],{})
        if public and public['lifecycle']=='active':
            latest=lib.version(PUBLIC,public['assetId'],public['latestVersionId'])
            baseline=lib.store.get(PUBLIC,'version',provenance.get('publicVersionId',''),{})
            view['officialUpdate']={'available':bool(baseline) and baseline['contentDigest']!=latest['contentDigest'],
                'versionId':latest['versionId'],'number':latest['number'],'publicAssetId':public['assetId']}
    return view


def delete_asset(lib,owner,key,etag,restore=False):
    with lib.store.transaction():
        asset=lib.asset(owner,key);lib.revision(asset,etag)
        if restore:
            names={a['name'] for a in lib.store.list(owner,'asset') if a['assetId']!=key and not a.get('deletedAt')}
            base=asset['name'];name=base;number=2
            while name in names:name=f'{base}_{number}';number+=1
            if name!=base:asset.update(name=name,displayName=name)
            asset.pop('deletedAt',None)
        else:asset['deletedAt']=now()
        asset.update(enabled=False,explicitlyDisabled=True,revision=asset['revision']+1)
        lib.store.put(owner,'asset',key,asset)
        return asset


def update_official(lib,owner,key,version_id,etag,stopped):
    from .official import prepare
    asset=lib.asset(owner,key);lib.revision(asset,etag)
    if asset.get('deletedAt') or asset['lifecycle']!='active':fail('CAPABILITY_BLOCKED','请先恢复此能力。',409)
    public=lib.asset(PUBLIC,asset.get('provenance',{}).get('publicAssetId',''))
    if public['lifecycle']!='active':fail('CAPABILITY_BLOCKED','官方能力已下架。',409)
    if public['latestVersionId']!=version_id:fail('REVISION_CONFLICT','官方版本已变化，请刷新并重新确认。',409)
    source=lib.version(PUBLIC,public['assetId'],version_id)
    # Preserve existing versions and edits. Import creates a version, never overwrites bytes.
    def unchanged():
        lib.revision(lib.asset(owner,key),etag)
        if lib.asset(PUBLIC,public['assetId'])['latestVersionId']!=version_id:
            fail('REVISION_CONFLICT','官方版本已变化，请重新确认。',409)
    result=prepare(lib,owner,source,stopped,before_copy=unchanged,target=key)
    with lib.store.transaction():
        updated=lib.asset(owner,result['assetId'])
        latest=lib.version(owner,result['assetId'],result['versionId'])
        updated.update(enabled=False,activeVersionId=None,readiness=latest['readiness'],issues=latest['issues'],name=updated.get('displayName') or latest['manifest']['name'],description=latest['manifest']['description'],revision=updated['revision']+1)
        lib.store.put(owner,'asset',updated['assetId'],updated)
    return result
