import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR || 'playwright');
const dir = 'data/capability-testing-browser'; await mkdir(dir, { recursive: true });
const browser = await chromium.launch({ headless: true, channel: 'msedge' });
const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
const errors = [], checks = []; page.on('pageerror', e => errors.push(e.message));
const button = name => page.getByRole('button', { name, exact: true });
try {
 await page.goto('http://127.0.0.1:48995/desktop/');
 await button('创建账号').click(); await page.locator('input[name="name"]').fill('测试体验'); await page.locator('input[name="email"]').fill(`testing-${Date.now()}@example.invalid`); await page.locator('input[name="password"]').fill('fixture-password'); await page.locator('form button[type="submit"]').click();
 await page.getByLabel('输入消息').fill('保留我的任务草稿');
 await button('扩展').click(); await page.getByRole('tab', { name: /Tools/ }).click(); await page.getByLabel('能力目录').selectOption('public'); await page.locator('.cap-card').filter({ hasText: '十进制求和' }).click();
 assert.equal(await page.locator('.cap-list').count(), 0);
 await button('保存个人副本').click(); await button('试试这个示例').click();
 assert.equal(await page.getByLabel('数字 1', { exact: true }).inputValue(), '0.10'); assert.equal(await page.getByLabel('数字 2', { exact: true }).inputValue(), '0.20');
 assert.equal(await page.getByLabel('输入参数（JSON）', { exact: true }).count(), 0); checks.push('example from actual version API; ordinary decimal form; independent detail');
 await page.getByLabel('自由试用', { exact: true }).check(); await button('运行试用').click(); await page.getByText('运行成功，尚未确认结果符合预期。自由试用不会启用能力或生成验证通过记录。', { exact: true }).waitFor();
 assert.match(await page.locator('.cap-result').innerText(), /0.30/); assert.equal(await button('启用并用于对话').count(), 0); checks.push('trial runs while disabled and never grants verification');
 await page.getByLabel('数字 1', { exact: true }).fill('0.15');
 page.once('dialog', d => d.dismiss()); await button('试试这个示例').click(); assert.equal(await page.getByLabel('数字 1', { exact: true }).inputValue(), '0.15');
 page.once('dialog', d => d.accept()); await button('试试这个示例').click(); assert.equal(await page.getByLabel('数字 1', { exact: true }).inputValue(), '0.10'); checks.push('example replacement confirmed or cancelled without losing edits');
 await page.getByLabel('数字 1', { exact: true }).fill(''); await button('开始验证').click(); await page.getByText('至少填写 1 个字符。', { exact: true }).waitFor(); await page.getByLabel('数字 1', { exact: true }).fill('0.10');
 await page.getByLabel('求和结果', { exact: true }).fill('9.99'); await button('开始验证').click(); await page.getByRole('heading', { name: '与预期不一致', exact: true }).waitFor(); assert.equal(await button('启用并用于对话').count(), 0);
 await page.getByLabel('求和结果', { exact: true }).fill('0.30'); await button('开始验证').click(); await page.getByRole('heading', { name: '本次测试通过', exact: true }).waitFor(); checks.push('field errors, mismatched expectation and passing verification');
 await page.getByText('保存为我的测试用例', { exact: true }).click(); await page.getByLabel('用例名称').fill('两项小数求和'); await button('保存测试用例').click(); await page.getByText('我的测试用例 · 1', { exact: true }).click(); await button('填入用例').click(); assert.equal(await page.getByLabel('数字 1', { exact: true }).inputValue(), '0.10'); checks.push('save and reload personal case');
 await page.locator('.cap-library').evaluate(e => { for (let n = e; n; n = n.parentElement) n.scrollTop = 0; window.scrollTo(0, 0); });
 await page.screenshot({ path: dir + '/desktop.png' });
 await page.setViewportSize({ width: 390, height: 844 }); await page.waitForFunction(() => getComputedStyle(document.querySelector('.sidebar')).visibility === 'hidden'); assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)); await page.locator('.cap-library').evaluate(e => { for (let n = e; n; n = n.parentElement) n.scrollTop = 0; window.scrollTo(0, 0); }); await page.screenshot({ path: dir + '/mobile.png' }); checks.push('mobile no horizontal overflow');
 await page.setViewportSize({ width: 1440, height: 1100 }); await button('启用并用于对话').click(); await page.getByRole('button', { name: '移除 十进制求和' }).waitFor(); assert.equal(await page.getByLabel('输入消息').inputValue(), '保留我的任务草稿'); checks.push('enable and use preserves chat draft');
 await button('扩展').click(); await page.getByLabel('能力目录').selectOption('public'); await page.locator('.cap-card').filter({ hasText: '资料摘要方法' }).click(); await button('保存个人副本').click(); await page.getByLabel('自由试用', {exact:true}).check(); await page.getByLabel('测试问题',{exact:true}).fill('请总结：收到12条，已处理9条，3条待确认。'); await button('运行试用').click(); await page.getByText('运行成功，尚未确认结果符合预期。自由试用不会启用能力或生成验证通过记录。',{exact:true}).waitFor(); assert.equal(await button('启用并用于对话').count(),0); page.once('dialog', d => d.accept()); await button('试试这个示例').click(); await button('开始验证').click(); await button('确认符合预期').waitFor(); assert.equal(await button('启用并用于对话').count(), 0); await button('确认符合预期').click(); await button('启用并用于对话').waitFor(); checks.push('Skill free trial without JSON, then explicit review before enabling');

 // Exercise file upload and explicit path assignment without JSON or production data.
 const base = 'http://127.0.0.1:48995/desktop/api/capabilities';
 const apiPost = async (path, data) => { const response = await page.request.post(base + path, { data, headers: { 'Idempotency-Key': crypto.randomUUID() } }); assert.ok(response.ok(), await response.text()); return response.json(); };
 const manifest = { format:'cd80.capability/1', kind:'tool', name:'文件测试', description:'读取本次授权文件', source:{identity:'test/file-reader',format:'platform'}, files:[], dependencies:[], permissions:{network:'none',externalEffects:'none',inputFiles:true,outputFiles:false}, tool:{inputSchema:{type:'object',properties:{path:{type:'string',title:'测试文件位置',minLength:1},settings:{type:'object',title:'选项',properties:{mode:{type:'string',title:'读取方式',enum:['plain','text']}},required:['mode'],additionalProperties:false}},required:['path'],additionalProperties:false},outputSchema:{type:'object',properties:{total:{type:'string',title:'文件内容'}},required:['total'],additionalProperties:false},execution:{type:'python_wasi',path:'original/tool.py',callable:'run'}} };
 const draft = await apiPost('/drafts', { kind:'tool', name:manifest.name, description:manifest.description, manifest, files:[{path:'original/tool.py',text:'from pathlib import Path\ndef run(payload):\n    return {"total": Path(payload["path"]).read_text()}\n'}] });
 let saving = await apiPost('/drafts/' + draft.draftId + '/save', {draftRevision:draft.revision});
 while (['queued','running'].includes(saving.status)) { await new Promise(r => setTimeout(r,100)); saving=await (await page.request.get(base+'/jobs/'+saving.jobId)).json(); }
 assert.equal(saving.status,'completed');
 await button('← 返回能力列表').click(); await page.getByRole('tab',{name:/Tools/}).click(); await button('刷新').click(); await page.locator('.cap-card').filter({hasText:'文件测试'}).click();
 await page.getByLabel('填写选项（可选）').check(); await page.getByLabel('读取方式',{exact:true}).selectOption('"plain"');
 await page.getByLabel('上传测试文件').setInputFiles({ name:'case.txt', mimeType:'text/plain', buffer:Buffer.from('authorized-only') });
 await page.getByText('文件已上传并授权本次测试，尚未执行。',{exact:true}).waitFor();
 await page.getByLabel('从授权文件填入路径').selectOption({label:'case.txt'});
 await button('运行试用').click(); await page.locator('.cap-result').getByText('authorized-only',{exact:true}).waitFor();
 assert.equal(await button('启用并用于对话').count(),0); checks.push('no-example tool, nested enum, test upload and explicit file grant/path assignment');
 assert.deepEqual(errors, []); await writeFile(dir + '/report.json', JSON.stringify({ passed: true, model: 'fixture', realWasi: true, checks, errors }, null, 2));
} catch (error) { await page.screenshot({ path: dir + '/failure.png', fullPage: true }); throw error; } finally { await browser.close(); }
