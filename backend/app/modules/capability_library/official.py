"""Install immutable official Skill/Tool versions without executing examples."""
from contextlib import nullcontext

from .admission import grant_official, _same
from .contracts import canonical, fail
from .packages import zip_bytes
from .test_cases import version_test_cases

PUBLIC = '__platform_public__'


def _trust(library, owner, personal, source):
    personal = grant_official(library, owner, personal, source)
    personal = library.check(owner, personal)
    asset = library.asset(owner, personal['assetId'])
    if (asset.get('activeVersionId') or asset.get('latestVersionId')) == personal['versionId']:
        asset.update(readiness=personal['readiness'], issues=personal['issues'], revision=asset['revision'] + 1)
        library.store.put(owner, 'asset', asset['assetId'], asset)
    return personal


def copy_version(library, owner, version, *, target=None, force_new=False, _in_transaction=False):
    with (nullcontext() if _in_transaction else library.store.transaction()):
        public = library.version(PUBLIC, version['assetId'], version['versionId'])
        asset = library.asset(PUBLIC, public['assetId'])
        if public['manifest']['kind'] not in {'skill', 'tool'}:
            fail('INVALID_REQUEST', '仅支持添加官方 Skill 或 Tool。')
        if asset.get('lifecycle') != 'active' or asset.get('deletedAt'):
            fail('CAPABILITY_BLOCKED', '该官方能力已下架。', 409)
        result = library.import_package(owner, zip_bytes({**library.files(PUBLIC, public),
            'capability.json': canonical(public['manifest']).encode()}), 'public-copy.zip',
            target=target, force_new=force_new, _in_transaction=True,
            test_cases=version_test_cases(library, PUBLIC, public))
        asset = library.asset(owner, result['assetId'])
        asset['provenance'] = {'publicAssetId': public['assetId'], 'publicVersionId': public['versionId']}
        library.store.put(owner, 'asset', asset['assetId'], asset)
        personal = _trust(library, owner, library.version(owner, result['assetId'], result['versionId']), public)
        return {**result, 'readiness': personal['readiness'], 'runtimeReadiness': personal['readiness'],
                'issues': personal['issues'], 'admission': personal['admission']}


def copy_closure(library, owner, root, *, target=None, force_new=False,
                 confirm_duplicate=False, before_copy=None, stopped=None):
    """Ensure the exact official dependency closure, with no invocation or verification."""
    from .lifecycle import personal_copies

    with library.store.transaction():
        copied, visiting, resolved = [], set(), {}
        public_versions = library.store.list(PUBLIC, 'version')
        root = library.version(PUBLIC, root['assetId'], root['versionId'])
        if before_copy:
            before_copy()
        if force_new and any(_same(library.version(owner, item['assetId'], item['versionId']), root)
                             for item in personal_copies(library, owner, root)) and not confirm_duplicate:
            fail('DUPLICATE_CONFIRMATION_REQUIRED', '你的库中已有相同内容，确认后可另存独立副本。', 409)

        def visit(version):
            if stopped and stopped.is_set():
                fail('CANCELLED', '添加已取消。', 409)
            asset = library.asset(PUBLIC, version['assetId'])
            if version['manifest']['kind'] not in {'skill', 'tool'}:
                fail('INVALID_REQUEST', '仅支持添加官方 Skill 或 Tool。')
            if asset.get('lifecycle') != 'active' or asset.get('deletedAt'):
                fail('CAPABILITY_BLOCKED', '该官方能力已下架。', 409)
            manifest = version['manifest']
            identity = (manifest['source']['identity'], version['contentDigest'])
            if identity in visiting:
                fail('SHARING_DEPENDENCY_CYCLE', '官方能力依赖存在循环。', 409)
            if identity in resolved:
                return resolved[identity]
            if len(resolved) + len(visiting) >= 16:
                fail('LIMIT_EXCEEDED', '官方能力依赖闭包超过16项。', 413)
            visiting.add(identity)
            for dependency in manifest['dependencies']:
                candidate = next((item for item in public_versions
                                  if item['manifest']['kind'] == 'tool'
                                  and item['manifest']['source']['identity'] == dependency['sourceIdentity']
                                  and item['contentDigest'] == dependency['contentDigest']), None)
                if candidate is None:
                    fail('MISSING_DEPENDENCY', '平台缺少官方能力的精确依赖版本。', 409,
                         {'sourceIdentity': dependency['sourceIdentity'], 'contentDigest': dependency['contentDigest']})
                visit(candidate)
            is_root = version['versionId'] == root['versionId']
            existing = [item for item in personal_copies(library, owner, version)
                        if library.asset(owner, item['assetId']).get('lifecycle') == 'active'
                        and _same(library.version(owner, item['assetId'], item['versionId']), version)]
            if is_root and (target or force_new):
                result = {**copy_version(library, owner, version, target=target,
                                         force_new=force_new, _in_transaction=True), 'alreadyOwned': False}
            elif existing:
                chosen = existing[0]
                personal = _trust(library, owner, library.version(owner, chosen['assetId'], chosen['versionId']), version)
                result = {'assetId': chosen['assetId'], 'versionId': chosen['versionId'],
                          'readiness': personal['readiness'], 'issues': personal['issues'],
                          'admission': personal['admission'], 'status': 'completed', 'alreadyOwned': True}
            else:
                result = {**copy_version(library, owner, version, force_new=True, _in_transaction=True),
                          'alreadyOwned': False}
            visiting.remove(identity)
            resolved[identity] = result
            if not is_root:
                copied.append({'assetId': result['assetId'], 'versionId': result['versionId'],
                               'sourceIdentity': identity[0], 'contentDigest': identity[1],
                               'alreadyOwned': result['alreadyOwned']})
            return result

        result = visit(root)
        return {**result, 'dependencies': copied}


def prepare(library, owner, public_version, stopped, personal=None, before_copy=None,
            target=None, force_new=False, confirm_duplicate=False):
    """Compatibility entrypoint: preparation now means pure install only."""
    if personal:
        local = library.version(owner, personal['assetId'], personal['versionId'])
        if local['contentDigest'] != public_version['contentDigest']:
            fail('INVALID_REQUEST', '个人内容已修改，请从公共库选择官方版本。', 409)
        asset = library.asset(owner, personal['assetId'])
        if asset['lifecycle'] == 'archived' or asset.get('deletedAt'):
            fail('CAPABILITY_BLOCKED', '此个人能力已归档，请先主动恢复。', 409)
    return copy_closure(library, owner, public_version, target=target or
                        (personal['assetId'] if personal else None), force_new=force_new,
                        confirm_duplicate=confirm_duplicate, before_copy=before_copy, stopped=stopped)
