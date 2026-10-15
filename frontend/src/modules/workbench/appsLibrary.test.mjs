import test from 'node:test';
import assert from 'node:assert/strict';
import { createAppsLibraryApi } from './appsLibraryApi.js';
import { authoredReleases, filterAppCards, personalAppCards, platformAppCards, targetForOwned } from './appsLibraryModel.js';

test('personal and platform catalogs preserve different application identities and public scope', () => {
  const assets = [{ id: 'private-app', versionId: 'private-v2', name: '同名', verification: { status: 'unverified' } }];
  const strategies = [{ id: 'strategy-1', name: '个人策略' }];
  const agents = [
    { agentRef: 'preset@1', personal: false, spec: { name: '同名', version: '1' }, personalCopies: [] },
    { agentRef: 'mine@1', personal: true, spec: { name: '旧版流程', version: '1' }, source: { kind: 'publication', id: 'old-release' } },
  ];
  const recipes = [{ id: 'recipe-1', name: '旧版研究', source: { kind: 'publication', id: 'recipe-release' } }];
  const modern = [
    { releaseId: 'public-v1', title: '同名', sourceVersionId: 'shared-v1', versionNumber: 1, status: 'published', owned: false },
    { releaseId: 'withdrawn', title: '撤回', status: 'withdrawn', owned: true },
  ];
  const legacy = [{ id: 'old-release', kind: 'pipeline', name: '旧版流程', owned: false },
    { id: 'recipe-release', kind: 'recipe', name: '旧版研究', owned: false },
    { id: 'withdrawn-legacy', kind: 'pipeline', name: '不公开', withdrawn: true }];
  const mine = personalAppCards({ assets, agents, strategies, recipes });
  const platform = platformAppCards({ agents, modernReleases: modern, legacyReleases: legacy, personalAssets: assets,
    personalAgents: agents.filter(item => item.personal), personalRecipes: recipes });
  assert.deepEqual(mine.filter(item => item.source === 'builtin').map(item => item.id), ['explore', 'strategy-workshop', 'app-workshop']);
  assert.equal(mine.find(item => item.id === 'private-app').owned, true);
  assert.equal(mine.find(item => item.id === 'private-app').status, '未验证');
  assert.equal(platform.some(item => item.id === 'private-app' || item.id === 'withdrawn' || item.id === 'withdrawn-legacy'), false);
  assert.equal(platform.find(item => item.id === 'public-v1').owned, false, 'same display name cannot imply ownership');
  assert.equal(platform.find(item => item.id === 'old-release').owned, false, 'old publication copies do not establish ownership of a modern APP');
  assert.equal(platform.find(item => item.id === 'recipe-release').owned, false);
  assert.equal(platform.find(item => item.id === 'preset@1').owned, false);
  assert.notEqual(mine.find(item => item.id === 'private-app').key, platform.find(item => item.id === 'public-v1').key);
  assert.deepEqual(filterAppCards(platform, 'Explore').map(item => item.id), ['explore']);
  assert.deepEqual(targetForOwned(mine.find(item => item.id === 'private-app')), { type: 'asset', appId: 'private-app', versionId: 'private-v2' });
  assert.equal(targetForOwned(platform.find(item => item.id === 'public-v1')), null);
});

test('legacy source cards become owned only through the saved modern APP provenance', () => {
  const personalAssets = [
    { id: 'modern-preset', versionId: 'av-p', provenance: { kind: 'official_legacy_preset', agentRef: 'preset@1' } },
    { id: 'modern-recipe', versionId: 'av-r', provenance: { kind: 'legacy_publication', publicationId: 'release-recipe' } },
    { id: 'modern-pipeline', versionId: 'av-f', provenance: { kind: 'legacy_pipeline_import', source: { kind: 'publication', id: 'release-pipeline' } } },
  ];
  const cards = platformAppCards({ personalAssets,
    agents: [{ agentRef: 'preset@1', personal: false, spec: { name: '预置', version: '1' }, personalCopies: [] }],
    legacyReleases: [{ id: 'release-recipe', kind: 'recipe', name: '旧版研究' },
      { id: 'release-pipeline', kind: 'pipeline', name: '旧版流程' }] });
  assert.deepEqual(targetForOwned(cards.find(item => item.id === 'preset@1')), { type: 'asset', appId: 'modern-preset', versionId: 'av-p' });
  assert.deepEqual(targetForOwned(cards.find(item => item.id === 'release-recipe')), { type: 'asset', appId: 'modern-recipe', versionId: 'av-r' });
  assert.deepEqual(targetForOwned(cards.find(item => item.id === 'release-pipeline')), { type: 'asset', appId: 'modern-pipeline', versionId: 'av-f' });
});

test('modern published versions rely on backend ownership and exact personal copy', () => {
  const releases = [
    { releaseId: 'r1', isOwner: true, status: 'published', personalCopies: [{ appId: 'mine', versionId: 'v1' }] },
    { releaseId: 'r2', isOwner: true, status: 'published', personalCopies: [{ appId: 'other', versionId: 'v1' }] },
    { releaseId: 'r3', isOwner: true, status: 'withdrawn', personalCopies: [{ appId: 'mine', versionId: 'v1' }] },
  ];
  assert.deepEqual(authoredReleases(releases, 'mine').map(item => item.releaseId), ['r1']);
  const [card] = platformAppCards({ modernReleases: [{ ...releases[0], releaseId: 'r1', title: '公开', owned: true, sourceVersionId: 'v1' }] })
    .filter(item => item.source === 'community-app');
  assert.deepEqual(targetForOwned(card), { type: 'asset', appId: 'mine', versionId: 'v1' });
});

test('public pages include only server-provided releases and traverse pagination', async () => {
  const seen = [];
  const api = createAppsLibraryApi('reader', new AbortController().signal, async (path, options) => {
    seen.push([path, options.method || 'GET']);
    if (path === '/community/apps?limit=100') return { items: [{ releaseId: 'one' }], nextCursor: 'next' };
    if (path === '/community/apps?limit=100&cursor=next') return { items: [{ releaseId: 'two' }], nextCursor: '' };
    throw new Error('Unexpected path: ' + path);
  });
  assert.deepEqual((await api.modernPublic()).items.map(item => item.releaseId), ['one', 'two']);
  assert.deepEqual(seen.map(item => item[1]), ['GET', 'GET']);
});

test('unknown copy result retries with the original key and account scopes stay separate', async () => {
  const calls = [];
  let fail = true;
  const transport = async (path, options) => {
    calls.push({ path, key: options.headers['Idempotency-Key'], body: options.body });
    if (fail) { fail = false; throw new Error('Connection lost after submission'); }
    return { appId: 'copy-1', versionId: 'v1', alreadyOwned: false };
  };
  const reader = createAppsLibraryApi('reader', new AbortController().signal, transport);
  await assert.rejects(reader.copyModern('release-1'), /Connection lost/);
  await reader.copyModern('release-1');
  assert.equal(calls[0].key, calls[1].key);
  const other = createAppsLibraryApi('other', new AbortController().signal, transport);
  await other.copyModern('release-1');
  assert.notEqual(calls[1].key, calls[2].key);
  assert.deepEqual(calls.map(call => call.path), Array(3).fill('/community/apps/release-1/copy'));
});

test('publishing requires an explicit confirmation and exact preview digest in the request', async () => {
  const calls = [];
  const api = createAppsLibraryApi('author', new AbortController().signal, async (path, options) => {
    calls.push({ path, options }); return {};
  });
  await api.publish({ appId: 'app-1', versionId: 'v2', title: '公开标题', description: '用途', snapshotDigest: 'digest-1' });
  assert.deepEqual(calls[0].options.body, { appId: 'app-1', versionId: 'v2', title: '公开标题', description: '用途', snapshotDigest: 'digest-1', confirmSharing: true });
  assert.ok(calls[0].options.headers['Idempotency-Key']);
});
