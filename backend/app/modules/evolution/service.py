"""Research recipes store methods, never inherited file grants or old results."""
import copy
import re

from ..capability_library.contracts import canonical, digest, fail, now, uid
from .store import Store

PUBLIC = '__evolution_publications__'
PLACEHOLDER = re.compile(r'\{\{\s*([^{}\n]{1,64}?)\s*\}\}')


def text(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail('INVALID_REQUEST', f'{label}须为非空文本，最多{limit}字符。')
    return value.strip()


def strings(value, label, maximum=20):
    if not isinstance(value, list) or len(value) > maximum:
        fail('INVALID_REQUEST', f'{label}格式无效。')
    return [text(item, label, 2000) for item in value]


def parameters(template):
    names = list(dict.fromkeys(m.group(1).strip() for m in PLACEHOLDER.finditer(template)))
    remainder = PLACEHOLDER.sub('', template)
    if '{{' in remainder or '}}' in remainder or len(names) > 20:
        fail('INVALID_REQUEST', '变量请使用{{名称}}，最多20个；不支持表达式。')
    if any(not re.fullmatch(r'[\w\u4e00-\u9fff][\w\u4e00-\u9fff -]{0,63}', name) for name in names):
        fail('INVALID_REQUEST', '变量名称只能包含文字、数字、空格、短横线或下划线。')
    return names


class Evolution:
    def __init__(self, workbench, config):
        self.w = workbench
        self.store = Store(config['DATA_DIR'])

    def recipe(self, owner, key):
        with self.store.transaction() as db:
            return self.store.get(db, owner, 'recipe', key)

    def recipes(self, owner):
        with self.store.transaction() as db:
            return self.store.list(db, owner, 'recipe')

    def _refs(self, owner, refs):
        if not isinstance(refs, list) or len(refs) > 8:
            fail('INVALID_REQUEST', '最多选择8个能力。')
        result, selected, identities = [], {}, {}
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get('assetId'), str) or not isinstance(ref.get('versionId'), str):
                fail('INVALID_REQUEST', '能力引用格式无效。')
            asset = self.w.capabilities.asset(owner, ref['assetId'])
            version = self.w.capabilities.version(owner, ref['assetId'], ref['versionId'])
            if asset.get('deletedAt') or asset.get('lifecycle') == 'archived':
                fail('CAPABILITY_BLOCKED', '引用的能力已删除或归档。', 409)
            if ref['assetId'] in selected and selected[ref['assetId']] != ref['versionId']:
                fail('DEPENDENCY_CONFLICT', '同一研究应用不能选择同一能力的不同版本。', 409)
            identity = version['manifest']['source']['identity']
            content = (version['contentDigest'], version['manifest']['kind'])
            if identity in identities and identities[identity] != content:
                fail('DEPENDENCY_CONFLICT', '同一来源身份不能绑定不同内容或类型，请明确使用的版本。', 409)
            selected[ref['assetId']], identities[identity] = ref['versionId'], content
            result.append({k: version[k] for k in ('assetId', 'versionId')})
        return list({r['assetId']: r for r in result}.values())

    def _definition(self, owner, body):
        goal = text(body.get('goalTemplate'), '目标模板', 16000)
        name = text(body.get('name'), '名称', 128)
        return {'name': name,
                'description': text(body.get('description') or name, '说明', 4000),
                'goalTemplate': goal, 'parameters': parameters(goal),
                'constraints': strings(body.get('constraints', []), '约束'),
                'capabilityRefs': self._refs(owner, body.get('capabilityRefs', []))}

    def _save(self, db, owner, body, source=None, *, binding_ids=None):
        definition = self._definition(owner, body)
        if binding_ids is None:
            binding_ids = ['capability-' + str(index + 1) for index in range(len(definition['capabilityRefs']))]
        record = {'id': uid('recipe'), 'kind': 'recipe', 'revision': 1, **definition,
                  'capabilityBindingIds': list(binding_ids),
                  'source': source or {}, 'createdAt': now()}
        self.store.put(db, owner, 'recipe', record['id'], record)
        return record

    def create(self, owner, body, key):
        source = {}
        if body.get('sourceRunId'):
            run = self.w.sub_agents.store.run(owner, body['sourceRunId'])
            if run['status'] != 'succeeded':
                fail('SOURCE_NOT_SUCCEEDED', '只有成功任务可作为保存来源。', 409)
            source = {'kind': 'sub_agent', 'runId': run['id']}
        return self.store.write(owner, 'recipe.create', key, body, lambda db: self._save(db, owner, body, source))

    def fork(self, owner, rid, body, key):
        original = self.recipe(owner, rid)
        definition = {k: original[k] for k in ('name', 'description', 'goalTemplate', 'constraints', 'capabilityRefs')}
        definition['name'] = (original['name'][:110] + ' · 改编')
        definition.update({k: body[k] for k in definition if k in body})
        binding_ids = original.get('capabilityBindingIds') if definition['capabilityRefs'] == original['capabilityRefs'] else None
        return self.store.write(owner, 'recipe.fork:' + rid, key, body,
                                lambda db: self._save(db, owner, definition, {'kind': 'recipe', 'id': rid}, binding_ids=binding_ids))

    def run(self, owner, rid, body, key):
        repeated, result = self.store.replay(owner, 'recipe.run:' + rid, key, body)
        if repeated:
            return result
        recipe = self.recipe(owner, rid)
        values = body.get('input', {})
        if not isinstance(values, dict) or set(values) != set(recipe['parameters']):
            fail('INVALID_INPUT', '请填写应用的全部参数，不要增加未声明参数。')
        for value in values.values():
            text(value, '参数', 4000)
        message = PLACEHOLDER.sub(lambda match: values[match.group(1).strip()], recipe['goalTemplate'])
        if recipe['constraints']:
            message += '\n\n本次执行约束：\n' + '\n'.join('- ' + item for item in recipe['constraints'])
        message += '\n\n请完成本次研究任务并核验实际结果。当前是已保存方法的一次执行，不要再次创建或保存应用。'
        for ref in recipe['capabilityRefs']:
            self.w.capabilities.admit(owner, ref['assetId'], ref['versionId'])
        payload = {'message': message, 'fileIds': body.get('fileIds', []), 'capabilityRefs': recipe['capabilityRefs'],
                   'searchMode': body.get('searchMode', 'auto')}
        cid = body.get('taskId') or 'new'
        if not isinstance(cid, str):
            fail('INVALID_REQUEST', '会话引用无效。')
        def submit(db):
            result = self.w.sub_agents.submit(owner, cid, payload, 'recipe-' + digest([rid, key]),
                execution_context={'kind': 'recipe', 'recipeId': rid, 'name': recipe['name']})
            self.store.put(db, owner, 'execution', result['id'], {'recipeId': rid, 'runId': result['id'], 'createdAt': now()})
            return result
        return self.store.write(owner, 'recipe.run:' + rid, key, body, submit)

    def portable(self, owner, rid):
        recipe = self.recipe(owner, rid)
        definition = {k: recipe[k] for k in ('name', 'description', 'goalTemplate', 'constraints')}
        dependencies = []
        binding_ids = recipe.get('capabilityBindingIds') or ['capability-' + str(index + 1) for index in range(len(recipe['capabilityRefs']))]
        for binding_id, ref in zip(binding_ids, recipe['capabilityRefs']):
            version = self.w.capabilities.version(owner, ref['assetId'], ref['versionId'])
            dependencies.append({'bindingId': binding_id, 'sourceIdentity': version['manifest']['source']['identity'],
                                 'contentDigest': version['contentDigest'], 'kind': version['manifest']['kind']})
        package = {'format': 'infinity-application', 'formatVersion': '1', 'kind': 'recipe',
                   'definition': definition, 'dependencies': dependencies,
                   'provenance': {'type': 'user-authored', 'execution': 'agent-research'}}
        from ..capability_library.sharing import assert_shareable
        assert_shareable(package)
        return package

    def _import(self, db, owner, package, source=None, bindings=None):
        if not isinstance(package, dict) or set(package) - {'format', 'formatVersion', 'kind', 'definition', 'dependencies', 'provenance'}:
            fail('INVALID_PACKAGE', '应用包格式无效。')
        if package.get('format') != 'infinity-application' or package.get('formatVersion') != '1' or package.get('kind') != 'recipe':
            fail('INVALID_PACKAGE', '请选择版本1的研究应用包。')
        if len(canonical(package)) > 100000:
            fail('LIMIT_EXCEEDED', '研究应用包超过100000字符。', 413)
        from ..capability_library.sharing import assert_shareable
        assert_shareable(package)
        definition = package.get('definition')
        if not isinstance(definition, dict) or set(definition) - {'name', 'description', 'goalTemplate', 'constraints'}:
            fail('INVALID_PACKAGE', '研究应用定义只能包含名称、说明、目标模板和约束。')
        dependencies = package.get('dependencies', [])
        if not isinstance(dependencies, list) or len(dependencies) > 8:
            fail('INVALID_PACKAGE', '应用依赖最多8项。')
        bindings = {} if bindings is None else bindings
        if not isinstance(bindings, dict):
            fail('INVALID_REQUEST', '依赖绑定必须是对象。')
        # A portable slot identifies a selection, not merely its source content.
        # Legacy packages are lossless only when each source occurs once.
        keyed = any(isinstance(dependency, dict) and 'bindingId' in dependency for dependency in dependencies)
        binding_ids, binding_keys = [], []
        for index, dependency in enumerate(dependencies):
            fields = {'sourceIdentity', 'contentDigest', 'kind'} | ({'bindingId'} if keyed else set())
            if not isinstance(dependency, dict) or set(dependency) != fields:
                fail('INVALID_PACKAGE', '依赖必须声明精确来源、内容摘要和类型，不能混用新旧槽位格式。')
            if (not isinstance(dependency['sourceIdentity'], str) or not dependency['sourceIdentity'] or
                    not isinstance(dependency['contentDigest'], str) or not re.fullmatch(r'[0-9a-f]{64}', dependency['contentDigest']) or
                    dependency['kind'] not in ('tool', 'skill')):
                fail('INVALID_PACKAGE', '依赖来源、内容摘要或类型无效。')
            binding_id = dependency.get('bindingId', 'capability-' + str(index + 1))
            if not isinstance(binding_id, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', binding_id):
                fail('INVALID_PACKAGE', '依赖槽位标识无效。')
            binding_key = binding_id if keyed else dependency['sourceIdentity']
            if binding_key in binding_keys:
                fail('INVALID_PACKAGE', '依赖槽位重复；旧包重复来源无法区分个人组件，请从原应用重新导出。')
            binding_ids.append(binding_id)
            binding_keys.append(binding_key)
        used_bindings = set()
        refs, selected_assets = [], set()
        assets = {a['assetId']: a for a in self.w.capabilities.store.list(owner, 'asset')
                  if not a.get('deletedAt') and a.get('lifecycle') == 'active'}
        versions = self.w.capabilities.store.list(owner, 'version')
        for dependency, binding_key in zip(dependencies, binding_keys):
            matches = [v for v in versions if v['assetId'] in assets and
                       v['manifest']['source']['identity'] == dependency['sourceIdentity'] and
                       v['contentDigest'] == dependency['contentDigest'] and v['manifest']['kind'] == dependency['kind']]
            if not matches:
                fail('DEPENDENCY_MISSING', '请先引入精确版本的能力：' + dependency['sourceIdentity'], 409,
                     {'dependency': dependency, 'candidates': []})
            binding = bindings.get(binding_key)
            if binding is not None:
                used_bindings.add(binding_key)
                if (not isinstance(binding, dict) or set(binding) != {'assetId', 'versionId'} or
                        any(not isinstance(value, str) or not value for value in binding.values())):
                    fail('INVALID_REQUEST', '依赖绑定需要个人assetId和versionId。')
                matches = [v for v in matches if all(v[k] == binding[k] for k in ('assetId', 'versionId'))]
                if not matches:
                    fail('DEPENDENCY_CHANGED', '绑定能力与应用包要求的来源、内容或类型不一致。', 409)
            if len(matches) != 1:
                fail('DEPENDENCY_AMBIGUOUS', '存在多个精确副本，请选择本次应用使用的能力：' + dependency['sourceIdentity'], 409,
                     {'dependency': dependency, 'candidates': [
                         {**dependency, 'assetId': v['assetId'], 'versionId': v['versionId'],
                          'name': assets[v['assetId']].get('name') or v['manifest'].get('name') or dependency['sourceIdentity']}
                         for v in matches]})
            if matches[0]['assetId'] in selected_assets:
                fail('DEPENDENCY_BINDING_CONFLICT', '不同依赖槽位不能合并到同一个个人组件，请准备并分别选择独立副本。', 409,
                     {'dependency': dependency})
            selected_assets.add(matches[0]['assetId'])
            refs.append({k: matches[0][k] for k in ('assetId', 'versionId')})
        if set(bindings) - used_bindings:
            fail('INVALID_REQUEST', '依赖绑定包含未使用的来源。')
        return self._save(db, owner, {**definition, 'capabilityRefs': refs}, source or {'kind': 'import', 'digest': digest(package)},
                          binding_ids=binding_ids)

    def import_recipe(self, owner, body, key):
        return self.store.write(owner, 'recipe.import', key, body,
                                lambda db: self._import(db, owner, body.get('package'), bindings=body.get('dependencyBindings')))

    def _package(self, owner, body):
        if body.get('kind') == 'recipe':
            package = self.portable(owner, text(body.get('recipeId'), '应用ID', 100))
            # A shared method must have a successful execution of this exact immutable recipe.
            with self.store.transaction() as db:
                runs = [e for e in self.store.list(db, owner, 'execution') if e['recipeId'] == body['recipeId']]
            if not any(self.w.sub_agents.store.run(owner, e['runId'])['status'] == 'succeeded' for e in runs):
                fail('VERIFICATION_REQUIRED', '请先成功运行这份研究应用，再发布。', 409)
            return package
        if body.get('kind') == 'pipeline':
            return self.pipeline_export(owner, text(body.get('agentRef'), '应用引用', 200), publishing=True)
        fail('INVALID_REQUEST', '应用类型无效。')

    def preview_publication(self, owner, body):
        package = self._package(owner, body)
        definition = package['definition']
        summary = {'name': text(body.get('title') or definition['name'], '发布名称', 128),
                   'description': text(body.get('description') or definition.get('description') or definition['name'], '发布说明', 4000),
                   'kind': package['kind'], 'dependencies': package.get('dependencies', []),
                   'scope': '本平台所有登录账号',
                   'warnings': ['共享定义与源码；不包含历史输入、产物或凭据。接收者需自行准备依赖并验证。']}
        from ..capability_library.sharing import assert_shareable
        assert_shareable(summary)
        return {'snapshotDigest': digest({'package': package, 'summary': summary}), 'package': package, 'summary': summary}

    def publish(self, owner, body, key):
        repeated, result = self.store.replay(owner, 'publication.publish', key, body)
        if repeated:
            return result
        if body.get('confirmSharing') is not True:
            fail('CONFIRMATION_REQUIRED', '请审阅将共享的内容后确认。', 409)
        def save(db):
            # _package uses a read transaction; perform preview before taking the write lock below.
            if body.get('snapshotDigest') != preview['snapshotDigest']:
                fail('PREVIEW_CHANGED', '内容已变化，请重新预览。', 409)
            record = {'id': uid('pub'), 'kind': preview['package']['kind'],
                      'name': preview['summary']['name'], 'description': preview['summary']['description'],
                      'createdAt': now(), 'publisherId': owner, 'withdrawn': False,
                      'snapshotDigest': preview['snapshotDigest'], 'package': preview['package']}
            self.store.put(db, PUBLIC, 'publication', record['id'], record)
            return self._public(record, owner)
        preview = self.preview_publication(owner, body)
        return self.store.write(owner, 'publication.publish', key, body, save)

    @staticmethod
    def _public(record, owner):
        return {k: record[k] for k in ('id', 'kind', 'name', 'description', 'createdAt', 'withdrawn', 'snapshotDigest')} | {
            'owned': record['publisherId'] == owner,
            'dependencies': record['package'].get('dependencies', []), 'publisher': '平台用户'}

    def publications(self, owner):
        with self.store.transaction() as db:
            return [self._public(r, owner) for r in self.store.list(db, PUBLIC, 'publication')
                    if not r['withdrawn'] or r['publisherId'] == owner]

    def publication_package(self, owner, pid):
        with self.store.transaction() as db:
            record = self.store.get(db, PUBLIC, 'publication', pid)
            if record['withdrawn']:
                fail('PUBLICATION_WITHDRAWN', '发布者已撤回该应用。', 410)
            return record['package']

    def copy_publication(self, owner, pid, body, key):
        from ..agent_platform.asset_creator import blank
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            fail('INVALID_REQUEST', '请提供Idempotency-Key。')
        def copy_item(db):
            record = self.store.get(db, PUBLIC, 'publication', pid)
            if record['withdrawn']:
                fail('PUBLICATION_WITHDRAWN', '发布者已撤回该应用。', 410)
            package = record['package']
            source = {'kind': 'legacy_publication', 'publicationId': pid,
                      'snapshotDigest': record['snapshotDigest'], 'packageKind': package['kind']}
            modern_key = digest([pid, key])
            if package['kind'] == 'recipe':
                result = self._import(db, owner, package, {'kind': 'publication', 'id': pid, 'digest': record['snapshotDigest']},
                                      bindings=body.get('dependencyBindings'))
                source['resolvedBindings'] = copy.deepcopy(result['capabilityRefs'])
                original = package['definition']
                doc = blank(original['name'])
                doc.update(description=original.get('description', ''),
                           inputSchema={'type': 'object', 'properties': {name: {'type': 'string'}
                               for name in result['parameters']}, 'required': list(result['parameters'])},
                           rules=[original['goalTemplate'][i:i + 4000]
                                  for i in range(0, len(original['goalTemplate']), 4000)]
                                 + list(original.get('constraints', [])),
                           source={'kind': 'legacy_research_recipe', 'publicationId': pid,
                                   'package': copy.deepcopy(package)})
                doc['gaps'].append('原研究应用依赖动态研究流程，未自动转换成固定执行节点；完整原包和精确能力绑定已保留，需在详情中实现并验证。')
                modern = self.w.asset_apps.save_legacy_copy(owner, doc, source, modern_key)
                return {'kind': 'recipe', 'recipe': result, 'assetId': modern['appId'],
                        'versionId': modern['versionId'], 'detail': '/desktop/api/asset-apps/' + modern['appId']}
            result = self.pipeline_import(owner, package, 'publication-' + digest([pid, key]), body.get('dependencyBindings'),
                                          source={'kind': 'publication', 'id': pid, 'digest': record['snapshotDigest']})
            return {'kind': 'pipeline', 'application': result, 'assetId': result['assetId'],
                    'versionId': result['versionId'], 'detail': result['detail']}
        return self.store.write(owner, 'publication.copy:' + pid, key, body, copy_item)

    def withdraw(self, owner, pid, body, key):
        def change(db):
            record = self.store.get(db, PUBLIC, 'publication', pid)
            if record['publisherId'] != owner:
                fail('NOT_FOUND', '资源不存在或无权访问。', 404)
            record['withdrawn'] = True
            self.store.put(db, PUBLIC, 'publication', pid, record)
            return self._public(record, owner)
        return self.store.write(owner, 'publication.withdraw:' + pid, key, body, change)

    def pipeline_export(self, owner, reference, publishing=False):
        if publishing:
            spec = self.w.platform.repository.agent(owner, reference)
            if not self.w.platform.repository.has_successful_definition(owner, spec):
                fail('VERIFICATION_REQUIRED', '请先成功运行这份固定流程应用，再发布。', 409)
        return self.w.platform.export_application(owner, reference)

    def pipeline_import(self, owner, package, key, bindings=None, *, source=None):
        return self.w.platform.import_application(owner, {'package': package, 'idempotencyKey': key, 'dependencyBindings': bindings or {}}, source=source)
