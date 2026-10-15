"""Manual Skill/Tool/App entry contracts with isolated accounts and real WASI."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import yaml

from backend.app import create_app
from backend.app.modules.capability_library.components import standard_skill_files


ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = '/desktop/api/capabilities'
APPS = '/desktop/api/asset-apps'


class SkillPoolManualEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cd80-skill-pool-manual-')
        self.app = create_app({
            'TESTING': True,
            'DATA_DIR': self.temp.name,
            'GITEE_AI_API_KEY': 'fixture-only',
            'CAPABILITY_SKIP_PUBLIC_SEED': True,
            'AGENT_RUNTIME_DIR': str(ROOT / 'data/agent-runtime'),
        })
        self.client = self.app.test_client()
        self.assertEqual(self.client.post('/desktop/api/register', json={
            'email': 'manual-entry@example.invalid', 'password': 'test-password',
        }).status_code, 200)
        self.owner = self.client.get('/desktop/api/session').json['user']['id']
        self.workbench = self.app.extensions['workbench_service']
        self.library = self.app.extensions['capability_library']
        self.apps = self.app.extensions['asset_apps']
        self.counter = 0
        # A manual form, saving and the WASI cases must never call a model.
        self.model_guard = patch.object(self.workbench.runtime.provider, 'events',
                                        side_effect=AssertionError('manual entry called a model'))
        self.model_guard.start()

    def tearDown(self):
        self.model_guard.stop()
        self.workbench.close()
        self.temp.cleanup()

    def post(self, client, path, body):
        self.counter += 1
        return client.post(path, json=body, headers={'Idempotency-Key': f'manual-{self.counter}'})

    def wait_job(self, client, job):
        deadline = time.monotonic() + 30
        while job['status'] in {'queued', 'running', 'cancelling'} and time.monotonic() < deadline:
            time.sleep(.02)
            job = client.get(CAPABILITIES + '/jobs/' + job['jobId']).json
        self.assertEqual(job['status'], 'completed', job)
        return job['result']

    def save_component(self, body):
        created = self.post(self.client, CAPABILITIES + '/drafts', body)
        self.assertEqual(created.status_code, 201, created.json)
        draft = created.json
        result = self.post(self.client, CAPABILITIES + f"/drafts/{draft['draftId']}/save",
                           {'draftRevision': draft['revision']})
        self.assertEqual(result.status_code, 202, result.json)
        saved = self.wait_job(self.client, result.json)
        self.assertFalse(self.library.asset(self.owner, saved['assetId'])['enabled'])
        return saved

    @staticmethod
    def common_manifest(kind, name, source_format):
        return {
            'format': 'cd80.capability/1', 'kind': kind, 'name': name,
            'description': 'Manual entry fixture',
            'source': {'identity': 'personal/manual-' + uuid4().hex, 'format': source_format},
            'files': [], 'dependencies': [],
            'permissions': {'network': 'none', 'externalEffects': 'none',
                            'inputFiles': False, 'outputFiles': False},
        }

    def skill_body(self):
        entry = ('---\nname: manual-summary\ndescription: Summarize supplied text without adding facts.\n'
                 'license: MIT\ncompatibility: Requires only supplied input.\n'
                 'metadata:\n  author: isolated-fixture\n  revision: 1\n'
                 'allowed-tools: Read\n'
                 '---\nRead references/method.md and summarize only the supplied text.\n')
        reference = '# Method\nPreserve the stated numbers and identify missing facts.\n'
        manifest = self.common_manifest('skill', 'manual-summary', 'agent_skills')
        manifest['skill'] = {'entry': 'original/SKILL.md'}
        return {
            'kind': 'skill', 'name': 'manual-summary', 'description': manifest['description'],
            'manifest': manifest,
            'files': [{'path': 'original/SKILL.md', 'text': entry},
                      {'path': 'original/references/method.md', 'text': reference}],
        }

    def tool_body(self, name='manual-double', code='def run(payload):\n    return {"value": payload["n"] * 2}\n',
                  extra_definition=None):
        descriptor = {
            'name': name, 'description': 'Transform an integer.',
            'input_schema': {'type': 'object', 'properties': {'n': {'type': 'integer'}},
                             'required': ['n'], 'additionalProperties': False},
        }
        descriptor.update(extra_definition or {})
        manifest = self.common_manifest('tool', name, 'claude_tool')
        manifest['tool'] = {
            'inputSchema': copy.deepcopy(descriptor['input_schema']),
            'outputSchema': {'type': 'object', 'properties': {'value': {'type': 'integer'}},
                             'required': ['value'], 'additionalProperties': False},
            'execution': ({'type': 'python_wasi', 'path': 'original/tool.py', 'callable': 'run'}
                          if code is not None else {'type': 'builtin', 'handlerId': 'missing-implementation'}),
        }
        files = [{'path': 'original/tool.json', 'text': json.dumps(descriptor, ensure_ascii=False)}]
        if code is not None:
            files.append({'path': 'original/tool.py', 'text': code})
        return {'kind': 'tool', 'name': name, 'description': manifest['description'],
                'manifest': manifest, 'files': files}

    def test_standard_skill_and_reference_save_as_complete_personal_asset(self):
        body = self.skill_body()
        saved = self.save_component(body)
        version = self.library.version(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(version['contentValidity'], 'valid')
        self.assertEqual(version['readiness'], 'needs_configuration')
        self.assertIn('MISSING_DEPENDENCY', {issue['code'] for issue in version['issues']})
        files = self.library.files(self.owner, version)
        self.assertEqual(files['original/SKILL.md'], body['files'][0]['text'].encode())
        self.assertEqual(files['original/references/method.md'], body['files'][1]['text'].encode())
        frontmatter = yaml.safe_load(files['original/SKILL.md'].decode().split('---', 2)[1])
        self.assertEqual(frontmatter['license'], 'MIT')
        self.assertEqual(frontmatter['compatibility'], 'Requires only supplied input.')
        self.assertEqual(frontmatter['metadata'], {'author': 'isolated-fixture', 'revision': 1})
        self.assertEqual(frontmatter['allowed-tools'], 'Read')
        self.assertEqual(standard_skill_files(version['manifest'], files), {
            'SKILL.md': files['original/SKILL.md'],
            'references/method.md': files['original/references/method.md'],
        })

    def test_claude_tool_definition_and_python_save_disabled_then_run_in_wasi_trial(self):
        saved = self.save_component(self.tool_body())
        version = self.library.version(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(version['readiness'], 'unverified', version['issues'])
        self.assertEqual(json.loads(self.library.files(self.owner, version)['original/tool.json'])['name'],
                         'manual-double')
        result = self.library.invoke(self.owner, {
            'assetId': saved['assetId'], 'versionId': saved['versionId'],
            'configurationId': version['configurationId'], 'input': {'n': 7}, 'fileIds': [],
        }, trial=True)
        self.assertEqual(result['result']['content'], {'value': 14})
        self.assertFalse(self.library.asset(self.owner, saved['assetId'])['enabled'])

    def test_claude_definition_without_implementation_stays_visible_but_cannot_verify_or_enable(self):
        saved = self.save_component(self.tool_body(code=None))
        version = self.library.version(self.owner, saved['assetId'], saved['versionId'])
        self.assertEqual(version['readiness'], 'needs_configuration')
        self.assertIn('MISSING_IMPLEMENTATION', {issue['code'] for issue in version['issues']})
        self.assertEqual(self.client.get(CAPABILITIES + '/assets?scope=personal&kind=tool').status_code, 200)
        request = {
            'assetId': saved['assetId'], 'versionId': saved['versionId'],
            'configurationId': version['configurationId'],
            'cases': [{'input': {'n': n}, 'fileIds': [],
                       'expectation': {'mode': 'json_equals', 'value': {'value': n * 2}}}
                      for n in (2, 3)],
        }
        rejected = self.post(self.client, CAPABILITIES + '/validation-suites', request)
        self.assertEqual(rejected.status_code, 403, rejected.json)
        self.assertEqual(rejected.json['code'], 'CAPABILITY_BLOCKED')
        asset = self.client.get(CAPABILITIES + '/assets/' + saved['assetId'])
        rejected_enable = self.client.patch(CAPABILITIES + '/assets/' + saved['assetId'],
            json={'enabled': True, 'activeVersionId': saved['versionId']},
            headers={'If-Match': asset.headers['ETag']})
        self.assertEqual(rejected_enable.status_code, 403, rejected_enable.json)
        self.assertEqual(rejected_enable.json['code'], 'CAPABILITY_BLOCKED')

    def test_two_exact_tool_versions_form_frozen_app_without_enabling_sources(self):
        first = self.save_component(self.tool_body())
        second = self.save_component(self.tool_body(
            name='manual-add-three', code='def run(payload):\n    return {"value": payload["n"] + 3}\n'))
        first_version = self.library.version(self.owner, first['assetId'], first['versionId'])
        second_version = self.library.version(self.owner, second['assetId'], second['versionId'])
        self.assertFalse(self.library.asset(self.owner, first['assetId'])['enabled'])
        self.assertFalse(self.library.asset(self.owner, second['assetId'])['enabled'])
        definition = {
            'format': 'cd80.app/2', 'name': 'manual two-step pipeline',
            'description': 'Double the input and add three.',
            'inputSchema': {'type': 'object', 'properties': {'n': {'type': 'integer'}},
                            'required': ['n'], 'additionalProperties': False},
            'rules': ['Use exactly the two selected component versions.'],
            'nodes': [
                {'id': 'double', 'kind': 'tool', 'title': 'Double',
                 'componentRef': {'assetId': first['assetId'], 'versionId': first['versionId']},
                 'inputBindings': {'n': {'from': 'input', 'path': 'n'}},
                 'delivery': {'schema': first_version['manifest']['tool']['outputSchema'], 'files': []}},
                {'id': 'add_three', 'kind': 'tool', 'title': 'Add three',
                 'componentRef': {'assetId': second['assetId'], 'versionId': second['versionId']},
                 'inputBindings': {'n': {'from': 'step', 'stepId': 'double', 'path': 'value'}},
                 'delivery': {'schema': second_version['manifest']['tool']['outputSchema'], 'files': []}},
            ],
            'outputs': {'answer': {'from': 'step', 'stepId': 'add_three', 'path': 'value'}},
            'gaps': [], 'parameterization': [],
        }
        saved_app = self.post(self.client, APPS, {'definition': definition})
        self.assertEqual(saved_app.status_code, 201, saved_app.json)
        app = saved_app.json
        self.assertEqual(app['issues'], [])
        self.assertIsNone(app['enabledVersionId'])
        self.assertEqual(app['verification']['status'], 'unverified')
        frozen = app['definition']['nodes']
        self.assertNotIn('componentRef', frozen[0])
        self.assertEqual(frozen[0]['component']['contentDigest'], first_version['contentDigest'])
        self.assertEqual(frozen[1]['component']['contentDigest'], second_version['contentDigest'])
        cases = [{'label': f'n={n}', 'input': {'n': n},
                  'expectation': {'mode': 'json_equals', 'value': {'answer': n * 2 + 3}}}
                 for n in (2, 5)]
        verification = self.post(self.client, APPS + f"/{app['id']}/verify",
                                 {'versionId': app['versionId'], 'cases': cases})
        self.assertEqual(verification.status_code, 202, verification.json)
        for run_id in verification.json['verification']['runIds']:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                run = self.apps.run(self.owner, run_id)
                if run['status'] not in {'queued', 'running'} and run_id not in self.apps.active:
                    break
                time.sleep(.025)
            else:
                self.fail('isolated App verification did not finish')
            self.assertEqual(run['status'], 'succeeded', run.get('error'))
        checked = self.apps.detail(self.owner, app['id'])
        self.assertEqual(checked['verification']['status'], 'passed', checked['verification'])
        self.assertFalse(self.library.asset(self.owner, first['assetId'])['enabled'])
        self.assertFalse(self.library.asset(self.owner, second['assetId'])['enabled'])

    def test_foreign_component_ref_is_rejected_and_unknown_claude_fields_are_preserved(self):
        saved = self.save_component(self.tool_body())
        other = self.app.test_client()
        self.assertEqual(other.post('/desktop/api/register', json={
            'email': 'manual-entry-other@example.invalid', 'password': 'test-password',
        }).status_code, 200)
        foreign_app = {
            'name': 'foreign source', 'inputSchema': {'type': 'object'},
            'nodes': [{'id': 'call', 'kind': 'tool', 'title': 'Foreign',
                       'componentRef': {'assetId': saved['assetId'], 'versionId': saved['versionId']},
                       'inputBindings': {}, 'delivery': {'schema': {}}}],
        }
        rejected = self.post(other, APPS, {'definition': foreign_app})
        self.assertEqual(rejected.status_code, 404, rejected.json)
        self.assertEqual(other.get(APPS).json['items'], [])
        body = self.tool_body(name='manual-extra-fields', code=None,
                              extra_definition={'strict': True})
        body['manifest']['tool']['inputSchema']['oneOf'] = [
            {'required': ['n']}, {'properties': {'n': {'minimum': 0}}},
        ]
        body['files'][0]['text'] = json.dumps({
            **json.loads(body['files'][0]['text']),
            'input_schema': body['manifest']['tool']['inputSchema'],
        }, ensure_ascii=False)
        extra = self.save_component(body)
        version = self.library.version(self.owner, extra['assetId'], extra['versionId'])
        self.assertIn('UNSUPPORTED_SCHEMA', {issue['code'] for issue in version['issues']})
        self.assertEqual(self.library.files(self.owner, version)['original/tool.json'],
                         body['files'][0]['text'].encode())


if __name__ == '__main__':
    unittest.main()
