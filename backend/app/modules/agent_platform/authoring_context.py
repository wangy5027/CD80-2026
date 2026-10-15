"""Resolve only owned, relevant source identities; never manufacture old evidence."""
import copy
import re

from ..capability_library.contracts import digest, fail, uid


def target_content(w, owner, kind, target):
    if not isinstance(target, dict) or not target.get('assetId'):
        fail('INVALID_TARGET', '请指定要修改的资产及版本。')
    if kind == 'app':
        detail = w.asset_apps.detail(owner, target['assetId'], target.get('baseVersionId'))
        version_id = detail['versionId']
        content = copy.deepcopy(detail['definition'])
        if detail.get('testCases'):
            content['testCases'] = copy.deepcopy(detail['testCases'])
        revision = detail['revision']
    else:
        asset = w.capabilities.asset(owner, target['assetId'])
        version_id = target.get('baseVersionId') or asset['latestVersionId']
        version = w.capabilities.version(owner, target['assetId'], version_id)
        if version['manifest']['kind'] != kind:
            fail('INVALID_TARGET', '资产类型不匹配。')
        revision = asset['revision']
        import base64
        content = {'manifest': version['manifest'], 'files': {
            name: {'base64': base64.b64encode(data).decode()} for name, data in w.capabilities.files(owner, version).items()}}
        from ..capability_library.test_cases import version_test_cases
        content['testCases'] = version_test_cases(w.capabilities, owner, version)
        if version.get('embeddedSnapshot'):
            content['component'] = version['embeddedSnapshot']
    if target.get('baseRevision') is not None and target['baseRevision'] != revision:
        fail('REVISION_CONFLICT', '资产已被其他操作修改，请核对最新版本。', 409)
    return copy.deepcopy(content), {'assetId': target['assetId'], 'baseVersionId': version_id, 'baseRevision': revision}


def resolve(w, owner, session_id, conversation_id, instruction, refs, kind, target=None):
    if not isinstance(refs, list) or len(refs) > 32:
        fail('INVALID_SOURCE', '来源引用格式无效或过多。')
    refs = copy.deepcopy(refs)
    if conversation_id:
        w.get_task(owner, conversation_id)
        refs.insert(0, {'kind': 'conversation', 'reference': {'conversationId': conversation_id}})
    result, seen, context = [], set(), {'fileIds': [], 'relatedHistory': [], 'target': target}
    content = None
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('reference'), dict):
            fail('INVALID_SOURCE', '来源必须包含类型和身份。')
        identity = digest(ref)
        if identity in seen:
            continue
        seen.add(identity)
        source_kind, reference = ref.get('kind'), ref['reference']
        if source_kind in {'conversation', 'message'}:
            task = w.get_task(owner, reference.get('conversationId'))
            messages = [{k: m.get(k) for k in ('role', 'content', 'at', 'authoringSessionId') if k in m}
                        for m in task.get('messages', [])[-16:]]
            for message in messages:
                message['content'] = str(message.get('content', ''))[:6000]
            data = {'conversationId': task['id'], 'messages': messages}
            context['relatedHistory'].append(data)
        elif source_kind == 'sub_agent_run':
            run = w.sub_agents.store.run(owner, reference.get('runId'))
            data = {'runId': run['id'], 'status': run['status'], 'completion': run.get('completion'),
                    'message': run.get('message'), 'steps': [{k: step.get(k) for k in
                        ('id', 'type', 'status', 'purpose', 'label', 'evidenceId')} for step in run.get('steps', [])]}
            context['sourceRunId'] = run['id']
            context['fileIds'].extend(run.get('payload', {}).get('fileIds', []))
        elif source_kind == 'upload':
            file_id = reference.get('fileId') or reference.get('uploadId')
            stream, meta, lock = w.open_file(owner, file_id)
            try:
                data = {key: meta.get(key) for key in ('id', 'name', 'size', 'sha256', 'type')}
            finally:
                stream.close()
                lock.release()
            context['fileIds'].append(file_id)
        elif source_kind == 'asset_version':
            if reference.get('legacy'):
                legacy = reference['legacy']
                if legacy.get('kind') == 'pipeline' and legacy.get('runId'):
                    from .asset_legacy import migrate_pipeline
                    run = w.platform.repository.get(owner, legacy['runId'])
                    data = migrate_pipeline(w.asset_apps, owner, run.get('spec') or {})
                else:
                    from .asset_creator import create
                    data = create(w.asset_apps, owner, {'kind': 'app', 'legacy': legacy})
                    data = data.get('definition', data)
            elif reference.get('kind') == 'app' and kind != 'app':
                from .asset_creator import extract_nodes
                extracted = extract_nodes(w.asset_apps, owner, reference.get('assetId'),
                    {'versionId': reference.get('versionId'), 'kind': kind, 'nodeIds': reference.get('nodeIds', [])})
                data = extracted.get('draft') or extracted.get('definition') or extracted
            else:
                data, _ = target_content(w, owner, reference.get('kind') or kind, {'assetId': reference.get('assetId'),
                                                         'baseVersionId': reference.get('versionId')})
            if content is None:
                content = data
        elif source_kind == 'import_draft':
            if reference.get('draftId'):
                draft = w.capabilities.store.get(owner, 'draft', reference['draftId'])
                draft_content = draft['content']
                data = {'kind': draft_content.get('kind') or draft_content.get('manifest', {}).get('kind'),
                        'content': {'draft': draft_content}, 'issues': []}
            else:
                from ..capability_library.portable_assets import read_source
                data = read_source(w.capabilities, owner, reference.get('portableAssetId'))
            if data['kind'] != kind:
                fail('INVALID_SOURCE', '导入原件的资产类型不匹配。')
            parsed = data.get('content')
            if isinstance(parsed, dict):
                content = parsed.get('definition') or parsed.get('draft') or parsed
            context['importIssues'] = data.get('issues', [])
        else:
            fail('INVALID_SOURCE', '不支持的来源类型。')
        result.append({'id': uid('source'), 'sessionId': session_id, 'kind': source_kind,
                       'reference': reference, 'contentDigest': digest(data), 'claim': 'observed',
                       'selectionBasis': 'user_request', 'data': data, 'evidenceRefs': [], 'issues': []})
    if target:
        base_content, normalized = target_content(w, owner, kind, target)
        context['baseContent'] = base_content
        if content is None:
            content = base_content
        context['target'] = normalized
        if kind == 'app':
            context['runtimeBindings'] = w.asset_apps.store.read('versions', owner, normalized['baseVersionId']).get('runtimeBindings', {})
        else:
            context['runtimeBindings'] = w.capabilities.version(owner, normalized['assetId'], normalized['baseVersionId']).get('componentBindings', {})
    # Search only when the user refers to previous work. Return a small set of
    # matching owned records, not the user's entire history.
    if re.search(r'以前|之前|上次|历史|过去|previous|last time', instruction, re.I):
        words = set(re.findall(r'[a-zA-Z0-9_]{3,}|[\u4e00-\u9fff]{2,}', instruction.lower()))
        pairs = {word[i:i + 2] for word in words for i in range(len(word) - 1)}
        ranked = []
        for task in w.store.get(owner)['tasks']:
            if task['id'] == conversation_id:
                continue
            text = task.get('title', '') + ' '.join(str(m.get('content', '')) for m in task.get('messages', [])[-6:])
            score = sum(pair in text.lower() for pair in pairs)
            if score >= 2:
                ranked.append((score, task))
        for _, task in sorted(ranked, key=lambda item: item[0], reverse=True)[:3]:
            context['relatedHistory'].append({'conversationId': task['id'], 'selectionBasis': 'inferred_selection',
                'messages': [{'role': m.get('role'), 'content': str(m.get('content', ''))[:6000]}
                             for m in task.get('messages', [])[-4:]]})
    context['fileIds'] = list(dict.fromkeys(context['fileIds']))
    context['sources'] = [{k: v for k, v in record.items() if k != 'data'} for record in result]
    return result, context, content
