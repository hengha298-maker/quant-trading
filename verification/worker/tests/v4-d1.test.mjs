import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {readFile,readdir} from 'node:fs/promises';
import {createHash,createHmac,randomBytes} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {fileURLToPath} from 'node:url';
import {Miniflare,convertV4MiniflareOptions} from 'miniflare';
const root=new URL('../',import.meta.url),origin='https://goose.test',secret='r'.repeat(43),sessionSecret='s'.repeat(43),uploadSecret='u'.repeat(43);
const hash=v=>createHash('sha256').update(v).digest('hex');
const hmac=(key,v)=>createHmac('sha256',key).update(v).digest('base64url');
const canonical=v=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const TEST_TIME=new Date(new Date(Date.now()+8*3600000).toISOString().slice(0,10)+'T06:38:00.000Z'),TEST_NOW=Math.floor(+TEST_TIME/1000);
const FUTURE_DAY=new Date(+TEST_TIME+86400000).toISOString().slice(0,10);
let mf,db,csrf,cookie,lastVerified;
before(async()=>{mf=new Miniflare(convertV4MiniflareOptions({modules:[{type:'ESModule',path:fileURLToPath(new URL('test-clock.mjs',root)),contents:`import {handle} from './worker.mjs';export default {fetch(request,env){return handle(request,env,${TEST_NOW});}}`},...['worker.mjs',...(await readdir(root)).filter(n=>n.endsWith('.mjs')&&n!=='worker.mjs')].map(n=>({type:'ESModule',path:fileURLToPath(new URL(n,root))}))],modulesRoot:fileURLToPath(root),compatibilityDate:'2026-11-09',d1Databases:{REPORT_DB:'goose-v4-isolated-test'},bindings:{GOOSE_EXECUTION_OWNER:'cloud',RUNNER_SECRET:secret,SESSION_SECRET:sessionSecret,UPLOAD_SECRET:uploadSecret,VIEW_KEY_SHA256:'a'.repeat(64)},serviceBindings:{ASSETS:()=>new Response('test asset')}}));db=await mf.getD1Database('REPORT_DB');for(const name of(await readdir(new URL('migrations/',root))).filter(n=>n.endsWith('.sql')).sort()){const sql=await readFile(new URL('migrations/'+name,root),'utf8');for(const part of sql.split(';'))if(part.trim())await db.prepare(part).run();}const now=TEST_NOW,payload=Buffer.from(JSON.stringify({v:1,aud:origin,iat:now,exp:now+7*86400})).toString('base64url');cookie='__Host-goose_session='+payload+'.'+hmac(sessionSecret,payload);const r=await mf.dispatchFetch(origin+'/investment/api/v4/bootstrap',{headers:{Cookie:cookie}});assert.equal(r.status,200,await r.clone().text());csrf=(await r.json()).csrfToken;});
after(async()=>{await mf?.dispose();});
async function browser(route,body){const r=await mf.dispatchFetch(origin+'/investment/api/v4/'+route,{method:body?'POST':'GET',headers:{Cookie:cookie,Origin:origin,'Content-Type':'application/json','X-Goose-CSRF':csrf},body:body?JSON.stringify(body):undefined});return{response:r,value:await r.json()};}
function signed(route,body,nonce=randomBytes(24).toString('base64url'),method='POST'){const pathname='/api/runner/'+route,raw=body?JSON.stringify(body):'',t=String(TEST_NOW),digest=hash(raw);return{url:origin+pathname,options:{method,body:method==='POST'?raw:undefined,headers:{'Content-Type':'application/json','X-Goose-Time':t,'X-Goose-Nonce':nonce,'X-Goose-SHA256':digest,Authorization:'Goose-Runner '+hmac(secret,`${method}\n${pathname}\n${t}\n${nonce}\n${digest}`)}}};}
async function runner(route,body){const s=signed(route,body),r=await mf.dispatchFetch(s.url,s.options);return{response:r,value:await r.json()};}
function object(raw,path){const compressed=gzipSync(raw);return{path,sha256:hash(raw),byteLength:raw.length,storageEncoding:'gzip',storageSha256:hash(compressed),storageBytes:compressed.length,chunkCount:1,compressed};}
async function seed(files){const list=files.map(x=>object(Buffer.from(JSON.stringify(x.payload)),x.path)),version=(await browser('bootstrap')).value.inputVersion;let r=await runner('input-begin',{expectedBundleVersion:version,files:list.map(({compressed,...x})=>x)});assert.equal(r.response.status,200,JSON.stringify(r.value));const uploadId=r.value.uploadId;for(let i=0;i<list.length;i++){const raw=list[i].compressed;r=await runner('input-chunk-upload',{uploadId,fileIndex:i,chunkIndex:0,sha256:hash(raw),contentBase64:raw.toString('base64')});assert.equal(r.response.status,200,JSON.stringify(r.value));}r=await runner('input-commit',{uploadId});assert.equal(r.response.status,200,JSON.stringify(r.value));return r.value;}
test('actual local D1: session, CSRF, wealth CAS and append-only void',async()=>{const denied=await mf.dispatchFetch(origin+'/investment/api/v4/bootstrap');assert.equal(denied.status,401);const missing=await mf.dispatchFetch(origin+'/investment/api/v4/records',{method:'POST',headers:{Cookie:cookie,'Content-Type':'application/json'},body:'{}'});assert.equal(missing.status,403);const payload={recordType:'wealth-entry',name:'隔离合成存款',category:'deposit',amountCents:'123456',currency:'CNY',asOf:'2026-11-17'};let r=await browser('records',{recordId:'wealth:test',kind:'wealth-entry',payload,expectedRevision:0});assert.equal(r.response.status,200,JSON.stringify(r.value));r=await browser('records',{recordId:'wealth:test',kind:'wealth-entry',payload,expectedRevision:0});assert.equal(r.response.status,409);r=await browser('records',{recordId:'wealth:test',kind:'wealth-entry',action:'void',expectedRevision:1});assert.equal(r.response.status,200);assert.equal(r.value.voided,true);r=await browser('records?recordId=wealth:test&history=1');assert.equal(r.value.records.length,2);assert.equal(r.value.records[1].payload.amountCents,'123456');});
test('actual local D1: HMAC tamper and nonce replay rejected',async()=>{const body={jobKey:'daily:2026-11-18',runId:'run-a',auditSha256:'a'.repeat(64),leaseSeconds:180},s=signed('claim',body);const a=await mf.dispatchFetch(s.url,s.options);assert.equal(a.status,200,await a.clone().text());const b=await mf.dispatchFetch(s.url,s.options);assert.equal(b.status,409);s.options.headers.Authorization+='x';const c=await mf.dispatchFetch(s.url,s.options);assert.equal(c.status,401);});
test('actual local D1: cloud ownership fails closed and stale lease cannot mutate',async()=>{
 const {runnerApi}=await import('../v4-api.mjs');
 let s=signed('claim',{jobKey:'daily:2026-11-16',runId:'owner-check',auditSha256:'a'.repeat(64),leaseSeconds:180});
 let r=await runnerApi(new Request(s.url,s.options),{RUNNER_SECRET:secret,REPORT_DB:db},TEST_NOW);
 assert.equal((await r.json()).code,'CLOUD_CUTOVER_NOT_AUTHORIZED');
 const first=await runner('claim',{jobKey:'daily:2026-11-16',runId:'lease-one',auditSha256:'a'.repeat(64),leaseSeconds:180});
 await db.prepare('UPDATE v4c_jobs SET lease_until=0 WHERE job_key=?').bind('daily:2026-11-16').run();
 const second=await runner('claim',{jobKey:'daily:2026-11-16',runId:'lease-two',auditSha256:'a'.repeat(64),leaseSeconds:180});
 assert.equal(second.value.attempt,2);
 r=await runner('heartbeat',{jobKey:'daily:2026-11-16',leaseToken:first.value.leaseToken});
 assert.equal(r.response.status,409);
 r=await runner('heartbeat',{jobKey:'daily:2026-11-16',leaseToken:second.value.leaseToken});
 assert.equal(r.value.status,'renewed');
 r=await runner('state',{jobKey:'daily:2026-11-16',leaseToken:second.value.leaseToken,state:'VERIFIED'});
 assert.equal(r.response.status,409);
});
test('actual local D1: concurrent browser writes have one winner and no silent lost update',async()=>{
 const payload={recordType:'wealth-entry',name:'并发合成账户',category:'cash',amountCents:'100',currency:'CNY',asOf:'2026-11-17'};
 const attempts=await Promise.all([browser('records',{recordId:'wealth:race',kind:'wealth-entry',expectedRevision:0,payload}),browser('records',{recordId:'wealth:race',kind:'wealth-entry',expectedRevision:0,payload:{...payload,amountCents:'200'}})]);
 assert.deepEqual(attempts.map(x=>x.response.status).sort(),[200,409]);
 assert.equal((await browser('records?recordId=wealth:race&history=1')).value.records.length,1);
});
test('actual local D1: gzip corruption never advances the input generation',async()=>{
 const beforeVersion=(await browser('bootstrap')).value.inputVersion;
 const d=object(Buffer.from('{"safe":"isolated"}'),'research-events/corrupt.json');
 d.storageSha256='0'.repeat(64);
 const start=await runner('input-begin',{expectedBundleVersion:beforeVersion,files:[(({compressed,...x})=>x)(d)]});
 const put=await runner('input-chunk',{uploadId:start.value.uploadId,fileIndex:0,chunkIndex:0,sha256:hash(d.compressed),contentBase64:d.compressed.toString('base64')});
 assert.equal(put.response.status,409);
 assert.equal(put.value.error,'STORAGE_HASH_MISMATCH');
 const commit=await runner('input-commit',{uploadId:start.value.uploadId});
 assert.equal(commit.response.status,409);
 assert.equal((await browser('bootstrap')).value.inputVersion,beforeVersion);
});
test('actual local D1: confirmed holdings are runner inputs; void preserves the old generation',async()=>{
 const preview={format:'goose-holdings-preview',version:1,previewId:'preview-synthetic',generatedAt:TEST_TIME.toISOString(),asOf:'2026-11-17',asOfPrecision:'date',sourceSha256:'b'.repeat(64),source:{kind:'manual',broker:'华宝证券',fileName:'手工录入.json'},positions:[{symbol:'600900.SH',name:'隔离合成标的',quantity:100,costPrice:'10',reportedMarketValue:null,reportedPnl:null}],positionsCount:1,availableCash:null,warnings:[],requiresConfirmation:true};
 const payload={...preview,previewSha256:hash(canonical(preview)),format:'goose-holdings-snapshot',snapshotId:'synthetic-holding',confirmedAt:TEST_TIME.toISOString(),confirmation:'user-reviewed-preview'};
 let saved=await browser('records',{recordId:'holdings:synthetic-holding',kind:'holdings-snapshot',payload,expectedRevision:0});
 assert.equal(saved.response.status,200,JSON.stringify(saved.value));
 const claim=await runner('claim',{jobKey:'daily:2026-11-15',runId:'holdings-before-void',auditSha256:'a'.repeat(64),leaseSeconds:180});
 const common={jobKey:'daily:2026-11-15',leaseToken:claim.value.leaseToken};
 const old=(await runner('inputs',common)).value;
 const item=old.files.find(x=>x.path==='private/holdings/synthetic-holding.json');
 assert.ok(item);
 assert.equal(item.sha256,saved.value.sha256);
 saved=await browser('records',{recordId:'holdings:synthetic-holding',kind:'holdings-snapshot',action:'void',expectedRevision:1});
 assert.equal(saved.response.status,200);
 const latest=(await browser('bootstrap')).value;
 assert.equal(latest.records.find(x=>x.recordId==='holdings:synthetic-holding').voided,true);
 assert.ok(latest.inputVersion>old.bundleVersion);
 const read=await runner('input-chunk',{...common,bundleVersion:old.bundleVersion,objectId:item.objectId,chunkIndex:0});
 assert.equal(read.response.status,200);
 assert.equal(hash(Buffer.from(read.value.contentBase64,'base64')),item.sha256);
});
test('actual local D1: imported source bytes become historical browser records idempotently',async()=>{
 const previous=(await browser('records?recordId=holdings:synthetic-holding&history=1')).value.records[0].payload;
 const payload={...previous,snapshotId:'imported-synthetic-holding'};
 const imported=await seed([{path:'private/holdings/imported-synthetic-holding.json',payload}]);
 let result=await runner('input-materialize',{expectedBundleVersion:imported.bundleVersion});
 assert.equal(result.response.status,200,JSON.stringify(result.value));
 assert.equal(result.value.recordsAdded,1);
 result=await runner('input-materialize',{expectedBundleVersion:imported.bundleVersion});
 assert.equal(result.value.recordsAdded,0);
 const record=(await browser('records?recordId=holdings:imported-synthetic-holding')).value.records[0];
 assert.equal(record.dataStatus,'historical');
 assert.equal(record.payload.availableCash,null);
 assert.equal(record.sha256,hash(Buffer.from(JSON.stringify(payload))));
});
test('actual local D1: gzip input hydration, immutable report, complete readback and one-shot outbox',async()=>{await seed([{path:'research-events/synthetic.json',payload:{format:'isolated-test-evidence',value:1}}]);const day=FUTURE_DAY,claim=await runner('claim',{jobKey:'daily:'+day,runId:'run-b',auditSha256:'a'.repeat(64),leaseSeconds:180});assert.equal(claim.value.status,'claimed');const common={jobKey:'daily:'+day,leaseToken:claim.value.leaseToken};const inputs=await runner('inputs',common);assert.ok(inputs.value.files.some(x=>x.path==='research-events/synthetic.json'));const chunk=await runner('input-chunk',{...common,bundleVersion:inputs.value.bundleVersion,objectId:inputs.value.files[0].objectId,chunkIndex:0});assert.equal(chunk.response.status,200);const report={format:'goose-live-research',version:1,id:'isolated-report-b',asOf:day,generatedAt:day+'T06:38:00.000Z',portfolio:{positions:[]}},raw=Buffer.from(JSON.stringify({format:'goose-private-cloud-snapshot',version:1,report})),desc=object(raw),start=await runner('upload-begin',{...common,reportId:report.id,sha256:hash(raw),expectedBundleVersion:inputs.value.bundleVersion,report:(({compressed,...x})=>x)(desc),artifacts:[]});assert.equal(start.response.status,200,JSON.stringify(start.value));const uploadId=start.value.uploadId;let r=await runner('upload-chunk',{...common,uploadId,fileIndex:0,chunkIndex:0,sha256:hash(desc.compressed),contentBase64:desc.compressed.toString('base64')});assert.equal(r.response.status,200);r=await runner('upload-commit',{...common,uploadId});assert.equal(r.response.status,400);assert.equal(r.value.error,'REPORT_PROVENANCE_REJECTED');
// A genuinely current dated synthetic payload is only used in isolated local D1.
const now=TEST_TIME,today=new Date(+now+8*3600000).toISOString().slice(0,10);const c=await runner('claim',{jobKey:'daily:'+today,runId:'run-current',auditSha256:'a'.repeat(64),leaseSeconds:180});if(c.value.status!=='claimed'){await db.prepare("UPDATE v4c_jobs SET status='failed',lease_until=0 WHERE job_key=?").bind('daily:'+today).run();}const c2=c.value.status==='claimed'?c:await runner('claim',{jobKey:'daily:'+today,runId:'run-current2',auditSha256:'a'.repeat(64),leaseSeconds:180}),co={jobKey:'daily:'+today,leaseToken:c2.value.leaseToken};const inp=await runner('inputs',co),rr={...report,id:'isolated-current',asOf:today,generatedAt:now.toISOString(),instruments:[{symbol:'600900.SH',quote:{symbol:'600900.SH',source:'tencent-quote',adjustment:'none',currency:'CNY',priceUnit:'CNY-per-share',price:'10',sourceTimestamp:now.toISOString(),fetchedAt:now.toISOString(),quality:{status:'intraday-verified',unitVerified:true,intradaySignalAllowed:true}}}]},bytes=Buffer.from(JSON.stringify({format:'goose-private-cloud-snapshot',version:1,report:rr})),d=object(bytes),st=await runner('upload-begin',{...co,reportId:rr.id,sha256:hash(bytes),expectedBundleVersion:inp.value.bundleVersion,report:(({compressed,...x})=>x)(d),artifacts:[]});assert.equal(st.response.status,200,JSON.stringify(st.value));await runner('upload-chunk',{...co,uploadId:st.value.uploadId,fileIndex:0,chunkIndex:0,sha256:hash(d.compressed),contentBase64:d.compressed.toString('base64')});r=await runner('upload-commit',{...co,uploadId:st.value.uploadId});assert.equal(r.response.status,200,JSON.stringify(r.value));await runner('state',{...co,state:'GENERATED'});await runner('state',{...co,state:'UPLOADING'});const s=signed('reports/'+rr.id,null,undefined,'GET'),read=await mf.dispatchFetch(s.url,s.options);assert.equal(read.status,200);assert.deepEqual(Buffer.from(await read.arrayBuffer()),bytes);r=await runner('state',{...co,state:'VERIFIED',reportId:rr.id,sha256:hash(bytes),readbackSha256:hash(bytes),readbackProof:hmac(secret,`READBACK
${rr.id}
${hash(bytes)}`)});assert.equal(r.response.status,409);r=await runner('state',{...co,state:'VERIFIED',reportId:rr.id,sha256:hash(bytes),readbackSha256:hash(bytes),readbackProof:read.headers.get('X-Goose-Readback-Proof')});assert.equal(r.response.status,200,JSON.stringify(r.value));const n=await runner('notification-claim',{...co,reportId:rr.id,sha256:hash(bytes)});assert.equal(n.value.status,'claimed',JSON.stringify(n.value));r=await runner('notification-claim',{...co,reportId:rr.id,sha256:hash(bytes)});assert.equal(r.value.status,'not-claimed-no-resend');r=await runner('notification-result',{...co,reportId:rr.id,sha256:hash(bytes),notificationToken:n.value.notificationToken,result:{sent:null,httpStatus:null}});assert.equal(r.value.status,'unknown');lastVerified={...co,reportId:rr.id,sha256:hash(bytes)};const boot=(await browser('bootstrap')).value;assert.equal(boot.report.report.id,rr.id);});

test('actual local D1: 14:44 server cutoff rejects notification even with an unexpired lease',async()=>{
 const {runnerApi}=await import('../v4-api.mjs');
 await db.prepare('UPDATE v4c_jobs SET lease_until=? WHERE job_key=?').bind(TEST_NOW+1000,lastVerified.jobKey).run();
 const request=signed('notification-claim',lastVerified),t=String(TEST_NOW+360),headers=request.options.headers;
 headers['X-Goose-Time']=t;headers.Authorization='Goose-Runner '+hmac(secret,`POST\n/api/runner/notification-claim\n${t}\n${headers['X-Goose-Nonce']}\n${headers['X-Goose-SHA256']}`);
 const response=await runnerApi(new Request(request.url,request.options),{RUNNER_SECRET:secret,SESSION_SECRET:sessionSecret,REPORT_DB:db,GOOSE_EXECUTION_OWNER:'cloud'},TEST_NOW+360);
 assert.equal(response.status,409);assert.equal((await response.json()).error,'NOTIFICATION_OUTSIDE_ORIGINAL_WINDOW');
});
