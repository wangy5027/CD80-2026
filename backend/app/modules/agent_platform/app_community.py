"""Explicitly published, immutable versions of the modern reusable App."""
import copy
import json

from ..capability_library.contracts import canonical, digest, fail, now, uid
from ..capability_library.sharing import assert_shareable


def _text(value, fallback, label, limit):
    value = fallback if value is None else value
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        fail('INVALID_REQUEST', f'{label}须为非空文本，最多{limit}字符。')
    return value.strip()


class AppCommunity:
    def __init__(self, asset_apps):
        self.apps = asset_apps
        self.store = asset_apps.store

    @staticmethod
    def _record(db, release_id, owner, *, require_published=False):
        row = db.execute('SELECT doc FROM asset_community_releases WHERE id=?', (release_id,)).fetchone()
        if row is None:
            fail('NOT_FOUND', '公开应用不存在或无权访问。', 404)
        record = json.loads(row['doc'])
        if record['status'] != 'published' and (require_published or record['publisherId'] != owner):
            fail('NOT_FOUND', '公开应用不存在或已撤回。', 404)
        return record

    @staticmethod
    def _put(db, record):
        db.execute('INSERT OR REPLACE INTO asset_community_releases VALUES(?,?,?,?,?,?)',
                   (record['releaseId'], record['publisherId'], record['sourceAppId'],
                    record['sourceVersionId'], record['status'], canonical(record)))

    @staticmethod
    def _copies(db, owner, record):
        if record['publisherId'] == owner:
            return [{'appId': record['sourceAppId'], 'versionId': record['sourceVersionId']}]
        row = db.execute('SELECT app_id,version_id FROM asset_community_copies WHERE owner=? AND release_id=?',
                         (owner, record['releaseId'])).fetchone()
        return [{'appId': row['app_id'], 'versionId': row['version_id']}] if row else []

    def _view(self, db, owner, record, *, detail=False):
        copies = self._copies(db, owner, record)
        value = {key: copy.deepcopy(record[key]) for key in (
            'releaseId', 'releaseNumber', 'previousReleaseId', 'title', 'description', 'kind',
            'status', 'revision', 'publishedAt', 'snapshotDigest', 'definitionDigest',
            'sourceVersionId', 'versionNumber')}
        if record.get('withdrawnAt'):
            value['withdrawnAt'] = record['withdrawnAt']
        value.update(isOwner=record['publisherId'] == owner, owned=bool(copies), personalCopies=copies,
                     source='community', publisher='平台用户')
        if detail:
            value['package'] = copy.deepcopy(record['package'])
            value['definition'] = copy.deepcopy(record['package']['definition'])
        return value

    def listing(self, owner):
        with self.store.transaction() as db:
            records = [json.loads(row['doc']) for row in db.execute(
                "SELECT doc FROM asset_community_releases WHERE status='published'")]
            records.sort(key=lambda record: (record['publishedAt'], record['releaseId']), reverse=True)
            return [self._view(db, owner, record) for record in records]

    def detail(self, owner, release_id):
        with self.store.transaction() as db:
            return self._view(db, owner, self._record(db, release_id, owner), detail=True)

    def _prepare(self, db, owner, body):
        app_id, version_id = body.get('appId'), body.get('versionId')
        if not isinstance(app_id, str) or not app_id or not isinstance(version_id, str) or not version_id:
            fail('INVALID_REQUEST', '请选择本人应用的精确版本。')
        app = self.store.get(db, 'apps', owner, app_id)
        version = self.store.get(db, 'versions', owner, version_id)
        if version['appId'] != app_id:
            fail('NOT_FOUND', '应用版本不存在或无权访问。', 404)
        verification = version['verification']
        if (version['issues'] or verification.get('status') != 'passed' or
                verification.get('contextDigest') != self.apps._context(
                    owner, version['definition'], version.get('runtimeBindings'))):
            fail('VERIFICATION_REQUIRED', '当前精确版本及运行条件须通过原任务用例验证后才能发布。', 409)
        title = _text(body.get('title'), version['definition']['name'], '发布名称', 128)
        description = _text(body.get('description'), version['definition'].get('description') or title,
                            '发布说明', 4000)
        assert_shareable({'title': title, 'description': description})
        portable = copy.deepcopy(version['definition'])
        portable['testCases'] = self.apps._version_test_cases(version)
        package = self.apps._export_definition(portable)
        snapshot_digest = digest({'appId': app_id, 'versionId': version_id,
                                  'title': title, 'description': description, 'package': package})
        return {'appId': app_id, 'versionId': version_id, 'title': title, 'description': description,
                'snapshotDigest': snapshot_digest, 'package': package,
                'definition': copy.deepcopy(package['definition']), 'definitionDigest': package['digest'],
                'versionNumber': version['number'],
                'warnings': ['完整应用定义、代码、资源及组件快照将向本平台其他登录账号公开，请审阅后确认。',
                             '原任务用例输入与独立预期会随应用公开；私人连接、凭据、运行记录和文件授权不会公开。接收者须重新验证并启用。']}

    def preview(self, owner, body):
        with self.store.transaction() as db:
            return self._prepare(db, owner, body)

    def publish(self, owner, body, key):
        with self.store.transaction() as db:
            def action():
                if body.get('confirmSharing') is not True:
                    fail('SHARING_CONFIRMATION_REQUIRED', '请先审阅完整公开内容并明确确认。', 409)
                prepared = self._prepare(db, owner, body)
                if body.get('snapshotDigest') != prepared['snapshotDigest']:
                    fail('SHARING_PREVIEW_CHANGED', '应用版本、运行条件或发布内容已变化，请重新预览。', 409)
                prior = [json.loads(row['doc']) for row in db.execute(
                    'SELECT doc FROM asset_community_releases WHERE publisher=? AND source_app=?',
                    (owner, prepared['appId']))]
                same = next((item for item in prior if item['snapshotDigest'] == prepared['snapshotDigest']
                             and item['status'] == 'published'), None)
                if same:
                    return self._view(db, owner, same, detail=True)
                latest = max(prior, key=lambda item: item['releaseNumber']) if prior else None
                record = {'releaseId': uid('app_release'), 'releaseNumber': latest['releaseNumber'] + 1 if latest else 1,
                          'previousReleaseId': latest['releaseId'] if latest else None,
                          'publisherId': owner, 'sourceAppId': prepared['appId'],
                          'sourceVersionId': prepared['versionId'], 'versionNumber': prepared['versionNumber'],
                          'title': prepared['title'], 'description': prepared['description'], 'kind': 'app',
                          'status': 'published', 'revision': 1, 'publishedAt': now(),
                          'snapshotDigest': prepared['snapshotDigest'],
                          'definitionDigest': prepared['definitionDigest'], 'package': prepared['package']}
                self._put(db, record)
                return self._view(db, owner, record, detail=True)
            result, _ = self.store.request(db, owner, 'app.community.publish', key, body, action)
            return result

    def copy(self, owner, release_id, body, key):
        with self.store.transaction() as db:
            def action():
                record = self._record(db, release_id, owner, require_published=True)
                copies = self._copies(db, owner, record)
                if copies:
                    return {**copies[0], 'alreadyOwned': True}
                package = record['package']
                doc = self.apps.import_preview(owner, {'package': package})['definition']
                from ..capability_library.components import export_snapshot
                for node in doc['nodes']:
                    if node.get('component'):
                        node['component'] = export_snapshot(node['component'])
                result = self.apps._save_in_transaction(db, owner, {'definition': doc, 'bindings': {}},
                    provenance={'communityReleaseId': release_id,
                                'snapshotDigest': record['snapshotDigest']})
                db.execute('INSERT INTO asset_community_copies VALUES(?,?,?,?)',
                           (owner, release_id, result['appId'], result['versionId']))
                return {**result, 'alreadyOwned': False}
            result, _ = self.store.request(db, owner, 'app.community.copy:' + release_id, key, body, action)
            return result

    def withdraw(self, owner, release_id, body, key):
        with self.store.transaction() as db:
            def action():
                record = self._record(db, release_id, owner)
                if record['publisherId'] != owner:
                    fail('NOT_FOUND', '公开应用不存在或无权修改。', 404)
                if type(body.get('revision')) is not int or body['revision'] != record['revision']:
                    fail('REVISION_CONFLICT', '发布状态已变化，请刷新后重试。', 409)
                if record['status'] != 'withdrawn':
                    record.update(status='withdrawn', withdrawnAt=now(), revision=record['revision'] + 1)
                    self._put(db, record)
                return self._view(db, owner, record, detail=True)
            result, _ = self.store.request(db, owner, 'app.community.withdraw:' + release_id,
                                           key, body, action)
            return result
