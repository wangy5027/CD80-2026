import test from 'node:test';
import assert from 'node:assert/strict';
import { createCapabilityDirectoryApi } from './capabilityDirectoryApi.js';
import { capabilityDetail, capabilityOwnedTarget, filterCapabilityCards, personalCapabilityCards,
  platformCapabilityCards } from './capabilityDirectoryModel.js';

test('personal and platform catalogs keep exact kind, source and account ownership apart', () => {
  const mine = personalCapabilityCards([
    { assetId: 'private', latestVersionId: 'old', activeVersionId: 'current', kind: 'skill', name: '同名', readiness: 'unverified' },
    { assetId: 'tool-1', latestVersionId: 'v1', kind: 'tool', name: '私人工具' },
    { assetId: 'deleted', latestVersionId: 'v1', kind: 'skill', name: '已删除', deletedAt: 'today' },
  ], 'skill');
  const platform = platformCapabilityCards({
    official: [{ assetId: 'official', latestVersionId: 'v2', kind: 'skill', name: '同名', owned: false, personalCopies: [] },
      { assetId: 'archived', latestVersionId: 'v1', kind: 'skill', lifecycle: 'archived' }],
    releases: [{ releaseId: 'published', kind: 'skill', title: '同名', status: 'published', personalCopies: [] },
      { releaseId: 'withdrawn', kind: 'skill', title: '隐藏', status: 'withdrawn' },
      { releaseId: 'private-tool', kind: 'tool', title: '工具', status: 'published' }],
  }, 'skill');
  assert.deepEqual(mine.map(item => item.id), ['private']);
  assert.deepEqual(platform.map(item => item.id), ['official', 'published']);
  assert.equal(platform.every(item => item.owned === false), true, 'same name does not establish ownership');
  assert.notEqual(mine[0].key, platform[0].key);
  assert.deepEqual(capabilityOwnedTarget(mine[0]), { assetId: 'private', versionId: 'current' });
  assert.equal(capabilityOwnedTarget(platform[0]), null);
  assert.deepEqual(filterCapabilityCards(platform, '平台官方').map(item => item.id), ['official']);
});

test('backend matched historical copies route to their exact personal version', () => {
  const [official, release] = platformCapabilityCards({
    official: [{ assetId: 'official', latestVersionId: 'v3', kind: 'tool', name: '发行版',
      owned: true, personalCopies: [{ assetId: 'mine', versionId: 'my-v1', name: '旧内容' }] }],
    releases: [{ releaseId: 'release', kind: 'tool', title: '发行版', status: 'published',
      owned: true, personalCopies: [{ assetId: 'other', versionId: 'historical-v2' }] }],
  }, 'tool');
  assert.deepEqual(capabilityOwnedTarget(official), { assetId: 'mine', versionId: 'my-v1' });
  assert.deepEqual(capabilityOwnedTarget(release), { assetId: 'other', versionId: 'historical-v2' });
  assert.notEqual(official.key, release.key);
  const detail = capabilityDetail(official, { assetId: 'official', personalCopies: [] }, {
    versionId: 'v3', owned: true, personalCopies: [{ assetId: 'mine', versionId: 'my-v1' }],
    manifest: { source: { identity: 'official/example' }, files: [{ path: 'tool.json' }],
      dependencies: [{ sourceIdentity: 'dependency/tool' }], permissions: { network: 'none' },
      tool: { inputSchema: { type: 'object' } } },
  });
  assert.equal(detail.owned, true);
  assert.deepEqual(detail.personalCopies.map(copy => copy.versionId), ['my-v1']);
  assert.equal(detail.sourceIdentity, 'official/example');
  assert.equal(detail.files[0].path, 'tool.json');
  assert.equal(detail.dependencies[0].sourceIdentity, 'dependency/tool');
});

test('Tool missing-implementation issue overrides a placeholder execution type only for that version', () => {
  const card = { source: 'personal', id: 'tool', owned: true };
  const manifest = { kind: 'tool', tool: { execution: { type: 'builtin' } } };
  const missing = capabilityDetail(card, { assetId: 'tool' }, { manifest, issues: [{ code: 'MISSING_IMPLEMENTATION' }] });
  const implemented = capabilityDetail(card, { assetId: 'tool', issues: [{ code: 'MISSING_IMPLEMENTATION' }] }, { manifest, issues: [] });
  assert.equal(missing.executionLabel, '待补齐执行实现');
  assert.equal(implemented.executionLabel, 'builtin');
});

test('all three catalog sources follow backend cursors and retain public boundaries', async () => {
  const paths = [];
  const api = createCapabilityDirectoryApi('reader', new AbortController().signal, async (path, options) => {
    paths.push(path);
    assert.equal(options.method, undefined);
    if (path.endsWith('&cursor=next')) return { items: [{ marker: 'two' }], nextCursor: null };
    return { items: [{ marker: 'one' }], nextCursor: 'next' };
  });
  const [personal, official, community] = await Promise.all([api.personal('skill'), api.official('skill'), api.community('skill')]);
  assert.deepEqual([personal, official, community].map(result => result.items.map(item => item.marker)),
    [['one', 'two'], ['one', 'two'], ['one', 'two']]);
  assert.deepEqual(paths.filter(path => !path.includes('cursor=')), [
    '/capabilities/assets?scope=personal&kind=skill&limit=100',
    '/capabilities/assets?scope=public&kind=skill&limit=100',
    '/community/capabilities?kind=skill&limit=100',
  ]);
});

test('official and community additions request ensureOwned with stable unknown-result keys', async () => {
  const calls = [];
  let fail = true;
  const transport = async (path, options) => {
    calls.push({ path, key: options.headers?.['Idempotency-Key'], body: options.body });
    if (fail) { fail = false; throw new Error('Connection lost after submission'); }
    return { assetId: 'personal', versionId: 'old-version', alreadyOwned: true };
  };
  const reader = createCapabilityDirectoryApi('reader', new AbortController().signal, transport);
  await assert.rejects(reader.addOfficial('official', 'v2'), /Connection lost/);
  assert.deepEqual(await reader.addOfficial('official', 'v2'), { assetId: 'personal', versionId: 'old-version', alreadyOwned: true });
  assert.equal(calls[0].key, calls[1].key);
  assert.deepEqual(calls[0].body, { versionId: 'v2', ensureOwned: true });
  assert.equal(calls[0].path, '/capabilities/assets/official/copy');
  await reader.addCommunity('release');
  assert.deepEqual(calls[2].body, { ensureOwned: true });
  assert.equal(calls[2].path, '/community/capabilities/release/copy');
  const another = createCapabilityDirectoryApi('another', new AbortController().signal, transport);
  await another.addOfficial('official', 'v2');
  assert.notEqual(calls[1].key, calls[3].key);
});

test('aborted account cannot submit an add operation', async () => {
  const controller = new AbortController();
  const api = createCapabilityDirectoryApi('reader', controller.signal, () => { throw new Error('should not request'); });
  controller.abort();
  await assert.rejects(api.addCommunity('release'), { name: 'AbortError' });
});
