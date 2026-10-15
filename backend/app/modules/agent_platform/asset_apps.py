"""Saved App implementations: immutable versions, checked delivery and durable runs."""
import base64
import copy
import json
import threading
from pathlib import Path

from ..capability_library.contracts import CapabilityError, canonical, digest, fail, now, uid
from ..sub_agent.media import artifact_media_type
from ..sub_agent.diagnostics import bounded_record
from .asset_contracts import at, definition, delivery_check, diff, normalize_test_cases, resource_files, validate_value, shareable_definition
from .asset_store import AssetStore
from .executor import _safe_name

AUTHORIZATION_ERRORS = {'CONNECTION_REQUIRED', 'CONNECTION_UNAVAILABLE', 'CAPABILITY_BLOCKED',
                        'CONFIGURATION_REQUIRED', 'BINDING_REQUIRED', 'MISSING_CONNECTION', 'STALE_CONFIGURATION'}


class AssetApps:
    def __init__(self, workbench, config):
        self.w = workbench
        self.store = AssetStore(workbench.platform.repository)
        self.config = config
        self.lock = threading.RLock()
        self.active = {}
        self.closed = False
        with self.store.transaction() as db:
            recovered = [(row['owner'], json.loads(row['doc'])) for row in
                         db.execute('SELECT owner,doc FROM asset_runs').fetchall()]
        for owner, run in recovered:
            if run.get('mode') == 'verification' and run['status'] not in {'queued', 'running'}:
                self._verification_result(owner, run['id'])

    def _freeze(self, owner, doc, previous=None):
        from ..capability_library.components import build_snapshot, validate_snapshot, snapshot_from_package
        doc = copy.deepcopy(doc)
        previous_nodes = {n['id']: n for n in (previous or {}).get('nodes', [])}
        sources = {}
        for node in doc.get('nodes', []):
            if node.get('componentRef'):
                ref = node.pop('componentRef')
                if not isinstance(ref, dict) or not isinstance(ref.get('assetId'), str) or not isinstance(ref.get('versionId'), str) or not ref['assetId'] or not ref['versionId']:
                    fail('INVALID_COMPONENT_REF', '组件引用需要本人资产ID和精确版本ID。', 422)
                node['component'] = build_snapshot(self.w.capabilities, owner, ref['assetId'], ref['versionId'])
            if node.get('component'):
                component = node['component']
                if node.get('provenance', {}).get('type') == 'modified':
                    try:
                        files = {path: base64.b64decode(raw, validate=True) for path, raw in component['files'].items()}
                    except (TypeError, ValueError, KeyError, AttributeError):
                        fail('INVALID_SNAPSHOT', '修改后的资源编码无效。', 422)
                    node['component'] = snapshot_from_package(component['manifest'], files,
                        component.get('dependencies', []), component.get('provenance'))
                else:
                    validate_snapshot(component)
            provenance = node.get('provenance', {})
            if not isinstance(provenance, dict):
                fail('INVALID_PROVENANCE', '步骤来源必须为对象。', 422)
            if provenance.get('type') == 'historical':
                implementation = lambda item: {k: v for k, v in item.items() if k != 'provenance'}
                prior = previous_nodes.get(node.get('id'))
                confirmed = bool(prior and prior.get('provenance', {}).get('sourceVerified')
                                 and all(prior['provenance'].get(k) == provenance.get(k) for k in ('runId', 'stepId'))
                                 and implementation(prior) == implementation(node))
                source_id = provenance.get('runId')
                if not confirmed and isinstance(source_id, str) and source_id:
                    selected = [n.get('provenance', {}).get('stepId') for n in doc['nodes']
                                if n.get('provenance', {}).get('runId') == source_id and n.get('provenance', {}).get('stepId')]
                    origin = doc.get('source', {})
                    if not isinstance(origin, dict):
                        selected = []
                    elif origin.get('runId') == source_id and isinstance(origin.get('selectedStepIds'), list):
                        selected = origin['selectedStepIds']
                    selected = selected if all(isinstance(key, str) for key in selected) else []
                    source_key = (source_id, tuple(dict.fromkeys(selected)))
                    if source_key not in sources:
                        from .asset_creator import _capture
                        try:
                            sources[source_key] = _capture(self, owner, source_id, list(source_key[1]))['nodes']
                        except Exception:
                            sources[source_key] = []
                    confirmed = any(n.get('provenance', {}).get('type') == 'historical' and
                                    n.get('provenance', {}).get('stepId') == provenance.get('stepId') and
                                    implementation(n) == implementation(node) for n in sources[source_key])
                node['provenance'] = {**provenance, 'sourceVerified': confirmed,
                    'type': 'historical' if confirmed else 'modified',
                    **({} if confirmed else {'description': '未能匹配当前账号的原始成功实现，按修改后的草稿保留，须重新验证。'})}
            else:
                provenance_type = provenance.get('type')
                node['provenance'] = {**provenance, 'type': provenance_type if isinstance(provenance_type, str)
                    and provenance_type in {'modified', 'generated'} else 'generated', 'sourceVerified': False}
        return doc

    def detail(self, owner, app_id, version_id=None):
        app = self.store.read('apps', owner, app_id)
        version = self.store.read('versions', owner, version_id or app['latestVersionId'])
        if version['appId'] != app_id:
            fail('NOT_FOUND', '应用版本不存在。', 404)
        versions = sorted(self.store.listing('versions', owner, app_id), key=lambda v: v['number'], reverse=True)
        return {**app, 'name': version['definition']['name'], 'description': version['definition']['description'],
                'versionId': version['id'], 'definition': version['definition'],
                'testCases': self._version_test_cases(version),
                'definitionDigest': version['definitionDigest'], 'issues': version['issues'],
                'authoringProvenance': version.get('authoringProvenance'),
                'runtimeBindings': version.get('runtimeBindings', {}),
                'bindingRequirements': self.binding_requirements(owner, version['definition']),
                'verification': version['verification'],
                'versions': [{k: v[k] for k in ('id', 'number', 'createdAt', 'definitionDigest', 'verification')}
                             | {'authoringProvenance': v.get('authoringProvenance')} for v in versions]}

    @staticmethod
    def _version_test_cases(version):
        """Recover old, independently supplied verification cases when present.

        Historical actual results and run status never become an expected value.
        """
        doc = version['definition']
        if doc.get('testCases'):
            return copy.deepcopy(doc['testCases'])
        schema = doc.get('inputSchema', {})
        recorded = version.get('verification', {}).get('cases') or []
        if isinstance(recorded, list) and recorded:
            cases = []
            for case in recorded[:10]:
                if not isinstance(case, dict) or not isinstance(case.get('input'), dict):
                    continue
                original = {key: copy.deepcopy(case[key]) for key in
                            ('label', 'input', 'fileBindings', 'expectation') if key in case}
                try:
                    cases.extend(normalize_test_cases([original], schema))
                except CapabilityError:
                    continue
            if cases:
                return cases
        return normalize_test_cases(None, schema)

    def binding_requirements(self, owner, doc):
        found = {}
        def visit(component, node_id):
            manifest = component.get('manifest', {})
            execution = manifest.get('tool', {}).get('execution', {})
            if execution.get('type') == 'mcp_http':
                identity = execution.get('connectionIdentity')
                if identity:
                    entry = found.setdefault(identity, {'sourceIdentity': identity,
                        'name': manifest.get('name') or identity, 'nodeIds': [], 'options': []})
                    if node_id not in entry['nodeIds']:
                        entry['nodeIds'].append(node_id)
            for dependency in component.get('dependencies', []):
                visit(dependency, node_id)
        for node in doc.get('nodes', []):
            if node.get('component'):
                visit(node['component'], node['id'])
        for connection in self.w.capabilities.store.list(owner, 'connection'):
            if connection.get('sourceIdentity') in found:
                found[connection['sourceIdentity']]['options'].append({
                    'connectionId': connection['connectionId'], 'name': connection.get('name', ''),
                    'configured': bool(connection.get('configured')), 'enabled': connection.get('enabled') is not False})
        return list(found.values())

    def configure(self, owner, app_id, body, key):
        with self.store.transaction() as db:
            def action():
                app = self.store.get(db, 'apps', owner, app_id)
                self._revision(app, body)
                version = self.store.get(db, 'versions', owner, body.get('versionId'))
                if version['appId'] != app_id:
                    fail('NOT_FOUND', '版本不属于应用。', 404)
                for row in db.execute('SELECT doc FROM asset_runs WHERE owner=? AND app=?', (owner, app_id)):
                    run = json.loads(row['doc'])
                    if run['versionId'] == version['id'] and run['status'] not in {'succeeded', 'cancelled', 'failed', 'interrupted'}:
                        fail('RUN_ACTIVE', '请先取消本版本未结束的运行，再更改连接。', 409)
                bindings = body.get('bindings')
                if not isinstance(bindings, dict):
                    fail('INVALID_BINDINGS', '请选择本人连接。')
                allowed = {r['sourceIdentity']: {c['connectionId'] for c in r['options']}
                           for r in self.binding_requirements(owner, version['definition'])}
                for identity, cid in bindings.items():
                    if not isinstance(cid, str) or cid not in allowed.get(identity, set()):
                        fail('INVALID_BINDINGS', '连接不属于本人或与所需来源不符。', 403)
                version['runtimeBindings'] = copy.deepcopy(bindings)
                version['verification'] = {'status': 'unverified', 'cases': [], 'runIds': []}
                if app['enabledVersionId'] == version['id']:
                    app['enabledVersionId'] = None
                app['revision'] += 1
                self.store.put(db, 'apps', owner, app)
                self.store.put(db, 'versions', owner, version)
                return {'versionId': version['id']}
            result, _ = self.store.request(db, owner, 'app.bindings:' + app_id, key, body, action)
        return self.detail(owner, app_id, result['versionId'])

    def listing(self, owner):
        return [self.detail(owner, app['id']) for app in sorted(self.store.listing('apps', owner),
                                                             key=lambda a: a['updatedAt'], reverse=True)]

    def preview(self, owner, body):
        supplied = copy.deepcopy(body.get('definition'))
        if isinstance(supplied, dict) and 'testCases' in body:
            supplied['testCases'] = body['testCases']
        doc, issues = definition(supplied)
        before = self.detail(owner, body['appId'], body.get('baseVersionId'))['definition'] if body.get('appId') else {}
        return {'definition': doc, 'issues': issues, 'diff': diff(before, doc),
                'warnings': ['草稿保存不代表验证通过；源码、参数和步骤连接需审阅。']}

    def _save_in_transaction(self, db, owner, body, *, provenance=None, authoring=None):
        """Persist one private version inside an existing platform transaction.

        Community copying uses this path so the private copy and its trusted
        release linkage commit together. ``provenance`` and ``authoring`` are
        server supplied, never read from an HTTP save body.
        """
        supplied = copy.deepcopy(body.get('definition'))
        if isinstance(supplied, dict) and 'testCases' in body:
            supplied['testCases'] = body['testCases']
        draft, _ = definition(supplied)
        bindings = body.get('bindings', {})
        if not isinstance(bindings, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in bindings.items()):
            fail('INVALID_BINDINGS', '连接绑定必须为来源名称到本人连接标识的映射。')
        if body.get('appId'):
            if provenance is not None:
                fail('INVALID_REQUEST', '公开副本不能覆盖已有应用。')
            app = self.store.get(db, 'apps', owner, body['appId'])
            self._revision(app, body)
            previous_version = self.store.get(db, 'versions', owner, app['latestVersionId'])
            previous = previous_version['definition']
        else:
            app = {'id': uid('app'), 'createdAt': now(), 'revision': 0, 'enabledVersionId': None}
            if provenance is not None:
                app['provenance'] = copy.deepcopy(provenance)
            previous = None
            previous_version = {}
        doc, issues = definition(self._freeze(owner, draft, previous))
        if provenance is not None:
            for case in doc['testCases']:
                case['fileBindings'] = {}
        vid = uid('av')
        app.update(name=doc['name'], description=doc.get('description', ''), latestVersionId=vid,
                   revision=app['revision'] + 1)
        version = {'id': vid, 'appId': app['id'], 'number': app['revision'], 'definition': doc,
                   'definitionDigest': digest(doc), 'issues': issues, 'createdAt': now(),
                   'verification': {'status': 'unverified', 'cases': [], 'runIds': []},
                   'runtimeBindings': copy.deepcopy(body.get('bindings', previous_version.get('runtimeBindings', {})))}
        if authoring is not None:
            version['authoringProvenance'] = copy.deepcopy(authoring)
        self.store.put(db, 'apps', owner, app)
        self.store.put(db, 'versions', owner, version)
        return {'appId': app['id'], 'versionId': vid}

    def save(self, owner, body, key):
        with self.store.transaction() as db:
            result, _ = self.store.request(db, owner, 'app.save', key, body,
                                           lambda: self._save_in_transaction(db, owner, body))
        return self.detail(owner, result['appId'], result['versionId'])

    def save_legacy_copy(self, owner, doc, source, key):
        """Create one private unverified App for a trusted legacy publication.

        This stable request survives a failure in the separate evolution store:
        the same publication copy retry returns the original modern version.
        """
        with self.store.transaction() as db:
            result, _ = self.store.request(db, owner, 'app.legacy.copy', key,
                {'definition': doc, 'source': source},
                lambda: self._save_in_transaction(db, owner, {'definition': doc}, provenance=source))
        return result

    def creator(self, owner, body, key):
        from .asset_creator import create
        save_key = 'creator-' + digest([key, body])
        with self.store.transaction() as db:
            reservation, fresh = self.store.request(db, owner, 'app.creator', key, body,
                                                   lambda: {'state': 'creating'})
        if not fresh:
            if reservation.get('state') == 'creating':
                fail('CREATOR_RUNNING', '该创作请求仍在进行，请稍后读取同一请求结果。', 409)
            if reservation.get('state') == 'failed':
                if reservation.get('error', {}).get('code') == 'CREATOR_INTERRUPTED':
                    with self.store.transaction() as db:
                        saved = db.execute('SELECT result FROM asset_requests WHERE owner=? AND scope=? AND key=?',
                                           (owner, 'app.save', save_key)).fetchone()
                    if saved:
                        previous = json.loads(saved['result'])
                        detail = self.detail(owner, previous['appId'], previous['versionId'])
                        return {'definition': detail['definition'], 'issues': detail['issues'],
                                'assetId': previous['appId'], 'versionId': previous['versionId'],
                                'verification': 'unverified', 'enabled': False,
                                'warnings': ['原创作结果已保存；请在应用详情中继续修改与验证。']}
                failure = reservation['error']
                fail(failure['code'], failure['message'], failure['status'])
            return reservation['result']
        try:
            traces = []
            def capture(stage, payload):
                if stage == 'request':
                    traces.append({'id': uid('trace'), 'request': bounded_record(payload),
                                   'createdAt': now()})
            result = create(self, owner, body, on_trace=capture if body.get('kind', 'app') == 'app' else None)
            doc = result.get('definition') if isinstance(result, dict) else None
            if body.get('kind', 'app') == 'app' and isinstance(doc, dict) and not result.get('selectionRequired'):
                # A bare blank editor is not an App. Once the creator has a
                # substantive definition, persist it before returning control.
                if doc.get('nodes') or doc.get('rules') or doc.get('gaps') or doc.get('source'):
                    payload = {'definition': doc}
                    if body.get('appId'):
                        payload.update(appId=body['appId'], revision=body.get('revision'))
                    with self.store.transaction() as db:
                        provenance = {'kind': 'asset_creator', 'requestDigest': digest(body),
                                      'sourceRunId': body.get('sourceRunId'),
                                      'creatorTraceIds': [trace['id'] for trace in traces]}
                        saved, fresh_save = self.store.request(db, owner, 'app.save', save_key, payload,
                            lambda: self._save_in_transaction(db, owner, payload, authoring=provenance))
                        if fresh_save:
                            for trace in traces:
                                record = {**trace, 'appId': saved['appId'], 'versionId': saved['versionId']}
                                db.execute('INSERT INTO asset_creator_traces VALUES(?,?,?,?,?)',
                                           (owner, trace['id'], saved['appId'], saved['versionId'], canonical(record)))
                    result.update(assetId=saved['appId'], versionId=saved['versionId'],
                                  verification='unverified', enabled=False,
                                  detail='/desktop/api/asset-apps/' + saved['appId'])
            record = {'state': 'completed', 'result': result}
        except Exception as error:
            record = {'state': 'failed', 'error': {'code': getattr(error, 'code', 'CREATOR_FAILED'),
                      'message': str(error)[:3000], 'status': getattr(error, 'status', 502)}}
            raise
        finally:
            with self.store.transaction() as db:
                db.execute('UPDATE asset_requests SET result=? WHERE owner=? AND scope=? AND key=?',
                           (canonical(record), owner, 'app.creator', key))
        return result

    def creator_trace(self, owner, app_id, trace_id):
        self.store.read('apps', owner, app_id)
        with self.store.transaction() as db:
            row = db.execute('SELECT doc FROM asset_creator_traces WHERE owner=? AND app=? AND id=?',
                             (owner, app_id, trace_id)).fetchone()
        if row is None:
            fail('NOT_FOUND', '创作模型记录不存在或无权访问。', 404)
        return json.loads(row['doc'])

    @staticmethod
    def _revision(doc, body):
        if body.get('revision') != doc['revision']:
            fail('REVISION_CONFLICT', '内容或运行状态已变化，请刷新后重试。', 409,
                 {'revision': doc['revision']})

    def _context(self, owner, doc, bindings=None):
        from ..capability_library.components import runtime_digest
        components = [runtime_digest(self.w.capabilities, owner, n['component'], bindings=bindings)
                      for n in doc['nodes'] if n.get('component')]
        executor = self.w.platform.executor
        providers = [self.w.runtime.provider, self.w.platform.planner.provider]
        skill_provider = getattr(getattr(self.w.sub_agents.model, 'planner', None), 'provider', None)
        if skill_provider is not None:
            providers.append(skill_provider)
        model = [{'class': type(p).__name__, 'model': getattr(p, 'model', ''),
                  'base': getattr(p, 'base', ''), 'credential': digest(getattr(p, 'key', '')),
                  'options': getattr(p, 'request_options', {}),
                  'timeouts': [getattr(p, 'connect_timeout', None), getattr(p, 'read_timeout', None)]}
                 for p in providers]
        runtime = executor.status()
        implementation = {name: digest(Path(__file__).with_name(name).read_bytes()) for name in
                          ('executor.py', 'executor_worker.py', 'executor_guest.py')}
        modules = Path(__file__).parents[1]
        implementation.update({name: digest((modules / name).read_bytes()) for name in
            ('agent_platform/asset_apps.py', 'agent_platform/asset_contracts.py',
             'sub_agent/skill_runtime.py', 'sub_agent/service.py', 'sub_agent/model.py',
             'sub_agent/protocol.py', 'sub_agent/media.py',
             'capability_library/components.py', 'capability_library/fixed_pipeline.py')})
        runtime_definition = {key: value for key, value in doc.items() if key != 'testCases'}
        return digest({'definition': digest(runtime_definition), 'components': components,
                       'model': model, 'engine': implementation, 'availability': runtime,
                       'runtime': str(executor.runtime_dir), 'timeout': executor.timeout,
                       'memory': executor.memory_mb, 'fuel': executor.fuel,
                       'limits': [executor.output_limit, executor.input_limit, executor.max_files]})

    def _admit_run(self, owner, run, version, app=None):
        if run['mode'] == 'run':
            app = app or self.store.read('apps', owner, run['appId'])
            if app.get('enabledVersionId') != run['versionId']:
                fail('ENABLE_REQUIRED', '该运行版本已停用，请重新核对验证并启用。', 409)
            verification = version['verification']
            if verification.get('status') != 'passed':
                fail('VERIFICATION_REQUIRED', '该版本需要完成有效验证后才能继续。', 409)
        current = self._context(owner, version['definition'], run.get('runtimeBindings'))
        if current != run.get('contextDigest'):
            fail('NEW_RUN_REQUIRED', '运行条件已改变，请重新验证并开启新运行；旧结果保留。', 409)
        if run['mode'] == 'run' and current != version['verification'].get('contextDigest'):
            fail('VERIFICATION_REQUIRED', '运行条件已改变，请重新验证。', 409)

    def enable(self, owner, app_id, body, key):
        with self.store.transaction() as db:
            def action():
                app = self.store.get(db, 'apps', owner, app_id)
                self._revision(app, body)
                version = self.store.get(db, 'versions', owner, body.get('versionId'))
                if version['appId'] != app_id:
                    fail('NOT_FOUND', '版本不属于应用。', 404)
                if body.get('enabled') is True:
                    verification = version['verification']
                    if verification['status'] != 'passed' or verification.get('contextDigest') != self._context(owner, version['definition'], version.get('runtimeBindings')):
                        fail('VERIFICATION_REQUIRED', '当前版本及运行条件需要通过原任务用例验证。', 409)
                    app['enabledVersionId'] = version['id']
                elif body.get('enabled') is False:
                    app['enabledVersionId'] = None
                else:
                    fail('INVALID_REQUEST', 'enabled须为布尔值。')
                app['revision'] += 1
                self.store.put(db, 'apps', owner, app)
                return {'appId': app_id, 'versionId': version['id']}
            result, _ = self.store.request(db, owner, 'app.enable:' + app_id, key, body, action)
        return self.detail(owner, app_id, result['versionId'])

    def _new_run(self, owner, version, body, mode, verification_id=None):
        if version['issues']:
            fail('DEFINITION_INCOMPLETE', '请先补齐应用草稿中的缺项。', 422, {'issues': version['issues']})
        inputs = body.get('input', {})
        bindings = body.get('fileBindings', {})
        if not isinstance(inputs, dict) or not isinstance(bindings, dict) or any(not isinstance(v, str) for v in bindings.values()):
            fail('INPUT_INVALID', '参数和文件授权必须为对象。')
        from ..sub_agent.evidence import clean
        if not clean(inputs)[1]:
            fail('INPUT_CONTAINS_CREDENTIAL', '凭据应通过连接配置提供，不能作为普通运行参数。', 403)
        metadata = self.w.platform._metadata(owner, list(bindings.values()))
        doc = version['definition']
        missing = [k for k in doc['inputSchema'].get('required', []) if k not in inputs]
        missing += [b.get('input') for n in doc['nodes'] for b in n['inputBindings'].values()
                    if b.get('from') == 'file' and b.get('input') not in bindings]
        if not missing:
            validate_value(inputs, doc['inputSchema'])
        elif mode == 'verification':
            fail('INPUT_REQUIRED', '验证用例缺少必要输入。', 422, {'missing': sorted(set(missing))})
        run = {'id': uid('ar'), 'appId': version['appId'], 'versionId': version['id'],
               'definitionDigest': version['definitionDigest'], 'input': copy.deepcopy(inputs),
               'fileBindings': copy.deepcopy(bindings), 'inputFiles': metadata,
               'inputDigest': digest({'input': inputs, 'fileBindings': bindings, 'files': metadata}),
               'revision': 1, 'mode': mode, 'verificationId': verification_id,
               'runtimeBindings': version.get('runtimeBindings', {}),
               'contextDigest': version['verification'].get('contextDigest'),
               'status': 'awaiting_input' if missing else 'queued', 'createdAt': now(),
               'steps': [{'nodeId': n['id'], 'kind': n['kind'], 'title': n['title'], 'status': 'pending',
                          'artifacts': []} for n in doc['nodes']],
               'artifacts': [], 'result': None, 'error': None,
               'waiting': {'reason': '请补齐本次运行输入。', 'missing': sorted(set(missing))} if missing else None}
        return run

    def create_run(self, owner, app_id, body, key):
        with self.store.transaction() as db:
            def action():
                app = self.store.get(db, 'apps', owner, app_id)
                vid = body.get('versionId') or app['enabledVersionId']
                if not vid or app['enabledVersionId'] != vid:
                    fail('ENABLE_REQUIRED', '请先验证并启用该版本。', 409)
                version = self.store.get(db, 'versions', owner, vid)
                run = self._new_run(owner, version, body, 'run')
                try:
                    self._admit_run(owner, run, version, app)
                except Exception as error:
                    if getattr(error, 'code', '') not in AUTHORIZATION_ERRORS:
                        raise
                    run.update(status='awaiting_authorization', waiting={'reason': str(error), 'missing': []},
                               error={'code': error.code, 'message': str(error)})
                self.store.put(db, 'runs', owner, run)
                return {'runId': run['id']}
            result, fresh = self.store.request(db, owner, 'app.run:' + app_id, key, body, action)
        if fresh:
            self._launch(owner, result['runId'])
        return self.run(owner, result['runId'])

    def verify(self, owner, app_id, body, key):
        cases = body.get('cases')
        if not isinstance(cases, list) or not 1 <= len(cases) <= 10 or any(not isinstance(c, dict) for c in cases):
            fail('INVALID_CASES', '验证需要原任务用例，可选最多9组补充用例。')
        if len(cases) > 1 and len({digest({'input': c.get('input'), 'files': c.get('fileBindings', {})}) for c in cases}) < 2:
            fail('INVALID_CASES', '至少一组验证必须使用不同输入。')
        for case in cases:
            e = case.get('expectation', {})
            if not isinstance(e, dict) or not isinstance(e.get('mode'), str) or e.get('mode') not in {'json_equals', 'fields'} or (e.get('mode') == 'json_equals' and 'value' not in e) or (e.get('mode') == 'fields' and not isinstance(e.get('values'), dict)) or (e.get('mode') == 'fields' and not e['values']):
                fail('INVALID_EXPECTATION', '每组验证需要独立预期值或字段预期。')
        with self.store.transaction() as db:
            def action():
                app = self.store.get(db, 'apps', owner, app_id)
                if 'revision' in body:
                    self._revision(app, body)
                version = self.store.get(db, 'versions', owner, body.get('versionId') or app['latestVersionId'])
                if version['appId'] != app_id:
                    fail('NOT_FOUND', '版本不属于应用。', 404)
                if version['verification']['status'] == 'running':
                    fail('VERIFICATION_RUNNING', '当前版本已有验证在进行。', 409)
                verification_id = uid('check')
                runs = [self._new_run(owner, version, c, 'verification', verification_id) for c in cases]
                if len(runs) > 1 and len({digest({'input': r['input'], 'fileContents': sorted(f['sha256'] for f in r['inputFiles'])})
                        for r in runs}) < 2:
                    fail('INVALID_CASES', '新用例须改变实际输入，不能只重新上传同一内容。')
                version['verification'] = {'id': verification_id, 'status': 'running', 'cases': copy.deepcopy(cases),
                                           'contextDigest': self._context(owner, version['definition'], version.get('runtimeBindings')),
                                           'runIds': [r['id'] for r in runs]}
                app['revision'] += 1
                if app['enabledVersionId'] == version['id']:
                    app['enabledVersionId'] = None
                for run in runs:
                    run['contextDigest'] = version['verification']['contextDigest']
                    self.store.put(db, 'runs', owner, run)
                self.store.put(db, 'versions', owner, version)
                self.store.put(db, 'apps', owner, app)
                return {'versionId': version['id'], 'runs': [r['id'] for r in runs]}
            result, fresh = self.store.request(db, owner, 'app.verify:' + app_id, key, body, action)
        if fresh:
            for rid in result['runs']:
                self._launch(owner, rid)
        return self.detail(owner, app_id, result['versionId'])

    def run(self, owner, run_id):
        return self.store.read('runs', owner, run_id)

    def runs(self, owner, app_id):
        self.store.read('apps', owner, app_id)
        return sorted(self.store.listing('runs', owner, app_id), key=lambda r: r['createdAt'], reverse=True)

    def _launch(self, owner, rid):
        with self.lock:
            if self.closed or rid in self.active or self.run(owner, rid)['status'] != 'queued':
                return
            stopped = threading.Event()
            thread = threading.Thread(target=self._worker, args=(owner, rid, stopped), daemon=True,
                                      name='asset-app-' + rid)
            self.active[rid] = (stopped, thread)
            thread.start()

    def _resolve(self, owner, run, bindings, uploaded, *, final=False):
        values, files, aliases = {}, {}, {}
        for key, binding in bindings.items():
            source = binding['from']
            if source == 'literal':
                value = copy.deepcopy(binding['value'])
            elif source == 'input':
                value = at(run['input'], binding.get('path', ''))
            elif source == 'step':
                step = next(s for s in run['steps'] if s['nodeId'] == binding['stepId'])
                if step['status'] != 'succeeded':
                    fail('BINDING_MISSING', '前序步骤尚未成功交付。', 422)
                value = at(step['result'], binding.get('path', ''))
            else:
                if source == 'file':
                    identity = run['fileBindings'][binding['input']]
                    raw = uploaded[identity]
                    original = next(f['name'] for f in run['inputFiles'] if f['id'] == identity)
                else:
                    step = next(s for s in run['steps'] if s['nodeId'] == binding['stepId'])
                    if step['status'] != 'succeeded':
                        fail('BINDING_MISSING', '前序文件尚未成功交付。', 422)
                    meta = next((a for a in step['artifacts'] if a['name'] == binding['name']), None)
                    if meta is None:
                        fail('BINDING_MISSING', '前序步骤缺少文件：' + binding['name'], 422)
                    actual, raw = self.store.artifact(owner, meta['id'])
                    self._artifact_integrity(meta, actual)
                    if final:
                        values[key] = copy.deepcopy(meta)
                        continue
                    identity, original = meta['id'], meta['name']
                ext = '.' + original.rsplit('.', 1)[-1] if '.' in original else ''
                name = identity + ext if _safe_name(identity + ext) else identity
                files[name] = raw
                value = '/input/' + name
                aliases[key] = value
            values[key] = value
        return values, files

    def _execute(self, owner, run, node, arguments, files, stopped, attempt, *, candidate_definition=None):
        if node['kind'] == 'code':
            arguments = {**arguments}
            aliases = {key: arguments[key] for key, binding in node['inputBindings'].items()
                       if binding['from'] in {'file', 'artifact'}}
            if aliases:
                arguments.setdefault('files', aliases)
            resources = resource_files(node.get('files', []))
            if resources:
                resources['__app_entry__.py'] = node['code'].encode('utf-8')
            return self.w.platform.executor.execute(node['code'], arguments, files, stopped,
                **({'package_files': resources, 'package_entry': '__app_entry__.py'} if resources else {}))
        if node['kind'] == 'tool':
            from ..capability_library.components import invoke_snapshot
            result = invoke_snapshot(self.w.capabilities, owner, node['component'], arguments,
                                     files=files, stopped=stopped,
                                     bindings=run.get('runtimeBindings'),
                                     context={'parentRunId': run['id'], 'nodeId': node['id'], 'attemptId': attempt})
            if result.get('status') in {'failed', 'cancelled', 'unknown'}:
                fail('OUTCOME_UNKNOWN' if result['status'] == 'unknown' else 'TOOL_FAILED',
                     str(result.get('error') or '工具调用失败。'), 422,
                     {'invocationId': result.get('invocationId'), 'status': result['status']})
            content = result.get('result', {})
            return {'result': content.get('content', content) if isinstance(content, dict) else content,
                    'files': result.get('files', {}), 'invocationId': result.get('invocationId')}
        doc = candidate_definition if candidate_definition is not None else self.store.read('versions', owner, run['versionId'])['definition']
        task = (node.get('task') or node['title']) + '\n\n本App固定规则：\n' + '\n'.join(doc['rules'])
        previous = next(s for s in run['steps'] if s['nodeId'] == node['id'])
        response = self.w.sub_agents.run_skill(owner, node['component'], task,
            arguments, node['delivery'], parent_run_id=run['id'], node_id=node['id'], stopped=stopped,
            files=files, request_id=previous.get('skillRequestId') or attempt,
            answer=previous.get('skillAnswer'), bindings=run.get('runtimeBindings'))
        if response.get('status') == 'awaiting_user':
            return {**response, 'pause': True}
        if response.get('status') != 'succeeded':
            fail('SKILL_FAILED', str(response.get('error') or response.get('question') or 'Skill 未完成约定交付。'), 422,
                 {'skillCallId': response.get('skillCallId'), 'status': response.get('status')})
        return response

    def _worker(self, owner, rid, stopped):
        try:
            def begin(run, db):
                if run['status'] != 'queued':
                    fail('RUN_NOT_QUEUED', '运行不处于待执行状态。', 409)
                run.update(status='running', error=None, waiting=None)
            run = self.store.mutate_run(owner, rid, begin)
            version = self.store.read('versions', owner, run['versionId'])
            doc = version['definition']
            if digest(doc) != run['definitionDigest']:
                fail('DEFINITION_CHANGED', '保存的实现摘要不一致。', 409)
            self._input_integrity(run)
            uploaded = self.w.platform._load_files(owner, run['inputFiles'])
            self._admit_run(owner, run, version)
            for index, node in enumerate(doc['nodes']):
                run = self.run(owner, rid)
                if stopped.is_set() or run['status'] != 'running':
                    return
                if run['steps'][index]['status'] == 'succeeded':
                    continue
                arguments, files = self._resolve(owner, run, node['inputBindings'], uploaded)
                from ..sub_agent.evidence import clean
                if not clean(arguments)[1]:
                    fail('INPUT_CONTAINS_CREDENTIAL', '步骤参数包含凭据，请使用连接配置。', 403)
                attempt = uid('attempt')
                def start(current, db):
                    if current['status'] != 'running':
                        fail('CANCELLED', '运行已经停止。', 409)
                    current['steps'][index].update(status='running', attemptId=attempt, resolvedInput=arguments,
                        inputDigests={k: digest(v) for k, v in files.items()}, startedAt=now(), error=None)
                    if node['kind'] == 'skill':
                        current['steps'][index].setdefault('skillRequestId', attempt)
                self.store.mutate_run(owner, rid, start)
                run = self.run(owner, rid)
                output = self._execute(owner, run, node, arguments, files, stopped, attempt)
                if output.get('pause'):
                    def pause(current, db):
                        if stopped.is_set() or current['status'] != 'running':
                            return
                        current['steps'][index].update(status='awaiting_input', skillCallId=output['skillCallId'])
                        current.update(status='awaiting_input', waiting={'kind': 'skill_question', 'nodeId': node['id'],
                            'question': output['question'], 'reason': output['question']['prompt'], 'missing': []})
                    self.store.mutate_run(owner, rid, pause)
                    return
                result, produced = output.get('result'), output.get('files', {})
                delivery_check(result, produced, node['delivery'])
                def finish(current, db):
                    step = current['steps'][index]
                    if stopped.is_set() or current['status'] != 'running' or step.get('attemptId') != attempt:
                        fail('CANCELLED', '执行已停止，迟到结果不会覆盖。', 409)
                    artifacts = self._artifacts(db, owner, rid, node['id'], produced)
                    step.update(status='succeeded', result=result, resultDigest=digest(result), artifacts=artifacts,
                                completedAt=now(), outcomeUnknown=False)
                    for field in ('skillCallId', 'invocationId'):
                        if output.get(field):
                            step[field] = output[field]
                    current['artifacts'].extend(artifacts)
                    if node['checkpoint'] and current['mode'] == 'run':
                        current.update(status='awaiting_confirmation', waiting={'nodeId': node['id'],
                                       'reason': '请检查此步骤的实际结果后继续。', 'missing': []})
                run = self.store.mutate_run(owner, rid, finish)
                if run['status'] == 'awaiting_confirmation':
                    return
            run = self.run(owner, rid)
            result = self._resolve(owner, run, doc['outputs'], uploaded, final=True)[0] if doc['outputs'] else {
                s['nodeId']: s['result'] for s in run['steps']}
            def complete(current, db):
                if stopped.is_set() or current['status'] != 'running':
                    return
                current.update(status='succeeded', result=result, completedAt=now(), waiting=None)
            self.store.mutate_run(owner, rid, complete)
        except Exception as error:
            def failed(run, db):
                if run['status'] in {'cancelled', 'succeeded'}:
                    return
                code = getattr(error, 'code', 'EXECUTION_FAILED')
                run['status'] = 'failed'
                if code in AUTHORIZATION_ERRORS | {'ENABLE_REQUIRED', 'VERIFICATION_REQUIRED'}:
                    run.update(status='awaiting_authorization', waiting={'reason': str(error)[:1000], 'missing': []})
                run['error'] = {'code': code, 'message': str(error)[:3000], 'details': getattr(error, 'details', {})}
                for step in run['steps']:
                    if step['status'] == 'running':
                        step.update(status='failed', error=run['error'], completedAt=now(),
                                    outcomeUnknown=code in {'OUTCOME_UNKNOWN', 'timeout', 'TIMEOUT'} and step['kind'] in {'tool', 'skill'})
            self.store.mutate_run(owner, rid, failed)
        finally:
            try:
                self._verification_result(owner, rid)
            finally:
                with self.lock:
                    self.active.pop(rid, None)

    @staticmethod
    def _artifacts(db, owner, rid, node_id, files):
        if not isinstance(files, dict) or len(files) > 128 or sum(len(b) for b in files.values()) > 16 * 1024 * 1024:
            fail('OUTPUT_LIMIT', '输出数量或大小超过限制。', 422)
        result = []
        for name, raw in files.items():
            if not _safe_name(name) or not isinstance(raw, bytes):
                fail('OUTPUT_INVALID', '输出文件名或内容无效。', 422)
            aid = uid('aa')
            meta = {'id': aid, 'nodeId': node_id, 'name': name, 'size': len(raw), 'sha256': digest(raw),
                    'mediaType': artifact_media_type(name),
                    'downloadUrl': '/desktop/api/asset-apps/artifacts/' + aid}
            db.execute('INSERT INTO asset_artifacts VALUES(?,?,?,?,?)', (owner, aid, rid, canonical(meta), raw))
            result.append(meta)
        return result

    def _verification_result(self, owner, rid):
        with self.store.transaction() as db:
            run = self.store.get(db, 'runs', owner, rid)
            if run['mode'] != 'verification':
                return
            version = self.store.get(db, 'versions', owner, run['versionId'])
            verification = version['verification']
            if verification.get('id') != run['verificationId']:
                return
            runs = [self.store.get(db, 'runs', owner, key) for key in verification['runIds']]
            cases = verification['cases']
            for case, current in zip(cases, runs):
                case['runId'] = current['id']
                case['status'] = 'running' if current['status'] in {'queued', 'running'} else 'failed'
                if current['status'] == 'succeeded':
                    expectation = case['expectation']
                    try:
                        passed = current['result'] == expectation['value'] if expectation['mode'] == 'json_equals' else all(
                            at(current['result'], path) == value for path, value in expectation['values'].items())
                    except Exception:
                        passed = False
                    case.update(status='passed' if passed else 'failed', actual=current['result'])
                elif current.get('error'):
                    case['error'] = current['error']
            verification['status'] = 'running' if any(c['status'] == 'running' for c in cases) else (
                'passed' if all(c['status'] == 'passed' for c in cases) else 'failed')
            verification['updatedAt'] = now()
            self.store.put(db, 'versions', owner, version)

    def resume(self, owner, rid, body, key):
        with self.lock, self.store.transaction() as db:
            def action():
                run = self.store.get(db, 'runs', owner, rid)
                self._revision(run, body)
                if rid in self.active:
                    fail('RUN_SETTLING', '当前执行正在结束，请稍后继续。', 409)
                if run['status'] not in {'awaiting_confirmation', 'awaiting_input', 'awaiting_authorization', 'failed', 'interrupted'}:
                    fail('RESUME_INVALID', '该运行当前不能继续。', 409)
                self._input_integrity(run)
                if (run.get('waiting') or {}).get('kind') == 'skill_question':
                    answer = body.get('skillAnswer')
                    question = run['waiting']['question']
                    if not isinstance(answer, dict) or answer.get('questionId') != question['id'] or answer.get('revision') != question['revision'] or not isinstance(answer.get('answer'), str) or not answer['answer'].strip():
                        fail('INPUT_REQUIRED', '请回答当前Skill提出的具体问题。', 422)
                    step = next(s for s in run['steps'] if s['nodeId'] == run['waiting']['nodeId'])
                    step['skillAnswer'] = answer
                completed = any(s['status'] == 'succeeded' for s in run['steps'])
                if ('input' in body and body['input'] != run['input']) or ('fileBindings' in body and body['fileBindings'] != run['fileBindings']):
                    if completed:
                        fail('NEW_RUN_REQUIRED', '已有完成步骤时修改输入必须创建新运行。', 409)
                    version = self.store.get(db, 'versions', owner, run['versionId'])
                    amended = self._new_run(owner, version, {**run, **body}, run['mode'], run.get('verificationId'))
                    for k in ('input', 'inputDigest', 'fileBindings', 'inputFiles'):
                        run[k] = amended[k]
                    if amended['status'] == 'awaiting_input':
                        fail('INPUT_REQUIRED', '仍缺少必需输入。', 422, amended['waiting'])
                version = self.store.get(db, 'versions', owner, run['versionId'])
                checked = self._new_run(owner, version, run, run['mode'], run.get('verificationId'))
                if checked['status'] == 'awaiting_input':
                    fail('INPUT_REQUIRED', '仍缺少必需输入。', 422, checked['waiting'])
                if run['definitionDigest'] != digest(version['definition']):
                    fail('DEFINITION_CHANGED', '实现发生变化，不能接续。', 409)
                self.w.platform._load_files(owner, run['inputFiles'])
                self._admit_run(owner, run, version, self.store.get(db, 'apps', owner, run['appId']))
                for step in run['steps']:
                    if step['status'] == 'succeeded':
                        if digest(step['result']) != step['resultDigest']:
                            fail('RESULT_CHANGED', '前序结果已改变，不能复用。', 409)
                        for artifact in step['artifacts']:
                            actual, _ = self.store.artifact(owner, artifact['id'], db)
                            self._artifact_integrity(artifact, actual)
                    elif step.get('outcomeUnknown') and body.get('retryUnknown') is not True:
                        fail('OUTCOME_UNKNOWN', '外部动作结果未知，请核对后明确选择重新尝试。', 409)
                    elif step['status'] in {'failed', 'interrupted'}:
                        step.pop('skillRequestId', None)
                        step.pop('skillAnswer', None)
                run.update(status='queued', revision=run['revision'] + 1, waiting=None, error=None)
                self.store.put(db, 'runs', owner, run)
                return {'runId': rid}
            _, fresh = self.store.request(db, owner, 'app.resume:' + rid, key, body, action)
        if fresh:
            self._launch(owner, rid)
        return self.run(owner, rid)

    def cancel(self, owner, rid, body, key):
        with self.lock, self.store.transaction() as db:
            def action():
                run = self.store.get(db, 'runs', owner, rid)
                self._revision(run, body)
                if run['status'] not in {'succeeded', 'cancelled'}:
                    run.update(status='cancelled', revision=run['revision'] + 1, waiting=None)
                    for step in run['steps']:
                        if step['status'] == 'running':
                            step['status'] = 'cancelled'
                    self.store.put(db, 'runs', owner, run)
                return {'runId': rid}
            self.store.request(db, owner, 'app.cancel:' + rid, key, body, action)
            if rid in self.active:
                self.active[rid][0].set()
        self._verification_result(owner, rid)
        return self.run(owner, rid)

    @staticmethod
    def _export_definition(definition_doc):
        from ..capability_library.components import export_snapshot
        doc = copy.deepcopy(definition_doc)
        doc.pop('source', None)
        doc['testCases'] = normalize_test_cases(doc.get('testCases'), doc.get('inputSchema', {}))
        for case in doc['testCases']:
            # An account's file authorization IDs cannot travel in a package.
            case['fileBindings'] = {}
        for node in doc['nodes']:
            if node.get('component'):
                node['component'] = export_snapshot(node['component'])
            node['provenance'] = {'type': node.get('provenance', {}).get('type', 'generated'),
                                  'description': '随应用导出的实现；接收者须重新验证。'}
        shareable_definition(doc)
        return {'format': 'cd80.app-package/2', 'definition': doc, 'digest': digest(doc)}

    def export(self, owner, app_id, version_id=None):
        app = self.detail(owner, app_id, version_id)
        doc = copy.deepcopy(app['definition'])
        doc['testCases'] = app['testCases']
        return self._export_definition(doc)

    def import_preview(self, owner, body):
        from ..capability_library.components import validate_snapshot
        package = body.get('package', {})
        if not isinstance(package, dict) or package.get('format') != 'cd80.app-package/2' or package.get('digest') != digest(package.get('definition')):
            fail('INVALID_PACKAGE', '应用包格式或内容摘要不一致。')
        result = self.preview(owner, {'definition': package['definition']})
        # A private upload needs complete, valid component bytes, but it need
        # not satisfy the separate public-publishing secret/content policy.
        for node in result['definition']['nodes']:
            if node.get('kind') == 'code':
                if 'code' in node and not isinstance(node['code'], str):
                    fail('INVALID_DEFINITION', '步骤代码必须为文本。', 422)
                resource_files(node.get('files', []))
            if node.get('component'):
                validate_snapshot(node['component'])
        result['bindingRequirements'] = self.binding_requirements(owner, result['definition'])
        result['warnings'].append('接收者须配置自己的连接与授权，重新验证并启用。')
        return result

    def import_app(self, owner, body, key):
        from ..capability_library.components import snapshot_from_package, validate_snapshot
        def private_copy(snapshot):
            # Untrusted package provenance is metadata, never an admission
            # grant. Keep all implementation bytes and dependency closure.
            value = validate_snapshot(snapshot)
            files = {path: base64.b64decode(raw, validate=True) for path, raw in value['files'].items()}
            dependencies = [private_copy(child) for child in value['dependencies']]
            return snapshot_from_package(value['manifest'], files, dependencies,
                                         {'contentDigest': value['contentDigest']})
        doc = self.import_preview(owner, body)['definition']
        for node in doc['nodes']:
            if node.get('component'):
                node['component'] = private_copy(node['component'])
        payload = {'definition': doc, 'bindings': body.get('bindings', {})}
        if 'testCases' in body:
            payload['testCases'] = body['testCases']
        return self.save(owner, payload, key)

    @staticmethod
    def _artifact_integrity(expected, actual):
        if any(expected.get(key) != actual.get(key) for key in ('id', 'nodeId', 'sha256', 'size', 'name')):
            fail('ARTIFACT_CHANGED', '产物与步骤验收时的文件记录不同，不能复用。', 409)

    @staticmethod
    def _input_integrity(run):
        if run['inputDigest'] != digest({'input': run['input'], 'fileBindings': run['fileBindings'], 'files': run['inputFiles']}):
            fail('INPUT_CHANGED', '运行输入摘要不一致，不能复用历史结果。', 409)

    def close(self):
        with self.lock:
            self.closed = True
            jobs = list(self.active.values())
            for stopped, _ in jobs:
                stopped.set()
        for _, thread in jobs:
            thread.join(timeout=5)
