/* Separate V3.2 origin and browser contexts; no user ledger or V1 data. */
'use strict';
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const base=process.env.V32_BASE_URL||'http://127.0.0.1:9394';
const work=path.resolve(__dirname,'../work');
const stateOf=p=>p.evaluate(async()=>(await import('/io.js')).loadState());
const password='fictional-research-backup-password';
const tests=[];
async function step(name,fn){await fn();tests.push(name);console.log('PASS '+name);}
(async()=>{
 await fs.mkdir(work,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,acceptDownloads:true});
 let p=await context.newPage();const errors=[],outbound=[],reportRequests=[];
 context.on('page',page=>page.on('pageerror',e=>errors.push(e.message)));p.on('pageerror',e=>errors.push(e.message));
 context.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith(base))outbound.push(r.url());if(r.url().endsWith('/research-report-v32.json'))reportRequests.push(r.url());});
 p.on('dialog',d=>d.accept());let saved,backup;
 try{
  await step('new research application is isolated and never auto-imports Windows reports',async()=>{
   await p.goto(base);await p.waitForFunction(()=>document.querySelector('#offline-status').textContent==='离线就绪');
   assert.match(await p.title(),/V3.2/);assert.equal(reportRequests.length,0);assert.equal((await stateOf(p)).planning.reports.length,0);
   assert.equal(await p.locator('.bottom-nav button').count(),4);
  });
  await step('explicit local-report fetch previews source date and four strategies before any save',async()=>{
   await p.locator('#load-local-research-report').click();await p.locator('#investment-report-confirm').waitFor({state:'visible'});
   assert.equal((await stateOf(p)).planning.reports.length,0);assert.match(await p.locator('#investment-report-preview').textContent(),/2025-12-31/);
   assert.equal(await p.locator('#import-research-comparison [data-strategy]').count(),4);
   await p.locator('#investment-report-confirm').click();await p.waitForFunction(()=>!document.querySelector('#investment-report-dialog').open);
   const s=await stateOf(p);assert.equal(s.planning.reports.length,1);assert.equal(s.accounts.length,0);assert.equal(s.entries.length,0);assert.equal(s.imports.length,0);
  });
  await step('dated historical comparison shows costs splits and incomplete independent verification',async()=>{
   assert.equal(await p.locator('#research-comparison [data-strategy]').count(),4);assert.match(await p.locator('#research-health-banner').textContent(),/单|独立|未/);
   assert.match(await p.locator('#research-provenance').textContent(),/2025-12-31/);assert.match(await p.locator('#research-split').textContent(),/训练|验证|测试/);
   const r=(await stateOf(p)).planning.reports[0];assert.equal(r.research.decisionAllowed,false);assert.equal(r.research.dataset.crossValidated,false);
   assert.ok(r.signals[0].basis.includes('MA20'));assert.equal(r.paperTrades.length,0);
   const benchmark=r.research.comparison.find(x=>x.strategy==='buy_hold');assert.ok(Number.isInteger(benchmark.totalReturnBps));
  });
  await step('duplicate local-report import is rejected without changing saved state',async()=>{
   const before=await stateOf(p);await p.locator('#load-local-research-report').click();await p.waitForFunction(()=>document.querySelector('#investment-report-error').textContent.includes('已导入'));
   assert.ok(await p.locator('#investment-report-confirm').isHidden());assert.deepEqual(await stateOf(p),before);await p.locator('#investment-report-dialog .icon-button').click();
  });
  await step('offline cold reopen retains research and failed new fetch never silently replaces it',async()=>{
   const before=await stateOf(p);await context.setOffline(true);await p.close();p=await context.newPage();p.on('dialog',d=>d.accept());await p.goto(base);await p.waitForFunction(()=>document.querySelector('#offline-status').textContent==='离线就绪');assert.deepEqual(await stateOf(p),before);
   await p.locator('#load-local-research-report').click();await p.waitForFunction(()=>document.querySelector('#investment-report-error').textContent.includes('离线可导入'));
   assert.ok(await p.locator('#investment-report-confirm').isHidden());assert.deepEqual(await stateOf(p),before);await p.locator('#investment-report-dialog .icon-button').click();await context.setOffline(false);
  });
  await step('research extension survives encrypted backup and fresh-device conflict-aware restore',async()=>{
   await p.locator('.bottom-nav [data-tab=settings]').click();await p.locator('#backup-password').fill(password);await p.locator('#backup-password-repeat').fill(password);
   const event=p.waitForEvent('download');await p.locator('#export-backup').click();const file=await event;backup=await fs.readFile(await file.path());saved=await stateOf(p);
   const fresh=await browser.newContext();const page=await fresh.newPage();page.on('dialog',d=>d.accept());await page.goto(base);await page.locator('.bottom-nav [data-tab=settings]').click();await page.locator('#restore-file').setInputFiles({name:'fictional-research-backup.json',mimeType:'application/json',buffer:backup});await page.locator('#restore-password').fill(password);await page.locator('#preview-restore').click();await page.locator('#restore-preview').waitFor({state:'visible'});assert.equal((await stateOf(page)).planning.reports.length,0);await page.locator('#merge-restore').click();await page.waitForFunction(()=>document.querySelector('#restore-preview').classList.contains('hidden'));assert.deepEqual(await stateOf(page),saved);await fresh.close();
  });
  await step('explicit blocked report shows observation and no invented return statistics',async()=>{
   const blocked={format:'goose-investment-report',version:1,source:'完全虚构的515450阻断回归报告',asOf:'2026-01-10',generatedAt:'2026-01-11T08:00:00.000Z',strategy:'数据未核验 / 观察',symbols:['515450.SH'],signals:[{symbol:'515450.SH',stance:'watch',basis:'完全虚构测试：单位未核验，不计算收益',risk:'无法确认历史数据的单位',quality:'low'}],backtest:null,paperTrades:[],notes:'本记录为虚构回归测试；没有真实买卖或收益'};
   await p.locator('.bottom-nav [data-tab=overview]').click();await p.locator('#open-investment-report').click();await p.locator('#investment-report-file').setInputFiles({name:'fictional-blocked.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(blocked))});await p.locator('#investment-report-preview-button').click();await p.locator('#investment-report-confirm').waitFor({state:'visible'});assert.match(await p.locator('#investment-report-preview').textContent(),/缺少回测/);await p.locator('#investment-report-dialog .icon-button').click();assert.equal((await stateOf(p)).planning.reports.length,1);
  });
  await step('phone report layout stays inside viewport and makes all results inspectable',async()=>{
   assert.ok(await p.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth&&innerWidth<=390));await p.screenshot({path:path.join(work,'v32-phone-research.png'),fullPage:true});
   await p.locator('#toast').waitFor({state:'hidden'});for(const summary of await p.locator('#research-comparison summary').all())await summary.click();await p.locator('#research-comparison').screenshot({path:path.join(work,'v32-strategy-comparison.png')});
  });
  assert.deepEqual(errors,[]);assert.deepEqual(outbound,[]);
  await fs.writeFile(path.join(work,'browser-research-results.json'),JSON.stringify({passed:tests.length,tests,unhandledErrors:errors,thirdPartyRequests:outbound},null,2));console.log(JSON.stringify({passed:tests.length,unhandledErrors:errors.length,thirdPartyRequests:outbound.length}));
 }catch(error){await p.screenshot({path:path.join(work,'v32-browser-failure.png'),fullPage:false});console.log(JSON.stringify(await p.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,quick:document.querySelector('#quick-add').getBoundingClientRect().toJSON(),nav:document.querySelector('.bottom-nav').getBoundingClientRect().toJSON(),settings:document.querySelector('.bottom-nav [data-tab=settings]').getBoundingClientRect().toJSON(),quickZ:getComputedStyle(document.querySelector('#quick-add')).zIndex,navZ:getComputedStyle(document.querySelector('.bottom-nav')).zIndex}))));throw error;}finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
