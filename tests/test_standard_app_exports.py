"""Every Apps directory source exports its real frozen format without running."""

import copy
import base64
import io
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from backend.app import create_app
from backend.app.modules.agent_platform.asset_imports import template_definition
from backend.app.modules.agent_platform.standard_exports import export_standard
from backend.app.modules.capability_library.components import snapshot_from_package
from backend.app.modules.capability_library.contracts import CapabilityError, digest, now
from backend.app.modules.evolution.service import PUBLIC
from backend.app.modules.workbench.errors import GatewayError
from tests.test_asset_components import manifest


def contents(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


class StandardAppExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='standard-app-export-')
        self.app = create_app({'TESTING': True, 'DATA_DIR': self.temp.name,
                               'CAPABILITY_SKIP_PUBLIC_SEED': True,
                               'GITEE_AI_API_KEY': 'fixture-only'})
        self.w = self.app.extensions['workbench_service']
        self.author, self.reader = self.app.test_client(), self.app.test_client()
        for index, client in enumerate((self.author, self.reader)):
            response = client.post('/desktop/api/register', json={
                'email': f'app-export-{index}@example.invalid', 'password': 'test-password'})
            self.assertEqual(response.status_code, 200, response.json)
        self.owner = self.author.get('/desktop/api/session').json['user']['id']
        self.other = self.reader.get('/desktop/api/session').json['user']['id']

    def tearDown(self):
        self.w.close()
        self.temp.cleanup()

    def saved_app(self):
        doc = template_definition()
        doc['name'] = '完整中文应用'
        doc['description'] = '很长的用途说明：' + '导出保留全部文字。' * 250
        doc['nodes'][0]['files'] = [{'path': 'readme.md',
                                     'base64': 'IyDljp/lp4votYTmupDkuI3og73kuKLlpLEK'}]
        response = self.author.post('/desktop/api/asset-apps', json={'definition': doc},
                                    headers={'Idempotency-Key': 'saved-modern'})
        self.assertEqual(response.status_code, 201, response.json)
        return response.json

    def test_modern_export_has_full_readme_and_roundtrips_as_unverified(self):
        saved = self.saved_app()
        before = len(self.w.asset_apps.store.listing('versions', self.owner))
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('export must not run')):
            name, raw = export_standard(self.w, self.owner, 'asset', saved['id'], saved['versionId'])
        self.assertEqual(name, '完整中文应用')
        self.assertEqual(len(self.w.asset_apps.store.listing('versions', self.owner)), before)
        files = contents(raw)
        self.assertEqual(set(files), {'app.json', 'readme.md'})
        readme = files['readme.md'].decode('utf-8')
        self.assertIn('导出保留全部文字。' * 250, readme)
        self.assertIn('def run(payload)', readme)
        self.assertIn('原任务用例：1 + 2', readme)
        self.assertIn('# 原始资源不能丢失', readme)
        package = json.loads(files['app.json'])
        self.assertEqual(package['format'], 'cd80.app-package/2')
        self.assertEqual(package['digest'], digest(package['definition']))
        self.assertEqual(package['definition']['nodes'][0]['files'], saved['definition']['nodes'][0]['files'])
        with patch.object(self.w.platform.executor, 'execute', side_effect=AssertionError('import must not run')):
            preview = self.reader.post('/desktop/api/asset-apps/import/file-preview',
                data={'file': (io.BytesIO(raw), '完整中文应用.zip')}, content_type='multipart/form-data')
            self.assertEqual(preview.status_code, 200, preview.json)
            self.assertTrue(any('readme.md' in warning for warning in preview.json['warnings']))
            imported = self.reader.post('/desktop/api/asset-apps/import',
                json={'package': preview.json['package']}, headers={'Idempotency-Key': 'roundtrip'})
        self.assertEqual(imported.status_code, 201, imported.json)
        self.assertEqual(imported.json['verification']['status'], 'unverified')
        self.assertIsNone(imported.json['enabledVersionId'])
        self.assertEqual(imported.json['definition']['nodes'][0]['files'],
                         saved['definition']['nodes'][0]['files'])
        self.assertEqual(imported.json['testCases'], saved['testCases'])
        with self.assertRaises(GatewayError):
            export_standard(self.w, self.other, 'asset', saved['id'], saved['versionId'])
        with self.assertRaises(CapabilityError):
            export_standard(self.w, self.owner, 'asset', saved['id'], 'unknown-version')

    def test_import_companion_does_not_shadow_a_declared_or_embedded_readme(self):
        doc = template_definition()
        doc['nodes'][0]['files'] = [{'path': 'readme.md',
                                     'base64': base64.b64encode(b'# original resource').decode()}]
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('app.json', json.dumps(doc))
            archive.writestr('readme.md', '# companion\n')
        response = self.author.post('/desktop/api/asset-apps/import/file-preview',
            data={'file': (io.BytesIO(payload.getvalue()), 'ambiguous.zip')},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(response.json['definition']['nodes'][0]['files'], doc['nodes'][0]['files'])
        self.assertTrue(any('readme.md' in warning for warning in response.json['warnings']))

        doc['nodes'][0]['files'] = []
        doc['nodes'][0]['resourceFiles'] = ['readme.md']
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('app.json', json.dumps(doc))
            archive.writestr('readme.md', '# original declared resource\n')
        response = self.author.post('/desktop/api/asset-apps/import/file-preview',
            data={'file': (io.BytesIO(payload.getvalue()), 'declared.zip')},
            content_type='multipart/form-data')
        self.assertEqual(response.status_code, 200, response.json)
        self.assertEqual(base64.b64decode(response.json['definition']['nodes'][0]['files'][0]['base64']),
                         b'# original declared resource\n')
        self.assertFalse(any('ZIP 根目录 readme.md' in warning for warning in response.json['warnings']))

    def test_public_modern_snapshot_does_not_follow_current_or_withdrawn_version(self):
        saved = self.saved_app()
        apps = self.w.asset_apps
        with apps.store.transaction() as db:
            version = apps.store.get(db, 'versions', self.owner, saved['versionId'])
            version['verification'] = {'status': 'passed', 'cases': [], 'runIds': [],
                'contextDigest': apps._context(self.owner, version['definition'], {})}
            apps.store.put(db, 'versions', self.owner, version)
        community = self.app.extensions['app_community']
        body = {'appId': saved['id'], 'versionId': saved['versionId']}
        preview = community.preview(self.owner, body)
        release = community.publish(self.owner,
            {**body, 'snapshotDigest': preview['snapshotDigest'], 'confirmSharing': True}, 'publish-once')
        name, raw = export_standard(self.w, self.other, 'community-app', release['releaseId'],
                                    release['sourceVersionId'])
        self.assertEqual(name, release['title'])
        self.assertEqual(json.loads(contents(raw)['app.json']), release['package'])
        self.assertNotIn(self.owner, contents(raw)['readme.md'].decode('utf-8'))
        with self.assertRaises(CapabilityError):
            export_standard(self.w, self.other, 'community-app', release['releaseId'], 'wrong')
        community.withdraw(self.owner, release['releaseId'], {'revision': release['revision']}, 'withdraw-once')
        for caller in (self.owner, self.other):
            with self.assertRaises(CapabilityError):
                export_standard(self.w, caller, 'community-app', release['releaseId'],
                                release['sourceVersionId'])

    def test_nested_skill_dependency_source_is_complete_in_readme(self):
        tool_source = b'def run(payload):\n    return {"value": payload["n"] + 7}\n'
        tool = snapshot_from_package(manifest('tool'), {'original/tool.py': tool_source})
        skill_manifest = manifest('skill')
        skill_manifest['source']['identity'] = 'test/export-composite-skill'
        skill_manifest['dependencies'] = [{'sourceIdentity': 'test/components',
            'contentDigest': tool['contentDigest'], 'required': True, 'reason': 'Nested source required'}]
        skill_text = (b'---\nname: export-skill\ndescription: Use nested tool.\n---\n'
                      b'Read the nested source.\n```python\nprint("inside")\n```\n')
        skill = snapshot_from_package(skill_manifest, {'original/SKILL.md': skill_text}, [tool])
        doc = template_definition()
        doc['name'] = '带嵌套工具的技能应用'
        doc['nodes'] = [{'id': 'invoke_skill', 'kind': 'skill', 'title': '调用技能',
                         'component': skill, 'inputBindings': {},
                         'delivery': {'schema': {'type': 'object'}}, 'checkpoint': False}]
        doc['outputs'] = {'result': {'from': 'step', 'stepId': 'invoke_skill', 'path': ''}}
        response = self.author.post('/desktop/api/asset-apps', json={'definition': doc},
                                    headers={'Idempotency-Key': 'nested-skill-save'})
        self.assertEqual(response.status_code, 201, response.json)
        _, raw = export_standard(self.w, self.owner, 'asset', response.json['id'], response.json['versionId'])
        files = contents(raw)
        readme = files['readme.md'].decode('utf-8')
        self.assertIn('Read the nested source.', readme)
        self.assertIn('payload["n"] + 7', readme)
        self.assertIn('依赖 1：test-component [test/components]', readme)
        self.assertIn('````text\n---\nname: export-skill', readme)
        self.assertIn('```python\nprint("inside")\n```\n````', readme)
        package = json.loads(files['app.json'])
        dependency = package['definition']['nodes'][0]['component']['dependencies'][0]
        self.assertEqual(base64.b64decode(dependency['files']['original/tool.py']), tool_source)

    def test_builtin_and_old_pipeline_formats_state_host_dependency(self):
        for app_id in ('explore', 'strategy-workshop', 'app-workshop'):
            name, raw = export_standard(self.w, self.owner, 'builtin', app_id)
            files = contents(raw)
            self.assertEqual(set(files), {'readme.md', 'workspace.json'})
            self.assertEqual(json.loads(files['workspace.json'])['id'], app_id)
            self.assertIn(name, files['readme.md'].decode('utf-8'))
            self.assertIn('不能作为独立', files['readme.md'].decode('utf-8'))
        ref, spec = next(iter(self.w.platform.presets.items()))
        preset_name, preset_zip = export_standard(self.w, self.owner, 'preset', ref, spec['version'])
        self.assertEqual(preset_name, spec['name'])
        self.assertEqual(json.loads(contents(preset_zip)['application.json'])['format'], 'infinity-application')
        self.assertIn('依赖', contents(preset_zip)['readme.md'].decode('utf-8'))
        personal = copy.deepcopy(spec)
        personal['id'] = 'private-pipeline'
        self.w.platform.repository.put_agent(self.owner, personal)
        name, raw = export_standard(self.w, self.owner, 'legacy-pipeline',
                                    personal['id'] + '@' + personal['version'], personal['version'])
        self.assertEqual(name, personal['name'])
        self.assertEqual(json.loads(contents(raw)['application.json'])['definition']['version'], personal['version'])
        with self.assertRaises(GatewayError):
            export_standard(self.w, self.other, 'legacy-pipeline',
                            personal['id'] + '@' + personal['version'], personal['version'])
        with self.assertRaises(GatewayError):
            export_standard(self.w, self.owner, 'legacy-pipeline', ref, spec['version'])

    def test_old_recipe_and_publication_freeze_original_but_refuse_withdrawal(self):
        evolution = self.w.asset_apps.evolution
        recipe = evolution.create(self.owner,
            {'name': '研究模板', 'description': '完整旧版目标',
             'goalTemplate': '请研究 {{主题}}', 'constraints': ['只引用已授权材料'],
             'capabilityRefs': []}, 'recipe-once')
        name, raw = export_standard(self.w, self.owner, 'legacy-recipe', recipe['id'])
        self.assertEqual(name, '研究模板')
        self.assertIn('请研究 {{主题}}', contents(raw)['readme.md'].decode('utf-8'))
        self.assertEqual(json.loads(contents(raw)['application.json'])['kind'], 'recipe')
        with self.assertRaises(CapabilityError):
            export_standard(self.w, self.other, 'legacy-recipe', recipe['id'])
        package = evolution.portable(self.owner, recipe['id'])
        record = {'id': 'legacy-public-test', 'kind': 'recipe', 'name': '公开研究模板',
                  'description': '公开描述', 'createdAt': now(), 'publisherId': self.owner,
                  'withdrawn': False, 'snapshotDigest': digest(package), 'package': package}
        with evolution.store.transaction() as db:
            evolution.store.put(db, PUBLIC, 'publication', record['id'], record)
        name, raw = export_standard(self.w, self.other, 'legacy-publication', record['id'])
        self.assertEqual(name, '公开研究模板')
        self.assertEqual(json.loads(contents(raw)['application.json']), package)
        record['withdrawn'] = True
        with evolution.store.transaction() as db:
            evolution.store.put(db, PUBLIC, 'publication', record['id'], record)
        for caller in (self.owner, self.other):
            with self.assertRaises(CapabilityError):
                export_standard(self.w, caller, 'legacy-publication', record['id'])

    def test_saved_strategy_rules_export_without_claiming_executable_snapshot(self):
        candidate = {'sourceHash': 'f' * 64, 'strategy': {'name': '示例规则'}}
        frozen = {'idea': '上涨突破后买入', 'candidate': candidate, 'baseCandidate': candidate,
                  'customization': None, 'languageStyle': 'beginner'}
        fingerprint = digest(frozen)
        record = {'id': 'strategy_app_' + fingerprint[:32], 'versionId': 'rules_' + fingerprint,
                  'kind': 'strategy', 'personal': True, 'name': '示例规则',
                  'description': frozen['idea'], 'strategy': candidate['strategy'],
                  'strategyKind': 'catalog', 'sourceHash': candidate['sourceHash'],
                  'customizationId': None, 'definitionDigest': fingerprint,
                  '_draftDefinition': frozen, 'sessionId': 'session-fixture',
                  'runId': None, 'requested': None, 'actual': None,
                  'mode': 'diagnostic', 'verification': {'status': 'unverified'},
                  'createdAt': now(), 'updatedAt': now()}
        with self.w.workshop.repository.transaction() as db:
            self.w.workshop.repository.insert(db, self.owner, 'personal_app', record)
        name, raw = export_standard(self.w, self.owner, 'strategy', record['id'], record['versionId'])
        self.assertEqual(name, '示例规则')
        files = contents(raw)
        self.assertEqual(set(files), {'readme.md', 'strategy.json'})
        self.assertEqual(json.loads(files['strategy.json'])['definition']['idea'], frozen['idea'])
        self.assertIn('未存有通过工程诊断', files['readme.md'].decode('utf-8'))
        with self.assertRaises(GatewayError):
            export_standard(self.w, self.other, 'strategy', record['id'], record['versionId'])

    def test_completed_strategy_includes_frozen_source_but_omits_market_snapshot(self):
        source = 'def run(payload):\n    return {"signal": 1}\n'
        package = {'implementationHash': 'fixture', 'pythonVersion': '3.11', 'custom': None,
                   'files': {'strategies/example.py': base64.b64encode(source.encode()).decode()}}
        frozen = {'market': {'confidentialQuote': 123}, 'package': package}
        record = {'id': 'strategy_app_completed', 'name': '已完成策略',
                  'executionSnapshotId': 'snapshot-fixture', 'updatedAt': now()}
        with self.w.workshop.repository.transaction() as db:
            self.w.workshop.repository.insert(db, self.owner, 'personal_app', record)
            self.w.workshop.repository.snapshot(db, self.owner, 'snapshot-fixture', 'execution', frozen)
        visible = {'app': {'id': record['id'], 'name': record['name'], 'description': '冻结规则',
                           'executionSnapshotDigest': digest(frozen), 'verification': {'status': 'passed'}},
                   'definition': None, 'run': {'idea': '冻结规则'}}
        with patch.object(self.w.workshop, 'personal_app', return_value=visible):
            name, raw = export_standard(self.w, self.owner, 'strategy', record['id'])
        self.assertEqual(name, record['name'])
        files = contents(raw)
        self.assertEqual(files['source/strategies/example.py'].decode(), source)
        self.assertEqual(json.loads(files['strategy-package.json']), package)
        self.assertNotIn('confidentialQuote', files['readme.md'].decode())
        self.assertNotIn('market', json.dumps(json.loads(files['strategy.json'])))

    def test_saved_rules_with_later_successful_diagnostic_include_frozen_code(self):
        source = 'def run(payload):\n    return {"signal": 2}\n'
        package = {'implementationHash': 'fixture', 'custom': None,
                   'files': {'strategies/later.py': base64.b64encode(source.encode()).decode()}}
        record = {'id': 'strategy_app_rules_later', 'name': '后验通过策略', 'updatedAt': now()}
        run = {'id': 'run-after-save', 'status': 'succeeded', 'updatedAt': now(),
               'savedPersonalAppId': record['id'], 'candidate': {'sourceHash': 'e' * 64},
               'executionSnapshotId': 'snapshot-after-save'}
        with self.w.workshop.repository.transaction() as db:
            self.w.workshop.repository.insert(db, self.owner, 'personal_app', record)
            self.w.workshop.repository.insert(db, self.owner, 'run', run)
            self.w.workshop.repository.snapshot(db, self.owner, run['executionSnapshotId'],
                                                'execution', {'market': {}, 'package': package})
        visible = {'app': {'id': record['id'], 'name': record['name'], 'description': '原想法',
                           'sourceHash': run['candidate']['sourceHash'],
                           'verification': {'status': 'passed'}},
                   'definition': {'idea': '原想法'}, 'run': {'id': run['id'], 'status': 'succeeded'}}
        with patch.object(self.w.workshop, 'personal_app', return_value=visible):
            _, raw = export_standard(self.w, self.owner, 'strategy', record['id'])
        self.assertEqual(contents(raw)['source/strategies/later.py'].decode(), source)


if __name__ == '__main__':
    unittest.main()
