import assert from 'node:assert/strict';

// Runs only against browser_evolution_fixture.py, through the normal authenticated API/UI.
export async function recoveryChecks({ author, reader, base, out, login, post, narrow }) {
  const evidence = {};
  const get = async (page, path) => {
    const response = await page.request.get(base + '/desktop/api' + path);
    assert.ok(response.ok(), await response.text()); return response.json();
  };
  const fixture = await (await author.request.get(base + '/__fixture__/state')).json();
  await login(author, 'evolve-author@example.invalid');
  await login(reader, 'evolve-reader@example.invalid');

  // Two genuinely executed successful code steps, interspersed with failed attempts.
  await author.locator('#composer-input').fill('RECOVERY_TWO_STEPS ' + fixture.message);
  await author.getByRole('button', { name: '发送消息', exact: true }).click();
  const completed = author.locator('.sub-task[data-status=succeeded]').first();
  await completed.waitFor({ timeout: 90000 });
  const runId = await completed.getAttribute('data-run-id');
  await completed.getByRole('button', { name: '沉淀为 Tool', exact: true }).click();
  const draft = author.locator('.cap-draft-editor');
  const stepSelect = draft.getByLabel('选择要沉淀的成功代码步骤');
  await stepSelect.waitFor();
  const options = await stepSelect.locator('option').evaluateAll(nodes => nodes.map(node => ({ value: node.value, text: node.textContent })));
  assert.equal(options.length, 2, 'Only the two successful code steps are selectable');
  assert.equal(await stepSelect.inputValue(), options[1].value);
  assert.match(await draft.locator('.cap-capture-step').innerText(), /analysis-result.json/);
  await draft.getByLabel('名称', { exact: true }).fill('不可丢失的本地编辑');
  author.once('dialog', dialog => dialog.dismiss());
  await stepSelect.selectOption(options[0].value);
  assert.equal(await stepSelect.inputValue(), options[1].value);
  assert.equal(await draft.getByLabel('名称', { exact: true }).inputValue(), '不可丢失的本地编辑');
  author.once('dialog', dialog => dialog.accept());
  await stepSelect.selectOption(options[0].value);
  await draft.getByText('sample.csv', { exact: true }).waitFor();
  assert.equal(await stepSelect.inputValue(), options[0].value);
  const chosenSource = await draft.getByLabel('original/source.py', { exact: true }).inputValue();
  assert.match(chosenSource, /maxDrawdown/);
  assert.doesNotMatch(chosenSource, /summaryRows/);
  await narrow(author);
  await draft.locator('.cap-capture-step').screenshot({ path: out + '/capture-step-mobile.png' });
  await author.setViewportSize({ width: 1440, height: 1000 });
  evidence.captureSteps = { runId, count: options.length, defaultStep: options[1].value, selectedStep: options[0].value, unsavedEditPreservedOnCancel: true };

  // A lost response must recover the committed copy, without a second duplicate prompt.
  const preview = await post(author, '/community/capabilities/preview', fixture.refs[0]);
  const publication = await post(author, '/community/capabilities', { ...fixture.refs[0], snapshotDigest: preview.snapshotDigest, confirmSharing: true });
  const copyPath = '/community/capabilities/' + publication.releaseId + '/copy';
  await reader.getByRole('button', { name: '共创', exact: true }).click();
  const card = reader.locator('.community-card').filter({ has: reader.getByRole('heading', { name: 'sample-reader', exact: true }) });
  const attempts = []; let prompts = 0;
  const onDialog = dialog => { prompts++; return dialog.dismiss(); };
  reader.on('dialog', onDialog);
  await reader.route('**/desktop/api' + copyPath, async route => {
    attempts.push({ body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key'] });
    const response = await route.fetch();
    if (attempts.length === 1) await route.abort('failed'); else await route.fulfill({ response });
  });
  await card.getByRole('button', { name: '保存个人副本' }).click();
  await reader.getByRole('alert').waitFor();
  const countAfterLoss = (await get(reader, '/capabilities/assets?limit=100')).items.length;
  await card.getByRole('button', { name: '保存个人副本' }).click();
  await reader.getByText(/已复制到我的组件/).waitFor();
  const copies = (await get(reader, '/capabilities/assets?limit=100')).items;
  assert.equal(countAfterLoss, 1); assert.equal(copies.length, 1);
  assert.equal(prompts, 0); assert.equal(attempts.length, 2);
  assert.deepEqual(attempts[0], attempts[1]);
  reader.off('dialog', onDialog); await reader.unroute('**/desktop/api' + copyPath);
  evidence.lostCopyResponse = { countAfterLoss, countAfterRetry: copies.length, duplicatePrompts: prompts, attempts };

  // Pending fields are disabled; a failed save retains the same editable draft.
  await author.getByRole('button', { name: '应用', exact: true }).click();
  await author.getByRole('button', { name: '新建研究应用', exact: true }).click();
  const editor = author.locator('.recipe-editor');
  await editor.getByLabel('应用名称', { exact: true }).fill('失败恢复研究应用');
  await editor.getByLabel('用途说明', { exact: true }).fill('保存失败应保留所有编辑');
  await editor.getByLabel('可重复使用的任务模板').fill('解释指标{{指标}}的作用');
  await editor.getByLabel('必须遵守的规则（每行一条）').fill('保留本次规则');
  let release, arrived;
  const held = new Promise(resolve => { release = resolve; });
  const sent = new Promise(resolve => { arrived = resolve; });
  await author.route('**/desktop/api/evolution/recipes', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    arrived(); await held;
    await route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ error: '隔离验收：暂时无法保存', code: 'TEMPORARILY_UNAVAILABLE' }) });
  });
  await editor.getByRole('button', { name: '审阅并保存应用', exact: true }).click(); await sent;
  assert.equal(await editor.locator('input:not(:disabled),textarea:not(:disabled)').count(), 0);
  await author.screenshot({ path: out + '/pending-save-protected.png', fullPage: true });
  release(); await author.getByRole('alert').filter({ hasText: '隔离验收：暂时无法保存' }).waitFor();
  assert.ok(await editor.getByLabel('应用名称', { exact: true }).isEnabled());
  assert.equal(await editor.getByLabel('应用名称', { exact: true }).inputValue(), '失败恢复研究应用');
  assert.equal(await editor.getByLabel('必须遵守的规则（每行一条）').inputValue(), '保留本次规则');
  await author.unroute('**/desktop/api/evolution/recipes');
  await editor.getByRole('button', { name: '审阅并保存应用', exact: true }).click();
  await author.locator('.recipe-card').filter({ hasText: '失败恢复研究应用' }).waitFor();
  const saved = (await get(author, '/evolution/recipes')).items.filter(item => item.name === '失败恢复研究应用');
  assert.equal(saved.length, 1); assert.deepEqual(saved[0].constraints, ['保留本次规则']);
  evidence.failedSave = { pendingFieldsDisabled: true, draftRetained: true, countAfterRetry: saved.length };

  // Two same-source slots select separate assets in both import and community copy.
  const authorCopy = await post(author, copyPath, { confirmDuplicate: true });
  const secondReaderCopy = await post(reader, copyPath, { confirmDuplicate: true });
  const sourceRecipe = await post(author, '/evolution/recipes', { name: '相同来源两个槽位', goalTemplate: '解释指标两个独立副本', capabilityRefs: [fixture.refs[0], { assetId: authorCopy.assetId, versionId: authorCopy.versionId }] });
  const exported = await get(author, '/evolution/recipes/' + sourceRecipe.id + '/export');
  assert.equal(exported.dependencies.length, 2);
  assert.notEqual(exported.dependencies[0].bindingId, exported.dependencies[1].bindingId);
  assert.equal(exported.dependencies[0].sourceIdentity, exported.dependencies[1].sourceIdentity);
  await reader.getByRole('button', { name: '应用', exact: true }).click();
  await reader.locator('.recipe-library input[type=file]').setInputFiles({ name: 'two-slots.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(exported)) });
  const bindings = reader.locator('.recipe-editor select');
  await bindings.nth(0).selectOption(copies[0].assetId + '@' + copies[0].latestVersionId);
  assert.equal(await bindings.nth(1).evaluate(select => select.selectedIndex), 0, 'Selecting slot one must not choose slot two');
  await bindings.nth(1).selectOption(secondReaderCopy.assetId + '@' + secondReaderCopy.versionId);
  assert.equal(await bindings.nth(0).inputValue(), copies[0].assetId + '@' + copies[0].latestVersionId);
  await reader.getByRole('button', { name: '导入并保存（不运行）', exact: true }).click();
  await reader.getByText(/已导入「相同来源两个槽位」/).waitFor();
  const imported = (await get(reader, '/evolution/recipes')).items.find(item => item.name === sourceRecipe.name);
  assert.equal(new Set(imported.capabilityRefs.map(item => item.assetId)).size, 2);
  // Validate the author's second Tool through real WASI before publishing its app.
  const copyVersion = await get(author, '/capabilities/assets/' + authorCopy.assetId + '/versions/' + authorCopy.versionId);
  let job = await post(author, '/capabilities/verifications', { assetId: authorCopy.assetId, versionId: authorCopy.versionId, configurationId: copyVersion.configurationId, input: {}, fileIds: [], expectation: { mode: 'json_equals', value: { rows: 5, source: 'synthetic-fixture', synthetic: true } } });
  for (let count = 0; ['queued', 'running'].includes(job.status) && count < 300; count++) {
    await author.waitForTimeout(50); job = await get(author, '/capabilities/jobs/' + job.jobId);
  }
  assert.equal(job.result?.status, 'passed');
  const authorAsset = await get(author, '/capabilities/assets/' + authorCopy.assetId);
  const enabled = await author.request.patch(base + '/desktop/api/capabilities/assets/' + authorCopy.assetId, { data: { enabled: true, activeVersionId: authorCopy.versionId }, headers: { 'If-Match': '"' + authorAsset.revision + '"' } });
  assert.ok(enabled.ok(), await enabled.text());
  const run = await post(author, '/evolution/recipes/' + sourceRecipe.id + '/run', { input: {} });
  for (let count = 0; count < 300; count++) {
    const state = await get(author, '/sub-agent-runs/' + run.id);
    if (state.status === 'succeeded') break;
    assert.ok(['queued', 'running', 'created', 'planning'].includes(state.status), JSON.stringify(state));
    await author.waitForTimeout(50);
  }
  const appPreview = await post(author, '/evolution/publications/preview', { kind: 'recipe', recipeId: sourceRecipe.id });
  await post(author, '/evolution/publications', { kind: 'recipe', recipeId: sourceRecipe.id, snapshotDigest: appPreview.snapshotDigest, confirmSharing: true });
  await reader.getByRole('button', { name: '共创', exact: true }).click();
  await reader.locator('.community-card').filter({ hasText: sourceRecipe.name }).getByRole('button', { name: '保存个人副本', exact: true }).click();
  const communityBindings = reader.locator('.community-dependency-row select');
  await communityBindings.nth(1).waitFor();
  await communityBindings.nth(0).selectOption(copies[0].assetId + '@' + copies[0].latestVersionId);
  assert.equal(await communityBindings.nth(1).evaluate(select => select.selectedIndex), 0);
  await communityBindings.nth(1).selectOption(secondReaderCopy.assetId + '@' + secondReaderCopy.versionId);
  await narrow(reader);
  await reader.locator('.community-dependency-help').screenshot({ path: out + '/two-slots-mobile.png' });
  await reader.getByRole('button', { name: '完成准备，重试复制', exact: true }).click();
  await reader.getByText(/已复制到我的应用/).waitFor();
  const allCopiedRecipes = (await get(reader, '/evolution/recipes')).items.filter(item => item.name === sourceRecipe.name);
  assert.equal(allCopiedRecipes.length, 2);
  assert.ok(allCopiedRecipes.every(item => new Set(item.capabilityRefs.map(ref => ref.assetId)).size === 2));
  evidence.sameSourceSlots = { bindingIds: exported.dependencies.map(item => item.bindingId), importedRefs: imported.capabilityRefs, communityCopyRefs: allCopiedRecipes.at(-1).capabilityRefs };

  // Remove a deleted reference that no longer appears in the available component list.
  const broken = await post(author, '/evolution/recipes', { name: '修复失效组件的应用', goalTemplate: '解释指标{{指标}}', capabilityRefs: [fixture.refs[0]] });
  const asset = await get(author, '/capabilities/assets/' + fixture.refs[0].assetId);
  const deleted = await author.request.delete(base + '/desktop/api/capabilities/assets/' + fixture.refs[0].assetId, { headers: { 'If-Match': '"' + asset.revision + '"' } });
  assert.ok(deleted.ok(), await deleted.text());
  await author.getByRole('button', { name: '共创', exact: true }).click();
  await author.getByRole('button', { name: '应用', exact: true }).click();
  await author.locator('.recipe-card').filter({ hasText: broken.name }).getByRole('button', { name: '改编', exact: true }).click();
  const missing = author.locator('.recipe-selected-component.missing');
  await missing.waitFor(); assert.match(await missing.innerText(), new RegExp(fixture.refs[0].assetId));
  await narrow(author);
  await missing.screenshot({ path: out + '/missing-component-mobile.png' });
  await missing.getByRole('button', { name: '取消组件 ' + fixture.refs[0].assetId, exact: true }).click();
  assert.equal(await author.locator('.recipe-selected-component').count(), 0);
  await author.locator('.recipe-editor summary').filter({ hasText: '组合我的组件' }).click();
  await author.locator('.recipe-editor input[type=checkbox][value="' + fixture.refs[1].assetId + '"]').check();
  await author.locator('.recipe-editor').getByRole('button', { name: '审阅并保存应用', exact: true }).click();
  await author.locator('.recipe-card').filter({ hasText: broken.name + ' · 改编' }).waitFor();
  const repaired = (await get(author, '/evolution/recipes')).items.find(item => item.name === broken.name + ' · 改编');
  assert.deepEqual(repaired.capabilityRefs, [fixture.refs[1]]);
  evidence.deletedDependency = { visibleBeforeRemoval: true, replacedWith: repaired.capabilityRefs };
  return evidence;
}
