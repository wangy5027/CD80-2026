import test from 'node:test';
import assert from 'node:assert/strict';
import { templateParameters, recipeBody, publicationCard, publicationDate, publicationWarnings, mergeComponents, mutationRequester, preparedMutationRequester, dependencyBindingKey, selectedRecipeComponents, allPages } from './evolutionModel.js';

test('research templates expose ordered unique Chinese and spaced parameters without evaluating expressions', () => {
  assert.deepEqual(templateParameters('分析 {{证券代码}} 从 {{ start date }} 到 {{end-date}}，再对比 {{证券代码}}'), ['证券代码', 'start date', 'end-date']);
  assert.deepEqual(templateParameters('没有可变参数'), []);
  assert.deepEqual(templateParameters('{{unclosed'), []);
});

test('research app save includes exact component identities and excludes inherited results, credentials and file grants', () => {
  const value = recipeBody({ name: '  行情研究 ', description: ' 日线分析 ', goalTemplate: ' 分析 {{symbol}} ',
    constraints: '  检查来源\n\n只使用本次授权数据 ', sourceRunId: 'source-run',
    capabilityRefs: [{ assetId: 'a', versionId: 'v', configurationId: 'private', token: 'private' }],
    fileIds: ['old-private-file'], output: 'old-results', apiKey: 'private' });
  assert.deepEqual(value, { name: '行情研究', description: '日线分析', goalTemplate: '分析 {{symbol}}',
    constraints: ['检查来源', '只使用本次授权数据'], sourceRunId: 'source-run', capabilityRefs: [{ assetId: 'a', versionId: 'v' }] });
  assert.throws(() => recipeBody({ name: '', goalTemplate: '任务' }), /名称/);
  assert.throws(() => recipeBody({ name: '应用', goalTemplate: ' ' }), /模板/);
});

test('community cards keep independent publication identities and recognize both withdrawal contracts', () => {
  const app = publicationCard({ id: 'same', kind: 'recipe', name: '应用', owned: true, withdrawn: true }, 'application');
  const capability = publicationCard({ releaseId: 'same', kind: 'tool', title: '工具', isOwner: false, status: 'published', personalCopies: [{ assetId: 'a' }] }, 'capability');
  assert.notEqual(app.key, capability.key);
  assert.equal(app.status, 'withdrawn'); assert.equal(app.owned, true);
  assert.equal(capability.status, 'published'); assert.equal(capability.owned, false);
  assert.equal(capability.name, '工具'); assert.equal(capability.personalCopies.length, 1);
});

test('publication dates use the platform local day across UTC midnight boundaries', () => {
  assert.equal(publicationDate('2026-10-13T17:00:00Z'), '2026-10-14');
  assert.equal(publicationDate('invalid'), '');
});

test('publication review exposes both application summary and component top-level warnings', () => {
  const appWarning = '不包含历史输入、产物或凭据。';
  const componentWarning = '请审阅完整源文件。';
  assert.deepEqual(publicationWarnings({ summary: { warnings: [appWarning] } }), [appWarning]);
  assert.deepEqual(publicationWarnings({ warnings: [componentWarning] }), [componentWarning]);
  assert.deepEqual(publicationWarnings({ warnings: [componentWarning, null], summary: { warnings: [{ message: appWarning }, componentWarning] } }), [componentWarning, appWarning]);
  assert.deepEqual(publicationWarnings(null), []);
});

test('dependency candidates retain exact old versions and multiple personal copies', () => {
  const current = [{ assetId: 'a', versionId: 'new', available: true, capabilityRef: { sourceIdentity: 'source', contentDigest: 'new-digest', kind: 'tool' } }];
  const candidates = [{ assetId: 'a', versionId: 'old', sourceIdentity: 'source', contentDigest: 'wanted', kind: 'tool' },
    { assetId: 'b', versionId: 'copy', sourceIdentity: 'source', contentDigest: 'wanted', kind: 'tool' }];
  const merged = mergeComponents(current, candidates);
  assert.equal(merged.length, 3);
  assert.deepEqual(merged.filter(item => item.capabilityRef.contentDigest === 'wanted').map(item => item.versionId), ['old', 'copy']);
  assert.equal(mergeComponents(merged, [{ assetId: 'a', versionId: 'new', sourceIdentity: 'source', contentDigest: 'new-digest', kind: 'tool' }])[0].available, true,
    'Error candidates omit readiness; merging them must retain the verified catalog state');
});

test('uncertain imports retry with the same request identity while changed bindings get a new identity', async () => {
  const sent = [];
  const mutate = mutationRequester(async (_path, options) => { sent.push(options); if (sent.length < 3) throw new Error('connection lost'); return { saved: true }; }, new AbortController().signal);
  const body = { package: { kind: 'pipeline' }, dependencyBindings: {} };
  await assert.rejects(mutate('/agents/import', body, { bodyKey: true }));
  await assert.rejects(mutate('/agents/import', body, { bodyKey: true }));
  assert.equal(sent[0].body.idempotencyKey, sent[1].body.idempotencyKey);
  await mutate('/agents/import', { ...body, dependencyBindings: { source: 'chosen' } }, { bodyKey: true });
  assert.notEqual(sent[1].body.idempotencyKey, sent[2].body.idempotencyKey);
  assert.equal(sent[2].body.idempotencyKey, sent[2].headers['Idempotency-Key']);
});

test('a completed response from a disposed page cannot trigger a follow-up action', async () => {
  const controller = new AbortController();
  let resolve;
  const mutate = mutationRequester(() => new Promise(done => { resolve = done; }), controller.signal);
  const pending = mutate('/evolution/recipes/run', { input: {} });
  controller.abort(); resolve({ conversationId: 'previous-account' });
  await assert.rejects(pending, error => error.name === 'AbortError');
});

test('community discovery follows every cursor so search can find results after the first hundred', async () => {
  const paths = [];
  const result = await allPages(async path => {
    paths.push(path);
    return path.includes('cursor=') ? { items: [{ id: 'after-100', name: '最后一项' }], nextCursor: null }
      : { items: Array.from({ length: 100 }, (_, index) => ({ id: index })), nextCursor: 'cursor+/=' };
  }, '/community/capabilities?limit=100&mine=true');
  assert.equal(result.items.length, 101);
  assert.equal(result.items.at(-1).name, '最后一项');
  assert.equal(paths[1], '/community/capabilities?limit=100&mine=true&cursor=cursor%2B%2F%3D');
});

test('a superseded catalog read cannot continue paging or expose partial results', async () => {
  let current = true, calls = 0;
  await assert.rejects(allPages(async () => { calls++; current = false; return { items: [{ id: 'old-account' }], nextCursor: 'next' }; }, '/catalog', { isCurrent: () => current }), error => error.name === 'AbortError');
  assert.equal(calls, 1);
  await assert.rejects(allPages(async () => ({ items: [], nextCursor: 'repeated' }), '/catalog'), /分页/);
});

test('an uncertain copy retries its original prepared body and key before inspecting new duplicate state', async () => {
  const sent = []; let prepared = 0;
  const mutate = mutationRequester(async (_path, options) => {
    sent.push(options);
    if (sent.length === 1) throw new Error('response lost after commit');
    return { assetId: 'first-copy' };
  }, new AbortController().signal);
  const copy = preparedMutationRequester(mutate);
  const prepare = async () => ({ confirmDuplicate: prepared++ > 0 });
  await assert.rejects(copy('/copy', prepare));
  await copy('/copy', prepare);
  assert.equal(prepared, 1);
  assert.deepEqual(sent[1].body, { confirmDuplicate: false });
  assert.equal(sent[0].headers['Idempotency-Key'], sent[1].headers['Idempotency-Key']);
  await copy('/copy', prepare);
  assert.equal(prepared, 2);
  assert.deepEqual(sent[2].body, { confirmDuplicate: true });
  assert.notEqual(sent[1].headers['Idempotency-Key'], sent[2].headers['Idempotency-Key']);
});

test('known copy errors discard preparation, while outcome-unknown errors keep it', async () => {
  let calls = 0, prepares = 0;
  const copy = preparedMutationRequester(async (_path, body) => {
    calls++;
    if (calls === 1) throw Object.assign(new Error('missing'), { status: 409, code: 'DEPENDENCY_MISSING' });
    if (calls === 2) throw Object.assign(new Error('unknown'), { status: 409, code: 'OUTCOME_UNKNOWN' });
    return body;
  });
  const prepare = () => ({ selected: ++prepares });
  await assert.rejects(copy('/copy', prepare));
  await assert.rejects(copy('/copy', prepare));
  assert.deepEqual(await copy('/copy', prepare), { selected: 2 });
  assert.equal(prepares, 2);
});

test('explicit portable slots keep same-source choices independent and old pipeline kind collisions separate', () => {
  const shared = { sourceIdentity: 'same-source', componentId: 'same-name', kind: 'tool' };
  assert.notEqual(dependencyBindingKey({ ...shared, bindingId: 'capability-1' }, 'recipe'), dependencyBindingKey({ ...shared, bindingId: 'capability-2' }, 'recipe'));
  assert.equal(dependencyBindingKey(shared, 'recipe'), 'same-source');
  assert.equal(dependencyBindingKey(shared, 'pipeline'), 'tool:same-name');
  assert.equal(dependencyBindingKey({ ...shared, kind: 'skill' }, 'pipeline'), 'skill:same-name');
});

test('selected references remain visible when absent or old, without silently changing the pinned version', () => {
  const refs = [{ assetId: 'deleted', versionId: 'old' }, { assetId: 'exists', versionId: 'pinned' }];
  const selected = selectedRecipeComponents(refs, [{ assetId: 'exists', versionId: 'latest', name: '现有组件', available: true }]);
  assert.equal(selected.length, 2);
  assert.equal(selected[0].missing, true);
  assert.equal(selected[0].versionId, 'old');
  assert.equal(selected[1].versionId, 'pinned');
  assert.equal(selected[1].replacement.versionId, 'latest');
  assert.deepEqual(refs, [{ assetId: 'deleted', versionId: 'old' }, { assetId: 'exists', versionId: 'pinned' }]);
});
