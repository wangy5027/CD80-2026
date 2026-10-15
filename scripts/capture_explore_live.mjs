/** Capture a real persisted Explore report. Receives disposable cookies via stdin only. */
import { createRequire } from 'node:module';
import { existsSync } from 'node:fs';
import path from 'node:path';
const require = createRequire(import.meta.url);
const moduleDir = process.env.PLAYWRIGHT_MODULE_DIR;
const playwright = moduleDir
  ? require(existsSync(path.join(moduleDir, 'playwright/package.json')) ? path.join(moduleDir, 'playwright') : moduleDir)
  : require('playwright');
let input = '';
for await (const chunk of process.stdin) input += chunk;
const { origin, session_id: session, run_id: run, cookies, output, phase = 'report', materials = false, pool = false } = JSON.parse(input);
const url = new URL(origin);
if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)) throw new Error('Only local temporary instances are supported.');
const browser = await playwright.chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'chrome', headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  await context.addCookies(cookies);
  await context.route('**/*', route => {
    const requestUrl = new URL(route.request().url());
    return requestUrl.origin === url.origin || ['data:', 'blob:'].includes(requestUrl.protocol) ? route.continue() : route.abort();
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`${origin}/desktop/#explore?session=${encodeURIComponent(session)}&run=${encodeURIComponent(run)}`);
  if (phase === 'clarification') await page.getByTestId('explore-clarification').waitFor({ timeout: 20000 });
  else if (phase === 'review') await page.getByTestId('explore-review').waitFor({ timeout: 20000 });
  else await page.getByText('核心发现', { exact: true }).first().waitFor({ timeout: 20000 });
  await page.screenshot({ path: output, fullPage: true, animations: 'disabled' });
  let directionsScreenshot = null;
  const firstDirection = page.getByTestId('explore-review-option').first();
  if (phase === 'clarification' && await firstDirection.count()) {
    await firstDirection.scrollIntoViewIfNeeded();
    directionsScreenshot = output.replace(/\.png$/i, '-directions.png');
    await page.screenshot({ path: directionsScreenshot, fullPage: true, animations: 'disabled' });
  }
  const retrieval = page.getByTestId('explore-retrieval');
  let retrievalScreenshot = null;
  if (await retrieval.count()) {
    retrievalScreenshot = output.replace(/\.png$/i, '-search.png');
    await retrieval.screenshot({ path: retrievalScreenshot, animations: 'disabled' });
  }
  let materialsScreenshot = null;
  if (materials) {
    await page.getByTestId('explore-open-materials').first().click();
    await page.getByTestId('explore-materials').waitFor();
    materialsScreenshot = output.replace(/\.png$/i, '-materials.png');
    await page.screenshot({ path: materialsScreenshot, fullPage: true, animations: 'disabled' });
  }
  let poolScreenshot = null;
  if (pool) {
    if (materials) await page.getByTestId('explore-materials-close').click();
    await page.getByTestId('explore-files-open').first().click();
    await page.getByTestId('explore-files-dialog').waitFor();
    const viewFile = page.getByTestId('explore-file-view').first();
    if (await viewFile.count()) {
      await viewFile.click();
      await page.getByTestId('explore-file-download').waitFor();
    }
    poolScreenshot = output.replace(/\.png$/i, '-pool.png');
    await page.screenshot({ path: poolScreenshot, fullPage: true, animations: 'disabled' });
  }
  if (errors.length) throw new Error('Live report UI encountered a browser error.');
  console.log(JSON.stringify({ actual_ui_screenshot: output, phase, directions_screenshot: directionsScreenshot, retrieval_screenshot: retrievalScreenshot, materials_screenshot: materialsScreenshot, pool_screenshot: poolScreenshot, browser_errors: 0 }));
} finally {
  await browser.close();
}
