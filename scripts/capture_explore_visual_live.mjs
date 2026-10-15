/** Actual app + actual public sources/model. No provider or report replacement. */
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import { writeFile, mkdir, rename } from 'node:fs/promises';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
const require = createRequire(import.meta.url);
const dir = process.env.PLAYWRIGHT_MODULE_DIR;
const { chromium } = dir ? require(existsSync(path.join(dir, 'playwright/package.json')) ? path.join(dir, 'playwright') : dir) : require('playwright');
const exec = promisify(execFile);
let raw = '';
for await (const chunk of process.stdin) raw += chunk;
const { origin, cookies, output } = JSON.parse(raw);
assert.equal(new URL(origin).hostname, '127.0.0.1');
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chrome', headless: true });
let context;
let video;
const result = { realNews: true, realWebSearch: true, realModel: true, temporaryAccount: true, stages: [], progressSamples: [], screenshots: [], errors: [], ok: false };
const save = async (name, value) => writeFile(path.join(output, name), JSON.stringify(value, null, 2));
const shot = async (page, name) => { const filename = path.join(output, name + '.png'); await page.screenshot({ path: filename, animations: 'allow' }); result.screenshots.push(filename); };
let start;
try {
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, recordVideo: { dir: output, size: { width: 1440, height: 1000 } } });
  await context.addCookies(cookies);
  await context.route('**/*', route => {
    const u = new URL(route.request().url());
    return u.origin === origin || ['data:', 'blob:'].includes(u.protocol) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  start = Date.now();
  video = page.video();
  page.on('pageerror', error => result.errors.push(String(error)));
  await page.goto(origin + '/desktop/#explore');
  await page.getByTestId('explore-input').waitFor();
  await page.evaluate(() => { window.__liveAmbient = document.querySelector('[data-testid="explore-ambient"]'); window.__liveInput = document.querySelector('[data-testid="explore-input"]'); });
  await shot(page, '01-entry-desktop');
  await page.waitForTimeout(4000);
  const question = '请分析近期半导体出口管制对中国芯片企业供应链的影响路径，区分已确认事实、条件推断与不确定性。请依据可检索到的公开材料，不预测股价。';
  await page.getByTestId('explore-input').fill(question);
  result.question = question;
  result.submittedAtSeconds = (Date.now() - start) / 1000;
  await page.getByTestId('explore-submit').click();
  await page.locator('[data-testid="explore-workspace"][data-scene="research"]').waitFor();
  await shot(page, '02-research-desktop');
  const deadline = Date.now() + 480000;
  let run;
  let lastStage;
  let chosen = false;
  let progressCaptured = false;
  let materialsCaptured = false;
  while (Date.now() < deadline) {
    const id = new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run');
    if (id) {
      const response = await context.request.get(origin + '/desktop/api/explore/runs/' + encodeURIComponent(id));
      assert.equal(response.status(), 200);
      run = (await response.json()).run;
      if (run.model_progress) result.progressSamples.push({ ...run.model_progress, stage: run.stage });
      if (run.stage !== lastStage) {
        lastStage = run.stage;
        const stage = { stage: run.stage, status: run.status, elapsedSeconds: (Date.now() - start) / 1000 };
        result.stages.push(stage);
        console.log(JSON.stringify(stage));
      }
      if (!materialsCaptured && run.status === 'running' && run.materials?.length) {
        await page.waitForFunction(() => Number(document.querySelector('[data-testid="explore-files-float"] strong')?.textContent) > 0);
        await shot(page, '02c-materials-while-running');
        materialsCaptured = true;
        result.materialsVisibleBeforeReport = true;
      }
      if (!progressCaptured && run.stage === 'analyzing' && run.model_progress?.task === 'report' && run.model_progress.output_chars > 0) {
        await page.waitForFunction(() => document.querySelector('[data-testid="explore-progress"]')?.textContent.includes('已接收'));
        await shot(page, '02d-model-output-progress');
        progressCaptured = true;
      }
      if (run.status === 'awaiting_clarification' && !chosen) {
        await shot(page, '02b-clarification-desktop');
        await page.getByTestId('explore-review-option').first().waitFor();
        result.selectedClarification = run.review.options[0];
        await page.getByTestId('explore-review-option').first().click();
        chosen = true;
      }
      if (['succeeded', 'partial'].includes(run.status)) break;
      if (['failed', 'cancelled', 'interrupted', 'awaiting_input', 'awaiting_review'].includes(run.status)) throw new Error('Live run stopped at ' + run.status);
    }
    await page.waitForTimeout(1200);
  }
  assert.ok(run?.report, 'No report within the live deadline');
  await page.getByTestId('explore-report').waitFor({ timeout: 20000 });
  await page.waitForTimeout(1000);
  result.reportAtSeconds = (Date.now() - start) / 1000;
  result.status = run.status;
  result.sources = run.report.sources;
  result.retrieval = run.report.retrieval || run.retrieval;
  assert.ok(run.report.sources.some(source => source.kind === 'web_search'), 'No real web search evidence');
  result.sameNodes = await page.evaluate(() => ({
    ambient: window.__liveAmbient === document.querySelector('[data-testid="explore-ambient"]'),
    input: window.__liveInput === document.querySelector('[data-testid="explore-input"]'),
  }));
  assert.deepEqual(result.sameNodes, { ambient: true, input: true });
  await save('saved-run.json', run);
  await shot(page, '03-report-desktop');
  await page.waitForTimeout(5000);
  await page.getByTestId('explore-open-materials').first().click();
  await shot(page, '04-materials-desktop');
  await page.waitForTimeout(2500);
  await page.getByTestId('explore-materials-close').click();
  await page.getByTestId('explore-files-open').click();
  await shot(page, '05-pool-desktop');
  await page.getByTestId('explore-files-close').click();
  const suggestions = page.locator('.explore-recommendations');
  if (await suggestions.count()) {
    await suggestions.scrollIntoViewIfNeeded();
    await shot(page, '05b-next-research');
    await page.locator('#explore-scroll').evaluate(node => { node.scrollTop = 0; });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  if ((await page.getByTestId('explore-history-drawer').getAttribute('class'))?.includes('open')) {
    await page.locator('.explore-history-close').click();
  }
  await page.waitForFunction(() => {
    const drawer = document.querySelector('[data-testid="explore-history-drawer"]');
    return drawer && drawer.getBoundingClientRect().right <= 0;
  });
  await page.locator('#explore-scroll').evaluate(node => { node.scrollTop = 0; });
  await shot(page, '06-report-mobile');
  result.ok = result.errors.length === 0;
} catch (error) {
  result.failure = String(error);
  process.exitCode = 1;
} finally {
  if (context) await context.close();
  await browser.close();
  if (video) {
    const filename = path.join(output, 'actual-live-full.webm');
    await rename(await video.path(), filename);
    result.fullVideo = filename;
    if (result.reportAtSeconds) {
      // Preserve actual entry->research and research->report transitions at 1x;
      // remove only the long middle wait. Full uncut recording remains available.
      const reportStart = Math.max(19, result.reportAtSeconds - 4);
      const short = path.join(output, 'actual-live-three-states.mp4');
      await exec('ffmpeg', ['-y', '-i', filename, '-filter_complex',
        `[0:v]trim=start=0:end=18,setpts=PTS-STARTPTS[a];[0:v]trim=start=${reportStart}:end=${reportStart + 14},setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1:a=0[v]`,
        '-map', '[v]', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', short], { maxBuffer: 2000000 });
      result.shortVideo = short;
      result.shortVideoNote = 'Actual live UI, 1x playback. Middle model wait removed at 18s; full uncut WebM retained. No prototype or substituted business output.';
    }
  }
  await save('result.json', result);
  console.log(JSON.stringify({ ok: result.ok, status: result.status, failure: result.failure, output, video: result.shortVideo }));
}
