"""Bounded acceptance helpers; no network, model, or verification on import."""
import copy
import json
import time


class SuiteBlocked(ValueError):
    """The author has not supplied enough independently checkable cases."""


def cases_from_examples(manifest, prepare_input=None):
    examples = manifest.get('examples', [])
    if not isinstance(examples, list) or not 2 <= len(examples) <= 10:
        raise SuiteBlocked('需提供原用例及至少一个不同的适用新输入（共2至10例）；不复制单例或推测业务预期。')
    # Check author-declared content, before uploading resources or invoking anything.
    identities = {json.dumps({'input': example.get('input', {}), 'prompt': example.get('prompt') if manifest['kind'] == 'skill' else None,
                               'files': example.get('filePaths', [])}, sort_keys=True, ensure_ascii=False) for example in examples}
    if len(identities) < 2:
        raise SuiteBlocked('至少两个用例必须具有不同实际输入、任务或资料；仅改标签/预期不算新输入。')
    cases = []
    for index, example in enumerate(examples):
        if manifest['kind'] == 'skill':
            if any(not isinstance(example.get(key), str) or not example[key].strip() for key in ('prompt', 'criteria')):
                raise SuiteBlocked('每个Skill用例都需任务与独立验收标准。')
            expectation = {key: example[key] for key in ('prompt', 'criteria')}
            expectation['mode'] = 'manual_review'
        elif example.get('assertions'):
            expectation = {'mode': 'assertions', 'assertions': copy.deepcopy(example['assertions'])}
            if 'expected' in example:
                expectation['assertions'].append({'path': [], 'op': 'eq', 'value': copy.deepcopy(example['expected'])})
        elif 'expected' in example:
            expectation = {'mode': 'json_equals', 'value': copy.deepcopy(example['expected'])}
        else:
            raise SuiteBlocked('每个Tool用例都需独立预期或业务断言。')
        if prepare_input:
            inputs, file_ids = prepare_input(copy.deepcopy(example))
        else:
            if example.get('filePaths'):
                raise SuiteBlocked('含文件的用例需要明确上传与逐案授权适配，不能继承旧文件。')
            inputs, file_ids = copy.deepcopy(example.get('input', {})), []
        if not isinstance(inputs, dict):
            raise SuiteBlocked('用例输入必须为对象。')
        cases.append({'label': example.get('title') or example.get('id') or f'用例 {index + 1}',
                      'input': inputs, 'fileIds': file_ids, 'expectation': expectation})
    return cases


def run_validation_suite(library, owner, version, cases, *, timeout=180):
    """Return suite and every case, even on execution error/timeout; never enable."""
    from backend.app.modules.capability_library.asset_validation import verify_suite
    if not 0 < timeout <= 600:
        raise ValueError('验证等待上限必须在0至600秒之间。')
    if not isinstance(cases, list) or not 2 <= len(cases) <= 10:
        raise SuiteBlocked('需提供原用例与适用新输入；不能以单例替代验证套件。')
    request = {key: version[key] for key in ('assetId', 'versionId', 'configurationId')}
    job = verify_suite(library, owner, {**request, 'cases': copy.deepcopy(cases)})
    verification_id = (job.get('result') or {}).get('verificationId') or job.get('verificationId')
    # Backend job envelopes keep initial verificationId in context/result depending on state.
    if not verification_id:
        version_now = library.version(owner, version['assetId'], version['versionId'])
        verification_id = version_now.get('lastVerificationId')
    deadline, timed_out = time.monotonic() + timeout, False
    while job['status'] in {'queued', 'running', 'cancelling'}:
        if time.monotonic() >= deadline:
            library.cancel(owner, job['jobId'])
            timed_out = True
            break
        time.sleep(min(.2, max(0, deadline - time.monotonic())))
        job = library.store.get(owner, 'job', job['jobId'])
    outcome = copy.deepcopy(job.get('result') or {})
    verification_id = outcome.get('verificationId') or verification_id
    record = library.store.get(owner, 'verification', verification_id) if verification_id else {}
    rows = copy.deepcopy(record.get('cases') or outcome.get('cases') or cases)
    status = 'timeout' if timed_out else record.get('status') or outcome.get('status') or job['status']
    return {'origin': 'validation_suite', 'verificationId': verification_id, 'jobId': job['jobId'],
            'jobStatus': job['status'], 'status': status, 'cases': rows,
            'manualReviewRequired': any(row.get('status') == 'awaiting_review' for row in rows),
            'error': copy.deepcopy(record.get('error') or job.get('error')),
            'timedOut': timed_out}


def execution_complete(suite):
    return (suite.get('origin') == 'validation_suite' and suite.get('jobStatus') == 'completed'
            and suite.get('status') in {'passed', 'awaiting_review'} and len(suite.get('cases', [])) >= 2
            and all(case.get('status') in ({'passed'} if suite.get('status') == 'passed' else {'passed', 'awaiting_review'}) for case in suite['cases']))


def reusable_evidence(record):
    """Old single-example reports do not satisfy the new validation policy."""
    return execution_complete(record) and bool(record.get('verificationId'))


def require_suite_review(record, review):
    """Explicit decisions must address this execution and every actual case."""
    if not execution_complete(record) or not isinstance(review, dict) or review.get('decision') != 'accept':
        raise SuiteBlocked('Skill套件尚无完整执行及明确接受记录。')
    if review.get('verificationId') != record.get('verificationId'):
        raise SuiteBlocked('人工审阅必须对应本次verificationId，不能沿用旧输出审阅。')
    decisions = review.get('cases')
    if not isinstance(decisions, list) or len(decisions) != len(record['cases']):
        raise SuiteBlocked('人工审阅需覆盖每个实际用例。')
    for case, decision in zip(record['cases'], decisions):
        if (not case.get('sampleDigest') or not isinstance(decision, dict)
                or decision.get('sampleDigest') != case['sampleDigest'] or decision.get('decision') != 'accept'
                or not isinstance(decision.get('comment'), str) or not decision['comment'].strip()):
            raise SuiteBlocked('逐案审阅需按顺序核对sampleDigest、接受决定与具体说明。')
    return copy.deepcopy(review)


def release_records(record, manifest, review=None):
    """Expand suites into exact per-example release records; preserve legacy rows."""
    if record.get('origin') != 'validation_suite':
        if not record.get('id') or 'result' not in record:
            raise SuiteBlocked('无法识别历史单例证据；不能推测结果。')
        result = copy.deepcopy(record)
        if review is not None: result['review'] = copy.deepcopy(review)
        return [result]
    examples = manifest.get('examples', [])
    expected_ids = [example.get('id') for example in examples]
    if (not execution_complete(record) or not all(expected_ids) or len(set(expected_ids)) != len(expected_ids)
            or record.get('exampleIds') != expected_ids or len(record['cases']) != len(examples)):
        raise SuiteBlocked('套件缺少与当前清单逐项对应的exampleIds或完整结果。')
    accepted = require_suite_review(record, review or record.get('review')) if manifest['kind'] == 'skill' else None
    common = {key: copy.deepcopy(value) for key, value in record.items() if key not in {'cases', 'exampleIds', 'result', 'id', 'review', 'invocations'}}
    rows = []
    for index, (example, case) in enumerate(zip(examples, record['cases'])):
        if 'result' not in case:
            raise SuiteBlocked('用例没有实际结果，不能发布。')
        status = case.get('executionStatus', case.get('status'))
        if manifest['kind'] == 'skill' and status != 'awaiting_review':
            raise SuiteBlocked('Skill缺少原始待审阅执行状态；不能倒推历史记录。')
        row = {**common, 'id': example['id'], 'status': status, 'result': copy.deepcopy(case['result']),
               'caseIndex': index, 'sampleDigest': case.get('sampleDigest'),
               'input': copy.deepcopy(case.get('input', {})), 'expectation': copy.deepcopy(case.get('expectation', {})),
               'executionEvidence': copy.deepcopy(case.get('executionEvidence', []))}
        if case.get('invocationId'): row['invocationId'] = case['invocationId']
        if 'invocations' in record:
            identities = {event['invocationId'] for event in case.get('executionEvidence', []) if event.get('invocationId')}
            if case.get('invocationId'): identities.add(case['invocationId'])
            invocations = [item for item in record['invocations'] if item.get('invocationId') in identities]
            if identities != {item.get('invocationId') for item in invocations}:
                raise SuiteBlocked('逐案执行事件引用的调用详情不完整，不能发布。')
            row['invocations'] = copy.deepcopy(invocations)
        if accepted: row['review'] = accepted['cases'][index]
        rows.append(row)
    return rows
