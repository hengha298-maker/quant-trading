// Finite provider pagination, at most 96 pages per dated campaign. Not admission.
import {sessions} from './sessions.mjs';
const filters={stock:'m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23',etf:'b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827',industry:'m:90 t:2'};
const fields='f2,f3,f5,f6,f10,f12,f13,f14,f15,f16,f17,f18,f24,f62,f100,f104,f105,f124,f297';
export function parseMarket(doc,kind,day,now=new Date()){
 if(doc.rc!==0||!Number.isInteger(doc.data?.total)||doc.data.total<1||doc.data.total>10000||!Array.isArray(doc.data.diff)||doc.data.diff.length<1||doc.data.diff.length>100)throw Error('SCAN_COUNTS_REJECTED');
 const items=[],seen=new Set();
 for(const x of doc.data.diff){
  const codes=[],code=x.f12,name=x.f14,market=x.f13;
  if(typeof name!=='string'||!name.length||name.length>80||/[\x00-\x1f<>]/.test(name)||!(kind==='industry'?/^BK\d{4}$/.test(code)&&market===90:/^\d{6}$/.test(code)&&[0,1].includes(market)))throw Error('SCAN_IDENTITY_REJECTED');
  const symbol=kind==='industry'?code:code+(market===1?'.SH':'.SZ');if(seen.has(symbol))throw Error('SCAN_DUPLICATE_SYMBOL');seen.add(symbol);
  const supported=kind==='industry'||kind==='stock'&&(market===1?/^(600|601|603|605|688)/.test(code):/^(000|001|002|003|300|301)/.test(code))||kind==='etf'&&(market===1?/^5/.test(code):/^(15|16)/.test(code));
  if(!supported||kind==='stock'&&(/ST|退/i.test(name)||/^[NC]/.test(name)))codes.push('SECURITY_OR_RISK_WARNING_REVIEW');
  const timestamp=Number.isInteger(x.f124)&&x.f124>=946684800&&x.f124<=4102444800?new Date(x.f124*1000):null;
  const sourceDay=timestamp?new Date(+timestamp+8*3600000).toISOString().slice(0,10):null;
  if(!timestamp||sourceDay!==day||String(x.f297)!==day.replaceAll('-','')||!sessions.includes(sourceDay)||+timestamp>+now+5000)codes.push('SOURCE_DATE_OR_TIME_REJECTED');
  const nums=['f2','f18','f15','f16','f17','f3','f5','f6'].map(k=>typeof x[k]==='number'&&Number.isFinite(x[k])&&Math.abs(x[k])<=1e18?x[k]:null);
  const [price,previousClose,high,low,open,changePct,volume,amountCNY]=nums;
  if(nums.some(n=>n===null)||[price,previousClose,high,low,open,volume,amountCNY].some(n=>n<=0)||!Number.isInteger(volume)||low>Math.min(price,open)||high<Math.max(price,open)||Math.abs((price/previousClose-1)*100-changePct)>.02)codes.push('PRICE_VOLUME_OHLC_OR_SCALE_REJECTED');
  const tick=kind==='etf'?1000:100;if(kind!=='industry'&&[price,previousClose,high,low,open].some(v=>v===null||Math.abs(v*tick-Math.round(v*tick))>1e-7))codes.push('RAW_TICK_REJECTED');
  items.push({symbol,name,kind,price,previousClose,changePct,amountCNY,volumeLots:volume,sourceTimestamp:timestamp?.toISOString()||null,dataDate:sourceDay,adjustment:'none',source:'eastmoney-delayed-market-discovery',codes,fieldValidated:!codes.length,historyValidated:false,tradeAdmitted:false});
 }
 return {total:doc.data.total,items};
}
async function page(env,day,kind,p){
 const query=new URLSearchParams({pn:p,pz:100,po:1,np:1,ut:'bd1d9ddb04089700cf9c27f6f7426281',fltt:2,invt:2,fid:'f12',fs:filters[kind],fields});
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);let value;
 try{
  const response=await fetch('https://push2delay.eastmoney.com/api/qt/clist/get?'+query,{signal:controller.signal,redirect:'manual'});if(!response.ok){const e=Error('SCAN_HTTP_FAILED');e.httpStatus=response.status;throw e;}
  const raw=await response.text();if(raw.length>150000)throw Error('SCAN_PAGE_TOO_LARGE');
  const rawSha256=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw))),b=>b.toString(16).padStart(2,'0')).join('');
  value={status:'success',kind,page:p,fetchedAt:new Date().toISOString(),rawSha256,...parseMarket(JSON.parse(raw),kind,day)};
 }catch(e){value={status:'failed',kind,page:p,fetchedAt:new Date().toISOString(),code:/^[A-Z_]+$/.test(e.message)?e.message:'SCAN_NETWORK_OR_PARSE_FAILED',failureType:e.name,httpStatus:e.httpStatus||null};}finally{clearTimeout(timer);}
 await env.REPORT_DB.prepare('INSERT OR IGNORE INTO cloud_scan_pages(day,kind,page,status,body) VALUES (?,?,?,?,?)').bind(day,kind,p,value.status,JSON.stringify(value)).run();return value;
}
export async function scanBatch(env,kind='scheduled',clock=new Date()){
 const local=new Date(+clock+8*3600000).toISOString(),day=local.slice(0,10),minutes=Number(local.slice(11,13))*60+Number(local.slice(14,16));
 if(day>sessions.at(-1))return {status:'blocked-calendar'};
 const campaign=sessions.filter(d=>d<day||d===day&&minutes>=570).at(-1);if(!campaign)return {status:'blocked-calendar'};
 const count=await env.REPORT_DB.prepare('SELECT COUNT(*) AS n FROM cloud_scan_pages').first();if(count.n>=5000)return {status:'storage-budget-stop'};
 let plan=await env.REPORT_DB.prepare('SELECT jobs FROM cloud_scan_plan WHERE day=?').bind(campaign).first();let results=[];
 if(!plan){
  const seeds=await Promise.all(Object.keys(filters).map(async k=>{const old=await env.REPORT_DB.prepare('SELECT body FROM cloud_scan_pages WHERE day=? AND kind=? AND page=1').bind(campaign,k).first();return old?JSON.parse(old.body):page(env,campaign,k,1);}));results.push(...seeds);
  if(seeds.some(p=>p.status!=='success'))return {status:'failed-seed',campaign,pages:seeds.map(({items,...v})=>v)};
  const jobs=seeds.flatMap(s=>Array.from({length:Math.ceil(s.total/100)},(_,i)=>({kind:s.kind,page:i+1,total:s.total})));
  if(jobs.length>96)return {status:'page-budget-exceeded',campaign,pagesRequired:jobs.length};
  await env.REPORT_DB.prepare('INSERT OR IGNORE INTO cloud_scan_plan(day,jobs,created_at) VALUES (?,?,?)').bind(campaign,JSON.stringify(jobs),new Date().toISOString()).run();plan={jobs:JSON.stringify(jobs)};
 }
 const jobs=JSON.parse(plan.jobs),done=(await env.REPORT_DB.prepare('SELECT kind,page,status FROM cloud_scan_pages WHERE day=?').bind(campaign).all()).results;
 // Failed pages remain visible and are not retried endlessly in the same campaign.
 const todo=jobs.filter(j=>!done.some(p=>p.kind===j.kind&&p.page===j.page)).slice(0,Math.max(0,6-results.length));
 for(let i=0;i<todo.length;i+=3)results.push(...await Promise.all(todo.slice(i,i+3).map(j=>page(env,campaign,j.kind,j.page))));
 return {status:'batch-finished',campaign,triggerKind:kind,pagesRequired:jobs.length,processed:results.map(({items,...p})=>p),tradeCandidateCount:0};
}
export async function scanView(env){
 const plan=await env.REPORT_DB.prepare('SELECT day,jobs FROM cloud_scan_plan ORDER BY day DESC LIMIT 1').first();
 if(!plan){const failures=(await env.REPORT_DB.prepare("SELECT day,kind,page,body FROM cloud_scan_pages WHERE status='failed' ORDER BY day DESC LIMIT 3").all()).results;return {status:'no-completed-seed',failures:failures.map(f=>({day:f.day,...JSON.parse(f.body)})),tradeCandidateCount:0};}
 const jobs=JSON.parse(plan.jobs),records=(await env.REPORT_DB.prepare('SELECT kind,page,status,body FROM cloud_scan_pages WHERE day=?').bind(plan.day).all()).results;
 const coverage={},rankings={};
 for(const kind of Object.keys(filters)){
  const expected=jobs.filter(j=>j.kind===kind),pages=records.filter(p=>p.kind===kind),decoded=pages.filter(p=>p.status==='success').map(p=>JSON.parse(p.body));
  const all=decoded.flatMap(p=>p.items),unique=new Map(all.map(i=>[i.symbol,i])),valid=[...unique.values()].filter(i=>i.fieldValidated);
  const complete=expected.length===decoded.length&&decoded.every(p=>p.total===expected[0]?.total)&&unique.size===expected[0]?.total;
  coverage[kind]={pagesExpected:expected.length,pagesSucceeded:decoded.length,providerTotal:expected[0]?.total,uniqueCount:unique.size,fieldUsable:valid.length,paginationComplete:complete};
  rankings[kind]=valid.sort((a,b)=>b.amountCNY-a.amountCNY).slice(0,20);
 }
 return {status:Object.values(coverage).every(c=>c.paginationComplete)?'complete-provider-pages':'partial-or-conflicting-provider-pages',asOf:plan.day,coverage,rankings,tradeCandidateCount:0,meaning:'成交活跃度研究线索，非策略评分或买入推荐；跨批次并非同时快照，历史日线未全量核验。'};
}
