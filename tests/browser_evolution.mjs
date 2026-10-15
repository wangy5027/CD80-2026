import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { recoveryChecks } from './browser_evolution_recovery.mjs';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const base = 'http://127.0.0.1:' + (process.env.EVOLUTION_BROWSER_PORT || '49005'), out = process.env.EVOLUTION_BROWSER_OUTPUT || 'data/evolution-browser';
await mkdir(out, { recursive: true });
await writeFile(out + '/report.json', JSON.stringify({ passed: false, status: 'running', startedAt: new Date().toISOString() }, null, 2));
const errors = [];
const author = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const reader = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
for (const page of [author, reader]) page.on('pageerror', error => errors.push(error.message));
async function login(page, email) {
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill(email);
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click();
  await page.getByRole('button', { name: '共创', exact: true }).waitFor();
}
async function post(page, path, data) {
  const response = await page.request.post(base + '/desktop/api' + path, { data, headers: { 'Idempotency-Key': crypto.randomUUID() } });
  const value = await response.json();
  assert.ok(response.ok(), JSON.stringify(value));
  return value;
}
async function narrow(page) {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'The page must fit a narrow viewport');
}
try {
  if (process.env.EVOLUTION_BROWSER_RECOVERY_ONLY === '1') {
    const evidence = await recoveryChecks({ author, reader, base, out, login, post, narrow });
    assert.deepEqual(errors, []);
    await writeFile(out + '/report.json', JSON.stringify({ passed: true, status: 'completed', completedAt: new Date().toISOString(), realWasi: true, realModel: false,
      cases: ['lost copy response restores original body and key', 'pending save protects fields and failed save preserves draft',
        'deleted dependency remains visible and can be replaced', 'same-source slots select independent copies in import and community',
        'successful capture step selection and unsaved-edit cancellation', 'recovery controls fit narrow screens'], evidence, errors }, null, 2));
    console.log('Evolution recovery browser acceptance passed');
  } else {
  const fixture = await (await author.request.get(base + '/__fixture__/state')).json();
  await login(author, 'evolve-author@example.invalid');
  await author.locator('#composer-input').fill(fixture.message);
  await author.getByRole('button', { name: '发送消息', exact: true }).click();
  const completed = author.locator('.sub-task[data-status=succeeded]').first();
  await completed.waitFor({ timeout: 90000 });
  const sourceRunId = await completed.getAttribute('data-run-id');
  const sourceRun = await (await author.request.get(base + '/desktop/api/sub-agent-runs/' + sourceRunId)).json();
  const sourceTask = await (await author.request.get(base + '/desktop/api/tasks/' + sourceRun.conversationId)).json();
  await completed.getByRole('button', { name: '保存为应用', exact: true }).click();
  const editor = author.locator('.recipe-editor');
  await editor.getByLabel('应用名称', { exact: true }).fill('浏览器研究应用');
  await editor.getByLabel('可重复使用的任务模板').fill('解释指标{{指标}}用于{{场景}}');
  await editor.getByRole('button', { name: '审阅并保存应用' }).click();
  const card = author.locator('.recipe-card').filter({ hasText: '浏览器研究应用' });
  await card.waitFor();
  await card.getByRole('button', { name: '填写参数并运行' }).click();
  await author.locator('.recipe-editor').getByLabel('指标', { exact: true }).fill('收益率');
  await author.locator('.recipe-editor').getByLabel('场景', { exact: true }).fill('全新样例B');
  await author.getByRole('button', { name: '按这些输入运行' }).click();
  await author.getByText(/本次实际输入：解释指标收益率用于全新样例B/).first().waitFor();
  await author.getByRole('button', { name: '应用', exact: true }).click();
  await card.getByRole('button', { name: '发布', exact: true }).click();
  await author.getByRole('button', { name: '检查发布内容' }).click();
  await author.locator('.release-confirm input').check();
  await author.getByRole('button', { name: '确认发布到共创' }).click();
  await author.getByText(/已发布到共创目录/).first().waitFor();
  const kit = await author.request.get(base + '/desktop/api/evolution/developer-kit');
  assert.equal(kit.status(), 200);
  assert.match(kit.headers()['content-type'], /zip/);
  await writeFile(out + '/developer-kit.zip', await kit.body());
  // Promote actual successful code through the visible review/save flow.
  await author.getByRole('button', { name: sourceTask.title, exact: true }).click();
  await author.locator('.sub-task[data-run-id="' + sourceRunId + '"]').getByRole('button', { name: '沉淀为 Tool', exact: true }).click();
  const draft = author.locator('.cap-draft-editor');
  await draft.getByLabel('名称', { exact: true }).fill('浏览器沉淀分析工具');
  await draft.getByText('original/source.py', { exact: true }).click();
  const sourceEditor = draft.getByLabel('original/source.py', { exact: true });
  await sourceEditor.fill((await sourceEditor.inputValue()) + '\n# reviewed-and-edited-in-browser\n');
  await narrow(author);
  await author.screenshot({ path: out + '/capture-mobile.png', fullPage: true });
  await author.setViewportSize({ width: 1440, height: 1000 });
  await draft.getByRole('button', { name: '审阅并保存', exact: true }).click();
  await author.locator('.cap-detail').getByRole('heading', { name: '浏览器沉淀分析工具', exact: true }).waitFor();
  const personalAssets = (await (await author.request.get(base + '/desktop/api/capabilities/assets?scope=personal&limit=100')).json()).items;
  const promotedTool = personalAssets.find(item => item.name === '浏览器沉淀分析工具');
  assert.ok(promotedTool && !promotedTool.enabled, 'Captured code must be saved independently and remain disabled');
  const sourceFile = await author.request.get(base + '/desktop/api/capabilities/assets/' + promotedTool.assetId + '/versions/' + promotedTool.latestVersionId + '/files/original/source.py');
  assert.match(await sourceFile.text(), /reviewed-and-edited-in-browser/);
  await author.screenshot({ path: out + '/captured-tool-review.png', fullPage: true });
  await author.getByLabel('上传测试文件').setInputFiles({ name: 'fresh-prices.csv', mimeType: 'text/csv', buffer: Buffer.from('symbol,date,close\nNEW,2023-01-01,10\nNEW,2023-01-02,20\nNEW,2023-01-03,15\n') });
  await author.getByText('文件已上传并授权本次测试，尚未执行。', { exact: true }).waitFor();
  await author.getByLabel('从授权文件填入路径').selectOption({ label: 'fresh-prices.csv' });
  await author.getByLabel('设定预期结果，重新运行并验证', { exact: true }).check();
  await author.getByLabel('预期结果（JSON）').fill(JSON.stringify({ rows: 3, change: '0.5', maxDrawdown: '0.25', prices: ['10', '20', '15'], dates: ['2023-01-01', '2023-01-02', '2023-01-03'], synthetic: true }));
  await author.getByRole('button', { name: '开始验证', exact: true }).click();
  await author.getByRole('heading', { name: '本次测试通过', exact: true }).waitFor();
  await author.screenshot({ path: out + '/captured-tool-new-input.png', fullPage: true });
  await author.getByRole('button', { name: '启用并用于对话', exact: true }).click();
  await author.getByRole('button', { name: '移除 浏览器沉淀分析工具', exact: true }).waitFor();
  // Review the exact source snapshot for a captured Skill and its Tool dependency.
  await author.getByRole('button', { name: sourceTask.title, exact: true }).click();
  await author.locator('.sub-task[data-run-id="' + sourceRunId + '"]').getByRole('button', { name: '沉淀为 Skill', exact: true }).click();
  await author.locator('.cap-draft-editor').getByLabel('名称', { exact: true }).fill('浏览器沉淀分析方法');
  await author.locator('.cap-draft-editor').getByRole('button', { name: '审阅并保存', exact: true }).click();
  await author.locator('.cap-detail').getByRole('heading', { name: '浏览器沉淀分析方法', exact: true }).waitFor();
  await author.getByLabel('填写验收标准，运行后确认结果', { exact: true }).check();
  await author.getByLabel('测试问题', { exact: true }).fill('Explain the sample method.');
  await author.getByLabel('验收标准', { exact: true }).fill('Reader, change, drawdown and synthetic label.');
  await author.getByRole('button', { name: '开始验证', exact: true }).click();
  await author.getByRole('button', { name: '确认符合预期', exact: true }).click();
  await author.getByRole('button', { name: '发布到共创', exact: true }).click();
  await author.getByRole('button', { name: '检查发布内容', exact: true }).click();
  const sourceBundles = author.locator('.release-source-bundle');
  await sourceBundles.first().waitFor();
  assert.equal(await sourceBundles.count(), 2);
  const rootSource = sourceBundles.filter({ has: author.getByRole('heading', { name: /本次发布的组件/ }) });
  const dependencySource = sourceBundles.filter({ has: author.getByRole('heading', { name: /同时分享的依赖组件/ }) });
  await rootSource.locator('summary').filter({ hasText: 'original/SKILL.md' }).click();
  assert.match(await rootSource.locator('.release-source-file').filter({ has: author.locator('summary').filter({ hasText: 'original/SKILL.md' }) }).locator('pre').innerText(), /重新获取并核查实际数据/);
  await dependencySource.locator('summary').filter({ hasText: 'original/tool.py' }).click();
  assert.match(await dependencySource.locator('.release-source-file').filter({ has: author.locator('summary').filter({ hasText: 'original/tool.py' }) }).locator('pre').innerText(), /read_bytes/);
  await dependencySource.locator('summary').filter({ hasText: 'original/sample.csv' }).click();
  assert.match(await dependencySource.locator('.release-source-file').filter({ has: author.locator('summary').filter({ hasText: 'original/sample.csv' }) }).locator('pre').innerText(), /000001.SZ,2022-01-04,10.00/);
  assert.ok(await author.getByRole('button', { name: '确认发布到共创', exact: true }).isDisabled());
  await author.screenshot({ path: out + '/publication-full-source-review.png', fullPage: true });
  await narrow(author);
  await author.screenshot({ path: out + '/publication-source-mobile.png', fullPage: true });
  await author.setViewportSize({ width: 1440, height: 1000 });
  // External application creation uses a reviewed package; importing must not run it.
  await author.getByRole('button', { name: '应用', exact: true }).click();
  const beforeRuns = (await (await author.request.get(base + '/desktop/api/agent-runs')).json()).runs.length;
  await author.locator('.recipe-library input[type=file]').setInputFiles({ name: 'external.application.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(fixture.package)) });
  // The save reaches the server but its response is lost; retry must reuse its identity.
  await author.route('**/desktop/api/agents/import', async route => {
    const saved = await route.fetch(); assert.equal(saved.status(), 201);
    await route.abort('connectionreset');
  }, { times: 1 });
  await author.getByRole('button', { name: '导入并保存（不运行）', exact: true }).click();
  await author.getByRole('alert').filter({ hasText: '无法连接网站后端' }).waitFor();
  await author.getByRole('button', { name: '导入并保存（不运行）', exact: true }).click();
  const externalCard = author.locator('.agent-catalog-card').filter({ has: author.getByRole('heading', { name: fixture.package.definition.name, exact: true }) });
  await externalCard.waitFor();
  assert.equal(await externalCard.count(), 1, 'Retry after a lost response must not create a duplicate application');
  assert.equal((await (await author.request.get(base + '/desktop/api/agent-runs')).json()).runs.length, beforeRuns);
  await externalCard.getByRole('button', { name: '填写参数并运行', exact: true }).click();
  const invoke = author.locator('.agent-run-card').filter({ has: author.getByRole('heading', { name: fixture.package.definition.name + ' · 版本 1', exact: true }) });
  const choosingFile = author.waitForEvent('filechooser');
  await invoke.getByRole('button', { name: '上传新文件', exact: true }).click();
  await (await choosingFile).setFiles({ name: 'external-input.csv', mimeType: 'text/csv', buffer: Buffer.from('category,amount\nA,10.25\nB,7.50\nA,4.75\n') });
  await invoke.getByLabel('分组列', { exact: false }).fill('category');
  await invoke.getByLabel('求和列', { exact: false }).fill('amount');
  await invoke.locator('select').first().locator('option', { hasText: 'external-input.csv' }).waitFor({ state: 'attached' });
  await invoke.getByRole('button', { name: '用这些输入直接运行', exact: true }).click();
  const externalRun = author.locator('.agent-run-card').filter({ has: author.getByRole('heading', { name: fixture.package.definition.name, exact: true }) });
  await externalRun.locator('.agent-status.succeeded').waitFor({ timeout: 90000 });
  const artifactUrl = await externalRun.locator('.agent-artifacts a').first().getAttribute('href');
  const artifact = await author.request.get(base + artifactUrl);
  assert.equal(artifact.status(), 200); assert.match(await artifact.text(), /A,15\.00/); assert.match(await artifact.text(), /B,7\.50/);
  await author.screenshot({ path: out + '/external-application-result.png', fullPage: true });
  await author.getByRole('button', { name: '应用', exact: true }).click();
  const exported = await author.request.get(base + await externalCard.getByRole('link', { name: '导出应用包' }).getAttribute('href'));
  assert.equal(exported.status(), 200); const exportedPackage = await exported.json();
  assert.equal(exportedPackage.kind, 'pipeline'); assert.equal(exportedPackage.definition.id, undefined);
  await writeFile(out + '/exported-pipeline.json', JSON.stringify(exportedPackage, null, 2));
  // Replanning can start without satisfying the source app's required old file fields.
  await externalCard.getByRole('button', { name: '对话改编', exact: true }).click();
  await author.getByLabel('希望保留什么，改变什么？').fill('保留CSV求和，先重新设计输入，稍后选择新文件。');
  await author.getByRole('button', { name: '先生成流程，再补参数', exact: true }).click();
  await author.locator('.agent-status.awaiting_input').waitFor({ timeout: 60000 });
  // Share source components so the recipient can resolve the recipe's precise dependencies.
  for (const ref of fixture.refs) {
    const body = { ...ref };
    const preview = await post(author, '/community/capabilities/preview', body);
    await post(author, '/community/capabilities', { ...body, snapshotDigest: preview.snapshotDigest, confirmSharing: true });
  }
  await author.getByRole('button', { name: '共创', exact: true }).click();
  await author.locator('.community-card').first().waitFor();
  assert.equal(await author.locator('.community-card').count(), 3);
  await author.screenshot({ path: out + '/community-desktop.png', fullPage: true });
  await login(reader, 'evolve-reader@example.invalid');
  await reader.getByRole('button', { name: '共创', exact: true }).click();
  await reader.locator('.community-card').filter({ hasText: '浏览器研究应用' }).getByRole('button', { name: '保存个人副本' }).click();
  await reader.getByRole('alert').filter({ hasText: '请先引入精确版本' }).waitFor();
  for (const name of ['sample-reader', 'sample-guide']) {
    await reader.locator('.community-card').filter({ has: reader.getByRole('heading', { name, exact: true }) }).getByRole('button', { name: '保存个人副本' }).click();
    await reader.getByText('已复制到我的组件。请检查依赖、验证效果，再启用使用。', { exact: false }).waitFor();
  }
  await reader.locator('.community-card').filter({ hasText: '浏览器研究应用' }).getByRole('button', { name: '保存个人副本' }).click();
  await reader.getByText(/已复制到我的应用/).waitFor();
  await reader.getByRole('button', { name: '打开我的副本 →' }).click();
  await reader.locator('.recipe-card').filter({ hasText: '浏览器研究应用' }).waitFor();
  const copied = (await (await reader.request.get(base + '/desktop/api/capabilities/assets')).json()).items;
  assert.ok(copied.every(item => item.enabled === false), 'Shared assets must not inherit author enablement');
  await reader.locator('.recipe-card').filter({ hasText: '浏览器研究应用' }).getByRole('button', { name: '填写参数并运行' }).click();
  await reader.locator('.recipe-editor').getByLabel('指标', { exact: true }).fill('回撤');
  await reader.locator('.recipe-editor').getByLabel('场景', { exact: true }).fill('新场景');
  await reader.getByRole('button', { name: '按这些输入运行' }).click();
  await reader.getByRole('alert').filter({ hasText: /禁用|验证/ }).waitFor();
  await reader.screenshot({ path: out + '/copied-requires-validation.png', fullPage: true });
  // The recipient verifies their own copies through the actual test/enable UI.
  for (const [name, type] of [['sample-reader', 'Tools'], ['sample-guide', 'Skills']]) {
    await reader.getByRole('button', { name: '扩展', exact: true }).click();
    await reader.getByRole('tab', { name: new RegExp(type) }).click();
    await reader.locator('.cap-card').filter({ has: reader.getByRole('heading', { name, exact: true }) }).click();
    if (type === 'Tools') {
      await reader.getByLabel('设定预期结果，重新运行并验证', { exact: true }).check();
      await reader.getByLabel('预期结果（JSON）').fill(JSON.stringify({ rows: 5, source: 'synthetic-fixture', synthetic: true }));
    } else {
      await reader.getByLabel('填写验收标准，运行后确认结果', { exact: true }).check();
      await reader.getByLabel('测试问题', { exact: true }).fill('Explain the sample method.');
      await reader.getByLabel('验收标准', { exact: true }).fill('Reader, change, drawdown and synthetic label.');
    }
    await reader.getByRole('button', { name: '开始验证', exact: true }).click();
    if (type === 'Skills') {
      await reader.getByRole('button', { name: '确认符合预期', exact: true }).waitFor();
      assert.match(await reader.locator('.cap-result').innerText(), /sample reader.*drawdown.*synthetic/);
      await reader.getByRole('button', { name: '确认符合预期', exact: true }).click();
    }
    await reader.getByRole('heading', { name: '本次测试通过', exact: true }).waitFor();
    await reader.getByRole('button', { name: '启用并用于对话', exact: true }).click();
    await reader.getByRole('button', { name: '移除 ' + name, exact: true }).waitFor();
  }
  await reader.getByRole('button', { name: '应用', exact: true }).click();
  await reader.locator('.recipe-card').filter({ hasText: '浏览器研究应用' }).getByRole('button', { name: '填写参数并运行' }).click();
  await reader.locator('.recipe-editor').getByLabel('指标', { exact: true }).fill('回撤');
  await reader.locator('.recipe-editor').getByLabel('场景', { exact: true }).fill('读者验证后的新场景');
  await reader.getByRole('button', { name: '按这些输入运行' }).click();
  await reader.getByText('本次实际输入：解释指标回撤用于读者验证后的新场景', { exact: true }).first().waitFor();
  await reader.screenshot({ path: out + '/recipient-verified-run.png', fullPage: true });
  // Duplicate content must be bound explicitly, for both community copy and package import.
  await reader.getByRole('button', { name: '共创', exact: true }).click();
  reader.once('dialog', dialog => dialog.accept());
  await reader.locator('.community-card').filter({ has: reader.getByRole('heading', { name: 'sample-reader', exact: true }) }).getByRole('button', { name: '保存个人副本' }).click();
  await reader.getByText(/已复制到我的组件/).waitFor();
  await reader.locator('.community-card').filter({ hasText: '浏览器研究应用' }).getByRole('button', { name: '保存个人副本' }).click();
  const binding = reader.getByLabel('fixture/tool 的副本', { exact: true });
  await binding.waitFor();
  assert.equal(await binding.locator('option').count(), 3);
  await reader.locator('.community-dependency-row').filter({ hasText: 'fixture/tool' }).getByText('已有可用版本；多个副本时请明确选择', { exact: true }).waitFor();
  await narrow(reader);
  await reader.screenshot({ path: out + '/dependency-mobile.png', fullPage: true });
  const readyTool = copied.find(item => item.kind === 'tool');
  await binding.selectOption(readyTool.assetId + '@' + readyTool.latestVersionId);
  await reader.getByRole('button', { name: '完成准备，重试复制', exact: true }).click();
  await reader.getByText(/已复制到我的应用/).waitFor();
  await reader.setViewportSize({ width: 1440, height: 1000 });
  await reader.screenshot({ path: out + '/community-explicit-binding.png', fullPage: true });
  await reader.getByRole('button', { name: '应用', exact: true }).click();
  const authorRecipes = (await (await author.request.get(base + '/desktop/api/evolution/recipes')).json()).items;
  const recipeExport = await (await author.request.get(base + '/desktop/api/evolution/recipes/' + authorRecipes[0].id + '/export')).json();
  await reader.locator('.recipe-library input[type=file]').setInputFiles({ name: 'research.application.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(recipeExport)) });
  await reader.getByRole('button', { name: '导入并保存（不运行）', exact: true }).click();
  await reader.getByRole('alert').filter({ hasText: /多个|副本/ }).waitFor();
  await reader.locator('.recipe-editor label').filter({ hasText: 'fixture/tool' }).locator('select').selectOption(readyTool.assetId + '@' + readyTool.latestVersionId);
  await reader.getByRole('button', { name: '导入并保存（不运行）', exact: true }).click();
  await reader.getByText(/已导入「浏览器研究应用」/).waitFor();
  // An exported external pipeline also runs independently in the other account.
  await reader.locator('.recipe-library input[type=file]').setInputFiles({ name: 'exported.application.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify(exportedPackage)) });
  await reader.getByRole('button', { name: '导入并保存（不运行）', exact: true }).click();
  const readerPipeline = reader.locator('.agent-catalog-card').filter({ has: reader.getByRole('heading', { name: exportedPackage.definition.name, exact: true }) });
  await readerPipeline.getByRole('button', { name: '填写参数并运行', exact: true }).click();
  const readerInvoke = reader.locator('.agent-run-card').filter({ has: reader.getByRole('heading', { name: exportedPackage.definition.name + ' · 版本 1', exact: true }) });
  const readerFileChooser = reader.waitForEvent('filechooser');
  await readerInvoke.getByRole('button', { name: '上传新文件', exact: true }).click();
  await (await readerFileChooser).setFiles({ name: 'reader-input.csv', mimeType: 'text/csv', buffer: Buffer.from('group,value\nR,4.20\nR,5.80\nS,2.00\n') });
  await readerInvoke.getByLabel('分组列', { exact: false }).fill('group');
  await readerInvoke.getByLabel('求和列', { exact: false }).fill('value');
  await readerInvoke.locator('select').first().locator('option', { hasText: 'reader-input.csv' }).waitFor({ state: 'attached' });
  await readerInvoke.getByRole('button', { name: '用这些输入直接运行', exact: true }).click();
  const readerRun = reader.locator('.agent-run-card').filter({ has: reader.getByRole('heading', { name: exportedPackage.definition.name, exact: true }) });
  await readerRun.locator('.agent-status.succeeded').waitFor({ timeout: 90000 });
  const readerArtifactUrl = await readerRun.locator('.agent-artifacts a').first().getAttribute('href');
  const readerArtifact = await (await reader.request.get(base + readerArtifactUrl)).text();
  assert.match(readerArtifact, /R,10\.00/); assert.match(readerArtifact, /S,2\.00/); assert.doesNotMatch(readerArtifact, /A,15\.00/);
  await reader.screenshot({ path: out + '/reader-exported-pipeline-new-input.png', fullPage: true });
  // Browse/search beyond the server's first page, without relying on the initial cached cards.
  const seeded = await author.request.post(base + '/__fixture__/seed-catalog');
  assert.equal(seeded.status(), 200);
  const firstCatalogPage = await (await author.request.get(base + '/desktop/api/community/capabilities?limit=100')).json();
  assert.ok(firstCatalogPage.nextCursor);
  assert.ok(!firstCatalogPage.items.some(item => item.title === 'sample-reader'));
  await author.getByRole('button', { name: '刷新', exact: true }).click();
  await author.locator('.community-card').filter({ has: author.getByRole('heading', { name: 'sample-reader', exact: true }) }).waitFor();
  await author.getByRole('searchbox', { name: '搜索共创成果' }).fill('sample-reader');
  await author.getByRole('tab', { name: 'Tools', exact: true }).click();
  assert.equal(await author.locator('.community-card').count(), 1);
  await author.getByLabel('我的发布', { exact: true }).check();
  await author.locator('.community-card').filter({ has: author.getByRole('heading', { name: 'sample-reader', exact: true }) }).waitFor();
  await author.screenshot({ path: out + '/catalog-after-first-page.png', fullPage: true });
  await narrow(author);
  await author.screenshot({ path: out + '/community-mobile.png', fullPage: true });
  assert.deepEqual(errors, []);
  await writeFile(out + '/report.json', JSON.stringify({ passed: true, status: 'completed', completedAt: new Date().toISOString(), realWasi: true, realModel: false,
    cases: ['task capture', 'new parameter run', 'reviewed publication', 'dependency missing', 'cross-account copy',
            'no inherited enablement', 'unverified run blocked', 'developer-kit download', 'code promotion to disabled tool',
            'external package review and import without execution', 'external package actual WASI output', 'portable export',
            'adaptation before new inputs', 'editable captured source saved', 'captured tool verified on new file and enabled',
            'recipient tool validation and skill review', 'recipient new parameter run after enabling',
            'community and imported research app duplicate dependency binding', 'lost import response retry without duplicate',
            'exported pipeline imported by recipient and run with new inputs', 'captured Skill verified before publication preview',
            'root and dependency full source review', 'search and type/mine filters discover beyond first catalog page',
            'desktop and mobile'], errors }, null, 2));
  console.log('Evolution browser acceptance passed');
  }
} catch (error) {
  await writeFile(out + '/report.json', JSON.stringify({ passed: false, status: 'failed', completedAt: new Date().toISOString(), error: error.message, errors }, null, 2));
  await author.screenshot({ path: out + '/failure-author.png', fullPage: true });
  await reader.screenshot({ path: out + '/failure-reader.png', fullPage: true });
  throw error;
} finally { await browser.close(); }
