// Independent, bounded public quote collection. Never overwrites account/report facts.
import {sessions} from './sessions.mjs';
export function parseQuotes(raw,now=new Date()) {
 const quotes=[];
 for(const [provider,symbol] of [['sh600900','600900.SH'],['sh515450','515450.SH']]) {
  const match=raw.match(new RegExp('v_'+provider+'="([^"]+)"'));if(!match)throw Error('QUOTE_MISSING');
  const f=match[1].split('~'),stamp=f[30];
  if(f[2]!==symbol.slice(0,6)||!/^\d{14}$/.test(stamp)||!/^\d+(\.\d+)?$/.test(f[3])||Number(f[3])<=0)throw Error('QUOTE_FIELDS_REJECTED');
  const sourceTime=`${stamp.slice(0,4)}-${stamp.slice(4,6)}-${stamp.slice(6,8)}T${stamp.slice(8,10)}:${stamp.slice(10,12)}:${stamp.slice(12,14)}+08:00`;
  const age=(now-Date.parse(sourceTime))/1000;if(!Number.isFinite(age)||age<0)throw Error('QUOTE_TIME_REJECTED');
  quotes.push({symbol,price:f[3],sourceTime,fetchedAt:now.toISOString(),source:'tencent-public-quote',adjustment:'none',quality:'dated-public-quote-not-independent-feed-verification',ageSeconds:age,intradaySignalAllowed:false});
 }
 return quotes;
}
export async function collectPulse(env,kind='scheduled',now=new Date()) {
 const day=new Date(+now+8*3600000).toISOString().slice(0,10);
 if(!sessions.includes(day))return {status:'skipped-calendar',day};
 const id='cloud-'+Math.floor(+now/300000),at=now.toISOString();
 const count=await env.REPORT_DB.prepare('SELECT COUNT(*) AS n FROM cloud_pulse').first();
 if(count.n>=10000)return {status:'storage-budget-stop'};
 const claim=await env.REPORT_DB.prepare("INSERT OR IGNORE INTO cloud_pulse(id,started_at,status,trigger_kind) VALUES (?,?,'running',?)").bind(id,at,kind).run();
 if(!claim.meta.changes)return {status:'already-claimed',id};
 let result;
 let timer;let stage='request';
 try {
  const controller=new AbortController();timer=setTimeout(()=>controller.abort(),8000);
  const response=await fetch('https://qt.gtimg.cn/q=sh600900,sh515450',{signal:controller.signal,redirect:'manual'});
  if(!response.ok)throw Error('SOURCE_HTTP_FAILED');
  stage='read';const raw=await response.text();if(raw.length>32000)throw Error('SOURCE_TOO_LARGE');stage='parse';
  result={id,status:'success',generatedAt:new Date().toISOString(),quotes:parseQuotes(raw,new Date()),pipeline:'independent-cloud-quotes-only',tradeCandidateCount:0,warning:'云端只完成两只持仓标的公开报价采集；账户非实时，完整研究与全市场扫描尚未迁移，不生成买卖指令。'};
 }catch(e){result={id,status:'failed',generatedAt:new Date().toISOString(),stage,failureType:e.name,code:/^[A-Z_]+$/.test(e.message)?e.message:'SOURCE_NETWORK_OR_RUNTIME_FAILED',quotes:[]};}finally{clearTimeout(timer);}
 await env.REPORT_DB.prepare('UPDATE cloud_pulse SET status=?,finished_at=?,body=? WHERE id=?').bind(result.status,result.generatedAt,JSON.stringify(result),id).run();
 return result;
}
