/**
 * Explore ambient-theme browser acceptance, screenshots, video and CDP traces.
 *
 * Build the frontend and start tests/browser_explore_fixture.py on port 48994:
 * PLAYWRIGHT_MODULE_DIR=/path/to/node_modules node tests/browser_explore_visual.mjs
 *
 * The fixture uses labelled synthetic news and model output. This script never
 * points to a non-loopback server or reads a real account/database. The video
 * shows the real Explore UI and state transitions driven by that fixture, not
 * a prototype's preview control or a production model integration.
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
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const output = path.resolve(process.env.EXPLORE_VISUAL_OUTPUT || path.join(projectRoot, 'data', 'explore-visual', stamp));
const base = new URL(process.env.BROWSER_BASE_URL || 'http://127.0.0.1:48994/desktop/');
assert.equal(base.protocol, 'http:');
assert.ok(['127.0.0.1', 'localhost'].includes(base.hostname), 'Only an isolated local fixture is accepted.');
await mkdir(output, { recursive: true });

const result = {
  startedAt: new Date().toISOString(), fixture: true, realNews: false, realModel: false,
  videoUsesSyntheticData: true, baseUrl: base.href, checks: [], screenshots: [],
  video: null, streamVideo: null, traces: [], metrics: {}, pageErrors: [], limitations: [],
};
let browser;
let videoContext;
let streamContext;
let interactionContext;
let currentStep = 'availability';
let primaryPage;

function tid(page, id) { return page.getByTestId(id); }

async function check(name, fn) {
  currentStep = name;
  console.log(`RUN ${name}`);
  const started = Date.now();
  const detail = await fn();
  result.checks.push({ name, passed: true, elapsedMs: Date.now() - started, ...(detail || {}) });
  console.log(`PASS ${name}`);
}

async function screenshot(page, name) {
  const filename = path.join(output, `${name}.png`);
  await page.screenshot({ path: filename, animations: 'allow', fullPage: false });
  result.screenshots.push(filename);
  return filename;
}

async function fixtureStats() {
  const response = await fetch(new URL('/__explore_fixture__/stats', base), { signal: AbortSignal.timeout(5000) });
  assert.equal(response.status, 200, 'Start tests/browser_explore_fixture.py first.');
  const stats = await response.json();
  assert.equal(stats.fixture, true);
  assert.equal(stats.realNews, false);
  assert.equal(stats.realModel, false);
  return stats;
}

async function reportRun(page) {
  const hash = new URL(page.url()).hash;
  const query = new URLSearchParams(hash.split('?')[1] || '');
  const runId = query.get('run');
  assert.ok(runId, 'The selected run must have a URL identity.');
  const response = await page.context().request.get(new URL(`/desktop/api/explore/runs/${encodeURIComponent(runId)}`, base).href);
  assert.equal(response.status(), 200);
  const data = await response.json();
  return data.run || data;
}

async function registerAndGetStorage() {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  await page.goto(base.href);
  await page.getByRole('button', { name: '创建账号', exact: true }).click();
  const form = page.locator('#auth-form');
  await form.locator('input[name="name"]').fill('Explore 视觉验收');
  await form.locator('input[name="email"]').fill(`explore-visual-${Date.now()}@example.test`);
  await form.locator('input[name="password"]').fill('synthetic-visual-fixture-password');
  await form.getByRole('button', { name: '创建账号', exact: true }).click();
  await page.getByRole('button', { name: '开始探索', exact: true }).waitFor({ state: 'visible' });
  const storage = await context.storageState();
  await context.close();
  return storage;
}

async function scene(page, name) {
  await page.locator(`[data-testid="explore-workspace"][data-scene="${name}"]`).waitFor({ state: 'visible', timeout: 45000 });
  assert.equal(await tid(page, 'explore-ambient').count(), 1);
  return page.evaluate(() => ({
    scene: document.querySelector('[data-testid="explore-workspace"]')?.dataset.scene,
    sameAmbient: document.querySelector('[data-testid="explore-ambient"]') === window.__visualAmbient,
    sameInput: document.querySelector('[data-testid="explore-input"]') === window.__visualInput,
  }));
}

async function ambientAnimations(page) {
  return page.evaluate(() => {
    const root = document.querySelector('[data-testid="explore-ambient"]');
    if (!root) return [];
    return root.getAnimations({ subtree: true }).map((animation, index) => {
      const target = animation.effect?.target;
      const pseudo = animation.effect?.pseudoElement || null;
      const computed = target ? getComputedStyle(target, pseudo) : null;
      return {
        index,
        name: animation.animationName || '',
        playState: animation.playState,
        currentTime: typeof animation.currentTime === 'number' ? animation.currentTime : null,
        transform: computed?.transform || '',
        keyframesTransform: Boolean(animation.effect?.getKeyframes?.().some(frame => frame.transform != null)),
      };
    });
  });
}

function animatedLayer(states) {
  const selected = states.find(item => item.keyframesTransform);
  assert.ok(selected, 'The ambient must contain a transform-animated layer.');
  return selected;
}

async function waitAnimationSample(page, delay = 850) {
  const before = animatedLayer(await ambientAnimations(page));
  await page.waitForTimeout(delay);
  const after = animatedLayer(await ambientAnimations(page));
  return { before, after };
}

async function traceSegment(page, name, action) {
  const longTaskObserverSupported = await page.evaluate(() => {
    window.__exploreVisualLongTasks = [];
    window.__exploreVisualLongTaskObserver?.disconnect();
    if (!PerformanceObserver.supportedEntryTypes?.includes('longtask')) return false;
    const observer = new PerformanceObserver(list => {
      for (const entry of list.getEntries()) window.__exploreVisualLongTasks.push(entry.duration);
    });
    observer.observe({ type: 'longtask', buffered: false });
    window.__exploreVisualLongTaskObserver = observer;
    return true;
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Tracing.start', {
    categories: 'toplevel,devtools.timeline,disabled-by-default-devtools.timeline,blink,cc,disabled-by-default-devtools.timeline.frame',
    options: 'record-as-much-as-possible',
    transferMode: 'ReturnAsStream',
  });
  let actionError;
  let actionValue;
  try { actionValue = await action(); } catch (error) { actionError = error; }
  const observedLongTasks = await page.evaluate(() => {
    const observer = window.__exploreVisualLongTaskObserver;
    if (!observer) return null;
    for (const entry of observer.takeRecords()) window.__exploreVisualLongTasks.push(entry.duration);
    observer.disconnect();
    window.__exploreVisualLongTaskObserver = null;
    return window.__exploreVisualLongTasks;
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
  const filename = path.join(output, `${name}.trace.json`);
  const body = Buffer.concat(chunks);
  await writeFile(filename, body);
  result.traces.push(filename);
  const parsed = JSON.parse(body.toString('utf8'));
  const events = Array.isArray(parsed.traceEvents) ? parsed.traceEvents : [];
  const renderer = new Set(events.filter(item => item.name === 'thread_name' && /CrRendererMain/.test(item.args?.name || ''))
    .map(item => `${item.pid}:${item.tid}`));
  const onMain = item => !renderer.size || renderer.has(`${item.pid}:${item.tid}`);
  const completed = events.filter(item => item.ph === 'X' && onMain(item));
  const layout = completed.filter(item => item.name === 'Layout');
  const style = completed.filter(item => item.name === 'RecalculateStyles' || item.name === 'UpdateLayoutTree');
  const runTasks = completed.filter(item => item.name === 'RunTask');
  const longTasks = runTasks.filter(item => Number(item.dur) >= 50_000);
  const frames = events.filter(item => ['BeginFrame', 'DrawFrame'].includes(item.name) && onMain(item) && Number.isFinite(item.ts));
  const byName = frames.filter(item => item.name === (frames.some(event => event.name === 'BeginFrame') ? 'BeginFrame' : 'DrawFrame'));
  const intervals = byName.map((event, index) => index ? (event.ts - byName[index - 1].ts) / 1000 : null)
    .filter(value => value != null && value >= 0 && value < 1000).sort((a, b) => a - b);
  const percentile = p => intervals.length ? Number(intervals[Math.min(intervals.length - 1, Math.floor((intervals.length - 1) * p))].toFixed(2)) : null;
  const metrics = {
    trace: filename, bytes: body.byteLength, rendererThreads: renderer.size,
    layoutEvents: layout.length, styleEvents: style.length,
    observedMainRunTasks: runTasks.length,
    longMainTasksOver50ms: runTasks.length ? longTasks.length : null,
    longestMainRunTaskMs: runTasks.length
      ? Number((Math.max(...runTasks.map(item => Number(item.dur))) / 1000).toFixed(2)) : null,
    performanceObserverLongTaskSupported: longTaskObserverSupported,
    performanceObserverLongTasksOver50ms: observedLongTasks?.length ?? null,
    performanceObserverLongestMs: observedLongTasks?.length
      ? Number(Math.max(...observedLongTasks).toFixed(2)) : (longTaskObserverSupported ? 0 : null),
    observedFrameEvents: byName.length, frameIntervalMedianMs: percentile(.5), frameIntervalP95Ms: percentile(.95),
    pageRaf: actionValue?.raf || null,
  };
  if (!runTasks.length) result.limitations.push(`${name}: CDP did not record main-thread RunTask events; CDP long-task count is unavailable.`);
  if (!longTaskObserverSupported) result.limitations.push(`${name}: PerformanceObserver longtask is unsupported in this browser.`);
  if (actionError) throw actionError;
  return metrics;
}

try {
  await fixtureStats();
  browser = await playwright.chromium.launch({
    channel: process.env.BROWSER_CHANNEL || 'chrome',
    headless: process.env.BROWSER_HEADLESS !== '0',
  });
  const storageState = await registerAndGetStorage();

  await check('entry, research and saved report share the same rendered ambient and input', async () => {
    videoContext = await browser.newContext({
      viewport: { width: 1440, height: 900 }, storageState,
      recordVideo: { dir: output, size: { width: 1440, height: 900 } },
      acceptDownloads: true,
    });
    primaryPage = await videoContext.newPage();
    primaryPage.setDefaultTimeout(12000);
    primaryPage.on('pageerror', error => result.pageErrors.push(String(error)));
    await primaryPage.goto(new URL('#explore', base).href);
    await tid(primaryPage, 'explore-workspace').waitFor({ state: 'visible' });
    await primaryPage.locator('.explore-welcome').waitFor({ state: 'visible' });
    assert.equal(await primaryPage.getByText('预览状态', { exact: true }).count(), 0,
      'The visual prototype state switch must not enter the production Explore page.');
    await primaryPage.evaluate(() => {
      window.__visualAmbient = document.querySelector('[data-testid="explore-ambient"]');
      window.__visualInput = document.querySelector('[data-testid="explore-input"]');
    });
    const entry = await scene(primaryPage, 'entry');
    assert.equal(entry.sameAmbient, true);
    assert.equal(entry.sameInput, true);
    assert.equal(await primaryPage.locator('[data-testid="explore-ambient"]').getAttribute('aria-hidden'), 'true');
    assert.equal(await tid(primaryPage, 'explore-ambient').evaluate(node => getComputedStyle(node).pointerEvents), 'none');
    await screenshot(primaryPage, '01-entry-desktop');

    const question = '请分析测试甲集团拟收购测试乙公司的背景、时间线与影响。[WAIT_EXPLORE]';
    await tid(primaryPage, 'explore-input').fill(question);
    await tid(primaryPage, 'explore-input').evaluate(node => node.setSelectionRange(2, 7));
    const selectedBefore = await tid(primaryPage, 'explore-input').evaluate(node => [node.value, node.selectionStart, node.selectionEnd]);
    await tid(primaryPage, 'explore-module-relation').click();
    await tid(primaryPage, 'explore-module-event').click();
    const selectedAfter = await tid(primaryPage, 'explore-input').evaluate(node => [node.value, node.selectionStart, node.selectionEnd]);
    assert.deepEqual(selectedAfter, selectedBefore, 'Changing modules must retain the composer element, draft and caret range.');

    const beforeSubmit = await fixtureStats();
    await tid(primaryPage, 'explore-input').evaluate(node => {
      node.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '研究' }));
      const event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: true, bubbles: true, cancelable: true });
      node.dispatchEvent(event);
      node.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '研究' }));
    });
    await primaryPage.waitForTimeout(110);
    await tid(primaryPage, 'explore-input').evaluate(node => {
      const event = new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', isComposing: false,
        bubbles: true, cancelable: true });
      Object.defineProperty(event, 'keyCode', { configurable: true, get: () => 229 });
      node.dispatchEvent(event);
    });
    await primaryPage.waitForTimeout(150);
    assert.equal((await fixtureStats()).modelCalls, beforeSubmit.modelCalls,
      'Simulated composing and keyCode 229 Enter must not submit. This is not a real OS IME test.');
    const sent = primaryPage.waitForResponse(response => new URL(response.url()).pathname.endsWith('/runs')
      && response.request().method() === 'POST');
    await tid(primaryPage, 'explore-input').focus();
    await primaryPage.keyboard.press('Enter');
    assert.ok([200, 201, 202].includes((await sent).status()));
    const research = await scene(primaryPage, 'research');
    assert.equal(research.sameAmbient, true);
    assert.equal(research.sameInput, true);
    await primaryPage.locator('.explore-progress').waitFor({ state: 'visible' });
    await primaryPage.waitForFunction(() =>
      document.querySelector('.explore-progress h3')?.textContent?.includes('正在调用模型分析'),
    null, { timeout: 15000 });
    assert.equal(await tid(primaryPage, 'explore-cancel').count(), 1);
    const progressVisibility = await primaryPage.evaluate(() => {
      const stage = document.querySelector('.explore-progress h3')?.getBoundingClientRect();
      const cancel = document.querySelector('[data-testid="explore-cancel"]')?.getBoundingClientRect();
      const composer = document.querySelector('.explore-compose-zone')?.getBoundingClientRect();
      return { stage: stage && { top: stage.top, bottom: stage.bottom },
        cancel: cancel && { top: cancel.top, bottom: cancel.bottom },
        composerTop: composer?.top, viewportHeight: innerHeight };
    });
    assert.ok(progressVisibility.stage?.top >= 0 && progressVisibility.stage.bottom < progressVisibility.composerTop,
      `The real research stage must be visible above the composer without scrolling: ${JSON.stringify(progressVisibility)}`);
    assert.ok(progressVisibility.cancel?.top >= 0 && progressVisibility.cancel.bottom < progressVisibility.composerTop,
      `Cancel must be visible during research without scrolling: ${JSON.stringify(progressVisibility)}`);
    await screenshot(primaryPage, '02-research-desktop');
    const running = await reportRun(primaryPage);
    assert.ok(['queued', 'running'].includes(running.status));

    await tid(primaryPage, 'explore-report').waitFor({ state: 'visible', timeout: 45000 });
    const reportScene = await scene(primaryPage, 'report');
    assert.equal(reportScene.sameAmbient, true);
    assert.equal(reportScene.sameInput, true);
    const saved = await reportRun(primaryPage);
    assert.ok(['succeeded', 'partial'].includes(saved.status) && saved.report?.id,
      'The report in the clip must actually be saved by the fixture-backed application.');
    assert.ok(saved.stage_history?.some(item => item.stage === 'searching_web'));
    assert.ok(saved.materials?.some(item => item.kind === 'news'));
    const findingVisibility = await primaryPage.evaluate(() => {
      const heading = [...document.querySelectorAll('[data-testid="explore-report"] h3')]
        .find(node => node.textContent?.trim() === '核心发现')?.getBoundingClientRect();
      const composer = document.querySelector('.explore-compose-zone')?.getBoundingClientRect();
      return { heading: heading && { top: heading.top, bottom: heading.bottom },
        composerTop: composer?.top };
    });
    assert.ok(findingVisibility.heading?.top >= 0 && findingVisibility.heading.bottom < findingVisibility.composerTop,
      `The actual report findings must be visible above the composer without scrolling: ${JSON.stringify(findingVisibility)}`);
    await screenshot(primaryPage, '03-report-desktop');
    await primaryPage.waitForTimeout(850);
    const savedRoute = new URL(primaryPage.url()).hash;
    const videoObject = primaryPage.video();
    const originalVideo = await videoObject.path();
    await videoContext.close();
    videoContext = null;
    const videoWebm = path.join(output, 'explore-entry-research-report-synthetic.webm');
    await rename(originalVideo, videoWebm);
    result.video = { webm: videoWebm, runId: saved.id, reportId: saved.report.id, savedRoute, synthetic: true };
    try {
      const videoMp4 = path.join(output, 'explore-entry-research-report-synthetic.mp4');
      await execFileAsync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', '-i', videoWebm,
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', videoMp4]);
      const { stdout } = await execFileAsync('ffprobe', ['-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', videoMp4]);
      result.video.mp4 = videoMp4;
      result.video.durationSeconds = Number(Number(stdout.trim()).toFixed(2));
      if (result.video.durationSeconds < 20 || result.video.durationSeconds > 35) {
        result.limitations.push(`The video is ${result.video.durationSeconds}s, outside the requested 20–35s short-clip range.`);
      }
    } catch (error) {
      result.limitations.push(`MP4 conversion unavailable; WebM is usable: ${String(error)}`);
    }
    return { scenes: ['entry', 'research', 'report'], inputCompositionSimulated: true,
      savedRunId: saved.id, videoSeconds: result.video.durationSeconds ?? null };
  });

  await check('saved materials and public model-output counts appear before a streamed synthetic report completes', async () => {
    streamContext = await browser.newContext({
      viewport: { width: 1440, height: 900 }, storageState,
      recordVideo: { dir: output, size: { width: 1440, height: 900 } },
    });
    const page = await streamContext.newPage();
    page.setDefaultTimeout(15000);
    page.on('pageerror', error => result.pageErrors.push(String(error)));
    await page.goto(new URL('#explore', base).href);
    await scene(page, 'entry');
    await tid(page, 'explore-input').fill('请分析测试甲集团拟收购测试乙公司的审批条件、时间线和影响。[STREAM_EXPLORE]');
    await tid(page, 'explore-submit').click();
    await scene(page, 'research');
    await page.waitForFunction(() => {
      const text = document.querySelector('[data-testid="explore-progress"]')?.textContent || '';
      const dock = document.querySelector('[data-testid="explore-files-float"] strong');
      return /已接收\s*[1-9]\d*\s*字符/.test(text) && Number(dock?.textContent || 0) > 0 &&
        !document.querySelector('[data-testid="explore-report"]');
    }, null, { timeout: 20000 });
    const arrivalsToggle = tid(page, 'explore-file-arrivals-toggle');
    await arrivalsToggle.waitFor({ state: 'visible' });
    if (await arrivalsToggle.getAttribute('aria-expanded') === 'false') await arrivalsToggle.click();
    await tid(page, 'explore-file-arrival').first().waitFor({ state: 'visible' });
    const first = await page.evaluate(() => {
      const text = document.querySelector('[data-testid="explore-progress"]')?.textContent || '';
      const pulse = document.querySelector('.explore-progress-pulse');
      return { chars: Number(/已接收\s*(\d+)\s*字符/.exec(text)?.[1] || 0),
        fileCount: Number(document.querySelector('[data-testid="explore-files-float"] strong')?.textContent || 0),
        arrivalCount: document.querySelectorAll('[data-testid="explore-file-arrival"]').length,
        animation: pulse ? getComputedStyle(pulse).animationName : 'none' };
    });
    const inProgress = await reportRun(page);
    assert.equal(inProgress.report, null);
    assert.ok(inProgress.materials?.length > 0 && inProgress.model_progress?.output_chars >= first.chars);
    assert.ok(first.fileCount > 0, 'Material files must appear in the floating session pool before report completion.');
    assert.ok(first.arrivalCount > 0, 'Newly added material files must appear in the arrival tray.');
    assert.ok(first.animation !== 'none', 'The real running stage must have a visible CSS pulse.');
    await screenshot(page, 'ux-stream-materials-and-progress');
    await page.waitForFunction(previous => {
      const text = document.querySelector('[data-testid="explore-progress"]')?.textContent || '';
      return Number(/已接收\s*(\d+)\s*字符/.exec(text)?.[1] || 0) > previous &&
        !document.querySelector('[data-testid="explore-report"]');
    }, first.chars, { timeout: 15000 });
    const later = await page.evaluate(() => Number(/已接收\s*(\d+)\s*字符/.exec(
      document.querySelector('[data-testid="explore-progress"]')?.textContent || '')?.[1] || 0));
    assert.ok(later > first.chars);
    await screenshot(page, 'ux-stream-progress-advanced');
    await tid(page, 'explore-report').waitFor({ state: 'visible', timeout: 20000 });
    const saved = await reportRun(page);
    assert.ok(saved.report?.id && ['succeeded', 'partial'].includes(saved.status));
    await screenshot(page, 'ux-stream-report-complete');
    const videoObject = page.video();
    await streamContext.close();
    streamContext = null;
    const sourceVideo = await videoObject.path();
    const target = path.join(output, 'explore-material-progress-synthetic.webm');
    await rename(sourceVideo, target);
    result.streamVideo = { webm: target, synthetic: true, runId: saved.id,
      initialChars: first.chars, laterChars: later, earlyFileCount: first.fileCount };
    return { beforeReportChars: first.chars, beforeReportLaterChars: later,
      beforeReportFileCount: first.fileCount, arrivalCount: first.arrivalCount,
      stageAnimation: first.animation, reportId: saved.report.id };
  });

  await check('ambient moves while active and respects system reduced motion without a phase reset', async () => {
    interactionContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, storageState });
    const page = await interactionContext.newPage();
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => result.pageErrors.push(String(error)));
    await page.goto(new URL(result.video.savedRoute, base).href);
    await scene(page, 'report');
    const moving = await waitAnimationSample(page);
    assert.equal(moving.before.playState, 'running');
    assert.ok(moving.after.currentTime > moving.before.currentTime + 250,
      'The animated transform timeline should advance with wall time.');
    assert.notEqual(moving.before.transform, moving.after.transform,
      'The ambient transform must visibly change over time.');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('[data-testid="explore-ambient"][data-paused="true"]').waitFor();
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const reduced = await ambientAnimations(page);
    const reducedTransform = await tid(page, 'explore-ambient').evaluate(node =>
      Array.from(node.getAnimations({ subtree: true }), animation => {
        const target = animation.effect?.target;
        return target ? getComputedStyle(target, animation.effect?.pseudoElement || null).transform : null;
      }));
    await page.waitForTimeout(600);
    const reducedLater = await ambientAnimations(page);
    const reducedTransformLater = await tid(page, 'explore-ambient').evaluate(node =>
      Array.from(node.getAnimations({ subtree: true }), animation => {
        const target = animation.effect?.target;
        return target ? getComputedStyle(target, animation.effect?.pseudoElement || null).transform : null;
      }));
    assert.ok(reduced.length === 0 || reduced.every(item => item.playState === 'paused' || item.playState === 'finished'),
      'Reduced-motion mode must stop decorational animation.');
    assert.deepEqual(reducedTransformLater, reducedTransform);
    const paused = reduced.find(item => item.keyframesTransform);
    const pausedLater = reducedLater.find(item => item.keyframesTransform);
    if (paused && pausedLater) {
      assert.ok(Math.abs(pausedLater.currentTime - paused.currentTime) < 20,
        `Reduced motion should freeze the animation clock: ${paused.currentTime} → ${pausedLater.currentTime}.`);
    }
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.locator('[data-testid="explore-ambient"][data-paused="false"]').waitFor();
    const resumed = await waitAnimationSample(page, 450);
    assert.equal(resumed.before.playState, 'running');
    assert.ok(resumed.after.currentTime > resumed.before.currentTime + 200,
      'The ambient should move again after system reduced motion is turned off.');
    if (pausedLater) {
      assert.ok(Math.abs(resumed.before.currentTime - pausedLater.currentTime) < 350,
        'Re-enabling movement should continue the paused phase.');
    }
    return { activeTransformChanged: true, reducedAnimationCount: reducedLater.length,
      resumedFromMs: pausedLater?.currentTime ?? null };
  });

  await check('hidden tab pauses ambience and visible tab resumes without fast-forward', async () => {
    const page = interactionContext.pages()[0];
    const before = animatedLayer(await ambientAnimations(page));
    const cover = await interactionContext.newPage();
    await cover.goto('about:blank');
    await cover.bringToFront();
    await page.waitForTimeout(250);
    let hidden = await page.evaluate(() => document.visibilityState);
    let simulated = false;
    if (hidden !== 'hidden') {
      simulated = true;
      await page.evaluate(() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
        Object.defineProperty(document, 'hidden', { configurable: true, get: () => true });
        document.dispatchEvent(new Event('visibilitychange'));
      });
      hidden = await page.evaluate(() => document.visibilityState);
    }
    assert.equal(hidden, 'hidden');
    await page.waitForTimeout(120);
    const paused = await waitAnimationSample(page, 400);
    assert.equal(paused.before.playState, 'paused');
    assert.ok(Math.abs(paused.after.currentTime - paused.before.currentTime) < 20,
      `Hidden ambient clock should remain frozen: ${JSON.stringify({ simulated, before: paused.before, after: paused.after })}`);
    if (simulated) {
      await page.evaluate(() => {
        delete document.visibilityState;
        delete document.hidden;
        document.dispatchEvent(new Event('visibilitychange'));
      });
      result.limitations.push('Chromium headless did not hide the first tab; visibilitychange handling was simulated.');
    }
    await page.bringToFront();
    await cover.close();
    const resumed = animatedLayer(await ambientAnimations(page));
    assert.equal(resumed.playState, 'running');
    assert.ok(Math.abs(resumed.currentTime - paused.after.currentTime) < 400,
      'Visibility resume should not advance by the full time spent hidden.');
    return { realTabVisibility: !simulated, beforeMs: before.currentTime, pausedMs: paused.after.currentTime };
  });

  await check('report reading, file and source dialogs keep layout and selection stable', async () => {
    const page = interactionContext.pages()[0];
    const report = tid(page, 'explore-report');
    const scroll = page.locator('.explore-scroll');
    const dimensions = await scroll.evaluate(node => ({ clientHeight: node.clientHeight, scrollHeight: node.scrollHeight }));
    assert.ok(dimensions.scrollHeight > dimensions.clientHeight + 300,
      'The saved report should be long enough for a meaningful scroll test.');
    const beforeWidth = await page.locator('.explore-document').evaluate(node => node.getBoundingClientRect().width);
    await scroll.evaluate(node => node.scrollTo({ top: Math.min(node.scrollHeight - node.clientHeight, 500), behavior: 'instant' }));
    await page.waitForTimeout(80);
    assert.ok(await scroll.evaluate(node => node.scrollTop) >= 200, 'The long report must respond to an explicit scroll.');
    const selection = await report.evaluate(node => {
      const text = node.querySelector('.explore-markdown')?.firstChild?.firstChild;
      if (!text || text.nodeType !== Node.TEXT_NODE || text.textContent.length < 2) return null;
      const range = document.createRange();
      range.setStart(text, 0);
      range.setEnd(text, Math.min(text.textContent.length, 4));
      const selected = window.getSelection();
      selected.removeAllRanges();
      selected.addRange(range);
      return selected.toString();
    });
    assert.ok(selection, 'The report text must allow selection.');
    await page.waitForTimeout(550);
    assert.equal(await page.evaluate(() => window.getSelection()?.toString()), selection);
    await tid(page, 'explore-open-materials').click();
    await tid(page, 'explore-materials').waitFor({ state: 'visible' });
    const materialWidth = await page.locator('.explore-document').evaluate(node => node.getBoundingClientRect().width);
    assert.ok(Math.abs(materialWidth - beforeWidth) < 2, 'Opening sources should not reflow the report width.');
    await screenshot(page, '04-source-dialog-desktop');
    await page.keyboard.press('Escape');
    await tid(page, 'explore-files-open').click();
    await tid(page, 'explore-files-dialog').waitFor({ state: 'visible' });
    const fileWidth = await page.locator('.explore-document').evaluate(node => node.getBoundingClientRect().width);
    assert.ok(Math.abs(fileWidth - beforeWidth) < 2, 'Opening file pool should not reflow the report width.');
    await screenshot(page, '05-file-pool-desktop');
    await page.keyboard.press('Escape');
    return { scrollHeight: dimensions.scrollHeight, clientHeight: dimensions.clientHeight,
      reportWidthPx: beforeWidth, materialWidthPx: materialWidth, fileWidthPx: fileWidth };
  });

  await check('desktop history remains at the left until explicitly hidden and recommendation modules use distinct colors', async () => {
    const page = interactionContext.pages()[0];
    await page.setViewportSize({ width: 1440, height: 900 });
    await scene(page, 'report');
    const history = tid(page, 'explore-history-drawer');
    const placement = await page.evaluate(() => {
      const sidebar = document.querySelector('[data-testid="explore-history-drawer"]')?.getBoundingClientRect();
      const main = document.querySelector('.explore-main')?.getBoundingClientRect();
      return { sidebarRight: sidebar?.right, mainLeft: main?.left, mainWidth: main?.width,
        mainInert: document.querySelector('.explore-main')?.inert || false };
    });
    assert.ok(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned')));
    assert.ok(placement.sidebarRight <= placement.mainLeft + 2 && placement.mainWidth >= 700 && !placement.mainInert,
      `Pinned desktop history must sit left of an operable reading area: ${JSON.stringify(placement)}`);
    const cards = tid(page, 'explore-recommendation');
    assert.equal(await cards.count(), 3);
    const colors = await cards.evaluateAll(nodes => nodes.map(node => ({ module: node.dataset.module,
      background: getComputedStyle(node).backgroundColor, border: getComputedStyle(node).borderTopColor,
      accent: getComputedStyle(node.querySelector('span')).color })));
    assert.deepEqual(colors.map(item => item.module), ['event', 'relation', 'history']);
    assert.equal(new Set(colors.map(item => item.background)).size, 3);
    assert.equal(new Set(colors.map(item => item.border)).size, 3);
    assert.equal(new Set(colors.map(item => item.accent)).size, 3);
    await cards.first().scrollIntoViewIfNeeded();
    await screenshot(page, 'ux-three-recommendation-colors');
    await tid(page, 'explore-new').click();
    await scene(page, 'entry');
    assert.ok(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned')));
    const firstSession = history.locator('.explore-session-item').filter({ hasText: '[WAIT_EXPLORE]' }).first();
    await firstSession.click();
    await scene(page, 'report');
    assert.ok(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned')),
      'Selecting an older report must not close the desktop sidebar.');
    await history.getByRole('button', { name: '关闭研究历史' }).click();
    assert.ok(!(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned'))));
    await page.reload();
    await scene(page, 'report');
    assert.ok(!(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned'))),
      'An explicit hide must survive refresh for this temporary account.');
    await tid(page, 'explore-history-toggle').click();
    assert.ok(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned')));
    await page.reload();
    await scene(page, 'report');
    assert.ok(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned')),
      'Explicitly restoring history must survive refresh.');
    return { placement, colors, hiddenAndRestoredAcrossRefresh: true };
  });

  await check('Chinese composer draft and selection survive system motion changes', async () => {
    const page = interactionContext.pages()[0];
    const composer = tid(page, 'explore-input');
    const draft = '中文问题：请进一步核对审批条件与历史案例。';
    await composer.fill(draft);
    await composer.evaluate(node => node.setSelectionRange(3, 8));
    const before = await composer.evaluate(node => ({ value: node.value, start: node.selectionStart, end: node.selectionEnd }));
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.locator('[data-testid="explore-ambient"][data-paused="true"]').waitFor();
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.locator('[data-testid="explore-ambient"][data-paused="false"]').waitFor();
    const after = await composer.evaluate(node => ({ value: node.value, start: node.selectionStart, end: node.selectionEnd }));
    assert.deepEqual(after, before);
    await composer.fill('');
    return { draftCharacters: draft.length, selection: [before.start, before.end] };
  });

  await check('390px reading uses drawers without horizontal overflow', async () => {
    const page = interactionContext.pages()[0];
    await page.setViewportSize({ width: 390, height: 844 });
    await scene(page, 'report');
    const history = tid(page, 'explore-history-drawer');
    await page.waitForFunction(() => document.querySelector('[data-testid="explore-history-drawer"]')?.getAttribute('role') === 'dialog',
      null, { timeout: 3000 });
    assert.equal(await history.getAttribute('role'), 'dialog',
      'With the saved visible-history preference, mobile starts with its history drawer open.');
    assert.equal(await page.locator('.explore-main').evaluate(node => node.inert), true);
    await screenshot(page, 'ux-mobile-history-initial');
    await history.getByRole('button', { name: '关闭研究历史' }).click();
    assert.equal(await page.locator('.explore-main').evaluate(node => node.inert), false);
    await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="explore-history-drawer"]')).visibility === 'hidden',
      null, { timeout: 1500 });
    const overflow = await page.evaluate(() => ({
      documentWidth: document.documentElement.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      bodyWidth: document.body.scrollWidth,
    }));
    assert.ok(overflow.documentWidth <= overflow.viewportWidth + 1,
      `Mobile Explore has horizontal document overflow: ${JSON.stringify(overflow)}`);
    await screenshot(page, '06-report-mobile');
    await tid(page, 'explore-history-toggle').click();
    assert.equal(await history.getAttribute('role'), 'dialog');
    await page.waitForFunction(() => getComputedStyle(document.querySelector('[data-testid="explore-history-drawer"]')).transform === 'matrix(1, 0, 0, 1, 0, 0)',
      null, { timeout: 1500 });
    await screenshot(page, '07-history-mobile');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('.explore-main').evaluate(node => node.inert), false);
    await tid(page, 'explore-files-open').click();
    await tid(page, 'explore-files-dialog').waitFor({ state: 'visible' });
    await screenshot(page, '08-file-pool-mobile');
    await page.keyboard.press('Escape');
    return overflow;
  });

  await check('CDP traces separate idle background from report interaction', async () => {
    const page = interactionContext.pages()[0];
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.locator('.explore-scroll').evaluate(node => { node.scrollTop = 0; });
    await page.waitForTimeout(250);
    const idle = await traceSegment(page, '09-idle-ambient', () => page.evaluate(() => new Promise(resolve => {
      const times = [];
      let began = null;
      function frame(at) {
        began ??= at;
        times.push(at);
        if (at - began < 2500) requestAnimationFrame(frame);
        else {
          const intervals = times.slice(1).map((value, index) => value - times[index]).sort((a, b) => a - b);
          const percentile = fraction => Number(intervals[Math.floor((intervals.length - 1) * fraction)].toFixed(2));
          resolve({ raf: { frames: times.length, medianIntervalMs: percentile(.5), p95IntervalMs: percentile(.95) } });
        }
      }
      requestAnimationFrame(frame);
    })));
    const interaction = await traceSegment(page, '10-report-scroll', async () => {
      return page.evaluate(async () => {
        const pane = document.querySelector('.explore-scroll');
        const start = performance.now();
        const times = [];
        await new Promise(resolve => {
          function move(at) {
            times.push(at);
            const progress = Math.min(1, (performance.now() - start) / 2200);
            pane.scrollTop = (pane.scrollHeight - pane.clientHeight) * progress;
            if (progress < 1) requestAnimationFrame(move); else resolve();
          }
          requestAnimationFrame(move);
        });
        const intervals = times.slice(1).map((value, index) => value - times[index]).sort((a, b) => a - b);
        const percentile = fraction => Number(intervals[Math.floor((intervals.length - 1) * fraction)].toFixed(2));
        return { raf: { frames: times.length, medianIntervalMs: percentile(.5), p95IntervalMs: percentile(.95) } };
      });
    });
    result.metrics = { idleAmbient: idle, reportScroll: interaction,
      interpretation: 'Observed Chrome trace counts and frame intervals on this fixture run; not a universal FPS claim.' };
    assert.ok(idle.bytes > 1000 && interaction.bytes > 1000);
    return { idleLayout: idle.layoutEvents, scrollLayout: interaction.layoutEvents,
      idleLongTasks: idle.longMainTasksOver50ms, scrollLongTasks: interaction.longMainTasksOver50ms };
  });

  await check('running stage and cancel remain visible at 1000px and 390px widths', async () => {
    const page = await interactionContext.newPage();
    page.setDefaultTimeout(12000);
    page.on('pageerror', error => result.pageErrors.push(String(error)));
    await page.goto(new URL('#explore', base).href);
    await scene(page, 'entry');
    await tid(page, 'explore-input').fill('请分析测试甲集团拟收购测试乙公司的审批风险。[WAIT_EXPLORE]');
    await tid(page, 'explore-input').focus();
    await page.keyboard.press('Enter');
    await scene(page, 'research');
    await page.waitForFunction(() =>
      document.querySelector('.explore-progress h3')?.textContent?.includes('正在调用模型分析'),
    null, { timeout: 15000 });
    const sizes = [];
    for (const viewport of [{ width: 1000, height: 790 }, { width: 390, height: 844 }]) {
      await page.setViewportSize(viewport);
      await page.waitForTimeout(180);
      const positions = await page.evaluate(() => {
        const stage = document.querySelector('.explore-progress h3')?.getBoundingClientRect();
        const cancel = document.querySelector('[data-testid="explore-cancel"]')?.getBoundingClientRect();
        const composer = document.querySelector('.explore-compose-zone')?.getBoundingClientRect();
        return {
          stage: stage && { top: stage.top, bottom: stage.bottom },
          cancel: cancel && { top: cancel.top, bottom: cancel.bottom },
          composerTop: composer?.top,
          documentWidth: document.documentElement.scrollWidth,
          viewportWidth: document.documentElement.clientWidth,
        };
      });
      assert.ok(positions.stage?.top >= 0 && positions.stage.bottom < positions.composerTop,
        `Stage hidden behind composer at ${viewport.width}px: ${JSON.stringify(positions)}`);
      assert.ok(positions.cancel?.top >= 0 && positions.cancel.bottom < positions.composerTop,
        `Cancel hidden behind composer at ${viewport.width}px: ${JSON.stringify(positions)}`);
      assert.ok(positions.documentWidth <= positions.viewportWidth + 1,
        `Horizontal overflow at ${viewport.width}px: ${JSON.stringify(positions)}`);
      sizes.push({ ...viewport, ...positions });
      await screenshot(page, viewport.width === 1000 ? '11-research-1000x790' : '12-research-390x844');
    }
    await tid(page, 'explore-cancel').click();
    await tid(page, 'explore-retry').waitFor({ state: 'visible', timeout: 12000 });
    await page.close();
    return { sizes, cancelledAfterScreenshots: true };
  });

  await check('regenerating from a long report opens the new run at the top without scroll jumps on poll', async () => {
    const page = interactionContext.pages()[0];
    await page.setViewportSize({ width: 1440, height: 900 });
    if (!(await tid(page, 'explore-workspace').evaluate(node => node.classList.contains('history-pinned')))) {
      await tid(page, 'explore-history-toggle').click();
    }
    const reader = page.locator('.explore-scroll');
    await reader.evaluate(node => node.scrollTo({ top: node.scrollHeight - node.clientHeight, behavior: 'instant' }));
    const bottom = await reader.evaluate(node => node.scrollTop);
    assert.ok(bottom >= 200, 'The source report must begin at an actual lower reading position.');
    const sessions = tid(page, 'explore-session-list').locator('.explore-session-item');
    await sessions.filter({ hasText: '[STREAM_EXPLORE]' }).first().click();
    await scene(page, 'report');
    await sessions.filter({ hasText: '[WAIT_EXPLORE]' }).first().click();
    await scene(page, 'report');
    await page.waitForFunction(expected => Math.abs(document.querySelector('.explore-scroll')?.scrollTop - expected) < 5,
      bottom, { timeout: 5000 });
    const restoredAcrossSessions = await reader.evaluate(node => node.scrollTop);
    const previous = new URL(page.url()).hash;
    await tid(page, 'explore-regenerate').click();
    await page.waitForFunction(oldHash => location.hash !== oldHash, previous, { timeout: 10000 });
    await scene(page, 'research');
    await page.waitForFunction(() => document.querySelector('.explore-scroll')?.scrollTop < 2,
      null, { timeout: 5000 });
    const top = await reader.evaluate(node => node.scrollTop);
    const position = await page.evaluate(() => {
      const stage = document.querySelector('.explore-progress h3')?.getBoundingClientRect();
      const cancel = document.querySelector('[data-testid="explore-cancel"]')?.getBoundingClientRect();
      const composer = document.querySelector('.explore-compose-zone')?.getBoundingClientRect();
      return { stage: stage && { top: stage.top, bottom: stage.bottom },
        cancel: cancel && { top: cancel.top, bottom: cancel.bottom }, composerTop: composer?.top };
    });
    assert.ok(position.stage?.top >= 0 && position.stage.bottom < position.composerTop,
      `Regenerated run stage must be visible: ${JSON.stringify(position)}`);
    assert.ok(position.cancel?.top >= 0 && position.cancel.bottom < position.composerTop,
      `Regenerated run cancel must be visible: ${JSON.stringify(position)}`);
    await page.waitForFunction(() =>
      document.querySelector('.explore-progress h3')?.textContent?.includes('正在调用模型分析'),
    null, { timeout: 15000 });
    const maxScroll = await reader.evaluate(node => node.scrollHeight - node.clientHeight);
    if (maxScroll >= 120) {
      await reader.evaluate(node => node.scrollTo({ top: 90, behavior: 'instant' }));
      const held = await reader.evaluate(node => node.scrollTop);
      await page.waitForTimeout(2200);
      const afterPoll = await reader.evaluate(node => node.scrollTop);
      assert.ok(Math.abs(afterPoll - held) < 4,
        `Polling the same run must preserve reading scroll: ${held} → ${afterPoll}`);
    } else {
      result.limitations.push('The regenerated in-progress document was too short to test a nonzero scroll during polling.');
    }
    const historyRuns = tid(page, 'explore-run-list').locator('.explore-run-item');
    assert.ok(await historyRuns.count() >= 2);
    await historyRuns.first().click();
    await scene(page, 'report');
    await page.waitForFunction(expected => Math.abs(document.querySelector('.explore-scroll')?.scrollTop - expected) < 5,
      bottom, { timeout: 5000 });
    const restoredSourceScroll = await reader.evaluate(node => node.scrollTop);
    await historyRuns.last().click();
    await scene(page, 'research');
    await tid(page, 'explore-cancel').click();
    await tid(page, 'explore-retry').waitFor({ state: 'visible', timeout: 12000 });
    return { priorScrollPx: bottom, restoredAcrossSessionsPx: restoredAcrossSessions,
      restoredSourceScrollPx: restoredSourceScroll, initialNewRunScrollPx: top, stageAndCancelVisible: true,
      sameRunNonzeroScrollTested: maxScroll >= 120 };
  });

  assert.deepEqual(result.pageErrors, [], 'The browser must have no uncaught page errors.');
  result.passed = true;
} catch (error) {
  result.passed = false;
  result.failedStep = currentStep;
  result.error = String(error?.stack || error);
  console.error(result.error);
  const page = interactionContext?.pages()?.[0] || primaryPage;
  if (page && !page.isClosed()) {
    try { await screenshot(page, 'failure'); } catch { /* best effort */ }
  }
  process.exitCode = 1;
} finally {
  try { await interactionContext?.close(); } catch { /* best effort */ }
  try { await videoContext?.close(); } catch { /* best effort */ }
  try { await browser?.close(); } catch { /* best effort */ }
  result.finishedAt = new Date().toISOString();
  await writeFile(path.join(output, 'result.json'), JSON.stringify(result, null, 2), 'utf8');
  console.log(JSON.stringify({ passed: result.passed, checks: result.checks.length,
    failedStep: result.failedStep || null, output, video: result.video?.mp4 || result.video?.webm || null }));
}
