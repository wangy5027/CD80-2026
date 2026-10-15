"""Server-issued, exact-version admission for official and shared capabilities.

Package metadata is never evidence of provenance.  A grant is useful only while
the server can match it to an immutable source version or published snapshot.
"""

from .contracts import digest, fail

PUBLIC = '__platform_public__'
COMMUNITY = '__platform_community__'


def _same(version, source):
    manifest = source.get('manifest', {})
    if (version.get('contentDigest') != source.get('contentDigest') or
            version.get('manifest') != manifest or
            manifest.get('kind') not in {'skill', 'tool'}):
        return False
    if 'testCases' in version:
        from .test_cases import prepare_test_cases
        released_cases = source.get('testCases')
        if released_cases is None:
            # Community snapshots publish only manifest.examples, not an
            # author's private version cases.
            released_cases = prepare_test_cases(manifest, {})
        if version['testCases'] != released_cases:
            return False
    component = source.get('componentSnapshot')
    embedded = version.get('embeddedSnapshot')
    return (component is None and embedded is None) or (
        component is not None and embedded is not None and digest(component) == digest(embedded))


def _official(lib, version, reference):
    source = lib.store.get(PUBLIC, 'version', reference.get('publicVersionId', ''), {})
    return (bool(source) and source.get('assetId') == reference.get('publicAssetId')
            and _same(version, source))


def _community(lib, version, reference):
    record = lib.store.get(COMMUNITY, 'release', reference.get('communityReleaseId', ''), {})
    if not record or record.get('status') not in {'published', 'withdrawn'}:
        return False
    snapshot = record.get('snapshot', {})
    nodes = [snapshot.get('root'), *(snapshot.get('dependencies') or [])]
    return any(node and _same(version, node) for node in nodes)


def policy(lib, owner, version):
    if owner == PUBLIC and version.get('manifest', {}).get('kind') in {'skill', 'tool'}:
        return {'mode': 'trusted_release', 'source': 'official', 'validationRequired': False}
    source = None
    if owner != PUBLIC:
        grant = version.get('admissionGrant') or {}
        if grant.get('source') == 'official' and _official(lib, version, grant):
            source = 'official'
        elif grant.get('source') == 'community' and _community(lib, version, grant):
            source = 'community'
        elif 'testCases' not in version:
            # Old copies were persisted before version-scoped grants existed.
            # New versions carry testCases and require their own server grant;
            # asset provenance cannot authorize a changed case snapshot.
            asset = lib.store.get(owner, 'asset', version.get('assetId', ''), {})
            provenance = asset.get('provenance') or {}
            if provenance.get('publicVersionId') and _official(lib, version, provenance):
                source = 'official'
            elif (provenance.get('communityReleaseId') and
                  provenance.get('contentDigest') == version.get('contentDigest') and
                  _community(lib, version, provenance)):
                source = 'community'
    return {'mode': 'trusted_release' if source else 'validation_required',
            'source': source, 'validationRequired': not bool(source)}


def grant_official(lib, owner, version, public_version):
    if not _same(version, public_version):
        fail('INVALID_PACKAGE', '官方副本与精确来源版本不一致。', 409)
    version['admissionGrant'] = {'source': 'official', 'publicAssetId': public_version['assetId'],
                                 'publicVersionId': public_version['versionId'],
                                 'contentDigest': public_version['contentDigest']}
    lib.store.put(owner, 'version', version['versionId'], version)
    return version


def grant_community(lib, owner, version, release, node):
    if not _same(version, node) or node not in [release['snapshot']['root'], *release['snapshot']['dependencies']]:
        fail('INVALID_PACKAGE', '共享副本与公开版本的精确内容不一致。', 409)
    version['admissionGrant'] = {'source': 'community', 'communityReleaseId': release['releaseId'],
                                 'contentDigest': node['contentDigest']}
    lib.store.put(owner, 'version', version['versionId'], version)
    return version
