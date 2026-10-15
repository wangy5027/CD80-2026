"""Real original/new-input evidence for authored assets, separate from creation."""
import copy

from .contracts import canonical, digest, fail, now, uid


def verify_suite(library, owner, payload):
    if not isinstance(payload, dict) or any(not isinstance(payload.get(k), str) or not payload[k]
            for k in ('assetId', 'versionId', 'configurationId')):
        fail('INVALID_REQUEST', '请指定资产、版本及当前配置。')
    version = library.admit(owner, payload['assetId'], payload['versionId'], payload['configurationId'], trial=True)
    cases = payload.get('cases')
    if not isinstance(cases, list) or not 1 <= len(cases) <= 10:
        fail('INVALID_REQUEST', '验证需要原用例，最多10组；不同输入可选。')
    prepared, samples = [], set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or set(case) - {'label', 'input', 'fileIds', 'expectation'} or not isinstance(case.get('input'), dict):
            fail('INVALID_REQUEST', '每组用例需要输入对象、文件授权和预期结果。')
        expectation = case.get('expectation', {})
        if not isinstance(expectation, dict):
            fail('INVALID_REQUEST', '预期格式无效。')
        mode = expectation.get('mode')
        if version['manifest']['kind'] == 'skill':
            if mode != 'manual_review' or any(not isinstance(expectation.get(k), str) or not expectation[k].strip()
                    for k in ('prompt', 'criteria')):
                fail('INVALID_REQUEST', 'Skill每组需要明确测试问题及验收标准。')
        elif mode == 'json_equals':
            if 'value' not in expectation:
                fail('INVALID_REQUEST', '请提供预期JSON。')
        elif mode == 'assertions':
            from .assertions import validate_assertions
            try:
                validate_assertions(expectation.get('assertions'))
            except ValueError as error:
                fail('INVALID_REQUEST', str(error))
        else:
            fail('INVALID_REQUEST', 'Tool验证需要明确预期JSON或结果断言，不能仅检查输出格式。')
        files = library.file_grants(owner, case.get('fileIds', []), version['manifest'])
        # IDs/names/labels and changed expected output are not different inputs.
        sample = digest({'input': case['input'], 'prompt': expectation.get('prompt'),
                         'fileContents': sorted(digest(data) for data in files.values())})
        samples.add(sample)
        prepared.append({'label': str(case.get('label') or ('原预期' if index == 0 else '新输入'))[:200],
                         'input': copy.deepcopy(case['input']), 'fileIds': case.get('fileIds', []),
                         'expectation': copy.deepcopy(expectation), 'sampleDigest': sample, 'status': 'queued'})
    if len(cases) > 1 and len(samples) < 2:
        fail('INVALID_REQUEST', '提交多组时至少一组必须使用不同的实际输入，不能只改名称或预期。')
    key = uid('check')
    record = {'verificationId': key, 'assetId': version['assetId'], 'versionId': version['versionId'],
              'configurationId': version['configurationId'], 'contextDigest': library.context_digest(version),
              'origin': 'validation_suite', 'cases': prepared, 'status': 'queued', 'createdAt': now()}
    library.store.put(owner, 'verification', key, record)
    version['lastVerificationId'] = key
    library.store.put(owner, 'version', version['versionId'], version)

    def run(stopped, job_id):
        from .skill_validation import ValidationBudget, collect_skill
        budget = ValidationBudget()
        record.update(status='running', jobId=job_id)
        library.store.put(owner, 'verification', key, record)
        try:
            for case in record['cases']:
                if stopped.is_set():
                    fail('CANCELLED', '验证已取消。', 409)
                library.admit(owner, version['assetId'], version['versionId'], version['configurationId'], trial=True)
                expectation = case['expectation']
                case.update(status='running', startedAt=now())
                library.store.put(owner, 'verification', key, record)
                if version['manifest']['kind'] == 'tool':
                    invocation = library.invoke(owner, {**payload, 'input': case['input'], 'fileIds': case['fileIds']},
                                                stopped, trial=True, job_id=job_id)
                    result = invocation['result']
                    case['invocationId'] = invocation['invocationId']
                    if expectation['mode'] == 'json_equals':
                        accepted = canonical(result['content']) == canonical(expectation['value'])
                    else:
                        from .assertions import evaluate
                        accepted = not evaluate(result['content'], expectation['assertions'])
                    case['status'] = 'passed' if accepted else 'failed'
                else:
                    result = collect_skill(library, owner, version,
                        expectation['prompt'] if version.get('embeddedSnapshot') else expectation['prompt'] + '\n本次明确输入：' + canonical(case['input']),
                        case['fileIds'], stopped, case, lambda: library.store.put(owner, 'verification', key, record),
                        inputs=case['input'], budget=budget)
                    case['status'] = 'awaiting_review'
                library.admit(owner, version['assetId'], version['versionId'], version['configurationId'], trial=True)
                if stopped.is_set():
                    fail('CANCELLED', '验证已取消。', 409)
                case.update(result=result, completedAt=now())
                library.store.put(owner, 'verification', key, record)
                if case['status'] == 'failed':
                    record.update(status='failed', completedAt=now())
                    return {'verificationId': key, 'status': record['status'], 'cases': record['cases']}
            record.update(status='awaiting_review' if any(c['status'] == 'awaiting_review' for c in prepared) else 'passed',
                          completedAt=now())
            return {'verificationId': key, 'status': record['status'], 'cases': record['cases']}
        except Exception as error:
            record.update(status='cancelled' if stopped.is_set() else 'failed', completedAt=now(),
                          error={'code': getattr(error, 'code', 'EXECUTION_FAILED'), 'error': str(error)})
            for case in record['cases']:
                if case['status'] == 'running':
                    case.update(status=record['status'], completedAt=now())
                    case.setdefault('error', record['error'])
            raise
        finally:
            library.store.put(owner, 'verification', key, record)
            library.refresh_readiness(owner, version['assetId'], version['versionId'])

    try:
        return library.job(owner, 'verification_suite', run, {'verificationId': key})
    except Exception:
        record.update(status='failed', completedAt=now())
        library.store.put(owner, 'verification', key, record)
        raise
