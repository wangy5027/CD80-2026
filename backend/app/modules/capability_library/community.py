"""User-published immutable capability snapshots, separate from official trust."""
import copy
import io
import zipfile

from .contracts import canonical, digest, fail, now, uid
from .packages import file_index, parse_package, safe_path
from .sharing import assert_shareable
from .admission import grant_community, _same

COMMUNITY = '__platform_community__'
MAX_PACKAGES = 16
MAX_BUNDLE_BYTES = 20 * 1024 * 1024
MAX_REVIEW_BYTES = 4 * 1024 * 1024
MAX_REVIEW_FILES = 512
_PUBLIC_FIELDS = ('releaseId', 'releaseNumber', 'previousReleaseId', 'title', 'description', 'kind', 'status', 'revision', 'publishedAt', 'withdrawnAt', 'contentDigest', 'snapshotDigest')


def _text(value, default, limit):
    value = default if value is None else value
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail('INVALID_REQUEST', '发布名称或说明为空、类型无效或超过长度限制。')
    return value.strip()


def _summary(node):
    manifest = node['manifest']
    return {'name': manifest['name'], 'kind': manifest['kind'], 'sourceIdentity': manifest['source']['identity'],
            'contentDigest': node['contentDigest'], 'permissions': copy.deepcopy(manifest['permissions']),
            'files': copy.deepcopy(manifest['files']), 'manifest': copy.deepcopy(manifest)}


def _source_archive(files):
    """Keep preview and confirmation byte-identical regardless of review time."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(files.items()):
            entry = zipfile.ZipInfo(safe_path(path), date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, content)
    return output.getvalue()


def _review_summary(node, raw):
    """Show the exact archived bytes whose digest the author will confirm."""
    source_files = []
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for path in sorted(archive.namelist()):
            content = archive.read(path)
            source_files.append({'path': path, 'text': content.decode('utf-8'),
                                 'sha256': digest(content), 'size': len(content)})
    return {**_summary(node), 'sourceFiles': source_files}


def _prepare(lib, owner, payload):
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), str) and payload[k] for k in ('assetId', 'versionId')):
        fail('INVALID_REQUEST', '请选择自己的能力和精确版本。')
    root = lib.version(owner, payload['assetId'], payload['versionId'])
    title = _text(payload.get('title'), root['manifest']['name'], 128)
    description = _text(payload.get('description'), root['manifest']['description'], 4096)
    assert_shareable({'title': title, 'description': description})
    nodes, visiting, seen = [], set(), set()
    review = {'fileCount': 0, 'totalBytes': 0, 'maxFiles': MAX_REVIEW_FILES, 'maxBytes': MAX_REVIEW_BYTES}

    if root.get('embeddedSnapshot'):
        from .components import export_snapshot, _files
        asset = lib.asset(owner, root['assetId'])
        if asset.get('deletedAt') or asset.get('lifecycle') != 'active':
            fail('CAPABILITY_BLOCKED', '已删除或归档的能力不能发布。', 409)
        root = lib.check(owner, root)
        if root['readiness'] != 'verified' or not lib.evidence(owner, root):
            fail('VERIFICATION_REQUIRED', '请先在当前配置下完成整个组件的真实用例验证。', 409)
        portable = export_snapshot(root['embeddedSnapshot'])
        def closed(component):
            identity = (component['manifest']['source']['identity'], component['contentDigest'])
            if identity in seen:
                return
            for child in component['dependencies']:
                closed(child)
            files = _files(component)
            assert_shareable(component['manifest'], files)
            source = {**files, 'capability.json': canonical(component['manifest']).encode('utf-8')}
            review['fileCount'] += len(source)
            review['totalBytes'] += sum(map(len, source.values()))
            if review['fileCount'] > MAX_REVIEW_FILES or review['totalBytes'] > MAX_REVIEW_BYTES:
                fail('SHARING_REVIEW_LIMIT_EXCEEDED', '共享完整闭包超过源码审阅限制。', 413, review)
            raw = _source_archive(source)
            nodes.append(({'manifest': copy.deepcopy(component['manifest']), 'contentDigest': component['contentDigest'],
                           'packageDigest': digest(raw)}, raw))
            seen.add(identity)
        closed(portable)
        nodes[-1][0]['componentSnapshot'] = portable
        if sum(len(raw) for _node, raw in nodes) > MAX_BUNDLE_BYTES:
            fail('LIMIT_EXCEEDED', '共享源包总大小超过20 MiB。', 413)
        return {'title': title, 'description': description, 'root': nodes[-1][0],
                'dependencies': [node for node, _raw in nodes[:-1]]}, nodes

    def visit(version):
        identity = (version['manifest']['source']['identity'], version['contentDigest'])
        if identity in visiting:
            fail('SHARING_DEPENDENCY_CYCLE', '依赖存在循环，不能发布。', 409)
        if identity in seen:
            return
        if len(seen) + len(visiting) >= MAX_PACKAGES:
            fail('LIMIT_EXCEEDED', '共享最多包含16个能力包。', 413)
        asset = lib.asset(owner, version['assetId'])
        if asset.get('deletedAt') or asset.get('lifecycle') != 'active':
            fail('CAPABILITY_BLOCKED', '已删除或归档的能力不能发布。', 409)
        if version['manifest']['kind'] not in {'tool', 'skill'}:
            fail('CAPABILITY_BLOCKED', '仅能发布 Tool 或 Skill，私人连接不能共享。', 409)
        visiting.add(identity)
        for dependency in version['manifest']['dependencies']:
            candidates = [v for v in lib.store.list(owner, 'version')
                          if v['manifest']['kind'] == 'tool' and v['contentDigest'] == dependency['contentDigest']
                          and v['manifest']['source']['identity'] == dependency['sourceIdentity']
                          and not lib.asset(owner, v['assetId']).get('deletedAt')
                          and lib.asset(owner, v['assetId']).get('lifecycle') == 'active']
            previous = {d['versionId'] for d in version.get('dependencyLocks', [])}
            candidates.sort(key=lambda v: (v['versionId'] not in previous, v['versionId']))
            if not candidates:
                fail('MISSING_DEPENDENCY', '缺少指定内容的个人依赖，不能发布不完整共享包。', 409,
                     {'sourceIdentity': dependency['sourceIdentity']})
            visit(candidates[0])
        # Recompute admission/evidence bindings after checking dependency versions.
        version = lib.check(owner, version)
        files = lib.files(owner, version)
        if version.get('containsSecret'):
            fail('SHARING_CONTAINS_SECRET', '原上传包疑似包含秘密，不能共享。', 403)
        assert_shareable(version['manifest'], files)
        if version.get('readiness') != 'verified' or not lib.evidence(owner, version):
            fail('VERIFICATION_REQUIRED', '请先在当前配置下验证此能力及全部依赖，再发布。', 409)
        source_files = {**files, 'capability.json': canonical(version['manifest']).encode('utf-8')}
        review['fileCount'] += len(source_files)
        review['totalBytes'] += sum(len(content) for content in source_files.values())
        if review['fileCount'] > MAX_REVIEW_FILES or review['totalBytes'] > MAX_REVIEW_BYTES:
            fail('SHARING_REVIEW_LIMIT_EXCEEDED', '共享预览最多512个文件、源码总计4 MiB（含依赖清单）。请拆分能力包后重新发布。', 413, review)
        raw = _source_archive(source_files)
        node = {'manifest': copy.deepcopy(version['manifest']), 'contentDigest': version['contentDigest'],
                'packageDigest': digest(raw)}
        nodes.append((node, raw))
        visiting.remove(identity)
        seen.add(identity)

    visit(root)
    if sum(len(raw) for _node, raw in nodes) > MAX_BUNDLE_BYTES:
        fail('LIMIT_EXCEEDED', '共享源包总大小超过20 MiB。', 413)
    snapshot = {'title': title, 'description': description, 'root': nodes[-1][0],
                'dependencies': [node for node, _raw in nodes[:-1]]}
    return snapshot, nodes


def preview(lib, owner, payload):
    with lib.store.lock:
        snapshot, nodes = _prepare(lib, owner, payload)
        reviewed = [_review_summary(node, raw) for node, raw in nodes]
        source_files = [item for node in reviewed for item in node['sourceFiles']]
        return {'snapshotDigest': digest(snapshot), 'title': snapshot['title'], 'description': snapshot['description'],
                'root': reviewed[-1], 'dependencies': reviewed[:-1],
                'sourceReview': {'fileCount': len(source_files), 'totalBytes': sum(item['size'] for item in source_files),
                                 'maxFiles': MAX_REVIEW_FILES, 'maxBytes': MAX_REVIEW_BYTES},
                'warnings': ['以上源包文件及示例将对本机平台所有登录账号公开，请确认你有权共享且不含私人资料。',
                             '不包含私人连接、凭据、运行文件、测试输入输出或验证记录；复制者仍需配置本账号环境并主动启用。',
                             '自动检查不能识别所有秘密或敏感信息，请审阅完整源文件。']}


def _copies(lib, owner, node):
    from .lifecycle import personal_copies
    return personal_copies(lib, owner, node)


def view(lib, owner, record, *, detail=True):
    value = {k: record[k] for k in _PUBLIC_FIELDS if k in record}
    copies = _copies(lib, owner, record['snapshot']['root'])
    value.update(isOwner=record['publisherId'] == owner, personalCopies=copies, owned=bool(copies))
    if detail:
        value['root'] = _summary(record['snapshot']['root'])
        value['dependencies'] = [_summary(n) for n in record['snapshot']['dependencies']]
    else:
        value['dependencyCount'] = len(record['snapshot']['dependencies'])
    return value


def get(lib, owner, release_id, *, copying=False):
    record = lib.store.get(COMMUNITY, 'release', release_id)
    if record['status'] != 'published' and (copying or record['publisherId'] != owner):
        fail('NOT_FOUND', '共享版本不存在或已撤回。', 404)
    return record


def list_releases(lib, owner, *, mine=False, kind=None, query=''):
    if kind and kind not in {'tool', 'skill'}:
        fail('INVALID_REQUEST', '能力类型无效。')
    with lib.store.lock:
        records = lib.store.list(COMMUNITY, 'release')
        records = [r for r in records if (r['publisherId'] == owner if mine else r['status'] == 'published')]
        if kind:
            records = [r for r in records if r['kind'] == kind]
        if query:
            records = [r for r in records if query.casefold() in (r['title'] + ' ' + r['description']).casefold()]
        return [view(lib, owner, r, detail=False) for r in records]


def publish(lib, owner, payload):
    if payload.get('confirmSharing') is not True:
        fail('SHARING_CONFIRMATION_REQUIRED', '请预览并明确确认共享全部列出的源文件。', 409)
    with lib.store.lock:
        snapshot, nodes = _prepare(lib, owner, payload)
        fingerprint = digest(snapshot)
        if payload.get('snapshotDigest') != fingerprint:
            fail('SHARING_PREVIEW_CHANGED', '共享内容或发布说明已变化，请重新预览后确认。', 409)
        prior = [r for r in lib.store.list(COMMUNITY, 'release') if r['publisherId'] == owner and r['sourceAssetId'] == payload['assetId']]
        same = next((r for r in prior if r['snapshotDigest'] == fingerprint and r['status'] == 'published'), None)
        if same:
            return {**view(lib, owner, same), 'deduplicated': True}
        previous = max(prior, key=lambda r: r['releaseNumber']) if prior else None
        record = {'releaseId': uid('release'), 'releaseNumber': (previous['releaseNumber'] + 1) if previous else 1,
                  'previousReleaseId': previous['releaseId'] if previous else None,
                  'publisherId': owner, 'sourceAssetId': payload['assetId'], 'sourceVersionId': payload['versionId'],
                  'title': snapshot['title'], 'description': snapshot['description'], 'kind': snapshot['root']['manifest']['kind'],
                  'status': 'published', 'revision': 1, 'publishedAt': now(), 'contentDigest': snapshot['root']['contentDigest'],
                  'snapshotDigest': fingerprint, 'snapshot': snapshot}
        with lib.store.transaction():
            for node, raw in nodes:
                lib.store.blob(COMMUNITY, node['packageDigest'], raw)
            lib.store.put(COMMUNITY, 'release', record['releaseId'], record)
        return view(lib, owner, record)


def withdraw(lib, owner, release_id, payload):
    with lib.store.transaction():
        record = lib.store.get(COMMUNITY, 'release', release_id)
        if record['publisherId'] != owner:
            fail('NOT_FOUND', '共享版本不存在或无权修改。', 404)
        if type(payload.get('revision')) is not int or payload['revision'] != record['revision']:
            fail('REVISION_CONFLICT', '发布状态已变化，请刷新后重试。', 409)
        if record['status'] != 'withdrawn':
            record.update(status='withdrawn', withdrawnAt=now(), revision=record['revision'] + 1)
            lib.store.put(COMMUNITY, 'release', release_id, record)
        return view(lib, owner, record)


def _existing_dependency(lib, owner, node):
    for version in lib.store.list(owner, 'version'):
        if not _same(version, node):
            continue
        asset = lib.asset(owner, version['assetId'])
        if not asset.get('deletedAt') and asset.get('lifecycle') == 'active':
            return {'assetId': asset['assetId'], 'versionId': version['versionId'], 'reused': True}
    return None


def _admit_copy(lib, owner, version, record, node):
    version = lib.check(owner, grant_community(lib, owner, version, record, node))
    asset = lib.asset(owner, version['assetId'])
    if (asset.get('activeVersionId') or asset.get('latestVersionId')) == version['versionId']:
        asset.update(readiness=version['readiness'], issues=version['issues'], revision=asset['revision'] + 1)
        lib.store.put(owner, 'asset', asset['assetId'], asset)
    return version


def _install(lib, owner, node, raw, record, dependencies=()):
    """Install one source-only version inside copy_release's aggregate transaction.

    Uses the normal package parser, immutable record shape and lib.check. Calling
    import_package here would independently commit each dependency.
    """
    manifest, files = parse_package(raw, 'community-copy.zip')
    content_digest = digest({'manifest': {**manifest, 'files': file_index(files)}, 'files': file_index(files)})
    if content_digest != node['contentDigest'] or digest(raw) != node['packageDigest']:
        fail('INVALID_PACKAGE', '共享包内容摘要不一致，已停止复制。', 409)
    assert_shareable(manifest, files)
    names = {a['name'] for a in lib.store.list(owner, 'asset')}
    base, number = manifest['name'], 2
    name = base
    while name in names:
        name = f'{base}_{number}'
        number += 1
    asset = {'assetId': uid('asset'), 'name': name, 'displayName': name, 'description': manifest['description'],
             'kind': manifest['kind'], 'sourceIdentity': manifest['source']['identity'], 'scope': 'personal',
             'lifecycle': 'active', 'enabled': False, 'activeVersionId': None, 'revision': 1, 'createdAt': now(),
             'provenance': {'communityReleaseId': record['releaseId'], 'contentDigest': content_digest}}
    lib.store.put(owner, 'asset', asset['assetId'], asset)
    lib.store.blob(owner, digest(raw), raw)
    for data in files.values():
        lib.store.blob(owner, digest(data), data)
    from .test_cases import prepare_test_cases
    version = {'versionId': uid('ver'), 'assetId': asset['assetId'], 'number': 1, 'contentDigest': content_digest,
               'manifest': manifest, 'originalId': digest(raw), 'originalName': 'community-copy.zip',
               'createdAt': now(), 'containsSecret': False, 'validationPolicy': 'original_and_new',
               'testCases': prepare_test_cases(manifest, files)}
    if node.get('componentSnapshot'):
        from .components import export_snapshot
        component = export_snapshot(node['componentSnapshot'])
        if component['contentDigest'] != content_digest or component['manifest'] != manifest:
            fail('INVALID_PACKAGE', '共享组件闭包与根包不一致。', 409)
        version.update(embeddedSnapshot=component, componentBindings={}, validationPolicy='original_and_new')
    # The generic resolver also considers archived exact copies. Preserve the
    # active versions explicitly selected by this aggregate copy operation.
    required = {(d['sourceIdentity'], d['contentDigest']) for d in manifest['dependencies']}
    version['dependencyLocks'] = [
        {'assetId': item['assetId'], 'versionId': item['versionId']}
        for item in dependencies if (item['sourceIdentity'], item['contentDigest']) in required
    ]
    version = _admit_copy(lib, owner, version, record, node)
    asset.update(latestVersionId=version['versionId'], readiness=version['readiness'], issues=version['issues'], revision=2)
    lib.store.put(owner, 'asset', asset['assetId'], asset)
    return {'assetId': asset['assetId'], 'versionId': version['versionId'], 'readiness': version['readiness'],
            'issues': version['issues'], 'admission': version['admission'], 'reused': False}


def copy_release(lib, owner, release_id, payload):
    with lib.store.transaction():
        record = get(lib, owner, release_id, copying=True)
        snapshot = record['snapshot']
        copies = [item for item in _copies(lib, owner, snapshot['root'])
                  if lib.asset(owner, item['assetId']).get('lifecycle') == 'active'
                  and _same(lib.version(owner, item['assetId'], item['versionId']), snapshot['root'])]
        if copies and payload.get('ensureOwned') is not True and payload.get('confirmDuplicate') is not True:
            fail('DUPLICATE_CONFIRMATION_REQUIRED', '你的库中已有相同内容，确认后可另存独立副本。', 409)
        copied = []
        for node in ([] if snapshot['root'].get('componentSnapshot') else snapshot['dependencies']):
            result = _existing_dependency(lib, owner, node)
            if result is None:
                result = _install(lib, owner, node, lib.store.blob(COMMUNITY, node['packageDigest']), record, copied)
            else:
                local = _admit_copy(lib, owner, lib.version(owner, result['assetId'], result['versionId']), record, node)
                result.update(readiness=local['readiness'], issues=local['issues'], admission=local['admission'])
            copied.append({**result, 'sourceIdentity': node['manifest']['source']['identity'], 'contentDigest': node['contentDigest']})
        node = snapshot['root']
        if copies and payload.get('ensureOwned') is True:
            chosen = copies[0]
            local = _admit_copy(lib, owner, lib.version(owner, chosen['assetId'], chosen['versionId']), record, node)
            result = {'assetId': chosen['assetId'], 'versionId': chosen['versionId'],
                      'readiness': local['readiness'], 'issues': local['issues'],
                      'admission': local['admission'], 'reused': True}
        else:
            result = _install(lib, owner, node, lib.store.blob(COMMUNITY, node['packageDigest']), record, copied)
        return {**result, 'status': 'completed', 'releaseId': release_id, 'dependencies': copied,
                'alreadyOwned': bool(copies and payload.get('ensureOwned') is True),
                'notice': ('此精确版本已在我的库中。' if copies and payload.get('ensureOwned') is True else
                           '已保存含完整组件的独立副本。请配置本账号连接，检查后主动启用。'
                           if snapshot['root'].get('componentSnapshot') else
                           '已保存独立个人副本。新复制的能力未启用，请检查依赖后主动启用。')}
