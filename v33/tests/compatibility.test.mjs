// Entirely fictional CLI replay, isolated state; never reads browser/user data.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {emptyState} from '../../v3/web/domain.js';
import {saveReport} from '../../v3/web/planning.js';
import {encryptBackup,decryptBackup,canonical} from '../../v3/web/io.js';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const work=path.join(root,'v33/work');fs.mkdirSync(work,{recursive:true});
const temp=fs.mkdtempSync(path.join(work,'compat-fiction-'));
const python=process.env.GOOSE_TEST_PYTHON||(process.platform==='win32'?path.resolve(root,'../Runtime/Research32/Scripts/python.exe'):'python');
const run=spawnSync(python,['-I','-B',path.join(root,'v33/run.py'),'replay','--input',path.join(root,'v33/fixtures/fictional-observations.json'),'--out',path.join(temp,'run')],{cwd:root,encoding:'utf8'});
assert.equal(run.status,2,run.stderr);
const report=JSON.parse(fs.readFileSync(path.join(temp,'run/goose-report-v31.json'),'utf8'));

test('actual CLI observation export fits existing strict report schema and deduplicates',()=>{
 const state=saveReport(emptyState(),report);
 assert.equal(state.planning.reports.length,1);assert.equal(report.signals[0].stance,'watch');
 assert.equal(report.signals[0].quality,'low');assert.equal(report.backtest,null);assert.deepEqual(report.paperTrades,[]);
 assert.ok(!Object.hasOwn(report,'research'));assert.match(report.source,/虚构|离线/);
 assert.match(report.generatedAt,/\.\d{3}Z$/);assert.throws(()=>saveReport(state,report),/已导入/);
});

test('new observation report survives existing authenticated backup without changing private schema',async()=>{
 const state=saveReport(emptyState(),report),before=canonical(state);
 const password='fictional-compatibility-password-only';
 const envelope=await encryptBackup(state,password);
 assert.equal(envelope.version,2);assert.equal(canonical(await decryptBackup(envelope,password)),before);
 await assert.rejects(decryptBackup(envelope,'wrong-fictional-password'));
 assert.equal(canonical(state),before);
});
