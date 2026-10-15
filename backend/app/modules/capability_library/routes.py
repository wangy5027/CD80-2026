"""Authenticated API. Job observation never starts or repeats work."""
import base64
import io
import json
import re
import time

from flask import Blueprint, Response, current_app, jsonify, request, send_file, stream_with_context

from ..workbench.routes import json_body
from ..workbench.errors import GatewayError
from .contracts import CapabilityError, TERMINAL, canonical, digest, fail, issue, parse_json, uid
from .service import PUBLIC
from .packages import zip_bytes, safe_path, parse_package, inspect, file_index, original_has_secret, skill_content_validity, component_export
from .mcp import endpoint
from .usage_guides import usage_guide, usage_examples
from .lifecycle import asset_view, version_view, public_view, personal_copies

capabilities=Blueprint('capabilities',__name__,url_prefix='/desktop/api/capabilities')


@capabilities.errorhandler(CapabilityError)
@capabilities.errorhandler(GatewayError)
def error_response(error):
    return jsonify({'error':str(error),'code':error.code or ('AUTH_REQUIRED' if error.status==401 else 'REQUEST_FAILED'),'requestId':uid('req'),'details':getattr(error,'details',{})}),error.status


def context():
    service=current_app.extensions['workbench_service']
    owner=service.authenticate(request.headers.get('Cookie',''))['user']['id']
    return current_app.extensions['capability_library'],owner


def output(value,status=200):
    response=jsonify(value)
    if isinstance(value,dict) and 'revision' in value: response.headers['ETag']=f'"{value["revision"]}"'
    return response,status


def action(library,owner,payload,function,status=202):
    key=request.headers.get('Idempotency-Key','')
    if not key or len(key)>150: fail('INVALID_REQUEST','请提供有效 Idempotency-Key。')
    identity=digest({'path':request.path,'key':key})
    fingerprint=digest(payload)
    with library.request_lock:
        previous=library.store.get(owner,'request',identity,{})
        if previous:
            if previous['digest']!=fingerprint: fail('IDEMPOTENCY_CONFLICT','相同请求键对应不同内容。',409)
            if 'result' not in previous: fail('OUTCOME_UNKNOWN','上次请求已登记但结果未知，请检查记录；未自动重试。',409)
            return output(previous['result'],previous['status'])
        library.store.put(owner,'request',identity,{'digest':fingerprint})
        try:
            result=function()
        except CapabilityError as error:
            library.store.put(owner,'request',identity,{'digest':fingerprint,'result':{'error':str(error),'code':error.code,'details':error.details,'requestId':uid('req')},'status':error.status})
            raise
        library.store.put(owner,'request',identity,{'digest':fingerprint,'result':result,'status':status})
        return output(result,status)


def page(items,owner):
    filters={k:v for k,v in request.args.items() if k!='cursor'}
    fingerprint=digest({'owner':owner,'path':request.path,'filters':filters})
    try:
        limit=int(request.args.get('limit',20))
        if not 1<=limit<=100: raise ValueError()
        cursor=request.args.get('cursor')
        value=json.loads(base64.urlsafe_b64decode(cursor)) if cursor else {'offset':0,'fingerprint':fingerprint}
        offset=value['offset']
        if value['fingerprint']!=fingerprint or type(offset) is not int or offset<0: raise ValueError()
    except Exception: fail('INVALID_REQUEST','分页参数无效。')
    next_cursor=base64.urlsafe_b64encode(canonical({'offset':offset+limit,'fingerprint':fingerprint}).encode()).decode() if len(items)>offset+limit else None
    return {'items':items[offset:offset+limit],'nextCursor':next_cursor}


def public_version_view(lib,viewer,value):
    copies=personal_copies(lib,viewer,value)
    return {**version_view(lib,PUBLIC,value),'personalCopies':copies,'owned':bool(copies),
            'usageGuide':usage_guide(value),'usageExamples':usage_examples(value)}


@capabilities.route('/assets',methods=['GET'])
def assets():
    lib,owner=context()
    scope=request.args.get('scope','personal')
    if scope not in {'personal','public'}: fail('INVALID_REQUEST','目录范围无效。')
    items=lib.store.list(PUBLIC if scope=='public' else owner,'asset')
    if scope=='public':items=[public_view(lib,owner,i) for i in items]
    if scope=='personal':
        items=[asset_view(lib,owner,i) for i in items if bool(i.get('deletedAt'))==(request.args.get('deleted')=='true')]
    for param,field in [('kind','kind'),('status','readiness')]:
        if request.args.get(param): items=[i for i in items if i.get(field)==request.args[param]]
    query=request.args.get('q','').casefold()
    if query: items=[i for i in items if query in (i['name']+' '+i['description']).casefold()]
    return output(page(items,owner))


@capabilities.route('/assets/<asset_id>',methods=['GET','PATCH','DELETE'])
def asset(asset_id):
    lib,owner=context()
    if request.method=='DELETE':
        from .lifecycle import delete_asset
        return output(delete_asset(lib,owner,asset_id,request.headers.get('If-Match')))
    if request.method=='PATCH': return output(lib.update_asset(owner,asset_id,json_body(),request.headers.get('If-Match')))
    value=lib.asset(owner,asset_id,request.args.get('scope')=='public')
    return output(public_view(lib,owner,value) if request.args.get('scope')=='public' else asset_view(lib,owner,value))


@capabilities.post('/assets/<asset_id>/restore')
def restore_asset(asset_id):
    lib,owner=context()
    from .lifecycle import delete_asset
    return output(delete_asset(lib,owner,asset_id,request.headers.get('If-Match'),restore=True))


@capabilities.post('/assets/<asset_id>/recheck')
def recheck_asset(asset_id):
    lib,owner=context();payload=json_body()
    value=lib.version(owner,asset_id,payload.get('versionId',''))
    if lib.asset(owner,asset_id).get('deletedAt'):fail('CAPABILITY_BLOCKED','请先恢复此能力。',409)
    return action(lib,owner,payload,lambda:lib.check(owner,value),200)


@capabilities.post('/assets/<asset_id>/official-update')
def official_update(asset_id):
    lib,owner=context();payload=json_body();etag=request.headers.get('If-Match')
    lib.revision(lib.asset(owner,asset_id),etag)
    from .lifecycle import update_official
    return action(lib,owner,payload,lambda:lib.job(owner,'official_update',
        lambda stopped,_key:update_official(lib,owner,asset_id,payload.get('versionId',''),etag,stopped)))


@capabilities.get('/assets/<asset_id>/versions')
def versions(asset_id):
    lib,owner=context();viewer=owner
    if request.args.get('scope')=='public': owner=PUBLIC
    lib.asset(owner,asset_id)
    return output(page([(public_version_view(lib,viewer,v) if owner==PUBLIC else
                         {**version_view(lib,owner,v),'usageGuide':usage_guide(v),'usageExamples':usage_examples(v)})
                        for v in lib.store.list(owner,'version') if v['assetId']==asset_id],owner))


@capabilities.get('/assets/<asset_id>/versions/<version_id>')
def version(asset_id,version_id):
    lib,owner=context();viewer=owner
    if request.args.get('scope')=='public': owner=PUBLIC
    value = lib.version(owner,asset_id,version_id)
    return output(public_version_view(lib,viewer,value) if owner==PUBLIC else
                  {**version_view(lib,owner,value),'usageGuide':usage_guide(value),'usageExamples':usage_examples(value)})


@capabilities.get('/assets/<asset_id>/versions/<version_id>/files/<path:file_path>')
def version_file(asset_id,version_id,file_path):
    lib,owner=context()
    version=lib.version(owner,asset_id,version_id)
    files=lib.files(owner,version)
    path=safe_path(file_path)
    if path not in files: fail('NOT_FOUND','文件不存在。',404)
    response=Response(files[path],content_type='text/plain; charset=utf-8')
    response.headers.update({'Content-Security-Policy':"sandbox; default-src 'none'",'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})
    return response


@capabilities.post('/assets/<asset_id>/copy')
def copy_asset(asset_id):
    lib,owner=context()
    payload=json_body()
    version=lib.version(PUBLIC,asset_id,payload.get('versionId',''))
    if payload.get('ensureOwned') is True:
        from .official import copy_closure
        return action(lib,owner,payload,lambda:copy_closure(lib,owner,version),201)
    def copy():
        from .official import copy_version
        with lib.store.lock:
            if personal_copies(lib,owner,version) and payload.get('confirmDuplicate') is not True:
                fail('DUPLICATE_CONFIRMATION_REQUIRED','你的库中已有相同内容，确认后可另存独立副本。',409)
            return copy_version(lib,owner,version,force_new=True)

    return action(lib,owner,payload,copy,201)


@capabilities.post('/official-preparations')
def prepare_official():
    lib,owner=context()
    payload=json_body()
    if payload.get('ensureOwned') is True:
        if payload.get('scope') == 'personal':
            fail('INVALID_REQUEST', '仅支持从官方公共版本添加到我的库。')
        source=lib.version(PUBLIC,payload.get('assetId',''),payload.get('versionId',''))
        from .official import copy_closure
        return action(lib,owner,payload,lambda:lib.job(owner,'official_copy',
            lambda _stopped,_key:copy_closure(lib,owner,source)))
    personal=None
    if payload.get('scope')=='personal':
        personal={'assetId':payload.get('assetId'),'versionId':payload.get('versionId')}
        asset=lib.asset(owner,personal['assetId'])
        provenance=asset.get('provenance',{})
        source=lib.version(PUBLIC,provenance.get('publicAssetId',''),provenance.get('publicVersionId',''))
    else:
        source=lib.version(PUBLIC,payload.get('assetId',''),payload.get('versionId',''))
    from .official import prepare
    def start():
        if personal is None and personal_copies(lib,owner,source) and payload.get('confirmDuplicate') is not True:
            fail('DUPLICATE_CONFIRMATION_REQUIRED','你的库中已有相同内容，确认后可另存独立副本。',409)
        return lib.job(owner,'official_preparation',lambda stopped,_key:prepare(lib,owner,source,stopped,personal,force_new=personal is None,confirm_duplicate=payload.get('confirmDuplicate') is True))
    return action(lib,owner,payload,start)



@capabilities.post('/imports/preview')
def import_preview():
    """Parse and statically inspect the exact upload; never persist or execute it."""
    _lib,_owner=context()
    upload=request.files.get('file')
    if upload is None: fail('INVALID_REQUEST','请选择能力包。')
    raw=upload.stream.read(30*1024*1024+1)
    filename=upload.filename or 'package.zip'
    if not raw or len(raw) > 20 * 1024 * 1024:
        fail('LIMIT_EXCEEDED', '能力包为空或超过 20 MiB。', 413)
    bundled = component_export(raw) if raw.startswith(b'PK\x03\x04') else None
    if bundled is None and filename.lower().endswith('.json'):
        decoded = parse_json(raw)
        if isinstance(decoded, dict) and decoded.get('format') == 'cd80.component/1':
            bundled = (decoded, None)
    if bundled is not None:
        from .components import validate_snapshot
        snapshot = validate_snapshot(bundled[0])
        manifest = snapshot['manifest']
        files = {path: base64.b64decode(data, validate=True) for path, data in snapshot['files'].items()}
        packaged_cases = bundled[1]
    else:
        manifest,files,metadata=parse_package(raw,filename,include_metadata=True)
        packaged_cases=metadata.get('testCases')
    from .test_cases import prepare_test_cases
    if manifest['kind'] not in {'skill','tool'}:
        fail('INVALID_REQUEST','仅支持预览 Skill 或 Tool 能力包。')
    expected_kind=request.form.get('kind')
    if expected_kind is not None and expected_kind!=manifest['kind']:
        fail('INVALID_REQUEST','所选类型与原件内容不一致。')
    problems=inspect(manifest,files)
    if original_has_secret(raw,filename) and not any(p['code']=='EMBEDDED_SECRET' for p in problems):
        problems.append(issue('EMBEDDED_SECRET','原始包疑似含凭据；原件可保留但不可启用。'))
    static_readiness=('blocked' if any(p['severity']=='blocker' for p in problems) else
                      'needs_configuration' if any(p['severity']=='configuration' for p in problems) else 'unverified')
    warnings=['此处仅做离线解析和静态检查；保存后仍需检查个人依赖、配置和验证状态。']
    if any(p['code']=='MISSING_IMPLEMENTATION' for p in problems):
        warnings.append('此 Tool 定义没有可执行实现；可以保存原件，补齐实现并验证前不能启用。')
    return output({'sha256':digest(raw),'originalName':filename,'kind':manifest['kind'],
                   'name':manifest['name'],'description':manifest['description'],
                   'sourceIdentity':manifest['source']['identity'],'manifest':manifest,
                   'testCases':prepare_test_cases(manifest,files,packaged_cases),
                   'files':file_index(files),'issues':problems,'staticReadiness':static_readiness,
                   'contentValidity':skill_content_validity(manifest,files),'warnings':warnings})


@capabilities.post('/imports')
def imports():
    lib,owner=context()
    upload=request.files.get('file')
    if upload is None: fail('INVALID_REQUEST','请选择能力包。')
    raw=upload.stream.read(30*1024*1024+1)
    filename=upload.filename or 'package.zip'
    target=request.form.get('targetAssetId')
    expected=request.form.get('expectedDigest')
    test_cases_raw=request.form.get('testCases')
    test_cases=parse_json(test_cases_raw) if test_cases_raw is not None else None
    if expected is not None:
        if not re.fullmatch(r'[a-f0-9]{64}',expected):
            fail('INVALID_REQUEST','预览摘要格式无效。')
        if expected!=digest(raw):
            fail('IMPORT_PREVIEW_CHANGED','上传原件与预览时不同，请重新预览。',409)
    if target: lib.asset(owner,target)
    def create():
        key=uid('imp')
        lib.store.put(owner,'import',key,{'importId':key,'status':'queued'})
        def parse(stopped,_job):
            try:
                if stopped.is_set(): fail('CANCELLED','导入已取消。',409)
                result={**lib.import_package(owner,raw,filename,target,test_cases=test_cases),'importId':key}
                lib.store.put(owner,'import',key,result)
                return result
            except Exception:
                lib.store.put(owner,'import',key,{'importId':key,'status':'failed'})
                raise
        return lib.job(owner,'import',parse,{'importId':key})
    return action(lib,owner,{'sha256':digest(raw),'name':filename,'target':target,
                             'expectedDigest':expected,'testCases':test_cases},create)


@capabilities.get('/imports/<key>')
def imported(key):
    lib,owner=context()
    return output(lib.store.get(owner,'import',key))


@capabilities.route('/drafts',methods=['GET','POST'])
def drafts():
    lib,owner=context()
    if request.method=='GET': return output(page(lib.store.list(owner,'draft'),owner))
    payload=json_body()
    return action(lib,owner,payload,lambda:lib.draft(owner,payload),201)


@capabilities.route('/drafts/<key>',methods=['GET','PATCH','DELETE'])
def draft(key):
    lib,owner=context()
    with lib.store.transaction():
        record=lib.store.get(owner,'draft',key)
        if request.method=='PATCH':
            lib.revision(record,request.headers.get('If-Match'))
            payload=json_body()
            if set(payload)-{'kind','name','description','manifest','files','dependencySnapshots','authoringProvenance','testCases'}: fail('INVALID_REQUEST','草稿修改字段无效。')
            record['content'].update(payload)
            record['revision']+=1
            lib.store.put(owner,'draft',key,record)
        elif request.method=='DELETE':
            if record.get('savedVersionId'): fail('CAPABILITY_BLOCKED','已保存的草稿不能删除，请归档对应资产。',403)
            lib.store.delete(owner,'draft',key)
            return '',204
    return output(record)


@capabilities.post('/drafts/<key>/save')
def save_draft(key):
    lib,owner=context()
    payload=json_body()
    return action(lib,owner,payload,lambda:lib.job(owner,'save',lambda _stop,_job:lib.save_draft(owner,key,payload)))


@capabilities.post('/drafts/generate')
def generate():
    lib,owner=context()
    payload=json_body()
    return action(lib,owner,payload,lambda:lib.job(owner,'generate',lambda stop,_job:lib.generate_draft(owner,payload,stop)))


@capabilities.route('/connections',methods=['GET','POST'])
def connections():
    lib,owner=context()
    if request.method=='GET': return output(page(lib.store.list(owner,'connection'),owner))
    payload=json_body()
    return action(lib,owner,payload,lambda:lib.create_connection(owner,payload),201)


@capabilities.route('/connections/<key>',methods=['GET','PATCH'])
def connection(key):
    lib,owner=context()
    with lib.store.transaction():
        value=lib.store.get(owner,'connection',key)
        if request.method=='PATCH':
            lib.revision(value,request.headers.get('If-Match'))
            payload=json_body()
            if set(payload)-{'name','endpoint','authMode','headerName'}: fail('INVALID_REQUEST','连接修改字段无效。')
            if 'endpoint' in payload: endpoint(payload['endpoint'],bool(lib.config.get('TESTING')))
            if 'authMode' in payload and payload['authMode'] not in {'none','bearer','api_key'}: fail('INVALID_REQUEST','认证模式无效。')
            if any(value.get(k)!=v for k,v in payload.items() if k!='name'):
                lib.store.delete(owner,'credential',key)
                value['credentialRevision']+=1
                value['configured']=payload.get('authMode',value['authMode'])=='none'
            value.update(payload,revision=value['revision']+1)
            lib.store.put(owner,'connection',key,value)
    return output(value)


@capabilities.post('/connections/<key>/credentials')
def credentials(key):
    lib,owner=context()
    payload=json_body()
    return action(lib,owner,{'action':payload.get('action'),'digest':digest(payload)},lambda:lib.credentials(owner,key,payload),200)


@capabilities.post('/connections/<key>/discover')
def discover(key):
    lib,owner=context()
    lib.store.get(owner,'connection',key)
    return action(lib,owner,{},lambda:lib.job(owner,'discover',lambda stopped,_job:lib.discover(owner,key,stopped)))


@capabilities.post('/connections/<key>/tools/import')
def connection_tools(key):
    lib,owner=context()
    payload=json_body()
    return action(lib,owner,payload,lambda:lib.job(owner,'import_tools',lambda _stop,_job:lib.import_discovered(owner,key,payload)))


@capabilities.post('/verifications')
def verify():
    lib,owner=context()
    payload=json_body()
    required(payload)
    return action(lib,owner,payload,lambda:lib.verify(owner,payload))


@capabilities.post('/validation-suites')
def validation_suites():
    lib, owner = context()
    payload = json_body()
    from .asset_validation import verify_suite
    return action(lib, owner, payload, lambda: verify_suite(lib, owner, payload))


@capabilities.get('/verifications/<key>')
def verification(key):
    lib,owner=context()
    return output(lib.store.get(owner,'verification',key))


@capabilities.post('/verifications/<key>/review')
def review(key):
    lib,owner=context()
    payload=json_body()
    return action(lib,owner,payload,lambda:lib.review(owner,key,payload),200)


def required(payload):
    if not all(isinstance(payload.get(k),str) and payload[k] for k in ('assetId','versionId','configurationId')) or not isinstance(payload.get('input'),dict) or not isinstance(payload.get('fileIds'),list):
        fail('INVALID_REQUEST','请指定资产、版本、配置、输入对象及本次授权文件列表。')
    if any(k.startswith('_') for k in payload): fail('INVALID_REQUEST','不允许内部调用字段。')


@capabilities.route('/invocations',methods=['GET','POST'])
def invocations():
    lib,owner=context()
    if request.method=='GET':
        items=lib.store.list(owner,'invocation')
        for name in ('assetId','taskId','status'):
            if request.args.get(name): items=[item for item in items if item.get(name)==request.args[name]]
        return output(page(items,owner))
    payload=json_body()
    required(payload)
    if payload.get('taskId'): lib.workbench.get_task(owner,payload['taskId'])
    def execute():
        lib.admit(owner,payload['assetId'],payload['versionId'],payload['configurationId'])
        return lib.job(owner,'invoke',lambda stopped,job:lib.invoke(owner,payload,stopped,job_id=job))
    return action(lib,owner,payload,execute)


@capabilities.get('/invocations/<key>')
def invocation(key):
    lib,owner=context()
    return output(lib.store.get(owner,'invocation',key))


@capabilities.get('/jobs/<key>')
def job(key):
    lib,owner=context()
    return output(lib.store.get(owner,'job',key))


@capabilities.post('/jobs/<key>/cancel')
def cancel(key):
    lib,owner=context()
    return action(lib,owner,{},lambda:lib.cancel(owner,key),200)


@capabilities.get('/jobs/<key>/events')
def events(key):
    lib,owner=context()
    lib.store.get(owner,'job',key)
    try: after=int(request.headers.get('Last-Event-ID') or request.args.get('after',0))
    except ValueError: fail('INVALID_REQUEST','事件游标无效。')
    def stream():
        cursor=after
        while True:
            record=lib.store.get(owner,'job',key)
            for event in lib.store.get(owner,'events',key)['items']:
                if event['id']>cursor:
                    cursor=event['id']
                    yield f'id: {cursor}\nevent: {event["type"]}\ndata: {canonical(event["data"])}\n\n'
            if record['status'] in TERMINAL: return
            yield ': heartbeat\n\n'
            time.sleep(.3)
    return Response(stream_with_context(stream()),mimetype='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})


@capabilities.post('/exports')
def export():
    lib,owner=context()
    payload=json_body()
    if not all(isinstance(payload.get(k),str) for k in ('assetId','versionId','format')): fail('INVALID_REQUEST','导出参数无效。')
    return action(lib,owner,payload,lambda:lib.job(owner,'export',lambda _stop,_job:lib.export(owner,payload)))


@capabilities.get('/downloads/<key>')
def download(key):
    lib,owner=context()
    value=lib.store.get(owner,'download',key)
    response=send_file(io.BytesIO(lib.store.blob(owner,key)),download_name=value['name'],as_attachment=True,mimetype='application/octet-stream')
    response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})
    return response


@capabilities.post('/trials')
def trial():
    lib, owner = context()
    payload = json_body()
    required(payload)
    return action(lib, owner, payload, lambda: lib.trial(owner, payload))


@capabilities.get('/trials/<key>')
def trial_detail(key):
    lib, owner = context()
    return output(lib.store.get(owner, 'trial', key))


@capabilities.route('/test-cases', methods=['GET', 'POST'])
def test_cases():
    lib, owner = context()
    if request.method == 'GET':
        asset_id = request.args.get('assetId')
        lib.asset(owner, asset_id)
        return output(page([c for c in lib.store.list(owner, 'test_case') if c['assetId'] == asset_id], owner))
    payload = json_body()
    return action(lib, owner, payload, lambda: lib.save_test_case(owner, payload), 201)


@capabilities.delete('/test-cases/<key>')
def delete_test_case(key):
    lib, owner = context()
    lib.store.get(owner, 'test_case', key)
    lib.store.delete(owner, 'test_case', key)
    return output({'ok': True})
