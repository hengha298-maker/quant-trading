/* Strict transport projection for explicitly separated private and fixture entries.
 * No demo import, no portfolio calculations, no persistence.
 */
(function(root){
  'use strict';
  const REVISION='f6c117fab964f2e627d1a64a651bab2827ff0308';
  const fail=()=>{throw new TypeError('只读协议不匹配');};
  const keys=(o,names)=>{if(!o||typeof o!=='object'||Array.isArray(o)||Object.keys(o).sort().join('|')!==names.slice().sort().join('|'))fail();};
  const text=(v)=>{if(typeof v!=='string'||!v.length||v.length>512||/[\x00-\x1f]/.test(v))fail();return v;};
  const day=(v)=>{if(typeof v!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(v))fail();const d=new Date(v+'T00:00:00Z');if(!Number.isFinite(d.valueOf())||d.toISOString().slice(0,10)!==v)fail();return v;};
  const stamp=(v)=>{text(v);if(!/(Z|[+-]\d{2}:\d{2})$/.test(v)||!Number.isFinite(Date.parse(v)))fail();return v;};
  const number=(v,signed=false)=>{if(v===null)return null;if(typeof v!=='string'||v.length>40||!(signed?/^-?\d+(\.\d{1,12})?$/:/^\d+(\.\d{1,12})?$/).test(v)||!Number.isFinite(Number(v))||Math.abs(Number(v))>1e18)fail();return v;};
  const symbol=(s)=>{if(typeof s!=='string'||!/^\d{6}\.(SH|SZ)$/.test(s))fail();};
  function accept(input,{testData=true}={}){
    if(typeof testData!=='boolean')fail();
    keys(input,['api_version','mode','test_data','p1_revision','bundle_id','snapshot_as_of','snapshot_at','generated_at','state','accounts','quotes','known_cash','unknown_cash_accounts','permissions','total_assets','strategies','reports']);
    if(input.api_version!==1||input.mode!=='real_readonly'||input.test_data!==testData||input.p1_revision!==REVISION||!(testData?/^fixture-[0-9a-f]{16}$/:/^private-[0-9a-f]{16}$/).test(input.bundle_id)||input.snapshot_at!==null||input.total_assets!==null||!['historical','stale'].includes(input.state))fail();
    day(input.snapshot_as_of);stamp(input.generated_at);number(input.known_cash);
    keys(input.permissions,['valuation','profit_loss','advice']);if(Object.values(input.permissions).some(v=>v!==false))fail();
    if(!Array.isArray(input.accounts)||!input.accounts.length||input.accounts.length>100||!Array.isArray(input.quotes)||input.quotes.length>100)fail();
    const seen=new Set(),kinds=new Map();
    for(const a of input.accounts){
      keys(a,['id','broker','coverage','cash','positions']);if(!/^account-[1-9][0-9]*$/.test(a.id)||seen.has(a.id)||!['partial','complete'].includes(a.coverage)||!Array.isArray(a.positions)||a.positions.length>1000)fail();seen.add(a.id);text(a.broker);number(a.cash);if(a.coverage==='complete'&&a.cash===null)fail();
      const symbols=new Set();
      for(const p of a.positions){keys(p,['symbol','kind','quantity','available','broker_cost']);symbol(p.symbol);if(symbols.has(p.symbol)||!['stock','etf'].includes(p.kind)||!Number.isSafeInteger(p.quantity)||p.quantity<=0||!Number.isSafeInteger(p.available)||p.available<0||p.available>p.quantity||(kinds.has(p.symbol)&&kinds.get(p.symbol)!==p.kind))fail();symbols.add(p.symbol);kinds.set(p.symbol,p.kind);number(p.broker_cost,true);}
    }
    if(!Number.isSafeInteger(input.unknown_cash_accounts)||input.unknown_cash_accounts!==input.accounts.filter(a=>a.cash===null).length)fail();
    const quotes=new Set();
    for(const q of input.quotes){
      keys(q,['symbol','name','state','label','close','close_unit','source','as_of','fetched_at','lag_days','reason','historical_cost_reference']);symbol(q.symbol);text(q.name);text(q.label);text(q.reason);if(quotes.has(q.symbol)||!['missing','unavailable','ambiguous','reference','observation'].includes(q.state))fail();quotes.add(q.symbol);
      if(['reference','observation'].includes(q.state)){
        number(q.close);if(q.close===null||Number(q.close)<=0)fail();day(q.as_of);stamp(q.fetched_at);text(q.source);if(!Number.isSafeInteger(q.lag_days)||q.lag_days<0)fail();
        if(q.state==='observation'&&(q.close_unit!==null||q.historical_cost_reference!==false))fail();
        if(q.state==='reference'&&(q.close_unit!=='CNY'||q.historical_cost_reference!==true))fail();
      }else if(q.close!==null||q.close_unit!==null||q.as_of!==null||q.lag_days!==null||q.historical_cost_reference!==false)fail();
      if(q.source!==null)text(q.source);if(q.fetched_at!==null)stamp(q.fetched_at);
    }
    if(!Array.isArray(input.strategies)||input.strategies.length!==2||!Array.isArray(input.reports)||input.reports.length!==1)fail();
    for(const [i,s] of input.strategies.entries()){keys(s,['id','title','status']);if(s.id!==['long-term','intraday'][i]||s.status!=='unavailable')fail();text(s.title);}
    const r=input.reports[0];keys(r,['id','title','status']);if(r.id!=='current'||r.status!=='snapshot_only')fail();text(r.title);
    // Defensive clone: no mutating caller-owned objects and no raw audit fields.
    return JSON.parse(JSON.stringify(input));
  }
  function decimal(value){
    if(value===null)return '未知';number(value,true);
    const [whole,frac]=value.split('.');const pretty=whole.replace(/\B(?=(\d{3})+(?!\d))/g,',');return pretty+(frac?'.'+frac:'');
  }
  function units(value){if(value===null)return '未知';if(!Number.isSafeInteger(value)||value<0)fail();return String(value).replace(/\B(?=(\d{3})+(?!\d))/g,',');}
  function time(value){if(value===null)return '未记录';stamp(value);return new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(value));}
  function stale(data,now=Date.now()){return data.state==='stale'||(now-Date.parse(data.snapshot_as_of+'T00:00:00+08:00'))>7*86400000;}
  const api=Object.freeze({accept,decimal,units,time,stale,REVISION});root.GooseReadonly=api;if(typeof module!=='undefined'&&module.exports)module.exports=api;
})(typeof globalThis!=='undefined'?globalThis:this);
