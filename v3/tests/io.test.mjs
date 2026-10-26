import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyState, saveAccount, saveEntry} from '../web/domain.js';
import {emptyPlanning, saveSnapshot, addWatch, saveReport, saveSalaryPlan} from '../web/planning.js';
import {canonical,parseJsonStrict,migrateState,encryptBackup,decryptBackup,parseP1Import,applyP1Import,previewRestore,restoreMerge,restoreReplace} from '../web/io.js';

const password='fictional-test-passphrase-only';
function ledger(){let s=saveAccount(emptyState(),{name:'虚构测试银行',type:'bank',openingBalance:'100000',openingDate:'2026-01-01'});return saveEntry(s,{kind:'income',date:'2026-01-02',accountId:s.accounts[0].id,amount:'1234'});}
function p1(){return {schema_version:1,as_of:'2026-01-10',transaction_history_complete:false,accounts:[{account:'fictional',broker:'完全虚构测试券商',coverage:'partial',cash:null,positions:[{symbol:'600900.SH',kind:'stock',quantity:337,available:119,broker_cost:null,cost_basis:'broker_display'},{symbol:'515450.SH',kind:'etf',quantity:7333,available:0,broker_cost:null,cost_basis:'broker_display'}]}]};}

test('encrypted backup restores all unknowns and full audit exactly without exposing names',async()=>{
 const s=await applyP1Import(ledger(),await parseP1Import(JSON.stringify(p1())));const e=await encryptBackup(s,password);
 assert.equal(e.cipher,'AES-GCM-256');assert.equal(e.version,2);assert.equal(e.iterations,600000);assert.ok(!JSON.stringify(e).includes('虚构测试银行'));
 assert.equal(canonical(await decryptBackup(e,password)),canonical(s));assert.equal(s.imports[0].accounts[0].cash,null);assert.equal(s.imports[0].positions[0].broker_cost,null);
});
test('fresh salt and nonce are used for every export',async()=>{const s=ledger(),a=await encryptBackup(s,password),b=await encryptBackup(s,password);assert.notEqual(a.salt,b.salt);assert.notEqual(a.iv,b.iv);assert.notEqual(a.ciphertext,b.ciphertext);});
test('wrong password and changed ciphertext fail without mutating existing ledger',async()=>{
 const s=ledger(),before=canonical(s),e=await encryptBackup(s,password);await assert.rejects(decryptBackup(e,'wrong-password-long-enough'));
 const c=e.ciphertext; e.ciphertext=(c[0]==='A'?'B':'A')+c.slice(1);await assert.rejects(decryptBackup(e,password));assert.equal(canonical(s),before);
});
test('authenticated header cannot be changed and expensive KDF bounds are enforced',async()=>{
 const e=await encryptBackup(ledger(),password);await assert.rejects(decryptBackup({...e,createdAt:'2026-01-01T00:00:00Z'},password));await assert.rejects(decryptBackup({...e,iterations:999999999},password));await assert.rejects(decryptBackup({...e,version:99},password));await assert.rejects(decryptBackup({...e,iv:'AA=='},password));
});
test('short backup password, truncated file and future state are refused',async()=>{
 await assert.rejects(encryptBackup(ledger(),'123'));assert.throws(()=>migrateState({...emptyState(),schemaVersion:9}));await assert.rejects(decryptBackup('{"format":',password));
});
test('schema zero migrates losslessly and unsupported data is never silently reset',()=>{
 const v0={...ledger(),schemaVersion:0};delete v0.imports;delete v0.audit;const m=migrateState(v0);assert.equal(m.schemaVersion,1);assert.equal(m.entries[0].amount,'1234');assert.deepEqual(m.imports,[]);assert.throws(()=>migrateState({accounts:[]}));
});
test('strict parser rejects duplicate keys, prototype fields, unsafe numbers and invalid JSON',()=>{
 for(const s of ['{"a":1,"a":2}','{"__proto__":{}}','{"x":NaN}','{"x":9007199254740992}','[1,]','{"a":1}extra','{"a":"unterminated}'])assert.throws(()=>parseJsonStrict(s));
 assert.deepEqual(parseJsonStrict(' {"a":[true,null,"中\\\"文"]} '),{a:[true,null,'中"文']});
});
test('restore preview finds conflicts and merge cannot overwrite them',()=>{
 const current=ledger(),incoming=structuredClone(current);incoming.accounts[0].name='另一个虚构名称';const before=canonical(current);
 assert.equal(previewRestore(current,incoming).conflicts.length,1);assert.throws(()=>restoreMerge(current,incoming));assert.equal(canonical(current),before);assert.equal(restoreReplace(current,incoming).accounts[0].name,incoming.accounts[0].name);
});
test('merge is idempotent and adds independently created accounts without losing audit',()=>{
 const a=ledger(),b=saveAccount(emptyState(),{name:'另一虚构账户',openingBalance:null,openingDate:'2026-01-01'});const m=restoreMerge(a,b);
 assert.equal(m.accounts.length,2);assert.equal(m.entries.length,1);assert.equal(canonical(restoreMerge(m,b)),canonical(m));
});
test('P1 import is a reference-only source with canonical duplicate detection',async()=>{
 const text=JSON.stringify(p1()),preview=await parseP1Import(text),s=applyP1Import(emptyState(),preview);assert.equal(s.accounts.length,0);assert.equal(s.imports[0].valuationIncluded,false);assert.equal(s.imports[0].positions[1].quantity,7333);
 await assert.rejects(async()=>applyP1Import(s,await parseP1Import(JSON.stringify(p1(),null,4))));assert.equal(s.imports.length,1);assert.equal(JSON.stringify(p1()),text);
});
test('P1 import refuses market data, unknown-to-zero corruption, dates and duplicate positions',async()=>{
 for(const mutate of [d=>d.accounts[0].positions.push(d.accounts[0].positions[0]),d=>d.as_of='2026-02-30',d=>d.accounts[0].coverage='complete',d=>d.accounts[0].positions[0].quantity=1.2,d=>d.schema_version=2,d=>d.rows=[]]){const d=p1();mutate(d);await assert.rejects(parseP1Import(JSON.stringify(d)));}
});
test('merging the same P1 fingerprint from two devices keeps one reference and both audit trails',async()=>{
 const a=applyP1Import(emptyState(),await parseP1Import(JSON.stringify(p1()))),b=applyP1Import(emptyState(),await parseP1Import(JSON.stringify(p1())));
 const m=restoreMerge(a,b);assert.equal(m.imports.length,1);assert.equal(m.audit.length,2);assert.ok(m.audit.every(x=>x.entityId===m.imports[0].id));
 const bad=structuredClone(b);bad.imports[0].positions[0].quantity=999;assert.throws(()=>restoreMerge(a,bad));
});
test('large valid ledger exports a recoverable encrypted envelope beyond 8 MiB base64',async()=>{
 const s=emptyState();s.accounts=Array.from({length:700},(_,i)=>({id:'fictional-'+i,name:'虚构大备份测试',type:'bank',openingBalance:null,openingDate:'2026-01-01',note:'x'.repeat(9990),annualRate:null,archived:false}));
 const e=await encryptBackup(s,password);assert.ok(JSON.stringify(e).length>8*1024*1024);assert.equal((await decryptBackup(JSON.stringify(e),password)).accounts.length,700);
});

// This deliberately constructs the previous release's authenticated v1 format
// directly, so compatibility is tested independently of the new exporter.
async function legacyEncryptedBackup(payload) {
 const salt=crypto.getRandomValues(new Uint8Array(16)),iv=crypto.getRandomValues(new Uint8Array(12));
 const header={format:'goose-v3-encrypted',version:1,cipher:'AES-GCM-256',kdf:'PBKDF2-SHA256',iterations:600000,salt:Buffer.from(salt).toString('base64'),iv:Buffer.from(iv).toString('base64'),createdAt:'2026-01-02T00:00:00.000Z'};
 const encoder=new TextEncoder();
 const material=await crypto.subtle.importKey('raw',encoder.encode(password),'PBKDF2',false,['deriveKey']);
 const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:header.iterations,hash:'SHA-256'},material,{name:'AES-GCM',length:256},false,['encrypt']);
 const encrypted=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:encoder.encode(canonical(header))},key,encoder.encode(canonical(payload)));
 return {...header,ciphertext:Buffer.from(encrypted).toString('base64')};
}

test('historical schema-one payload defaults planning only when absent without mutating the original',()=>{
 const old=ledger();delete old.planning;const before=canonical(old),restored=migrateState(old);
 assert.deepEqual(restored.planning,emptyPlanning());assert.equal(canonical(old),before);assert.equal(restored.entries[0].amount,'1234');
 const schemaZero={...old,schemaVersion:0};delete schemaZero.imports;delete schemaZero.audit;
 assert.deepEqual(migrateState(schemaZero).planning,emptyPlanning());
});

test('genuine encrypted v1 backups remain readable and are re-exported as authenticated v2',async()=>{
 const old=ledger();delete old.planning;const previous=await legacyEncryptedBackup(old);
 const restored=await decryptBackup(JSON.stringify(previous),password);
 assert.equal(canonical(restored),canonical(migrateState(old)));assert.deepEqual(restored.planning,emptyPlanning());
 const current=await encryptBackup(restored,password);assert.equal(current.version,2);
 assert.equal(canonical(await decryptBackup(current,password)),canonical(restored));
 await assert.rejects(decryptBackup({...current,version:1},password));
});

test('null, future and malformed planning are rejected rather than replaced with empty arrays',()=>{
 for(const invalid of [null,undefined,[],{...emptyPlanning(),schemaVersion:99},{...emptyPlanning(),snapshots:null},{...emptyPlanning(),reports:{}},{...emptyPlanning(),watchlist:'invalid'},{...emptyPlanning(),salaryPlans:null}]) {
  const current=ledger();current.planning=invalid;
  if(invalid===undefined) {
   // JSON has no undefined; an explicit in-memory undefined must not be a
   // loophole for dropping an extension before validating it.
   assert.throws(()=>migrateState(current));
  } else assert.throws(()=>migrateState(current));
 }
});

function plannedLedger() {
 const date='2026-01-02';
 let state=saveSnapshot(ledger(),{date,items:[{name:'完全虚构测试现金',type:'cash',balance:'10000'},{name:'完全虚构未知借款',type:'loan',balance:null}],note:'离线备份测试，不是真实财富数据'});
 state=addWatch(state,{symbol:'600900.SH',name:'虚构测试关注'});
 state=saveReport(state,{format:'goose-investment-report',version:1,id:'fictional-report-only',source:'完全虚构离线测试',asOf:date,generatedAt:'2026-01-02T12:00:00.000Z',strategy:'虚构测试策略',symbols:['600900.SH'],signals:[],backtest:null,paperTrades:[],notes:''});
 return saveSalaryPlan(state,{date,input:{date,cashAsOf:null,receipt:'10000',cashBefore:null,emergency:'0',fixed:'0',future:'0',investment:'0',savings:'0',free:'0',repayment:'0',debtBalance:'0',debtRate:null,debtDue:null,risk:'balanced',cashBasis:'before-receipt'},status:'draft'});
}

test('all four planning collections and unknown balances survive v2 encryption and full restore exactly',async()=>{
 const state=plannedLedger(),before=canonical(state),encrypted=await encryptBackup(state,password);
 const restored=await decryptBackup(encrypted,password);
 assert.equal(encrypted.version,2);assert.equal(canonical(restored),before);
 assert.equal(restored.planning.snapshots[0].items[1].balance,null);
 assert.equal(restored.planning.salaryPlans[0].input.cashBefore,null);
 assert.equal(canonical(restoreReplace(emptyState(),restored)),before);
 const preview=previewRestore(emptyState(),restored);
 for(const key of ['snapshots','reports','watchlist','salaryPlans'])assert.equal(preview[key],1);
 assert.equal(preview.accounts,1);assert.equal(preview.entries,1);assert.equal(preview.imports,0);assert.deepEqual(preview.conflicts,[]);
 assert.equal(canonical(state),before);
});

test('planning merge preserves every extension, is idempotent and retains it when merging an older backup',()=>{
 const existing=ledger(),incoming=plannedLedger(),original=canonical(incoming);
 const merged=restoreMerge(existing,incoming);
 assert.equal(canonical(merged.planning),canonical(incoming.planning));
 assert.equal(canonical(restoreMerge(merged,incoming)),canonical(merged));
 const old=ledger();delete old.planning;
 assert.equal(canonical(restoreMerge(merged,old).planning),canonical(merged.planning));
 assert.equal(canonical(incoming),original);
});

test('each planning collection detects same-id different-content conflicts without changing either side',()=>{
 const current=plannedLedger(),before=canonical(current);
 const changes={snapshots:x=>{x.note+=' changed';},reports:x=>{x.notes+=' changed';},watchlist:x=>{x.name+=' changed';},salaryPlans:x=>{x.input.risk='conservative';}};
 for(const [key,change] of Object.entries(changes)) {
  const incoming=structuredClone(current);change(incoming.planning[key][0]);const incomingBefore=canonical(incoming);
  const preview=previewRestore(current,incoming);
  assert.deepEqual(preview.conflicts,[{type:`planning.${key}`,id:incoming.planning[key][0].id}]);
  assert.throws(()=>restoreMerge(current,incoming),/冲突/);
  assert.equal(canonical(current),before);assert.equal(canonical(incoming),incomingBefore);
 }
});

test('duplicate planning ids, invalid item amounts and malformed records cannot enter encrypted backups or merges',async()=>{
 const mutations=[
  state=>state.planning.snapshots.push(structuredClone(state.planning.snapshots[0])),
  state=>{state.planning.snapshots[0].items[0].balance=10000;},
  state=>{state.planning.reports[0].version=99;},
  state=>{state.planning.watchlist[0].symbol='unverified';},
  state=>{state.planning.salaryPlans[0].input.cashBefore=0;},
 ];
 for(const mutate of mutations) {
  const state=plannedLedger();mutate(state);const before=canonical(state);
  assert.throws(()=>migrateState(state));assert.throws(()=>restoreMerge(ledger(),state));await assert.rejects(encryptBackup(state,password));
  assert.equal(canonical(state),before);
 }
});

test('same watchlist symbol with different ids is an explicit semantic restore conflict',()=>{
 const current=plannedLedger(),incoming=structuredClone(current);
 incoming.planning.watchlist[0].id='fictional-second-watch-id';
 const before=canonical(current),incomingBefore=canonical(incoming);
 assert.deepEqual(previewRestore(current,incoming).conflicts,[{type:'planning.watchlist-symbol',id:'fictional-second-watch-id'}]);
 assert.throws(()=>restoreMerge(current,incoming),/冲突/);
 assert.equal(canonical(current),before);assert.equal(canonical(incoming),incomingBefore);
 assert.equal(canonical(restoreMerge(current,current)),before);
});

test('same report source date and strategy with different ids cannot be silently merged or discarded',()=>{
 const current=plannedLedger(),incoming=structuredClone(current);
 incoming.planning.reports[0].id='fictional-second-report-id';
 const before=canonical(current),incomingBefore=canonical(incoming);
 assert.deepEqual(previewRestore(current,incoming).conflicts,[{type:'planning.report-source',id:'fictional-second-report-id'}]);
 assert.throws(()=>restoreMerge(current,incoming),/冲突/);
 assert.equal(canonical(current),before);assert.equal(canonical(incoming),incomingBefore);
 incoming.planning.reports[0].notes='不同报告内容仍属于同来源、日期和策略';
 assert.deepEqual(previewRestore(current,incoming).conflicts,[{type:'planning.report-source',id:'fictional-second-report-id'}]);
 assert.throws(()=>restoreMerge(current,incoming),/冲突/);
 assert.equal(canonical(restoreMerge(current,current)),before);
});

test('different report source keys remain independent records rather than false semantic conflicts',()=>{
 const current=plannedLedger(),incoming=structuredClone(current);
 incoming.planning.reports[0].id='fictional-other-strategy-id';
 incoming.planning.reports[0].strategy='另一虚构测试策略';
 assert.deepEqual(previewRestore(current,incoming).conflicts,[]);
 const merged=restoreMerge(current,incoming);
 assert.equal(merged.planning.reports.length,2);
 assert.equal(canonical(restoreMerge(merged,incoming)),canonical(merged));
});
