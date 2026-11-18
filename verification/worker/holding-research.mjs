// Port of complete-session MA20/60 facts. Does not replace the admission engine.
import {sessions} from './sessions.mjs';
import {trackSignals} from './signal-archive.mjs';
const SCALE=1000000n;
function fixed(s){if(!/^\d+(\.\d{1,6})?$/.test(String(s)))throw Error('PRICE_SCALE_INVALID');const [a,b='']=String(s).split('.');const n=BigInt(a)*SCALE+BigInt(b.padEnd(6,'0'));if(n<=0n)throw Error('NONPOSITIVE_PRICE');return n;}
function decimal(n,d=1n){let v=n/d;const remainder=n%d;if(remainder*2n>d||remainder*2n===d&&v%2n===1n)v++;return (v/SCALE)+'.'+(v%SCALE).toString().padStart(6,'0');}
export function facts(raw,adjusted){
 if(raw.length<60||raw.length!==adjusted.length)throw Error('HISTORY_TOO_SHORT');
 const close=raw.map(r=>fixed(r[2])),qclose=adjusted.map(r=>fixed(r[2]));
 const sum=(v,n)=>v.slice(-n).reduce((a,b)=>a+b,0n),relation=(c,s,n)=>c*BigInt(n)>s?'above':c*BigInt(n)<s?'below':'equal';
 const last=close.at(-1),r20=relation(last,sum(close,20),20),q20=relation(qclose.at(-1),sum(qclose,20),20);
 return {through:raw.at(-1)[0],completedClose:decimal(last),ma20:decimal(sum(close,20),20n),ma60:decimal(sum(close,60),60n),rawMa20Relation:r20,qfqMa20:decimal(sum(qclose,20),20n),qfqMa60:decimal(sum(qclose,60),60n),qfqMa20Relation:q20,adjustmentConflict:r20!==q20,basis:'raw prices for price facts; provider-qfq trend separate; not total return',ruleProvenance:'investment/indicators.py complete-session means; integer micro-CNY, Python Decimal default half-even six decimals; unrounded comparisons; no threshold change'};
}
async function history(symbol,basis,dates){
 const provider=symbol.slice(-2).toLowerCase()+symbol.slice(0,6),param=`${provider},day,${dates[0]},${dates.at(-1)},90,${basis==='qfq'?'qfq':''}`;
 const url='https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?'+new URLSearchParams({_var:'goose',param});
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
 try{
  const response=await fetch(url,{redirect:'manual',signal:controller.signal});if(!response.ok)throw Error('HISTORY_HTTP_FAILED');
  const text=await response.text();if(text.length>90000)throw Error('HISTORY_TOO_LARGE');
  const json=JSON.parse(text.replace(/^goose=/,'').replace(/;\s*$/,'')),data=json.data?.[provider];
  if(json.code!==0||!data||data.qt?.[provider]?.[2]!==symbol.slice(0,6))throw Error('HISTORY_IDENTITY_FAILED');
  const supplied=data[basis==='qfq'?'qfqday':'day'];
  // Existing Python adapter also filters the requested interval: this endpoint
  // may return more rows than requested. Missing in-window sessions still fail.
  const rows=Array.isArray(supplied)?supplied.filter(r=>Array.isArray(r)&&r[0]>=dates[0]&&r[0]<=dates.at(-1)):[];
  if(rows.length!==dates.length||rows.some((r,i)=>r[0]!==dates[i])){const e=Error('HISTORY_CALENDAR_GAP');e.details={symbol,basis,expectedCount:dates.length,receivedCount:rows.length,expectedFrom:dates[0],expectedThrough:dates.at(-1),receivedFrom:rows[0]?.[0]||null,receivedThrough:rows.at(-1)?.[0]||null};throw e;}
  for(const r of rows){const o=fixed(r[1]),c=fixed(r[2]),h=fixed(r[3]),l=fixed(r[4]);if(l>o||l>c||h<o||h<c||l>h||!/^\d+(\.\d+)?$/.test(String(r[5]))||Number(r[5])<=0)throw Error('HISTORY_FIELDS_FAILED');if(basis==='none'&&[o,c,h,l].some(p=>p%(symbol==='600900.SH'?10000n:1000n)!==0n))throw Error('RAW_PRICE_TICK_FAILED');}
  const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text))),b=>b.toString(16).padStart(2,'0')).join('');
  return {rows,basis,source:'tencent-public-daily',fetchedAt:new Date().toISOString(),rawSha256:sha};
 }finally{clearTimeout(timer);}
}
export async function research(env,kind='scheduled',clock=new Date()){
 const local=new Date(+clock+8*3600000).toISOString(),day=local.slice(0,10),minute=Number(local.slice(11,13))*60+Number(local.slice(14,16));
 if(day>sessions.at(-1))return {status:'blocked-calendar'};
 const dates=sessions.filter(d=>d<day||d===day&&minute>=930).slice(-80);
 if(dates.length<60) return {status:'blocked-calendar'};
 const id='holding-'+Math.floor(+clock/900000),started=clock.toISOString();
 const count=await env.REPORT_DB.prepare('SELECT COUNT(*) AS n FROM cloud_holding_reports').first();if(count.n>=3000)return {status:'storage-budget-stop'};
 const claim=await env.REPORT_DB.prepare("INSERT OR IGNORE INTO cloud_holding_reports(id,started_at,status,trigger_kind) VALUES (?,?,'running',?)").bind(id,started,kind).run();
 if(!claim.meta.changes)return {status:'already-claimed',id};
 let report;
 try{
  const basis=await env.REPORT_DB.prepare("SELECT report_id,archive_sha256,json_extract(body,'$.snapshot.report.portfolio') AS portfolio FROM private_snapshot WHERE id=1").first();
  if(!basis)throw Error('ACCOUNT_BASIS_MISSING');const pf=JSON.parse(basis.portfolio);
  const holdings=[];
  for(const symbol of ['600900.SH','515450.SH']){
   const position=pf.positions.find(p=>p.symbol===symbol);if(!position)throw Error('POSITION_BASIS_MISSING');
   const raw=await history(symbol,'none',dates),qfq=await history(symbol,'qfq',dates);
   if(raw.rows.some((r,i)=>String(r[5])!==String(qfq.rows[i][5])))throw Error('VOLUME_BASIS_CONFLICT');
   holdings.push({position,accountAsOf:position.holdingAsOf||pf.asOf,facts:facts(raw.rows,qfq.rows),evidence:{raw,qfq},conclusion:'完整日趋势研究，账户仍为历史快照；不作当前交易指令',conditions:{hold:'核对原始及前复权趋势、当前账户与风险预算；完整准入仍未通过',reduceOrExit:'均线依据失效或价格口径冲突时重核风险；不直接推导清仓或止损价'},tradeAdmitted:false});
  }
  report={id,status:'success',startedAt:started,generatedAt:new Date().toISOString(),researchThrough:dates.at(-1),kind:'cloud-holding-complete-session-facts',accountBasisReportId:basis.report_id,accountBasisArchiveSha256:basis.archive_sha256,holdings,tradeCandidateCount:0,limitations:['Only two holdings; not full-market research or complete Python strategy migration','Single public provider; unit/company-action/cross-source checks not fully migrated','No verified dividends, net PnL, valuations, fund flows or trading eligibility','Windows 14:38 task remains backup; no Bark sent by this module']};
 }catch(e){report={id,status:'failed',startedAt:started,generatedAt:new Date().toISOString(),code:/^[A-Z_]+$/.test(e.message)?e.message:'RESEARCH_NETWORK_OR_RUNTIME_FAILED',failureType:e.name,diagnostics:e.details||null};}
 await env.REPORT_DB.prepare('UPDATE cloud_holding_reports SET finished_at=?,status=?,body=? WHERE id=?').bind(report.generatedAt,report.status,JSON.stringify(report),id).run();
 try{await trackSignals(env,report);}catch(e){await env.REPORT_DB.prepare("INSERT OR IGNORE INTO cloud_tracking_runs(report_id,status,at,code) VALUES (?,'failed',?,?)").bind(report.id,new Date().toISOString(),/^[A-Z_]+$/.test(e.message)?e.message:'TRACKING_RUNTIME_FAILED').run();}
 return report;
}
