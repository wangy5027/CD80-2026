/**
 * D08-B representative report layout acceptance.
 * The study content below exists only in intercepted browser GET responses.
 * It is never POSTed or saved, and every source is a clearly labelled substitute.
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
const output = path.resolve(process.env.EXPLORE_V3_REPRESENTATIVE_OUTPUT ||
  path.join(root, 'data', 'explore-ui-v3', `representative-${Date.now()}`));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname));
await mkdir(output, { recursive: true });

const longCompany = '纯布局测试用超长虚构主体名称——成都第八十号跨区域产业链研究与资料核验联合协作中心及其多层级业务观察分部';
const longUrl = `https://example.test/layout/${'very-long-unbroken-source-path-segment-'.repeat(10)}representative-only`;
const headings = [
  '事件的最初线索与观察范围', '主体名称、关系方向和条件', '时间口径与资料更新差异',
  '证据类型及摘录的边界', '相反解释与仍待查明的问题', '阅读决策与后续核对顺序',
];
const lenses = [
  '这是一段纯排版测试文字。它检查普通中文段落在浅色阅读面上的行长、行距与标题距离，也检查页面滚动时正文是否保持稳定。文字不描述真实市场、公司、价格或投资结果，任何看似具体的主体都只是虚构占位。',
  '读者需要能够先看到核心发现，再按自身兴趣进入事件脉络、关系路径和资料来源。这里刻意放入多个完整句子与转折，观察内容长短变化时排版是否重新跳动，以及小屏能否继续查看而不遮住发送入口。',
  '资料卡片在本测试里只代表已经保存的摘录范围，并不代表公开网页全文。若检索失败，页面应说清失败和可用证据的限制；不能把缺失的来源补成一条貌似真实的新闻，也不能把检索时间写成发布时间。',
  '同一工作区中，长主体名称、链接、表格和时间线会与普通叙述并存。测试关注局部横向滚动、换行和焦点回返：打开来源后，读者应该知道当前引用对应哪一条资料，关闭抽屉后仍回到先前阅读的位置。',
];
const sectionBodies = headings.map((heading, index) => {
  const ordered = lenses.map((_, offset) => lenses[(index + offset) % lenses.length]);
  return `**纯布局测试段落 ${index + 1}：${heading}。**\n\n${ordered[0]}\n\n${ordered[1]}\n\n${ordered[2]}\n\n${ordered[3]}`;
});
const table = [
  '| 纯布局测试字段 | 第一观察口径 | 第二观察口径 | 材料形态 | 核对动作 | 失败时的说明 | 边界条件 | 阅读提示 |',
  '| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |',
  ...Array.from({ length: 5 }, (_, index) => `| 第 ${index + 1} 行占位 | 未验证的主体关系与事件条件 | 仅用于显示长表格的第二种解释 | 合成摘录类型 ${index + 1} | 返回原始来源核对时间与正文范围 | 如接口失败则显示限制，不填造数据 | 资料不足时不作结论 | 横向滚动可查看此列内容 |`),
].join('\n');
const layoutReport = {
  title: `纯布局测试报告 · ${longCompany}`,
  summary: '这是一份仅用于排版验收的浏览器响应替身。它包含多标题、时间线、宽表格、长网址、长虚构主体名称与失败来源提示；不代表任何真实新闻、证券判断、模型运行结论或实际调研成果。',
  key_findings: [
    '版面应清楚呈现证据边界：保存的搜索摘要只是摘录，不等于网页全文。',
    '多节内容需要保持标题层级、段落节奏、阅读位置和固定输入框的稳定。',
    '单个来源检索失败时应如实显示限制，不能补造来源或把失败当成功。',
  ],
  uncertainties: [
    '这里没有真实新闻、行情或第三方佐证；所有条目均为浏览器界面验收替身。',
    '来源 6 的检索被合成失败标记覆盖，不得据此推断事件是否发生。',
  ],
  limitations: [
    '纯布局材料未进入服务端保存的报告；刷新后的正常接口仍应返回原始合成 fixture 报告。',
    '一条来源检索在本次浏览器响应中被标记为失败。页面只能交付已有合成摘录与明确限制。',
    '本测试未执行原生输入法和原生浏览器缩放，也没有验证投资判断的正确性。',
  ],
  timeline: Array.from({ length: 6 }, (_, index) => ({
    time: `2026-10-${String(index + 1).padStart(2, '0')}T09:30:00+08:00`,
    event: `纯布局测试时间线节点 ${index + 1}：此处仅核验日期、长句和来源按钮的排版。资料摘录与判断需要保留区分，不能因为时间靠近就假定存在因果路径。`,
    source_refs: [index % 2 ? 'S2' : 'S1'],
  })),
  sections: [
    ...headings.map((heading, index) => ({ heading, body: sectionBodies[index], source_refs: [`S${index % 5 + 1}`] })),
    { heading: '宽表格与局部横向阅读', body: `以下表格内容全部为占位说明，不含实际研究数据。\n\n${table}`, source_refs: ['S4'] },
    { heading: '长网址与超长主体名称', body: `长主体：${longCompany}。\n\n长链接：${longUrl}。\n\n链接仅指向 example.test 的布局测试域名，不应用作实际来源。`, source_refs: ['S5'] },
  ],
  sources: [
    { id: 'S1', kind: 'web_search', title: '合成网页搜索摘要：事件起点', content_kind: 'search_snippet' },
    { id: 'S2', kind: 'news', title: '合成 AkShare 快讯摘录：时间线材料', content_kind: 'news_excerpt' },
    { id: 'S3', kind: 'user', title: '用户提供的布局测试原文', content_kind: 'user_input' },
    { id: 'S4', kind: 'public_page', title: '合成公开网页正文片段：宽表格参照', content_kind: 'extracted_page_text' },
    { id: 'S5', kind: 'tool_result', title: '合成工具摘录：长主体名称参照', content_kind: 'tool_excerpt' },
  ].map((source, index) => ({ ...source, source_name: '浏览器响应替身 · 非真实外部来源',
    summary: `第 ${index + 1} 条合成材料只用于来源类型、标题和换行测试。`,
    text_used: `第 ${index + 1} 条合成来源保存的使用文本；没有真实金融事实，也不代表网页全文。`,
    published_at: index === 2 ? null : `2026-10-0${index + 1}T09:00:00+08:00`,
    retrieved_at: '2026-10-13T18:00:00+08:00',
    url: index === 2 ? null : index === 4 ? longUrl : `https://example.test/layout/source-${index + 1}`,
  })),
  retrieval: { searches: [
    { query: '纯布局测试成功检索', freshness: 'oneWeek', status: 'complete', count: 2,
      searched_at: '2026-10-13T18:00:00+08:00' },
    { query: '纯布局测试来源失败检索', freshness: 'oneWeek', status: 'failed', count: 0,
      searched_at: '2026-10-13T18:01:00+08:00', error: 'internal-layout-fixture-error-do-not-render' },
  ] },
  recommendations: [],
};
const contentPieces = [layoutReport.title, layoutReport.summary, ...layoutReport.key_findings,
  ...layoutReport.uncertainties, ...layoutReport.limitations,
  ...layoutReport.timeline.flatMap(item => [item.event]),
  ...layoutReport.sections.flatMap(item => [item.heading, item.body])];
const chineseCharacters = contentPieces.join('').match(/[\u3400-\u9fff]/g)?.length || 0;

const result = {
  startedAt: new Date().toISOString(), fixture: true, realNews: false, realModel: false,
  browserResponseSubstitute: true, savedReportModified: false, chineseCharacters,
  requiredRange: [2000, 4000], screenshotPaths: [], checks: [], pageErrors: [],
  cases: { headings: headings.length + 2, timeline: layoutReport.timeline.length,
    tableRows: 5, sourceKinds: layoutReport.sources.map(item => item.kind),
    failedSearches: 1, limitations: layoutReport.limitations.length,
    longCompanyChars: longCompany.length, longUrlChars: longUrl.length },
};
let browser, context, page;
let current = 'start';
function testId(id) { return page.getByTestId(id); }
async function shot(name) {
  const file = path.join(output, `${name}.png`);
  await page.screenshot({ path: file, fullPage: false, animations: 'allow' });
  result.screenshotPaths.push(file);
  return file;
}
async function check(name, fn) {
  current = name;
  console.log(`RUN ${name}`);
  const details = await fn();
  result.checks.push({ name, passed: true, ...(details || {}) });
  console.log(`PASS ${name}`);
}
async function serverRun(runId) {
  const response = await context.request.get(new URL(`/desktop/api/explore/runs/${runId}`, base).href);
  assert.equal(response.status(), 200);
  return (await response.json()).run;
}
function routeInfo() {
  const query = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { sessionId: query.get('session') || '', runId: query.get('run') || '' };
}
async function viewportCheck(width, height) {
  await page.setViewportSize({ width, height });
  await page.waitForTimeout(240);
  const history = testId('explore-history-drawer');
  if (await history.getAttribute('role') === 'dialog') {
    await history.locator('.explore-history-close').click();
    await history.waitFor({ state: 'hidden' });
  }
  return page.evaluate(() => {
    const node = selector => document.querySelector(selector)?.getBoundingClientRect();
    const send = node('[data-testid="explore-submit"]'), reader = node('.explore-scroll');
    return { width: innerWidth, height: innerHeight, docWidth: document.documentElement.scrollWidth,
      rootScrollHeight: document.documentElement.scrollHeight, bodyScrollHeight: document.body.scrollHeight,
      sendBottom: send?.bottom, readerHeight: reader?.height };
  });
}

try {
  assert.ok(chineseCharacters >= 2000 && chineseCharacters <= 4000,
    `Representative body must contain 2,000–4,000 Chinese characters, got ${chineseCharacters}`);
  const stats = await fetch(new URL('/__explore_fixture__/stats', base));
  assert.equal(stats.status, 200);
  const fixture = await stats.json();
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
  await form.locator('input[name="name"]').fill('Explore 代表性长文验收');
  await form.locator('input[name="email"]').fill(`explore-v3-representative-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('synthetic-representative-fixture-password');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.goto(new URL('#explore', base).href);
  await page.locator('.explore-welcome').waitFor({ state: 'visible' });
  await testId('explore-input').fill('请整理合成布局测试材料的结构。[仅测试页面，不代表真实研究]');
  const post = page.waitForResponse(response => response.request().method() === 'POST' &&
    /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
  await testId('explore-submit').click();
  const created = (await (await post).json()).run;
  assert.ok(created?.id);
  await testId('explore-report').waitFor({ state: 'visible', timeout: 30000 });
  const { sessionId, runId } = routeInfo();
  assert.equal(runId, created.id);
  const savedBefore = await serverRun(runId);
  assert.ok(savedBefore.report?.id);
  result.savedOriginal = { runId, sessionId, reportId: savedBefore.report.id,
    reportTitle: savedBefore.report.title, sourceCount: savedBefore.report.sources?.length || 0 };
  result.build = await page.evaluate(() => document.querySelector('script[type="module"]')?.getAttribute('src') || '');
  await page.waitForTimeout(400);
  await shot('00-normal-synthetic-report-before-browser-substitute-1440x900');

  await page.route('**/desktop/api/explore/**', async requestRoute => {
    const request = requestRoute.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== 'GET' || pathname.endsWith('/events')) return requestRoute.continue();
    const response = await requestRoute.fetch();
    if (!String(response.headers()['content-type'] || '').includes('json')) return requestRoute.fulfill({ response });
    const body = await response.json();
    function substitute(run) {
      if (run?.id !== runId || !run.report) return;
      run.report = { ...run.report, ...layoutReport, id: savedBefore.report.id };
      run.retrieval = layoutReport.retrieval;
    }
    substitute(body.run);
    if (Array.isArray(body.runs)) body.runs.forEach(substitute);
    await requestRoute.fulfill({ response, json: body });
  });
  await page.reload();
  await testId('explore-report').waitFor({ state: 'visible' });
  await page.waitForTimeout(450);

  await check('representative document has structured headings, timeline, table and limits', async () => {
    assert.equal(await page.locator('.explore-report-head h2').innerText(), layoutReport.title);
    assert.ok(await page.getByRole('heading', { name: '事件的最初线索与观察范围' }).count());
    assert.ok(await page.getByRole('heading', { name: '长网址与超长主体名称' }).count());
    assert.equal(await page.locator('.explore-timeline li').count(), 6);
    assert.ok(await page.locator('.explore-report-body table').count() >= 1,
      'Markdown pipe table must render semantically as a table.');
    assert.match(await testId('explore-limitations').innerText(), /来源检索.*失败/);
    await shot('01-representative-report-first-screen-1440x900');
    await testId('explore-uncertainties').scrollIntoViewIfNeeded();
    await shot('02-uncertainties-and-limits-1440x900');
    return { chineseCharacters, sectionHeadings: headings.length + 2 };
  });

  await check('six dated timeline nodes and five synthetic source previews stay legible', async () => {
    await page.locator('.explore-timeline').scrollIntoViewIfNeeded();
    await shot('03-six-event-timeline-1440x900');
    await testId('explore-sources').scrollIntoViewIfNeeded();
    await testId('explore-open-sources').click();
    const sourceList = testId('explore-source-list');
    await sourceList.waitFor({ state: 'visible' });
    assert.equal(await sourceList.locator('.explore-source-preview').count(), layoutReport.sources.length);
    const labels = await sourceList.innerText();
    assert.match(labels, /浏览器响应替身 · 非真实外部来源/);
    assert.match(labels, /发布时间：/);
    assert.doesNotMatch(labels, /类型：|检索时间：/);
    for (const source of layoutReport.sources)
      assert.ok(labels.includes(source.title), `Missing synthetic source title ${source.title}`);
    assert.equal(await sourceList.locator('.explore-source-preview .explore-source-number').count(), 0);
    assert.equal(await sourceList.locator('.explore-source-preview > a, .explore-source-unavailable').count(), 0);
    const linkedTitle = sourceList.locator('.explore-source-preview h3 a[href="https://example.test/layout/source-1"]');
    assert.equal(await linkedTitle.count(), 1);
    const unlinkedTitle = sourceList.locator('.explore-source-preview').filter({ hasText: '用户提供的布局测试原文' }).locator('h3');
    assert.equal(await unlinkedTitle.count(), 1);
    assert.equal(await unlinkedTitle.locator('a').count(), 0);
    await shot('04-five-source-types-1440x900');
    await testId('explore-materials-close').click();
    return { sourceKinds: layoutReport.sources.map(source => source.kind) };
  });

  await check('wide Markdown pipe table scrolls locally without widening the page', async () => {
    const tableNode = page.locator('.explore-report-body table').first();
    await tableNode.scrollIntoViewIfNeeded();
    const geometry = await tableNode.evaluate(tableElement => {
      let node = tableElement.parentElement;
      while (node && node !== document.body && !['auto', 'scroll'].includes(getComputedStyle(node).overflowX)) node = node.parentElement;
      return { tableWidth: tableElement.scrollWidth, tableRows: tableElement.querySelectorAll('tbody tr').length,
        parentWidth: node?.clientWidth, parentScrollWidth: node?.scrollWidth,
        parentOverflowX: node ? getComputedStyle(node).overflowX : null,
        documentWidth: document.documentElement.scrollWidth, viewportWidth: innerWidth,
        parentClass: String(node?.className || '') };
    });
    assert.ok(geometry.tableRows >= 5);
    assert.ok(geometry.documentWidth <= geometry.viewportWidth + 1, JSON.stringify(geometry));
    assert.ok(geometry.parentWidth > 0 && geometry.parentScrollWidth > geometry.parentWidth,
      `Wide table needs a local horizontal scroll container: ${JSON.stringify(geometry)}`);
    await shot('05-wide-markdown-table-1440x900');
    await tableNode.evaluate(tableElement => {
      let node = tableElement.parentElement;
      while (node && node !== document.body && !['auto', 'scroll'].includes(getComputedStyle(node).overflowX)) node = node.parentElement;
      node.scrollLeft = node.scrollWidth;
    });
    await shot('06-wide-table-scrolled-locally-1440x900');
    return geometry;
  });

  await check('long company and URL wrap without horizontal page overflow', async () => {
    const section = page.locator('.explore-report-section', { has: page.getByRole('heading', { name: '长网址与超长主体名称' }) });
    await section.scrollIntoViewIfNeeded();
    assert.match(await section.innerText(), /成都第八十号跨区域产业链研究/);
    assert.ok((await section.innerText()).includes(longUrl), 'The long URL must remain readable in report text.');
    const bounds = await page.evaluate(() => ({ width: document.documentElement.scrollWidth, viewport: innerWidth }));
    assert.ok(bounds.width <= bounds.viewport + 1, JSON.stringify(bounds));
    await shot('07-long-company-and-url-1440x900');
    await section.locator('.explore-source-refs button', { hasText: 'S5' }).click();
    const drawer = testId('explore-materials');
    await drawer.waitFor({ state: 'visible' });
    const sourceLink = drawer.locator('.explore-material-card.source-highlight a[href^="https://example.test/layout/"]');
    assert.equal(await sourceLink.count(), 1, 'The actual source drawer must retain the safe original link.');
    await shot('07b-long-url-original-source-link-1440x900');
    await page.keyboard.press('Escape');
    await drawer.waitFor({ state: 'hidden' });
    return { ...bounds, longCompanyChars: longCompany.length, longUrlChars: longUrl.length,
      reportBodyShowsFullUrl: true, sourceDrawerHasSafeLink: true };
  });

  await check('failed source remains a failure with an explicit limitation and no raw error leakage', async () => {
    const retrieval = testId('explore-retrieval');
    await retrieval.locator('summary').click();
    const failed = retrieval.locator('.explore-searches li').filter({ hasText: '纯布局测试来源失败检索' });
    assert.equal(await failed.count(), 1);
    assert.match(await failed.innerText(), /检索失败/);
    assert.equal((await page.locator('body').innerText()).includes('internal-layout-fixture-error-do-not-render'), false);
    await failed.scrollIntoViewIfNeeded();
    await shot('08-failed-source-search-1440x900');
    await testId('explore-limitations').scrollIntoViewIfNeeded();
    await shot('09-source-failure-limits-1440x900');
    return { failedQuery: '纯布局测试来源失败检索', rawInternalErrorHidden: true };
  });

  await check('representative document stays contained at desktop and mobile reading widths', async () => {
    const metrics = [];
    for (const size of [{ width: 1440, height: 900 }, { width: 1024, height: 768 }, { width: 390, height: 844 }]) {
      const observed = await viewportCheck(size.width, size.height);
      assert.ok(observed.docWidth <= size.width + 1, JSON.stringify(observed));
      assert.ok(observed.sendBottom <= size.height + 1 && observed.readerHeight > 100, JSON.stringify(observed));
      await shot(`10-representative-${size.width}x${size.height}`);
      metrics.push(observed);
    }
    return { metrics };
  });

  await check('browser response substitution never rewrites the saved report', async () => {
    const savedAfter = await serverRun(runId);
    assert.equal(savedAfter.report.id, savedBefore.report.id);
    assert.equal(savedAfter.report.title, savedBefore.report.title);
    assert.deepEqual(savedAfter.report.sections, savedBefore.report.sections);
    assert.deepEqual(savedAfter.report.sources, savedBefore.report.sources);
    assert.notEqual(savedAfter.report.title, layoutReport.title);
    return { savedReportTitle: savedAfter.report.title, substituteTitle: layoutReport.title };
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
  console.log(`EXPLORE_V3_REPRESENTATIVE_OUTPUT=${output}`);
  console.log(`EXPLORE_V3_REPRESENTATIVE_RESULT=${result.passed ? 'PASS' : 'FAIL'} ${result.checks.length} checks; ${chineseCharacters} Chinese characters`);
}
if (!result.passed) process.exitCode = 1;
