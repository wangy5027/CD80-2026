"""Read-only, source-aware ZIP exports for every App directory card.

Only a modern cd80.app-package/2 is a self-contained App import. Historical
workspaces, strategy rules and infinity-application records retain their own
formats and explicitly describe their host dependencies in readme.md.
"""

import base64
import hashlib
import io
import json
import re
import zipfile

from ..capability_library.contracts import fail
from ..capability_library.packages import safe_path
from .app_community import AppCommunity


WORKSPACES = {
    'explore': {
        'name': 'Explore',
        'description': '理解事件、发现关联、对照历史，保留研究过程与来源。',
        'workflow': '提出研究问题 → 查看证据与关联 → 保存研究结果',
        'inputs': '研究问题、用户授权的材料与可用数据',
        'outputs': '带来源的研究过程与结果',
    },
    'strategy-workshop': {
        'name': '策略工坊',
        'description': '把投资想法整理为策略，诊断回测并保存复用。',
        'workflow': '说明想法 → 对齐规则 → 历史数据诊断 → 查看结果',
        'inputs': '策略想法、标的、日期与初始资金',
        'outputs': '规则、回测诊断与个人策略应用',
    },
    'app-workshop': {
        'name': '应用工坊',
        'description': '按金融需求推荐和组合应用，保存个人应用。',
        'workflow': '说明需求 → 查看推荐 → 组合能力 → 保存应用',
        'inputs': '金融任务需求与可用应用',
        'outputs': '推荐结果与个人应用',
    },
}

SOURCE_LABELS = {
    'builtin': '平台内置', 'asset': '个人 APP', 'community-app': '公开 APP',
    'strategy': '个人策略', 'legacy-pipeline': '旧版流程', 'preset': '平台预置',
    'legacy-recipe': '旧版研究', 'legacy-publication': '旧版共创',
}


def _json(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')


def _pretty(value):
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _text(value):
    return value if isinstance(value, str) else ''


def _fence(body, language='text'):
    """A source file may itself contain Markdown fences; keep it readable."""
    longest = max((len(run) for run in re.findall(r'`+', body)), default=0)
    marker = '`' * max(3, longest + 1)
    return marker + language + '\n' + body + ('' if body.endswith('\n') else '\n') + marker + '\n'


def _block(title, value):
    return '\n## ' + title + '\n\n' + _fence(_pretty(value), 'json')


def _readme_header(name, source, version, description, portability):
    return (f'# {name}\n\n'
            f'- 类别：APP\n- 来源：{SOURCE_LABELS[source]}\n'
            f'- 版本：{version or "此来源未单独编号"}\n'
            f'- 可移植性：{portability}\n\n'
            f'## 用途说明\n\n{description or "作者尚未填写用途说明。"}\n')


def _zip(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(entries):
            safe_path(name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, entries[name])
    return output.getvalue()


def _version(requested, actual):
    if requested and requested != str(actual or ''):
        fail('VERSION_MISMATCH', '所选 APP 版本已变化，请刷新详情后重试。', 409)


def _resource_details(component, label):
    parts = [_block(label + ' 协议', component.get('manifest', {}))]
    for path, encoded in sorted((component.get('files') or {}).items()):
        content = base64.b64decode(encoded, validate=True)
        parts.append(f'\n### {label} / {path}\n\n'
                     f'大小：{len(content)} 字节；SHA-256：{hashlib.sha256(content).hexdigest()}。'
                     '完整原件保留在 app.json 的组件快照 files 中。\n')
        try:
            body = content.decode('utf-8')
        except UnicodeError:
            continue
        parts.append('\n' + _fence(body))
    for index, dependency in enumerate(component.get('dependencies') or [], 1):
        manifest = dependency.get('manifest') or {}
        identity = (manifest.get('source') or {}).get('identity') or '来源未标记'
        child = f'{label} / 依赖 {index}：{manifest.get("name") or "未命名组件"} [{identity}]'
        parts.append(_resource_details(dependency, child))
    return ''.join(parts)


def _modern_readme(name, source, version, description, doc, status):
    parts = [_readme_header(name, source, version, description,
                            'app.json 是 cd80.app-package/2 完整定义，可重新导入；接收者仍需自行授权、验证与启用。')]
    parts.extend([
        '\n## 调用方式\n\n将输入按下述 inputSchema 交给应用，按节点流程执行，并按 outputs 收集结果。'
        '此 ZIP 不继承原账号的文件授权、连接凭据、验证或启用状态。\n',
        _block('输入协议', doc.get('inputSchema', {})),
        _block('输出映射', doc.get('outputs', {})),
        _block('固定规则', doc.get('rules', [])),
        _block('待确认范围', doc.get('gaps', [])),
        f'\n## 当前状态\n\n{status}\n',
        _block('原任务用例与独立预期', doc.get('testCases', [])),
    ])
    for index, node in enumerate(doc.get('nodes', []), 1):
        title = _text(node.get('title')) or _text(node.get('id')) or f'步骤 {index}'
        parts.append(f'\n## 步骤 {index}：{title}\n\n类型：{node.get("kind", "未声明")}。\n')
        parts.append(_block('完整步骤契约、调用与交付', {key: value for key, value in node.items()
                    if key not in {'code', 'files', 'component'}}))
        if 'code' in node:
            parts.append('\n### Python 源码\n\n' + _fence(node['code'], 'python'))
        for resource in node.get('files', []):
            content = base64.b64decode(resource['base64'], validate=True)
            parts.append(f'\n### 资源：{resource["path"]}\n\n大小：{len(content)} 字节；'
                         f'SHA-256：{hashlib.sha256(content).hexdigest()}。'
                         '完整字节保留在 app.json 的 files 中。\n')
            try:
                parts.append('\n' + _fence(content.decode('utf-8')))
            except UnicodeError:
                pass
        if node.get('component'):
            parts.append(_resource_details(node['component'], '组件快照'))
    return ''.join(parts)


def _legacy_readme(name, source, version, description, package, *, recipe=False):
    definition = package.get('definition', {})
    parts = [_readme_header(name, source, version, description,
                            'application.json 保留旧版 infinity-application/1 定义。它依赖本平台旧版执行器；'
                            '依赖列表只有身份与摘要，不包含依赖能力的源码，不能直接作为现代 APP 导入。')]
    parts.append('\n## 调用方式与运行条件\n\n')
    parts.append('通过平台旧版研究应用入口填写模板参数，并重新绑定依赖能力。\n' if recipe else
                 '通过平台旧版流程入口调用；重新绑定依赖能力并核对原有平台执行条件。\n')
    if recipe:
        parts.append('\n## 工作流程\n\n' + _text(definition.get('goalTemplate')) + '\n')
        parts.append(_block('固定约束', definition.get('constraints', [])))
    else:
        parts.append(_block('流程定义与输入输出', definition))
    parts.append(_block('依赖声明', package.get('dependencies', [])))
    parts.append(_block('完整旧版应用原件', package))
    return ''.join(parts)


def _strategy_export(workbench, owner, asset_id, version_id):
    detail = workbench.workshop.personal_app(owner, asset_id)
    app = detail['app']
    _version(version_id, app.get('versionId'))
    name = app['name']
    definition = detail.get('definition')
    run = detail.get('run')
    status = (app.get('verification') or {}).get('status', 'unverified')
    parts = [_readme_header(name, 'strategy', app.get('versionId') or app.get('runId'),
                            app.get('description', ''),
                            'strategy.json 是策略工坊的规则与诊断记录，不是 cd80.app/2。'
                            '运行依赖平台策略工坊、历史行情及用户重新确认的日期和资金。')]
    parts.append('\n## 调用方式与运行条件\n\n进入策略详情，审阅冻结规则，再主动修改或验证。'
                 '验证时需重新确认行情、日期、资金和实际执行条件。\n')
    parts.append('\n## 原始想法\n\n' + _text((definition or {}).get('idea') or (run or {}).get('idea')
                                              or app.get('description')) + '\n')
    parts.append('\n## 版本状态\n\n' + ('工程验证与诊断回测通过' if status == 'passed' else
                                    '未验证 · 尚未运行诊断回测') + '\n')
    parts.append(_block('已保存的策略规则', definition or app))
    entries = {'strategy.json': _json({'format': 'cd80.strategy-export/1', 'app': app,
                                       'definition': definition, 'run': run})}
    # A legacy completed App points directly at its execution snapshot. A
    # saved rules App can instead have a later successful diagnostic run; its
    # public detail verifies that run, but the snapshot ID stays private.
    snapshot_id = None
    with workbench.workshop.repository.transaction() as db:
        stored = workbench.workshop.repository.get(db, owner, 'personal_app', asset_id)
        if app.get('executionSnapshotDigest'):
            snapshot_id = stored.get('executionSnapshotId')
        elif isinstance(run, dict) and run.get('status') == 'succeeded' and run.get('id'):
            stored_run = workbench.workshop.repository.get(db, owner, 'run', run['id'])
            if (stored_run.get('savedPersonalAppId') == asset_id
                    and stored_run.get('candidate', {}).get('sourceHash') == app.get('sourceHash')):
                snapshot_id = stored_run.get('executionSnapshotId')
    if snapshot_id:
        snapshot = workbench.workshop.repository.load_snapshot(owner, snapshot_id)
        package = snapshot['package']
        entries['strategy-package.json'] = _json(package)
        parts.append('\n## 冻结执行实现\n\nstrategy-package.json 保留通过完整性检查的执行包。'
                     '执行快照中的原始行情数据不随导出。\n')
        parts.append(_block('实现清单', {key: value for key, value in package.items() if key != 'files'}))
        for path, encoded in sorted((package.get('files') or {}).items()):
            content = base64.b64decode(encoded, validate=True)
            target = 'source/' + safe_path(path)
            if target in entries:
                fail('INVALID_PACKAGE', '冻结策略包出现重复源码路径。', 503)
            entries[target] = content
            parts.append(f'\n### 源码或资源：{target}\n\n')
            try:
                parts.append(_fence(content.decode('utf-8')))
            except UnicodeError:
                parts.append(f'二进制资源，{len(content)} 字节；SHA-256：'
                             f'{hashlib.sha256(content).hexdigest()}。\n')
        custom = package.get('custom')
        if isinstance(custom, dict) and isinstance(custom.get('sourceCode'), str):
            if 'source/custom_strategy.py' in entries:
                fail('INVALID_PACKAGE', '冻结策略包出现重复自定义源码路径。', 503)
            entries['source/custom_strategy.py'] = custom['sourceCode'].encode('utf-8')
            parts.append('\n### 自定义策略源码：source/custom_strategy.py\n\n'
                         + _fence(custom['sourceCode'], 'python'))
    else:
        parts.append('\n## 实现边界\n\n当前版本未存有通过工程诊断的完整执行快照。'
                     '已保存规则如有候选源码，仅以 strategy.json 中的真实内容为准；本 ZIP 不伪造可运行代码。\n')
    entries['readme.md'] = ''.join(parts).encode('utf-8')
    return name, _zip(entries)


def export_standard(workbench_service, owner, source, asset_id, version_id=''):
    """Return the selected card's display name and a read-only standard ZIP.

    `source`, `asset_id`, `version_id` are the exact AppsLibrary card identity.
    Authentication must be resolved by the HTTP caller; this layer enforces
    account-scoped reads and rejects withdrawn community snapshots.
    """
    if source not in SOURCE_LABELS or not isinstance(asset_id, str) or not asset_id:
        fail('INVALID_REQUEST', '请选择有效的 APP。', 400)
    if not isinstance(version_id, str):
        fail('INVALID_REQUEST', 'APP 版本无效。', 400)
    if source == 'builtin':
        data = WORKSPACES.get(asset_id)
        if data is None:
            fail('NOT_FOUND', '平台内置工作区不存在。', 404)
        _version(version_id, '')
        name = data['name']
        readme = _readme_header(name, source, '', data['description'],
                                'workspace.json 仅是平台工作区说明；功能依赖本平台服务、权限和业务数据，'
                                '不能作为独立 cd80.app/2 执行或导入。')
        readme += ('\n## 调用方式与运行条件\n\n登录后进入此工作区；业务流程独立使用。\n'
                   '\n## 工作流程\n\n' + data['workflow'] + '\n'
                   '\n## 输入\n\n' + data['inputs'] + '\n'
                   '\n## 输出\n\n' + data['outputs'] + '\n'
                   '\n## 实现边界\n\n平台工作区是宿主功能，当前没有可单独导入的应用源码。\n')
        return name, _zip({'readme.md': readme.encode('utf-8'),
                           'workspace.json': _json({'format': 'cd80.workspace-reference/1',
                                                    'id': asset_id, **data})})
    if source == 'strategy':
        return _strategy_export(workbench_service, owner, asset_id, version_id)
    if source in {'asset', 'community-app'}:
        if source == 'asset':
            detail = workbench_service.asset_apps.detail(owner, asset_id, version_id or None)
            name, version = detail['name'], detail['versionId']
            package = workbench_service.asset_apps.export(owner, asset_id, version)
            status = (detail.get('verification') or {}).get('status', 'unverified')
        else:
            detail = AppCommunity(workbench_service.asset_apps).detail(owner, asset_id)
            if detail['status'] != 'published':
                fail('NOT_FOUND', '公开应用不存在或已撤回。', 404)
            _version(version_id, detail['sourceVersionId'])
            name, version = detail['title'], detail['sourceVersionId']
            package = detail['package']
            status = '已发布；接收者仍需验证和启用'
        doc = package['definition']
        readme = _modern_readme(name, source, version, detail.get('description') or doc.get('description'),
                                 doc, status)
        return name, _zip({'app.json': _json(package), 'readme.md': readme.encode('utf-8')})
    if source == 'legacy-recipe':
        recipe = workbench_service.asset_apps.evolution.recipe(owner, asset_id)
        _version(version_id, '')
        package = workbench_service.asset_apps.evolution.portable(owner, asset_id)
        name, description = recipe['name'], recipe.get('description', '')
        recipe_kind = True
        version = ''
    elif source == 'legacy-publication':
        evolution = workbench_service.asset_apps.evolution
        # The listing reflects only published records for other users. The
        # package endpoint independently refuses withdrawn releases for all.
        published = next((item for item in evolution.publications(owner) if item['id'] == asset_id
                          and not item['withdrawn']), None)
        if published is None:
            fail('NOT_FOUND', '公开应用不存在或已撤回。', 404)
        _version(version_id, '')
        package = evolution.publication_package(owner, asset_id)
        name, description = published['name'], published.get('description', '')
        recipe_kind = package.get('kind') == 'recipe'
        version = ''
    else:
        platform = workbench_service.platform
        if source == 'preset':
            if asset_id not in platform.presets:
                fail('NOT_FOUND', '平台预置应用不存在。', 404)
        else:
            # platform.agent would fall back to an official preset. A personal
            # card must resolve only against this owner's saved agent records.
            platform.repository.agent(owner, asset_id)
        package = platform.export_application(owner, asset_id)
        spec = package['definition']
        version = spec['version']
        _version(version_id, version)
        name, description = spec['name'], spec.get('description') or spec.get('goal', '')
        recipe_kind = False
    readme = _legacy_readme(name, source, version, description, package, recipe=recipe_kind)
    return name, _zip({'application.json': _json(package), 'readme.md': readme.encode('utf-8')})
