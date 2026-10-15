"""Trials never grant evidence; personal cases remain owner-scoped."""
from .contracts import canonical, fail, now, uid
from .packages import has_secret


class TestingMixin:
    def trial(self, owner, payload):
        version = self.admit(owner, payload['assetId'], payload['versionId'], payload.get('configurationId'), trial=True)
        if version['manifest']['kind'] == 'skill' and (not isinstance(payload.get('prompt'), str) or not payload['prompt'].strip() or len(payload['prompt']) > 100000):
            fail('INVALID_REQUEST', '请填写测试问题（最多100000字）。')
        self.file_grants(owner, payload.get('fileIds', []), version['manifest'])
        key = uid('trial')
        record = {'trialId': key, 'assetId': version['assetId'], 'versionId': version['versionId'], 'configurationId': version['configurationId'], 'sample': payload, 'status': 'queued', 'createdAt': now()}
        self.store.put(owner, 'trial', key, record)

        def run(stopped, job_id):
            record.update(status='running', jobId=job_id)
            self.store.put(owner, 'trial', key, record)
            try:
                if version['manifest']['kind'] == 'tool':
                    invocation = self.invoke(owner, payload, stopped, trial=True, job_id=job_id)
                    record.update(result=invocation['result'], invocationId=invocation['invocationId'])
                else:
                    from .skill_validation import collect_skill
                    record['result'] = collect_skill(self, owner, version, payload['prompt'], payload.get('fileIds', []),
                        stopped, record, lambda: self.store.put(owner, 'trial', key, record))
                record.update(status='succeeded', completedAt=now())
                return record
            except Exception as error:
                status = 'unknown' if getattr(error, 'code', None) == 'OUTCOME_UNKNOWN' else ('cancelled' if stopped.is_set() else 'failed')
                record.update(status=status, completedAt=now())
                record.setdefault('error', {'code': getattr(error, 'code', None) or 'EXECUTION_FAILED', 'error': str(error)})
                raise
            finally:
                self.store.put(owner, 'trial', key, record)
        try:
            return self.job(owner, 'trial', run, {'trialId': key})
        except Exception:
            record['status'] = 'failed'
            self.store.put(owner, 'trial', key, record)
            raise

    def save_test_case(self, owner, payload):
        allowed = {'name', 'assetId', 'versionId', 'input', 'fileIds', 'expected', 'prompt', 'criteria', 'assertions'}
        if set(payload) - allowed or not isinstance(payload.get('name'), str) or not 1 <= len(payload['name'].strip()) <= 128:
            fail('INVALID_REQUEST', '请填写用例名称（1–128字）。')
        if not isinstance(payload.get('input'), dict) or not isinstance(payload.get('fileIds'), list):
            fail('INVALID_REQUEST', '用例需要输入对象和文件授权列表。')
        if len(canonical(payload).encode()) > 128 * 1024:
            fail('LIMIT_EXCEEDED', '测试用例超过128 KiB。', 413)
        for field in ('assetId', 'versionId'):
            if not isinstance(payload.get(field), str): fail('INVALID_REQUEST', '请选择能力和版本。')
        for field in ('prompt', 'criteria'):
            if field in payload and not isinstance(payload[field], str): fail('INVALID_REQUEST', '测试问题和验收标准必须为文本。')
        if 'assertions' in payload:
            from .assertions import validate_assertions
            try: validate_assertions(payload['assertions'])
            except ValueError as error: fail('INVALID_REQUEST', str(error))
        version = self.version(owner, payload['assetId'], payload['versionId'])
        self.file_grants(owner, payload['fileIds'], version['manifest'])
        if has_secret({'case': canonical(payload).encode()}):
            fail('EMBEDDED_SECRET', '用例疑似包含凭据，请移除后再保存。')
        record = {**payload, 'name': payload['name'].strip(), 'caseId': uid('case'), 'createdAt': now()}
        self.store.put(owner, 'test_case', record['caseId'], record)
        return record
