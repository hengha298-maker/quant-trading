import test from 'node:test';
import assert from 'node:assert/strict';
import {emptyState, saveAccount, saveEntry} from '../web/domain.js';
import {canonical,parseJsonStrict,migrateState,encryptBackup,decryptBackup,parseP1Import,applyP1Import,previewRestore,restoreMerge,restoreReplace} from '../web/io.js';

const password='fictional-test-passphrase-only';
function ledger(){let s=saveAccount(emptyState(),{name:'虚构测试银行',type:'bank',openingBalance:'100000',openingDate:'2026-01-01'});return saveEntry(s,{kind:'income',date:'2026-01-02',accountId:s.accounts[0].id,amount:'1234'});}
function p1(){return {schema_version:1,as_of:'2026-01-10',transaction_history_complete:false,accounts:[{account:'fictional',broker:'完全虚构测试券商',coverage:'partial',cash:null,positions:[{symbol:'600900.SH',kind:'stock',quantity:337,available:119,broker_cost:null,cost_basis:'broker_display'},{symbol:'515450.SH',kind:'etf',quantity:7333,available:0,broker_cost:null,cost_basis:'broker_display'}]}]};}

test('encrypted backup restores all unknowns and full audit exactly without exposing names',async()=>{
 const s=await applyP1Import(ledger(),await parseP1Import(JSON.stringify(p1())));const e=await encryptBackup(s,password);
 assert.equal(e.cipher,'AES-GCM-256');assert.equal(e.iterations,600000);assert.ok(!JSON.stringify(e).includes('虚构测试银行'));
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
