"""Acceptance-script helpers use synthetic objects only; no upstream or user data."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.validation_suite_helpers import (
    SuiteBlocked, cases_from_examples, run_validation_suite, execution_complete, reusable_evidence,
    require_suite_review, release_records,
)


class ValidationSuiteHelperTests(unittest.TestCase):
    def manifest(self):
        return {'kind': 'tool', 'examples': [
            {'id': 'original', 'input': {'n': 2}, 'expected': {'value': 4}},
            {'id': 'new', 'input': {'n': 5}, 'expected': {'value': 10}},
        ]}

    def test_missing_or_relabelled_second_input_blocks_before_upload(self):
        manifest = self.manifest()
        manifest['examples'] = manifest['examples'][:1]
        calls = []
        with self.assertRaises(SuiteBlocked):
            cases_from_examples(manifest, lambda case: calls.append(case))
        manifest['examples'].append({**manifest['examples'][0], 'id': 'renamed', 'expected': {'value': 99}})
        with self.assertRaises(SuiteBlocked):
            cases_from_examples(manifest, lambda case: calls.append(case))
        self.assertEqual(calls, [])

    def test_cases_keep_independent_expectations_and_do_not_mutate_manifest(self):
        manifest = self.manifest()
        result = cases_from_examples(manifest)
        self.assertEqual([case['input']['n'] for case in result], [2, 5])
        self.assertEqual([case['expectation']['value'] for case in result], [{'value': 4}, {'value': 10}])
        result[0]['input']['n'] = 999
        self.assertEqual(manifest['examples'][0]['input']['n'], 2)

    def test_skill_needs_distinct_tasks_and_per_case_manual_criteria(self):
        manifest = {'kind': 'skill', 'examples': [{'prompt': '总结12、9、3', 'criteria': '对应反馈含义'}, {'prompt': '总结47、31、16', 'criteria': '对应库存含义'}]}
        cases = cases_from_examples(manifest)
        self.assertEqual([row['expectation']['mode'] for row in cases], ['manual_review'] * 2)
        manifest['examples'][1].pop('criteria')
        with self.assertRaises(SuiteBlocked): cases_from_examples(manifest)

    def test_file_examples_need_explicit_per_case_adapter(self):
        manifest = self.manifest()
        for index, row in enumerate(manifest['examples']): row['filePaths'] = [f'data-{index}.csv']
        with self.assertRaises(SuiteBlocked): cases_from_examples(manifest)
        cases = cases_from_examples(manifest, lambda row: (row['input'], ['uploaded-' + row['filePaths'][0]]))
        self.assertEqual(cases[0]['fileIds'], ['uploaded-data-0.csv'])
        self.assertEqual(cases[1]['fileIds'], ['uploaded-data-1.csv'])

    def fake(self, status='completed', case_status='passed'):
        cases = cases_from_examples(self.manifest())
        for row in cases: row.update(status=case_status, result={'content': {'value': row['input']['n'] * 2}})
        record = {'verificationId': 'verify', 'status': case_status, 'cases': cases}
        job = {'jobId': 'job', 'verificationId': 'verify', 'status': status,
               'result': {'verificationId': 'verify', 'status': case_status, 'cases': cases} if status == 'completed' else None}
        calls = []
        lib = SimpleNamespace(store=SimpleNamespace(get=lambda owner, kind, key: copy.deepcopy(record if kind == 'verification' else job)),
                              cancel=lambda owner, key: calls.append(key))
        version = {'assetId': 'asset', 'versionId': 'version', 'configurationId': 'config'}
        return lib, version, job, record, calls

    def test_completed_suite_keeps_each_result_and_has_no_single_result_alias(self):
        lib, version, job, record, _ = self.fake()
        with patch('backend.app.modules.capability_library.asset_validation.verify_suite', return_value=job) as start:
            result = run_validation_suite(lib, 'owner', version, cases_from_examples(self.manifest()))
        self.assertTrue(execution_complete(result)); self.assertTrue(reusable_evidence(result))
        self.assertNotIn('result', result)
        self.assertEqual([row['result']['content']['value'] for row in result['cases']], [4, 10])
        self.assertEqual(start.call_args.args[2]['configurationId'], 'config')

    def test_awaiting_review_is_preserved_without_approval_or_enable(self):
        lib, version, job, _, calls = self.fake(case_status='awaiting_review')
        with patch('backend.app.modules.capability_library.asset_validation.verify_suite', return_value=job):
            result = run_validation_suite(lib, 'owner', version, cases_from_examples(self.manifest()))
        self.assertTrue(result['manualReviewRequired']); self.assertEqual(result['status'], 'awaiting_review')
        self.assertTrue(execution_complete(result)); self.assertEqual(calls, [])

    def test_timeout_requests_cancel_and_retains_partial_cases(self):
        lib, version, job, record, calls = self.fake(status='running', case_status='queued')
        record['cases'][0].update(status='passed', result={'content': {'value': 4}})
        with patch('backend.app.modules.capability_library.asset_validation.verify_suite', return_value=job), patch('scripts.validation_suite_helpers.time.monotonic', side_effect=[0, 2]):
            result = run_validation_suite(lib, 'owner', version, cases_from_examples(self.manifest()), timeout=1)
        self.assertEqual(result['status'], 'timeout'); self.assertEqual(calls, ['job'])
        self.assertEqual([row['status'] for row in result['cases']], ['passed', 'queued'])
        self.assertFalse(execution_complete(result)); self.assertFalse(reusable_evidence({'status': 'passed', 'result': {}}))

    def test_failure_preserves_case_error_and_unstarted_case(self):
        lib, version, job, record, _ = self.fake(status='failed', case_status='failed')
        record['error'] = {'code': 'INVALID_OUTPUT', 'error': 'missing value'}
        record['cases'][1]['status'] = 'queued'
        with patch('backend.app.modules.capability_library.asset_validation.verify_suite', return_value=job):
            result = run_validation_suite(lib, 'owner', version, cases_from_examples(self.manifest()))
        self.assertEqual(result['error']['code'], 'INVALID_OUTPUT'); self.assertEqual(result['cases'][1]['status'], 'queued')
        self.assertFalse(execution_complete(result))

    def suite_record(self, kind='tool'):
        rows = cases_from_examples(self.manifest())
        for index, row in enumerate(rows):
            row.update(status='passed' if kind == 'tool' else 'awaiting_review', sampleDigest='sample-' + str(index), result={'content': {'value': row['input']['n'] * 2}})
        return {'origin':'validation_suite','verificationId':'v-check','jobStatus':'completed','status':'passed' if kind=='tool' else 'awaiting_review',
                'kind':kind,'exampleIds':['original','new'],'cases':rows}

    def test_release_expands_actual_independent_cases_without_suite_result_alias(self):
        suite=self.suite_record()
        expanded=release_records(suite,self.manifest())
        self.assertEqual([row['id'] for row in expanded],['original','new'])
        self.assertEqual([row['result']['content'] for row in expanded],[{'value':4},{'value':10}])
        self.assertTrue(all('cases' not in row for row in expanded))
        suite['exampleIds'].reverse()
        with self.assertRaises(SuiteBlocked): release_records(suite,self.manifest())

    def test_skill_release_requires_exact_run_and_every_case_review(self):
        suite=self.suite_record('skill'); manifest={**self.manifest(),'kind':'skill'}
        decisions={'decision':'accept','verificationId':'v-check','cases':[
            {'sampleDigest':'sample-0','decision':'accept','comment':'已核对原资料'},
            {'sampleDigest':'sample-1','decision':'accept','comment':'已核对新资料'},
        ]}
        for bad in ({'decision':'accept'}, {**decisions,'verificationId':'old'}, {**decisions,'cases':decisions['cases'][:1]}):
            with self.assertRaises(SuiteBlocked): require_suite_review(suite,bad)
        with self.assertRaises(SuiteBlocked): release_records(suite,manifest)
        expanded=release_records(suite,manifest,decisions)
        self.assertEqual([row['status'] for row in expanded],['awaiting_review']*2)
        self.assertEqual([row['review']['comment'] for row in expanded],['已核对原资料','已核对新资料'])

    def test_legacy_single_example_records_remain_unchanged(self):
        old={'id':'original','status':'passed','result':{'content':{'value':4}},'kind':'tool'}
        self.assertEqual(release_records(old,{'kind':'tool','examples':[self.manifest()['examples'][0]]}),[old])
        with self.assertRaises(SuiteBlocked):release_records({'status':'passed'},self.manifest())

    def test_release_keeps_invocations_with_their_actual_case(self):
        suite=self.suite_record()
        suite['invocations']=[{'invocationId':'call-0','input':{'n':2}}, {'invocationId':'call-1','input':{'n':5}}]
        suite['cases'][0]['invocationId']='call-0'
        suite['cases'][1]['executionEvidence']=[{'type':'tool_completed','invocationId':'call-1'}]
        expanded=release_records(suite,self.manifest())
        self.assertEqual([[call['input']['n'] for call in row['invocations']] for row in expanded],[[2],[5]])
        suite['invocations'].pop()
        with self.assertRaises(SuiteBlocked): release_records(suite,self.manifest())

    def test_expanded_tool_cases_pass_real_release_checker_independently(self):
        from backend.app.modules.capability_library.contracts import digest
        from backend.app.modules.capability_library.releases import check_release
        manifest=self.manifest();suite=self.suite_record()
        suite.update(manifestDigest=digest(manifest),configurationDigest='synthetic-config',contentDigest='synthetic-content')
        evidence={'createdAt':'2026-10-14T00:00:00Z','runnerRevision':'isolated-test','system':'synthetic','executionPassed':True,
                  'examples':release_records(suite,manifest)}
        self.assertTrue(check_release(manifest,evidence))
        evidence['examples'][1]['result']=copy.deepcopy(evidence['examples'][0]['result'])
        with self.assertRaises(Exception):check_release(manifest,evidence)

    def test_expanded_skill_reviews_pass_real_release_checker_per_case(self):
        from backend.app.modules.capability_library.contracts import digest
        from backend.app.modules.capability_library.releases import check_release
        manifest={**self.manifest(),'kind':'skill'};suite=self.suite_record('skill')
        suite.update(manifestDigest=digest(manifest),configurationDigest='synthetic-config',contentDigest='synthetic-content')
        review={'decision':'accept','verificationId':'v-check','cases':[
            {'sampleDigest':'sample-0','decision':'accept','comment':'合成测试原案说明'},
            {'sampleDigest':'sample-1','decision':'accept','comment':'合成测试新案说明'},
        ]}
        evidence={'createdAt':'2026-10-14T00:00:00Z','runnerRevision':'isolated-test','system':'synthetic','executionPassed':True,
                  'examples':release_records(suite,manifest,review)}
        self.assertTrue(check_release(manifest,evidence))
        evidence['examples'][1].pop('review')
        with self.assertRaises(Exception):check_release(manifest,evidence)

    def test_incomplete_case_cannot_be_hidden_by_overall_passed_status(self):
        suite=self.suite_record();suite['cases'][1]['status']='queued'
        self.assertFalse(execution_complete(suite))
        with self.assertRaises(SuiteBlocked):release_records(suite,self.manifest())
        suite['cases'][1]['status']='awaiting_review'
        self.assertFalse(execution_complete(suite))

    def test_real_wasi_two_inputs_obtain_real_case_results_without_enablement(self):
        from backend.app import create_app
        from backend.app.modules.capability_library.packages import zip_bytes
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='suite-helper-test-') as folder:
            app = create_app({'TESTING': True, 'DATA_DIR': folder, 'CAPABILITY_SKIP_PUBLIC_SEED': True, 'GITEE_AI_API_KEY': 'fixture-only'})
            work = app.extensions['workbench_service']
            try:
                owner = app.test_client().post('/desktop/api/register', json={'email': 'suite@example.invalid', 'password': 'fixture-password'}).json['user']['id']
                source = root / 'docs/capability-library/examples/decimal-total'
                package = zip_bytes({path.relative_to(source).as_posix(): path.read_bytes() for path in source.rglob('*') if path.is_file()})
                ref = work.capabilities.import_package(owner, package, 'decimal.zip')
                version = work.capabilities.version(owner, ref['assetId'], ref['versionId'])
                cases = cases_from_examples({'kind': 'tool', 'examples': [
                    {'input': {'values': ['0.10', '0.20']}, 'expected': {'total': '0.30'}},
                    {'input': {'values': ['2', '5']}, 'expected': {'total': '7'}},
                ]})
                result = run_validation_suite(work.capabilities, owner, version, cases, timeout=30)
                self.assertTrue(execution_complete(result), result)
                self.assertEqual([case['result']['content'] for case in result['cases']], [{'total': '0.30'}, {'total': '7'}])
                self.assertFalse(work.capabilities.asset(owner, ref['assetId'])['enabled'])
            finally: work.close()


if __name__ == '__main__': unittest.main()
