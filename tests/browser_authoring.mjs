// Isolated real-DOM acceptance. Fixture models are synthetic; candidate execution is real WASI.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:' + (process.env.AUTHORING_BROWSER_PORT || '49010');
const out = process.env.AUTHORING_BROWSER_OUTPUT || 'data/authoring-browser';
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(20000);
const errors = [], external = [], mutations = [], evidence = [];
page.on('pageerror', cause => errors.push(cause.message));
page.on('request', request => {
  if (request.method() === 'GET') return;
  const path = new URL(request.url()).pathname;
  let body; try { body = path.endsWith('/login') ? { account: 'isolated-fixture' } : request.postDataJSON(); } catch { body = { multipart: true }; }
  mutations.push({ path, body });
});
await page.route('**/*', route => {
  const url = route.request().url();
  if (url.startsWith(base + '/') || /^(data:|blob:)/.test(url)) return route.continue();
  external.push(url); return route.abort();
});
const detail = () => page.getByRole('dialog').filter({ has: page.locator('#author-detail-title') });
const start = () => page.getByRole('dialog').filter({ has: page.locator('#author-start-title') });
async function get(path) { const response = await page.request.get(base + '/desktop/api' + path); const value = await response.json(); assert.ok(response.ok(), JSON.stringify(value)); return value; }
async function nav(name) { if (await page.getByRole('button', { name: '打开导航', exact: true }).isVisible()) await page.getByRole('button', { name: '打开导航', exact: true }).click(); await page.locator('.nav-group').getByRole('button', { name, exact: true }).click(); }
async function library(kind) {
  await nav(kind === 'app' ? '应用' : '扩展');
  if (kind === 'app') await page.locator('.asset-authoring').waitFor();
  else { await page.locator('.cap-library').waitFor(); const back = page.getByRole('button', { name: '← 返回能力列表', exact: true }); if (await back.isVisible()) await back.click(); await page.getByRole('tab', { name: kind === 'tool' ? /^Tools/ : /^Skills/ }).click(); }
}
const authoringPost = suffix => page.waitForResponse(response => new URL(response.url()).pathname === '/desktop/api/authoring-sessions' + suffix && response.request().method() === 'POST');
async function prepared(id, previous) {
  // A visible old review is not proof that a queued chat edit has been prepared.
  let value;
  const deadline = Date.now() + 60000;
  while (true) {
    value = await get('/authoring-sessions/' + id);
    if (value.session.phase === 'awaiting_review' && value.candidate?.id === value.session.currentCandidateId
      && value.reviewSnapshot?.candidateId === value.candidate.id
      && (!previous || (value.session.revision > previous.revision && value.candidate.id !== previous.candidateId))) break;
    assert.ok(Date.now() < deadline, JSON.stringify(value.session));
    await new Promise(resolve => setTimeout(resolve, 200));
  }
  const current = detail().filter({ has: page.locator('#author-detail-title') });
  await page.waitForFunction(({ id, revision, candidateId }) => {
    const panel = document.querySelector('.author-panel');
    return panel?.dataset.authoringSessionId === id && panel.dataset.authoringRevision === String(revision)
      && panel.dataset.authoringCandidateId === candidateId
      && [...panel.querySelectorAll('button')].some(button => button.textContent === '确认保存并使用' && !button.disabled);
  }, { id, revision: value.session.revision, candidateId: value.candidate.id }, { timeout: 15000 });
  assert.equal(await current.getAttribute('data-authoring-revision'), String(value.session.revision));
  assert.equal(await current.getByRole('button', { name: '确认保存并使用', exact: true }).isEnabled(), true);
  assert.equal(value.session.phase, 'awaiting_review', JSON.stringify(value.session));
  assert.equal(value.reviewSnapshot.candidateId, value.candidate.id);
  assert.ok(value.evidence.some(row => row.candidateId === value.candidate.id && row.caseResults?.length >= 2));
  return value;
}
async function startCreate(kind) {
  await library(kind);
  await page.getByRole('button', { name: kind === 'app' ? '新建应用' : '新建草稿', exact: true }).click();
  await start().getByLabel('希望它完成什么', { exact: true }).fill('创建 ' + kind + ' 浏览器合成价格方法，保留固定业务规则。');
  const response = authoringPost(''); await start().getByRole('button', { name: '开始整理与验证', exact: true }).click();
  const created = await (await response).json(); assert.ok(created.session?.id, JSON.stringify(created));
  return created.session.id;
}
try {
  const fixtureResponse = await page.request.get(base + '/__fixture__/state'), fixture = await fixtureResponse.json();
  assert.equal(fixture.synthetic, true); assert.equal(fixture.realModel, false); assert.equal(fixture.realWasi, true);
  await page.goto(base + '/desktop/');
  await page.locator('input[name=email]').fill('authoring-browser@example.invalid');
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click(); await page.locator('#composer-input').waitFor();
  for (const kind of ['app','tool','skill']) {
    const id = await startCreate(kind), before = await prepared(id);
    assert.equal(before.session.kind, kind); assert.equal(before.commit, null);
    await detail().getByLabel('继续调整这份草稿', { exact: true }).fill('浏览器调整：名称与说明更清楚，保留全部原业务规则。');
    const revisedResponse = authoringPost('/' + id + '/messages');
    await detail().getByRole('button', { name: '提交修改要求', exact: true }).click();
    const revised = await (await revisedResponse).json(); assert.equal(revised.session.id, id);
    const ready = await prepared(id, { revision: before.session.revision, candidateId: before.candidate.id });
    assert.notEqual(ready.candidate.id, before.candidate.id); assert.equal(ready.session.budgetRoundId, before.session.budgetRoundId);
    const confirmResponse = authoringPost('/' + id + '/confirm');
    await detail().getByRole('button', { name: '确认保存并使用', exact: true }).click();
    const saved = await (await confirmResponse).json(); assert.equal(saved.commit.stage, 'completed'); assert.ok(saved.commit.targetAssetId);
    const body = mutations.filter(item => item.path.endsWith('/' + id + '/confirm')).at(-1).body;
    assert.equal(body.candidateId, ready.candidate.id); assert.equal(body.reviewDigest, ready.reviewSnapshot.reviewDigest);
    await detail().getByRole('heading', { name: '已保存并可使用', exact: true }).waitFor();
    await page.screenshot({ path: out + '/' + kind + '-review-and-saved.png', fullPage: true });
    const target = saved.session.result;
    if (kind === 'app') { const asset = await get('/asset-apps/' + target.assetId); assert.equal(asset.enabledVersionId, target.versionId); }
    else { const asset = await get('/capabilities/assets/' + target.assetId); assert.equal(asset.enabled, true); assert.equal(asset.activeVersionId, target.versionId); }
    evidence.push({ case: kind + ' create, continuous edit and exact final commit', sessionId: id, oldCandidateId: before.candidate.id, candidateId: ready.candidate.id, assetId: target.assetId, versionId: target.versionId });
    await detail().getByRole('button', { name: '关闭创作详情', exact: true }).click();
  }
  await nav('新任务');
  await page.locator('#composer-input').fill('创建一个 Tool，用合成价格计算固定指标。');
  await page.locator('#composer-input').press('Enter');
  const chatCard = page.locator('.conversation [data-authoring-session-id]').first(); await chatCard.waitFor({ timeout: 60000 });
  const chatId = await chatCard.getAttribute('data-authoring-session-id');
  await chatCard.getByRole('button').click(); const chatBefore = await prepared(chatId);
  await detail().getByRole('button', { name: '在对话中继续修改', exact: true }).click();
  assert.ok(await page.locator('.author-chat-context').isVisible());
  await page.locator('#composer-input').fill('浏览器调整：继续完善这份草稿，保持原规则。'); await page.locator('#composer-input').press('Enter');
  await page.waitForFunction(() => document.querySelector('#composer-input')?.value === '');
  const chatRequest = mutations.filter(item => /\/conversations\/[^/]+\/messages$/.test(item.path)).at(-1);
  assert.equal(chatRequest.body.authoringSessionId, chatId);
  await chatCard.getByRole('button').click();
  const chatReady = await prepared(chatId, { revision: chatBefore.session.revision, candidateId: chatBefore.candidate.id });
  assert.equal(chatReady.session.budgetRoundId, chatBefore.session.budgetRoundId);
  await detail().getByRole('button', { name: '只存草稿', exact: true }).click();
  await detail().getByRole('heading', { name: '草稿已保留', exact: true }).waitFor();
  const draft = await get('/authoring-sessions/' + chatId); assert.equal(draft.commit.mode, 'draft_only'); assert.ok(!draft.commit.targetAssetId);
  await detail().getByRole('button', { name: '关闭创作详情', exact: true }).click();
  evidence.push({ case: 'chat card and explicit same-session continuation, draft-only retains without formal version', sessionId: chatId });
  for (const kind of ['app','tool','skill']) {
    await library(kind); await page.getByRole('button', { name: kind === 'app' ? '导入应用原件' : '导入能力', exact: true }).click();
    const raw = Buffer.from('original-' + kind + '\u0000\u0001\nunknown portable format');
    const before = mutations.filter(item => item.path === '/desktop/api/authoring-sessions').length;
    await start().locator('input[type=file]').setInputFiles({ name: kind + '-original.bin', mimeType: 'application/octet-stream', buffer: raw });
    await start().getByRole('button', { name: '保存原件，不运行', exact: true }).click();
    await start().getByRole('heading', { name: '原件已保存 · 待准备', exact: true }).waitFor();
    assert.equal(mutations.filter(item => item.path === '/desktop/api/authoring-sessions').length, before);
    const downloadEvent = page.waitForEvent('download'); await start().getByRole('link', { name: '导出原件', exact: true }).click();
    const download = await downloadEvent, path = out + '/' + kind + '-original.bin'; await download.saveAs(path);
    assert.deepEqual(await readFile(path), raw);
    evidence.push({ case: kind + ' external unknown original stored and exact-byte exported without validation', bytes: raw.length });
    await start().getByRole('button', { name: '完成', exact: true }).click();
  }
  await page.setViewportSize({ width: 390, height: 844 }); await nav('应用');
  await page.getByRole('button', { name: '新建应用', exact: true }).click();
  const bounds = await start().boundingBox(); assert.ok(bounds && bounds.width <= 390 && bounds.x >= 0);
  await start().getByLabel('希望它完成什么', { exact: true }).fill('手机上只存当前需求，不准备或运行。');
  const mobileResponse = authoringPost(''); await start().getByRole('button', { name: '只存草稿', exact: true }).click();
  const mobile = await (await mobileResponse).json(); await detail().waitFor();
  const panelBounds = await detail().boundingBox(); assert.ok(panelBounds && Math.abs(panelBounds.width - 390) < 2 && panelBounds.x >= 0);
  assert.equal((await get('/authoring-sessions/' + mobile.session.id)).session.mode, 'draft_only');
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1); assert.equal(overflow, false);
  await page.screenshot({ path: out + '/mobile-draft.png', fullPage: true });
  evidence.push({ case: 'mobile full-screen authoring and explicit draft-only creation', width: 390, sessionId: mobile.session.id });
  assert.deepEqual(errors, []); assert.deepEqual(external, []);
  await writeFile(out + '/result.json', JSON.stringify({ passed: true, synthetic: true, realModel: false, fixture, evidence, errors, external, mutations }, null, 2));
  console.log(JSON.stringify({ passed: true, cases: evidence.length, output: out }));
} catch (cause) {
  await page.screenshot({ path: out + '/failure.png', fullPage: true }).catch(() => {});
  await writeFile(out + '/result.json', JSON.stringify({ passed: false, error: cause.stack, evidence, errors, external, mutations }, null, 2));
  throw cause;
} finally { await browser.close(); }
