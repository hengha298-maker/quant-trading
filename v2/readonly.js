/* Independent real_readonly entry, currently locked to fictional input.
 * Never loads GooseDemo/GooseModel, never computes assets, never registers SW.
 */
(function(){
  'use strict';
  const M=window.GooseReadonly, main=document.getElementById('main');
  const dialog=document.getElementById('app-dialog'), dialogContent=document.getElementById('dialog-content');
  const state={data:null,phase:'unauthorized',message:'请输入电脑窗口中的一次性虚构测试配对码。',account:'all',hidden:false,busy:false};
  let generation=0, pending=null;
  const e=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const paths={home:'M3 11 12 3l9 8M5 10v11h5v-7h4v7h5V10',holdings:'M3 6h18v15H3V6M15 11h6v5h-6z',strategies:'M20 3C7 3 3 8 4 14s11 7 16-11M4 21l11-11',reports:'M5 3h14v18H5zM8 8h8M8 12h8M8 16h5'};
  const icon=id=>`<svg width="21" height="21" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="${paths[id]||paths.reports}"/></svg>`;
  const nav=[['/','home','首页'],['/holdings','holdings','持仓'],['/strategies','strategies','策略'],['/reports','reports','报告']];
  const pill=(s,style='gray')=>`<span class="pill ${style}">${e(s)}</span>`;
  const money=v=>state.hidden?'••••':M.decimal(v)+(v===null?'':' 元');
  const quantity=v=>state.hidden?'••••':M.units(v);
  const value=v=>state.hidden?'••••':M.decimal(v);
  const unavailable=()=>'<div class="unavailable-box"><strong>无法形成可靠建议</strong><p>估值、盈亏、长期加仓和日内做T建议均未开放。</p></div>';
  function clear(phase,message){
    generation++;pending?.abort();pending=null;state.data=null;state.phase=phase;state.message=message;
    state.account='all';state.busy=false;if(dialog.open)dialog.close();dialogContent.replaceChildren();render();
  }
  function heading(title,description){return `<div class="page-head"><div><div class="eyebrow">real_readonly · 仅虚构测试</div><h1>${e(title)}</h1><p>${e(description)}</p></div><div class="head-note">P1校验投影<span>真实持仓入口关闭</span></div></div>`;}
  function tools(){return '<div class="session-tools"><button data-action="refresh">重新读取当前批次</button><button data-action="privacy">隐藏 / 显示数字</button><button data-action="logout">退出并撤销会话</button></div>';}
  function times(){const d=state.data;return `<dl class="data-lines"><div class="data-line"><dt>持仓快照</dt><dd>${e(d.snapshot_as_of)} · 日内时刻未知</dd></div><div class="data-line"><dt>报告生成</dt><dd>${e(M.time(d.generated_at))} · 北京时间</dd></div><div class="data-line"><dt>云端同步</dt><dd>未接入</dd></div></dl>`;}
  function quote(symbol){return state.data.quotes.find(q=>q.symbol===symbol);}
  function quoteBox(q){
    if(!q||q.close===null)return `<div class="unavailable-box"><strong>${e(q?.label||'缺少行情')}</strong><p>${e(q?.reason||'没有已选日线，不使用演示或旧价格补充。')}</p></div>`;
    return `<div class="card detail-card"><div class="card-top"><h3>${e(q.name)}</h3>${pill(q.label,q.state==='observation'?'amber':'')}</div><p>${q.state==='observation'?'原始历史收盘数值 · 单位未核验':'历史参考收盘 · 元/股或份'}</p><p class="readonly-value">${value(q.close)}</p><p>${e(q.as_of)} · 非实时 · 距生成时点 ${q.lag_days} 自然日</p><details class="detail-disclosure"><summary>来源与限制</summary><p>虚构测试情景 · ${e(q.source)}</p><p>采集时间 ${e(M.time(q.fetched_at))} · 北京时间</p><p>${e(q.reason)}</p><p>历史费用资格：${q.historical_cost_reference?'仅有对应资格，本页不计算费用':'未开放'}；不能据此扩大估值、盈亏或建议权限。</p></details></div>`;
  }
  function home(){const d=state.data;return `${heading('资产与数据状态','现有P1只读结构的虚构接口测试，不是已接通真实账户。')}${tools()}<div class="home-grid"><div class="main-column"><section class="card asset-hero"><div class="card-top"><span>总资产状态</span>${pill('估值未开放','amber')}</div><div class="asset-total">暂无法完整估值</div><p>不把成本或未核验行情当成市值，不计算盈亏。</p><div class="hero-metrics"><dl class="hero-metric"><dt>已知现金 · 已录入部分</dt><dd>${money(d.known_cash)}</dd></dl><dl class="hero-metric"><dt>现金未知账户</dt><dd>${d.unknown_cash_accounts} 个</dd></dl></div></section><section><div class="section-heading"><h2>重点持仓与行情</h2><a class="text-link" href="#/holdings">查看持仓 ↗</a></div><div class="watch-list">${d.quotes.map(q=>quoteBox(q)).join('')}</div></section></div><div class="side-column"><section class="card"><h2>数据有迹可循</h2>${times()}<p class="tiny-note">刷新只读取电脑已发布的同一批次，不改变持仓日期、行情日期或生成时间。</p></section>${unavailable()}</div></div>`;}
  function accountCard(a){return `<section class="card account-card"><div class="account-header"><div class="account-title-row"><h2>${e(a.broker)}</h2>${pill(a.coverage==='complete'?'声明已录全':'部分录入','amber')}</div><div class="account-cash"><span>快照现金</span><strong>${money(a.cash)}</strong></div></div><div class="positions">${a.positions.map(p=>`<a class="position-link" href="#/holdings/${e(a.id)}/${e(p.symbol)}"><div class="stock-id"><span class="stock-symbol ${p.kind==='etf'?'blue':''}">${p.kind==='etf'?'ETF':'股'}</span><div><h3>${e(quote(p.symbol)?.name||p.symbol)}</h3><small>${e(p.symbol)}</small></div></div><dl class="position-metrics"><div class="metric"><dt>数量 / ${p.kind==='etf'?'份':'股'}</dt><dd>${quantity(p.quantity)}</dd></div><div class="metric"><dt>快照可卖数量</dt><dd>${quantity(p.available)}</dd></div><div class="metric"><dt>券商成本 · 元/股或份</dt><dd>${value(p.broker_cost)}</dd></div></dl></a>`).join('')||'<p>未录入持仓，不代表实际空仓。</p>'}</div><div class="account-foot">快照 ${e(state.data.snapshot_as_of)} · 不是当前可卖额度</div></section>`;}
  function holdings(){const d=state.data;return `${heading('我的持仓','数量、可卖数量、现金和成本按快照保留，不跨账户调剂。')}${tools()}<div class="toolbar"><div class="segmented" role="group" aria-label="账户筛选">${[['all','全部账户'],...d.accounts.map(a=>[a.id,a.broker])].map(([id,label])=>`<button class="segment" data-account="${id}" aria-pressed="${id===state.account}">${e(label)}</button>`).join('')}</div></div><div class="account-grid">${d.accounts.filter(a=>state.account==='all'||a.id===state.account).map(accountCard).join('')}</div>`;}
  function positionDetail(id,symbol){const a=state.data.accounts.find(a=>a.id===id),p=a?.positions.find(p=>p.symbol===symbol);if(!p)return notFound('/holdings');return `<a class="back-link" href="#/holdings">← 返回持仓</a>${heading(quote(symbol)?.name||symbol,a.broker+' · 虚构测试快照')}<section class="card detail-card"><h2>${e(symbol)}</h2><dl class="detail-facts"><div><dt>总数量</dt><dd>${quantity(p.quantity)}</dd></div><div><dt>快照可卖数量</dt><dd>${quantity(p.available)}</dd></div><div><dt>券商显示成本 · 元/股或份</dt><dd>${value(p.broker_cost)}</dd></div><div><dt>市值 / 盈亏</dt><dd>尚未开放，不计算</dd></div></dl><details class="detail-disclosure"><summary>快照与T+1说明</summary><p>可卖数量只对应该快照；不推算今天可卖量，不将当天买入份额作为可卖底仓。</p></details></section><div class="section-spaced">${quoteBox(quote(symbol))}</div>${unavailable()}`;}
  function strategies(id){const d=state.data;if(id&&!d.strategies.some(s=>s.id===id))return notFound('/strategies');return `${id?'<a class="back-link" href="#/strategies">← 返回策略</a>':''}${heading('研究与策略','长期加仓与日内做T独立；接口不能开放投资操作权限。')}<div class="strategy-grid">${d.strategies.filter(s=>!id||s.id===id).map(s=>`<section class="card strategy-card"><span class="section-label">独立研究模块</span><h2>${e(s.title)}</h2>${pill('未验证 · 无法形成可靠建议','amber')}<p>当前仅有受限历史观察，缺少已验证研究、实际费用和可执行条件。</p><details class="detail-disclosure"><summary>数据与权限限制</summary><p>不提供加仓/做T价格、操作数量或资金建议。515450未知单位及复权行情不能参与估值、盈亏和量价信号。</p></details>${id?'':`<a class="primary-link" href="#/strategies/${s.id}">查看限制 ↗</a>`}</section>`).join('')}</div>${unavailable()}`;}
  function reports(id){if(id&&id!=='current')return notFound('/reports');return `${id?'<a class="back-link" href="#/reports">← 返回报告</a>':''}${heading('数据报告','只展示已发布的当前快照，不补演示历史研究。')}<section class="card detail-card"><h2>${e(state.data.reports[0].title)}</h2>${pill('持仓与行情摘要，不是投资建议')}<p>没有真实成交、历史投资建议或收益评估记录。</p>${times()}${id?state.data.quotes.map(quoteBox).join(''):'<a class="primary-link" href="#/reports/current">查看当前批次详情 ↗</a>'}</section>${unavailable()}`;}
  function notFound(parent='/'){return `<a class="back-link" href="#${parent}">← 返回</a><section class="card empty-state"><h1>这份记录不存在</h1><p>不会使用演示记录填补。</p></section>`;}
  function render(){
    const parts=(location.hash.replace(/^#/,'')||'/').split('/').filter(Boolean),tab=parts[0]||'home';
    document.title=`养鹅计划 · ${nav.find(n=>n[1]===tab)?.[2]||'连接状态'} · 只读虚构测试`;
    const links=nav.map(([path,id,title],i)=>`<a class="nav-link" href="#${path}" ${tab===id?'aria-current="page"':''}>${icon(id)}<span>${title}</span><span class="nav-index">0${i+1}</span></a>`).join('');
    document.querySelector('.desktop-nav').innerHTML=links;document.querySelector('.bottom-nav').innerHTML=links;
    document.querySelector('.status-word').textContent=state.data?'已配对 · 虚构测试':'未连接';
    if(!state.data){
      const canPair=['unauthorized','revoked','disconnected'].includes(state.phase)&&!state.busy;
      main.innerHTML=`${heading('只读模式连接','此入口仅用于虚构数据安全测试，不加载真实持仓。')}<section class="card detail-card"><h2>${e(state.message)}</h2><p class="connection-note">未授权、连接中断或协议不匹配时不展示旧数据，也不回填演示数据。</p>${canPair?'<form class="pair-form" id="pair-form" autocomplete="off"><label>电脑窗口的一次性测试配对码<input name="code" type="password" inputmode="numeric" autocomplete="off" pattern="[0-9]{8}" minlength="8" maxlength="8" required></label><button class="primary-link" type="submit">配对并读取虚构数据</button></form>':''}<div class="session-tools"><button data-action="refresh" ${state.busy?'disabled':''}>检查连接 / 重试</button></div></section>`;return;
    }
    const age=M.stale(state.data)?'<div class="subtle-banner">数据已过期或超过7自然日展示阈值，只能查看历史快照；不形成投资建议。</div>':'<div class="subtle-banner">历史快照 · 非实时行情 · 全部为虚构测试，不代表已接通真实数据。</div>';
    let html=tab==='home'&&parts.length===0?home():tab==='holdings'?(parts.length===3?positionDetail(parts[1],parts[2]):parts.length===1?holdings():notFound('/holdings')):tab==='strategies'&&parts.length<=2?strategies(parts[1]):tab==='reports'&&parts.length<=2?reports(parts[1]):notFound();
    main.innerHTML=age+html;
  }
  async function read(){
    if(state.busy||state.phase==='blocked')return;
    const ticket=++generation;pending?.abort();pending=new AbortController();let decoding=false;
    try{
      const res=await fetch('/api/v1/view',{cache:'no-store',credentials:'same-origin',signal:pending.signal});
      if(ticket!==generation||document.visibilityState==='hidden')return;
      if(res.status===401){clear('unauthorized','未授权：会话可能已过期或撤销，请使用电脑新配对码。');return;}
      if(!res.ok)throw new Error('network');
      decoding=true;const length=Number(res.headers.get('Content-Length'));if(!Number.isFinite(length)||length<=0||length>128000)throw new TypeError('protocol');
      const raw=await res.text();if(raw.length>128000)throw new TypeError('protocol');const body=JSON.parse(raw);if(ticket!==generation||document.visibilityState==='hidden')return;
      const next=M.accept(body);
      if(state.data?.bundle_id===next.bundle_id){
        if(JSON.stringify(state.data)!==JSON.stringify(next))throw new TypeError('immutable batch changed');
        return; // Preserve open details, focus and scroll for the same batch.
      }
      state.data=next;state.phase='connected';state.message='';render();
    }catch(err){if(ticket!==generation||err.name==='AbortError')return;clear(decoding?'protocol':'disconnected',decoding?'数据协议不匹配，已拒绝显示；不会回填演示数据。':'连接已断开，已清空本页数据。请检查电脑测试服务。');}
  }
  document.addEventListener('submit',async event=>{
    if(event.target.id!=='pair-form')return;event.preventDefault();if(state.busy||state.phase==='blocked')return;
    const code=new FormData(event.target).get('code');event.target.reset();const ticket=++generation;state.busy=true;state.message='正在验证配对…';render();
    try{
      const res=await fetch('/session/pair',{method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({code})});
      if(ticket!==generation)return;
      state.busy=false;
      if(!res.ok){clear('unauthorized','配对失败：码错误、已用、过期或尝试次数过多。请在电脑更新配对码。');return;}
      await read();
    }catch(_){if(ticket===generation){state.busy=false;clear('disconnected','无法连接电脑测试服务，未加载数据。');}}
    finally{state.busy=false;}
  });
  document.addEventListener('click',async event=>{
    const button=event.target.closest('button');if(!button)return;
    if(button.dataset.action==='refresh'){await read();return;}
    if(button.dataset.action==='logout'){
      clear('revoked','已退出，本页数据已清空。');state.busy=true;render();
      try{await fetch('/session/logout',{method:'POST',cache:'no-store',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'});}catch(_){}
      finally{state.busy=false;render();}return;
    }
    if(button.dataset.action==='privacy'){state.hidden=!state.hidden;render();return;}
    if(button.dataset.account&&state.data){state.account=button.dataset.account;render();return;}
    if(button.id==='close-dialog'){dialog.close();return;}
    if(button.dataset.dialog){dialogContent.innerHTML=`<h2 id="dialog-title">连接与权限</h2><p>${state.data?'已通过虚构测试配对；数据来自指定P1校验器。':'当前未加载账户数据。'}</p><p>真实持仓、手机访问、HTTPS、云同步、Bark和交易均未启用。关闭电脑测试服务即可停止访问。</p>${state.data?times():''}`;dialog.showModal();}
  });
  window.addEventListener('hashchange',()=>{render();main.focus({preventScroll:true});window.scrollTo(0,0);});
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='hidden')clear('locked','页面已锁定，返回前台后重新检查会话。');else read();});
  window.addEventListener('pagehide',()=>clear('locked','页面已锁定，需重新检查会话。'));
  window.addEventListener('pageshow',event=>{if(event.persisted){clear('locked','已返回页面，正在重新检查会话。');read();}});
  document.getElementById('settings-icon').innerHTML=icon('reports');
  if(location.protocol!=='http:'||location.hostname!=='127.0.0.1'||('serviceWorker' in navigator&&navigator.serviceWorker.controller)){
    clear('blocked','测试入口或旧离线缓存不符合要求。请停止测试并改用新的本机端口。');return;
  }
  render();read();setInterval(()=>{if(state.data&&document.visibilityState==='visible')read();},15000);
})();
