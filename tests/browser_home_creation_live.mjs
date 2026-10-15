import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:49025';
const out = process.env.HOME_CREATION_BROWSER_OUTPUT || `.run/home-creation-live-${Date.now()}`;
await mkdir(out, { recursive: true, mode: 0o700 });
execFileSync('.venv/bin/python', ['scripts/verify_home_creation_live.py', '--emit-cases', out + '/cases.json']);
const manifest = JSON.parse(await readFile(out + '/cases.json', 'utf8'));
const fixture = await (await fetch(base + '/__home_creation_fixture__/state')).json();
assert.equal(fixture.realModel, true);
assert.equal(fixture.model, 'GLM-5.3-Flash');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const report = { realModel: true, model: fixture.model, site: base + '/desktop/',
  attempts: [], startedAt: new Date().toISOString(), status: 'running', evidenceDir: out };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const save = async (path, value) => writeFile(path, JSON.stringify(value, null, 2) + '\n');

async function request(context, method, path, body) {
  const options = { method };
  if (body !== undefined) {
    options.data = body;
    options.headers = { 'Idempotency-Key': `home-create-${crypto.randomUUID()}` };
  }
  const response = await context.request.fetch(base + '/desktop/api' + path, options);
  const value = await response.json().catch(() => null);
  if (!response.ok()) throw Error(`${method} ${path}: HTTP ${response.status()} ${JSON.stringify(value)?.slice(0, 1200)}`);
  return value;
}

async function waitSession(context, id, kind) {
  const deadline = Date.now() + 600000;
  let last = '';
  while (Date.now() < deadline) {
    const value = await request(context, 'GET', '/authoring-sessions/' + encodeURIComponent(id));
    const phase = value.session?.phase;
    if (phase !== last) { console.log(`${kind} creation: ${phase}`); last = phase; }
    if (phase === 'completed') return value;
    if (!['preparing', 'checking', 'validating', 'repairing', 'committing'].includes(phase)) {
      await save(out + '/' + kind + '/session-stopped.json', value);
      throw Error(`${kind} creation stopped in ${phase}`);
    }
    await sleep(1000);
  }
  throw Error(`${kind} creation exceeded 600 seconds`);
}

async function waitRun(context, id, kind, label) {
  const deadline = Date.now() + 180000;
  let last = '';
  while (Date.now() < deadline) {
    const run = await request(context, 'GET', '/asset-apps/runs/' + encodeURIComponent(id));
    if (run.status !== last) { console.log(`${kind} ${label}: ${run.status}`); last = run.status; }
    if (!['queued', 'running'].includes(run.status)) return run;
    await sleep(500);
  }
  throw Error(`${kind} ${label} run exceeded 180 seconds`);
}

async function waitVerification(context, id, versionId, kind) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const detail = await request(context, 'GET', '/asset-apps/' + encodeURIComponent(id) + '?versionId=' + encodeURIComponent(versionId));
    if (detail.verification?.status !== 'running') return detail;
    await sleep(500);
  }
  throw Error(`${kind} verification status did not settle`);
}

async function run(kind, email) {
  const folder = out + '/' + kind;
  await mkdir(folder, { recursive: true, mode: 0o700 });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  page.setDefaultTimeout(30000);
  const pageErrors = [], browserWrites = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('request', req => {
    if (req.method() !== 'GET' && req.url().includes('/desktop/api/'))
      browserWrites.push({ method: req.method(), path: new URL(req.url()).pathname });
  });
  const attempt = { kind, status: 'running', promptSha256: manifest[kind].promptSha256,
    browserWrites, pageErrors };
  try {
    const writesBeforeAttempt = (await (await context.request.get(base + '/__home_creation_fixture__/state')).json()).writes.length;
    await page.goto(base + '/desktop/');
    await page.locator('input[name=email]').fill(email);
    await page.locator('input[name=password]').fill(fixture.fixturePassword);
    await page.locator('form button[type=submit]').click();
    const input = page.locator('#composer-input');
    await input.waitFor();
    const toggle = page.getByRole('button', { name: '创建模式', exact: true });
    await toggle.waitFor();
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-pressed'), 'true');
    await input.fill(manifest[kind].prompt);
    await page.screenshot({ path: folder + '/home-create-selected.png' });

    const pending = page.waitForResponse(response =>
      response.url().endsWith('/desktop/api/authoring-sessions') && response.request().method() === 'POST',
      { timeout: 60000 });
    await page.getByRole('button', { name: '创建并保存 APP', exact: true }).click();
    const response = await pending;
    assert.equal(response.status(), 201, await response.text());
    const requestBody = response.request().postDataJSON();
    assert.deepEqual({ kind: requestBody.kind, intent: requestBody.intent, mode: requestBody.mode,
      instruction: requestBody.instruction, sourceRefs: requestBody.sourceRefs || [] },
      { kind: 'app', intent: 'create', mode: 'prepare', instruction: manifest[kind].prompt, sourceRefs: [] });
    await save(folder + '/browser-creation-request.json', requestBody);
    const created = await response.json();
    const sessionId = created.session?.id;
    assert.ok(sessionId);
    attempt.sessionId = sessionId;
    await save(folder + '/session-start.json', created);
    const completed = await waitSession(context, sessionId, kind);
    await save(folder + '/session-completed.json', completed);
    const appId = completed.session?.result?.assetId;
    const versionId = completed.session?.result?.versionId;
    assert.ok(appId && versionId, 'creation did not save a version');
    attempt.appId = appId;
    attempt.versionId = versionId;
    const card = page.locator('.apps-library-card[data-app-id="' + appId + '"]');
    await card.waitFor({ timeout: 45000 });
    assert.match(await card.innerText(), /刚保存|已拥有/);
    await page.screenshot({ path: folder + '/saved-in-my-apps.png', fullPage: true });

    const detail = await request(context, 'GET', '/asset-apps/' + encodeURIComponent(appId) + '?versionId=' + encodeURIComponent(versionId));
    await save(folder + '/app-initial.json', detail);
    const traces = [];
    for (const id of detail.authoringProvenance?.modelTraceIds || []) {
      const trace = await request(context, 'GET', '/authoring-sessions/' + encodeURIComponent(sessionId) + '/model-traces/' + encodeURIComponent(id));
      traces.push(Object.fromEntries(['id', 'provider', 'model', 'requestId', 'createdAt', 'updatedAt'].map(key => [key, trace[key]])));
    }
    await save(folder + '/model-trace-summary.json', traces);
    assert.equal(detail.verification?.status, 'unverified');
    assert.equal(detail.enabledVersionId, null);
    const runsBefore = await request(context, 'GET', '/asset-apps/' + encodeURIComponent(appId) + '/runs');
    await save(folder + '/runs-before.json', runsBefore);
    assert.equal(runsBefore.items?.length, 0);
    assert.ok((detail.testCases || []).length >= 1, 'saved original case absent');
    assert.ok(detail.testCases[0].expectation, 'saved original independent expectation absent');

    const creationWrites = (await (await context.request.get(base + '/__home_creation_fixture__/state')).json()).writes.slice(writesBeforeAttempt);
    await save(folder + '/writes-before-verification.json', creationWrites);
    assert.equal(creationWrites.filter(item => /\/asset-apps\/[^/]+\/(verify|runs|enable)$/.test(item.path)).length, 0,
      'creation prematurely verified, enabled, or ran an App');
    assert.equal(browserWrites.filter(item => /\/(sub-agents|chat)(\/|$)/.test(item.path)).length, 0,
      'create mode submitted an ordinary chat task');

    // Current lifecycle: only the original example is required for verification.
    const original = manifest[kind].original;
    const verifying = await request(context, 'POST', '/asset-apps/' + encodeURIComponent(appId) + '/verify',
      { versionId, cases: [original] });
    assert.equal(verifying.verification?.runIds?.length, 1);
    const originalRun = await waitRun(context, verifying.verification.runIds[0], kind, 'original');
    await save(folder + '/run-original.json', originalRun);
    const verified = await waitVerification(context, appId, versionId, kind);
    await save(folder + '/app-verified.json', verified);
    assert.equal(verified.verification?.status, 'passed');
    const enabled = await request(context, 'POST', '/asset-apps/' + encodeURIComponent(appId) + '/enable',
      { versionId, enabled: true, revision: verified.revision });
    assert.equal(enabled.enabledVersionId, versionId);
    await save(folder + '/app-enabled.json', enabled);
    for (const label of ['additional', 'boundary']) {
      const testCase = manifest[kind][label];
      const started = await request(context, 'POST', '/asset-apps/' + encodeURIComponent(appId) + '/runs',
        { versionId, input: testCase.input });
      assert.ok(started.id);
      const result = await waitRun(context, started.id, kind, label);
      await save(folder + '/run-' + label + '.json', result);
    }
    attempt.status = 'passed';
    attempt.initialVerification = 'unverified';
    attempt.initialRunCount = 0;
    attempt.verificationRuns = 1;
    attempt.ordinaryRuns = 2;
    assert.deepEqual(pageErrors, [], 'browser JavaScript errors');
    return attempt;
  } catch (error) {
    attempt.status = 'failed';
    attempt.failure = String(error?.stack || error);
    await page.screenshot({ path: folder + '/failure.png', fullPage: true }).catch(() => {});
    await save(folder + '/failure.json', { error: attempt.failure, browserWrites, pageErrors });
    return attempt;
  } finally {
    await context.close();
  }
}

try {
  report.attempts.push(await run('holdings', fixture.accounts[0]));
  await save(out + '/report.json', report);
  report.attempts.push(await run('orders', fixture.accounts[1]));
  report.completedAt = new Date().toISOString();
  report.status = report.attempts.every(item => item.status === 'passed') ? 'passed' : 'failed';
  await save(out + '/report.json', report);
  if (report.status !== 'passed') throw Error('At least one home creation attempt failed');
  execFileSync('.venv/bin/python', ['scripts/verify_home_creation_live.py', '--check-evidence', out], { stdio: 'inherit' });
  await save(out + '/summary.json', {
    status: 'passed', mode: 'home-composer-create', realModel: 'GiteeProvider/GLM-5.3-Flash',
    attempts: await Promise.all(report.attempts.map(async item => {
      const folder = out + '/' + item.kind;
      const initial = JSON.parse(await readFile(folder + '/app-initial.json', 'utf8'));
      const enabled = JSON.parse(await readFile(folder + '/app-enabled.json', 'utf8'));
      return { kind: item.kind, promptSha256: item.promptSha256, appName: initial.name,
        appId: initial.id, versionId: initial.versionId,
        initialVerification: initial.verification.status, initialEnabled: initial.enabledVersionId,
        initialRunCount: JSON.parse(await readFile(folder + '/runs-before.json', 'utf8')).items.length,
        savedOriginalCase: { label: initial.testCases[0].label, expectationMode: initial.testCases[0].expectation.mode },
        modelTraces: JSON.parse(await readFile(folder + '/model-trace-summary.json', 'utf8')).length,
        verificationRuns: 1, ordinaryRuns: 2, enabledVersionId: enabled.enabledVersionId,
        browserPageErrors: item.pageErrors };
    })),
  });
  console.log('PASS home creation live evidence: ' + out);
} finally {
  await browser.close();
}
