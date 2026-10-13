/**
 * Explore V3 UI acceptance against a disposable local fixture. All news/model
 * records are synthetic old-provider substitutes. Layout-only long content is
 * labelled separately. Native IME and browser zoom require human verification.
 */
import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises';
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
const output = path.resolve(process.env.EXPLORE_UI_V3_OUTPUT || path.join(root, 'data', 'explore-ui-v3', stamp));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only an isolated local fixture is accepted.');
await mkdir(output, { recursive: true });

const result = {
  startedAt: new Date().toISOString(), fixture: true, realNews: false, realModel: false,
  checks: [], screenshots: [], traces: [], video: null, videos: [], pageErrors: [], limitations: [], metrics: {},
  criteria: Object.fromEntries(Array.from({ length: 20 }, (_, index) => [`U${String(index + 1).padStart(2, '0')}`,
    { status: 'not_run', evidence: [], limitation: '' }])),
  fixtureContract: 'Legacy TESTING provider substitute; this does not exercise production Tool/Skill orchestration.',
  safari: { tested: false, reason: 'Native Safari has not been exercised by this Chrome/CDP script.' },
};
let browser;
let videoContext;
let page;
let runId = '';
let sessionId = '';
let reportId = '';
let branchRunId = '';
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
function criterion(id, status, evidence, limitation = '') {
  assert.ok(result.criteria[id], `Unknown V3 criterion ${id}`);
  result.criteria[id] = { status, evidence: Array.isArray(evidence) ? evidence : [evidence], limitation };
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

async function traceAction(page, name, action) {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const before = await cdp.send('Performance.getMetrics');
  const longTaskSupported = await page.evaluate(() => {
    window.__v3LongTasks = [];
    if (!PerformanceObserver.supportedEntryTypes?.includes('longtask')) return false;
    window.__v3LongObserver = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) window.__v3LongTasks.push(entry.duration);
    });
    window.__v3LongObserver.observe({ type: 'longtask', buffered: false });
    return true;
  });
  await cdp.send('Tracing.start', {
    categories: 'toplevel,devtools.timeline,disabled-by-default-devtools.timeline,blink,cc',
    options: 'record-as-much-as-possible', transferMode: 'ReturnAsStream',
  });
  const started = Date.now();
  let actionResult, actionError;
  try { actionResult = await action(); } catch (error) { actionError = error; }
  const elapsedMs = Date.now() - started;
  const after = await cdp.send('Performance.getMetrics');
  const longTasks = await page.evaluate(() => {
    window.__v3LongObserver?.takeRecords().forEach(entry => window.__v3LongTasks.push(entry.duration));
    window.__v3LongObserver?.disconnect();
    return window.__v3LongTasks || [];
  });
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
  const bytes = Buffer.concat(chunks);
  const filename = path.join(output, `${name}.trace.json`);
  await writeFile(filename, bytes);
  result.traces.push(filename);
  const events = JSON.parse(bytes.toString('utf8')).traceEvents || [];
  const counts = Object.fromEntries(['Layout', 'Paint', 'UpdateLayoutTree', 'RunTask'].map(key =>
    [key, events.filter(item => item.ph === 'X' && item.name === key).length]));
  const values = source => Object.fromEntries(source.metrics.map(item => [item.name, item.value]));
  const a = values(before), b = values(after);
  const delta = key => Number(((b[key] || 0) - (a[key] || 0)).toFixed(3));
  if (actionError) throw actionError;
  return { actionResult, elapsedMs, trace: filename, traceBytes: bytes.length,
    longTaskSupported, longTaskCount: longTasks.length,
    longestLongTaskMs: longTasks.length ? Math.max(...longTasks) : 0,
    taskDurationSeconds: delta('TaskDuration'), scriptDurationSeconds: delta('ScriptDuration'),
    layoutCountDelta: delta('LayoutCount'), recalcStyleCountDelta: delta('RecalcStyleCount'),
    eventCounts: counts, note: 'Chrome CDP and PerformanceObserver sample; not render FPS.' };
}
async function rafIntervals(page, durationMs = 3000) {
  return page.evaluate(duration => new Promise(resolve => {
    const intervals = [];
    let start = null, previous = null;
    function frame(at) {
      start ??= at;
      if (previous != null) intervals.push(at - previous);
      previous = at;
      if (at - start < duration) requestAnimationFrame(frame);
      else {
        intervals.sort((a, b) => a - b);
        const pick = factor => intervals.length ? Number(intervals[Math.floor((intervals.length - 1) * factor)].toFixed(2)) : null;
        resolve({ durationMs: at - start, callbacks: intervals.length + 1,
          medianIntervalMs: pick(.5), p95IntervalMs: pick(.95), maxIntervalMs: pick(1) });
      }
    }
    requestAnimationFrame(frame);
  }), durationMs);
}
async function finishVideo(context, recordedPage, name) {
  const video = recordedPage.video();
  await context.close();
  const oldPath = await video.path();
  const webm = path.join(output, `${name}.webm`);
  const mp4 = path.join(output, `${name}.mp4`);
  await rename(oldPath, webm);
  await execFileAsync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', webm,
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '22', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', mp4]);
  const { stdout } = await execFileAsync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
    '-of', 'default=noprint_wrappers=1:nokey=1', mp4]);
  const item = { name, webm, mp4, durationSeconds: Number(Number(stdout.trim()).toFixed(2)),
    fixture: true, realNews: false, realModel: false };
  result.videos.push(item);
  return item;
}

async function recordFixedViewportStory(storageState) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState,
    recordVideo: { dir: output, size: { width: 1440, height: 900 } } });
  const storyPage = await context.newPage();
  storyPage.setDefaultTimeout(15000);
  storyPage.on('pageerror', error => result.pageErrors.push(String(error)));
  let storyRunId = '';
  await storyPage.route('**/desktop/api/explore/**', async requestRoute => {
    const request = requestRoute.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() !== 'GET' || pathname.endsWith('/events')) return requestRoute.continue();
    const response = await requestRoute.fetch();
    if (!String(response.headers()['content-type'] || '').includes('json')) return requestRoute.fulfill({ response });
    const json = await response.json();
    const addFifth = run => {
      if (!storyRunId || run?.id !== storyRunId || !run.report || run.report.sections?.some(section => section.heading === '合成第五来源定位')) return;
      const firstSource = run.report.sources?.[0] || {};
      run.report.sources = [...(run.report.sources || []), { ...firstSource, id: 'S5',
        title: '合成测试第 5 条来源 · 仅用于验证抽屉定位', source_name: '浏览器路由替身',
        text_used: '合成第 5 条来源的已保存使用文本，不代表外部新闻。',
        summary: '合成第 5 条来源的摘要。' }];
      run.report.sections = [...(run.report.sections || []), { heading: '合成第五来源定位',
        body: '以下引用只用于界面验收，内容为浏览器响应替身。', source_refs: ['S5'] }];
      run.report.claims = [...(run.report.claims || []), { id: 'story-s5',
        text: '合成第五来源定位主张', support_status: 'supported',
        supports: [{ source_ref: 'S5', quote: '合成第 5 条来源的已保存使用文本' }] }];
    };
    addFifth(json.run);
    if (Array.isArray(json.runs)) json.runs.forEach(addFifth);
    await requestRoute.fulfill({ response, json });
  });
  try {
    await storyPage.goto(new URL('#explore', base).href);
    await tid(storyPage, 'explore-workspace').waitFor({ state: 'visible' });
    await tid(storyPage, 'explore-new').click();
    await storyPage.locator('.explore-welcome').waitFor({ state: 'visible' });
    await screenshot(storyPage, 'v3-story-fixed-entry');
    await storyPage.waitForTimeout(3000);
    await tid(storyPage, 'explore-input').focus();
    await tid(storyPage, 'explore-input').fill('请梳理测试甲集团拟收购测试乙公司的事件脉络与不确定性。[STREAM_EXPLORE]');
    for (const module of ['relation', 'history', 'event']) {
      await tid(storyPage, `explore-module-${module}`).click();
      await storyPage.waitForTimeout(350);
    }
    const post = storyPage.waitForResponse(response => response.request().method() === 'POST' &&
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
    await tid(storyPage, 'explore-submit').click();
    storyRunId = (await (await post).json()).run.id;
    await tid(storyPage, 'explore-progress').waitFor({ state: 'visible' });
    await screenshot(storyPage, 'v3-story-fixed-research');
    await storyPage.waitForTimeout(1000);
    await tid(storyPage, 'explore-report').waitFor({ state: 'visible', timeout: 30000 });
    await storyPage.waitForTimeout(5500);
    await screenshot(storyPage, 'v3-story-fixed-report');
    const fifthCitation = storyPage.locator('.explore-source-refs button', { hasText: '来源 S5' }).first();
    await fifthCitation.waitFor({ state: 'visible', timeout: 5000 });
    await fifthCitation.scrollIntoViewIfNeeded();
    await fifthCitation.click();
    await tid(storyPage, 'explore-materials').waitFor({ state: 'visible' });
    await storyPage.waitForTimeout(700);
    assert.match(await storyPage.locator('.explore-material-card.source-highlight').innerText(), /合成第五来源定位|合成测试第 5 条来源/);
    await screenshot(storyPage, 'v3-story-fixed-fifth-source');
    await storyPage.waitForTimeout(3000);
    await storyPage.keyboard.press('Escape');
    await tid(storyPage, 'explore-materials').waitFor({ state: 'hidden' });
    await storyPage.waitForTimeout(900);
    const relation = storyPage.locator('[data-testid="explore-recommendation"][data-module="relation"]').first();
    await relation.scrollIntoViewIfNeeded();
    await relation.click();
    await tid(storyPage, 'explore-report').waitFor({ state: 'visible', timeout: 30000 });
    await storyPage.waitForTimeout(3000);
    await screenshot(storyPage, 'v3-story-fixed-branch');
    await tid(storyPage, 'explore-motion-toggle').click();
    await storyPage.waitForTimeout(1000);
    const video = await finishVideo(context, storyPage, 'v3-fixed-1440-story-synthetic');
    result.limitations.push('The fifth source shown in the fixed-viewport story was inserted only into the browser response for UI navigation; it was not a saved or real external source.');
    return { storyRunId, video, sourceSubstitute: 'S5 browser-response only' };
  } catch (error) { await context.close(); throw error; }
}

async function recordFixedShortWindow(storageState, existingSessionId, existingRunId) {
  const context = await browser.newContext({ viewport: { width: 390, height: 300 }, storageState,
    recordVideo: { dir: output, size: { width: 390, height: 300 } } });
  const shortPage = await context.newPage();
  shortPage.setDefaultTimeout(15000);
  shortPage.on('pageerror', error => result.pageErrors.push(String(error)));
  try {
    await shortPage.goto(new URL(`#explore?session=${existingSessionId}&run=${existingRunId}`, base).href);
    await tid(shortPage, 'explore-report').waitFor({ state: 'visible' });
    const history = tid(shortPage, 'explore-history-drawer');
    if (await history.getAttribute('role') === 'dialog') {
      await history.locator('.explore-history-close').click();
      await history.waitFor({ state: 'hidden' });
    }
    await shortPage.waitForTimeout(1300);
    await screenshot(shortPage, 'v3-short-fixed-report');
    await tid(shortPage, 'explore-input').fill('合成短窗长草稿：请继续分析资料来源、交易审批与条件。'.repeat(15));
    await shortPage.waitForTimeout(1100);
    await screenshot(shortPage, 'v3-short-fixed-draft');
    await tid(shortPage, 'explore-files-open').click();
    await tid(shortPage, 'explore-files-dialog').waitFor({ state: 'visible' });
    await shortPage.waitForTimeout(900);
    await screenshot(shortPage, 'v3-short-fixed-file-drawer');
    await shortPage.keyboard.press('Escape');
    await tid(shortPage, 'explore-files-dialog').waitFor({ state: 'hidden' });
    const layers = await shortPage.evaluate(() => {
      const y = innerHeight - 2, x = innerWidth / 2;
      return document.elementsFromPoint(x, y).slice(0, 6).map(node => ({
        tag: node.tagName, className: String(node.className?.baseVal || node.className || ''),
        background: getComputedStyle(node).backgroundColor }));
    });
    await shortPage.waitForTimeout(700);
    const video = await finishVideo(context, shortPage, 'v3-fixed-390x300-short-window-synthetic');
    return { video, bottomLayers: layers };
  } catch (error) { await context.close(); throw error; }
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
  result.environment = await page.evaluate(() => ({ userAgent: navigator.userAgent,
    platform: navigator.platform, devicePixelRatio, viewport: { width: innerWidth, height: innerHeight },
    prefReduceMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
    appScript: document.querySelector('script[type="module"]')?.getAttribute('src') || '',
    bodyScrollHeight: document.body.scrollHeight }));
  result.environment.browserVersion = browser.version();
  result.environment.cpuThrottle = 'none';
  result.environment.backgroundThrottling = 'headless Chrome default; no explicit override';
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
    const idleTrace = await traceAction(page, 'v3-idle-motion-on', () => rafIntervals(page, 5100));
    result.metrics.idleMotionOn = idleTrace;
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
    criterion('U01', 'pass', ['01-entry-glass.png', '02-entry-after-five-seconds.png']);
    criterion('U16', 'partial', ['v3-idle-motion-on.trace.json'], 'The on/off/reduced functional comparison runs later in this script.');
    return { idleComposerHeightPx: idleHeight, shineBefore: before.composerShineTransform,
      shineAfter: after.composerShineTransform, focusShadowChanged: true,
      idleRaf: idleTrace.actionResult };
  });

  await check('entry examples fill editable questions and never execute, and keyboard focus is visible', async () => {
    const examples = page.locator('.explore-example-pills button');
    assert.equal(await examples.count(), 3);
    const labels = await examples.allInnerTexts();
    assert.ok(labels.every(label => !/示例问题[一二三123]/.test(label)), `Generic examples remain: ${labels}`);
    let posts = 0;
    const onRequest = request => {
      if (request.method() === 'POST' && /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(request.url()).pathname)) posts += 1;
    };
    page.on('request', onRequest);
    for (const module of ['event', 'relation', 'history']) {
      await page.locator(`.explore-example-pills button[data-module="${module}"]`).click();
      assert.equal(await tid(page, `explore-module-${module}`).getAttribute('aria-pressed'), 'true');
      assert.ok((await tid(page, 'explore-input').inputValue()).length > 20);
      assert.equal(await tid(page, 'explore-input').evaluate(node => node === window.__glassInput), true);
    }
    page.off('request', onRequest);
    assert.equal(posts, 0);
    await tid(page, 'explore-input').focus();
    await page.keyboard.press('Tab');
    const keyboard = await page.evaluate(() => {
      const active = document.activeElement;
      const style = active && getComputedStyle(active);
      return { tag: active?.tagName, label: active?.getAttribute('aria-label') || active?.textContent?.trim().slice(0, 45),
        outlineStyle: style?.outlineStyle, outlineWidth: style?.outlineWidth,
        boxShadow: style?.boxShadow, rect: active?.getBoundingClientRect().toJSON() };
    });
    assert.ok(keyboard.rect?.width > 0 && keyboard.rect?.height > 0);
    const focused = await page.locator(':focus').count();
    assert.equal(focused, 1);
    await screenshot(page, '03b-keyboard-focus-and-examples');
    await tid(page, 'explore-module-event').click();
    criterion('U02', 'pass', ['03-input-focused.png', '03b-keyboard-focus-and-examples.png']);
    return { labels, posts, keyboard };
  });

  await check('Chinese text insertion trace keeps one textarea and selection stable', async () => {
    const input = tid(page, 'explore-input');
    await input.fill('');
    await input.focus();
    const trace = await traceAction(page, 'v3-chinese-text-insertion', async () => {
      for (let index = 0; index < 24; index += 1) {
        await page.keyboard.insertText('研究材料与主体关系');
        await page.waitForTimeout(25);
      }
    });
    assert.equal(await input.evaluate(node => node === window.__glassInput), true);
    assert.equal(await page.locator('[data-testid="explore-input"]').count(), 1);
    const chars = (await input.inputValue()).length;
    assert.equal(chars, '研究材料与主体关系'.length * 24);
    const selection = await input.evaluate(node => [node.selectionStart, node.selectionEnd]);
    assert.deepEqual(selection, [chars, chars]);
    await screenshot(page, '03c-chinese-text-insertion');
    result.metrics.chineseTextInsertion = trace;
    return { chars, selection, trace: trace.trace,
      limitation: 'Chrome insertText is a browser automation input path, not a native Chinese IME.' };
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
    criterion('U03', 'partial', ['04-measured-module-slider.png'], 'CompositionEvent was synthetic; native Chinese IME candidate confirmation remains untested.');
    criterion('U08', 'partial', ['04-measured-module-slider.png'], 'Rapid drawer and module succession is tested later.');
    return { samples, imeEventSimulated: true, selectedText: before.slice(1) };
  });

  await check('synthetic streamed research shows saved evidence and reaches a persisted report', async () => {
    const before = await fixtureStats();
    const entryComposer = await tid(page, 'explore-composer').boundingBox();
    await page.evaluate(() => {
      window.__v3SceneFrames = [];
      const started = performance.now();
      function sample(at) {
        const composer = document.querySelector('[data-testid="explore-composer"]')?.getBoundingClientRect();
        const documentPane = document.querySelector('.explore-scroll');
        window.__v3SceneFrames.push({ at: Math.round(at - started), y: composer?.y,
          height: composer?.height, inputCount: document.querySelectorAll('[data-testid="explore-input"]').length,
          readerChildren: documentPane?.children.length });
        if (at - started < 1800) requestAnimationFrame(sample);
      }
      requestAnimationFrame(sample);
    });
    const post = page.waitForResponse(response => response.request().method() === 'POST' &&
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
    const submittedAt = Date.now();
    await tid(page, 'explore-submit').click();
    const response = await post;
    const submitResponseLatencyMs = Date.now() - submittedAt;
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
    assert.equal(await page.locator('[data-testid="explore-input"]').count(), 1);
    const activeComposer = await tid(page, 'explore-composer').boundingBox();
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
    const notice = tid(page, 'explore-file-arrivals');
    if (await notice.count()) {
      await notice.hover();
      await page.waitForTimeout(3100);
      assert.equal(await notice.isVisible(), true, 'Hovering a new-file notice must pause automatic dismissal.');
      await screenshot(page, '06b-save-notice-held');
      await page.mouse.move(850, 500);
      await notice.waitFor({ state: 'hidden', timeout: 4800 });
    } else {
      throw new Error('New-file notice disappeared before the hover/focus dismissal check.');
    }
    const sceneFrames = await page.evaluate(() => window.__v3SceneFrames || []);
    assert.ok(sceneFrames.length > 5 && sceneFrames.every(frame => frame.inputCount === 1),
      'No scene frame may contain a duplicate active textarea.');
    const boundarySpacing = await page.evaluate(() => {
      const selectors = ['[data-testid="explore-summary"]', '.explore-finding-list', '.explore-finding-list li',
        '.explore-finding-list li p', '[data-testid="explore-uncertainties"]', '[data-testid="explore-uncertainties"] h3',
        '[data-testid="explore-uncertainties"] .explore-simple-list', '[data-testid="explore-uncertainties"] .explore-simple-list li'];
      return selectors.map(selector => {
        const node = document.querySelector(selector);
        if (!node) return { selector, missing: true };
        const style = getComputedStyle(node), rect = node.getBoundingClientRect();
        return { selector, top: rect.top, bottom: rect.bottom, height: rect.height,
          paddingTop: style.paddingTop, paddingBottom: style.paddingBottom,
          marginTop: style.marginTop, marginBottom: style.marginBottom, minHeight: style.minHeight };
      });
    });
    result.metrics.boundarySpacing = boundarySpacing;
    criterion('U04', 'pass', ['05-real-fixture-research-stage.png', 'v3 scene frame sampling']);
    criterion('U05', 'pass', ['05-real-fixture-research-stage.png', 'draft and textarea identity assertions']);
    criterion('U06', 'partial', ['05-real-fixture-research-stage.png', '06-report-arrival.png'],
      'Synthetic legacy provider exercises the actual UI/persistence path, not the new production Tool/Skill chain.');
    await page.waitForTimeout(1100);
    return { runId, reportId, modelProgressChars: running.model_progress?.output_chars || 0,
      fileCount, reportMaterials: citedCount, status: saved.status, submitResponseLatencyMs,
      entryComposer, activeComposer, sceneFrames: sceneFrames.slice(0, 20), boundarySpacing };
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
    branchRunId = created.id;
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
    criterion('U10', 'pass', ['10-next-research-branch.png', '11-branched-report.png', 'saved branch source_report_id']);
    return { sourceReportId: reportId, branchRunId: created.id, branchReportId: saved.report?.id };
  });

  await check('a background synthetic stream finishes without stealing the old report', async () => {
    const input = tid(page, 'explore-input');
    await input.fill('请继续研究合成资料中的审批条件。[STREAM_EXPLORE]');
    const post = page.waitForResponse(response => response.request().method() === 'POST' &&
      /\/api\/explore\/sessions\/[^/]+\/runs$/.test(new URL(response.url()).pathname));
    await tid(page, 'explore-submit').click();
    const created = (await (await post).json()).run;
    assert.ok(created?.id && created.id !== branchRunId);
    await page.waitForFunction(id => new URLSearchParams(location.hash.split('?')[1] || '').get('run') === id,
      created.id, { timeout: 12000 });
    await tid(page, 'explore-progress').waitFor({ state: 'visible' });
    await tid(page, 'explore-run-list').locator('.explore-run-item').first().click();
    await page.waitForFunction(id => new URLSearchParams(location.hash.split('?')[1] || '').get('run') === id,
      runId, { timeout: 12000 });
    await tid(page, 'explore-report').waitFor({ state: 'visible' });
    const selectedBefore = route(page).runId;
    let finished;
    for (let retry = 0; retry < 50; retry += 1) {
      finished = (await apiGet(videoContext, `/desktop/api/explore/runs/${created.id}`)).run;
      if (!['queued', 'running'].includes(finished.status)) break;
      await page.waitForTimeout(300);
    }
    assert.ok(['succeeded', 'partial'].includes(finished.status), `Background run did not finish: ${finished.status}`);
    await page.waitForTimeout(700);
    assert.equal(route(page).runId, selectedBefore);
    assert.equal(await tid(page, 'explore-report').isVisible(), true);
    await screenshot(page, '11b-background-complete-old-report-stable');
    await tid(page, 'explore-run-list').locator('.explore-run-item').filter({ hasText: '追踪并购关系路径' }).first().click();
    await page.waitForFunction(id => new URLSearchParams(location.hash.split('?')[1] || '').get('run') === id,
      branchRunId, { timeout: 12000 });
    await tid(page, 'explore-report').waitFor({ state: 'visible' });
    criterion('U07', 'pass', ['11b-background-complete-old-report-stable.png', 'saved synthetic background run']);
    return { backgroundRunId: created.id, backgroundStatus: finished.status, selectedRunId: selectedBefore };
  });

  await check('rapid module choices and drawer toggles settle on the last requested state', async () => {
    const input = tid(page, 'explore-input');
    const draft = '下一题草稿：检测快速操作后仍保留。';
    await input.fill(draft);
    const trace = await traceAction(page, 'v3-rapid-controls-and-drawers', async () => {
      for (const module of ['history', 'relation', 'event', 'history', 'event'])
        await tid(page, `explore-module-${module}`).click();
      assert.equal(await tid(page, 'explore-module-event').getAttribute('aria-pressed'), 'true');
      for (let index = 0; index < 2; index += 1) {
        await tid(page, 'explore-files-open').click();
        await tid(page, 'explore-files-dialog').waitFor({ state: 'visible' });
        await page.keyboard.press('Escape');
        await tid(page, 'explore-files-dialog').waitFor({ state: 'hidden' });
      }
      await tid(page, 'explore-open-materials').click();
      await tid(page, 'explore-materials').waitFor({ state: 'visible' });
      await page.keyboard.press('Escape');
      await tid(page, 'explore-materials').waitFor({ state: 'hidden' });
    });
    await page.waitForTimeout(500);
    assert.equal(await input.inputValue(), draft);
    assert.equal(await input.evaluate(node => node === window.__glassInput), true);
    assert.equal(await tid(page, 'explore-module-event').getAttribute('aria-pressed'), 'true');
    assert.equal(await page.locator('.explore-materials-overlay').count(), 0);
    await screenshot(page, '11c-rapid-controls-settled');
    result.metrics.drawerInteractions = trace;
    criterion('U08', 'pass', ['04-measured-module-slider.png', '11c-rapid-controls-settled.png']);
    return { finalModule: 'event', draftPreserved: true, trace: trace.trace };
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
    criterion('U16', 'partial', ['12-static-mode-still-usable.png', 'v3-idle-motion-on.trace.json'],
      'Motion on/off/reduced functional assertions pass; performance comparisons follow later.');
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
    const expectedFiles = (await apiGet(verifyContext, `/desktop/api/explore/sessions/${sessionId}/files`)).files.length;
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
    await page.waitForTimeout(3100);
    assert.equal((await page.evaluate(() => window.__glassReloadArrivals)).length, 0);
    assert.equal(route(page).runId, result.checks.find(item => item.branchRunId)?.branchRunId);
    assert.equal(await tid(page, 'explore-file-arrivals').count(), 0, 'A refreshed saved report should not replay a new-file notice.');
    assert.equal((await apiGet(verifyContext, `/desktop/api/explore/sessions/${sessionId}/files`)).files.length, expectedFiles);
    criterion('U11', 'pass', ['saved branch refresh', `server file count ${expectedFiles}`]);
    return { restoredRunId: route(page).runId, expectedFiles };
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
      run.report.sections = [{ heading: '合成压力测试长文', body: longBody, source_refs: ['S5'] },
        ...(run.report.sections || [])];
      if (run.materials?.length) run.materials[0].title = longSource;
      if (run.report.sources?.length) run.report.sources[0].title = longSource;
      const sample = run.report.sources?.[0] || {};
      run.report.sources = [sample, ...['S2', 'S3', 'S4', 'S5'].map(id => ({ ...sample, id,
        title: id === 'S5' ? longSource : `合成来源 ${id}`, source_name: '浏览器路由替身',
        text_used: id === 'S5' ? '合成第 5 条材料引用片段：仅供来源定位测试。' : `合成 ${id} 摘录`,
        summary: id === 'S5' ? '合成第 5 条材料摘要，仅供来源定位测试。' : `合成 ${id} 摘要` }))];
      run.report.claims = [...(run.report.claims || []), { id: 'synthetic-fifth-claim',
        text: '合成第 5 条来源仅用于测试来源定位。', claim_type: 'fact', support_status: 'supported',
        supports: [{ source_ref: 'S5', quote: '合成第 5 条材料引用片段' }] }];
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
    criterion('U12', 'partial', ['14-long-title-report.png', '15-long-source-drawer.png'],
      'The saved report was replaced in browser responses; actual 10k-character production stream was not exercised.');
    return { contentLength, longTitleChars: longTitle.length, longSourceChars: longSource.length };
  });

  await check('fifth synthetic citation targets only the fifth source and restores focus', async () => {
    const citation = page.locator('.explore-source-refs button', { hasText: '来源 S5' }).first();
    assert.equal(await citation.count(), 1);
    await citation.scrollIntoViewIfNeeded();
    await citation.click();
    const drawer = tid(page, 'explore-materials');
    await drawer.waitFor({ state: 'visible' });
    const highlighted = drawer.locator('.explore-material-card.source-highlight');
    await highlighted.waitFor({ state: 'visible' });
    await page.waitForTimeout(450);
    assert.match(await highlighted.innerText(), /S5.*合成测试长来源|合成测试长来源.*S5/s);
    assert.match(await highlighted.innerText(), /合成第 5 条材料引用片段/);
    assert.match(await highlighted.innerText(), /合成第 5 条来源仅用于测试来源定位/);
    assert.equal(await drawer.locator('.explore-material-card.source-highlight').count(), 1);
    const position = await highlighted.evaluate(node => {
      const r = node.getBoundingClientRect(), viewport = node.closest('.explore-materials-scroll')?.getBoundingClientRect();
      return { cardTop: r.top, cardBottom: r.bottom, viewportTop: viewport?.top, viewportBottom: viewport?.bottom };
    });
    assert.ok(position.cardTop >= position.viewportTop - 2 && position.cardTop < position.viewportBottom,
      `Fifth citation did not scroll into the evidence viewport: ${JSON.stringify(position)}`);
    await screenshot(page, '15b-fifth-source-focused');
    await page.keyboard.press('Escape');
    await drawer.waitFor({ state: 'hidden' });
    assert.equal(await citation.evaluate(node => node === document.activeElement), true);
    criterion('U09', 'partial', ['15b-fifth-source-focused.png'],
      'The fifth source and claim were browser-response substitutes; citation navigation and focus were real UI behavior.');
    return { position, sourceId: 'S5', browserResponseSubstitute: true };
  });

  await check('labelled ten-thousand-character DOM update keeps completed report and draft stable', async () => {
    const draft = '合成长文更新后的下一题草稿';
    await tid(page, 'explore-input').fill(draft);
    const initial = await page.evaluate(() => {
      window.__v3StableSummary = document.querySelector('[data-testid="explore-summary"]');
      window.__v3StableInput = document.querySelector('[data-testid="explore-input"]');
      return { summaryText: window.__v3StableSummary?.innerText, inputValue: window.__v3StableInput?.value };
    });
    const trace = await traceAction(page, 'v3-layout-only-long-dom-update', async () => {
      await page.evaluate(async () => {
        const container = document.createElement('section');
        container.dataset.v3LayoutOnly = 'true';
        container.style.cssText = 'max-width:100%;overflow-wrap:anywhere;color:#333;padding:12px';
        document.querySelector('.explore-report-body').append(container);
        for (let index = 0; index < 20; index += 1) {
          const block = document.createElement('p');
          block.textContent = `合成布局流式追加第 ${index + 1} 段：` + '研究材料、来源、主体、条件及限制。'.repeat(36);
          container.append(block);
          await new Promise(resolve => setTimeout(resolve, 70));
        }
      });
    });
    const observed = await page.evaluate(() => ({
      addedChars: document.querySelector('[data-v3-layout-only]')?.innerText.length || 0,
      summarySameNode: document.querySelector('[data-testid="explore-summary"]') === window.__v3StableSummary,
      inputSameNode: document.querySelector('[data-testid="explore-input"]') === window.__v3StableInput,
      inputValue: document.querySelector('[data-testid="explore-input"]')?.value,
      overflow: document.documentElement.scrollWidth - innerWidth,
    }));
    assert.ok(observed.addedChars >= 10000, JSON.stringify(observed));
    assert.equal(observed.summarySameNode, true);
    assert.equal(observed.inputSameNode, true);
    assert.equal(observed.inputValue, draft);
    assert.ok(observed.overflow <= 1);
    await screenshot(page, '15c-layout-only-long-update');
    await page.evaluate(() => document.querySelector('[data-v3-layout-only]')?.remove());
    result.metrics.layoutOnlyLongUpdate = trace;
    criterion('U12', 'partial', ['14-long-title-report.png', '15c-layout-only-long-update.png',
      'v3-layout-only-long-dom-update.trace.json'],
      '>=10k characters were appended to a labelled test-only DOM section; this does not verify production Vue streaming or Tool/Skill model output.');
    return { ...observed, trace: trace.trace, initialSummaryChars: initial.summaryText.length };
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
    criterion('U16', 'pass', ['chrome-motion-on.trace.json', 'chrome-motion-off.trace.json',
      '12-static-mode-still-usable.png']);
    return { motionOn: { callbackCount: on.callbackCount, medianMs: on.rafIntervalMedianMs,
      p95Ms: on.rafIntervalP95Ms, longTasks: on.observedLongTasks },
    motionOff: { callbackCount: off.callbackCount, medianMs: off.rafIntervalMedianMs,
      p95Ms: off.rafIntervalP95Ms, longTasks: off.observedLongTasks } };
  });

  await check('desktop height matrix keeps reader and controls in their own scroll regions', async () => {
    const details = [];
    for (const size of [{ width: 1440, height: 900 }, { width: 1280, height: 720 },
      { width: 1440, height: 700 }, { width: 1024, height: 768 }]) {
      await page.setViewportSize(size);
      await page.waitForTimeout(170);
      const geometry = await centerGeometry(page);
      assertCommonAxis(geometry, `${size.width}×${size.height}`);
      const bounds = await page.evaluate(() => {
        const input = document.querySelector('[data-testid="explore-input"]')?.getBoundingClientRect();
        const send = document.querySelector('[data-testid="explore-submit"]')?.getBoundingClientRect();
        const reader = document.querySelector('.explore-scroll')?.getBoundingClientRect();
        return { inputBottom: input?.bottom, sendBottom: send?.bottom, readerHeight: reader?.height,
          rootScrollHeight: document.documentElement.scrollHeight, bodyScrollHeight: document.body.scrollHeight,
          viewportHeight: innerHeight, width: document.documentElement.scrollWidth };
      });
      assert.ok(bounds.sendBottom <= size.height + 1 && bounds.readerHeight >= 100, JSON.stringify(bounds));
      assert.ok(bounds.rootScrollHeight <= size.height + 1 && bounds.bodyScrollHeight <= size.height + 1,
        `Whole-page overflow appears at ${size.width}×${size.height}: ${JSON.stringify(bounds)}`);
      await screenshot(page, `v3-desktop-${size.width}x${size.height}`);
      details.push({ size, geometry, bounds });
    }
    criterion('U13', 'pass', details.map(item => `v3-desktop-${item.size.width}x${item.size.height}.png`));
    criterion('U14', 'partial', ['v3-desktop-1024x768.png'],
      '1024 and 390 CSS viewports were exercised; native text zoom remains untested.');
    return { details };
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
    criterion('U14', 'partial', ['390-responsive-report.png', '720-responsive-report.png'],
      'Responsive viewports pass; native browser text scaling was not invoked.');
    criterion('U15', 'not_tested', [], 'Native Chrome 125% and 200% browser zoom was not automated; CSS viewport substitution is not equivalent.');
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
    criterion('U20', 'partial', ['16-mobile-short-long-draft.png', '17-mobile-short-file-drawer.png'],
      'Fixed Chrome viewport screenshots captured; old video gray edge cannot be attributed without reproducing the native capture surface.');
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
  const businessPath = process.env.EXPLORE_V3_BUSINESS_REPORT;
  if (businessPath) {
    const business = JSON.parse(await readFile(path.resolve(businessPath), 'utf8'));
    assert.equal(business.passed, true, `Separate business browser suite did not pass: ${businessPath}`);
    assert.equal(business.pageErrors.length, 0);
    const businessBuild = process.env.EXPLORE_V3_BUSINESS_BUILD || 'not recorded in external report';
    const businessLimitation = businessBuild !== result.environment.appScript
      ? `Separate business suite ran on ${businessBuild}; this V3 run is ${result.environment.appScript}. Only UI presentation and safe pipe-table handling changed after the business run.` : '';
    result.businessRegression = { path: businessPath, build: businessBuild, checks: business.checks.length,
      sameBuild: businessBuild === result.environment.appScript };
    criterion('U17', 'pass', [businessPath, 'separate fixture cancel/failure/retry/refresh/delete checks'], businessLimitation);
    criterion('U18', 'pass', [businessPath, 'separate fixture ordinary chat/Build/Evolve navigation check'], businessLimitation);
    criterion('U19', 'pass', [businessPath, 'separate fixture unsafe Markdown/source/account-isolation checks'], businessLimitation);
  } else {
    for (const id of ['U17', 'U18', 'U19']) criterion(id, 'not_tested', [],
      'The separate full business browser suite must run against the final V3 build; set EXPLORE_V3_BUSINESS_REPORT to its passing report.json.');
  }
  await verifyContext.close();
  await check('fixed 1440×900 story video shows entry, research, saved reading, fifth citation and branch', async () => {
    const story = await recordFixedViewportStory(storageState);
    assert.ok(story.video.durationSeconds >= 20);
    return story;
  });
  await check('separate fixed 390×300 short-window video identifies actual bottom layers', async () => {
    const short = await recordFixedShortWindow(storageState, sessionId, runId);
    assert.ok(short.video.durationSeconds >= 4);
    criterion('U20', 'partial', ['v3-fixed-1440-story-synthetic.mp4',
      'v3-fixed-390x300-short-window-synthetic.mp4', 'v3-short-fixed-report.png', 'v3-short-fixed-draft.png'],
      'Fixed Chrome viewports and bottom-element layers are recorded; native prior-recording gray artifact was not reproduced.');
    return short;
  });
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
  console.log(`EXPLORE_UI_V3_OUTPUT=${output}`);
  console.log(`EXPLORE_UI_V3_RESULT=${result.passed ? 'PASS' : 'FAIL'} ${result.checks.length} checks`);
}
if (!result.passed) process.exitCode = 1;
