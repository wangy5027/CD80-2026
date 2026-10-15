"""Main conversation hand-off and exact enabled App discovery."""
from ..capability_library.contracts import fail


def app_catalog(w, owner):
    result = []
    for app in w.asset_apps.store.listing('apps', owner):
        version_id = app.get('enabledVersionId')
        if not version_id:
            continue
        version = w.asset_apps.store.read('versions', owner, version_id)
        definition = version['definition']
        result.append({'assetId': app['id'], 'versionId': version_id, 'name': definition.get('name'),
                       'description': definition.get('description'), 'inputSchema': definition.get('inputSchema'),
                       'rules': definition.get('rules', []), 'revision': app['revision']})
    return result[:100]


def handoff(sub_agents, owner, run, spec=None):
    w, cid = sub_agents.w, run['conversationId']
    payload = run['payload']
    refs = [{'kind': 'upload', 'reference': {'fileId': file_id}} for file_id in payload.get('fileIds', [])]
    session_id = payload.get('authoringSessionId')
    if session_id:
        detail = w.authoring.detail(owner, session_id)
        session = detail['session']
        if session.get('conversationId') not in {None, cid}:
            fail('INVALID_TARGET', '创作会话不属于当前对话。', 409)
        if payload.get('authoringRevision') is not None and payload['authoringRevision'] != session['revision']:
            fail('REVISION_CONFLICT', '创作内容已更新，请核对后再提交。', 409)
        if session.get('question'):
            result = w.authoring.command(owner, session_id, 'answers', {'revision': session['revision'],
                'questionId': session['question']['id'], 'answer': run['message'], 'sourceRefs': refs}, 'chat:' + run['id'])
        else:
            result = w.authoring.command(owner, session_id, 'messages', {'revision': session['revision'],
                'instruction': run['message'], 'sourceRefs': refs}, 'chat:' + run['id'])
    else:
        spec = spec or {}
        refs = [{'kind': 'upload', 'reference': {'fileId': file_id}} for file_id in payload.get('fileIds', [])]
        if spec.get('intent') in {'capture', 'extract'}:
            previous = sub_agents.queue(owner, cid)['items']
            candidates = [r for r in previous if r['id'] != run['id'] and r['status'] in
                          {'succeeded', 'partial', 'failed', 'interrupted'} and r.get('steps')]
            source_id = spec.get('sourceRunId')
            if source_id and source_id not in {r['id'] for r in candidates}:
                fail('INVALID_SOURCE', '选择的历史来源不属于当前对话。', 409)
            if candidates:
                refs.append({'kind': 'sub_agent_run', 'reference': {'runId': source_id or candidates[-1]['id']}})
        result = w.authoring.create(owner, {'conversationId': cid, 'kind': spec.get('kind', 'app'),
            'intent': spec.get('intent', 'create'), 'mode': spec.get('mode', 'prepare'),
            'instruction': run['message'], 'sourceRefs': refs, **({'target': spec['target']} if spec.get('target') else {})},
            'chat:' + run['id'])
    session = result['session']
    sub_agents.mutate(owner, run['id'], lambda r: r.update(kind='authoring', authoringSessionId=session['id'],
                        authoringSession=session, completion={'status': 'complete', 'missingDeliverables': [], 'reasons': []}))
    sub_agents._end(owner, run['id'], 'succeeded', '草稿已保留，正在整理和验证。你可以继续补充要求，最后审阅后保存使用。'
                    if session['phase'] != 'completed' else '当前草稿已保存，尚未启用。')


def invoke_app(sub_agents, owner, run, spec):
    available = app_catalog(sub_agents.w, owner)
    item = next((a for a in available if a['assetId'] == spec.get('assetId') and a['versionId'] == spec.get('versionId')), None)
    if item is None:
        fail('ASSET_UNAVAILABLE', '所选应用版本当前不可用，不能静默替换。', 409)
    bindings = spec.get('fileBindings', {})
    granted = set(run.get('payload', {}).get('fileIds', []))
    if not isinstance(bindings, dict) or any(not isinstance(v, str) or v not in granted for v in bindings.values()):
        fail('FILE_NOT_GRANTED', '应用只能使用本次对话明确选择的文件，请补充授权后重试。', 403)
    result = sub_agents.w.asset_apps.create_run(owner, item['assetId'], {'versionId': item['versionId'],
        'input': spec.get('input', {}), 'fileBindings': bindings, 'taskId': run['conversationId']},
        'chat:' + run['id'])
    sub_agents.mutate(owner, run['id'], lambda r: r.update(kind='asset_app', assetAppRunId=result['id'],
        assetAppId=item['assetId'], assetAppVersionId=item['versionId'], completion={'status': 'complete', 'missingDeliverables': [], 'reasons': []}))
    sub_agents._end(owner, run['id'], 'succeeded', '已使用“' + item['name'] + '”的已启用版本启动本次运行，结果将在应用执行详情中显示。')
