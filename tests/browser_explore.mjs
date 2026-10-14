/**
 * Browser acceptance for the built Explore UI against browser_explore_fixture.py.
 * Synthetic news and model responses are always identified as test substitutes.
 *
 * Start the fixture server, then run:
 * PLAYWRIGHT_MODULE_DIR=/path/to/node_modules node tests/browser_explore.mjs
 *
 * This script never points at a production server. Business operations happen
 * through the actual page; read-only API checks inspect persisted identities.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const playwright = moduleDir
  ? require(existsSync(path.join(moduleDir, 'playwright', 'package.json')) ? path.join(moduleDir, 'playwright') : moduleDir)
  : require('playwright');
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const output = path.resolve(process.env.EXPLORE_BROWSER_OUTPUT || path.join(root, 'data', 'explore-acceptance', stamp));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Refusing to browse a non-loopback server.');
await mkdir(output, { recursive: true });

const report = {
  startedAt: new Date().toISOString(), baseUrl: base.href,
  fixture: true, realNews: false, realModel: false,
  checks: [], screenshots: [], downloads: [], pageErrors: [],
};
let browser;
let context;
let page;
let currentStep = 'fixture availability';

async function fixtureStats() {
  const response = await fetch(new URL('/__explore_fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200, 'Start tests/browser_explore_fixture.py first.');
  const data = await response.json();
  assert.equal(data.fixture, true);
  assert.equal(data.realNews, false);
  assert.equal(data.realModel, false);
  assert.ok(Number.isInteger(data.clarifyModelCalls) && Number.isInteger(data.reportModelCalls),
    'The fixture must count clarification and report model calls separately.');
  assert.ok(Array.isArray(data.reportContextFileIds) && Array.isArray(data.lastReportContextFileIds),
    'The fixture must expose IDs of files actually sent to the synthetic report provider.');
  return data;
}

async function platformStats() {
  const response = await fetch(new URL('/__fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.fixture, true);
  assert.equal(data.realModel, false);
  return data;
}

async function waitStats(predicate, timeout = 10000) {
  const until = Date.now() + timeout;
  let value;
  while (Date.now() < until) {
    value = await fixtureStats();
    if (predicate(value)) return value;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail(`Explore fixture counters did not reach the expected value: ${JSON.stringify(value)}`);
}

async function step(name, action) {
  currentStep = name;
  console.log(`RUN ${name}`);
  const began = Date.now();
  const details = await action();
  report.checks.push({ name, passed: true, elapsedMs: Date.now() - began, ...(details || {}) });
  console.log(`PASS ${name}`);
}

async function screenshot(name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: true });
  report.screenshots.push(filename);
}

function testId(value) { return page.getByTestId(value); }

async function openRetrievalDetails() {
  const details = testId('explore-retrieval');
  await details.waitFor({ state: 'visible' });
  if (!(await details.evaluate(node => node.open))) await details.locator('summary').click();
  return details;
}

async function openUsedContextDetails() {
  const details = testId('explore-context-used');
  await details.waitFor({ state: 'visible' });
  if (!(await details.evaluate(node => node.open))) await details.locator('summary').click();
  return details;
}

function route() {
  const hash = new URL(page.url()).hash;
  const [name, raw = ''] = hash.split('?');
  assert.equal(name, '#explore');
  const parameters = new URLSearchParams(raw);
  return { sessionId: parameters.get('session') || '', runId: parameters.get('run') || '' };
}

async function apiGet(pathname, expectedStatus = 200) {
  const response = await context.request.get(new URL(pathname, base).href);
  assert.equal(response.status(), expectedStatus, `${pathname} returned ${response.status()}`);
  return expectedStatus === 200 ? await response.json() : null;
}

async function storedRun(runId) {
  const data = await apiGet(`/desktop/api/explore/runs/${encodeURIComponent(runId)}`);
  return data.run || data;
}

async function reportFor(runId) {
  const run = await storedRun(runId);
  assert.ok(run.report?.id, `Run ${runId} has no persisted report`);
  return run.report;
}

async function sessionFiles(sessionId) {
  const result = await apiGet(`/desktop/api/explore/sessions/${encodeURIComponent(sessionId)}/files`);
  assert.ok(Array.isArray(result.files));
  return result.files;
}

async function sessionFile(sessionId, fileId) {
  const result = await apiGet(`/desktop/api/explore/sessions/${encodeURIComponent(sessionId)}/files/${encodeURIComponent(fileId)}`);
  assert.ok(result.file?.id === fileId);
  return result.file;
}

function fileRow(file) {
  return testId('explore-file-list').locator(`.explore-file-item[data-file-id="${file.id}"]`);
}

function isRunCreate(response) {
  const url = new URL(response.url());
  return url.pathname.startsWith('/desktop/api/explore/sessions/')
    && url.pathname.endsWith('/runs') && response.request().method() === 'POST';
}

async function submit(module, question) {
  await testId(`explore-module-${module}`).click();
  await testId('explore-input').fill(question);
  const before = await fixtureStats();
  const sent = page.waitForResponse(isRunCreate);
  await testId('explore-submit').click();
  const response = await sent;
  assert.ok([200, 201, 202].includes(response.status()), `Run create returned ${response.status()}`);
  const payload = await response.json();
  const run = payload.run || payload;
  assert.ok(run?.id, 'Run create must return a persisted run ID.');
  await page.waitForFunction(expected => new URLSearchParams(location.hash.split('?')[1] || '').get('run') === expected,
    run.id, { timeout: 10000 });
  return { run, before, requestUrl: response.url(), requestBody: response.request().postDataJSON() };
}

async function awaitClarification(expectedRunId, previousReportCalls = null) {
  await testId('explore-clarification').waitFor({ state: 'visible', timeout: 30000 });
  assert.equal(route().runId, expectedRunId);
  const stored = await storedRun(expectedRunId);
  assert.equal(stored.status, 'awaiting_clarification');
  assert.equal(stored.report, null);
  assert.equal(stored.review?.needs_clarification, true);
  assert.ok(Number.isInteger(stored.review?.revision));
  assert.ok(Array.isArray(stored.materials) && stored.materials.length > 0);
  assert.ok(stored.retrieval?.searches?.length);
  if (previousReportCalls !== null) {
    assert.equal((await fixtureStats()).reportModelCalls, previousReportCalls,
      'A vague request must pause before the report model is called.');
  }
  return stored;
}

async function inspectMaterialsWindow(run, screenshotName = null) {
  await testId('explore-open-materials').click();
  const dialog = testId('explore-materials');
  await dialog.waitFor({ state: 'visible' });
  const body = await dialog.innerText();
  const withText = run.materials.find(item => item.text_used && item.url);
  assert.ok(withText, 'The synthetic retrieval must include linked text actually used for analysis.');
  assert.ok(body.includes(withText.title));
  assert.ok(body.includes(withText.text_used),
    'The review window must show the actual saved text_used, not only a generated label.');
  assert.ok(await dialog.locator(`a[href="${withText.url}"]`).count() >= 1,
    'The review window must link to the original material URL.');
  const readableText = dialog.locator('.explore-material-text').first();
  assert.equal(await readableText.getAttribute('tabindex'), '0');
  await readableText.focus();
  await page.keyboard.press('ArrowDown');
  if (screenshotName) await screenshot(screenshotName);
  await page.keyboard.press('Escape');
  await dialog.waitFor({ state: 'hidden' });
  return { linkedMaterialId: withText.id, contentKind: withText.content_kind,
    textLength: withText.text_used.length };
}

async function awaitReport(expectedRunId, previousReportCalls = null) {
  await testId('explore-report').waitFor({ state: 'visible', timeout: 30000 });
  assert.equal(route().runId, expectedRunId);
  assert.match(await testId('explore-report').innerText(), /合成测试报告|合成模型/);
  if (previousReportCalls !== null) {
    await waitStats(stats => stats.reportModelCalls >= previousReportCalls + 1);
  }
  const stored = await storedRun(expectedRunId);
  assert.ok(['succeeded', 'partial'].includes(stored.status), `Unexpected saved run status: ${stored.status}`);
  assert.ok(stored.report?.id, 'A visible report must be persisted on the server.');
  return stored;
}

async function clickNewExplore() {
  await openHistory();
  await testId('explore-new').click();
  assert.equal(route().sessionId, '');
  await testId('explore-input').waitFor({ state: 'visible' });
}

async function openHistory() {
  const drawer = testId('explore-history-drawer');
  if (!(await drawer.evaluate(node => node.classList.contains('open')))) {
    await testId('explore-history-toggle').click();
  }
  await drawer.waitFor({ state: 'visible' });
}

async function selectRunInHistory(question) {
  await openHistory();
  const row = testId('explore-run-list').locator('button.explore-run-item').filter({ hasText: question });
  assert.equal(await row.count(), 1, `Expected one saved run for ${question}`);
  await row.click();
  return route().runId;
}

async function register() {
  await page.goto(base.href);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('Explore 浏览器验收');
  await form.locator('input[name="email"]').fill(`explore-browser-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('fixture-explore-password');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('button', { name: '开始探索', exact: true }).waitFor({ state: 'visible' });
}

try {
  report.initialStats = await fixtureStats();
  const platformInitial = await platformStats();
  report.platformInitialStats = platformInitial;
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: process.env.BROWSER_HEADLESS !== '0' });
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.setDefaultNavigationTimeout(20000);
  page.on('pageerror', error => report.pageErrors.push(String(error)));

  await step('register with temporary account and enter Explore from home', async () => {
    await register();
    await screenshot('01-home-before-entry');
    await page.getByRole('button', { name: '开始探索', exact: true }).click();
    await testId('explore-workspace').waitFor({ state: 'visible' });
    await page.locator('.explore-welcome').waitFor({ state: 'visible' });
    assert.equal(route().sessionId, '');
    assert.equal(await testId('explore-input').count(), 1);
    assert.equal(await testId('explore-report').count(), 0);
    await screenshot('02-explore-empty');
  });

  await step('direct URL and refresh restore empty Explore without a model call', async () => {
    const before = await fixtureStats();
    await page.goto(new URL('#explore', base).href);
    await testId('explore-workspace').waitFor({ state: 'visible' });
    await page.reload();
    await testId('explore-workspace').waitFor({ state: 'visible' });
    assert.equal((await fixtureStats()).modelCalls, before.modelCalls);
  });

  let first;
  let firstRequest;
  await step('clear event request runs directly and renders a saved structured report', async () => {
    const created = await submit('event', '测试甲集团宣布收购测试乙公司；请梳理事件背景、时间线和影响。');
    first = created.run;
    firstRequest = { url: created.requestUrl, body: created.requestBody };
    const saved = await awaitReport(first.id, created.before.reportModelCalls);
    assert.equal(saved.report?.resolved_question, first.question);
    assert.ok(saved.materials.length >= 2, 'Retrieved materials must persist with the report.');
    assert.ok(saved.materials.some(item => item.kind === 'web_search'));
    assert.ok(saved.materials.some(item => item.kind === 'news'));
    assert.ok(!saved.stage_history.some(item => item.stage === 'awaiting_review'),
      'A clear request must not pause for material confirmation.');
    const inspected = await inspectMaterialsWindow(saved, '03a-materials-window');
    assert.ok(['search_snippet', 'news_excerpt', 'user_text'].includes(inspected.contentKind));
    const body = await testId('explore-report').innerText();
    assert.match(body, /核心发现|重要不确定性|事件概述|时间线/);
    assert.match(body, /测试甲集团/);
    await testId('explore-sources').waitFor({ state: 'visible' });
    await testId('explore-open-sources').click();
    const reportSources = testId('explore-source-list');
    await reportSources.waitFor({ state: 'visible' });
    assert.match(await reportSources.innerText(), /合成测试材料/);
    assert.match(await reportSources.innerText(), /发布时间：/);
    assert.doesNotMatch(await reportSources.innerText(), /类型：|检索时间：/);
    assert.equal(await reportSources.locator('.explore-source-preview .explore-source-number').count(), 0);
    assert.ok(await reportSources.locator('.explore-source-preview h3 a[href^="https://example.test/news/"]').count() >= 1);
    assert.equal(await reportSources.locator('.explore-source-preview > a, .explore-source-unavailable').count(), 0);
    await testId('explore-materials-close').click();
    await testId('explore-open-materials').click();
    const confirmedDialog = testId('explore-materials');
    await confirmedDialog.waitFor({ state: 'visible' });
    assert.equal(await confirmedDialog.getByTestId('explore-materials-select').count(), 0,
      'Saved report materials must be read-only.');
    await page.keyboard.press('Escape');
    assert.ok(route().sessionId);
    await screenshot('03-event-report');
    await testId('explore-sources').scrollIntoViewIfNeeded();
    await testId('explore-open-sources').click();
    await testId('explore-source-list').waitFor({ state: 'visible' });
    await screenshot('03b-event-sources');
    await testId('explore-materials-close').click();
    await page.locator('.explore-scroll').evaluate(element => { element.scrollTop = 0; });
    return { sessionId: route().sessionId, runId: first.id };
  });

  await step('event report displays the recorded web query, result status, and source types', async () => {
    const saved = await storedRun(first.id);
    assert.ok(saved.stage_history.some(item => item.stage === 'searching_web'),
      'The run must record its actual web-search stage.');
    const searches = saved.report?.retrieval?.searches;
    assert.ok(Array.isArray(searches) && searches.length >= 1,
      'The saved report must retain each web-search attempt.');
    const completed = searches.find(item => item.status === 'complete' && item.count > 0);
    assert.ok(completed, 'The synthetic web search should return at least one source.');
    assert.ok(completed.query && completed.freshness && completed.searched_at,
      'A completed search must include the query, freshness, and search time.');
    assert.ok((await fixtureStats()).webSearchCalls >= 1,
      'The persisted search record must correspond to a synthetic search-tool call.');
    const kinds = new Set(saved.report.sources.map(source => source.kind));
    assert.ok(kinds.has('web_search') && kinds.has('news') && kinds.has('user'),
      `Expected web, AkShare, and user source types; got ${[...kinds].join(', ')}`);
    const retrieval = await openRetrievalDetails();
    assert.equal(await retrieval.locator('.explore-searches li').count(), searches.length);
    assert.match(await retrieval.innerText(), /检索完成/);
    assert.ok((await retrieval.innerText()).includes(completed.query),
      'The actual submitted web query must be visible in the report.');
    await testId('explore-open-sources').click();
    const sourceList = testId('explore-source-list');
    await sourceList.waitFor({ state: 'visible' });
    const sourceLabels = await sourceList.innerText();
    assert.match(sourceLabels, /发布时间：/);
    assert.doesNotMatch(sourceLabels, /类型：|检索时间：/);
    assert.ok(await sourceList.locator('.explore-source-preview h3 a[href^="https://example.test/web/"]').count() >= 1,
      'A synthetic web-search result title must link to its original source.');
    await testId('explore-materials-close').click();
    await retrieval.scrollIntoViewIfNeeded();
    await screenshot('03c-web-search-trace');
    return { searchAttempts: searches.length, webResults: completed.count, sourceKinds: [...kinds] };
  });

  const firstRoute = route();
  let firstReportFile;
  let firstMaterialFile;
  await step('retrieved materials and the report enter the shared session file pool', async () => {
    const saved = await storedRun(first.id);
    const files = await sessionFiles(firstRoute.sessionId);
    firstReportFile = files.find(item => item.id === saved.report.file_id);
    firstMaterialFile = files.find(item => item.id === saved.materials.find(source => source.kind === 'web_search')?.file_id);
    assert.ok(firstReportFile?.kind === 'report' && /理解事件报告/.test(firstReportFile.name),
      'The completed report must receive a session-pool file with an automatic name.');
    assert.ok(firstMaterialFile?.kind === 'material' && firstMaterialFile.origin === 'web_search' && /网页摘要/.test(firstMaterialFile.name),
      'The retrieved web snippet must be labelled as an excerpt, not an article body.');
    assert.ok(files.some(item => item.origin === 'news') && files.some(item => item.origin === 'user'));
    const original = await sessionFile(firstRoute.sessionId, firstMaterialFile.id);
    assert.ok(original.content.includes(saved.materials.find(source => source.file_id === firstMaterialFile.id).text_used));
    assert.match(original.content, /摘要|摘录|实际可用/);
    await testId('explore-files-open').click();
    const dialog = testId('explore-files-dialog');
    await dialog.waitFor({ state: 'visible' });
    assert.equal(await dialog.getAttribute('role'), 'dialog');
    const preview = testId('explore-auto-preview').locator('details');
    await preview.waitFor({ state: 'visible' });
    assert.equal(await preview.getAttribute('open'), null, 'The file preview must be collapsed by default.');
    await preview.locator('summary').click();
    assert.notEqual(await preview.getAttribute('open'), null, 'The file preview must expand on demand.');
    assert.ok(await preview.locator('li').count() >= 1);
    assert.ok((await preview.innerText()).includes(firstReportFile.name));
    await preview.locator('summary').click();
    assert.equal(await preview.getAttribute('open'), null, 'The screenshot must capture the compact preview.');
    assert.equal(await fileRow(firstMaterialFile).count(), 1);
    await fileRow(firstMaterialFile).getByTestId('explore-file-view').click();
    const detail = testId('explore-file-detail');
    await detail.getByText(firstMaterialFile.name, { exact: true }).waitFor({ state: 'visible' });
    assert.ok((await detail.innerText()).includes(original.content));
    assert.ok(await detail.locator(`a[href="${firstMaterialFile.url}"]`).count() >= 1);
    const saving = page.waitForEvent('download');
    await testId('explore-file-download').click();
    const download = await saving;
    const target = path.join(output, 'first-web-excerpt.md');
    await download.saveAs(target);
    assert.equal(await readFile(target, 'utf8'), original.content);
    report.downloads.push({ path: target, bytes: Buffer.byteLength(original.content),
      sha256: createHash('sha256').update(original.content).digest('hex') });
    await screenshot('03d-session-file-pool');
    await testId('explore-files-close').click();
    await dialog.waitFor({ state: 'hidden' });
    return { sessionId: firstRoute.sessionId, fileCount: files.length,
      reportFileId: firstReportFile.id, materialFileId: firstMaterialFile.id };
  });
  await step('repeating the identical network submission is idempotent', async () => {
    const before = await fixtureStats();
    const filesBefore = await sessionFiles(firstRoute.sessionId);
    const response = await context.request.post(firstRequest.url, { data: firstRequest.body });
    assert.ok([200, 201, 202].includes(response.status()), `Duplicate create returned ${response.status()}`);
    const repeated = await response.json();
    assert.equal((repeated.run || repeated).id, first.id);
    assert.equal((await fixtureStats()).modelCalls, before.modelCalls);
    assert.equal((await fixtureStats()).webSearchCalls, before.webSearchCalls);
    assert.equal((await apiGet(`/desktop/api/explore/sessions/${firstRoute.sessionId}`)).runs.length, 1);
    assert.deepEqual((await sessionFiles(firstRoute.sessionId)).map(file => file.id), filesBefore.map(file => file.id),
      'A duplicate submission must not duplicate pooled materials or reports.');
  });
  await step('refresh and direct report URL restore persisted data without rerunning', async () => {
    const before = await fixtureStats();
    await page.reload();
    await testId('explore-report').waitFor({ state: 'visible' });
    assert.equal(route().runId, first.id);
    assert.equal((await fixtureStats()).modelCalls, before.modelCalls);
    assert.equal((await fixtureStats()).webSearchCalls, before.webSearchCalls);
    assert.match(await (await openRetrievalDetails()).innerText(), /检索完成/);
    await page.goto(new URL(`#explore?session=${firstRoute.sessionId}&run=${first.id}`, base).href);
    await testId('explore-report').waitFor({ state: 'visible' });
    assert.equal((await fixtureStats()).modelCalls, before.modelCalls);
    assert.equal((await fixtureStats()).webSearchCalls, before.webSearchCalls);
    assert.match(await (await openRetrievalDetails()).innerText(), /检索完成/);
  });

  await step('Markdown download contains the selected saved report', async () => {
    const saving = page.waitForEvent('download');
    await testId('explore-download').click();
    const downloaded = await saving;
    assert.match(downloaded.suggestedFilename(), /Explore.*\.md$/);
    const target = path.join(output, 'first-report.md');
    await downloaded.saveAs(target);
    const content = await readFile(target, 'utf8');
    assert.match(content, /测试甲集团|合成测试报告/);
    assert.match(content, /来源/);
    assert.match(content, /网页搜索|联网搜索/);
    report.downloads.push({ path: target, bytes: Buffer.byteLength(content),
      sha256: createHash('sha256').update(content).digest('hex') });
  });

  let second;
  let third;
  await step('1→2→3 creates related but immutable reports', async () => {
    const firstReport = await reportFor(first.id);
    await page.getByRole('button', { name: '基于这份报告继续', exact: true }).click();
    assert.match(await testId('explore-source-context').innerText(), /基于报告/);
    const relation = await submit('relation', '测试甲集团与测试乙公司之间有哪些关联和条件？');
    second = relation.run;
    await awaitReport(second.id, relation.before.reportModelCalls);
    assert.equal((await storedRun(second.id)).source_report_id, firstReport.id);
    assert.match(await testId('explore-report').innerText(), /重要关联|影响机制|成立条件/);
    const relationReport = await reportFor(second.id);
    const history = await submit('history', '验证判断：测试甲集团收购测试乙公司是否推动行业集中度提升？');
    third = history.run;
    await awaitReport(third.id, history.before.reportModelCalls);
    assert.equal((await storedRun(third.id)).source_report_id, relationReport.id);
    assert.deepEqual((await storedRun(third.id)).context_snapshot.map(item => item.report_id),
      [relationReport.id, firstReport.id]);
    assert.match(await testId('explore-report').innerText(), /定性对照，未进行行情验证/);
    assert.match(await testId('explore-report').innerText(), /2022 年测试丙集团并购测试丁公司/);
    const searches = (await reportFor(third.id)).retrieval?.searches;
    assert.ok(Array.isArray(searches) && searches.some(item => item.freshness === 'noLimit'),
      'Historical verification must record a bounded no-limit web search.');
    assert.equal(await (await openRetrievalDetails()).locator('.explore-searches li').count(), searches.length);
    assert.ok((await reportFor(first.id)).id === firstReport.id, 'Original report must remain readable.');
    await screenshot('04-history-report');
    await page.locator('.explore-case').first().scrollIntoViewIfNeeded();
    await screenshot('04b-history-case');
    await page.locator('.explore-scroll').evaluate(element => { element.scrollTop = 0; });
    return { eventRun: first.id, relationRun: second.id, historyRun: third.id };
  });

  await step('all three modules share one pool while automatic context follows the chosen report chain', async () => {
    const files = await sessionFiles(firstRoute.sessionId);
    const names = files.filter(file => file.kind === 'report').map(file => file.name);
    for (const prefix of ['理解事件报告', '发现关联报告', '历史验证报告'])
      assert.ok(names.some(name => name.startsWith(prefix)), `Missing auto-named ${prefix} in the shared pool.`);
    const relation = await storedRun(second.id);
    const history = await storedRun(third.id);
    assert.equal(relation.context_selection.mode, 'auto');
    assert.ok(relation.context_files.some(item => item.file_id === firstReportFile.id));
    assert.equal(history.context_selection.mode, 'auto');
    assert.ok(history.context_files.some(item => item.file_id === firstReportFile.id));
    assert.ok(history.context_files.some(item => item.file_id === relation.report.file_id));
    assert.equal(history.context_files.length, history.context_selection.file_ids.length);
    assert.match(await (await openUsedContextDetails()).innerText(), /自动沿当前报告分支|本次实际使用的文件/);
    await screenshot('04c-three-module-file-context');
    return { poolSize: files.length, relationContextFiles: relation.context_files.length,
      historyContextFiles: history.context_files.length };
  });

  let branchB;
  await step('branch from the first report does not inherit sibling runs', async () => {
    const firstReport = await reportFor(first.id);
    const selectedId = await selectRunInHistory('测试甲集团宣布收购测试乙公司；请梳理事件背景、时间线和影响。');
    assert.equal(selectedId, first.id);
    await page.getByRole('button', { name: '基于这份报告继续', exact: true }).click();
    const branch = await submit('event', '从第一份报告重新梳理审批节点，不使用其他分支。');
    branchB = branch.run;
    await awaitReport(branch.run.id, branch.before.reportModelCalls);
    assert.equal((await storedRun(branch.run.id)).source_report_id, firstReport.id);
    assert.deepEqual((await storedRun(branch.run.id)).context_snapshot.map(item => item.report_id), [firstReport.id]);
    assert.ok((await storedRun(branch.run.id)).context_files.some(item => item.file_id === firstReportFile.id));
    const relationFileId = (await reportFor(second.id)).file_id;
    assert.ok(!(await storedRun(branch.run.id)).context_files.some(item => item.file_id === relationFileId),
      'Automatic context must not pull a sibling report from the same session pool.');
    const session = await apiGet(`/desktop/api/explore/sessions/${firstRoute.sessionId}`);
    const runs = session.runs || [];
    assert.equal(runs.length, 4);
    assert.equal((await reportFor(third.id)).id, (await storedRun(third.id)).report.id);
    const item = testId('explore-run-list').locator('button.explore-run-item').filter({ hasText: '从第一份报告重新梳理审批节点' });
    assert.match(await item.getAttribute('style') || '', /--branch-depth:\s*1/);
    await openHistory();
    await screenshot('05-branch-history');
    await page.keyboard.press('Escape');
    return { branchRun: branch.run.id };
  });

  await step('late A→A2 branch is displayed under A before root→B', async () => {
    const relationReport = await reportFor(second.id);
    assert.equal(await selectRunInHistory('测试甲集团与测试乙公司之间有哪些关联和条件？'), second.id);
    await page.getByRole('button', { name: '基于这份报告继续', exact: true }).click();
    const branchA2 = await submit('event', '从关系报告再探讨监管审批路径 A2。');
    await awaitReport(branchA2.run.id, branchA2.before.reportModelCalls);
    assert.equal((await storedRun(branchA2.run.id)).source_report_id, relationReport.id);
    assert.deepEqual((await storedRun(branchA2.run.id)).context_snapshot.map(item => item.report_id),
      [relationReport.id, (await reportFor(first.id)).id]);
    await openHistory();
    const rows = await testId('explore-run-list').locator('button.explore-run-item').allInnerTexts();
    const names = [
      '测试甲集团宣布收购测试乙公司；请梳理事件背景、时间线和影响。',
      '测试甲集团与测试乙公司之间有哪些关联和条件？',
      '验证判断：测试甲集团收购测试乙公司是否推动行业集中度提升？',
      '从关系报告再探讨监管审批路径 A2。',
      '从第一份报告重新梳理审批节点，不使用其他分支。',
    ];
    assert.equal(rows.length, names.length);
    for (let index = 0; index < names.length; index += 1) {
      assert.ok(rows[index].includes(names[index]), `Branch order ${index} was ${rows[index]}`);
    }
    assert.equal((await storedRun(branchB.id)).source_report_id, (await reportFor(first.id)).id);
    await screenshot('05a-late-branch-depth-first');
    await page.keyboard.press('Escape');
    return { branchA2Run: branchA2.run.id, depthFirst: true };
  });

  await step('a recommendation starts exactly one related run in its target module and preserves the draft', async () => {
    const sourceReport = await reportFor(first.id);
    assert.equal(await selectRunInHistory(first.question), first.id);
    await testId('explore-input').fill('我尚未提交的独立草稿');
    const before = await fixtureStats();
    const sent = page.waitForResponse(isRunCreate);
    await page.getByRole('button', { name: /追踪并购关系路径/ }).click();
    const response = await sent;
    assert.ok([200, 201, 202].includes(response.status()));
    const body = response.request().postDataJSON();
    assert.equal(body.module, 'relation');
    assert.equal(body.question, '追踪并购关系路径');
    assert.equal(body.source_report_id, sourceReport.id);
    assert.equal(body.context_mode, 'auto');
    const created = (await response.json()).run;
    assert.ok(created?.id);
    await page.waitForFunction(expected => new URLSearchParams(location.hash.split('?')[1] || '').get('run') === expected,
      created.id, { timeout: 10000 });
    const saved = await awaitReport(created.id, before.reportModelCalls);
    assert.equal(saved.source_report_id, sourceReport.id);
    assert.ok(saved.context_files.some(item => item.file_id === firstReportFile.id));
    assert.equal(await testId('explore-input').inputValue(), '我尚未提交的独立草稿');
    assert.equal((await fixtureStats()).reportModelCalls, before.reportModelCalls + 1);
    await screenshot('05b-recommendation-executed');
    assert.equal(await selectRunInHistory(first.question), first.id);
    await testId('explore-report').waitFor({ state: 'visible' });
    assert.match(await testId('explore-history-drawer').getAttribute('class') || '', /\bopen\b/,
      'Selecting a report must keep desktop history visible.');
    return { runId: created.id, module: body.module, sourceReportId: body.source_report_id };
  });

  let manualRun;
  let manualSnapshot;
  await step('manual file selection persists across modules and excludes every unselected pool file', async () => {
    await selectRunInHistory('测试甲集团宣布收购测试乙公司；请梳理事件背景、时间线和影响。');
    await page.getByRole('button', { name: '基于这份报告继续', exact: true }).click();
    await testId('explore-module-relation').click();
    await testId('explore-files-open').click();
    const dialog = testId('explore-files-dialog');
    await dialog.waitFor({ state: 'visible' });
    await testId('explore-context-manual').click();
    assert.ok(await dialog.getByTestId('explore-file-select').count() >= 2);
    const chosen = fileRow(firstReportFile).getByTestId('explore-file-select');
    await chosen.check();
    assert.equal(await fileRow(firstMaterialFile).getByTestId('explore-file-select').isChecked(), false);
    await testId('explore-files-save-context').click();
    await page.waitForFunction(() => !document.querySelector('[data-testid="explore-files-save-context"]'), null,
      { timeout: 12000 });
    await testId('explore-files-done').click();
    await dialog.waitFor({ state: 'hidden' });
    const storedPreference = await apiGet(`/desktop/api/explore/sessions/${firstRoute.sessionId}/context`);
    assert.equal(storedPreference.contexts.relation.mode, 'manual');
    assert.deepEqual(storedPreference.contexts.relation.file_ids, [firstReportFile.id]);
    await testId('explore-module-event').click();
    await testId('explore-files-open').click();
    assert.equal(await testId('explore-context-auto').getAttribute('aria-pressed'), 'true');
    await testId('explore-files-done').click();
    await testId('explore-module-relation').click();
    await testId('explore-files-open').click();
    assert.equal(await testId('explore-context-manual').getAttribute('aria-pressed'), 'true');
    await testId('explore-files-done').click();
    const created = await submit('relation', '只依据我所选的一份报告，继续分析测试甲集团与测试乙公司的关系。');
    assert.equal(created.requestBody.context_mode, 'manual');
    assert.deepEqual(created.requestBody.file_ids, [firstReportFile.id]);
    manualRun = created.run;
    manualSnapshot = await awaitReport(manualRun.id, created.before.reportModelCalls);
    assert.deepEqual((await fixtureStats()).lastReportContextFileIds, [firstReportFile.id],
      'The synthetic report provider must receive only the checked file, not unselected pool files.');
    assert.equal(manualSnapshot.context_selection.mode, 'manual');
    assert.deepEqual(manualSnapshot.context_selection.file_ids, [firstReportFile.id]);
    assert.deepEqual(manualSnapshot.context_files.map(item => item.file_id), [firstReportFile.id]);
    assert.deepEqual(manualSnapshot.context_snapshot, [], 'Manual mode must not add report ancestors implicitly.');
    assert.ok(!manualSnapshot.context_files.some(item => item.file_id === firstMaterialFile.id));
    assert.match(await (await openUsedContextDetails()).innerText(), /自选文件/);
    await page.reload();
    await testId('explore-report').waitFor({ state: 'visible' });
    await testId('explore-module-relation').click();
    await testId('explore-files-open').click();
    assert.equal(await testId('explore-context-manual').getAttribute('aria-pressed'), 'true',
      'The module preference must survive a browser refresh.');
    await testId('explore-files-done').click();
    await screenshot('05c-manual-file-context');
    return { selectedFileId: firstReportFile.id, excludedFileId: firstMaterialFile.id,
      runId: manualRun.id };
  });

  await step('renaming or removing a pooled file leaves completed run snapshots unchanged', async () => {
    await testId('explore-files-open').click();
    const dialog = testId('explore-files-dialog');
    await dialog.waitFor({ state: 'visible' });
    await fileRow(firstReportFile).getByTestId('explore-file-view').click();
    await testId('explore-file-detail').getByText(firstReportFile.name, { exact: true }).waitFor({ state: 'visible' });
    const renamed = '人工命名-甲集团首份研究报告.md';
    await dialog.locator('#explore-file-name').fill(renamed);
    await testId('explore-file-rename').click();
    await testId('explore-file-detail').getByText(renamed, { exact: true }).waitFor({ state: 'visible' });
    assert.equal((await sessionFile(firstRoute.sessionId, firstReportFile.id)).name, renamed);
    assert.deepEqual((await storedRun(manualRun.id)).context_files, manualSnapshot.context_files,
      'A renamed file must not mutate the run-time context snapshot.');
    await dialog.getByRole('button', { name: '移除', exact: true }).click();
    await testId('explore-file-remove').click();
    await fileRow(firstReportFile).waitFor({ state: 'detached', timeout: 12000 });
    assert.ok(!(await sessionFiles(firstRoute.sessionId)).some(item => item.id === firstReportFile.id));
    await apiGet(`/desktop/api/explore/sessions/${firstRoute.sessionId}/files/${firstReportFile.id}`, 404);
    assert.deepEqual((await storedRun(manualRun.id)).context_files, manualSnapshot.context_files,
      'Removing a file must not rewrite the saved model-context snapshot.');
    assert.ok((await reportFor(first.id)).id, 'The original report must remain readable after pool removal.');
    await testId('explore-files-done').click();
    await dialog.waitFor({ state: 'hidden' });
    return { removedFileId: firstReportFile.id, preservedRunId: manualRun.id };
  });

  await step('111 and 222 paths repeat modules without enforced ordering', async () => {
    await clickNewExplore();
    const one = await submit('event', '测试甲集团公告，理解事件一。');
    await awaitReport(one.run.id, one.before.reportModelCalls);
    const two = await submit('event', '继续理解事件二。');
    await awaitReport(two.run.id, two.before.reportModelCalls);
    const three = await submit('event', '继续理解事件三。');
    await awaitReport(three.run.id, three.before.reportModelCalls);
    const eventSession = route().sessionId;
    assert.equal((await apiGet(`/desktop/api/explore/sessions/${eventSession}`)).runs.length, 3);
    assert.ok(!(await sessionFiles(eventSession)).some(file => file.id === firstMaterialFile.id),
      'A different Explore session must not list files from the first session.');
    await apiGet(`/desktop/api/explore/sessions/${eventSession}/files/${firstMaterialFile.id}`, 404);
    const foreignSelection = await context.request.put(new URL(`/desktop/api/explore/sessions/${eventSession}/context`, base).href,
      { data: { module: 'event', mode: 'manual', file_ids: [firstMaterialFile.id] } });
    assert.equal(foreignSelection.status(), 404,
      'A pool file from another session must not be selectable as model context.');
    await clickNewExplore();
    const relOne = await submit('relation', '测试甲集团与乙公司的关系一。');
    await awaitReport(relOne.run.id, relOne.before.reportModelCalls);
    const relTwo = await submit('relation', '测试甲集团与乙公司的关系二。');
    await awaitReport(relTwo.run.id, relTwo.before.reportModelCalls);
    const relThree = await submit('relation', '测试甲集团与乙公司的关系三。');
    await awaitReport(relThree.run.id, relThree.before.reportModelCalls);
    const relationSession = route().sessionId;
    assert.equal((await apiGet(`/desktop/api/explore/sessions/${relationSession}`)).runs.length, 3);
    return { eventSession, relationSession };
  });

  await step('each of three clarification choices executes directly in its module', async () => {
    const results = [];
    for (const [index, module] of ['event', 'relation', 'history'].entries()) {
      await clickNewExplore();
      const created = await submit(module, '最近发生了什么新闻？ [AMBIGUOUS_EXPLORE]');
      const vague = await awaitClarification(created.run.id, created.before.reportModelCalls);
      assert.equal(vague.review.options.length, 3, 'The fixture must offer three executable directions.');
      assert.equal(await testId('explore-review-option').count(), 3);
      const beforeOption = await fixtureStats();
      const unexpectedConfirm = [];
      const onPost = request => {
        if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith(`/explore/runs/${created.run.id}/confirm`))
          unexpectedConfirm.push(request.url());
      };
      page.on('request', onPost);
      const sent = page.waitForResponse(response =>
        new URL(response.url()).pathname.endsWith(`/explore/runs/${created.run.id}/clarify`)
          && response.request().method() === 'POST');
      await testId('explore-review-option').nth(index).click();
      const response = await sent;
      assert.ok([200, 201, 202].includes(response.status()));
      const body = response.request().postDataJSON();
      assert.equal(body.revision, vague.review.revision);
      assert.equal(body.option_id, vague.review.options[index].id);
      assert.equal(body.question, undefined, 'A clicked option must submit its ID directly.');
      const saved = await awaitReport(created.run.id, beforeOption.reportModelCalls);
      page.off('request', onPost);
      assert.deepEqual(unexpectedConfirm, [], 'A selected direction must not require second confirmation.');
      assert.ok(!saved.stage_history.some(item => item.stage === 'awaiting_review'));
      assert.equal(saved.clarification_history.at(-1).option_id, body.option_id);
      assert.ok(saved.report?.resolved_question, 'Selected direction must be saved on the report.');
      if (index === 0) {
        const beforeReplay = await fixtureStats();
        const replay = await context.request.post(response.url(), { data: body });
        assert.ok([200, 201, 202].includes(replay.status()));
        const afterReplay = await fixtureStats();
        for (const key of ['webSearchCalls', 'clarifyModelCalls', 'reportModelCalls'])
          assert.equal(afterReplay[key], beforeReplay[key], `Duplicate choice repeated ${key}.`);
        const stale = await context.request.post(response.url(), { data: { ...body,
          idempotency_key: `stale-${Date.now()}`, revision: vague.review.revision } });
        assert.equal(stale.status(), 409, 'A stale review revision must not start another report.');
      }
      results.push({ module, chosenOption: body.option_id, runId: created.run.id });
    }
    await screenshot('05b-clarification-choice-report');
    return { choices: results };
  });

  await step('custom clarification re-searches and each clear rewrite reaches a report', async () => {
    const clarifiedQuestions = {
      event: '测试甲集团拟收购测试乙公司，发生了什么？',
      relation: '测试甲集团与测试乙公司的拟收购关系如何？',
      history: '请历史验证：测试甲集团收购测试乙公司将推动行业集中度提升',
    };
    const results = [];
    for (const module of ['event', 'relation', 'history']) {
      await clickNewExplore();
      const created = await submit(module, '最近发生了什么新闻？ [AMBIGUOUS_EXPLORE]');
      const vague = await awaitClarification(created.run.id, created.before.reportModelCalls);
      const beforeCustom = await fixtureStats();
      await testId('explore-clarification-input').fill(clarifiedQuestions[module]);
      const clarifying = page.waitForResponse(response =>
        new URL(response.url()).pathname.endsWith(`/explore/runs/${created.run.id}/clarify`)
          && response.request().method() === 'POST');
      await testId('explore-clarify').click();
      const clarification = await clarifying;
      assert.ok([200, 201, 202].includes(clarification.status()));
      assert.equal(clarification.request().postDataJSON().revision, vague.review.revision);
      const saved = await awaitReport(created.run.id, beforeCustom.reportModelCalls);
      assert.ok(!saved.stage_history.some(item => item.stage === 'awaiting_review'),
        'A clear custom question must not wait for second confirmation.');
      const clarifiedInput = saved.materials.find(item => item.id === 'U1');
      assert.ok(clarifiedInput?.text_used.includes(clarifiedQuestions[module]));
      assert.match(clarifiedInput.text_used, /^本轮最新研究请求/);
      assert.ok(clarifiedInput.text_used.indexOf(clarifiedQuestions[module])
        < clarifiedInput.text_used.indexOf('[AMBIGUOUS_EXPLORE]'),
        'The confirmed question must precede the labelled original background text.');
      assert.match(clarifiedInput.text_used, /仅作背景/);
      assert.ok(saved.question.includes('[AMBIGUOUS_EXPLORE]'),
        'The original question must remain in the audit record.');
      if (module === 'event') {
        const beforeReplay = await fixtureStats();
        const replay = await context.request.post(clarification.url(),
          { data: clarification.request().postDataJSON() });
        assert.ok([200, 201, 202].includes(replay.status()));
        assert.equal((await fixtureStats()).webSearchCalls, beforeReplay.webSearchCalls);
        assert.equal((await fixtureStats()).clarifyModelCalls, beforeReplay.clarifyModelCalls);
      }
      assert.ok((await fixtureStats()).webSearchCalls > beforeCustom.webSearchCalls,
        'Clarification must trigger a fresh search rather than reusing vague results.');
      assert.equal(saved.report?.resolved_question, clarifiedQuestions[module]);
      assert.ok(saved.report.sources.find(item => item.id === 'U1')?.text_used.includes(clarifiedQuestions[module]));
      results.push({ module, runId: created.run.id, firstRevision: vague.review.revision,
        clarificationCount: saved.clarification_history.length });
    }
    await screenshot('05c-custom-clarification-report');
    return { clarified: results };
  });

  await step('history-only event retrieves materials and asks for a judgment before reporting', async () => {
    await clickNewExplore();
    const before = await fixtureStats();
    const pending = await submit('history', '测试甲集团收购测试乙公司');
    await awaitClarification(pending.run.id, before.reportModelCalls);
    assert.equal((await fixtureStats()).reportModelCalls, before.reportModelCalls);
    await testId('explore-clarification-input').fill('测试甲集团收购测试乙公司是否会提高行业集中度？');
    await testId('explore-clarify').click();
    await awaitReport(pending.run.id, before.reportModelCalls);
    assert.match((await storedRun(pending.run.id)).judgment || '', /提高行业集中度/);
  });

  await step('cancelling a waiting clarification clears evidence and never invokes the report model', async () => {
    await clickNewExplore();
    const created = await submit('event', '最近发生了什么新闻？ [AMBIGUOUS_EXPLORE]');
    await awaitClarification(created.run.id, created.before.reportModelCalls);
    const beforeCancel = await fixtureStats();
    await testId('explore-cancel').click();
    await testId('explore-retry').waitFor({ state: 'visible' });
    const saved = await storedRun(created.run.id);
    assert.equal(saved.status, 'cancelled');
    assert.equal(saved.report, null);
    assert.deepEqual(saved.materials, []);
    assert.equal(saved.review, null);
    await page.reload();
    await testId('explore-retry').waitFor({ state: 'visible' });
    const afterReload = await fixtureStats();
    assert.equal(afterReload.reportModelCalls, beforeCancel.reportModelCalls);
    assert.equal(afterReload.webSearchCalls, beforeCancel.webSearchCalls);
    return { runId: created.run.id };
  });

  await step('failure retry creates a new run and regeneration preserves the old report', async () => {
    await clickNewExplore();
    const before = await fixtureStats();
    const failed = await submit('event', `测试甲集团公告 [FAIL_ONCE_EXPLORE] ${Date.now()}`);
    await testId('explore-retry').waitFor({ state: 'visible', timeout: 30000 });
    assert.equal((await storedRun(failed.run.id)).status, 'failed');
    const retryClicked = page.waitForResponse(response => new URL(response.url()).pathname.endsWith(`/explore/runs/${failed.run.id}/retry`) && response.request().method() === 'POST');
    await testId('explore-retry').click();
    const retryResponse = await retryClicked;
    assert.ok([200, 201, 202].includes(retryResponse.status()));
    const retried = (await retryResponse.json()).run;
    assert.ok(retried.id && retried.id !== failed.run.id);
    await awaitReport(retried.id, before.reportModelCalls + 1);
    assert.equal((await storedRun(failed.run.id)).status, 'failed');
    const reportId = (await reportFor(retried.id)).id;
    const regenerateClicked = page.waitForResponse(isRunCreate);
    await testId('explore-regenerate').click();
    const regenerateResponse = await regenerateClicked;
    const regenerated = (await regenerateResponse.json()).run;
    assert.notEqual(regenerated.id, retried.id);
    await awaitReport(regenerated.id);
    assert.equal((await reportFor(retried.id)).id, reportId);
    return { failedRun: failed.run.id, retriedRun: retried.id, regeneratedRun: regenerated.id };
  });

  await step('cancelled model call remains cancelled after its late return', async () => {
    await clickNewExplore();
    const before = await fixtureStats();
    const waiting = await submit('event', '测试甲集团公告 [WAIT_EXPLORE]');
    await waitStats(stats => stats.reportModelCalls > before.reportModelCalls);
    await testId('explore-cancel').click();
    await testId('explore-retry').waitFor({ state: 'visible', timeout: 15000 });
    assert.equal((await storedRun(waiting.run.id)).status, 'cancelled');
    await waitStats(stats => stats.cancellationObserved > before.cancellationObserved, 5000);
    assert.equal((await storedRun(waiting.run.id)).status, 'cancelled');
    assert.equal((await storedRun(waiting.run.id)).report, null);
    await screenshot('06-cancelled-run');
  });

  await step('model HTML and unsafe link are inert in the displayed report', async () => {
    await clickNewExplore();
    const unsafe = await submit('event', '测试甲集团拟收购测试乙公司，分析交易影响。[UNSAFE_HTML] <img src=x onerror="window.__exploreXss=1">');
    await awaitReport(unsafe.run.id, unsafe.before.reportModelCalls);
    await testId('explore-open-materials').click();
    const materials = testId('explore-materials');
    await materials.waitFor({ state: 'visible' });
    assert.match(await materials.innerText(), /<img src=x onerror=/);
    assert.equal(await materials.locator('img[onerror]').count(), 0);
    assert.equal(await page.evaluate(() => window.__exploreXss), undefined);
    await page.keyboard.press('Escape');
    const reportDom = testId('explore-report');
    assert.match(await reportDom.innerText(), /<img|恶意链接/);
    assert.equal(await reportDom.locator('img[onerror]').count(), 0);
    assert.equal(await reportDom.locator('a[href^="javascript:"]').count(), 0);
    assert.equal(await page.evaluate(() => window.__exploreXss), undefined);
    await screenshot('07-safe-markdown');
  });

  await step('rename and delete session update navigation and remove saved reports', async () => {
    const doomedSession = route().sessionId;
    const doomedRun = route().runId;
    const doomedReport = await reportFor(doomedRun);
    await openHistory();
    const sessionRow = testId('explore-session-list').locator(`li[data-session-id="${doomedSession}"]`);
    await sessionRow.getByTestId('explore-session-more').click();
    await testId('explore-session-rename').click();
    const renameDialog = testId('explore-rename-dialog');
    await renameDialog.waitFor({ state: 'visible' });
    await testId('explore-rename-input').fill('浏览器验收新标题');
    await testId('explore-rename-save').click();
    await renameDialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.querySelector('.explore-topbar-title h1 button')?.textContent.includes('浏览器验收新标题'));
    assert.equal((await apiGet(`/desktop/api/explore/sessions/${doomedSession}`)).session.title, '浏览器验收新标题');
    await openHistory();
    await sessionRow.getByTestId('explore-session-more').click();
    await testId('explore-session-delete').click();
    const deleteDialog = testId('explore-delete-dialog');
    await deleteDialog.waitFor({ state: 'visible' });
    await testId('explore-delete-confirm').click();
    await deleteDialog.waitFor({ state: 'hidden' });
    await page.waitForFunction(() => location.hash === '#explore', null, { timeout: 12000 });
    assert.equal(route().sessionId, '');
    await apiGet(`/desktop/api/explore/sessions/${doomedSession}`, 404);
    await apiGet(`/desktop/api/explore/runs/${doomedRun}`, 404);
    await apiGet(`/desktop/api/explore/reports/${doomedReport.id}`, 404);
    await apiGet(`/desktop/api/explore/reports/${doomedReport.id}/markdown`, 404);
  });

  await step('small-screen research history is an operable drawer', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await testId('explore-history-drawer').waitFor({ state: 'visible' });
    await page.waitForFunction(() => document.querySelector('[data-testid="explore-history-drawer"]')?.getAttribute('role') === 'dialog',
      null, { timeout: 3000 });
    assert.equal(await testId('explore-history-drawer').getAttribute('role'), 'dialog');
    assert.equal(await testId('explore-history-drawer').getAttribute('aria-modal'), 'true');
    assert.match(await testId('explore-history-drawer').getAttribute('class') || '', /open/);
    await page.waitForFunction(() => {
      const bounds = document.querySelector('[data-testid="explore-history-drawer"]')?.getBoundingClientRect();
      return bounds && bounds.left >= -1 && bounds.width >= 250;
    }, null, { timeout: 3000 });
    assert.ok(await testId('explore-session-list').locator('button').count() >= 1);
    await screenshot('08-mobile-history-drawer');
    await page.keyboard.press('Escape');
    assert.doesNotMatch(await testId('explore-history-drawer').getAttribute('class') || '', /\bopen\b/);
    assert.equal(await testId('explore-history-toggle').evaluate(node => node === document.activeElement), true,
      'Closing the drawer with Escape must restore keyboard focus.');
    await testId('explore-history-toggle').click();
    await page.getByRole('button', { name: '关闭研究历史' }).last().click();
    await page.goto(new URL(`#explore?session=${firstRoute.sessionId}&run=${first.id}`, base).href);
    await testId('explore-report').waitFor({ state: 'visible' });
    await testId('explore-files-open').click();
    const filesDialog = testId('explore-files-dialog');
    await filesDialog.waitFor({ state: 'visible' });
    assert.equal(await filesDialog.getAttribute('role'), 'dialog');
    const mobilePreview = testId('explore-auto-preview').locator('details');
    await mobilePreview.waitFor({ state: 'visible' });
    assert.equal(await mobilePreview.getAttribute('open'), null);
    await mobilePreview.locator('summary').click();
    assert.notEqual(await mobilePreview.getAttribute('open'), null);
    assert.ok(await mobilePreview.locator('li').count() >= 1);
    await mobilePreview.locator('summary').click();
    assert.equal(await mobilePreview.getAttribute('open'), null);
    assert.ok(await testId('explore-file-list').locator('.explore-file-item').count() >= 1,
      'The shared pool must remain usable on a narrow screen.');
    await screenshot('08b-mobile-file-pool');
    await page.keyboard.press('Escape');
    await filesDialog.waitFor({ state: 'hidden' });
    assert.equal(await testId('explore-files-open').evaluate(node => node === document.activeElement), true);
    await page.setViewportSize({ width: 1440, height: 1000 });
  });

  await step('return, browser navigation, ordinary chat, Build and Evolve still work', async () => {
    const before = await fixtureStats();
    await page.getByRole('button', { name: '返回工作台' }).click();
    await page.getByRole('button', { name: '开始探索', exact: true }).waitFor({ state: 'visible' });
    await page.goBack();
    await testId('explore-workspace').waitFor({ state: 'visible' });
    await page.goForward();
    await page.getByRole('button', { name: '开始探索', exact: true }).waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '开始构建', exact: true }).click();
    await page.getByLabel('输入消息', { exact: true }).waitFor({ state: 'visible' });
    assert.match(await page.getByLabel('输入消息', { exact: true }).inputValue(), /可运行的应用/);
    await page.getByRole('button', { name: '查看我的应用', exact: true }).click();
    await page.getByRole('heading', { name: '应用', exact: true }).waitFor({ state: 'visible' });
    await page.locator('.agent-platform .agent-catalog').waitFor({ state: 'visible' });
    await page.getByRole('button', { name: '新任务', exact: true }).first().click();
    await page.getByLabel('对话方式', { exact: true }).selectOption('chat');
    await page.getByLabel('输入消息', { exact: true }).fill('普通聊天：什么是复利？');
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await page.locator('.message.assistant .message-content').filter({ hasText: '这是浏览器验收的普通聊天回复' }).waitFor({ state: 'visible' });
    assert.equal((await fixtureStats()).modelCalls, before.modelCalls, 'Ordinary chat must not invoke Explore.');
    const after = await platformStats();
    assert.equal(after.chatCalls, platformInitial.chatCalls + 1);
  });

  await step('a second temporary account cannot read another account’s Explore records', async () => {
    const firstReport = await reportFor(first.id);
    const otherContext = await browser.newContext({ viewport: { width: 1200, height: 900 } });
    try {
      const other = await otherContext.newPage();
      await other.goto(base.href);
      await other.getByRole('button', { name: '创建账号', exact: true }).click();
      const form = other.locator('#auth-form');
      await form.locator('input[name="name"]').fill('隔离验收账号');
      await form.locator('input[name="email"]').fill(`explore-other-${Date.now()}@example.test`);
      await form.locator('input[name="password"]').fill('fixture-other-password');
      await form.getByRole('button', { name: '创建账号', exact: true }).click();
      await other.getByRole('button', { name: '开始探索', exact: true }).waitFor({ state: 'visible' });
      for (const pathname of [
        `/desktop/api/explore/sessions/${firstRoute.sessionId}`,
        `/desktop/api/explore/runs/${first.id}`,
        `/desktop/api/explore/reports/${firstReport.id}`,
        `/desktop/api/explore/reports/${firstReport.id}/sources`,
        `/desktop/api/explore/reports/${firstReport.id}/markdown`,
        `/desktop/api/explore/sessions/${firstRoute.sessionId}/files`,
        `/desktop/api/explore/sessions/${firstRoute.sessionId}/files/${firstMaterialFile.id}`,
        `/desktop/api/explore/sessions/${firstRoute.sessionId}/files/${firstMaterialFile.id}/download`,
        `/desktop/api/explore/sessions/${firstRoute.sessionId}/context?module=event`,
      ]) {
        const response = await otherContext.request.get(new URL(pathname, base).href);
        assert.equal(response.status(), 404, `${pathname} leaked across accounts`);
      }
      for (const action of ['confirm', 'clarify']) {
        const pathname = `/desktop/api/explore/runs/${first.id}/${action}`;
        const response = await otherContext.request.post(new URL(pathname, base).href, {
          data: action === 'confirm'
            ? { idempotency_key: `other-confirm-${Date.now()}`, revision: 1,
                source_ids: ['S1'] }
            : { idempotency_key: `other-clarify-${Date.now()}`, revision: 1,
                question: '测试甲集团收购测试乙公司发生了什么？' },
        });
        assert.equal(response.status(), 404, `${pathname} accepted an action from another account`);
      }
      const foreignContext = await otherContext.request.put(new URL(`/desktop/api/explore/sessions/${firstRoute.sessionId}/context`, base).href,
        { data: { module: 'event', mode: 'manual', file_ids: [firstMaterialFile.id] } });
      assert.equal(foreignContext.status(), 404, 'Another account must not configure this session’s file context.');
      await other.goto(new URL(`#explore?session=${firstRoute.sessionId}&run=${first.id}`, base).href);
      await other.getByTestId('explore-workspace').waitFor({ state: 'visible' });
      await other.getByRole('alert').filter({ hasText: /无权访问|不存在/ }).waitFor({ state: 'visible' });
    } finally { await otherContext.close(); }
  });

  assert.deepEqual(report.pageErrors, [], 'The UI must not emit browser JavaScript errors.');
  report.finalStats = await fixtureStats();
  report.platformFinalStats = await platformStats();
  report.passed = true;
} catch (error) {
  report.passed = false;
  report.failedStep = currentStep;
  report.error = String(error?.stack || error);
  if (page) {
    try {
      const snapshot = typeof page.locator('body').ariaSnapshot === 'function'
        ? await page.locator('body').ariaSnapshot() : await page.locator('body').innerText();
      await writeFile(path.join(output, 'failure-body.txt'), snapshot, 'utf8');
      console.error(`BODY SNAPSHOT (${currentStep}):\n${snapshot.slice(0, 15000)}`);
      await screenshot('failure');
    } catch (diagnosticError) { report.diagnosticError = String(diagnosticError); }
  }
  console.error(report.error);
  process.exitCode = 1;
} finally {
  report.completedAt = new Date().toISOString();
  await writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
  if (browser) await browser.close();
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length,
    failedStep: report.failedStep, report: path.join(output, 'report.json') }));
}
