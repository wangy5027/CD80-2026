/**
 * Explore V2 glass and motion acceptance. This always uses the disposable local
 * browser_explore_fixture.py: news, model text and deliberate long-title UI data
 * are synthetic. Video encoding rate is not presented as browser render FPS.
 */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, rename, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const playwright = moduleDir
  ? require(existsSync(path.join(moduleDir, 'playwright', 'package.json')) ? path.join(moduleDir, 'playwright') : moduleDir)
  : require('playwright');
const execFileAsync = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const output = path.resolve(process.env.EXPLORE_GLASS_OUTPUT || path.join(root, 'data', 'explore-glass-v2', stamp));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only an isolated local fixture is accepted.');
await mkdir(output, { recursive: true });

const result = {
  startedAt: new Date().toISOString(), fixture: true, realNews: false, realModel: false,
  checks: [], screenshots: [], traces: [], video: null, pageErrors: [], limitations: [], metrics: {},
  safari: { tested: false, reason: 'Native Safari has not been exercised by this Chrome/CDP script.' },
};
let browser;
let videoContext;
let page;
let runId = '';
let sessionId = '';
let reportId = '';
let currentStep = 'fixture availability';
const tid = (page, id) => page.getByTestId(id);

async function check(name, action) {
  currentStep = name;
  console.log(`RUN ${name}`);
  const began = Date.now();
  const details = await action();
  result.checks.push({ name, passed: true, elapsedMs: Date.now() - began, ...(details || {}) });
  console.log(`PASS ${name}`);
}
async function screenshot(page, name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, fullPage: false, animations: 'allow' });
  result.screenshots.push(filename);
  return filename;
}
function route(page) {
  const query = new URLSearchParams(new URL(page.url()).hash.split('?')[1] || '');
  return { sessionId: query.get('session') || '', runId: query.get('run') || '' };
}
async function apiGet(context, pathname) {
  const response = await context.request.get(new URL(pathname, base).href);
  assert.equal(response.status(), 200, pathname);
  return response.json();
}
async function fixtureStats() {
  const response = await fetch(new URL('/__explore_fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200, 'Start tests/browser_explore_fixture.py first.');
  const data = await response.json();
  assert.equal(data.fixture, true);
  assert.equal(data.realNews, false);
  assert.equal(data.realModel, false);
  return data;
}
async function registerStorage() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(base.href);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('Explore 玻璃界面验收');
  await form.locator('input[name="email"]').fill(`explore-glass-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('synthetic-glass-fixture-password');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('button', { name: '开始探索', exact: true }).waitFor({ state: 'visible' });
  const storage = await context.storageState();
  await context.close();
  return storage;
}
async function centerGeometry(page) {
  return page.evaluate(() => {
    const rect = selector => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const { x, y, width, height, right, bottom } = node.getBoundingClientRect();
      return { x, y, width, height, right, bottom, centerX: x + width / 2 };
    };
    return {
      report: rect('.explore-document'), composer: rect('[data-testid="explore-composer"]'),
      summary: rect('[data-testid="explore-summary"]'), header: rect('.explore-report-head'),
      viewport: { width: innerWidth, height: innerHeight, documentWidth: document.documentElement.scrollWidth },
    };
  });
}
function assertCommonAxis(geometry, label) {
  assert.ok(geometry.report && geometry.composer, `${label}: missing report or composer geometry`);
  assert.ok(Math.abs(geometry.report.centerX - geometry.composer.centerX) <= 5,
    `${label}: report/composer centerlines diverge: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.viewport.documentWidth <= geometry.viewport.width + 1,
    `${label}: horizontal overflow: ${JSON.stringify(geometry.viewport)}`);
}
async function sliderSample(page, module) {
  await tid(page, `explore-module-${module}`).click();
  await page.waitForFunction(value => {
    const rail = document.querySelector('.ex-glass-composer__modules');
    const slider = document.querySelector('.ex-glass-composer__slider');
    const button = rail?.querySelector(`button[data-module="${value}"]`);
    if (!slider || !button || !slider.classList.contains('ready')) return false;
    const a = slider.getBoundingClientRect(), b = button.getBoundingClientRect();
    return Math.abs((a.left + a.width / 2) - (b.left + b.width / 2)) <= 3 && Math.abs(a.width - b.width) <= 3;
  }, module, { timeout: 3000 });
  return page.evaluate(value => {
    const slider = document.querySelector('.ex-glass-composer__slider');
    const button = document.querySelector(`.ex-glass-composer__modules button[data-module="${value}"]`);
    const s = slider.getBoundingClientRect(), b = button.getBoundingClientRect();
    return { module: value, sliderLeft: s.left, sliderWidth: s.width, buttonLeft: b.left, buttonWidth: b.width,
      transform: getComputedStyle(slider).transform };
  }, module);
}
async function decorativeMotion(page) {
  return page.evaluate(() => {
    const ambient = document.querySelector('[data-testid="explore-ambient"]');
    const composer = document.querySelector('.ex-glass-composer__shine');
    const header = document.querySelector('.explore-report-head');
    const states = [ambient, composer, header].filter(Boolean).map(node => ({
      selector: node.className?.baseVal || node.className || node.dataset.testid || node.tagName,
      running: node.getAnimations({ subtree: true }).filter(item => item.playState === 'running').length,
      total: node.getAnimations({ subtree: true }).length,
    }));
    return { states, composerShineTransform: composer ? getComputedStyle(composer, '::before').transform : null,
      ambientPaused: ambient?.dataset.paused, composerPaused: document.querySelector('[data-testid="explore-composer"]')?.classList.contains('is-motion-paused') };
  });
}
async function traceScroll(page, name) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const before = await cdp.send('Performance.getMetrics');
  const supported = await page.evaluate(() => PerformanceObserver.supportedEntryTypes?.includes('longtask') || false);
  await cdp.send('Tracing.start', {
    categories: 'toplevel,devtools.timeline,disabled-by-default-devtools.timeline,blink,cc',
    options: 'record-as-much-as-possible', transferMode: 'ReturnAsStream',
  });
  let actionError;
  let action;
  try {
    action = await page.evaluate(() => new Promise(resolve => {
      const pane = document.querySelector('.explore-scroll');
      pane.scrollTo({ top: 0, behavior: 'instant' });
      const max = Math.min(5000, pane.scrollHeight - pane.clientHeight);
      const intervals = [], longTasks = [];
      let previous = null, start = null;
      const observer = PerformanceObserver.supportedEntryTypes?.includes('longtask')
        ? new PerformanceObserver(list => longTasks.push(...list.getEntries().map(entry => entry.duration))) : null;
      observer?.observe({ type: 'longtask', buffered: false });
      function frame(at) {
        start ??= at;
        if (previous != null) intervals.push(at - previous);
        previous = at;
        const progress = Math.min((at - start) / 5200, 1);
        pane.scrollTo({ top: max * progress, behavior: 'instant' });
        if (progress < 1) requestAnimationFrame(frame);
        else {
          observer?.takeRecords().forEach(entry => longTasks.push(entry.duration));
          observer?.disconnect();
          const ordered = intervals.sort((a, b) => a - b);
          const quantile = fraction => ordered.length ? Number(ordered[Math.floor((ordered.length - 1) * fraction)].toFixed(2)) : null;
          resolve({ durationMs: at - start, callbackCount: intervals.length + 1,
            rafIntervalMedianMs: quantile(.5), rafIntervalP95Ms: quantile(.95),
            rafIntervalMaxMs: ordered.length ? Number(ordered.at(-1).toFixed(2)) : null,
            observedLongTasks: longTasks.length,
            longestObservedLongTaskMs: longTasks.length ? Number(Math.max(...longTasks).toFixed(2)) : 0,
            scrollDistancePx: max, textLength: document.querySelector('.explore-document')?.innerText.length || 0 } );
        }
      }
      requestAnimationFrame(frame);
    }));
  } catch (error) { actionError = error; }
  const after = await cdp.send('Performance.getMetrics');
  const completion = new Promise(resolve => cdp.once('Tracing.tracingComplete', resolve));
  await cdp.send('Tracing.end');
  const { stream } = await completion;
  const chunks = [];
  for (;;) {
    const packet = await cdp.send('IO.read', { handle: stream });
    chunks.push(packet.base64Encoded ? Buffer.from(packet.data, 'base64') : Buffer.from(packet.data));
    if (packet.eof) break;
  }
  await cdp.send('IO.close', { handle: stream });
  await cdp.detach();
  const body = Buffer.concat(chunks);
  const filename = path.join(output, `${name}.trace.json`);
  await writeFile(filename, body);
  result.traces.push(filename);
  const traceEvents = JSON.parse(body.toString('utf8')).traceEvents || [];
  const done = traceEvents.filter(item => item.ph === 'X');
  const byName = value => done.filter(item => item.name === value);
  const metricMap = values => Object.fromEntries(values.metrics.map(item => [item.name, item.value]));
  const left = metricMap(before), right = metricMap(after);
  const delta = key => Number(((right[key] || 0) - (left[key] || 0)).toFixed(3));
  if (actionError) throw actionError;
  return { ...action, trace: filename, traceBytes: body.length,
    layoutEvents: byName('Layout').length, styleEvents: byName('UpdateLayoutTree').length,
    paintEvents: byName('Paint').length, taskDurationSeconds: delta('TaskDuration'),
    scriptDurationSeconds: delta('ScriptDuration'), layoutCountDelta: delta('LayoutCount'),
    recalcStyleCountDelta: delta('RecalcStyleCount'), performanceObserverLongTaskSupported: supported,
    note: 'rAF callback intervals and CDP events in headless Chrome; not a measured render FPS.' };
}

try {
  await fixtureStats();
  browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: process.env.BROWSER_HEADLESS !== '0' });
  const storageState = await registerStorage();
  videoContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState,
    recordVideo: { dir: output, size: { width: 1440, height: 900 } }, acceptDownloads: true });
  page = await videoContext.newPage();
  page.setDefaultTimeout(12000);
  page.on('pageerror', error => result.pageErrors.push(String(error)));
  await page.goto(new URL('#explore', base).href);
  await page.locator('.explore-welcome').waitFor({ state: 'visible' });
  await page.evaluate(() => {
    window.__glassInput = document.querySelector('[data-testid="explore-input"]');
    window.__arrivalSeen = [];
    new MutationObserver(() => {
      const node = document.querySelector('.explore-document.report-arriving');
      if (node && !window.__arrivalSeen.length) window.__arrivalSeen.push(performance.now());
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
  });

  await check('entry glass moves during a five-second idle and the focused textarea stays stable', async () => {
    assert.equal(await tid(page, 'explore-motion-toggle').getAttribute('aria-checked'), 'true');
    const composer = tid(page, 'explore-composer');
    const input = tid(page, 'explore-input');
    const before = await decorativeMotion(page);
    await screenshot(page, '01-entry-glass');
    await page.waitForTimeout(5100);
    const after = await decorativeMotion(page);
    await screenshot(page, '02-entry-after-five-seconds');
    assert.ok(before.composerShineTransform !== after.composerShineTransform ||
      after.states.some(item => item.running > 0), 'At least one decorative layer must be visibly active.');
    const idleHeight = await composer.evaluate(node => node.getBoundingClientRect().height);
    assert.ok(idleHeight <= 184, `Empty composer is ${idleHeight}px high at 1440×900.`);
    const unfocused = await composer.locator('.ex-glass-composer').evaluate(node => getComputedStyle(node).boxShadow);
    await input.focus();
    await page.waitForTimeout(250);
    const focused = await composer.locator('.ex-glass-composer').evaluate(node => getComputedStyle(node).boxShadow);
    assert.notEqual(focused, unfocused, 'Focus should visibly strengthen the shell highlight.');
    await screenshot(page, '03-input-focused');
    return { idleComposerHeightPx: idleHeight, shineBefore: before.composerShineTransform,
      shineAfter: after.composerShineTransform, focusShadowChanged: true };
  });

  await check('measured module slider follows mouse and keyboard without losing Chinese draft or IME Enter', async () => {
    const input = tid(page, 'explore-input');
    const question = '请分析测试甲集团拟收购测试乙公司的背景、时间线与影响。[STREAM_EXPLORE]';
    await input.fill(question);
    await input.evaluate(node => node.setSelectionRange(3, 9));
    const before = await input.evaluate(node => [node.value, node.selectionStart, node.selectionEnd]);
    const samples = [];
    for (const module of ['event', 'relation', 'history']) samples.push(await sliderSample(page, module));
    await tid(page, 'explore-module-relation').focus();
    await page.keyboard.press('ArrowRight');
    assert.equal(await tid(page, 'explore-module-history').getAttribute('aria-pressed'), 'true');
    samples.push(await sliderSample(page, 'event'));
    assert.deepEqual(await input.evaluate(node => [node.value, node.selectionStart, node.selectionEnd]), before);
    assert.equal(await input.evaluate(node => node === window.__glassInput), true);
    let submitted = 0;
    const observe = request => { if (request.method() === 'POST' && /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(request.url()).pathname)) submitted += 1; };
    page.on('request', observe);
    await input.evaluate(node => {
      node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '中' }));
      node.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, cancelable: true, key: 'Enter', code: 'Enter', isComposing: true, keyCode: 229 }));
      node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '中' }));
    });
    await page.waitForTimeout(100);
    page.off('request', observe);
    assert.equal(submitted, 0, 'IME candidate confirmation must not create a run.');
    assert.equal(route(page).runId, '');
    await screenshot(page, '04-measured-module-slider');
    return { samples, imeEventSimulated: true, selectedText: before.slice(1) };
  });

  await check('synthetic streamed research shows saved evidence and reaches a persisted report', async () => {
    const before = await fixtureStats();
    const post = page.waitForResponse(response => response.request().method() === 'POST' &&
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
    await tid(page, 'explore-submit').click();
    const response = await post;
    assert.ok([200, 201, 202].includes(response.status()));
    const created = (await response.json()).run;
    assert.ok(created?.id);
    runId = created.id;
    await tid(page, 'explore-progress').waitFor({ state: 'visible', timeout: 15000 });
    ({ sessionId } = route(page));
    assert.ok(sessionId);
    await page.waitForFunction(() => Number(document.querySelector('[data-testid="explore-files-float"] strong')?.textContent || 0) > 0 &&
      !document.querySelector('[data-testid="explore-report"]'), null, { timeout: 15000 });
    const running = (await apiGet(videoContext, `/desktop/api/explore/runs/${runId}`)).run;
    assert.equal(running.report, null);
    assert.ok(running.materials?.length > 0);
    assert.equal(await tid(page, 'explore-input').evaluate(node => node === window.__glassInput), true);
    assert.equal(await tid(page, 'explore-input').isEnabled(), true, 'A following-question draft must be editable during analysis.');
    assert.match(await tid(page, 'explore-input').getAttribute('placeholder') || '', /研究进行中/);
    await tid(page, 'explore-input').fill('下一个问题草稿：请分析审批后的关系路径。');
    assert.equal(await tid(page, 'explore-submit').isDisabled(), true);
    assert.equal(await tid(page, 'explore-module-relation').isDisabled(), true);
    await screenshot(page, '05-real-fixture-research-stage');
    await tid(page, 'explore-report').waitFor({ state: 'visible', timeout: 20000 });
    const saved = (await apiGet(videoContext, `/desktop/api/explore/runs/${runId}`)).run;
    assert.ok(['succeeded', 'partial'].includes(saved.status) && saved.report?.id);
    reportId = saved.report.id;
    assert.equal(await tid(page, 'explore-input').evaluate(node => node === window.__glassInput), true);
    assert.equal(await tid(page, 'explore-input').inputValue(), '下一个问题草稿：请分析审批后的关系路径。');
    assert.ok((await fixtureStats()).reportModelCalls >= before.reportModelCalls + 1);
    const fileResponse = await apiGet(videoContext, `/desktop/api/explore/sessions/${sessionId}/files`);
    await page.waitForFunction(count => Number(document.querySelector('[data-testid="explore-files-float"] strong')?.textContent || 0) === count,
      fileResponse.files.length, { timeout: 3000 });
    const fileCount = fileResponse.files.length;
    const citedCount = saved.materials.length;
    assert.ok(fileCount > citedCount, 'The shared pool includes the saved report; its count must differ from this run’s materials.');
    assert.ok((await page.evaluate(() => window.__arrivalSeen.length)) > 0,
      'A newly completed active report should receive its one-time arrival animation.');
    await page.waitForTimeout(500);
    await screenshot(page, '06-report-arrival');
    await page.waitForTimeout(5100);
    return { runId, reportId, modelProgressChars: running.model_progress?.output_chars || 0,
      fileCount, reportMaterials: citedCount, status: saved.status };
  });

  await check('report and composer share a centerline with core findings visible at 1440 and 1640 widths', async () => {
    const at1440 = await centerGeometry(page);
    assertCommonAxis(at1440, '1440×900 report');
    assert.ok(at1440.composer.height <= 184, `Report composer is ${at1440.composer.height}px high with a short draft.`);
    assert.ok(at1440.summary.y < at1440.composer.y && at1440.summary.y + 42 < at1440.composer.y,
      `The actual core finding must begin above composer: ${JSON.stringify(at1440)}`);
    await page.setViewportSize({ width: 1640, height: 900 });
    const at1640 = await centerGeometry(page);
    assertCommonAxis(at1640, '1640×900 report');
    assert.ok(at1640.summary.y + 42 < at1640.composer.y);
    await screenshot(page, '07-report-1640');
    await page.setViewportSize({ width: 1440, height: 900 });
    return { at1440, at1640 };
  });

  await check('right file drawer preserves the reading axis and saves a real per-module selection', async () => {
    const before = await centerGeometry(page);
    const opener = tid(page, 'explore-files-open');
    await opener.click();
    const dialog = tid(page, 'explore-files-dialog');
    await dialog.waitFor({ state: 'visible' });
    await page.waitForTimeout(410);
    const geometry = await dialog.evaluate(node => {
      const r = node.getBoundingClientRect();
      return { left: r.left, right: r.right, width: r.width, viewport: innerWidth };
    });
    assert.ok(geometry.right >= geometry.viewport - 2 && geometry.width <= 440 && geometry.width >= 310,
      `File review should be a right-side drawer: ${JSON.stringify(geometry)}`);
    assertCommonAxis(await centerGeometry(page), 'file drawer open');
    const during = await centerGeometry(page);
    assert.ok(Math.abs(during.report.centerX - before.report.centerX) <= 5,
      'Opening the file drawer must not shove the report away from its composer.');
    await screenshot(page, '08-reference-file-drawer');
    await tid(page, 'explore-context-manual').click();
    const checkbox = tid(page, 'explore-file-select').first();
    await checkbox.check();
    await tid(page, 'explore-files-done').click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await opener.evaluate(node => node === document.activeElement), true,
      'Closing the file drawer must return focus to its trigger.');
    const stored = await apiGet(videoContext, `/desktop/api/explore/sessions/${sessionId}/context`);
    assert.equal(stored.contexts.event.mode, 'manual');
    assert.equal(stored.contexts.event.file_ids.length, 1);
    return { drawer: geometry, chosenFileId: stored.contexts.event.file_ids[0] };
  });

  await check('citation opens the right evidence drawer, locates source, and Escape restores focus', async () => {
    const sourceButton = page.locator('.explore-source-refs button').first();
    await sourceButton.scrollIntoViewIfNeeded();
    const label = await sourceButton.innerText();
    await sourceButton.click();
    const dialog = tid(page, 'explore-materials');
    await dialog.waitFor({ state: 'visible' });
    const geometry = await dialog.evaluate(node => {
      const r = node.getBoundingClientRect();
      return { left: r.left, right: r.right, width: r.width, viewport: innerWidth };
    });
    assert.ok(geometry.right >= geometry.viewport - 2 && geometry.width >= 310 && geometry.width <= 440,
      `Source review should enter at right: ${JSON.stringify(geometry)}`);
    await dialog.locator('.explore-material-card.source-highlight').waitFor({ state: 'visible', timeout: 1200 });
    await page.waitForTimeout(340);
    const highlighted = await dialog.locator('.explore-material-card.source-highlight').getAttribute('id');
    assertCommonAxis(await centerGeometry(page), 'citation drawer open');
    await screenshot(page, '09-citation-focused-drawer');
    await page.keyboard.press('Escape');
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await sourceButton.evaluate(node => node === document.activeElement), true);
    return { citation: label, highlighted, drawer: geometry };
  });

  await check('clicking a recommendation creates one saved branch and keeps the composer instance', async () => {
    const target = page.locator('[data-testid="explore-recommendation"][data-module="relation"]').first();
    assert.ok(await target.count(), 'The synthetic event report should suggest a relation branch.');
    await target.scrollIntoViewIfNeeded();
    await screenshot(page, '10-next-research-branch');
    const post = page.waitForResponse(response => response.request().method() === 'POST' &&
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
    await target.click();
    const response = await post;
    const body = response.request().postDataJSON();
    assert.equal(body.module, 'relation');
    assert.equal(body.source_report_id, reportId);
    const created = (await response.json()).run;
    assert.ok(created?.id && created.id !== runId);
    await tid(page, 'explore-report').waitFor({ state: 'visible', timeout: 30000 });
    await page.waitForFunction(expected => new URLSearchParams(location.hash.split('?')[1] || '').get('run') === expected,
      created.id, { timeout: 12000 });
    const saved = (await apiGet(videoContext, `/desktop/api/explore/runs/${created.id}`)).run;
    assert.equal(saved.source_report_id, reportId);
    assert.equal(await tid(page, 'explore-input').evaluate(node => node === window.__glassInput), true);
    assert.ok(await tid(page, 'explore-source-context').count() > 0);
    const history = tid(page, 'explore-run-list');
    assert.ok(await history.locator('.explore-run-item').count() >= 2);
    await page.waitForTimeout(540);
    await screenshot(page, '11-branched-report');
    await page.waitForTimeout(700);
    return { sourceReportId: reportId, branchRunId: created.id, branchReportId: saved.report?.id };
  });

  await check('turning motion off stops decoration without disabling research controls', async () => {
    const toggle = tid(page, 'explore-motion-toggle');
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-checked'), 'false');
    await page.waitForTimeout(400);
    const decorative = await decorativeMotion(page);
    assert.equal(decorative.ambientPaused, 'true');
    assert.equal(decorative.composerPaused, true);
    assert.ok(decorative.states.every(item => item.running === 0),
      `Decorative animations still run in static mode: ${JSON.stringify(decorative.states)}`);
    await tid(page, 'explore-module-history').click();
    assert.equal(await tid(page, 'explore-module-history').getAttribute('aria-pressed'), 'true');
    await tid(page, 'explore-files-open').click();
    await tid(page, 'explore-files-dialog').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await tid(page, 'explore-files-dialog').waitFor({ state: 'hidden' });
    await screenshot(page, '12-static-mode-still-usable');
    return decorative;
  });

  const capturedVideo = page.video();
  const savedRoute = new URL(page.url()).hash;
  await videoContext.close();
  videoContext = null;
  const originalVideo = await capturedVideo.path();
  const webm = path.join(output, 'explore-glass-motion-synthetic.webm');
  await rename(originalVideo, webm);
  result.video = { webm, synthetic: true, route: savedRoute };
  try {
    const mp4 = path.join(output, 'explore-glass-motion-synthetic.mp4');
    await execFileAsync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', webm,
      '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
    const { stdout } = await execFileAsync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
      '-of', 'default=noprint_wrappers=1:nokey=1', mp4]);
    result.video.mp4 = mp4;
    result.video.durationSeconds = Number(Number(stdout.trim()).toFixed(2));
  } catch (error) { result.limitations.push(`WebM remains available; MP4 conversion failed: ${String(error)}`); }

  const verifyContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState });
  page = await verifyContext.newPage();
  page.on('pageerror', error => result.pageErrors.push(String(error)));
  await check('refresh restores the saved branch without replaying report-arrival motion', async () => {
    await page.addInitScript(() => {
      window.__glassReloadArrivals = [];
      document.addEventListener('DOMContentLoaded', () => new MutationObserver(() => {
        if (document.querySelector('.explore-document.report-arriving')) {
          window.__glassReloadArrivals.push(performance.now());
        }
      }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] }), { once: true });
    });
    await page.goto(new URL(savedRoute, base).href);
    await tid(page, 'explore-report').waitFor({ state: 'visible' });
    await page.waitForTimeout(500);
    assert.equal((await page.evaluate(() => window.__glassReloadArrivals)).length, 0);
    assert.equal(route(page).runId, result.checks.find(item => item.branchRunId)?.branchRunId);
    return { restoredRunId: route(page).runId };
  });

  await check('top-bar title edits in place and Escape preserves its original value and draft', async () => {
    const title = page.locator('.explore-topbar-title');
    const before = await title.evaluate(node => ({ text: node.innerText, height: node.getBoundingClientRect().height }));
    const draft = await tid(page, 'explore-input').inputValue();
    await title.locator('h1 button').click();
    const editor = tid(page, 'explore-title-editor');
    await editor.waitFor({ state: 'visible' });
    const input = editor.getByRole('textbox', { name: '探索名称' });
    await input.fill('仅作合成验收的待取消标题');
    await screenshot(page, '13-inline-title-edit');
    await page.keyboard.press('Escape');
    await editor.waitFor({ state: 'hidden' });
    const after = await title.evaluate(node => ({ text: node.innerText, height: node.getBoundingClientRect().height }));
    assert.equal(after.text, before.text);
    assert.ok(after.height <= before.height + 1);
    assert.equal(await tid(page, 'explore-input').inputValue(), draft);
    return { topbarTitleHeightPx: before.height, draftPreserved: true };
  });

  await check('synthetic long report title, source title, and body wrap without changing saved data', async () => {
    const branchRunId = route(page).runId;
    const branchSessionId = route(page).sessionId;
    const longTitle = `合成测试长标题：${'这是一段仅供玻璃界面排版测试的研究标题，含有审批、关联与历史证据的多个条件。'.repeat(3)}`;
    const longSource = `合成测试长来源：${'这是一条用于验证右侧证据抽屉自动换行的资料标题。'.repeat(4)}`;
    const paragraph = '合成测试长文段落：交易条件、审批进度、主体关联、历史材料均为自动化替身，不代表实际新闻或投资结论。';
    const longBody = Array.from({ length: 400 }, (_, index) => `${index + 1}. ${paragraph}`).join('\n\n');
    const substitute = run => {
      if (run?.id !== branchRunId) return;
      run.report.title = longTitle;
      run.report.sections = [{ heading: '合成压力测试长文', body: longBody, source_refs: ['S1'] },
        ...(run.report.sections || [])];
      if (run.materials?.length) run.materials[0].title = longSource;
      if (run.report.sources?.length) run.report.sources[0].title = longSource;
    };
    const fulfillSubstitute = async requestRoute => {
      const response = await requestRoute.fetch();
      const json = await response.json();
      if (json.run) substitute(json.run);
      if (json.runs) json.runs.forEach(substitute);
      await requestRoute.fulfill({ response, json });
    };
    await page.route(`**/desktop/api/explore/sessions/${branchSessionId}`, fulfillSubstitute);
    await page.route(`**/desktop/api/explore/runs/${branchRunId}`, fulfillSubstitute);
    await page.reload();
    await tid(page, 'explore-report').waitFor({ state: 'visible' });
    await page.waitForTimeout(450);
    assert.equal(await page.locator('.explore-report-head h2').innerText(), longTitle);
    const contentLength = (await page.locator('.explore-document').innerText()).length;
    assert.ok(contentLength >= 20000, `The performance case needs >=20k visible characters: ${contentLength}`);
    const geometry = await centerGeometry(page);
    assertCommonAxis(geometry, 'long report 1440×900');
    await screenshot(page, '14-long-title-report');
    await tid(page, 'explore-open-materials').click();
    const sources = tid(page, 'explore-materials');
    await sources.waitFor({ state: 'visible' });
    await page.waitForTimeout(450);
    assert.ok(await sources.getByText(longSource, { exact: true }).count() > 0,
      'The synthetic long source title must reach the evidence drawer.');
    const sourceOverflow = await sources.evaluate(node => node.scrollWidth > node.clientWidth + 1);
    assert.equal(sourceOverflow, false, 'Long source title must not widen its drawer.');
    await screenshot(page, '15-long-source-drawer');
    await page.keyboard.press('Escape');
    await sources.waitFor({ state: 'hidden' });
    result.limitations.push('The >=20k-character report title/body and long source title are browser-response test substitutes only; saved fixture data was not modified.');
    return { contentLength, longTitleChars: longTitle.length, longSourceChars: longSource.length };
  });

  await check('motion-on and motion-off Chrome traces use the same saved report and scroll path', async () => {
    const on = await traceScroll(page, 'chrome-motion-on');
    await tid(page, 'explore-motion-toggle').click();
    await page.waitForTimeout(400);
    const off = await traceScroll(page, 'chrome-motion-off');
    assert.equal(on.scrollDistancePx, off.scrollDistancePx);
    assert.equal(on.textLength, off.textLength);
    assert.ok(on.textLength >= 20000 && on.durationMs >= 5000 && off.durationMs >= 5000);
    result.metrics.performance = { motionOn: on, motionOff: off };
    return { motionOn: { callbackCount: on.callbackCount, medianMs: on.rafIntervalMedianMs,
      p95Ms: on.rafIntervalP95Ms, longTasks: on.observedLongTasks },
    motionOff: { callbackCount: off.callbackCount, medianMs: off.rafIntervalMedianMs,
      p95Ms: off.rafIntervalP95Ms, longTasks: off.observedLongTasks } };
  });

  await check('390px mobile and 720px compact viewport remain usable without horizontal overflow', async () => {
    const details = [];
    for (const size of [{ width: 390, height: 844 }, { width: 720, height: 450 }]) {
      await page.setViewportSize(size);
      await page.waitForTimeout(300);
      const drawer = tid(page, 'explore-history-drawer');
      if (await drawer.getAttribute('role') === 'dialog') {
        await drawer.locator('.explore-history-close').click();
        await drawer.waitFor({ state: 'hidden' });
      }
      await page.locator('.explore-scroll').evaluate(node => node.scrollTo({ top: 0, behavior: 'instant' }));
      await page.waitForTimeout(200);
      const geometry = await centerGeometry(page);
      assertCommonAxis(geometry, `${size.width}×${size.height}`);
      assert.ok(await tid(page, 'explore-submit').isVisible());
      await screenshot(page, `${size.width}-responsive-report`);
      details.push({ size, geometry });
    }
    result.limitations.push('720×450 is a compact CSS viewport representative of 200% layout space, not a native browser zoom command.');
    await page.setViewportSize({ width: 1440, height: 900 });
    return { details };
  });

  await check('390×300 short mobile keeps a long next-question draft, source, and controls reachable', async () => {
    await page.setViewportSize({ width: 390, height: 300 });
    const draft = '合成测试长草稿：请继续研究交易审批条件、相关主体的关系、历史案例及资料可信度。'.repeat(20);
    await tid(page, 'explore-input').fill(draft);
    assert.equal(await tid(page, 'explore-input').inputValue(), draft);
    assert.ok(await tid(page, 'explore-source-context').isVisible());
    const bounds = await page.evaluate(() => {
      const rect = selector => {
        const node = document.querySelector(selector), r = node?.getBoundingClientRect();
        return r ? { top: r.top, bottom: r.bottom, height: r.height } : null;
      };
      return { composer: rect('[data-testid="explore-composer"]'), submit: rect('[data-testid="explore-submit"]'),
        input: rect('[data-testid="explore-input"]'), reader: rect('.explore-scroll'), width: document.documentElement.scrollWidth,
        viewport: { width: innerWidth, height: innerHeight } };
    });
    assert.ok(bounds.width <= 391, `Short mobile must not overflow horizontally: ${JSON.stringify(bounds)}`);
    assert.ok(bounds.submit?.bottom <= 301 && bounds.submit.top >= 0,
      `The submit control must stay on screen with a long draft: ${JSON.stringify(bounds)}`);
    assert.ok(bounds.input?.height <= 105, `The draft should scroll within its textarea: ${JSON.stringify(bounds)}`);
    assert.ok(bounds.reader?.height > 0, `Even an extreme short screen must leave a scrollable reading area: ${JSON.stringify(bounds)}`);
    await screenshot(page, '16-mobile-short-long-draft');
    await tid(page, 'explore-files-open').click();
    const drawer = tid(page, 'explore-files-dialog');
    await drawer.waitFor({ state: 'visible' });
    await page.waitForTimeout(350);
    const drawerBounds = await drawer.evaluate(node => { const r = node.getBoundingClientRect(); return { x: r.x, right: r.right, bottom: r.bottom, height: r.height, scrollWidth: node.scrollWidth, clientWidth: node.clientWidth }; });
    assert.ok(drawerBounds.right <= 391 && drawerBounds.bottom <= 301 && drawerBounds.scrollWidth <= drawerBounds.clientWidth + 1,
      `The short-screen drawer must remain contained: ${JSON.stringify(drawerBounds)}`);
    await screenshot(page, '17-mobile-short-file-drawer');
    await page.keyboard.press('Escape');
    await drawer.waitFor({ state: 'hidden' });
    await page.setViewportSize({ width: 1440, height: 900 });
    return { bounds, drawerBounds, draftChars: draft.length };
  });

  await check('system reduced-motion stops decoration while file controls still work', async () => {
    await tid(page, 'explore-motion-toggle').click();
    assert.equal(await tid(page, 'explore-motion-toggle').getAttribute('aria-checked'), 'true');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.waitForTimeout(250);
    const decorated = await decorativeMotion(page);
    assert.equal(decorated.composerPaused, true);
    assert.ok(decorated.states.every(item => item.running === 0));
    await tid(page, 'explore-files-open').click();
    await tid(page, 'explore-files-dialog').waitFor({ state: 'visible' });
    await page.keyboard.press('Escape');
    await tid(page, 'explore-files-dialog').waitFor({ state: 'hidden' });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    return decorated;
  });
  await verifyContext.close();
  assert.deepEqual(result.pageErrors, [], 'No uncaught page errors are allowed.');
  result.passed = true;
} catch (error) {
  result.passed = false;
  result.failedAt = currentStep;
  result.error = { name: error.name, message: error.message, stack: error.stack };
  try { if (page && !page.isClosed()) await screenshot(page, 'failure-state'); } catch {}
  console.error(`FAIL ${currentStep}: ${error.stack || error}`);
} finally {
  try { if (videoContext) await videoContext.close(); } catch {}
  try { if (browser) await browser.close(); } catch {}
  result.completedAt = new Date().toISOString();
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
  console.log(`EXPLORE_GLASS_OUTPUT=${output}`);
  console.log(`EXPLORE_GLASS_RESULT=${result.passed ? 'PASS' : 'FAIL'} ${result.checks.length} checks`);
}
if (!result.passed) process.exitCode = 1;
