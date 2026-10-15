import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
const {chromium}=createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE_DIR||'playwright');
const browser=await chromium.launch({headless:true,channel:'msedge'});
const page=await browser.newPage({viewport:{width:1440,height:1100}});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
page.on('response',async response=>{if(response.url().includes('/jobs/')){const value=await response.json();if(value.error)console.log(JSON.stringify(value.error));}});
const out='data/local-financial-browser';await mkdir(out,{recursive:true});
const button=name=>page.getByRole('button',{name,exact:true});
try{
 await page.goto('http://127.0.0.1:48996/desktop/');
 await button('创建账号').click();await page.locator('input[name="name"]').fill('隔离验收');
 await page.locator('input[name="email"]').fill(`financial-${Date.now()}@example.invalid`);
 await page.locator('input[name="password"]').fill('fixture-password');await page.locator('form button[type="submit"]').click();
 await button('扩展').waitFor();
 const response=await page.request.post('http://127.0.0.1:48996/desktop/api/capabilities/imports',{
   headers:{'Idempotency-Key':crypto.randomUUID()},multipart:{file:{name:'news.zip',mimeType:'application/zip',buffer:await readFile('capabilities/local-financial/packages/cd80-news-tool.zip')}}});
 assert.ok(response.ok(),await response.text());let job=await response.json();
 while(['queued','running'].includes(job.status)){await new Promise(r=>setTimeout(r,100));job=await(await page.request.get('http://127.0.0.1:48996'+job.statusUrl)).json();}
 assert.equal(job.status,'completed');
 await button('扩展').click();await page.getByRole('tab',{name:/Tools/}).click();
 await page.locator('.cap-card').filter({hasText:'已接入财经新闻工具'}).click();await button('试试这个示例').click();
 await page.getByText(/将按已加载示例的 .* 项规则验证实际结果/).waitFor();
 await button('开始验证').click();await page.getByRole('heading',{name:'本次测试通过',exact:true}).waitFor();
 await button('启用并用于对话').waitFor();
 await page.getByText('保存为我的测试用例',{exact:true}).click();await page.getByLabel('用例名称').fill('动态新闻规则');await button('保存测试用例').click();
 await page.getByText('我的测试用例 · 1',{exact:true}).click();await button('填入用例').click();
 await page.getByText(/将按已加载示例的 .* 项规则验证实际结果/).waitFor();
 await page.screenshot({path:out+'/desktop.png'});
 await page.setViewportSize({width:390,height:844});
 await page.waitForFunction(()=>getComputedStyle(document.querySelector('.sidebar')).visibility==='hidden');
 await page.locator('.cap-library').evaluate(e=>{for(let node=e;node;node=node.parentElement)node.scrollTop=0;window.scrollTo(0,0);});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:out+'/mobile.png'});
 assert.deepEqual(errors,[]);
 await writeFile(out+'/report.json',JSON.stringify({passed:true,realWasi:true,source:'fixture',checks:['动态官方示例填入','声明式规则实际验证','个人用例规则保存与加载','验证后提供主动启用','移动端无横向溢出'],errors},null,2));
}catch(e){await page.screenshot({path:out+'/failure.png',fullPage:true});console.log(await page.locator('.cap-library').innerText());throw e;}finally{await browser.close();}
