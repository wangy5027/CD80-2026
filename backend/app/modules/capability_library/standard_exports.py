"""Read-only, self-contained standard downloads for capability detail views."""
import copy
import json
from pathlib import PurePosixPath

from . import community
from .components import _files as snapshot_files
from .components import export_snapshot, snapshot_from_package, standard_skill_files
from .contracts import CapabilityError, canonical, digest, fail
from .packages import MAX_PACKAGE, archive_files, file_index, has_secret, parse_package, safe_path, zip_bytes
from .service import PUBLIC
from .sharing import assert_shareable
from .test_cases import prepare_test_cases, version_test_cases
from .usage_guides import usage_examples


def _owner_snapshot(library, source_owner, asset_id, version_id):
    """Capture the selected exact version and its locked dependencies only."""
    active = set()

    def capture(aid, vid):
        if (aid, vid) in active:
            fail('INVALID_SNAPSHOT', '能力依赖存在循环，无法完整导出。', 422)
        version = library.version(source_owner, aid, vid)
        if version.get('embeddedSnapshot'):
            snapshot = export_snapshot(version['embeddedSnapshot'])
            if (snapshot['manifest'] != version['manifest'] or
                    snapshot['contentDigest'] != version['contentDigest']):
                fail('INVALID_SNAPSHOT', '保存的组件快照与所选版本不一致。', 422)
            return snapshot
        active.add((aid, vid))
        try:
            manifest = version['manifest']
            locks = version.get('dependencyLocks', [])
            children = []
            for declared in manifest['dependencies']:
                matches = []
                for lock in locks:
                    candidate = library.version(source_owner, lock['assetId'], lock['versionId'])
                    identity = candidate['manifest']['source']['identity']
                    if identity == declared['sourceIdentity'] and candidate['contentDigest'] == declared['contentDigest']:
                        matches.append(lock)
                if not matches and source_owner == PUBLIC:
                    # Official seed versions may predate persisted locks. The
                    # public catalogue resolves their declared exact content.
                    matches = [
                        {'assetId': item['assetId'], 'versionId': item['versionId']}
                        for item in library.store.list(PUBLIC, 'version')
                        if item['manifest']['kind'] == 'tool'
                        and item['manifest']['source']['identity'] == declared['sourceIdentity']
                        and item['contentDigest'] == declared['contentDigest']
                    ][:1]
                if len(matches) != 1:
                    fail('MISSING_DEPENDENCY', '所选版本的精确依赖尚未完整保存，无法完整导出。', 409)
                children.append(capture(matches[0]['assetId'], matches[0]['versionId']))
            snapshot = snapshot_from_package(manifest, library.files(source_owner, version), children)
            if snapshot['contentDigest'] != version['contentDigest']:
                fail('INVALID_SNAPSHOT', '所选版本源码与内容摘要不一致。', 422)
            return snapshot
        finally:
            active.remove((aid, vid))

    return export_snapshot(capture(asset_id, version_id))


def _released_snapshot(library, record):
    """Reconstruct only immutable published blobs, never publisher-owned state."""
    nodes = {(
        node['manifest']['source']['identity'], node['contentDigest']): node
        for node in [*record['snapshot']['dependencies'], record['snapshot']['root']]}
    active = set()

    def capture(node):
        key = (node['manifest']['source']['identity'], node['contentDigest'])
        if key in active:
            fail('INVALID_SNAPSHOT', '公开版本依赖存在循环。', 422)
        raw = library.store.blob(community.COMMUNITY, node['packageDigest'])
        if digest(raw) != node['packageDigest']:
            fail('INVALID_PACKAGE', '公开版本源码摘要不一致。', 409)
        manifest, files = parse_package(raw, 'release.zip')
        if manifest != node['manifest']:
            fail('INVALID_PACKAGE', '公开版本声明与冻结源码不一致。', 409)
        assert_shareable(manifest, files)
        active.add(key)
        try:
            children = []
            for declared in manifest['dependencies']:
                child = nodes.get((declared['sourceIdentity'], declared['contentDigest']))
                if child is None:
                    fail('MISSING_DEPENDENCY', '公开版本缺少冻结的精确依赖。', 409)
                children.append(capture(child))
            snapshot = snapshot_from_package(manifest, files, children)
            if snapshot['contentDigest'] != node['contentDigest']:
                fail('INVALID_PACKAGE', '公开版本内容摘要不一致。', 409)
            return snapshot
        finally:
            active.remove(key)

    root = record['snapshot']['root']
    if root.get('componentSnapshot'):
        snapshot = export_snapshot(root['componentSnapshot'])
        if snapshot['manifest'] != root['manifest'] or snapshot['contentDigest'] != root['contentDigest']:
            fail('INVALID_SNAPSHOT', '公开组件快照与发布源码不一致。', 409)
        # The raw release package is still checked, even when the portable
        # closure is already frozen beside it.
        raw = library.store.blob(community.COMMUNITY, root['packageDigest'])
        manifest, files = parse_package(raw, 'release.zip')
        if (digest(raw) != root['packageDigest'] or manifest != snapshot['manifest'] or
                file_index(files) != file_index(snapshot_files(snapshot))):
            fail('INVALID_PACKAGE', '公开组件源码与发布快照不一致。', 409)
        return snapshot
    return export_snapshot(capture(root))


def _all_nodes(snapshot):
    nodes = []
    seen = set()

    def visit(node):
        identity = (node['manifest']['source']['identity'], node['contentDigest'])
        if identity in seen:
            return
        for dependency in node['dependencies']:
            visit(dependency)
        seen.add(identity)
        nodes.append(node)

    visit(snapshot)
    return nodes


def _check_source(manifest, files, *, public_release=False):
    """Never read server credentials; keep ordinary binary owner resources."""
    if public_release:
        # Publication already enforced this stronger, text-only contract.
        assert_shareable(manifest, files)
        return
    assert_shareable(manifest)
    if has_secret(files):
        fail('EXPORT_CONTAINS_SECRET', '源码疑似包含凭据，不能生成标准分享包。', 403)
    forbidden_parts = {'.env', '.aws', '.ssh', 'credentials', 'credentials.json',
                       'credential.json', 'secrets.json', 'id_rsa', 'id_ed25519'}
    forbidden_suffixes = {'.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3'}
    for path, raw in files.items():
        safe_path(path)
        file_path = PurePosixPath(path)
        parts = [part.casefold() for part in file_path.parts]
        if (any(part in forbidden_parts or part.startswith('.env.') for part in parts) or
                file_path.suffix.casefold() in forbidden_suffixes):
            fail('EXPORT_CONTAINS_SECRET', '源码包含凭据或私人数据库文件，不能生成标准分享包。', 403)
        try:
            text = raw.decode('utf-8')
        except UnicodeError:
            continue
        if file_path.suffix.casefold() in {
                '.py', '.md', '.txt', '.json', '.csv', '.tsv', '.yaml', '.yml', '.toml',
                '.rst', '.sql', '.html', '.css', '.xml', '.svg'}:
            assert_shareable({}, {path: raw})
        else:
            assert_shareable({'text': text})


def _indented_json(value):
    return '\n'.join('    ' + line for line in json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).splitlines())


def _indented_text(value):
    # Indented Markdown code blocks cannot be prematurely closed by source
    # text containing backticks or tildes.
    return '\n'.join('    ' + line for line in value.split('\n'))


def _readme(*, display_name, source, manifest, snapshot, version, release, examples, cases,
            standard_skill_note, source_index, source_files):
    kind_label = 'Skill' if manifest['kind'] == 'skill' else 'Tool'
    source_label = {'personal': '我的能力池', 'official': '平台官方能力', 'community': '其他用户公开发布'}[source]
    state = ('公开发布' if release else version.get('readiness', '未标记'))
    number = (release.get('releaseNumber') if release else version.get('number'))
    lines = [f'# {display_name}', '',
             f'- 类别：{kind_label}', f'- 来源：{source_label}',
             f'- 原始能力名称：{manifest["name"]}', f'- 精确版本：{number}',
             f'- 来源身份：{manifest["source"]["identity"]}',
             f'- 内容摘要：{snapshot["contentDigest"]}',
             f'- 快照摘要：{snapshot["snapshotDigest"]}',
             f'- 导出时状态：{state}', '',
             '此 ZIP 可直接作为完整组件上传至成都八零技能池。导入只保存源码和独立预期，'
             '不继承原账号的文件授权、连接、验证证据或启用状态；请重新检查运行条件。', '',
             '## 完整说明', '', manifest['description'], '']
    if release:
        lines.extend([
            '公开发布说明：', '', release['description'], '',
            f'公开发布时间：{release["publishedAt"]}', ''])
    lines.extend(['## 调用与运行条件', ''])
    if manifest['kind'] == 'skill':
        lines.extend([f'- 标准入口：`{manifest["skill"]["entry"]}`',
                      '- 方法正文及资源见 `standard-skill/`（如可无损转换），以及 `implementation/source/`。'])
    else:
        lines.extend(['- 工具接口定义见 `standard-tool/tool.json`；该文件只描述调用参数，不能单独执行。',
                      '- 真正的执行声明、源码和依赖见完整清单及 `implementation/source/`。'])
    lines.extend(['- 实际运行取决于目标平台支持的执行环境、连接、权限和依赖。', '',
                  '## 输入与输出契约', '',
                  _indented_json(manifest['tool'].get('inputSchema', {}) if manifest['kind'] == 'tool' else
                                 {'skillEntry': manifest['skill']['entry']}), ''])
    if manifest['kind'] == 'tool':
        lines.extend(['输出结构：', '', _indented_json(manifest['tool'].get('outputSchema', {})), ''])
    lines.extend(['## 权限与依赖', '', _indented_json({
        'permissions': manifest['permissions'], 'dependencies': manifest['dependencies']}), '',
        '## 使用示例', '', _indented_json(examples), '',
        '## 原用例与独立预期', '',
        '独立预期来自所选精确版本；账号文件授权已清空。公开版本仅包含发布时声明的公开示例，'
        '不包含发布者的私人测试记录。' if source == 'community' else
        '以下用例随包保存；账号文件授权已清空。未填写预期的候选不能视为已验证。', '',
        _indented_json(cases) if cases else '该公开版本未附可导出的原用例。', '',
        '## 标准文件与完整清单', '',
        '根目录 `component.json` 是 `cd80.component/1` 自足实现；'
        '`test-cases.json`（存在时）只保存用例；`standard-export.json` 校验附属文件。',
        '所有原始路径及字节保留在组件快照内，同时以 `implementation/source/` 下的独立文件提供阅读。',
        standard_skill_note, '',
        _indented_json(source_index), '',
        '## 源文件正文', '',
        '以下逐项保留可阅读 UTF-8 原文；二进制资源按上方路径、字节数和 SHA-256 在包中提供。', ''])
    for entry in source_index:
        path = entry['archivePath']
        raw = source_files[path]
        lines.extend([f'### `{path}`', ''])
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeError:
            lines.extend(['二进制资源：请读取包内同名文件。', ''])
            continue
        if any(ord(char) < 32 and char not in '\t\n\r' for char in text):
            lines.extend(['非纯文本资源：请读取包内同名文件。', ''])
            continue
        lines.extend([_indented_text(text), ''])
    lines.extend([
        '完整清单：', '', _indented_json(manifest), ''])
    return '\n'.join(lines).encode('utf-8')


def export_standard(library, owner, kind, source, asset_id, version_id=''):
    """Return the visible name and a directly importable, bounded ZIP; no writes."""
    if kind not in {'skill', 'tool'} or source not in {'personal', 'official', 'community'}:
        fail('INVALID_REQUEST', '标准导出类别或来源无效。')
    if not isinstance(asset_id, str) or not asset_id or not isinstance(version_id, str):
        fail('INVALID_REQUEST', '请选择精确能力版本。')
    if source != 'community' and not version_id:
        fail('INVALID_REQUEST', '请选择精确能力版本。')
    library.workbench.auth.get_user(owner)
    with library.store.lock:
        if source == 'community':
            record = community.get(library, owner, asset_id, copying=True)
            if version_id and version_id != record['releaseId']:
                fail('INVALID_REQUEST', '公开版本标识与所选发布不一致。')
            snapshot = _released_snapshot(library, record)
            manifest = snapshot['manifest']
            display_name = record['title']
            version = asset = None
            examples = copy.deepcopy(manifest.get('examples', []))
            cases = prepare_test_cases(manifest, snapshot_files(snapshot)) if examples else []
        else:
            source_owner = owner if source == 'personal' else PUBLIC
            asset = library.asset(source_owner, asset_id)
            version = library.version(source_owner, asset_id, version_id)
            snapshot = _owner_snapshot(library, source_owner, asset_id, version_id)
            manifest = snapshot['manifest']
            display_name = asset.get('displayName') or asset['name']
            record = None
            examples = [{key: value for key, value in item.items() if key != 'versionId'}
                        for item in usage_examples(version)]
            cases = version_test_cases(library, source_owner, version)
            for case in cases:
                case['fileIds'] = []
        if manifest['kind'] != kind:
            fail('INVALID_REQUEST', '所选能力类别与版本不一致。')
        nodes = _all_nodes(snapshot)
        source_files = {}
        source_index = []
        for index, node in enumerate(nodes):
            raw_files = snapshot_files(node)
            _check_source(node['manifest'], raw_files, public_release=source == 'community')
            label = 'root' if node is nodes[-1] else f'dependency-{index + 1}'
            for path, content in sorted(raw_files.items()):
                output_path = f'implementation/source/{label}/{path}'
                source_files[output_path] = content
                source_index.append({'component': label, 'sourceIdentity': node['manifest']['source']['identity'],
                                     'path': path, 'archivePath': output_path,
                                     'sha256': digest(content), 'size': len(content)})
        if cases:
            assert_shareable(cases)
        supplementary = {}
        standard_skill_note = '平台完整组件和源文件保留所有资源。'
        if kind == 'skill':
            try:
                standard = standard_skill_files(manifest, snapshot_files(snapshot))
                supplementary.update({f'standard-skill/{path}': data for path, data in standard.items()})
                standard_skill_note = '纯 Agent Skills 目录位于 `standard-skill/`，入口为 `standard-skill/SKILL.md`。'
            except CapabilityError as error:
                if error.code != 'UNSUPPORTED_EXPORT':
                    raise
                standard_skill_note = ('本版本不能无损转换为纯 Agent Skills 子目录：' + str(error) +
                                       '。完整源码和平台自足组件仍已保留。')
        else:
            tool = manifest['tool']
            wire_name = 'cap_' + snapshot['contentDigest'][:24]
            supplementary['standard-tool/tool.json'] = canonical({
                'name': wire_name, 'description': manifest['description'],
                'input_schema': tool['inputSchema']}).encode('utf-8')
            if 'outputSchema' in tool:
                supplementary['standard-tool/output-schema.json'] = canonical(tool['outputSchema']).encode('utf-8')
        content = {
            'component.json': canonical(snapshot).encode('utf-8'),
            **source_files, **supplementary,
        }
        if cases:
            content['test-cases.json'] = canonical(cases).encode('utf-8')
        content['readme.md'] = _readme(
            display_name=display_name, source=source, manifest=manifest, snapshot=snapshot,
            version=version, release=record, examples=examples, cases=cases,
            standard_skill_note=standard_skill_note, source_index=source_index,
            source_files=source_files)
        assert_shareable({'displayName': display_name, 'readme': content['readme.md'].decode('utf-8')})
        content['standard-export.json'] = canonical({
            'format': 'cd80.standard-export/1', 'files': file_index(content)}).encode('utf-8')
        package = zip_bytes(content)
        if len(package) > MAX_PACKAGE:
            fail('LIMIT_EXCEEDED', '标准导出包超过20 MiB上限，无法完整导出。', 413)
        archive_files(package, component_entry=True)
        return display_name, package
