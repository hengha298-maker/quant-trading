/* DEVELOPER ONLY. Local self-signed TLS surrogate; not a real Tailscale tunnel.
 * Certificate bypass is restricted to this isolated browser test context.
 * No production TLS bypass flag, real files, identities or tokens are saved.
 */
'use strict';
const {chromium}=require('playwright'),{spawn,execFileSync}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
const ROOT=path.resolve(__dirname,'..'),tmp=fs.mkdtempSync(path.join(os.tmpdir(),'goose-phone-tls-'));
const privateFiles=process.env.GOOSE_TEST_PRIVATE_FILES==='1';
const sensitive=privateFiles?'7,333':'2,500';
let proc,browser,phase='startup';
(async()=>{
 const cert=path.join(tmp,'cert.pem'),key=path.join(tmp,'key.pem');
 execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=fixture-pc.fixture-tail.ts.net','-addext','subjectAltName=DNS:fixture-pc.fixture-tail.ts.net','-keyout',key,'-out',cert],{stdio:'ignore'});
 proc=spawn(process.env.GOOSE_TEST_PYTHON||'python',['-u','tests/phone_fixture_host.py'],{cwd:ROOT,env:{...process.env,GOOSE_TEST_TLS_CERT:cert,GOOSE_TEST_TLS_KEY:key},stdio:['pipe','pipe','ignore']});
 const lines=[],waiters=[];let buffer='';
 proc.stdout.on('data',chunk=>{buffer+=chunk;let i;while((i=buffer.indexOf('\n'))>=0){const value=JSON.parse(buffer.slice(0,i));buffer=buffer.slice(i+1);if(waiters.length)waiters.shift()(value);else lines.push(value);}});
 function next(){return Promise.race([new Promise(resolve=>{if(lines.length)resolve(lines.shift());else waiters.push(resolve);}),new Promise((_,reject)=>{const t=setTimeout(()=>reject(Error('fixture TLS host timeout')),10000);t.unref();})]);}
 async function command(op){proc.stdin.write(JSON.stringify({op})+'\n');return next();}
 const s=await next();let count=0;
 browser=await chromium.launch({executablePath:process.env.P1_BROWSER_EXECUTABLE||'/usr/bin/chromium',headless:true,args:['--no-sandbox','--host-resolver-rules=MAP fixture-pc.fixture-tail.ts.net 127.0.0.1','--no-proxy-server']});
 const context=await browser.newContext({ignoreHTTPSErrors:true,viewport:{width:390,height:844}});
 const page=await context.newPage(),requests=[],errors=[];
 page.on('request',r=>requests.push(new URL(r.url()).pathname));page.on('pageerror',e=>errors.push(e.message));
 async function pair(code){await page.locator('input[name=code]').fill(code);await page.locator('#pair-form button').click();await page.getByText('暂无法完整估值',{exact:true}).waitFor();}
 await page.goto(s.origin+'/readonly/');await page.locator('#pair-form').waitFor();await pair(s.code);
 const cookies=await context.cookies();assert.equal(cookies.length,1);assert.ok(cookies[0].name.startsWith('__Host-'));assert.equal(cookies[0].secure,true);assert.equal(cookies[0].httpOnly,true);assert.equal(cookies[0].sameSite,'Strict');count++;
 phase='four pages';
 for(const route of ['/','/holdings','/strategies','/reports']){
  await page.locator('.bottom-nav a[href="#'+route+'"]').click();
  await page.getByRole('heading',{name:({'/':'资产与数据状态','/holdings':'我的持仓','/strategies':'研究与策略','/reports':'数据报告'})[route],exact:true}).waitFor();
  assert.ok(!(await page.locator('main').innerText()).includes('无法连接'));
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);count++;
 }
 phase='private holdings';
 if(privateFiles){
  await page.locator('.bottom-nav a[href="#/holdings"]').click();
  await page.getByRole('heading',{name:'我的持仓',exact:true}).waitFor();
  const text=await page.locator('main').innerText();assert.match(text,/337/);assert.match(text,/119/);assert.match(text,/23\.456/);assert.match(text,/7,333/);assert.match(text,/未知/);
  assert.ok(!(await page.locator('body').innerText()).includes('仅虚构测试'));
  await page.locator('a[href="#/holdings/account-2/515450.SH"]').click();
  await page.getByText('原始历史收盘数值 · 单位未核验',{exact:true}).waitFor();
  assert.match(await page.locator('main').innerText(),/原始历史收盘数值 · 单位未核验/);count++;
 }
 phase='storage';
 assert.deepEqual(await page.evaluate(async()=>({local:Object.keys(localStorage),session:Object.keys(sessionStorage),cache:await caches.keys(),cookie:document.cookie})),{local:[],session:[],cache:[],cookie:''});count++;
 await page.locator('.bottom-nav a[href="#/"]').click();
 const oldView=await page.evaluate(async()=>await (await fetch('/api/v1/view',{cache:'no-store',credentials:'same-origin'})).json());let delayed;
 assert.equal(oldView.test_data,!privateFiles);
 assert.deepEqual(await page.evaluate(async()=>({db:await indexedDB.databases(),sw:(await navigator.serviceWorker.getRegistrations()).length})),{db:[],sw:0});
 if(privateFiles){assert.equal(oldView.accounts[0].cash,'0');assert.equal(oldView.accounts[1].cash,null);assert.equal(oldView.quotes[0].close,'22.718');assert.equal(oldView.quotes[1].close_unit,null);}
 phase='timeout request';
 await page.route('**/api/v1/view',route=>{delayed=route;});
 await page.getByRole('button',{name:'重新读取当前批次'}).click();
 await page.getByRole('heading',{name:/连接已超时/}).waitFor({timeout:11000});
 phase='late response rejection';
 assert.ok(!(await page.locator('main').innerText()).includes(sensitive));
 await delayed.fulfill({status:200,contentType:'application/json',body:JSON.stringify(oldView)}).catch(()=>{});
 await page.unroute('**/api/v1/view');await page.waitForTimeout(200);
 assert.ok(!(await page.locator('main').innerText()).includes(sensitive));count++;
 await page.getByRole('button',{name:'检查连接 / 重试'}).click();await page.getByText('暂无法完整估值',{exact:true}).waitFor();
 phase='offline clearing';
 await context.setOffline(true);await page.getByRole('heading',{name:/网络已断开|连接已断开/}).waitFor();assert.ok(!(await page.locator('main').innerText()).includes(sensitive));count++;
 await context.setOffline(false);await page.getByRole('button',{name:'检查连接 / 重试'}).click();await page.getByText('暂无法完整估值',{exact:true}).waitFor();
 phase='expired session';
 await command('expire');await page.getByRole('button',{name:'重新读取当前批次'}).count();
 await page.locator('.bottom-nav a[href="#/"]').click();await page.getByRole('button',{name:'重新读取当前批次'}).click();
 await page.getByRole('heading',{name:/未授权：会话可能已过期或撤销/}).waitFor();assert.ok(!(await page.locator('main').innerText()).includes(sensitive));count++;
 await pair((await command('rotate')).code);await command('rotate');
 phase='revoked session';
 await page.getByRole('heading',{name:/未授权：会话可能已过期或撤销/}).waitFor({timeout:20000});count++;
 await pair((await command('rotate')).code);await command('deny');await page.getByRole('button',{name:'重新读取当前批次'}).click();
 phase='denied device';
 await page.getByRole('heading',{name:/此设备未获授权/}).waitFor();assert.equal(await page.locator('#pair-form').count(),0);assert.ok(!(await page.locator('main').innerText()).includes(sensitive));count++;
 const fresh=await command('allow');await page.getByRole('button',{name:'检查连接 / 重试'}).click();await page.locator('#pair-form').waitFor();await pair(fresh.code);
 await command('disconnect');await page.getByRole('button',{name:'重新读取当前批次'}).click();
 await page.getByRole('heading',{name:/连接已断开|连接已超时/}).waitFor({timeout:12000});assert.ok(!(await page.locator('main').innerText()).includes(sensitive));count++;
 for(const forbidden of ['/demo-data.js','/model.js','/app.js','/sw.js'])assert.ok(!requests.includes(forbidden));
 assert.deepEqual(errors,[]);
 await context.close();await browser.close();browser=null;
 proc.stdin.write('{"op":"stop"}\n');
 await new Promise((resolve,reject)=>{const t=setTimeout(()=>reject(Error('fixture host did not stop')),5000);proc.on('exit',code=>{clearTimeout(t);code===0?resolve():reject(Error('fixture host failed'));});});proc=null;count++;
 console.log(JSON.stringify({checks:count,fixturesOnly:true,explicitTemporaryJsonFiles:privateFiles,transport:'local TLS surrogate, NOT Tailscale/iPhone acceptance',demoRequests:0,pageErrors:0}));
})().catch(e=>{console.error(JSON.stringify({failedPhase:phase,error:e.name,details:'No credentials or response bodies logged.',location:e.stack?.split('\n').find(line=>line.includes('phone-browser.cjs:'))?.trim()}));process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();if(proc){proc.stdin.end();proc.kill();}fs.rmSync(tmp,{recursive:true,force:true});});
