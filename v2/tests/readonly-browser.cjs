/* Developer-only browser integration. Existing Playwright; fixture HTTP only.
 * The child loads pinned P1 CODE, not holdings. Never prints codes or cookies.
 */
'use strict';
const {chromium}=require('playwright'),{spawn}=require('node:child_process'),fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..'),OUT=process.argv[2]||'/tmp/goose-readonly-browser';fs.mkdirSync(OUT,{recursive:true});
async function start(scenario='normal'){
 const code="import json,os\nfrom readonly_server import create_server\ns=create_server(os.environ['GOOSE_P1_CODE'],port=0,scenario='"+scenario+"')\nprint(json.dumps({'origin':s.origin,'code':s.access.pair_code}),flush=True)\ntry:s.serve_forever()\nfinally:s.server_close()";
 const proc=spawn(process.env.GOOSE_TEST_PYTHON||'python',['-u','-c',code],{cwd:ROOT,env:process.env,stdio:['ignore','pipe','pipe']});
 const info=await new Promise((resolve,reject)=>{let buffer='';const timer=setTimeout(()=>{proc.kill();reject(Error('fixture server startup timeout'));},10000);proc.stdout.on('data',data=>{buffer+=data;const i=buffer.indexOf('\n');if(i>=0){clearTimeout(timer);resolve(JSON.parse(buffer.slice(0,i)));}});proc.on('exit',()=>{clearTimeout(timer);reject(Error('fixture server unavailable; check P1 version'));});});
 return {...info,proc,stop:()=>proc.kill()};
}
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.P1_BROWSER_EXECUTABLE||'/usr/bin/chromium',headless:true,args:['--no-sandbox']});const servers=[];let count=0;
 async function visit(s,width=390){const context=await browser.newContext({viewport:{width,height:844},deviceScaleFactor:1});const page=await context.newPage();const requests=[],errors=[];page.on('request',r=>requests.push(new URL(r.url()).pathname));page.on('pageerror',e=>errors.push(e.message));await page.goto(s.origin+'/readonly/');await page.locator('#pair-form').waitFor();return {context,page,requests,errors};}
 async function pair(page,code){await page.locator('input[name=code]').fill(code);await page.locator('#pair-form button').click();await page.getByText('暂无法完整估值',{exact:true}).waitFor();}
 try{
  const s=await start();servers.push(s);const {context,page,requests,errors}=await visit(s);
  assert.equal((await context.request.get(s.origin+'/api/v1/view')).status(),401);await pair(page,s.code);
  assert.deepEqual(await page.evaluate(async()=>({local:Object.keys(localStorage),session:Object.keys(sessionStorage),cache:await caches.keys(),cookies:document.cookie})),{local:[],session:[],cache:[],cookies:''});
  for(const [route,name] of [['/','home'],['/holdings','holdings'],['/strategies','strategies'],['/reports','reports']]){
   await page.locator('.bottom-nav a[href="#'+route+'"]').click();await page.waitForTimeout(100);
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false);
   const text=await page.locator('main').innerText();assert.ok(!/固定演示样本|DEMO_HB|DEMO_YH|参考市值 · 演示/.test(text));
   await page.screenshot({path:path.join(OUT,'iphone-'+name+'.png')});count++;
  }
  await page.locator('.bottom-nav a[href="#/holdings"]').click();
  await page.locator('a[href="#/holdings/account-2/515450.SH"]').click();
  await page.getByText('原始历史收盘数值 · 单位未核验',{exact:true}).waitFor();
  assert.match(await page.locator('main').innerText(),/原始历史收盘数值 · 单位未核验/);
  assert.match(await page.locator('main').innerText(),/1\.234/);assert.match(await page.locator('main').innerText(),/未知/);
  await page.getByRole('link',{name:'← 返回持仓'}).click();count++;
  await context.setOffline(true);
  await page.getByRole('heading',{name:/网络已断开|连接已断开/}).waitFor();assert.ok(!(await page.locator('main').innerText()).includes('6,000'));count++;
  await context.setOffline(false);await page.getByRole('button',{name:'检查连接 / 重试'}).click();await page.getByText('我的持仓',{exact:true}).first().waitFor();
  await page.route('**/api/v1/view',async route=>{const res=await route.fetch();const payload=await res.json();payload.mode='demo';payload.secret='PRIVATE_CANARY';await route.fulfill({status:200,body:JSON.stringify(payload),contentType:'application/json'});});
  await page.getByRole('button',{name:'重新读取当前批次'}).click();await page.getByRole('heading',{name:/数据协议不匹配/}).waitFor();
  assert.ok(!(await page.locator('main').innerText()).includes('PRIVATE_CANARY'));count++;await page.unroute('**/api/v1/view');
  await page.getByRole('button',{name:'检查连接 / 重试'}).click();await page.getByRole('button',{name:'退出并撤销会话'}).click();
  assert.equal((await context.request.get(s.origin+'/api/v1/view')).status(),401);assert.ok(!(await page.locator('main').innerText()).includes('6,000'));count++;
  for(const forbidden of ['/demo-data.js','/model.js','/app.js','/sw.js'])assert.ok(!requests.includes(forbidden),forbidden);
  assert.deepEqual(errors,[]);await context.close();
  for(const scenario of ['missing','stale']){
   const ss=await start(scenario);servers.push(ss);const c=await visit(ss,1440);await pair(c.page,ss.code);
   const text=await c.page.locator('main').innerText();assert.match(text,scenario==='missing'?/尚未提供日线/:/数据已过期/);
   await c.page.screenshot({path:path.join(OUT,'desktop-'+scenario+'.png')});count++;await c.context.close();
  }
  const dead=await start();servers.push(dead);const d=await visit(dead);await pair(d.page,dead.code);dead.stop();
  await d.page.getByRole('button',{name:'重新读取当前批次'}).click();await d.page.getByRole('heading',{name:/网络已断开|连接已断开/}).waitFor();assert.ok(!(await d.page.locator('main').innerText()).includes('2,500'));count++;await d.context.close();
  const revoked=await start();servers.push(revoked);const rv=await visit(revoked);await pair(rv.page,revoked.code);
  assert.equal((await rv.context.request.post(revoked.origin+'/session/logout',{headers:{Origin:revoked.origin},data:{}})).status(),200);
  await rv.page.getByRole('heading',{name:/未授权：会话可能已过期或撤销/}).waitFor({timeout:20000});
  assert.ok(!(await rv.page.locator('main').innerText()).includes('2,500'));count++;await rv.context.close();
  const sw=await start();servers.push(sw);const sc=await browser.newContext();const sp=await sc.newPage();let privateFetches=0;
  await sp.addInitScript(()=>Object.defineProperty(navigator,'serviceWorker',{value:{controller:{}}}));
  sp.on('request',r=>{if(new URL(r.url()).pathname==='/api/v1/view')privateFetches++;});await sp.goto(sw.origin+'/readonly/');
  await sp.getByRole('heading',{name:/旧离线缓存不符合要求/}).waitFor();assert.equal(privateFetches,0);count++;await sc.close();
  console.log(JSON.stringify({checks:count,demoRequests:0,pageErrors:0,fixturesOnly:true}));
 }finally{for(const s of servers)s.stop();await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
