import {sessions} from './sessions.mjs';
const enc=new TextEncoder();
export async function importSignals(env,value){
 if(value?.format!=='goose-immutable-signal-transfer'||!Array.isArray(value.events)||value.events.length<1||value.events.length>10)throw Error('SIGNAL_TRANSFER_INVALID');
 const count=await env.REPORT_DB.prepare('SELECT COUNT(*) AS n FROM cloud_signal_versions').first();if(count.n>=5000)throw Error('SIGNAL_STORAGE_BUDGET');
 const statements=[];
 for(const event of value.events){
  if(!/^[a-f0-9]{64}$/.test(event.eventId)||!['prospective','reconstructed','simulation','unverified'].includes(event.cohort)||!/^\d{6}\.(SH|SZ)$/.test(event.symbol)||!Number.isFinite(Date.parse(event.signalAt))||event.followUp)throw Error('SIGNAL_FACT_INVALID');
  const body=JSON.stringify(event);if(body.length>16000)throw Error('SIGNAL_FACT_TOO_LARGE');
  const sha=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(body))),b=>b.toString(16).padStart(2,'0')).join('');
  statements.push(env.REPORT_DB.prepare('INSERT OR IGNORE INTO cloud_signal_versions(event_id,version_sha,cohort,signal_at,symbol,imported_at,body) VALUES (?,?,?,?,?,?,?)').bind(event.eventId,sha,event.cohort,event.signalAt,event.symbol,new Date().toISOString(),body));
 }
 await env.REPORT_DB.batch(statements);return {status:'stored-immutable-versions',count:value.events.length};
}
export function priceOutcome(event,rows,through){
 const available=sessions.filter(d=>d>event.signalDay),byDay=new Map(rows.map(r=>[r[0],r]));const horizons={};
 for(const n of [1,5,20]){
  const window=available.slice(0,n),due=window[n-1];let result={through:due||null,priceReturnPct:null,netTotalReturnPct:null,benchmarkExcessPct:null};
  if(!due)result.status='unavailable-calendar';else if(due>through)result.status='pending';
  else if(!window.every(d=>byDay.has(d))||Date.parse(window[0]+'T09:30:00+08:00')<=Date.parse(event.signalAt))result.status='unavailable-history-or-ordering';
  else{
   const price=s=>{if(!/^\d+(\.\d{1,6})?$/.test(String(s)))throw Error('PRICE_SCALE_INVALID');const [a,b='']=String(s).split('.');return BigInt(a)*1000000n+BigInt(b.padEnd(6,'0'));};
   const first=price(byDay.get(window[0])[1]),last=price(byDay.get(due)[2]);
   if(first<=0n||last<=0n)result.status='unavailable-price';else{
    let numerator=(last-first)*1000000n;const negative=numerator<0n;if(negative)numerator=-numerator;
    let quotient=numerator/first;const remainder=numerator%first;if(remainder*2n>first||remainder*2n===first&&quotient%2n===1n)quotient++;
    const formatted=(negative&&quotient!==0n?'-':'')+(quotient/10000n)+'.'+(quotient%10000n).toString().padStart(4,'0');
    result={...result,status:'observed-raw-price-only',priceReturnPct:formatted,basis:'next-session raw open to horizon close; not executable net profit, fees/dividends/share changes unverified'};
   }
  }
  horizons[n]=result;
 }
 return {eventId:event.eventId,cohort:event.cohort,horizons,triggerVerified:false,tradeAdmitted:false};
}
export async function trackSignals(env,report){
 if(report.status!=='success')return;
 const count=await env.REPORT_DB.prepare('SELECT COUNT(*) AS n FROM cloud_signal_outcomes').first();if(count.n>=30000){await env.REPORT_DB.prepare("INSERT OR IGNORE INTO cloud_tracking_runs(report_id,status,at,code) VALUES (?,'blocked',?,'OUTCOME_STORAGE_BUDGET')").bind(report.id,new Date().toISOString()).run();return;}
 const events=(await env.REPORT_DB.prepare("SELECT body FROM cloud_signal_versions WHERE cohort IN ('prospective','reconstructed') ORDER BY imported_at DESC LIMIT 500").all()).results;
 const unique=new Map();for(const row of events){const event=JSON.parse(row.body);if(!unique.has(event.eventId))unique.set(event.eventId,event);}
 const statements=[];
 for(const event of unique.values()){
  const held=report.holdings.find(h=>h.position.symbol===event.symbol);if(!held||event.validForEventStudy!==true)continue;
  const outcome=priceOutcome(event,held.evidence.raw.rows,report.researchThrough);outcome.historySourceSha256=held.evidence.raw.rawSha256;
  statements.push(env.REPORT_DB.prepare('INSERT OR IGNORE INTO cloud_signal_outcomes(report_id,event_id,evaluated_at,body) VALUES (?,?,?,?)').bind(report.id,event.eventId,new Date().toISOString(),JSON.stringify(outcome)));
 }
 // At most 40 writes in each bounded batch; no dynamic task fanout.
 for(let i=0;i<statements.length;i+=40)await env.REPORT_DB.batch(statements.slice(i,i+40));
 await env.REPORT_DB.prepare("INSERT OR IGNORE INTO cloud_tracking_runs(report_id,status,at,events_written) VALUES (?,'success',?,?)").bind(report.id,new Date().toISOString(),statements.length).run();
}
