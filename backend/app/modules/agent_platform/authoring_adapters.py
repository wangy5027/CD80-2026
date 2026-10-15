"""Private immutable candidates; validation never registers a visible personal asset.

Only the authoring coordinator calls this adapter. Durable evidence is owner-scoped
and commit re-reads it instead of accepting client supplied validation claims.
"""
import base64
import copy
import threading
from pathlib import PurePosixPath

from ..capability_library.contracts import canonical, digest, fail, now, uid
from ..capability_library import components
from ..capability_library.assertions import evaluate, validate_assertions
from .asset_contracts import at, definition, delivery_check, resource_files, validate_value
from .planner import _json_object
from .executor import _safe_name


def _check(stopped):
    if stopped is not None:
        if hasattr(stopped, 'check'):
            stopped.check()
        elif stopped.is_set() if hasattr(stopped, 'is_set') else stopped():
            fail('CANCELLED', '创作检查已停止。', 409)


def _execution(content, kind):
    """Conservative whitelist: unknown fields always invalidate execution evidence."""
    value = copy.deepcopy(content)
    # Saved examples are test metadata, never business execution instructions.
    value.pop('testCases', None)
    # A Skill's name is inserted into its model observations; this is execution
    # context, not a display-only rename. Its metadata therefore stays covered.
    if kind == 'skill':
        return value
    for key in ('name', 'description'):
        value.pop(key, None)
    if kind != 'app':
        for key in ('name', 'description'):
            value.get('manifest', {}).pop(key, None)
    return value


def _equal(actual, expected):
    """JSON numbers share a value domain; booleans never equal 0/1."""
    if type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_equal(a, b) for a, b in zip(actual, expected))
    return actual == expected


def component_context(workbench, owner, snapshot, bindings=None):
    """Admission fingerprint includes the actual dynamic Skill model and engine."""
    runtime = components.runtime_digest(workbench.capabilities, owner, snapshot, bindings)
    if snapshot['manifest']['kind'] == 'skill':
        runtime = digest({'component': runtime, 'skillEngine': workbench.asset_apps._context(owner,
            {'nodes': [{'component': snapshot}]}, bindings)})
    return runtime


def _feedback(value, limit=8000):
    """Bound diagnostics without cutting the user's business requirements."""
    if value is None:
        return None
    rendered = canonical(value)
    if len(rendered) <= limit:
        return copy.deepcopy(value)
    problem = canonical({key: value[key] for key in ('issues', 'error') if key in value})[:1800] if isinstance(value, dict) else ''
    return {'error': value.get('error') if isinstance(value, dict) else None, 'problem': problem,
            'summary': rendered[:max(0, limit - len(problem))], 'truncated': True}


class AuthoringAdapters:
    def __init__(self, workbench):
        self.w = workbench
        self.apps = workbench.asset_apps
        self.library = workbench.capabilities

    @staticmethod
    def _content(kind, value):
        value = copy.deepcopy(value)
        if kind == 'app':
            return value.get('definition', value)
        value = value.get('draft', value)
        test_cases = value.get('testCases')
        if value.get('component'):
            value = value['component']
        if value.get('format') == 'cd80.component/1':
            snapshot = components.export_snapshot(value)
            value = {'manifest': snapshot['manifest'], 'files': [{'path': p, 'base64': b} for p, b in snapshot['files'].items()],
                     'dependencySnapshots': snapshot['dependencies']}
        if test_cases is not None:
            value['testCases'] = test_cases
        if isinstance(value.get('files'), dict):
            value['files'] = [{'path': path, **raw} for path, raw in value['files'].items()]
        manifest = value.get('manifest', {})
        value.update(kind=kind, name=manifest.get('name', value.get('name', '')),
                     description=manifest.get('description', value.get('description', '')))
        return value

    def _model(self, prompt, data, stopped):
        _check(stopped)
        raw = self.w.platform.planner._call(prompt, data, stopped or threading.Event(),
                                           lambda response: None, structured=True)
        _check(stopped)
        return _json_object(raw)

    def _file_context(self, owner, ids):
        """Bounded read-only data previews, never implementation or test output."""
        from ..sub_agent.evidence import clean
        items = self.w.platform._metadata(owner, ids)
        for item in items:
            if PurePosixPath(item['name']).suffix.lower() not in {'.txt', '.md', '.csv', '.tsv', '.json', '.xml'}:
                continue
            stream, _, lock = self.w.open_file(owner, item['id'])
            try:
                raw = stream.read(4097)
                text = raw[:4096].decode('utf-8-sig')
                if clean(text)[1]:
                    item.update(preview=text, truncated=len(raw) > 4096)
            except UnicodeError:
                pass
            finally:
                stream.close()
                lock.release()
        return items

    def _fixtures(self, owner, case):
        """Synthetic test data stays out of the user's upload and asset lists."""
        entries = case.get('fileFixtures', [])
        if not isinstance(entries, list) or len(entries) > 8:
            fail('TEST_FIXTURE_INVALID', '每个用例最多生成8份测试文件。', 422)
        files, metadata, aliases, total = {}, [], {}, 0
        allowed = {'.txt', '.md', '.csv', '.tsv', '.json', '.xml', '.svg', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.pdf', '.xlsx', '.docx', '.pptx'}
        for entry in entries:
            if (not isinstance(entry, dict) or not isinstance(entry.get('name'), str) or
                    not _safe_name(entry['name']) or PurePosixPath(entry['name']).suffix.lower() not in allowed):
                fail('TEST_FIXTURE_INVALID', '测试文件只允许安全平面名称及受支持的数据格式。', 422)
            name = entry['name']
            if name in files or ('text' in entry) == ('base64' in entry):
                fail('TEST_FIXTURE_INVALID', '测试文件名称重复或内容编码无效。', 422)
            try:
                raw = entry['text'].encode('utf-8') if 'text' in entry else base64.b64decode(entry['base64'], validate=True)
            except (TypeError, ValueError, AttributeError):
                fail('TEST_FIXTURE_INVALID', '测试文件编码无效。', 422)
            total += len(raw)
            if not raw or len(raw) > 256 * 1024 or total > 1024 * 1024:
                fail('TEST_FIXTURE_INVALID', '测试文件应非空，单文件不超过256KiB且每用例总量不超过1MiB。', 422)
            from ..capability_library.packages import has_secret
            if has_secret({name: raw}):
                fail('TEST_FIXTURE_INVALID', '合成用例不能夹带凭据。', 403)
            delivery_check(None, {name: raw}, {'files': [{'name': name}]})
            checksum = digest(raw)
            identity = 'fixture_' + digest({'name': name, 'sha256': checksum})
            meta = {'id': identity, 'name': name, 'size': len(raw), 'sha256': checksum, 'claim': 'generated'}
            if entry.get('bindAs') is not None:
                alias = entry['bindAs']
                if not isinstance(alias, str) or not alias or len(alias) > 120 or alias in aliases:
                    fail('TEST_FIXTURE_INVALID', '测试文件的输入别名无效或重复。', 422)
                aliases[alias] = identity
            entry.update(id=identity, sha256=checksum, claim='generated')
            files[name] = raw
            metadata.append(meta)
            self.library.store.blob(owner, checksum, raw)
            self.library.store.put(owner, 'authoring_fixture', identity, meta)
        return files, metadata, aliases

    def create(self, owner, kind, instruction, *, content=None, context=None, stopped=None):
        from .asset_creator import create, _capture
        self.w.auth.get_user(owner)
        context = copy.deepcopy(context or {})
        if 'technicalRepairFeedback' in context:
            context['technicalRepairFeedback'] = _feedback(context['technicalRepairFeedback'])
        body = {'kind': kind, 'instruction': instruction}
        if content:
            body['definition' if kind == 'app' else 'draft'] = self._content(kind, content)
        source = None
        if context.get('sourceRunId') and content is None:
            info = {}
            source = _capture(self.apps, owner, context['sourceRunId'], context.get('stepIds'), info)
            if info.get('selectionRequired'):
                candidates = [row for row in info['candidateSteps'] if row['reconstructable']]
                if candidates:
                    selected = self._model('从已核实的真实成功步骤中推荐本次要求需要的步骤。仅返回JSON {stepIds:[实际id],reason:文本}。'
                        '选择是推断，不代表历史已采用。业务目标不明确返回 {question:具体问题}，不能默认全部成功尝试。',
                        {'instruction': instruction, 'candidates': candidates,
                         'source': source.get('source'), 'relatedHistory': context.get('relatedHistory', [])}, stopped)
                    if selected.get('question'):
                        fail('AUTHORING_INPUT_REQUIRED', str(selected['question']), 422)
                    chosen = selected.get('stepIds')
                    if not isinstance(chosen, list) or not chosen or any(x not in {r['stepId'] for r in candidates} for x in chosen):
                        fail('AUTHORING_INPUT_REQUIRED', '暂时不能确定所需来源步骤，请补充要保留的方法。', 422)
                    source = _capture(self.apps, owner, context['sourceRunId'], chosen)
                    source['source'].update(selectionBasis='inferred_selection', claim='inferred',
                                            selectionReason=str(selected.get('reason', ''))[:2000])
            body['definition'] = source
            context['verifiedSource'] = copy.deepcopy(source.get('source', {}))
        _check(stopped)
        result = create(self.apps, owner, body, stopped=stopped, context=context)
        value = result.get('definition') if kind == 'app' else result.get('draft')
        if not value:
            fail('AUTHORING_INPUT_REQUIRED', '；'.join(result.get('warnings', [])) or '缺少可创作的实现或方法。', 422)
        if source is not None:
            if kind == 'app':
                value['source'] = copy.deepcopy(source.get('source', {}))
            else:
                value.setdefault('authoringProvenance', {})['sourceRecord'] = copy.deepcopy(source.get('source', {}))
                if kind == 'skill':
                    from .asset_creator import _retain_source_skills
                    _retain_source_skills(self.apps, owner, context['sourceRunId'], value,
                                         set(source.get('source', {}).get('selectedStepIds', [])))
        return value

    def prepare(self, owner, kind, content, *, bindings=None, previous=None):
        self.w.auth.get_user(owner)
        if kind not in {'app', 'tool', 'skill'}:
            fail('INVALID_REQUEST', '请选择App、Tool或Skill。')
        bindings = copy.deepcopy(bindings or {})
        if not isinstance(bindings, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in bindings.items()):
            fail('INVALID_BINDINGS', '连接绑定必须为本人连接标识的映射。')
        prior = (previous or {}).get('content', previous)
        prior = self._content(kind, prior) if prior else None
        value = self._content(kind, content)
        if kind == 'app':
            value, _ = definition(value.get('definition', value))
            value, issues = definition(self.apps._freeze(owner, value, prior))
            material = {'definition': value, 'runtimeBindings': bindings}
        else:
            value = value.get('draft', value)
            if value.get('format') == 'cd80.component/1':
                snapshot = components.export_snapshot(value)
                value = {'kind': kind, 'name': snapshot['manifest']['name'], 'description': snapshot['manifest']['description'],
                         'manifest': snapshot['manifest'], 'files': [{'path': p, 'base64': b} for p, b in snapshot['files'].items()],
                         'dependencySnapshots': snapshot['dependencies']}
            else:
                snapshot = components.snapshot_from_package(value['manifest'], resource_files(value.get('files', [])),
                    value.get('dependencySnapshots', []))
                snapshot = components.export_snapshot(snapshot)
                value['manifest'] = copy.deepcopy(snapshot['manifest'])
            if snapshot['manifest']['kind'] != kind:
                fail('INVALID_REQUEST', '候选类型不一致。')
            from ..capability_library.packages import inspect
            issues = inspect(snapshot['manifest'], components._files(snapshot))
            material = {'snapshot': snapshot, 'runtimeBindings': bindings}
        material['kind'] = kind
        execution_digest = digest(_execution(value, kind))
        change_class = 'behavior'
        if isinstance(prior, dict) and digest(_execution(prior, kind)) == execution_digest:
            change_class = 'display_only' if prior == value else 'matching'
        if previous and previous.get('material', {}).get('runtimeBindings', {}) != bindings:
            change_class = 'behavior'
        prepared = {'kind': kind, 'content': value, 'contentDigest': digest(value), 'executionDigest': execution_digest,
                    'changeClass': change_class, 'material': material, 'issues': copy.deepcopy(issues)}
        prepared['materialDigest'] = digest(material)
        try:
            prepared['runtimeContextDigest'] = self._runtime(owner, prepared)
            if kind != 'app' and material['snapshot']['manifest'].get('tool', {}).get('execution', {}).get('type') == 'builtin':
                prepared['issues'] = [p for p in prepared['issues'] if p.get('code') != 'MISSING_IMPLEMENTATION']
        except Exception as error:
            prepared['runtimeContextDigest'] = None
            prepared['issues'].append({'code': getattr(error, 'code', 'RUNTIME_UNAVAILABLE'), 'message': str(error)})
        return prepared

    def _runtime(self, owner, prepared):
        material = prepared['material']
        if prepared['kind'] == 'app':
            return self.apps._context(owner, material['definition'], material['runtimeBindings'])
        return component_context(self.w, owner, material['snapshot'], material['runtimeBindings'])

    @staticmethod
    def _intact(prepared):
        if digest(prepared['content']) != prepared['contentDigest'] or digest(prepared['material']) != prepared['materialDigest']:
            fail('CANDIDATE_CHANGED', '候选内容已变化，必须重新准备和检查。', 409)

    def _reuse(self, owner, prepared, context):
        target = (context or {}).get('target')
        if not target or prepared['changeClass'] == 'behavior':
            return None
        if prepared['kind'] == 'app':
            old = self.apps.detail(owner, target['assetId'], target.get('baseVersionId'))
            verification = old['verification']
            prior = self.prepare(owner, 'app', old['definition'], bindings=old['runtimeBindings'])
            valid = verification.get('status') == 'passed' and verification.get('contextDigest') == prior['runtimeContextDigest']
            ref = {'kind': 'app_version', 'assetId': old['id'], 'versionId': old['versionId']}
        else:
            version = self.library.version(owner, target['assetId'], target['baseVersionId'])
            snapshot = components.build_snapshot(self.library, owner, target['assetId'], target['baseVersionId'])
            prior = self.prepare(owner, prepared['kind'], snapshot, bindings=version.get('componentBindings'))
            # Draft representations may differ (text/base64); compare closed runtime materials as well.
            verification = self.library.evidence(owner, version)
            valid = (bool(verification) and not prior['issues'] and
                     self.library.configuration(owner, version)['configurationId'] == version['configurationId'])
            ref = {'kind': 'capability_version', 'assetId': target['assetId'], 'versionId': target['baseVersionId']}
        same = prior['executionDigest'] == prepared['executionDigest']
        if prepared['kind'] != 'app':
            def runtime_body(item):
                snapshot = copy.deepcopy(item['material']['snapshot'])
                for key in ('name', 'description'):
                    snapshot['manifest'].pop(key, None)
                for key in ('contentDigest', 'snapshotDigest', 'provenance'):
                    snapshot.pop(key, None)
                return snapshot
            same = runtime_body(prior) == runtime_body(prepared)
        if valid and same and prior['material']['runtimeBindings'] == prepared['material']['runtimeBindings']:
            return {'reference': ref, 'evidenceDigest': digest(verification), 'executionDigest': prior['executionDigest'],
                    'runtimeContextDigest': prior['runtimeContextDigest'],
                    'reusedEvidenceIds': [verification.get('id') or verification.get('verificationId') or ref['versionId']],
                    'reuseReason': '仅匹配/展示信息变化，原执行内容、连接及运行环境仍与原有效证据一致。',
                    'comparedDigests': {'oldContent': prior['contentDigest'], 'newContent': prepared['contentDigest'],
                                       'oldRuntime': prior['runtimeContextDigest'], 'newRuntime': prepared['runtimeContextDigest']}}
        return None

    def plan(self, owner, prepared, *, instruction='', context=None, stopped=None):
        self._intact(prepared)
        _check(stopped)
        if prepared['issues']:
            return {'cases': [], 'issues': prepared['issues']}
        context = context or {}
        reuse = self._reuse(owner, prepared, context)
        if reuse and prepared['changeClass'] == 'display_only':
            return {'cases': [], 'issues': [], 'reuse': reuse, 'contentDigest': prepared['contentDigest']}
        value = prepared['content']
        # Expectations are generated from the requirement/interface, never source code or candidate output.
        interface = ({k: value.get(k) for k in ('name', 'description', 'inputSchema', 'rules', 'parameterization')}
                     if prepared['kind'] == 'app' else {k: value['manifest'].get(k) for k in ('name', 'description', 'kind')})
        if prepared['kind'] == 'app':
            interface['outputContract'] = {'outputs': value['outputs'],
                'steps': [{'id': node['id'], 'delivery': node['delivery']} for node in value['nodes']]}
        if prepared['kind'] == 'tool':
            interface.update({k: value['manifest']['tool'].get(k) for k in ('inputSchema', 'outputSchema')})
        prompt = '''你是独立测试设计者，不能读取候选实现或从运行结果反推预期。只返回JSON {cases:[],issues:[]}。
你的输出由平台保存为测试计划，在用户授权的测试流程中执行。上述流程已经由平台实现，不是缺失条件，也不是资产业务输出；不要为“隔离执行/展示结果/最后确认保存/尚未测试”提出issues或生成业务断言。
issues只用于缺少实际业务输入定义、计算口径、真实数据依赖或授权。无法自动判断的效果使用manual_review并同时给客观交付检查，不能因此阻止生成和运行测试。例：用户说“创建求和工具，测两组数后让我确认”，只设计求和业务输入和独立预期，issues=[]；不要要求用户先提供测试结果或确认测试流程。
用户业务规则优先，interface只是候选接口参考，不是新的业务需求；候选擅自增加的自测/verified/schema等字段不能变成用户验收标准。测试过程与业务功能严格区分。
同一case的所有checks必须互相一致：json_equals必须包含该案例完整业务预期，fields/assertions/delivery.schema必须对该预期也成立；不能一边要求精确只有total/count，一边又要求额外verified字段。动态ID/时间/工件标识用字段、Schema或效果标准，不写虚假精确值。
planRepairFeedback只含上次尚未冻结计划与技术错误，不含候选运行结果；修复计划内部格式/一致性，不改变用户规则，不从实现输出反推预期。
默认只设计一个case，role为original（原要求案例，历史不存在可构造并标generated）。不同输入和边界用例不是必测项，不要主动追加；用户可在资产详情另行选择追加测试。
每个case为{id,label,role,input:{},task:文本,fileRefs:[],checks:[],basis:{claim:'observed|inferred|generated',description:文本}}。
checks为{id,mode,requirement,payload,basisRefs:[]}。mode=json_equals时payload={value:独立精确预期}；fields时payload={values:{点分路径:预期}}；assertions时payload={rules:[{path:数组,op:eq|type|minItems|requiredKeys|eachType|date,value:值}]}；delivery时payload={schema:JSONSchema,files:[{name}]}；manual_review时payload={criteria:具体效果标准}。
固定算法须从用户要求独立计算业务断言，不能只测“是对象”。开放文本/图表用manual_review加客观交付检查，不能宣称自动证明效果。
授权文件只能引用给定fileIds，App可另给fileBindings:{输入别名:授权id}。有缺失业务口径/真实数据/授权时用issues:[{code:'AUTHORING_INPUT_REQUIRED',message:具体问题}]，不杜撰。
测试不得外部业务写入。可生成合成输入但须标来源，不能伪造历史原用例。
文件输入可自动构造小型适用的不同数据，不要求用户另造文件：case.fileFixtures=[{name:'sample.csv',text:实际内容或base64:真实字节,bindAs:App文件别名}]。
App的bindAs须匹配parameterization中的文件别名，不填入fileBindings；Tool/Skill参数用/input/<name>路径。只能生成数据文件，禁止脚本/安装包；单文件最多256KiB，每用例最多8份且总量1MiB。
有完整授权原文件可使用原文件；否则original角色为原业务要求对应的合成案例并标generated。预期仍须从业务规则/输入独立计算，不能只改文件名当不同输入。'''
        if reuse:
            prompt = '''只返回JSON {cases:[],issues:[]}。为修改后的名称/说明设计1个原任务的选择验证。
case={id,label,role:'original',input:{},task:用户任务,checks:[{id,mode:'matching',requirement:选择边界,payload:{expected:true或false},basisRefs:[]}],basis:{claim:'generated',description:依据}}。
仅验证任务与能力名称/说明的匹配，不能更改业务范围。'''
        generated = self._model(prompt, {'instruction': instruction, 'interface': interface,
            'verifiedExamples': context.get('verifiedExamples', []), 'fileIds': context.get('fileIds', []),
            'authorizedFiles': self._file_context(owner, context.get('fileIds', [])),
            'businessRequirements': context.get('businessRequirements', []),
            'userRequirements': [message.get('content', '') for history in context.get('relatedHistory', [])
                                 for message in history.get('messages', []) if message.get('role') == 'user'][-16:],
            'planRepairFeedback': _feedback(context.get('planRepairFeedback'))}, stopped)
        if generated.get('issues') or generated.get('question'):
            return {'cases': [], 'issues': generated.get('issues') or [{'code': 'AUTHORING_INPUT_REQUIRED', 'message': generated['question']}]}
        plan = {'cases': generated.get('cases', []), 'issues': [], 'contentDigest': prepared['contentDigest']}
        # Models may retain the old two-case convention. Additional inputs are
        # opt-in at the saved asset's test form, not automatically run here.
        if isinstance(plan['cases'], list):
            originals = [case for case in plan['cases'] if isinstance(case, dict) and case.get('role') == 'original']
            if originals:
                plan['cases'] = originals[:1]
        if reuse:
            plan['reuse'] = reuse
        try:
            self._validate_plan(owner, prepared, plan, context)
        except Exception as error:
            error.authoringPlan = copy.deepcopy(plan)
            raise
        return plan

    def _validate_plan(self, owner, prepared, plan, context):
        cases = plan.get('cases')
        if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
            fail('TEST_PLAN_INVALID', '验证需要原任务用例，最多10组；其他输入为可选。', 422)
        ids, fingerprints, roles, originals, different = set(), set(), set(), set(), set()
        granted = set((context or {}).get('fileIds', []))
        for case in cases:
            if (not isinstance(case, dict) or not isinstance(case.get('input'), dict) or
                    not isinstance(case.get('checks'), list) or not 1 <= len(case['checks']) <= 40):
                fail('TEST_PLAN_INVALID', '每个用例需要明确输入和独立预期。', 422)
            case.setdefault('id', uid('case'))
            if case['id'] in ids:
                fail('TEST_PLAN_INVALID', '用例标识重复。', 422)
            ids.add(case['id']); roles.add(case.get('role'))
            refs = case.get('fileRefs', [])
            if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
                fail('FILE_NOT_GRANTED', '测试文件引用无效。', 403)
            refs = sorted(set(refs) | set(case.get('fileBindings', {}).values()))
            if not set(refs) <= granted:
                fail('FILE_NOT_GRANTED', '测试只能读取本次明确授权的文件。', 403)
            meta = self.w.platform._metadata(owner, refs)
            _, fixture_meta, _ = self._fixtures(owner, case)
            basis = case.get('basis') if isinstance(case.get('basis'), dict) else {}
            claim = basis.get('claim', 'generated')
            if claim == 'observed':
                verified = any(example.get('id') == basis.get('exampleId') and example.get('id') and
                    example.get('input') == case['input'] and example.get('checks') == case['checks']
                    for example in (context or {}).get('verifiedExamples', []) if isinstance(example, dict))
                if not verified:
                    basis['description'] = str(basis.get('description', '')) + '；这是根据需求整理的新用例，未核实为历史成功用例。'
                    claim = 'generated'
            basis['claim'] = 'generated' if fixture_meta or claim not in {'observed', 'inferred', 'generated'} else claim
            case['basis'] = basis
            replacements = {m['id']: m['sha256'] for m in meta}
            replacements.update({m['name']: m['sha256'] for m in fixture_meta})
            def content_identity(value):
                if isinstance(value, str):
                    for identity, checksum in replacements.items():
                        value = value.replace(identity, checksum)
                    return value
                if isinstance(value, list): return [content_identity(v) for v in value]
                if isinstance(value, dict): return {k: content_identity(v) for k, v in value.items()}
                return value
            fingerprint = digest({'input': content_identity(case['input']), 'task': content_identity(case.get('task')) if prepared['kind'] == 'skill' or plan.get('reuse') else None,
                                  'files': sorted(m['sha256'] for m in meta + fixture_meta)})
            fingerprints.add(fingerprint)
            if case.get('role') == 'original': originals.add(fingerprint)
            if case.get('role') == 'different_input': different.add(fingerprint)
            case['fileRefs'] = refs
            for check in case['checks']:
                if not isinstance(check, dict) or not isinstance(check.get('payload'), dict):
                    fail('TEST_PLAN_INVALID', '每个检查需要结构化预期。', 422)
                payload = check.get('payload', {})
                mode = check.get('mode')
                reason = ''
                valid = isinstance(payload, dict) and bool(check.get('requirement'))
                if mode == 'json_equals': valid &= 'value' in payload
                elif mode == 'fields': valid &= isinstance(payload.get('values'), dict) and bool(payload.get('values'))
                elif mode == 'assertions':
                    try: validate_assertions(payload.get('rules'))
                    except ValueError as error:
                        valid = False
                        reason = str(error)
                elif mode == 'manual_review': valid &= isinstance(payload.get('criteria'), str) and bool(payload['criteria'].strip())
                elif mode == 'delivery': valid &= bool(payload.get('schema') or payload.get('files'))
                elif mode == 'matching': valid &= bool(plan.get('reuse')) and type(payload.get('expected')) is bool
                else: valid = False
                if not valid:
                    fail('TEST_PLAN_INVALID', '用例 ' + str(case['id']) + ' 的检查 ' + str(check.get('id')) +
                         '（mode=' + str(mode) + '）格式无效：' + (reason or
                         '需要非空requirement及该mode对应的payload；只支持json_equals、fields、assertions、delivery、manual_review。'), 422)
            exact = [check['payload']['value'] for check in case['checks'] if check['mode'] == 'json_equals']
            for expected in exact:
                for check in case['checks']:
                    payload, mode = check['payload'], check['mode']
                    consistent = True
                    try:
                        if mode == 'json_equals': consistent = _equal(expected, payload['value'])
                        elif mode == 'fields': consistent = all(_equal(at(expected, path), value) for path, value in payload['values'].items())
                        elif mode == 'assertions': consistent = not evaluate(expected, payload['rules'])
                        elif mode == 'delivery' and payload.get('schema'):
                            validate_value(expected, payload['schema'], '交付')
                    except Exception:
                        consistent = False
                    if not consistent:
                        fail('TEST_PLAN_INVALID', '用例 ' + str(case['id']) + ' 的独立精确预期与检查 ' + str(check.get('id', mode)) + ' 相互矛盾，请在执行前修正计划。', 422)
        if (cases[0].get('role') != 'original' or not roles <= {'original', 'different_input', 'boundary'} or
                ('different_input' in roles and not different - originals)):
            fail('TEST_PLAN_INVALID', '首项须为原任务用例；追加不同输入时不能只改标签或预期。', 422)

    def validate(self, owner, prepared, plan, *, stopped=None, on_case=None, context=None):
        self._intact(prepared)
        prepared, plan = copy.deepcopy(prepared), copy.deepcopy(plan)
        stopped = stopped or threading.Event()
        _check(stopped)
        if prepared['issues'] or plan.get('issues'):
            return {'status': 'failed', 'cases': [], 'issues': prepared['issues'] + plan.get('issues', [])}
        if plan.get('contentDigest') not in (None, prepared['contentDigest']):
            fail('CANDIDATE_CHANGED', '测试计划不属于当前候选。', 409)
        if self._runtime(owner, prepared) != prepared['runtimeContextDigest']:
            fail('STALE_CONFIGURATION', '运行条件已改变，请重新准备候选。', 409)
        reuse = self._reuse(owner, prepared, context) if plan.get('reuse') else None
        if plan.get('reuse') and reuse != plan['reuse']:
            fail('VERIFICATION_REQUIRED', '原证据或运行条件已变化，不能免测。', 409)
        if not reuse or prepared['changeClass'] != 'display_only':
            self._validate_plan(owner, prepared, plan, context)
        evidence = {'id': uid('ae'), 'kind': prepared['kind'], 'contentDigest': prepared['contentDigest'],
            'executionDigest': prepared['executionDigest'], 'runtimeContextDigest': prepared['runtimeContextDigest'],
            'planDigest': digest(plan), 'executionMode': 'real', 'status': 'running', 'cases': [], 'createdAt': now(),
            'materialDigest': prepared['materialDigest'], 'issues': [], 'reuse': reuse}
        evidence['adapterEvidenceId'] = evidence['id']
        self.library.store.put(owner, 'authoring_plan', evidence['id'], copy.deepcopy(plan))
        try:
            for case in plan['cases']:
                _check(stopped)
                control_error = None
                row = {'id': case['id'], 'label': case.get('label', ''), 'role': case['role'], 'status': 'running',
                       'input': case['input'], 'checks': [], 'basis': case.get('basis', {}), 'startedAt': now()}
                row['fixtureRefs'] = self._fixtures(owner, case)[1]
                row['fileRefs'] = self.w.platform._metadata(owner, case.get('fileRefs', []))
                try:
                    output = ({'result': None, 'files': {}} if reuse else self._execute(owner, prepared, case, stopped))
                    _check(stopped)
                    row.update(result=output.get('result'), artifacts=output.get('artifacts', []))
                    for check in case['checks']:
                        outcome = self._check_result(prepared, case, check, output, stopped)
                        row['checks'].append({'id': check.get('id'), 'mode': check['mode'], **outcome})
                    row['status'] = ('failed' if any(r['status'] == 'failed' for r in row['checks']) else
                                     'awaiting_review' if any(r['status'] == 'awaiting_review' for r in row['checks']) else 'passed')
                    if output.get('executionRef'): row['executionRef'] = output['executionRef']
                except Exception as error:
                    row.update(status='cancelled' if stopped.is_set() else 'failed',
                               error={'code': getattr(error, 'code', 'EXECUTION_FAILED'), 'message': str(error)[:3000]})
                    if getattr(error, 'candidateRunId', None):
                        row['executionRef'] = {'candidateRunId': error.candidateRunId}
                    for field in ('invocationId', 'skillCallId'):
                        identity = getattr(error, field, None) or getattr(error, 'details', {}).get(field)
                        if identity: row.setdefault('executionRef', {})[field] = identity
                    if row['error']['code'] in {'BUDGET_EXHAUSTED', 'AUTHORING_DEADLINE'}:
                        control_error = error
                row['completedAt'] = now()
                evidence['cases'].append(row)
                self.library.store.put(owner, 'authoring_evidence', evidence['id'], evidence)
                if control_error:
                    raise control_error
                _check(stopped)
                if on_case: on_case(copy.deepcopy(row))
                if row.get('error', {}).get('code') == 'OUTCOME_UNKNOWN':
                    evidence['cases'].extend({'id': pending['id'], 'label': pending.get('label', ''), 'role': pending['role'],
                        'status': 'not_run', 'reason': '前一调用结果未知，等待核对后再继续。'} for pending in plan['cases'][len(evidence['cases']):])
                    break
            evidence['status'] = ('failed' if any(r['status'] == 'failed' for r in evidence['cases']) else
                                  'awaiting_review' if any(r['status'] == 'awaiting_review' for r in evidence['cases']) else 'passed')
            errors = [row['error'] for row in evidence['cases'] if row.get('error')]
            if any(e['code'] in {'AUTHORING_INPUT_REQUIRED', 'INPUT_REQUIRED', 'FILE_NOT_GRANTED'} for e in errors):
                evidence.update(status='awaiting_input', issues=errors)
            elif any(e['code'] in {'MISSING_CONNECTION', 'CONNECTION_REQUIRED', 'CAPABILITY_BLOCKED', 'STALE_CONFIGURATION'} for e in errors):
                evidence.update(status='awaiting_authorization', issues=errors)
            elif any(e['code'] == 'OUTCOME_UNKNOWN' for e in errors):
                evidence.update(status='unknown', issues=errors)
            if self._runtime(owner, prepared) != evidence['runtimeContextDigest']:
                fail('STALE_CONFIGURATION', '验证期间运行条件改变，原结果不能准入。', 409)
        except BaseException as error:
            evidence.update(status='cancelled' if stopped.is_set() else 'interrupted',
                            issues=[{'code': getattr(error, 'code', 'EXECUTION_INTERRUPTED'), 'message': str(error)[:3000]}])
            error.authoringEvidence = evidence
            raise
        finally:
            evidence['completedAt'] = now()
            evidence['caseResults'] = copy.deepcopy(evidence['cases'])
            self.library.store.put(owner, 'authoring_evidence', evidence['id'], evidence)
        return evidence

    def _execute(self, owner, prepared, case, stopped):
        material = prepared['material']
        if prepared['kind'] == 'app':
            return self._execute_app(owner, prepared, case, stopped)
        snapshot = material['snapshot']
        files = self.library.file_grants(owner, case.get('fileRefs', []), snapshot['manifest'])
        generated, _, _ = self._fixtures(owner, case)
        if generated and not snapshot['manifest']['permissions']['inputFiles']:
            fail('FILE_NOT_GRANTED', '候选没有声明输入文件权限。', 403)
        if files.keys() & generated.keys():
            fail('TEST_FIXTURE_INVALID', '合成文件名称与授权文件冲突。', 422)
        files.update(generated)
        if prepared['kind'] == 'tool':
            validate_value(case['input'], snapshot['manifest']['tool']['inputSchema'])
            output = components.invoke_snapshot(self.library, owner, snapshot, case['input'], files=files,
                stopped=stopped, bindings=material['runtimeBindings'], context={'parentRunId': uid('candidate')})
            if output.get('status') != 'succeeded':
                error_code = output.get('error', {}).get('code') if isinstance(output.get('error'), dict) else None
                fail(error_code if error_code in {'BUDGET_EXHAUSTED', 'AUTHORING_DEADLINE'} else
                     'OUTCOME_UNKNOWN' if output.get('status') == 'unknown' else 'TOOL_FAILED',
                     str(output.get('error') or '工具候选没有成功交付。'), 422,
                     {'invocationId': output.get('invocationId'), 'status': output.get('status')})
            result = output.get('result', {})
            result = result.get('content', result) if isinstance(result, dict) else result
            return {'result': result, 'files': output.get('files', {}), 'artifacts': output.get('artifacts', []),
                    'executionRef': {'invocationId': output['invocationId']}}
        delivery = next((c['payload'] for c in case['checks'] if c['mode'] == 'delivery'), {})
        output = self.w.sub_agents.run_skill(owner, snapshot, case.get('task') or snapshot['manifest']['description'],
            case['input'], delivery, parent_run_id=uid('candidate'), node_id=case['id'], stopped=stopped,
            files=files, bindings=material['runtimeBindings'])
        if output.get('status') != 'succeeded':
            fail('AUTHORING_INPUT_REQUIRED' if output.get('status') == 'awaiting_user' else 'SKILL_FAILED',
                 str(output.get('question') or output.get('error') or 'Skill未完成交付。'), 422,
                 {'skillCallId': output.get('skillCallId'), 'status': output.get('status')})
        with self.apps.store.transaction() as db:
            artifacts = self.apps._artifacts(db, owner, output.get('skillCallId', uid('candidate')), case['id'], output.get('files', {}))
        return {**output, 'artifacts': artifacts, 'executionRef': {'skillCallId': output.get('skillCallId')}}

    def _execute_app(self, owner, prepared, case, stopped):
        material = prepared['material']
        doc = material['definition']
        version = {'id': 'candidate_' + prepared['contentDigest'], 'appId': 'candidate', 'issues': [],
                   'definition': doc, 'definitionDigest': digest(doc), 'runtimeBindings': material['runtimeBindings'],
                   'verification': {'contextDigest': prepared['runtimeContextDigest']}}
        generated, fixture_meta, fixture_aliases = self._fixtures(owner, case)
        validate_value(case['input'], doc['inputSchema'])
        run = self.apps._new_run(owner, version, case, 'run' if generated else 'verification')
        actual_input_files = copy.deepcopy(run['inputFiles'])
        if set(fixture_aliases) & set(run['fileBindings']):
            fail('TEST_FIXTURE_INVALID', '同一输入别名不能同时使用原文件和合成文件。', 422)
        run['fileBindings'].update(fixture_aliases)
        run['inputFiles'].extend(fixture_meta)
        missing = [b['input'] for n in doc['nodes'] for b in n['inputBindings'].values()
                   if b['from'] == 'file' and b['input'] not in run['fileBindings']]
        if missing:
            fail('INPUT_REQUIRED', '测试仍缺少文件输入别名：' + ', '.join(sorted(set(missing))), 422)
        run['inputDigest'] = digest({'input': run['input'], 'fileBindings': run['fileBindings'], 'files': run['inputFiles']})
        run['mode'] = 'verification'
        run['waiting'] = None
        run['status'] = 'running'
        self.library.store.put(owner, 'authoring_candidate_run', run['id'], run)
        produced = {}
        try:
            uploaded = self.w.platform._load_files(owner, actual_input_files)
            uploaded.update({meta['id']: generated[meta['name']] for meta in fixture_meta})
            for index, node in enumerate(doc['nodes']):
                _check(stopped)
                arguments, files = self.apps._resolve(owner, run, node['inputBindings'], uploaded)
                from ..sub_agent.evidence import clean
                if not clean(arguments)[1]:
                    fail('INPUT_CONTAINS_CREDENTIAL', '测试参数包含凭据，请使用连接配置。', 403)
                attempt = uid('attempt')
                step = run['steps'][index]
                step.update(status='running', attemptId=attempt, resolvedInput=arguments,
                            inputDigests={key: digest(raw) for key, raw in files.items()}, startedAt=now())
                self.library.store.put(owner, 'authoring_candidate_run', run['id'], run)
                output = self.apps._execute(owner, run, node, arguments, files, stopped, attempt, candidate_definition=doc)
                _check(stopped)
                if output.get('pause'):
                    fail('AUTHORING_INPUT_REQUIRED', str(output.get('question') or 'Skill需要补充业务信息。'), 422)
                delivery_check(output.get('result'), output.get('files', {}), node['delivery'])
                with self.apps.store.transaction() as db:
                    artifacts = self.apps._artifacts(db, owner, run['id'], node['id'], output.get('files', {}))
                step.update(status='succeeded', result=output['result'], resultDigest=digest(output['result']),
                            artifacts=artifacts, completedAt=now())
                for field in ('skillCallId', 'invocationId'):
                    if output.get(field): step[field] = output[field]
                run['artifacts'].extend(artifacts)
                produced.update(output.get('files', {}))
                self.library.store.put(owner, 'authoring_candidate_run', run['id'], run)
            result = (self.apps._resolve(owner, run, doc['outputs'], uploaded, final=True)[0] if doc['outputs'] else
                      {s['nodeId']: s['result'] for s in run['steps']})
            run.update(status='succeeded', result=result)
            return {'result': result, 'files': produced, 'artifacts': run['artifacts'], 'executionRef': {'candidateRunId': run['id']}}
        except BaseException as error:
            error.candidateRunId = run['id']
            run.update(status='cancelled' if stopped.is_set() else 'failed',
                       error={'code': getattr(error, 'code', 'EXECUTION_FAILED'), 'message': str(error)[:3000]})
            for step in run['steps']:
                if step['status'] == 'running': step.update(status=run['status'], error=run['error'])
            raise
        finally:
            run['completedAt'] = now()
            self.library.store.put(owner, 'authoring_candidate_run', run['id'], run)

    def _check_result(self, prepared, case, check, output, stopped):
        mode, payload, result = check['mode'], check['payload'], output.get('result')
        if mode == 'manual_review':
            return {'status': 'awaiting_review', 'criteria': payload['criteria']}
        if mode == 'json_equals': ok = _equal(result, payload['value'])
        elif mode == 'fields': ok = all(_equal(at(result, p), v) for p, v in payload['values'].items())
        elif mode == 'assertions': ok = not evaluate(result, payload['rules'])
        elif mode == 'delivery':
            delivery_check(result, output.get('files', {}), payload)
            ok = True
        else:
            content = prepared['content']
            info = content if prepared['kind'] == 'app' else content['manifest']
            chosen = self._model('按名称和说明判断此能力是否适用于用户任务，仅返回JSON {selected:布尔,reason:理由}。不执行任务。',
                {'task': case['task'], 'candidate': {k: info.get(k) for k in ('name', 'description')}}, stopped)
            ok = type(chosen.get('selected')) is bool and chosen['selected'] == payload['expected']
        return {'status': 'passed' if ok else 'failed'}

    def commit(self, owner, prepared, evidence, *, key, target=None, review=None):
        """Each asset database commits version/evidence/enable/idempotency in one transaction.

        Retrying the same key after the separate session ledger loses its response
        returns the same target; no second version or execution is created.
        """
        if prepared.get('kind') == 'app':
            fail('VERIFICATION_REQUIRED', 'APP 必须先保存为未验证版本，再在应用详情中验证并启用。', 409)
        self.w.auth.get_user(owner)
        self._intact(prepared)
        if not isinstance(key, str) or not 1 <= len(key) <= 150:
            fail('IDEMPOTENCY_REQUIRED', '提交需要稳定请求标识。')
        if not isinstance(review, dict) or review.get('accepted') is not True or not review.get('reviewDigest'):
            fail('REVIEW_REQUIRED', '请审阅当前候选及实际检查结果后确认。', 409)
        trusted = self.library.store.get(owner, 'authoring_evidence', evidence.get('adapterEvidenceId', evidence.get('id')))
        core = ('cases', 'status', 'contentDigest', 'executionDigest', 'materialDigest', 'runtimeContextDigest', 'executionMode', 'reuse')
        if any(trusted.get(k) != evidence.get(k) for k in core) or trusted['status'] not in {'passed', 'awaiting_review'}:
            fail('VERIFICATION_REQUIRED', '缺少服务器保存的当前候选成功检查证据。', 409)
        for field in ('contentDigest', 'executionDigest', 'materialDigest', 'runtimeContextDigest'):
            if trusted.get(field) != prepared.get(field):
                fail('CANDIDATE_CHANGED', '验证证据不属于当前候选。', 409)
        fingerprint = digest({'content': prepared['contentDigest'], 'material': prepared['materialDigest'],
                              'evidence': digest(trusted), 'target': target, 'review': review})
        if prepared['kind'] == 'app':
            with self.apps.store.transaction() as db:
                result, _ = self.apps.store.request(db, owner, 'authoring.commit', key, {'fingerprint': fingerprint},
                    lambda: self._commit_app(db, owner, prepared, trusted, target, review))
            return result
        with self.library.store.transaction():
            old = self.library.store.get(owner, 'authoring_commit', key, {})
            if old:
                if old['fingerprint'] != fingerprint:
                    fail('IDEMPOTENCY_CONFLICT', '提交标识已用于其他内容。', 409)
                return old['result']
            result = self._commit_component(owner, prepared, trusted, target, review)
            self.library.store.put(owner, 'authoring_commit', key, {'fingerprint': fingerprint, 'result': result})
            return result

    def _commit_app(self, db, owner, prepared, evidence, target, review):
        if self._runtime(owner, prepared) != evidence['runtimeContextDigest']:
            fail('STALE_CONFIGURATION', '运行条件已变化，请重新验证。', 409)
        if target:
            app = self.apps.store.get(db, 'apps', owner, target['assetId'])
            if app['revision'] != target.get('baseRevision') or app['latestVersionId'] != target.get('baseVersionId'):
                fail('REVISION_CONFLICT', '正式资产已变化，请重新整理修改。', 409)
        else:
            app = {'id': uid('app'), 'createdAt': now(), 'revision': 0, 'enabledVersionId': None}
        doc = prepared['material']['definition']
        vid = uid('av')
        verification = {'id': evidence['id'], 'origin': 'authoring_candidate', 'status': 'passed', 'cases': evidence['cases'],
                        'runIds': [], 'contextDigest': evidence['runtimeContextDigest'], 'review': review,
                        'candidateContentDigest': prepared['contentDigest'], 'candidateEvidenceId': evidence['id']}
        version = {'id': vid, 'appId': app['id'], 'number': app['revision'] + 1, 'definition': doc,
                   'definitionDigest': digest(doc), 'issues': [], 'createdAt': now(), 'verification': verification,
                   'runtimeBindings': prepared['material']['runtimeBindings']}
        app.update(name=doc['name'], description=doc['description'], latestVersionId=vid, enabledVersionId=vid,
                   revision=app['revision'] + 1)
        self.apps.store.put(db, 'versions', owner, version)
        self.apps.store.put(db, 'apps', owner, app)
        return {'assetId': app['id'], 'versionId': vid}

    def _commit_component(self, owner, prepared, evidence, target, review):
        if self._runtime(owner, prepared) != evidence['runtimeContextDigest']:
            fail('STALE_CONFIGURATION', '运行条件已变化，请重新验证。', 409)
        value = prepared['material']['snapshot']
        manifest, files = value['manifest'], components._files(value)
        if target:
            asset = self.library.asset(owner, target['assetId'])
            if asset['revision'] != target.get('baseRevision') or asset.get('latestVersionId') != target.get('baseVersionId'):
                fail('REVISION_CONFLICT', '正式资产已变化，请重新整理修改。', 409)
            if asset.get('deletedAt') or asset['lifecycle'] == 'archived' or asset['kind'] != prepared['kind'] or asset['sourceIdentity'] != manifest['source']['identity']:
                fail('INVALID_PACKAGE', '正式资产已删除、归档或类型/来源不一致。', 409)
        else:
            asset = {'assetId': uid('asset'), 'kind': prepared['kind'], 'sourceIdentity': manifest['source']['identity'],
                     'scope': 'personal', 'lifecycle': 'active', 'revision': 0, 'createdAt': now()}
        raw = canonical(value).encode()
        self.library.store.blob(owner, digest(raw), raw)
        for data in files.values(): self.library.store.blob(owner, digest(data), data)
        version = {'versionId': uid('ver'), 'assetId': asset['assetId'], 'number': 1 + len([v for v in self.library.store.list(owner, 'version') if v['assetId'] == asset['assetId']]),
                   'contentDigest': value['contentDigest'], 'manifest': manifest, 'originalId': digest(raw),
                   'originalName': 'component.json', 'createdAt': now(), 'containsSecret': False,
                   'embeddedSnapshot': value, 'validationPolicy': 'original_and_new',
                   'componentBindings': prepared['material']['runtimeBindings'],
                   'authoringProvenance': {'historicalEvidenceVerified': False, 'candidateContentDigest': prepared['contentDigest']}}
        from ..capability_library.test_cases import prepare_test_cases
        frozen_plan = self.library.store.get(owner, 'authoring_plan', evidence['id'], {})
        cases = self._saved_component_cases(prepared['kind'], frozen_plan)
        version['testCases'] = prepare_test_cases(manifest, files,
            cases or prepared['content'].get('testCases'))
        version = self.library.check(owner, version)
        if version['issues']:
            fail('VERIFICATION_REQUIRED', '候选提交前运行检查已变化。', 409, {'issues': version['issues']})
        record = {**copy.deepcopy(evidence), 'verificationId': uid('verification'), 'versionId': version['versionId'],
                  'assetId': asset['assetId'], 'configurationId': version['configurationId'],
                  'contextDigest': self.library.context_digest(version), 'status': 'passed', 'origin': 'validation_suite',
                  'authoringCandidateEvidenceId': evidence['id'], 'candidateRuntimeContextDigest': evidence['runtimeContextDigest'], 'review': review}
        self.library.store.put(owner, 'verification', record['verificationId'], record)
        version.update(readiness='verified', runtimeReadiness='verified')
        self.library.store.put(owner, 'version', version['versionId'], version)
        asset.update(name=manifest['name'], description=manifest['description'], latestVersionId=version['versionId'],
                     activeVersionId=version['versionId'], enabled=True, explicitlyDisabled=False,
                     readiness='verified', issues=[], revision=asset['revision'] + 1)
        self.library.store.put(owner, 'asset', asset['assetId'], asset)
        return {'assetId': asset['assetId'], 'versionId': version['versionId']}

    @staticmethod
    def _saved_component_cases(kind, plan):
        """Keep pre-execution expectations; actual evidence is never a template."""
        saved = []
        for case in plan.get('cases', []):
            if case.get('role') != 'original':
                continue
            checks = case.get('checks', [])
            if checks and all(check.get('mode') == 'matching' for check in checks):
                # A rename's discovery check is not a replacement business
                # example. Keep the original frozen case from the content.
                continue
            expectation = None
            if kind == 'skill':
                criteria = '\n'.join(check['payload'].get('criteria') or check.get('requirement', '')
                    for check in checks if isinstance(check.get('payload'), dict))
                expectation = {'mode': 'manual_review', 'prompt': case.get('task', ''), 'criteria': criteria}
            else:
                exact = next((check['payload'] for check in checks if check.get('mode') == 'json_equals'), None)
                assertions = []
                for check in checks:
                    payload = check.get('payload', {})
                    if check.get('mode') == 'assertions':
                        assertions.extend(copy.deepcopy(payload.get('rules', [])))
                    elif check.get('mode') == 'fields':
                        assertions.extend({'path': path.split('.') if path else [], 'op': 'eq', 'value': value}
                            for path, value in payload.get('values', {}).items())
                if exact is not None:
                    expectation = {'mode': 'json_equals', 'value': copy.deepcopy(exact['value'])}
                elif assertions:
                    expectation = {'mode': 'assertions', 'assertions': assertions}
            # Authoring fixtures live in a private temporary namespace, not the
            # file pool. Re-selection is required before independently testing.
            if case.get('fileFixtures'):
                expectation = None
            saved.append({'label': case.get('label') or '原任务用例',
                'input': copy.deepcopy(case['input']), 'fileIds': list(case.get('fileRefs', [])),
                'expectation': expectation})
        return saved
