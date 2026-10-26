/* Isolated browser regression: fictional data only, existing installed Edge. */
'use strict';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const base = process.env.V3_BASE_URL || 'http://127.0.0.1:9393';
const work = path.resolve(__dirname, '../work');
const password = 'fictional-browser-test-password';
const results = [];
async function step(name, fn) { await fn(); results.push(name); console.log('PASS '+name); }
const stateOf = page => page.evaluate(async()=> (await import('/io.js')).loadState());
const p1 = {schema_version:1,as_of:'2026-01-10',transaction_history_complete:false,accounts:[{account:'fictional',broker:'完全虚构测试券商',coverage:'partial',cash:null,positions:[{symbol:'600900.SH',kind:'stock',quantity:337,available:119,broker_cost:null,cost_basis:'broker_display'},{symbol:'515450.SH',kind:'etf',quantity:7333,available:0,broker_cost:null,cost_basis:'broker_display'}]}]};
(async()=>{
 await fs.mkdir(work,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const context=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1,isMobile:true,hasTouch:true,acceptDownloads:true});
 let page=await context.newPage();const errors=[],outbound=[];
 context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
 page.on('pageerror',e=>errors.push(e.message));
 context.on('request',r=>{if(/^https?:/.test(r.url()) && !r.url().startsWith(base))outbound.push(r.url());});
 page.on('dialog',d=>d.accept());
 let backup, saved;
 try {
  await step('first install caches full app shell and four pages',async()=>{
   await page.goto(base);await page.waitForFunction(()=>document.querySelector('#offline-status').textContent==='离线就绪');
   assert.equal(await page.locator('.bottom-nav button').count(),4);assert.equal((await stateOf(page)).accounts.length,0);
   for(const tab of ['assets','entries','settings','overview']){await page.locator(`.bottom-nav [data-tab="${tab}"]`).click();assert.ok(await page.locator('#page-'+tab).isVisible());}
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  });
  await step('phone UI saves an account and quick expense with exact cents',async()=>{
   await page.locator('#empty-overview [data-action="new-account"]').click();
   await page.locator('#account-form [name="name"]').fill('虚构测试工资卡');await page.locator('#account-form [name="openingBalance"]').fill('1000.10');
   await page.locator('#account-form [type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('#account-dialog').open);
   await page.locator('#quick-add').click();await page.locator('#entry-form [name="amount"]').fill('12.34');await page.locator('#entry-form [type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('#entry-dialog').open);
   const s=await stateOf(page);assert.equal(s.accounts[0].openingBalance,'100010');assert.equal(s.entries[0].amount,'1234');assert.equal(await page.locator('#net-worth').textContent(),'¥987.76');
  });
  await step('modify and void entries preserves history and recalculates totals',async()=>{
   await page.locator('.bottom-nav [data-tab="entries"]').click();await page.locator('#entry-list [data-entry-id]').first().click();await page.locator('#entry-form [name="amount"]').fill('10.10');await page.locator('#entry-form [type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('#entry-dialog').open);
   await page.locator('#entry-list [data-entry-id]').first().click();await page.locator('#void-entry').click();await page.waitForFunction(()=>!document.querySelector('#entry-dialog').open);
   const s=await stateOf(page);assert.equal(s.entries[0].amount,'1010');assert.equal(s.entries[0].voided,true);assert.ok(s.audit.some(x=>x.action==='void'));
  });
  await step('cold reopen while offline still writes and retains records',async()=>{
   await context.setOffline(true);await page.close();page=await context.newPage();page.on('dialog',d=>d.accept());await page.goto(base);await page.waitForFunction(()=>document.querySelector('#offline-status').textContent==='离线就绪');
   await page.locator('#quick-add').click();await page.locator('#entry-kind').selectOption('income');await page.locator('#entry-form [name="amount"]').fill('0.20');await page.locator('#entry-form [type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('#entry-dialog').open);
   await page.reload();await page.waitForFunction(()=>document.querySelector('#net-worth').textContent==='¥1,000.30');assert.equal((await stateOf(page)).entries.length,2);
   await context.setOffline(false);
  });
  await step('manual P1 import remains reference-only and duplicate import is blocked',async()=>{
   await page.locator('.bottom-nav [data-tab="assets"]').click();await page.locator('#import-p1-button').click();await page.locator('#p1-file').setInputFiles({name:'fictional-p1.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(p1))});await page.locator('#preview-p1').click();await page.locator('#confirm-p1').waitFor({state:'visible'});await page.locator('#confirm-p1').click();await page.waitForFunction(()=>!document.querySelector('#p1-dialog').open);
   const s=await stateOf(page);assert.equal(s.imports.length,1);assert.equal(s.accounts.length,1);assert.equal(s.imports[0].accounts[0].cash,null);assert.ok((await page.locator('#position-list').textContent()).includes('7333'));
   await page.locator('#import-p1-button').click();await page.locator('#p1-file').setInputFiles({name:'same-fictional.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(p1,null,2))});await page.locator('#preview-p1').click();await page.waitForFunction(()=>document.querySelector('#p1-preview').textContent.includes('已导入'));assert.ok(await page.locator('#confirm-p1').isHidden());await page.locator('[data-close="p1-dialog"]').click();
  });
  await step('complete encrypted export downloads and decrypts to identical state',async()=>{
   await page.locator('.bottom-nav [data-tab="settings"]').click();await page.locator('#backup-password').fill(password);await page.locator('#backup-password-repeat').fill(password);
   const download=page.waitForEvent('download');await page.locator('#export-backup').click();const file=await download;backup=await fs.readFile(await file.path());assert.ok(!backup.toString().includes('虚构测试工资卡'));
   saved=await stateOf(page);const decoded=await page.evaluate(async({text,password})=>(await import('/io.js')).decryptBackup(text,password),{text:backup.toString(),password});assert.deepEqual(decoded,saved);
  });
  await step('fresh device validates preview then restores encrypted backup',async()=>{
   const fresh=await browser.newContext({viewport:{width:390,height:844}});const p=await fresh.newPage();p.on('dialog',d=>d.accept());await p.goto(base);await p.locator('.bottom-nav [data-tab="settings"]').click();await p.locator('#restore-file').setInputFiles({name:'fictional-backup.json',mimeType:'application/json',buffer:backup});await p.locator('#restore-password').fill(password);await p.locator('#preview-restore').click();await p.locator('#restore-preview').waitFor({state:'visible'});
   assert.equal((await stateOf(p)).accounts.length,0);await p.locator('#merge-restore').click();await p.waitForFunction(()=>document.querySelector('#restore-preview').classList.contains('hidden'));assert.deepEqual(await stateOf(p),saved);await fresh.close();
  });
  await step('wrong password and conflicting restore leave existing database unchanged',async()=>{
   const before=await stateOf(page);await page.locator('#restore-file').setInputFiles({name:'fictional-backup.json',mimeType:'application/json',buffer:backup});await page.locator('#restore-password').fill('wrong-password-for-test');await page.locator('#preview-restore').click();await page.waitForFunction(()=>document.querySelector('#toast').textContent.includes('密码错误'));assert.deepEqual(await stateOf(page),before);
   const edited=await page.evaluate(async()=>{const io=await import('/io.js');const s=await io.loadState();s.accounts[0].name='冲突虚构名称';return io.encryptBackup(s,'fictional-browser-test-password');});await page.locator('#restore-file').setInputFiles({name:'conflicting.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(edited))});await page.locator('#restore-password').fill(password);await page.locator('#preview-restore').click();await page.locator('#restore-preview').waitFor({state:'visible'});assert.ok(await page.locator('#merge-restore').isDisabled());assert.deepEqual(await stateOf(page),before);
  });
  await step('optimistic transaction blocks stale concurrent writes',async()=>{
   const other=await context.newPage();await other.goto(base);await other.waitForFunction(()=>document.querySelector('#net-worth').textContent!=='—');const old=await stateOf(other);
   await page.evaluate(async()=>{const io=await import('/io.js');const d=await import('/domain.js');let s=await io.loadState();s=d.saveEntry(s,{kind:'income',accountId:s.accounts[0].id,amount:'1'});await io.persistState(s);});
   const error=await other.evaluate(async old=>{try{await (await import('/io.js')).persistState(old);return '';}catch(e){return e.message;}},old);assert.match(error,/另一窗口/);await other.close();await page.reload();
  });
  await step('quota failure never claims save success or changes ledger',async()=>{
   const before=await stateOf(page);await page.evaluate(()=>{const original=IDBDatabase.prototype.transaction;IDBDatabase.prototype.transaction=function(names,mode,...rest){if(mode==='readwrite')throw new DOMException('fictional quota test','QuotaExceededError');return original.call(this,names,mode,...rest);};});
   await page.locator('#quick-add').click();await page.locator('#entry-form [name="amount"]').fill('1.00');await page.locator('#entry-form [type="submit"]').click();await page.waitForFunction(()=>document.querySelector('#entry-error').textContent.length>0);assert.ok(await page.locator('#entry-dialog').isVisible());assert.deepEqual(await stateOf(page),before);await page.reload();
  });
  await step('damaged current record recovers previous complete copy with visible warning',async()=>{
   await page.evaluate(async()=>{const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('goose-v3-private',2);r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});await new Promise((resolve,reject)=>{const t=db.transaction('snapshots','readwrite');const st=t.objectStore('snapshots');const r=st.get('current');r.onsuccess=()=>st.put({...r.result,checksum:'damaged-for-fictional-test'});t.oncomplete=resolve;t.onerror=()=>reject(t.error);});db.close();});await page.reload();await page.waitForFunction(()=>document.querySelector('#global-warning').textContent.includes('上一份完整副本'));assert.ok((await stateOf(page)).accounts.length>0);
  });
  await step('unknown balances remain unknown and mobile layout stays within screen',async()=>{
   await page.locator('.bottom-nav [data-tab="assets"]').click();await page.locator('#page-assets [data-action="new-account"]').click();await page.locator('#account-form [name="name"]').fill('虚构未知余额');await page.locator('#account-form [type="submit"]').click();await page.waitForFunction(()=>!document.querySelector('#account-dialog').open);await page.locator('.bottom-nav [data-tab="overview"]').click();assert.equal(await page.locator('#net-worth').textContent(),'未知');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
   await page.screenshot({path:path.join(work,'iphone-overview.png'),fullPage:true});await page.locator('.bottom-nav [data-tab="assets"]').click();await page.screenshot({path:path.join(work,'iphone-assets.png'),fullPage:true});
  });
  assert.deepEqual(errors,[],'unhandled JS errors');assert.deepEqual(outbound,[],'no third-party network requests');
  await fs.writeFile(path.join(work,'browser-results.json'),JSON.stringify({passed:results.length,tests:results,unhandledErrors:errors,thirdPartyRequests:outbound},null,2));
  console.log(JSON.stringify({passed:results.length,unhandledErrors:errors.length,thirdPartyRequests:outbound.length}));
 } finally {await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
