"""Preview successful work as editable assets without saving or executing it."""
import ast

from ..capability_library.contracts import fail, uid


def _skill_dependencies(service, owner, run):
    """Resolve successful use only within the run's exact capability bindings."""
    library = service.w.capabilities
    refs = service._refs(owner, run.get('loadedRefs') or run['payload'].get('capabilityRefs', []))
    tools, skills = {}, {}
    for ref in refs:
        version = library.version(owner, ref['assetId'], ref['versionId'])
        if version['manifest']['kind'] == 'tool':
            tools.setdefault(version['versionId'], {'version': version, 'skills': set()})
        else:
            skills[version['assetId']] = version
    skill_tools = {}
    for asset_id, version in skills.items():
        declared = version['manifest'].get('dependencies', [])
        if any(item.get('required') is not True for item in declared):
            fail('SOURCE_NEEDS_EDIT', '来源方法含当前格式不支持的可选依赖，请在外部整理。', 422)
        locked = []
        for lock in version.get('dependencyLocks', []):
            dependency = library.version(owner, lock['assetId'], lock['versionId'])
            manifest = dependency['manifest']
            if manifest['kind'] != 'tool':
                fail('SOURCE_NEEDS_EDIT', '当前沉淀格式不支持嵌套Skill依赖，请在外部整理方法与资源。', 422)
            identity = (manifest['source']['identity'], dependency['contentDigest'])
            if not any((item['sourceIdentity'], item['contentDigest']) == identity for item in declared):
                fail('SOURCE_NEEDS_EDIT', '来源方法的依赖锁与声明不一致，不能猜测版本。', 422)
            locked.append(dependency)
            tools.setdefault(dependency['versionId'], {'version': dependency, 'skills': set()})['skills'].add(asset_id)
        if {(item['sourceIdentity'], item['contentDigest']) for item in declared} != {
                (item['manifest']['source']['identity'], item['contentDigest']) for item in locked}:
            fail('SOURCE_NEEDS_EDIT', '来源方法缺少精确依赖锁，请先在外部整理。', 422)
        skill_tools[asset_id] = locked
    used_tools, used_skills = {}, set()
    for step in run['steps']:
        if step.get('status') != 'succeeded' or step.get('type') != 'tool':
            continue
        name = step.get('name') or ''
        if name.startswith('cap_'):
            bound = tools.get(name[4:])
            if not bound:
                fail('SOURCE_NEEDS_EDIT', '成功调用缺少本任务的精确能力绑定，不能按名称补找。', 422)
            used_tools[name[4:]] = bound['version']
            used_skills.update(bound['skills'])
        elif name in {'library_resource', 'library_script'}:
            asset_id = (step.get('arguments') or {}).get('assetId')
            if asset_id not in skills:
                fail('SOURCE_NEEDS_EDIT', '来源Skill缺少本任务的精确版本，不能自动沉淀。', 422)
            if name == 'library_script':
                fail('SOURCE_NEEDS_EDIT', '该方法实际执行了Skill脚本，不能只保留步骤说明；请下载并整理脚本与资源。', 422)
            used_skills.add(asset_id)
    for asset_id in used_skills:
        for version in skill_tools[asset_id]:
            used_tools[version['versionId']] = version
    dependencies, names, identities = [], [], {}
    for version in used_tools.values():
        manifest = version['manifest']
        identity, content = manifest['source']['identity'], version['contentDigest']
        binding = (content, version['configurationId'])
        if identity in identities:
            if identities[identity] != binding:
                fail('SOURCE_NEEDS_EDIT', '成功步骤使用了同一来源的不同内容或配置，单个Skill依赖不能保留这些绑定；请在外部整理。', 422)
            continue
        identities[identity] = binding
        dependencies.append({'sourceIdentity': identity, 'contentDigest': content, 'required': True,
                             'reason': '本任务成功调用的精确工具，或该来源方法声明的必需工具。'})
        names.append(manifest['name'])
    return dependencies, names


def _step_options(service, owner, run, steps):
    options = []
    for index, step in enumerate(steps):
        inputs = []
        for position, ref in enumerate(dict.fromkeys(step.get('inputRefs', []))):
            meta = next((item for item in run['artifacts'] if item['id'] == ref), None)
            if meta is None:
                try:
                    meta, _ = service.w.sub_agents.store.artifact(owner, ref)
                except Exception as error:
                    if getattr(error, 'status', None) != 404:
                        raise
                    meta = {}
            origin = next((item for item in run['steps'] if item['id'] == meta.get('stepId')), {})
            source_type = origin.get('type', 'upload' if meta.get('stepId') == 'upload' else 'unknown')
            if meta.get('runId') and meta['runId'] != run['id']:
                source_type = 'previous_run'
            inputs.append({'parameter': 'input_' + str(position + 1), 'label': meta.get('name') or f'输入文件 {position + 1}',
                           'sourceType': source_type, 'sourceLabel': origin.get('purpose') or origin.get('label') or
                           ('前一任务产物，须重新准备' if source_type == 'previous_run' else '本次需重新授权的文件')})
        options.append({'stepId': step['id'], 'label': f'成功代码步骤 {index + 1}',
                        'purpose': step.get('purpose') or step.get('label') or '执行计算',
                        'inputCount': len(inputs), 'inputs': inputs})
    return options


def preview(service, owner, body):
    run = service.w.sub_agents.store.run(owner, body.get('runId', ''))
    if run['status'] != 'succeeded':
        fail('SOURCE_NOT_SUCCEEDED', '仅可从成功任务沉淀；失败过程可在任务详情中检查。', 409)
    kind = body.get('kind')
    delegation = run.get('delegation') or {}
    name = str(delegation.get('goal') or run['message']).splitlines()[0][:80]
    constraints = [str(item)[:2000] for item in delegation.get('constraints', [])][:20]
    warnings = ['这是可编辑草稿。请检查参数、方法和私密信息；原任务成功不等于新资产已经验证。',
                '不复制原始输入、数据文件、结果或连接凭据；再次使用时重新授权。']
    if kind == 'recipe':
        refs = service._refs(owner, run.get('loadedRefs') or run['payload'].get('capabilityRefs', []))
        return {'kind': kind, 'recipe': {'name': name, 'description': name,
                'goalTemplate': delegation.get('goal') or run['message'], 'constraints': constraints,
                'capabilityRefs': refs, 'sourceRunId': run['id']},
                'warnings': warnings + ['将需要变化的证券、区间等替换为{{参数名}}，运行时填写新值。']}
    if kind not in {'skill', 'tool'}:
        fail('INVALID_REQUEST', '请选择应用、Skill或Tool。')
    identity = 'user/captured-' + uid('method')
    manifest = {'format': 'cd80.capability/1', 'kind': kind, 'name': name, 'description': name,
                'source': {'identity': identity, 'format': 'platform'}, 'files': [], 'dependencies': [],
                'permissions': {'network': 'none', 'externalEffects': 'none', 'inputFiles': False, 'outputFiles': False}}
    files = []
    selection = {}
    if kind == 'skill':
        manifest['dependencies'], dependency_names = _skill_dependencies(service, owner, run)
        purposes = [s.get('purpose') or s.get('label') for s in run['steps']
                    if s['status'] == 'succeeded' and s.get('type') in {'tool', 'code'}]
        purposes = [p for p in purposes if p][:20]
        content = ('---\nname: captured-method\ndescription: Reusable method from a reviewed task.\n---\n\n'
                   '# 方法目标\n\n' + str(delegation.get('goal') or run['message']) + '\n\n'
                   '## 每次使用\n\n确认本次证券、区间、输入和数据范围；重新获取并核查实际数据，不复用旧结论。\n\n'
                   + '\n'.join(f'{index + 1}. {purpose}' for index, purpose in enumerate(purposes))
                   + '\n\n## 约束\n\n' + '\n'.join('- ' + c for c in constraints)
                   + '\n\n## 核验\n\n检查来源、完整性、单位、计算与交付；缺数据应明确停止或询问，不编造结果。\n'
                   + ('\n依赖工具：' + '、'.join(dependency_names) if dependency_names else ''))
        files = [{'path': 'original/SKILL.md', 'text': content}]
        manifest['skill'] = {'entry': 'original/SKILL.md'}
        warnings.append('方法草稿整理成功步骤的目的与约束，不自动复制原Skill的全部正文、参考资料或脚本；请逐项审阅。')
    else:
        steps = [s for s in run['steps'] if s.get('type') == 'code' and s['status'] == 'succeeded' and s.get('result', {}).get('codeArtifactId')]
        options = _step_options(service, owner, run, steps)
        if body.get('stepId'):
            steps = [s for s in steps if s['id'] == body['stepId']]
        if not steps:
            fail('NO_REUSABLE_CODE', '该任务没有成功的计算代码步骤；可保存为方法或研究应用。', 422)
        step = steps[-1]
        selection = {'steps': options, 'selectedStepId': step['id']}
        aid = step['result']['codeArtifactId']
        if aid not in {a['id'] for a in run['artifacts']}:
            fail('INVALID_SOURCE', '源码不属于本次任务。', 409)
        meta, raw = service.w.sub_agents.store.artifact(owner, aid)
        try:
            code = raw.decode('utf-8')
            tree = ast.parse(code)
        except (UnicodeError, SyntaxError):
            fail('INVALID_SOURCE', '实际源码无法解析。', 422)
        if len(raw) > 100000:
            fail('LIMIT_EXCEEDED', '代码过大，请下载后在外部整理。', 413)
        imports_plot = any(isinstance(node, ast.ImportFrom) and node.module == 'cd80_plot' or
                           isinstance(node, ast.Import) and any(alias.name == 'cd80_plot' for alias in node.names)
                           for node in ast.walk(tree))
        # New runs store their executed helper inside the source. Never replace
        # a historical module with today's helper merely because its name matches.
        embedded_plot = any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'exec'
                            and len(node.args) >= 2 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)
                            and isinstance(node.args[1], ast.Attribute) and node.args[1].attr == '__dict__'
                            and isinstance(node.args[1].value, ast.Name) and node.args[1].value.id == '_cd80_plot'
                            for node in ast.walk(tree))
        if imports_plot and not embedded_plot:
            fail('SOURCE_NEEDS_EDIT', '旧源码未保存实际绘图模块，不能用当前版本替代；请下载并在外部整理精确依赖。', 422)
        input_refs = list(dict.fromkeys(step.get('inputRefs', [])))
        aliases = {ref: 'input_' + str(index + 1) for index, ref in enumerate(input_refs)}
        class Parameterize(ast.NodeTransformer):
            def visit_Constant(self, node):
                if isinstance(node.value, str):
                    if node.value in aliases:
                        return ast.copy_location(ast.Constant(aliases[node.value]), node)
                    if any(ref in node.value for ref in aliases):
                        fail('SOURCE_NEEDS_EDIT', '代码内含固定工件路径，请下载并改为payload.files参数后导入。', 422)
                return node
        source = ast.unparse(Parameterize().visit(tree)) + '\n'
        props = {alias: {'type': 'string', 'title': f'输入文件 {index + 1}',
                         'description': '选择本次授权文件的/input/路径，不沿用旧任务文件。'}
                 for index, alias in enumerate(aliases.values())}
        wrapper = ('from source import run as _execute\n\ndef run(payload):\n'
                   f'    names = {list(aliases.values())!r}\n'
                   '    files = {}\n    for name in names:\n'
                   '        path = payload[name]\n'
                   "        if not isinstance(path, str) or not path.startswith('/input/') or '..' in path or '\\\\' in path:\n"
                   "            raise ValueError('Select a currently authorized input file')\n"
                   '        files[name] = path\n'
                   "    return _execute({'files': files})\n")
        files = [{'path': 'original/tool.py', 'text': wrapper}, {'path': 'original/source.py', 'text': source}]
        manifest['tool'] = {'inputSchema': {'type': 'object', 'properties': props, 'required': list(props), 'additionalProperties': False},
                            'execution': {'type': 'python_wasi', 'path': 'original/tool.py', 'callable': 'run'}}
        manifest['permissions'].update(inputFiles=bool(aliases), outputFiles=True)
        warnings.append('保留实际成功算法，工件标识已改为输入参数；请检查代码内固定的业务常量，并用不同输入重新验证。')
        warnings.append('仅沉淀所选的一个成功代码步骤，不自动组合其他代码、前置取数或中间处理。来自工具或前一步的输入须重新准备同格式文件；不携带旧结果。')
        if len(options) > 1:
            warnings.append('本任务有多个成功代码步骤；默认选择最后一个，请核对步骤用途和输入，也可切换后重新预览。')
    return {'kind': kind, 'draft': {'kind': kind, 'name': name, 'description': name, 'manifest': manifest, 'files': files}, 'warnings': warnings, **selection}
