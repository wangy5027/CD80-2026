// Browser acceptance for the two home Composers and the existing pool Creator.
// The fixture supplies deterministic candidates; all HTTP, authoring and APP
// persistence are real and isolated from the user's accounts and model network.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';

const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const base = 'http://127.0.0.1:49024';
const output = '.run/home-creation-browser';
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(30000);
const checks = [], errors = [], external = [], mutations = [];
let droppedCreate = null;
let rejectedCreate = null;
page.on('pageerror', error => errors.push(error.message));
page.on('request', request => {
  if (request.method() === 'GET') return;
  const path = new URL(request.url()).pathname;
  const data = /\/(login|register)$/.test(path) ? { account: 'fixture' } : (() => {
    try { return request.postDataJSON(); } catch { return { multipart: true }; }
  })();
  mutations.push({ path, data, key: request.headers()['idempotency-key'] || null });
});
await page.route('**/*', async route => {
  const url = route.request().url();
  if (!url.startsWith(base + '/') && !/^(data:|blob:)/.test(url)) {
    external.push(url); return route.abort();
  }
  if (rejectedCreate && !rejectedCreate.done && new URL(url).pathname === '/desktop/api/authoring-sessions'
      && route.request().method() === 'POST') {
    rejectedCreate.done = true;
    rejectedCreate.body = route.request().postDataJSON();
    rejectedCreate.key = route.request().headers()['idempotency-key'];
    return route.fulfill({ status: 400, contentType: 'application/json',
      body: JSON.stringify({ code: 'INVALID_INSTRUCTION', error: '明确拒绝：请输入完整需求。' }) });
  }
  if (droppedCreate && !droppedCreate.done && new URL(url).pathname === '/desktop/api/authoring-sessions'
      && route.request().method() === 'POST') {
    droppedCreate.done = true;
    const accepted = await route.fetch();
    droppedCreate.status = accepted.status();
    droppedCreate.response = await accepted.json();
    return route.abort('failed'); // Backend accepted; only the browser response is lost.
  }
  return route.continue();
});

const creationPosts = () => mutations.filter(row => row.path === '/desktop/api/authoring-sessions');
const ordinaryPosts = () => mutations.filter(row => /\/conversations\/[^/]+\/messages$/.test(row.path));
const appRuns = () => mutations.filter(row => /\/asset-apps\/[^/]+\/(runs|verify|enable)$/.test(row.path)
  || /\/agents\/[^/]+\/runs$/.test(row.path));
const hash = text => createHash('sha256').update(text).digest('hex');
const mode = () => page.getByRole('button', { name: '创建模式', exact: true });
const detail = () => page.getByRole('dialog').filter({ has: page.locator('#author-detail-title') });
const nav = name => page.locator('#workspace-sidebar').getByRole('button', { name, exact: true });
async function fixture() { return (await (await page.request.get(base + '/__home_creation_fixture__/state')).json()); }
async function get(path) {
  const response = await page.request.get(base + '/desktop/api' + path);
  assert.ok(response.ok(), `${path}: ${response.status()}`);
  return response.json();
}
async function step(label, fn) { await fn(); checks.push(label); console.log('PASS', label); }
async function ensureMode(on) {
  await mode().waitFor();
  if ((await mode().getAttribute('aria-pressed')) !== String(on)) await mode().click();
  assert.equal(await mode().getAttribute('aria-pressed'), String(on));
}
async function waitSession(id, phases, timeout = 45000) {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const value = await get('/authoring-sessions/' + encodeURIComponent(id));
    if (phases.includes(value.session.phase)) return value;
    await new Promise(resolve => setTimeout(resolve, 180));
  }
  throw new Error(`Authoring ${id} did not reach ${phases.join(', ')}`);
}
async function sendFromComposer(prompt, method = 'button') {
  const at = creationPosts().length;
  await page.locator('#composer-input').fill(prompt);
  if (method === 'enter') await page.locator('#composer-input').press('Enter');
  else await page.getByRole('button', { name: '创建并保存 APP', exact: true }).click();
  const until = Date.now() + 30000;
  while (creationPosts().length === at && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 30));
  assert.equal(creationPosts().length, at + 1, 'One Composer submit creates one authoring session');
  return creationPosts().at(-1);
}
async function newTask() {
  await nav('新任务').click();
  await page.locator('#composer-input').waitFor();
}
async function attachExisting(id) {
  await page.getByRole('button', { name: '添加文件', exact: true }).click();
  await page.getByRole('button', { name: '上传或选择文件', exact: true }).click();
  const picker = page.getByRole('dialog', { name: '添加文件', exact: true });
  await picker.getByLabel('数据管理中的文件', { exact: true }).getByRole('button').filter({ hasText: 'creation-source.csv' }).click();
  await picker.getByRole('button', { name: '完成', exact: true }).click();
  assert.ok(await page.locator('.attach-chip').filter({ hasText: 'creation-source.csv' }).isVisible());
  return id;
}
async function savedAfter(request, expectedCount) {
  const body = request.data;
  assert.equal(body.kind, 'app'); assert.equal(body.intent, 'create'); assert.equal(body.mode, 'prepare');
  await page.locator('#my-apps-title').waitFor({ timeout: 45000 });
  const until = Date.now() + 45000;
  let state;
  while (Date.now() < until) {
    state = await fixture();
    if (state.ownerApps.length === expectedCount) break;
    await new Promise(resolve => setTimeout(resolve, 180));
  }
  assert.equal(state.ownerApps.length, expectedCount, JSON.stringify(state.ownerApps));
  const saved = state.ownerApps[0];
  assert.equal(saved.verification.status, 'unverified');
  assert.equal(saved.enabledVersionId, null);
  assert.deepEqual(saved.testCases[0].input, { prices: [100, 120, 90, 108] });
  await page.locator('.apps-library-card').filter({ hasText: saved.name }).first().waitFor();
  return saved;
}
async function login(email) {
  await page.locator('input[name=email]').fill(email);
  await page.locator('input[name=password]').fill('fixture-password');
  await page.locator('form button[type=submit]').click();
  await page.locator('#composer-input').waitFor();
}
const longPrompt = [
  '请创建一个完整 APP：输入 prices 数组，按原顺序计算首末变化率、历史最大回撤和样本条数。',
  '接口必须是 cd80.app/2；输入 {"prices":[100,120,90,108]}，输出 {"analysis":{"change":8,"drawdown":25,"count":4}}。',
  '原任务用例要随版本保存，生成后先保存到我的 APP，标为未验证、未启用。请读取明确选中的 creation-source.csv 作为本轮参考，但不能替换 prices 输入。',
  ...Array.from({ length: 100 }, (_, i) => `完整约束 ${i + 1}：不得在创建阶段运行、验证或启用生成的代码；保留原始需求。`),
  '【末尾完整性标记】FINAL-CREATION-MARKER-20261015',
].join('\n');

async function servedBuild() {
  const html = await readFile('frontend/dist/index.html');
  const entry = html.toString('utf8').match(/<script[^>]+src="([^"]+\.js)"/);
  assert.ok(entry, 'Built index must contain an entry script');
  const filename = entry[1].split('/').at(-1);
  const javascript = await readFile('frontend/dist/assets/' + filename);
  const liveHtml = await (await page.request.get(base + '/desktop/')).body();
  const liveJavascript = await (await page.request.get(base + entry[1])).body();
  assert.deepEqual(liveHtml, html, 'Fixture must serve the frozen index.html');
  assert.deepEqual(liveJavascript, javascript, 'Fixture must serve the frozen entry JS');
  return { indexHtmlSha256: hash(html), entryJs: filename, entryJsSha256: hash(javascript) };
}

try {
  await step('默认普通对话，未开启创建模式时只走原问答', async () => {
    const ready = await fixture();
    assert.equal(ready.fixture, true); assert.equal(ready.realModel, false);
    await page.goto(base + '/desktop/'); await login('creation-owner@example.invalid');
    await ensureMode(false);
    await page.locator('#composer-input').fill('你好，请正常答复这条普通消息。');
    await page.getByRole('button', { name: '发送消息', exact: true }).click();
    await page.getByText('普通问答已收到。', { exact: true }).waitFor({ timeout: 30000 });
    assert.equal(creationPosts().length, 0);
    assert.equal((await fixture()).chatCalls.length, 1);
  });

  let existingBody, poolBody, homeBody, firstApp;
  const sourceId = (await fixture()).fileId;
  await step('已有会话 Composer 发送完整长需求与已选文件，只创建标准 APP', async () => {
    await ensureMode(true);
    await attachExisting(sourceId);
    const oldChat = ordinaryPosts().length;
    existingBody = await sendFromComposer(longPrompt, 'enter');
    assert.equal(ordinaryPosts().length, oldChat);
    assert.equal(existingBody.data.instruction, longPrompt);
    assert.ok(existingBody.data.sourceRefs.some(item => item.kind === 'upload' && item.reference.fileId === sourceId));
    firstApp = await savedAfter(existingBody, 1);
    const detailValue = await get('/asset-apps/' + firstApp.id);
    assert.equal(detailValue.definition.format, 'cd80.app/2');
    assert.ok(detailValue.definition.nodes.some(node => node.kind === 'code'));
    assert.equal((await get('/asset-apps/' + firstApp.id + '/runs')).items.length, 0);
  });

  await step('技能池自然语言入口和首页使用相同创建参数及保存状态', async () => {
    await nav('技能池').click();
    await page.getByRole('button', { name: '创建 APP', exact: true }).click();
    await page.getByRole('button', { name: /用自然语言创建/ }).click();
    const start = page.getByRole('dialog').filter({ has: page.locator('#author-start-title') });
    await start.getByLabel('希望它完成什么', { exact: true }).fill(longPrompt);
    const files = start.getByText('补充本次已授权文件', { exact: true });
    await files.click();
    await start.getByLabel('creation-source.csv', { exact: true }).check();
    const at = creationPosts().length;
    await start.getByRole('button', { name: '整理并保存未验证 APP', exact: true }).click();
    const until = Date.now() + 30000;
    while (creationPosts().length === at && Date.now() < until) await new Promise(resolve => setTimeout(resolve, 30));
    assert.equal(creationPosts().length, at + 1);
    poolBody = creationPosts().at(-1);
    assert.deepEqual(['kind', 'intent', 'mode', 'instruction', 'sourceRefs'].map(k => poolBody.data[k]),
      ['kind', 'intent', 'mode', 'instruction', 'sourceRefs'].map(k => existingBody.data[k]));
    await savedAfter(poolBody, 2);
  });

  await step('首页新任务 Composer 可直接创建，创建后定位我的 APP', async () => {
    await newTask(); await ensureMode(true);
    homeBody = await sendFromComposer('请创建合成价格序列计算 APP。输入 prices，输出 change、drawdown、count；生成后保存为未验证。');
    const saved = await savedAfter(homeBody, 3);
    assert.ok(saved.id !== firstApp.id);
    assert.equal(await page.locator('.apps-library-card').filter({ hasText: saved.name }).first().count(), 1);
  });

  await step('必要问题在同一创作会话补充，关闭详情后仍可恢复', async () => {
    await newTask(); await ensureMode(true);
    const post = await sendFromComposer('【需要补充】请创建合成价格 APP。先确认 prices 字段，再生成完整实现。');
    assert.equal(post.data.kind, 'app');
    let state = await fixture();
    const id = state.creates.at(-1)?.responseId || null;
    // The POST response may arrive while generation is still preparing.
    const created = await get('/authoring-sessions?limit=100');
    const sessionId = id || created.items.find(item => item.instruction?.includes('【需要补充】'))?.id;
    assert.ok(sessionId, JSON.stringify(created.items));
    const waiting = await waitSession(sessionId, ['awaiting_input']);
    assert.equal((await fixture()).ownerApps.length, 3);
    if (await detail().isVisible()) await detail().getByRole('button', { name: '关闭创作详情', exact: true }).click();
    await page.reload(); await page.locator('#composer-input').waitFor();
    await ensureMode(true);
    const before = creationPosts().length;
    const answerRequest = page.waitForRequest(request =>
      new URL(request.url()).pathname === '/desktop/api/authoring-sessions/' + sessionId + '/answers'
      && request.method() === 'POST');
    await page.locator('#composer-input').fill('是，prices 是非空数字数组。');
    await page.getByRole('button', { name: '创建并保存 APP', exact: true }).click();
    const answerBody = (await answerRequest).postDataJSON();
    assert.equal(answerBody.questionId, waiting.session.question.id);
    assert.equal(creationPosts().length, before, 'Answer must reuse original session');
    const completed = await waitSession(sessionId, ['completed']);
    assert.ok(completed.session.result?.assetId);
    assert.equal((await fixture()).ownerApps.length, 4);
    assert.ok((await fixture()).generated.some(item => item.instruction.includes('用户回答：是，prices 是非空数字数组。')));
    assert.equal(waiting.session.id, completed.session.id);
  });

  await step('明确400拒绝后可编辑需求重发，不锁定为未知结果', async () => {
    await newTask(); await ensureMode(true);
    const before = (await fixture()).ownerApps.length;
    const initial = '请创建合成价格 APP，需求还需补全。';
    const improved = '请创建完整的合成价格 APP；输入 prices 数组，输出 change、drawdown 和 count，保存未验证。';
    rejectedCreate = { done: false };
    const rejection = page.waitForResponse(response =>
      new URL(response.url()).pathname === '/desktop/api/authoring-sessions'
      && response.request().method() === 'POST' && response.status() === 400);
    await page.locator('#composer-input').fill(initial);
    await page.getByRole('button', { name: '创建并保存 APP', exact: true }).click();
    await rejection;
    assert.equal(rejectedCreate.done, true);
    assert.equal(rejectedCreate.body.instruction, initial);
    assert.equal((await fixture()).ownerApps.length, before);
    await page.getByText('当前网络连接失败', { exact: true }).first().waitFor();
    assert.equal(await page.locator('#composer-input').inputValue(), initial);
    assert.equal(await page.getByRole('button', { name: '核实并恢复同一次创建', exact: true }).count(), 0);
    await page.locator('#composer-input').fill(improved);
    assert.equal(await page.getByRole('button', { name: '创建并保存 APP', exact: true }).isEnabled(), true);
    const post = await sendFromComposer(improved);
    assert.equal(post.data.instruction, improved);
    assert.notEqual(post.key, rejectedCreate.key);
    await savedAfter(post, before + 1);
    const state = await fixture();
    assert.equal(state.creates.filter(item => item.key === rejectedCreate.key).length, 0);
    assert.equal(state.creates.filter(item => item.key === post.key).length, 1);
    rejectedCreate = null;
  });

  await step('已有成功创作后再次创建，丢失首次响应仍按原幂等请求恢复一个 APP', async () => {
    await newTask(); await ensureMode(true);
    const before = (await fixture()).ownerApps.length;
    droppedCreate = { done: false };
    await page.locator('#composer-input').fill('请创建第二次独立的合成价格分析 APP，先保存未验证。');
    await page.getByRole('button', { name: '创建并保存 APP', exact: true }).click();
    await page.waitForFunction(() => !!document.querySelector('#composer-input'), null, { timeout: 15000 });
    const deadline = Date.now() + 10000;
    while (!droppedCreate.done && Date.now() < deadline) await new Promise(resolve => setTimeout(resolve, 40));
    assert.equal(droppedCreate.status, 201);
    const acceptedId = droppedCreate.response.session.id;
    await waitSession(acceptedId, ['completed']);
    await page.getByRole('button', { name: '核实并恢复同一次创建', exact: true }).click();
    await page.locator('#my-apps-title').waitFor({ timeout: 45000 });
    assert.equal((await fixture()).ownerApps.length, before + 1);
    const lastTwo = creationPosts().slice(-2);
    assert.equal(lastTwo.length, 2);
    assert.equal(lastTwo[0].key, lastTwo[1].key);
    assert.deepEqual(lastTwo[0].data, lastTwo[1].data);
    assert.equal((await fixture()).creates.filter(row => row.key === lastTwo[0].key).length, 2);
    droppedCreate = null;
  });

  await step('账号切换清除模式与提交关联，另一账号看不到原账号 APP', async () => {
    await nav('账号与设置').click();
    await page.getByRole('button', { name: '退出登录', exact: true }).click();
    await login('creation-other@example.invalid');
    await ensureMode(false);
    await nav('技能池').click();
    await page.getByRole('heading', { name: '我的 APP', exact: true }).waitFor();
    assert.equal((await page.request.get(base + '/desktop/api/asset-apps/' + firstApp.id)).status(), 404);
    const state = await fixture();
    assert.equal(state.otherAppCount, 0);
  });

  await step('390px 手机上首页开关和输入框无横向溢出', async () => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole('button', { name: '打开导航', exact: true }).click();
    await nav('新任务').click();
    await mode().waitFor();
    await ensureMode(true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.screenshot({ path: output + '-mobile.png', fullPage: true });
  });

  const state = await fixture();
  assert.equal(state.realModel, false);
  for (const key of ['run', 'verify', 'plan', 'execute', 'upstream']) assert.equal(state.counters[key], 0, key);
  assert.deepEqual(errors, []); assert.deepEqual(external, []); assert.deepEqual(appRuns(), []);
  const report = {
    status: 'passed', checks, realModel: false, temporaryAccounts: true,
    servedBuild: await servedBuild(),
    persistedApps: state.ownerApps.length, otherAppCount: state.otherAppCount,
    longPrompt: { length: longPrompt.length, sha256: hash(longPrompt),
      exactInExisting: existingBody.data.instruction === longPrompt,
      exactInPool: poolBody.data.instruction === longPrompt },
    counters: state.counters, droppedResponseRecovered: droppedCreate === null,
    errors, external,
  };
  await writeFile(output + '.json', JSON.stringify(report, null, 2));
  console.log(JSON.stringify({ status: 'passed', checks: checks.length, persistedApps: state.ownerApps.length }));
} catch (error) {
  await writeFile(output + '-failure.txt', String(error.stack || error) + '\n' + await page.locator('body').innerText().catch(() => ''));
  await page.screenshot({ path: output + '-failure.png', fullPage: true }).catch(() => {});
  throw error;
} finally {
  await browser.close();
}
