/* V3.1 integration uses fresh contexts and fictional wealth only. */
'use strict';
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const base=process.env.V3_BASE_URL||'http://127.0.0.1:9393';
const work=path.resolve(__dirname,'../work');
const password='fictional-v31-backup-password';
const results=[];
const stateOf=p=>p.evaluate(async()=>(await import('/io.js')).loadState());
async function closed(page,id){await page.waitForFunction(id=>!document.getElementById(id).open,id);}
async function step(name,fn){await fn();results.push(name);console.log('PASS '+name);}
(async()=>{
  await fs.mkdir(work,{recursive:true});
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,acceptDownloads:true});
  let page=await context.newPage();const errors=[],outbound=[];
  context.on('page',p=>{p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());});
  page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());
  context.on('request',r=>{if(/^https?:/.test(r.url())&&!r.url().startsWith(base))outbound.push(r.url());});
  let backup,saved;
  try{
    await step('upgrades a genuine v1 IndexedDB record without losing ledger or original checksum',async()=>{
      await page.goto(base+'/icons/icon.svg');
      await page.evaluate(async()=>{
        const d=await import('/domain.js'),io=await import('/io.js'),p=await import('/planning.js');
        const payload=d.saveAccount(d.emptyState(),{name:'虚构旧V3工资卡',type:'bank',openingBalance:'100010',openingDate:p.localToday(),note:'虚构兼容测试'});delete payload.planning;
        const checksum=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(io.canonical(payload))))).map(x=>x.toString(16).padStart(2,'0')).join('');
        const db=await new Promise((resolve,reject)=>{const r=indexedDB.open('goose-v3-private',1);r.onupgradeneeded=()=>r.result.createObjectStore('snapshots',{keyPath:'id'});r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});
        await new Promise((resolve,reject)=>{const t=db.transaction('snapshots','readwrite');t.objectStore('snapshots').put({id:'current',revision:1,checksum,payload});t.oncomplete=resolve;t.onerror=()=>reject(t.error);});db.close();
      });
      await page.goto(base);await page.waitForFunction(()=>document.querySelector('#offline-status').textContent==='离线就绪');
      const s=await stateOf(page);assert.equal(s.accounts[0].name,'虚构旧V3工资卡');assert.equal(s.accounts[0].openingBalance,'100010');assert.equal(s.planning.schemaVersion,1);
      assert.equal(await page.evaluate(()=>new Promise(resolve=>{const r=indexedDB.open('goose-v3-private',1);r.onerror=()=>resolve(r.error.name);r.onsuccess=()=>{r.result.close();resolve('unsafe');};})),'VersionError');
    });
    await step('investment-first home clearly identifies missing data and does not fabricate holdings',async()=>{
      assert.equal(await page.locator('#overview-title').textContent(),'投资有据，决策有数');assert.match(await page.locator('#daily-decision-summary').textContent(),/缺|暂无|missing/);
      assert.equal((await stateOf(page)).imports.length,0);assert.equal((await stateOf(page)).planning.reports.length,0);
      assert.equal(await page.locator('#page-overview > #investment-research + #investment-overview').count(),1);
      assert.equal(await page.locator('.bottom-nav button').count(),4);
    });
    await step('phone snapshot form saves complete assets and debt without adding ledger or holdings twice',async()=>{
      await page.locator('#investment-new-snapshot').click();
      for(const [index,name,type,balance] of [[0,'虚构存款','bank','1000.10'],[1,'虚构证券总额','securities','2000'],[2,'虚构借款','loan','300.20']]){
        if(index)await page.locator('#snapshot-add-item').click();const r=page.locator('#snapshot-items .investment-snapshot-item').nth(index);
        await r.locator('[data-snapshot-field=name]').fill(name);await r.locator('[data-snapshot-field=type]').selectOption(type);await r.locator('[data-snapshot-field=balance]').fill(balance);
      }
      await page.locator('#save-snapshot').click();await closed(page,'snapshot-dialog');
      assert.equal(await page.locator('.investment-hero-money').textContent(),'¥2,699.90');assert.equal(await page.locator('#net-worth').textContent(),'¥1,000.10');
      const s=await stateOf(page);assert.equal(s.planning.snapshots.length,1);assert.equal(s.accounts.length,1);assert.equal(s.entries.length,0);
    });
    await step('unknown snapshot remains unknown, revisions persist and history is visible',async()=>{
      await page.locator('#investment-new-snapshot').click();await page.locator('[data-snapshot-field=balance]').nth(1).fill('');await page.locator('#save-snapshot').click();await closed(page,'snapshot-dialog');assert.equal(await page.locator('.investment-hero-money').textContent(),'未知');
      await page.locator('.bottom-nav [data-tab=assets]').click();await page.locator('#snapshot-list [data-snapshot-id]').first().getByRole('button',{name:'修订',exact:true}).click();await page.locator('[data-snapshot-field=balance]').nth(1).fill('2000');await page.locator('#save-snapshot').click();await closed(page,'snapshot-dialog');
      assert.equal((await stateOf(page)).planning.snapshots.length,3);assert.match(await page.locator('#snapshot-list').textContent(),/已修订/);await page.locator('.bottom-nav [data-tab=overview]').click();assert.equal(await page.locator('.investment-hero-money').textContent(),'¥2,699.90');
    });
    await step('salary with unknown cash saves draft but cannot confirm or pretend unknown is zero',async()=>{
      await page.locator('#new-salary-plan').click();await page.locator('#salary-receipt').fill('500.25');assert.ok(await page.locator('#salary-confirm-plan').isDisabled());assert.match(await page.locator('#salary-allocation-preview').textContent(),/未知/);await page.locator('#salary-save-draft').click();await closed(page,'salary-dialog');
      const p=(await stateOf(page)).planning.salaryPlans[0];assert.equal(p.status,'draft');assert.equal(p.input.cashBefore,null);assert.equal(p.receiptApplied,null);
    });
    await step('salary conditions calculate exact bounded allocations, confirm plan but do not move money',async()=>{
      await page.locator('#new-salary-plan').click();await page.locator('#salary-receipt').fill('500.25');await page.locator('#salary-use-snapshot').click();
      for(const [key,value] of Object.entries({emergency:'400',fixed:'100',future:'0',repayment:'50',debtBalance:'300.20',debtRate:'3.5',investment:'100',savings:'50',free:'0'}))await page.locator('#salary-'+key).fill(value);
      const date=await page.locator('#salary-date').inputValue();await page.locator('#salary-debtDue').fill(date);assert.ok(await page.locator('#salary-confirm-plan').isEnabled());assert.match(await page.locator('#salary-allocation-preview').textContent(),/1,500.35/);
      const before=await stateOf(page);await page.locator('#salary-confirm-plan').click();await closed(page,'salary-dialog');const after=await stateOf(page);const p=after.planning.salaryPlans.at(-1);assert.equal(p.status,'confirmed');assert.equal(p.allocations.debt,'5000');assert.ok(Object.values(p.allocations).reduce((n,x)=>n+BigInt(x),0n)<=150035n);assert.deepEqual(after.planning.snapshots,before.planning.snapshots);assert.deepEqual(after.entries,before.entries);
    });
    await step('explicit salary receipt creates one cash increase only and retains all other balances',async()=>{
      const before=await stateOf(page);const prior=(await page.evaluate(async()=>(await import('/planning.js')).latestSnapshot(await (await import('/io.js')).loadState())));
      await page.locator('[data-plan-id]').filter({hasText:'已确认计划'}).getByRole('button',{name:'我已收到款项，核实入账'}).click();await page.locator('#receipt-confirm').check();await page.locator('#confirm-salary-receipt').click();await closed(page,'salary-receipt-dialog');
      const after=await stateOf(page),snap=after.planning.snapshots.at(-1);assert.equal(snap.items[0].balance,'150035');assert.deepEqual(snap.items.slice(1),prior.items.slice(1));assert.deepEqual(after.entries,before.entries);assert.equal(snap.replacesId,prior.id);assert.ok(after.planning.salaryPlans.at(-1).receiptApplied);
      await page.getByRole('button',{name:'收入已记录为快照'}).click();assert.equal((await stateOf(page)).planning.snapshots.length,after.planning.snapshots.length);
    });
    await step('dated sourced report import displays historical evidence, costs and paper fills with stale warning',async()=>{
      const report={format:'goose-investment-report',version:1,source:'完全虚构的回归测试报告',asOf:'2026-01-10',generatedAt:'2026-01-11T08:00:00.000Z',strategy:'虚构样例策略',symbols:['600900.SH','515450.SH'],signals:[{symbol:'600900.SH',stance:'watch',basis:'完全虚构测试依据',risk:'完全虚构测试风险',quality:'low'}],backtest:{from:'2025-01-01',to:'2026-01-10',returnBps:123,maxDrawdownBps:456,commissionBps:3,slippageBps:5,tradeCount:2,limitations:'所有结果为虚构测试，不能作为投资判断'},paperTrades:[{date:'2026-01-09',symbol:'600900.SH',side:'buy',quantity:100,price:'1234',totalCost:'123900'}],notes:'完全虚构；不用于真实建议'};
      await page.locator('#open-investment-report').click();await page.locator('#investment-report-file').setInputFiles({name:'fictional-report.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(report))});await page.locator('#investment-report-preview-button').click();await page.locator('#investment-report-confirm').waitFor({state:'visible'});assert.equal((await stateOf(page)).planning.reports.length,0);await page.locator('#investment-report-confirm').click();await closed(page,'investment-report-dialog');
      assert.match(await page.locator('#investment-research').textContent(),/已陈旧/);assert.match(await page.locator('#investment-research').textContent(),/佣金 0.03%/);assert.match(await page.locator('#investment-research').textContent(),/来源自评/);
      await page.locator('#open-investment-report').click();await page.locator('#investment-report-file').setInputFiles({name:'same.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(report))});await page.locator('#investment-report-preview-button').click();await page.waitForFunction(()=>document.querySelector('#investment-report-error').textContent.includes('已导入'));assert.ok(await page.locator('#investment-report-confirm').isHidden());await page.locator('#investment-report-dialog .icon-button').click();
    });
    await step('daily report and AI review export only on explicit download, preserve fact/inference boundaries',async()=>{
      let event=page.waitForEvent('download');await page.locator('#export-daily-decision').click();let file=await event;const daily=JSON.parse(await fs.readFile(await file.path(),'utf8'));assert.equal(daily.status,'historical');assert.equal(daily.sourceReport.asOf,'2026-01-10');assert.ok(daily.warnings.length);
      event=page.waitForEvent('download');await page.locator('#export-review-package').click();file=await event;const ai=JSON.parse(await fs.readFile(await file.path(),'utf8'));assert.deepEqual(ai.modelInferences,[]);assert.deepEqual(ai.actionSuggestions,[]);assert.equal(ai.facts.reports.length,1);
    });
    await step('new assets and plans survive offline cold reopen and further snapshot editing',async()=>{
      const before=await stateOf(page);await context.setOffline(true);await page.close();page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));page.on('dialog',d=>d.accept());await page.goto(base);await page.waitForFunction(()=>document.querySelector('#offline-status').textContent==='离线就绪');assert.deepEqual(await stateOf(page),before);
      await page.locator('#investment-new-snapshot').click();await page.locator('[data-snapshot-field=balance]').nth(0).fill('1500.36');await page.locator('#save-snapshot').click();await closed(page,'snapshot-dialog');await page.reload();assert.equal((await stateOf(page)).planning.snapshots.at(-1).items[0].balance,'150036');await context.setOffline(false);
    });
    await step('encrypted v2 full backup restores all V3.1 data and refuses planning conflicts',async()=>{
      await page.locator('.bottom-nav [data-tab=settings]').click();await page.locator('#backup-password').fill(password);await page.locator('#backup-password-repeat').fill(password);const event=page.waitForEvent('download');await page.locator('#export-backup').click();const file=await event;backup=await fs.readFile(await file.path());assert.equal(JSON.parse(backup).version,2);saved=await stateOf(page);
      const fresh=await browser.newContext({viewport:{width:390,height:844}});const p=await fresh.newPage();p.on('dialog',d=>d.accept());await p.goto(base);await p.locator('.bottom-nav [data-tab=settings]').click();await p.locator('#restore-file').setInputFiles({name:'fictional-backup.json',mimeType:'application/json',buffer:backup});await p.locator('#restore-password').fill(password);await p.locator('#preview-restore').click();await p.locator('#restore-preview').waitFor({state:'visible'});assert.equal((await stateOf(p)).planning.snapshots.length,0);await p.locator('#merge-restore').click();await p.waitForFunction(()=>document.querySelector('#restore-preview').classList.contains('hidden'));assert.deepEqual(await stateOf(p),saved);
      const conflict=await p.evaluate(async({password})=>{const io=await import('/io.js'),s=await io.loadState();s.planning.snapshots[0].note='虚构冲突';return io.encryptBackup(s,password);},{password});await p.locator('#restore-file').setInputFiles({name:'conflict.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(conflict))});await p.locator('#restore-password').fill(password);await p.locator('#preview-restore').click();await p.locator('#restore-preview').waitFor({state:'visible'});assert.ok(await p.locator('#merge-restore').isDisabled());assert.deepEqual(await stateOf(p),saved);await fresh.close();
    });
    await step('watchlist edits never create holdings and iPhone layout remains usable',async()=>{
      await page.locator('.bottom-nav [data-tab=overview]').click();await page.locator('#investment-watch-details summary').click();await page.locator('#watch-symbol').fill('510300.SH');await page.locator('#watch-name').fill('虚构关注');await page.locator('#watch-form [type=submit]').click();await page.waitForFunction(()=>document.querySelector('#investment-research').textContent.includes('虚构关注'));assert.equal((await stateOf(page)).imports.length,0);
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(work,'v31-iphone-decision.png'),fullPage:true});await page.locator('.bottom-nav [data-tab=assets]').click();await page.screenshot({path:path.join(work,'v31-iphone-assets.png'),fullPage:true});
    });
    assert.deepEqual(errors,[]);assert.deepEqual(outbound,[]);
    await fs.writeFile(path.join(work,'browser-v31-results.json'),JSON.stringify({passed:results.length,tests:results,unhandledErrors:errors,thirdPartyRequests:outbound},null,2));console.log(JSON.stringify({passed:results.length,unhandledErrors:errors.length,thirdPartyRequests:outbound.length}));
  }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
