/**
 * Explore grouped-citation browser acceptance. All study content is synthetic.
 * A normal temporary fixture report is saved first; the citation-rich report is
 * substituted only in browser GET responses and is never written to the server.
 */
import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
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
const output = path.resolve(process.env.EXPLORE_CITATIONS_OUTPUT ||
  path.join(root, 'data', 'explore-citations', `run-${Date.now()}`));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only a local fixture is allowed.');
await mkdir(output, { recursive: true });

const xssMarker = 'citation-xss-must-stay-text';
const syntheticSources = ['S1', 'S2', 'S3', 'S4'].map((id, index) => ({
  id, kind: index % 2 ? 'news' : 'web_search',
  title: `引用验收替身材料 ${id}`, source_name: '仅浏览器测试替身',
  summary: `合成摘要 ${id}；不代表实际网页、新闻或模型结论。`,
  text_used: `合成原文摘录 ${id}。仅供引用抽屉分组和焦点测试。`,
  content_kind: index % 2 ? 'news_excerpt' : 'search_snippet',
  published_at: null, retrieved_at: '2026-10-13T18:00:00+08:00',
  url: `https://example.test/explore-citation-fixture/${id.toLowerCase()}`,
}));
const citationReport = {
  title: '引用胶囊布局与交互验收 · 合成测试报告',
  summary: '本报告只用于测试来源胶囊的布局、合并、抽屉范围与安全渲染，没有真实新闻或金融结论。',
  key_findings: ['所有主体、来源与判断均为浏览器界面测试替身。'],
  uncertainties: ['未执行真实新闻搜索或模型分析。'],
  limitations: ['来源 ID 与正文均为临时测试数据；报告未写入服务端。'],
  event_overview: '仅检查多来源引用能否归在一句话末尾。',
  timeline: [
    { time: '2026-10-01', event: '这条短句末尾应紧跟一个合并的引用胶囊。', source_refs: ['S1', 'S2', 'S1'] },
    { time: '2026-10-02', event: '这条记录只有未知来源，应显示待核对。', source_refs: ['S999', 'S999'] },
    { time: '2026-10-03', event: '单条已知来源也必须能够查看。', source_refs: ['S3'] },
  ],
  sections: [
    { heading: '短句行内引用测试', body: '正文短句后面应直接出现来源胶囊。', source_refs: ['S1', 'S2', 'S2'] },
    { heading: '未知来源与恶意正文安全测试',
      body: `未知来源仍需待核对。<img id="${xssMarker}" src="x" onerror="window.__citationXss=1"><script>window.__citationXss=2</script>`,
      source_refs: ['S1', 'S999'] },
    { heading: '页面滚动稳定性材料',
      body: Array.from({ length: 12 }, (_, index) => `合成阅读段落 ${index + 1}：这里没有真实研究内容，只用于增加报告高度并检查抽屉关闭后的阅读位置。`).join('\n\n'),
      source_refs: ['S4'] },
  ],
  relations: [{ from: '合成主体甲', to: '合成主体乙', relation_type: '仅测试界面',
    direction: '未验证', mechanism: '只验证关系详情中的合并引用。', conditions: '合成条件。',
    source_refs: ['S1', 'S4', 'S4'] }],
  cases: [{ event: '合成历史案例', similarities: '用于验证来源归组。',
    differences: '没有真实历史证据。', supporting_conditions: '无。', failure_conditions: '无。',
    source_refs: ['S2', 'S3', 'S2'] }],
  sources: syntheticSources,
  recommendations: [],
};

const result = {
  startedAt: new Date().toISOString(), fixture: true, realNews: false, realModel: false,
  browserResponseSubstitute: true, savedReportModified: false,
  checks: [], screenshots: [], pageErrors: [],
};
let browser, context, page;
let current = 'setup';
function testId(id) { return page.getByTestId(id); }
async function shot(name) {
  const file = path.join(output, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false, animations: 'allow' });
  result.screenshots.push(file);
}
async function check(name, fn) {
  current = name;
  console.log(`RUN ${name}`);
  const details = await fn();
  result.checks.push({ name, passed: true, ...(details || {}) });
  console.log(`PASS ${name}`);
}
async function savedRun(runId) {
  const response = await context.request.get(new URL(`/desktop/api/explore/runs/${runId}`, base).href);
  assert.equal(response.status(), 200);
  return (await response.json()).run;
}
function routeInfo() {
  const query = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { sessionId: query.get('session') || '', runId: query.get('run') || '' };
}
async function closeDrawerByEscape(chip) {
  await page.keyboard.press('Escape');
  await testId('explore-materials').waitFor({ state: 'hidden' });
  await page.waitForTimeout(300);
  assert.equal(await chip.evaluate(node => document.activeElement === node), true,
    'Escape must restore focus to the citation chip.');
}
async function groupedDrawer(chip, expectedIds, screenshotName) {
  await chip.scrollIntoViewIfNeeded();
  await chip.click();
  const drawer = testId('explore-materials');
  await drawer.waitFor({ state: 'visible' });
  await page.waitForTimeout(320);
  const cards = drawer.locator('.explore-material-card');
  assert.equal(await cards.count(), expectedIds.length);
  const actualIds = await cards.locator('.explore-source-number').allInnerTexts();
  assert.deepEqual(actualIds.map(text => text.match(/^S\d+/)?.[0]), expectedIds);
  assert.match(await drawer.innerText(), new RegExp(`本段引用\\s*${expectedIds.length}\\s*条来源`));
  if (screenshotName) await shot(screenshotName);
  return { drawer, cards, actualIds };
}
async function inlinePosition(containerSelector) {
  return page.locator(containerSelector).first().evaluate(container => {
    const prose = container.querySelector('p');
    const chip = container.querySelector('[data-testid="explore-source-chip"]');
    if (!prose || !chip) return { error: 'missing prose or chip' };
    const nodes = [];
    const walker = document.createTreeWalker(prose, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode;
      if (!node.parentElement?.closest('[data-testid="explore-source-chip"]') && node.textContent.trim()) nodes.push(node);
    }
    const last = nodes.at(-1);
    if (!last) return { error: 'missing sentence text' };
    const end = last.textContent.length;
    const range = document.createRange();
    range.setStart(last, Math.max(0, end - 1));
    range.setEnd(last, end);
    const text = range.getBoundingClientRect();
    const button = chip.getBoundingClientRect();
    const citedBody = container.querySelector('.explore-cited-prose-body');
    const sameFlow = prose.contains(chip) || Boolean(citedBody &&
      getComputedStyle(citedBody).display === 'contents' && getComputedStyle(prose).display === 'inline');
    return { text: { left: text.left, right: text.right, top: text.top, bottom: text.bottom },
      chip: { left: button.left, right: button.right, top: button.top, bottom: button.bottom },
      sameFlow, viewportWidth: innerWidth };
  });
}

try {
  const statsResponse = await fetch(new URL('/__explore_fixture__/stats', base));
  assert.equal(statsResponse.status, 200);
  const fixture = await statsResponse.json();
  assert.equal(fixture.fixture, true);
  assert.equal(fixture.realNews, false);
  assert.equal(fixture.realModel, false);
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  page = await context.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => result.pageErrors.push(String(error)));
  await page.goto(base.href);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('Explore 引用验收替身');
  await form.locator('input[name="email"]').fill(`explore-citation-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('citation-synthetic-fixture-password');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.goto(new URL('#explore', base).href);
  await page.locator('.explore-welcome').waitFor({ state: 'visible' });
  await testId('explore-input').fill('请仅生成引用界面验收用合成报告。');
  const post = page.waitForResponse(response => response.request().method() === 'POST' &&
    /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
  await testId('explore-submit').click();
  const created = (await (await post).json()).run;
  assert.ok(created?.id);
  await testId('explore-report').waitFor({ state: 'visible', timeout: 30000 });
  const { runId, sessionId } = routeInfo();
  assert.equal(runId, created.id);
  const savedBefore = await savedRun(runId);
  assert.ok(savedBefore.report?.id);
  result.savedOriginal = { runId, sessionId, reportId: savedBefore.report.id,
    title: savedBefore.report.title, sourceCount: savedBefore.report.sources?.length || 0 };
  await page.route('**/desktop/api/explore/**', async requestRoute => {
    const request = requestRoute.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== 'GET' || pathname.endsWith('/events')) return requestRoute.continue();
    const response = await requestRoute.fetch();
    if (!String(response.headers()['content-type'] || '').includes('json')) return requestRoute.fulfill({ response });
    const body = await response.json();
    const substitute = run => {
      if (run?.id !== runId || !run.report) return;
      run.report = { ...run.report, ...citationReport, id: savedBefore.report.id };
    };
    substitute(body.run);
    if (Array.isArray(body.runs)) body.runs.forEach(substitute);
    await requestRoute.fulfill({ response, json: body });
  });
  await page.reload();
  await testId('explore-report').waitFor({ state: 'visible' });
  result.build = await page.evaluate(() => document.querySelector('script[type="module"]')?.getAttribute('src') || '');
  await page.waitForTimeout(450);

  await check('two sources collapse to one deduplicated +1 chip', async () => {
    const first = page.locator('.explore-timeline li').first();
    await first.scrollIntoViewIfNeeded();
    const chips = first.getByTestId('explore-source-chip');
    assert.equal(await chips.count(), 1);
    assert.equal((await chips.first().innerText()).trim(), '来源 +1');
    assert.equal(await chips.first().locator('svg').count(), 1);
    assert.match(await chips.first().getAttribute('aria-label'), /S1.*S2/);
    await shot('01-timeline-one-chip-two-sources-1440x900');
    return { refs: citationReport.timeline[0].source_refs, renderedChips: 1 };
  });

  await check('short sentence and chip share the final text line at desktop width', async () => {
    const timeline = await inlinePosition('.explore-timeline li:first-child');
    const section = await inlinePosition('.explore-cited-prose');
    for (const [name, geometry] of [['timeline', timeline], ['section', section]]) {
      assert.equal(geometry.sameFlow, true, `${name}: ${JSON.stringify(geometry)}`);
      const centerText = (geometry.text.top + geometry.text.bottom) / 2;
      const centerChip = (geometry.chip.top + geometry.chip.bottom) / 2;
      assert.ok(Math.abs(centerText - centerChip) <= 9, `${name}: not on same line ${JSON.stringify(geometry)}`);
      assert.ok(geometry.chip.left >= geometry.text.right - 3,
        `${name}: chip must follow last sentence character ${JSON.stringify(geometry)}`);
    }
    await page.locator('.explore-cited-prose').first().scrollIntoViewIfNeeded();
    await shot('02-section-inline-chip-1440x900');
    return { timeline, section };
  });

  await check('source drawer scopes to both references and can show all material', async () => {
    const chip = page.locator('.explore-timeline li').first().getByTestId('explore-source-chip');
    const { drawer } = await groupedDrawer(chip, ['S1', 'S2'], '03-two-source-drawer-1440x900');
    await testId('explore-all-materials').click();
    await page.waitForTimeout(200);
    const allIds = (await drawer.locator('.explore-material-card .explore-source-number').allInnerTexts())
      .map(text => text.match(/^S\d+/)?.[0]);
    for (const id of ['S1', 'S2', 'S3', 'S4']) assert.ok(allIds.includes(id), `Missing ${id}: ${allIds}`);
    assert.equal(await testId('explore-all-materials').count(), 0);
    await shot('04-all-materials-drawer-1440x900');
    await closeDrawerByEscape(chip);
    return { filtered: ['S1', 'S2'], allIds };
  });

  await check('all four report locations open their own matching grouped sources', async () => {
    const locations = [
      { name: 'section', chip: page.locator('.explore-cited-prose').first().getByTestId('explore-source-chip'), ids: ['S1', 'S2'] },
      { name: 'relation', chip: page.locator('.explore-relation-detail').getByTestId('explore-source-chip'), ids: ['S1', 'S4'] },
      { name: 'case', chip: page.locator('.explore-case').first().getByTestId('explore-source-chip'), ids: ['S2', 'S3'] },
    ];
    for (const { name, chip, ids } of locations) {
      assert.equal(await chip.count(), 1, name);
      assert.equal((await chip.innerText()).trim(), '来源 +1');
      await groupedDrawer(chip, ids, `05-${name}-grouped-drawer-1440x900`);
      await closeDrawerByEscape(chip);
    }
    return { checked: locations.map(item => item.name) };
  });

  await check('unknown IDs remain pending and never turn into invented source cards', async () => {
    const unknownOnly = page.locator('.explore-timeline li').nth(1);
    assert.equal(await unknownOnly.getByTestId('explore-source-chip').count(), 0);
    assert.equal(await unknownOnly.getByTestId('explore-source-unmapped').count(), 1);
    assert.match(await unknownOnly.getByTestId('explore-source-unmapped').getAttribute('title'), /S999/);
    const mixed = page.locator('.explore-cited-prose').nth(1);
    assert.equal(await mixed.getByTestId('explore-source-chip').count(), 1);
    assert.equal((await mixed.getByTestId('explore-source-chip').innerText()).trim(), '来源');
    assert.equal(await mixed.getByTestId('explore-source-unmapped').count(), 1);
    const chip = mixed.getByTestId('explore-source-chip');
    await groupedDrawer(chip, ['S1'], '06-known-plus-unmapped-1440x900');
    assert.equal(await testId('explore-materials').getByText('S999').count(), 0);
    await closeDrawerByEscape(chip);
    return { unknown: 'S999', mixedKnown: 'S1' };
  });

  await check('Escape restores focus, reader position and input draft', async () => {
    const draft = '合成草稿保持原样：下一题尚未提交。';
    await testId('explore-input').fill(draft);
    const chip = page.locator('.explore-cited-prose').last().getByTestId('explore-source-chip');
    await chip.scrollIntoViewIfNeeded();
    const before = await page.locator('.explore-scroll').evaluate(node => node.scrollTop);
    await groupedDrawer(chip, ['S4'], '07-reader-position-before-close-1440x900');
    await closeDrawerByEscape(chip);
    const after = await page.locator('.explore-scroll').evaluate(node => node.scrollTop);
    assert.ok(Math.abs(after - before) <= 3, `Reader shifted: ${before} -> ${after}`);
    assert.equal(await testId('explore-input').inputValue(), draft);
    return { scrollBefore: before, scrollAfter: after, draftPreserved: true };
  });

  await check('malicious body stays inert and literal', async () => {
    const section = page.locator('.explore-cited-prose').nth(1);
    const prose = await section.innerText();
    assert.ok(prose.includes(`<img id="${xssMarker}"`));
    assert.equal(await page.locator(`#${xssMarker}`).count(), 0);
    assert.equal(await page.locator('.explore-report-body script').count(), 0);
    assert.equal(await page.evaluate(() => window.__citationXss), undefined);
    await section.scrollIntoViewIfNeeded();
    await shot('08-escaped-malicious-section-1440x900');
  });

  await check('390px view keeps citation and drawer inside viewport', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    const history = testId('explore-history-drawer');
    if (await history.getAttribute('role') === 'dialog') {
      await history.locator('.explore-history-close').click();
      await history.waitFor({ state: 'hidden' });
    }
    const chip = page.locator('.explore-timeline li').first().getByTestId('explore-source-chip');
    await chip.scrollIntoViewIfNeeded();
    const pageGeometry = await page.evaluate(() => ({ width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      sendBottom: document.querySelector('[data-testid="explore-submit"]')?.getBoundingClientRect().bottom }));
    assert.ok(pageGeometry.scrollWidth <= 391, JSON.stringify(pageGeometry));
    assert.ok(pageGeometry.sendBottom <= 845, JSON.stringify(pageGeometry));
    await shot('09-mobile-timeline-chip-390x844');
    await groupedDrawer(chip, ['S1', 'S2'], '10-mobile-two-source-drawer-390x844');
    const drawerGeometry = await testId('explore-materials').evaluate(node => {
      const rect = node.getBoundingClientRect();
      return { left: rect.left, right: rect.right, width: innerWidth,
        scrollWidth: document.documentElement.scrollWidth };
    });
    assert.ok(drawerGeometry.left >= -1 && drawerGeometry.right <= 391, JSON.stringify(drawerGeometry));
    assert.ok(drawerGeometry.scrollWidth <= 391, JSON.stringify(drawerGeometry));
    await closeDrawerByEscape(chip);
    return { pageGeometry, drawerGeometry };
  });

  await check('browser-only citation substitute never changes saved report', async () => {
    const savedAfter = await savedRun(runId);
    assert.equal(savedAfter.report.id, savedBefore.report.id);
    assert.equal(savedAfter.report.title, savedBefore.report.title);
    assert.deepEqual(savedAfter.report.sources, savedBefore.report.sources);
    assert.deepEqual(savedAfter.report.sections, savedBefore.report.sections);
    assert.notEqual(savedAfter.report.title, citationReport.title);
    return { savedTitle: savedAfter.report.title, substitutedTitle: citationReport.title };
  });
  assert.deepEqual(result.pageErrors, []);
  result.passed = true;
} catch (error) {
  result.passed = false;
  result.failedAt = current;
  result.error = { message: error.message, stack: error.stack };
  try { if (page && !page.isClosed()) await shot('failure'); } catch {}
  console.error(`FAIL ${current}: ${error.stack || error}`);
} finally {
  try { await context?.close(); } catch {}
  try { await browser?.close(); } catch {}
  result.completedAt = new Date().toISOString();
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  console.log(`EXPLORE_CITATIONS_OUTPUT=${output}`);
  console.log(`EXPLORE_CITATIONS_RESULT=${result.passed ? 'PASS' : 'FAIL'} ${result.checks.length} checks`);
}
if (!result.passed) process.exitCode = 1;
