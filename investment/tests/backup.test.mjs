import test from 'node:test';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {encryptFullBackup,decryptFullBackup} from '../web/backup.js';
import {emptyState,saveAccount} from '../../v3/web/domain.js';
import {canonical,encryptBackup} from '../../v3/web/io.js';
const cwd=fileURLToPath(new URL('../../',import.meta.url));
const password='fictional-complete-backup-password';
function python(code,input=''){
 const result=spawnSync(process.env.GOOSE_RESEARCH_PYTHON||'python',['-B','-c',code],{cwd,input,encoding:'utf8',maxBuffer:8*1024*1024,env:{...process.env,PYTHONIOENCODING:'utf-8'}});
 assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);
}
function fixture(){return python(`import sys,json
sys.path.insert(0,'investment/tests')
from test_ledger_trust import RecoveryTests
from investment import ledger_backup,ledger_accounting
t=RecoveryTests();t.setUp()
try:
 t.seed();print(json.dumps({'ledger':ledger_backup.export_state(t.root),'expected':ledger_accounting.overview(t.root)}))
finally:t.tearDown()
`);}
const phone=()=>saveAccount(emptyState(),{name:'FICTIONAL-PRIVATE-BANK',type:'bank',openingBalance:'123400',openingDate:'2026-01-01',note:''});
test('encrypted complete backup actually restores and reconciles the Python journal',async()=>{
 const {ledger,expected}=fixture(),state=phone(),before=canonical(state);
 const encrypted=await encryptFullBackup(state,ledger,password),encoded=JSON.stringify(encrypted);
 for(const marker of ['FICTIONAL-PRIVATE-BANK','华宝证券','600900','broker-events','carryingCost'])assert.ok(!encoded.includes(marker));
 const decoded=await decryptFullBackup(encoded,password);assert.equal(canonical(decoded.phoneState),before);assert.equal(decoded.ledger.manifestSha256,ledger.manifestSha256);
 const restored=python(`import json,sys,tempfile
from pathlib import Path
from investment import ledger_backup,ledger_accounting
parent=Path('investment/work/crypto-recovery-tests');parent.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(dir=parent) as folder:
 root=Path(folder);state=json.load(sys.stdin)
 preview=ledger_backup.preview_restore(root,state);ledger_backup.restore(root,preview,True)
 print(json.dumps({'ledger':ledger_backup.export_state(root),'result':ledger_accounting.overview(root)}))
`,JSON.stringify(decoded.ledger));
 assert.equal(restored.ledger.manifestSha256,ledger.manifestSha256);assert.deepEqual(restored.result,expected);
 assert.equal(restored.result.accounts[0].returns.accountTotalPnl,'483.00');
});
test('wrong password, tampered metadata and unsupported version fail closed',async()=>{
 const {ledger}=fixture(),encrypted=await encryptFullBackup(phone(),ledger,password);
 await assert.rejects(decryptFullBackup(encrypted,'another-wrong-password'));
 await assert.rejects(decryptFullBackup({...encrypted,createdAt:'2026-01-01T00:00:00Z'},password));
 await assert.rejects(decryptFullBackup({...encrypted,version:99},password));
 await assert.rejects(decryptFullBackup({...encrypted,ciphertext:encrypted.ciphertext.slice(0,-12)},password));
 await assert.rejects(encryptFullBackup(phone(),ledger,'short'));
});
test('fresh nonces, salts and legacy phone backups remain supported without inventing a server ledger',async()=>{
 const {ledger}=fixture(),state=phone(),a=await encryptFullBackup(state,ledger,password),b=await encryptFullBackup(state,ledger,password);
 assert.notEqual(a.salt,b.salt);assert.notEqual(a.iv,b.iv);assert.notEqual(a.ciphertext,b.ciphertext);
 const old=await encryptBackup(state,password),restored=await decryptFullBackup(old,password);
 assert.equal(restored.ledger,null);assert.equal(restored.scope.windowsLedger,false);assert.equal(canonical(restored.phoneState),canonical(state));
});
test('genuine previous v1 encrypted phone envelope is accepted by the new restore dispatcher',async()=>{
 const state=phone();delete state.planning;const salt=crypto.getRandomValues(new Uint8Array(16)),iv=crypto.getRandomValues(new Uint8Array(12));
 const header={format:'goose-v3-encrypted',version:1,cipher:'AES-GCM-256',kdf:'PBKDF2-SHA256',iterations:600000,salt:Buffer.from(salt).toString('base64'),iv:Buffer.from(iv).toString('base64'),createdAt:'2026-01-02T00:00:00.000Z'};
 const enc=new TextEncoder(),material=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveKey']);
 const key=await crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:600000,hash:'SHA-256'},material,{name:'AES-GCM',length:256},false,['encrypt']);
 const body=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:enc.encode(canonical(header))},key,enc.encode(canonical(state)));
 const restored=await decryptFullBackup({...header,ciphertext:Buffer.from(body).toString('base64')},password);
 assert.equal(restored.ledger,null);assert.equal(restored.phoneState.accounts[0].name,state.accounts[0].name);assert.equal(restored.phoneState.planning.schemaVersion,1);
});
