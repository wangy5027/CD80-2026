"""Host-owned, fixed-route read connections. Guests never receive a URL or secret."""
import time
from pathlib import Path
from jsonschema import Draft202012Validator
from flask import request
from .contracts import digest, fail

_GRANT = object()


def request_user(workbench):
    grant = request.environ.get('cd80.connector_grant')
    if isinstance(grant, tuple) and len(grant) == 2 and grant[0] is _GRANT:
        return workbench.auth.get_user(grant[1])
    return workbench.authenticate(request.headers.get('Cookie', ''))['user']


class DataConnections:
    def __init__(self, app, registry):
        self.app, self.registry = app, registry

    def revision(self):
        # Secrets only contribute a one-way digest, never leave the host.
        modules = Path(__file__).resolve().parents[1]
        implementation = {str(p.relative_to(modules)): digest(p.read_bytes()) for p in
                          [modules/'financial_data/routes.py', modules/'news/routes.py', modules/'news/service.py', modules/'news/worker.py'] if p.exists()}
        return digest({'registry': self.registry, 'implementation':implementation, 'config': {k: self.app.config.get(k) for k in
                      ('CD80_DATA_API_URL', 'CD80_READ_API_KEY', 'CD80_ADMIN_API_KEY', 'NEWS_CACHE_TTL_SECONDS')}})

    def check(self, declarations):
        for item in declarations:
            connection = self.registry.get(item['identity'])
            if not connection or any(op not in connection for op in item['operations']):
                fail('MISSING_CONNECTION', '没有指定的只读数据连接或操作。', 409)

    def call(self, owner, declarations, query, stopped):
        if stopped.is_set(): fail('CANCELLED', '数据读取已取消。', 409)
        if not isinstance(query, dict) or set(query) != {'connection', 'operation', 'arguments'}:
            fail('INVALID_REQUEST', '数据请求格式无效。')
        identity, operation = query['connection'], query['operation']
        if not isinstance(identity, str) or not isinstance(operation, str) or not isinstance(query['arguments'], dict):
            fail('INVALID_REQUEST', '数据请求字段类型无效。')
        if not any(d['identity'] == identity and operation in d['operations'] for d in declarations):
            fail('CAPABILITY_BLOCKED', '工具未声明此数据操作。', 403)
        self.check(declarations)
        spec = self.registry[identity][operation]
        try: Draft202012Validator(spec['schema']).validate(query['arguments'])
        except Exception: fail('INVALID_INPUT', '数据连接参数不符合允许的只读契约。', 422)
        arguments = dict(query['arguments'])
        if spec.get('tableFields'):
            allowed = spec['tableFields'].get(arguments.get('table'), [])
            fields = [*arguments.get('columns',[]), *arguments.get('group_by',[])]
            fields += [f['field'] for f in arguments.get('filters',[])]
            fields += [f['field'] for f in arguments.get('metrics',[])]
            if arguments.get('sort_by'): fields.append(arguments['sort_by'])
            aliases = [m['alias'] for m in arguments.get('metrics',[])]
            fields += [o['field'] for o in arguments.get('order_by',[]) if o['field'] not in aliases]
            if any(f not in allowed for f in fields):
                fail('INVALID_INPUT','字段不属于指定表的允许字段。',422)
        path = spec['path']
        for key in spec.get('pathFields', []): path = path.replace('{'+key+'}', arguments.pop(key))
        # Registry is maintained by the platform; imported packages cannot set routes.
        if not path.startswith('/desktop/api/') or '?' in path or '..' in path or '\\' in path:
            fail('CAPABILITY_BLOCKED', '数据连接路由无效。', 403)
        self.app.extensions['workbench_service'].auth.get_user(owner)
        options = {'json': arguments} if spec['method'] == 'POST' else {'query_string': arguments}
        with self.app.test_request_context(path, method=spec['method'], base_url='http://127.0.0.1',
                                           environ_overrides={'cd80.connector_grant': (_GRANT, owner)}, **options):
            response = self.app.full_dispatch_request()
            if not 200 <= response.status_code < 300:
                error = response.get_json(silent=True) or {}
                if error.get('code') in {'CURSOR_EXPIRED', 'DATA_CHANGED', 'READ_BUDGET_EXCEEDED', 'DATA_AMBIGUOUS', 'LIMIT_EXCEEDED'}:
                    fail(error['code'], error.get('error', '数据请求失败。'), response.status_code)
                fail('DATA_SOURCE_FAILED', '指定数据接口失败（HTTP '+str(response.status_code)+'）；未切换来源。', 502)
            if not response.is_json or len(response.get_data()) > 512 * 1024:
                fail('LIMIT_EXCEEDED', '数据接口结果格式无效或超过512 KiB。', 413)
            value = response.get_json()
        if stopped.is_set(): fail('CANCELLED', '数据读取已取消。', 409)
        return value


def run_connected(library, owner, version, arguments, files, stopped, trial, historical, trace):
    execution = version['manifest']['tool']['execution']
    source = library.files(owner, version)
    from .read_budget import charge
    protocol = execution.get('protocolVersion', '1')
    if protocol not in {'1', '2'}:
        fail('UNSUPPORTED_EXECUTION', '不支持的连接协议版本。', 422)
    max_reads = 100 if protocol == '2' else 6
    if protocol == '2' and arguments.get('cursor'):
        return library.result_pages.page(owner,version,arguments,trace=trace)
    responses, deadline, row_count = [], time.monotonic() + 120, 0
    for _ in range(max_reads + 1):
        if stopped.is_set(): fail('CANCELLED', '数据读取已取消。', 409)
        library.admit(owner, version['assetId'], version['versionId'], version['configurationId'], trial, historical)
        if time.monotonic() > deadline: fail('LIMIT_EXCEEDED', '数据工具超过总执行时限。', 408)
        output = library.executor.execute(source[execution['path']].decode('utf-8'),
            {'input': arguments, 'responses': responses}, files, stopped, package_files=source, package_entry=execution['path'])
        if time.monotonic() > deadline: fail('LIMIT_EXCEEDED', '数据工具超过总执行时限。', 408)
        value = output['result']
        if output['files'] or not isinstance(value, dict): fail('INVALID_OUTPUT', '连接工具只允许结构化返回。', 422)
        if protocol == '2' and set(value) == {'error'}:
            error = value['error']
            allowed = {'DATA_AMBIGUOUS', 'UNSUPPORTED_OPERATION', 'DATA_INCOMPLETE', 'INVALID_INPUT'}
            if not isinstance(error, dict) or error.get('code') not in allowed or not isinstance(error.get('message'), str):
                fail('INVALID_OUTPUT', '无效业务错误格式。', 422)
            fail(error['code'], error['message'][:300], 422)
        if set(value) == {'result'}:
            return library.result_pages.page(owner,version,arguments,value['result'],trace) if protocol == '2' else value['result']
        if set(value) != {'request'} or len(responses) >= max_reads: fail('READ_BUDGET_EXCEEDED', '无效数据协议或读取次数超限。', 429)
        charge(reads=1)
        result = library.data_connections.call(owner, execution['connections'], value['request'], stopped)
        fetched = result.get('processed_rows', len(result.get('data', result.get('items', []))))
        if type(fetched) is not int or fetched < 0:
            fail('INVALID_OUTPUT', '数据读取计数无效。', 422)
        charge(rows=fetched)
        row_count += fetched
        if row_count > 10000: fail('READ_BUDGET_EXCEEDED', '单次能力读取超过10000条。', 429)
        if time.monotonic() > deadline: fail('LIMIT_EXCEEDED', '数据工具超过总执行时限。', 408)
        trace.append({'connection': value['request']['connection'], 'operation': value['request']['operation'],
                      'requestDigest': digest(value['request']), 'responseDigest': digest(result), 'rows': fetched,
                      'snapshot': result.get('snapshot')})
        responses.append(result)
    fail('LIMIT_EXCEEDED', '数据读取次数超限。', 413)
