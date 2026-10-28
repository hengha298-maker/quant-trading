/* Private server view: no analytics, wealth-data storage, IndexedDB or service worker. */
'use strict';
const $ = id => document.getElementById(id);
const api = '/investment/api/';
let csrf = '', pending = null, polling = null;
const keyFragment = new URLSearchParams(location.hash.slice(1)).get('key');
if(keyFragment){sessionStorage.setItem('goose-private-access',keyFragment);history.replaceState(null,'',location.pathname);}
const accessKey = sessionStorage.getItem('goose-private-access')||'';
const labels = {watch:'观察，不必操作', hold:'继续持有研究', accumulate:'研究分批买入', reduce:'研究分批减仓', missing:'数据缺失', blocked:'核验未通过', historical:'历史参考', verified:'已核验', ready:'分析完成', saved:'已保存', unavailable:'暂无有效数据', current:'当日数据', healthy:'已核验','intraday-verified':'盘中新鲜度通过','closing-snapshot':'带日期收盘快照','current-user-confirmed':'你已确认的当日持仓','stale-snapshot':'过期持仓快照','portfolio-research':'真实持仓研究','holdings-required':'等待当前持仓'};
Object.assign(labels,{'stale-report':'历史报告待更新','historical-snapshot':'已过期的盘中快照','market-unavailable':'本次行情不可用'});
const show = value => value === null || value === undefined || value === '' ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const text = (tag, value, cls) => {const n = document.createElement(tag); n.textContent = value===''?'':show(value); if(cls)n.className=cls; return n;};
const amount = value => {if(value===null||value===undefined||value==='')return '未知';const str=show(value),parts=str.split('.');return '¥ '+(/^-?\d+(\.\d{1,2})?$/.test(str)?parts[0]+'.'+(parts[1]||'').padEnd(2,'0'):str);};
const date = value => {if(!value)return '未知';const d=new Date(value);return Number.isNaN(d.getTime())?show(value):d.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})+' 北京时间';};
const strings = value => Array.isArray(value)?value.map(item=>typeof item==='object'?item.message||item.code||JSON.stringify(item):String(item)):value?[show(value)]:[];
function empty(title, body){const d=text('div','', 'empty');d.append(text('h3',title),text('p',body,'muted'));return d;}
function risks(items){const list=strings(items);if(!list.length)return null;const box=text('div','','risks'), ul=document.createElement('ul');for(const item of list)ul.append(text('li',item));box.append(ul);return box;}
function metric(label,value){const box=text('div','','metric');box.append(text('span',label),text('strong',value));return box;}
async function request(route, body){const options={cache:'no-store',credentials:'same-origin',headers:{Authorization:'Bearer '+accessKey}};if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.headers['X-Goose-CSRF']=csrf;options.body=JSON.stringify(body);}const response=await fetch(api+route,options);let data;try{data=await response.json();}catch{throw new Error('服务返回异常，请检查电脑控制窗口。');}if(!response.ok)throw new Error(data.message||data.error||'请求失败');return data;}
function holdingsTable(value){const box=text('div','');const positions=value.positions||[];box.append(text('p',`持仓日期：${show(value.asOf)} · 来源：${show(value.source?.broker||value.source?.kind||value.source||'用户确认导出')}`,'meta'));box.append(text('p',`可用资金：${amount(value.availableCash)}。缺失金额不会按零处理。`,'meta'));const scroll=text('div','','table-scroll'), table=document.createElement('table'), head=document.createElement('thead'), hr=document.createElement('tr');for(const name of ['标的','数量','成本价','导出市值','导出浮盈亏'])hr.append(text('th',name));head.append(hr);table.append(head);const tbody=document.createElement('tbody');for(const p of positions){const tr=document.createElement('tr');for(const val of [`${show(p.name)} ${show(p.symbol)}`,show(p.quantity),amount(p.costPrice),amount(p.reportedMarketValue),amount(p.reportedPnl)])tr.append(text('td',val));tbody.append(tr);}table.append(tbody);scroll.append(table);box.append(scroll);const note=risks(value.warnings);if(note)box.append(note);return box;}
function financialTable(headers, rows){const scroll=text('div','','table-scroll'),table=document.createElement('table'),head=document.createElement('tr');for(const h of headers)head.append(text('th',h));table.append(head);for(const values of rows){const row=document.createElement('tr');for(const v of values)row.append(text('td',v));table.append(row);}scroll.append(table);return scroll;}
function renderReport(report){
 const target=$('report');target.replaceChildren();
 if(!report){target.append(empty('尚未生成当前分析','先在“更新持仓”导入并核对真实持仓，然后点击“更新行情与分析”。没有新报告时，不会用旧历史信号填充。'));return;}
 const card=text('article','','card'),header=text('div','','report-head'),info=text('div','');
 info.append(text('h3','投资决策摘要'),text('p',`生成于 ${date(report.generatedAt)}`,'meta'),text('p',`行情 / 分析日期 ${show(report.asOf)} · 报告 ${show(report.id)}`,'meta'));
 header.append(info,text('span',labels[report.status]||show(report.status),'badge'));
 card.append(header,text('p',report.summary||'请结合下方事实、研究信号与数据限制判断。','summary'));
 const warning=risks(report.warnings);if(warning)card.append(warning);
 const portfolio=report.portfolio||{},totals=text('div','','metrics');
 totals.append(metric('核验报价重估市值',amount(portfolio.marketValue)),metric('按导出成本计算浮盈亏',amount(portfolio.pnl)),metric('导出可用资金',amount(portfolio.availableCash)));card.append(totals);
 card.append(text('p',`持仓状态：${labels[portfolio.status]||show(portfolio.status)} · 持仓日期：${show(portfolio.asOf)}`,'meta'));
 if(portfolio.concentration!==null&&portfolio.concentration!==undefined)card.append(text('p',`最大单一标的占已导入证券市值 ${show(portfolio.concentration)}%。此占比不含现金和其他资产。`,'basis'));
 if(portfolio.positions?.length)card.append(financialTable(['持仓','数量','核验报价重估市值','浮盈亏','证券占比'],portfolio.positions.map(p=>[`${show(p.name)} ${show(p.symbol)}`,show(p.quantity),amount(p.marketValue),amount(p.pnl),p.weightPct==null?'未知':show(p.weightPct)+'%'])));
 const pw=risks(portfolio.warnings);if(pw)card.append(pw);
 for(const instrument of report.instruments||[]){
  const block=text('section','','instrument'),q=instrument.quote||{},signal=typeof instrument.signal==='object'?instrument.signal:{},title=text('div','','report-head');
  title.append(text('h3',`${show(instrument.name)} ${show(instrument.symbol)}`),text('span',typeof instrument.signal==='string'?instrument.signal:labels[instrument.stance||signal.stance||signal.action]||'观察，不必操作','badge'));block.append(title);
  block.append(text('p',`行情来源：${show(q.source||q.provider||q.sourceName)} · 行情时间：${show(q.sourceTime||q.timestamp||q.sourceTimestamp||q.asOf||q.tradeTime)}`,'meta'));
  const qm=text('div','','metrics'),quality=q.quality?.status||q.status;
  qm.append(metric('核验价格（元）',show(q.price??q.last??q.lastPrice)),metric('数据状态',labels[quality]||show(quality)),metric('持有数量',show(instrument.quantity??portfolio.positions?.find(p=>p.symbol===instrument.symbol)?.quantity)));block.append(qm,text('p','模型依据','label'));
  for(const line of strings(instrument.basis||signal.basis||'数据不足时保持观察，不生成今日买卖结论。'))block.append(text('p',line,'basis'));
  const ir=risks(instrument.risks);if(ir)block.append(ir);
  const m=instrument.metrics;
  if(m){const detail=text('details','');detail.append(text('summary','趋势与波动（仅用上一完整交易日）'),text('p',`截至 ${show(m.through)}；前收盘 ${show(m.previousClose)} 元；MA20 ${show(m.ma20)} 元。`,'basis'),text('p',`原V1规则：${{above:'前收盘在MA20之上',below:'前收盘在MA20之下',equal:'前收盘等于MA20'}[m.v1Comparison]||'未知'}；均线交叉对照：${{up:'上穿',down:'下穿',none:'无交叉'}[m.cross]||'未知'}。`,'basis'),text('p',`历史价格最大回撤 ${show(m.maxPriceDrawdownPct)}%；年化价格波动 ${show(m.annualizedPriceVolatilityPct)}%；最近成交量 / 前19日均量 ${show(m.latestVsPrior19VolumeRatio)}。`,'basis'),text('p','以上为未复权价格指标，除权和分红可能影响结果。前复权趋势与现金分红总收益分开，不作完整含息收益声称。','footnote'));if(m.qfqContext)detail.append(text('p',`独立前复权趋势：${show(m.qfqContext.previousClose)} 元 / MA20 ${show(m.qfqContext.ma20)} 元；仅供应商调整价格，不等于含息总回报。`,'basis'));block.append(detail);}
  const bt=instrument.backtest;
  if(bt){const b=text('details','');b.append(text('summary','同区间回测与基准（价格路径）'),text('p',`${show(bt.from)} 至 ${show(bt.to)}。未完整处理分红、税款与再投资，不是完整投资收益。`,'footnote'));
   const pct=value=>value===null||value===undefined||value===''?'未知':Number.isFinite(Number(value))?(Number(value)/100).toFixed(2)+'%':'未知',names={buy_hold:'买入并持有',v1_ma20:'原V1前收盘/MA20',ma20_cross:'MA20交叉对照',periodic:'定期投入'};
   b.append(financialTable(['策略','价格路径收益','最大回撤','交易次数','成本（分）'],(bt.comparison||[]).map(row=>[names[row.strategy]||row.strategy,pct(row.totalReturnBps),pct(row.maxDrawdownBps),show(row.tradeCount),show(row.cost)])));
   const br=risks(bt.limitations);if(br)b.append(br);block.append(b);
  }
  card.append(block);
 }
 if(!report.instruments?.length)card.append(empty('当前没有可用于分析的持仓标的','请导入当日持仓。历史持仓只作原始参考，不会自动确认为今天的账户。'));
 if(report.diagnostics){const d=text('details','');d.append(text('summary','数据健康与运行诊断'),text('pre',JSON.stringify(report.diagnostics,null,2)));card.append(d);}
 target.append(card);
}
async function reload(){
 const [latest,history,holdings]=await Promise.all([request('latest'),request('history'),request('holdings')]);
 renderReport(latest.report);$('history-list').replaceChildren();
 if(!history.reports.length)$('history-list').append(empty('还没有报告历史','每次点击更新后，报告将独立保存到电脑。'));
 for(const item of history.reports){
  const row=text('article','','history-item'),open=text('button','查看这份报告');
  row.append(text('span',date(item.generatedAt),'meta'),text('p','当时结论：'+(item.summary||labels[item.status]||item.status)),text('p',`分析日期 ${show(item.asOf)} · ${show(item.id)}`,'meta'));
  open.addEventListener('click',async()=>{try{const data=await request('history/'+encodeURIComponent(item.id));renderReport(data.report);document.querySelector('[data-page="overview"]').click();$('job').textContent='正在查看指定日期的历史记录。点“更新行情与分析”生成新报告。';window.scrollTo({top:0,behavior:'smooth'});}catch(error){$('job').textContent=error.message;}});
  row.append(open);$('history-list').append(row);
 }
 $('current-holdings').replaceChildren();
 const accounts=holdings.accounts||[];
 if(!accounts.length)$('current-holdings').append(empty('尚无你确认的当前真实持仓','请分别为华宝证券、银河证券录入。历史快照不会自动作为当前持仓。'));
 for(const account of accounts){const card=text('article','','card');card.append(text('h3',account.source?.broker||'用户确认账户'),holdingsTable(account));$('current-holdings').append(card);}
 const present=new Set(accounts.map(a=>a.source?.broker));
 for(const name of ['华宝证券','银河证券'])if(!present.has(name))$('current-holdings').append(empty(name+'：尚未更新','该券商的持仓和现金保持未知，不会从另一个账户或旧记录推断。'));
}
async function status(){try{const state=await request('status');$('refresh').disabled=state.running;if(state.running&&!polling)polling=setInterval(status,1800);$('job').textContent=state.running?'正在核验行情并生成报告；无需再次点击。':state.error?'本次分析失败，未把旧报告当作新结果。请检查电脑运行诊断。':state.finishedAt?'本次运行已结束：'+date(state.finishedAt):'';if(!state.running){clearInterval(polling);polling=null;await reload();}}catch(error){$('job').textContent=error.message;clearInterval(polling);polling=null;$('refresh').disabled=false;}}
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab===button));document.querySelectorAll('.page').forEach(page=>page.hidden=page.id!==button.dataset.page);}));
document.querySelectorAll('.download').forEach(button=>button.addEventListener('click',async()=>{try{const r=await fetch(api+button.dataset.report,{cache:'no-store',headers:{Authorization:'Bearer '+accessKey}});if(!r.ok)throw new Error('当前没有可下载的报告。');const objectUrl=URL.createObjectURL(await r.blob()),link=document.createElement('a');link.href=objectUrl;link.download=button.dataset.report==='report-v31'?'goose-phone-report.json':'goose-full-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);}catch(error){$('job').textContent=error.message;}}));
$('refresh').addEventListener('click',async()=>{try{$('refresh').disabled=true;await request('analyze',{});$('job').textContent='已开始，正在核验真实行情…';polling=setInterval(status,1800);await status();}catch(error){$('job').textContent=error.message;$('refresh').disabled=false;}});
function clearPreview(){pending=null;$('confirm').hidden=true;$('preview').replaceChildren();}
function checkAccount(){if(!$('asof').value||!$('broker').value)throw new Error('请先明确选择持仓实际日期和本次券商。');return {asOf:$('asof').value,broker:$('broker').value};}
function showPreview(data){pending=data.previewId;$('preview').replaceChildren(holdingsTable(data.preview));$('confirm').hidden=false;$('import-status').textContent='请逐项核对券商、数量、成本、日期和现金；确认前不会保存为正式持仓。';}
$('import-form').addEventListener('submit',async event=>{event.preventDefault();clearPreview();const file=$('file').files[0];if(!file||file.size>5*1024*1024){$('import-status').textContent='请选择不超过 5 MiB 的 CSV / XLSX 文件。';return;}$('import-status').textContent='正在本机读取并校验，尚未写入正式持仓…';try{const account=checkAccount(),bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));showPreview(await request('preview',{fileName:file.name,content:btoa(binary),...account}));}catch(error){$('import-status').textContent='未导入：'+error.message;}});
function addManualRow(){
 if($('manual-rows').children.length>=30){$('import-status').textContent='手动录入最多30行；更多持仓请使用结构化导出。';return;}
 const row=text('div','','manual-row'),heading=text('div','','report-head'),remove=text('button','移除此行');remove.type='button';heading.append(text('h4','一只持仓'),remove);row.append(heading);
 const fields=[['symbol','证券代码（必填）','例如 600900.SH 或 515450.SH'],['quantity','数量（股 / 份，必填）','请对照 App 填写当前整数数量'],['name','证券名称（可留空）','名称'],['costPrice','成本价（元，可留空）','未知则留空'],['reportedMarketValue','App 显示市值（元，可留空）','未知则留空'],['reportedPnl','App 显示浮盈亏（元，可留空）','未知则留空']];
 for(const [key,title,placeholder] of fields){const label=text('label',title),input=document.createElement('input');input.dataset.field=key;input.placeholder=placeholder;input.autocomplete='off';if(key==='symbol'||key==='quantity')input.required=true;if(!['symbol','name'].includes(key))input.inputMode='decimal';label.append(input);row.append(label);}
 remove.addEventListener('click',()=>{row.remove();clearPreview();});$('manual-rows').append(row);
}
$('add-row').addEventListener('click',addManualRow);addManualRow();
$('manual-form').addEventListener('submit',async event=>{event.preventDefault();clearPreview();try{const account=checkAccount(),rows=[...$('manual-rows').children].map(row=>Object.fromEntries([...row.querySelectorAll('input[data-field]')].map(input=>[input.dataset.field,input.value])));$('import-status').textContent='正在核验手动录入，尚未保存为正式持仓…';showPreview(await request('manual-preview',{...account,rows,availableCash:$('manual-cash').value}));}catch(error){$('import-status').textContent='未导入：'+error.message;}});
for(const id of ['asof','broker','manual-form','import-form'])$(id).addEventListener('input',()=>{if(pending){clearPreview();$('import-status').textContent='输入内容已改变，请重新预览后再确认。';}});
$('confirm').addEventListener('click',async()=>{if(!pending)return;try{$('confirm').disabled=true;await request('confirm',{previewId:pending,confirmed:true});pending=null;$('confirm').hidden=true;$('preview').replaceChildren();$('import-status').textContent='持仓已作为独立快照保存，正在生成新的投资分析。';await reload();await request('analyze',{});if(!polling)polling=setInterval(status,1800);await status();}catch(error){$('import-status').textContent='未保存：'+error.message;}finally{$('confirm').disabled=false;}});
(async()=>{try{csrf=(await request('session')).csrf;await reload();$('connection').textContent='已连接私人电脑服务 · 无自动交易 · 无自动推送';await status();}catch(error){$('connection').className='notice warning';$('connection').textContent=error.message==='PRIVATE_ACCESS_REQUIRED'?'请从 Windows 控制窗口提供的私人完整链接打开；访问凭证不应转发他人。':'暂时无法连接电脑；不会显示缓存冒充最新报告。请保持电脑服务及 Tailscale 在线。';$('refresh').disabled=true;}})();
