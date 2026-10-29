/* Private server view: no analytics, wealth-data storage, IndexedDB or service worker. */
'use strict';
import {decimalDisplay, moneyDisplay, percentDisplay, bpsDisplay, instrumentLabel} from './presentation.js';
const $ = id => document.getElementById(id);
const api = '/investment/api/';
let csrf = '', pending = null, ledgerPending=null, polling = null, viewingHistory = false, renderedSignature = '';
const keyFragment = new URLSearchParams(location.hash.slice(1)).get('key');
if(keyFragment&&/^[A-Za-z0-9_-]{32,128}$/.test(keyFragment)){sessionStorage.setItem('goose-private-access',keyFragment);}
// Keep the private fragment so a bookmark works in a fresh Safari context.
// Fragments are never sent in HTTP requests; the server uses no-referrer.
const accessKey = sessionStorage.getItem('goose-private-access')||'';
const labels = {watch:'观察，不必操作', hold:'继续持有研究', accumulate:'研究分批买入', reduce:'研究分批减仓', missing:'数据缺失', blocked:'核验未通过', historical:'历史参考', verified:'已核验', ready:'分析完成', saved:'已保存', unavailable:'暂无有效数据', current:'当日数据', healthy:'已核验','intraday-verified':'盘中新鲜度通过','closing-snapshot':'带日期收盘快照','current-user-confirmed':'你已确认的当日持仓','stale-snapshot':'过期持仓快照','portfolio-research':'真实持仓研究','holdings-required':'等待当前持仓'};
Object.assign(labels,{'stale-report':'历史报告待更新','historical-snapshot':'已过期的盘中快照','market-unavailable':'本次行情不可用'});
labels['dated-portfolio-research']='最近确认快照研究（非实时账户）';
const show = value => value === null || value === undefined || value === '' ? '未知' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const text = (tag, value, cls) => {const n = document.createElement(tag); n.textContent = value===''?'':show(value); if(cls)n.className=cls; return n;};
const amount = moneyDisplay;
const date = value => {if(!value)return '未知';const d=new Date(value);return Number.isNaN(d.getTime())?show(value):d.toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'})+' 北京时间';};
const strings = value => Array.isArray(value)?value.map(item=>typeof item==='object'?item.message||item.code||JSON.stringify(item):String(item)):value?[show(value)]:[];
const sourceKindLabel=kind=>({manual:'用户手工录入并核对（非券商API）',csv:'CSV 文件导入（用户核对）',xlsx:'Excel 文件导入（用户核对）'}[kind]||'来源类型未记录（原始快照）');
function empty(title, body){const d=text('div','', 'empty');d.append(text('h3',title),text('p',body,'muted'));return d;}
function risks(items){const list=strings(items);if(!list.length)return null;const box=text('div','','risks'), ul=document.createElement('ul');for(const item of list)ul.append(text('li',item));box.append(ul);return box;}
function compactRisks(items){const list=strings(items);if(!list.length)return null;const box=text('div','','risk-summary');const important=risks(list.slice(0,2));if(important)box.append(important);if(list.length>2){const detail=text('details','');detail.append(text('summary',`完整数据与口径说明（另${list.length-2}项）`));detail.append(risks(list.slice(2)));box.append(detail);}return box;}
function metric(label,value){const box=text('div','','metric');box.append(text('span',label),text('strong',value));return box;}
async function request(route, body){const options={cache:'no-store',credentials:'same-origin',headers:{Authorization:'Bearer '+accessKey}};if(body!==undefined){options.method='POST';options.headers['Content-Type']='application/json';options.headers['X-Goose-CSRF']=csrf;options.body=JSON.stringify(body);}const response=await fetch(api+route,options);let data;try{data=await response.json();}catch{throw new Error('服务返回异常，请检查电脑控制窗口。');}if(!response.ok)throw new Error(data.message||data.error||'请求失败');return data;}
function holdingsTable(value){const box=text('div','');const positions=value.positions||[];box.append(text('p',`持仓日期：${show(value.asOf)} · 券商：${show(value.source?.broker||'未记录')} · 来源：${sourceKindLabel(value.source?.kind)}`,'meta'));box.append(text('p',`可用资金：${amount(value.availableCash)}。缺失金额不会按零处理。`,'meta'));const scroll=text('div','','table-scroll'), table=document.createElement('table'), head=document.createElement('thead'), hr=document.createElement('tr');for(const name of ['标的','数量','成本价','导入/录入市值','导入/录入盈亏'])hr.append(text('th',name));head.append(hr);table.append(head);const tbody=document.createElement('tbody');for(const p of positions){const tr=document.createElement('tr');for(const val of [instrumentLabel(p.name,p.symbol),show(p.quantity),p.costPrice==null?'未知':decimalDisplay(p.costPrice,6),amount(p.reportedMarketValue),amount(p.reportedPnl)])tr.append(text('td',val));tbody.append(tr);}table.append(tbody);scroll.append(table);box.append(scroll);const note=risks(value.warnings);if(note)box.append(note);return box;}
function financialTable(headers, rows){const scroll=text('div','','table-scroll'),table=document.createElement('table'),head=document.createElement('tr');for(const h of headers)head.append(text('th',h));table.append(head);for(const values of rows){const row=document.createElement('tr');for(const v of values)row.append(text('td',v));table.append(row);}scroll.append(table);return scroll;}
function renderReport(report){
 renderResearch(report);
 renderedSignature=reportSignature(report);
 const target=$('report');target.replaceChildren();
 if(!report){target.append(empty('尚未生成当前分析','先在“更新持仓”导入并核对真实持仓，然后点击“更新行情与分析”。没有新报告时，不会用旧历史信号填充。'));return;}
 const card=text('article','','card'),header=text('div','','report-head'),info=text('div','');
 info.append(text('h3','投资决策摘要'),text('p',`生成于 ${date(report.generatedAt)}`,'meta'),text('p',`行情 / 分析日期 ${show(report.asOf)} · 报告 ${show(report.id)}`,'meta'));
 header.append(info,text('span',labels[report.status]||show(report.status),'badge'));
 card.append(text('p',report.dataMode==='closing-snapshot'?'收盘快照研究 · 不能作为盘中信号':report.dataMode==='intraday'?'本次盘中观察 · 超过90秒需重新核验':'带日期研究 · 数据质量见下方','data-mode'));card.append(header,text('p',report.summary||'请结合下方事实、研究信号与数据限制判断。','summary'));
 const warning=compactRisks(report.warnings);if(warning)card.append(warning);
 const portfolio=report.portfolio||{},totals=text('div','','metrics');
 totals.append(metric('核验报价重估市值',amount(portfolio.marketValue)),metric('按成本价重估差额',amount(portfolio.computedPnl??portfolio.pnl)),metric('导入/录入盈亏（原值）',amount(portfolio.reportedPnl)),metric('导入/录入可用资金',amount(portfolio.availableCash)));card.append(totals);
 card.append(text('p',`持仓状态：${labels[portfolio.status]||show(portfolio.status)} · 持仓日期：${show(portfolio.asOf)}`,'meta'));
 if(portfolio.concentration!==null&&portfolio.concentration!==undefined)card.append(text('p',`最大单一标的占已导入证券市值 ${percentDisplay(portfolio.concentration)}。此占比不含现金和其他资产。`,'basis'));
 if(portfolio.positions?.length)card.append(financialTable(['持仓 / 券商','数量','重估市值','重估差额','导入/录入盈亏','证券占比'],portfolio.positions.map(p=>[instrumentLabel(p.name,p.symbol)+' / '+(p.broker||'未标注'),show(p.quantity),amount(p.marketValue),amount(p.computedPnl??p.pnl),amount(p.reportedPnl),percentDisplay(p.weightPct)])));

 if(portfolio.pnlDifference!=null)card.append(text('p',`重估差额 − 导入/录入盈亏 = ${amount(portfolio.pnlDifference)}。两者时间与成本/费用口径可能不同，不覆盖原值。`,'basis'));
 const reconciliation=text('details','');reconciliation.append(text('summary','逐项核对市值、成本与盈亏口径'));if(portfolio.costBasis)reconciliation.append(text('p',portfolio.costBasis,'footnote'));if(portfolio.feeBasis)reconciliation.append(text('p',portfolio.feeBasis,'footnote'));
 for(const p of portfolio.positions||[]){const entry=text('div','','reconciliation');entry.append(text('h4',instrumentLabel(p.name,p.symbol)),text('p',`券商 ${show(p.broker)}；来源：${sourceKindLabel(p.reportedSourceKind)}；记录日期 ${show(p.reportedAsOf||p.holdingAsOf)}；报价时间 ${show(p.quote?.sourceTimestamp)}。`,'meta'),text('p',`导入/录入市值 ${amount(p.reportedMarketValue)}；按本次报价重估 ${amount(p.marketValue)}；导入/录入盈亏 ${amount(p.reportedPnl)}；按成本价计算差额 ${amount(p.computedPnl??p.pnl)}。`,'basis'));const notes=risks(p.pnlDifferenceBasis||p.reconciliationWarnings||p.warnings);if(notes)entry.append(notes);reconciliation.append(entry);}if(portfolio.positions?.length)card.append(reconciliation);
 const pw=risks((portfolio.warnings||[]).filter(item=>!(report.warnings||[]).includes(item)));if(pw)card.append(pw);
 for(const instrument of report.instruments||[]){
  const block=text('section','','instrument'),q=instrument.quote||{},signal=typeof instrument.signal==='object'?instrument.signal:{},title=text('div','','report-head');
  title.append(text('h3',instrumentLabel(instrument.name,instrument.symbol)),text('span',typeof instrument.signal==='string'?instrument.signal:labels[instrument.stance||signal.stance||signal.action]||'观察，不必操作','badge'));block.append(title);
  block.append(text('p',`行情来源：${show(q.source||q.provider||q.sourceName)} · 行情时间：${show(q.sourceTime||q.timestamp||q.sourceTimestamp||q.asOf||q.tradeTime)}`,'meta'));
  const qm=text('div','','metrics'),quality=q.quality?.status||q.status;
  qm.append(metric('核验价格（元）',show(q.price??q.last??q.lastPrice)),metric('数据状态',labels[quality]||show(quality)),metric('持有数量',show(instrument.scenarioAnalysis?.facts?.quantityFromConfirmedSnapshot??instrument.quantity??portfolio.positions?.find(p=>p.symbol===instrument.symbol)?.quantity)));block.append(qm);const modelBasis=text('details','');modelBasis.append(text('summary','原V1模型与报价计算依据'));
  for(const line of strings(instrument.basis||signal.basis||'数据不足时保持观察，不生成今日买卖结论。'))modelBasis.append(text('p',line,'basis'));block.append(modelBasis);
  const analysis=instrument.scenarioAnalysis;
  if(analysis){
   const headline=text('div','','scenario-head');headline.append(text('h4',analysis.summary),text('p',analysis.modelConditions?.window||'仅条件研究','meta'));if(analysis.modelConditions?.thresholdNote)headline.append(text('p',analysis.modelConditions.thresholdNote,'footnote'));
   const facts=analysis.facts||{},numbers=text('div','','metrics');numbers.append(metric('完整日收盘距MA20',percentDisplay(facts.completedCloseVsMa20Pct)),metric('完整日成交量比',facts.completedDayVolumeRatio==null?'未知':decimalDisplay(facts.completedDayVolumeRatio,2)+' 倍'),metric('完整日价格回撤',percentDisplay(facts.historicalMaxPriceDrawdownPct)));headline.append(numbers);block.append(headline);
   const states={'preferred':'优先观察','available':'可参考','conditional-fit':'具备部分研究条件','conditional':'条件研究','conditional-not-actionable':'证据待补，暂不操作','condition-not-met':'条件未满足','blocked':'暂停方向研究'};
   for(const scenario of analysis.scenarios||[]){const detail=text('details','','scenario');detail.append(text('summary',scenario.label+' · '+(states[scenario.status]||scenario.status)),text('p',scenario.trigger,'basis'));const basis=risks(scenario.basis);if(basis)detail.append(basis);const notes=risks(scenario.risks);if(notes)detail.append(notes);block.append(detail);}
   const eq=risks(analysis.evidenceQuality?.reasons);if(eq)block.append(eq);
  }
  const ir=risks((instrument.risks||[]).filter(r=>!(report.warnings||[]).includes(r)));if(ir)block.append(ir);
  const m=instrument.metrics;
  if(m){const detail=text('details','');detail.append(text('summary','趋势与波动（仅用上一完整交易日）'),text('p',`截至 ${show(m.through)}；前收盘 ${show(m.previousClose)} 元；MA20 ${show(m.ma20)} 元。`,'basis'),text('p',`原V1规则：${{above:'前收盘在MA20之上',below:'前收盘在MA20之下',equal:'前收盘等于MA20'}[m.v1Comparison]||'未知'}；均线交叉对照：${{up:'上穿',down:'下穿',none:'无交叉'}[m.cross]||'未知'}。`,'basis'),text('p',`历史价格最大回撤 ${show(m.maxPriceDrawdownPct)}%；年化价格波动 ${show(m.annualizedPriceVolatilityPct)}%；最近成交量 / 前19日均量 ${show(m.latestVsPrior19VolumeRatio)}。`,'basis'),text('p','以上为未复权价格指标，除权和分红可能影响结果。前复权趋势与现金分红总收益分开，不作完整含息收益声称。','footnote'));if(m.qfqContext)detail.append(text('p',`独立前复权趋势：${show(m.qfqContext.previousClose)} 元 / MA20 ${show(m.qfqContext.ma20)} 元；仅供应商调整价格，不等于含息总回报。`,'basis'));block.append(detail);}
  const bt=instrument.backtest;
  if(bt){const b=text('details','');b.append(text('summary','同区间回测与基准（价格路径）'),text('p',`${show(bt.from)} 至 ${show(bt.to)}。未完整处理分红、税款与再投资，不是完整投资收益。`,'footnote'));
   const pct=bpsDisplay,names={buy_hold:'买入并持有',v1_ma20:'原V1前收盘/MA20',ma20_cross:'MA20交叉对照',periodic:'定期投入'};
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
 viewingHistory=false;renderReport(latest.report);$('history-list').replaceChildren();
 if(!history.reports.length)$('history-list').append(empty('还没有报告历史','每次点击更新后，报告将独立保存到电脑。'));
 for(const item of history.reports){
  const row=text('article','','history-item'),open=text('button','查看这份报告');
  row.append(text('span',date(item.generatedAt),'meta'),text('p','当时结论：'+(item.summary||labels[item.status]||item.status)),text('p',`分析日期 ${show(item.asOf)} · ${show(item.id)}`,'meta'));
  open.addEventListener('click',async()=>{try{const data=await request('history/'+encodeURIComponent(item.id));viewingHistory=true;renderReport(data.report);document.querySelector('[data-page="overview"]').click();$('job').textContent='正在查看指定日期的历史记录。点“更新行情与分析”生成新报告。';window.scrollTo({top:0,behavior:'smooth'});}catch(error){$('job').textContent=error.message;}});
  row.append(open);$('history-list').append(row);
 }
 $('current-holdings').replaceChildren();
 const accounts=holdings.accounts||[];
 if(!accounts.length)$('current-holdings').append(empty('尚无你确认的当前真实持仓','请分别为华宝证券、银河证券录入。历史快照不会自动作为当前持仓。'));
 for(const account of accounts){const card=text('article','','card');card.append(text('h3',account.source?.broker||'用户确认账户'),holdingsTable(account));$('current-holdings').append(card);}
 const present=new Set(accounts.map(a=>a.source?.broker));
 for(const name of ['华宝证券','银河证券'])if(!present.has(name))$('current-holdings').append(empty(name+'：尚未更新','该券商的持仓和现金保持未知，不会从另一个账户或旧记录推断。'));
 await loadLedger();
 await brokerAccess();
}
async function status(){try{const state=await request('status');$('refresh').disabled=state.running;if(state.running&&!polling)polling=setInterval(status,1800);$('job').textContent=state.running?'正在核验行情并生成报告；无需再次点击。':state.error?'本次分析失败，未把旧报告当作新结果。请检查电脑运行诊断。':state.finishedAt?'本次运行已结束：'+date(state.finishedAt):'';if(!state.running){clearInterval(polling);polling=null;await reload();}}catch(error){$('job').textContent=error.message;clearInterval(polling);polling=null;$('refresh').disabled=false;}}
document.querySelectorAll('.tab').forEach(button=>button.addEventListener('click',()=>{document.querySelectorAll('.tab').forEach(tab=>tab.classList.toggle('active',tab===button));document.querySelectorAll('.page').forEach(page=>page.hidden=page.id!==button.dataset.page);}));
document.querySelectorAll('.download').forEach(button=>button.addEventListener('click',async()=>{try{const r=await fetch(api+button.dataset.report,{cache:'no-store',headers:{Authorization:'Bearer '+accessKey}});if(!r.ok)throw new Error('当前没有可下载的报告。');const objectUrl=URL.createObjectURL(await r.blob()),link=document.createElement('a');link.href=objectUrl;link.download=button.dataset.report==='report-v31'?'goose-phone-report.json':'goose-full-report.json';link.click();setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);}catch(error){$('job').textContent=error.message;}}));
$('refresh').addEventListener('click',async()=>{try{$('refresh').disabled=true;await request('analyze',{});$('job').textContent='已开始，正在核验真实行情…';polling=setInterval(status,1800);await status();}catch(error){$('job').textContent=error.message;$('refresh').disabled=false;}});
function clearPreview(){pending=null;$('confirm').hidden=true;$('preview').replaceChildren();}
function checkAccount(){if(!$('asof').value||!$('broker').value)throw new Error('请先明确选择持仓实际日期和本次券商。');return {asOf:$('asof').value,broker:$('broker').value};}
function showPreview(data){pending=data.previewId;$('preview').replaceChildren(holdingsTable(data.preview));if(data.reconciliation){const r=data.reconciliation;$('preview').append(text('h3',r.status==='quantity-matches'?'数量对账相符（其他口径待核）':'数量对账存在差异'),financialTable(['代码','成交推演','券商快照','差额'],r.rows.map(x=>[x.symbol,show(x.projected),show(x.brokerSnapshot),show(x.difference)])),text('p',r.warning,'footnote'));}$('confirm').hidden=false;$('import-status').textContent='请逐项核对券商、数量、成本、日期和现金；确认前不会保存为正式持仓。';}
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
(async()=>{try{csrf=(await request('session')).csrf;await reload();await automationStatus();$('connection').textContent='已连接私人电脑服务 · 私人地址仅保存到自己的手机，勿转发';await status();}catch(error){$('connection').className='notice warning';$('connection').textContent=error.message==='PRIVATE_ACCESS_REQUIRED'?'请从 Windows 控制窗口提供的私人完整链接打开；访问凭证不应转发他人。':'暂时无法连接电脑；不会显示缓存冒充最新报告。请保持电脑服务及 Tailscale 在线。';$('refresh').disabled=true;}})();

async function automationStatus(){
 try{
  const result=await request('automation'),box=$('automation-status');box.replaceChildren();
  box.append(text('h3',result.control?.paused?'自动分析已暂停':result.enabled===true?'每日自动报告已启用':result.enabled===false?'每日自动任务未启用':'自动任务状态暂不可读取'));
  box.append(text('p',`模式：真实账户只读分析；不执行证券订单。任务 ${result.task?.state||'未知'}；最近运行 ${date(result.task?.lastRun)}；下次 ${date(result.task?.nextRun)}。`,'meta'));
  box.append(text('p','交易日14:38运行，14:43:30停止采集，14:44投递截止。失败不沿用旧信号，投递结果不明不重发。','basis'));
  const n=result.latest?.notification;
  if(result.latest)box.append(text('p',`最近研究：${show(result.latest.day)} · ${show(result.latest.code)}；${n?.status==='unknown'?'投递结果未知，禁止重发':n?.sent?n.code==='SERVICE_ACK_AFTER_DEADLINE'?'服务接受但已过截止':'Bark服务已接受（不等于手机到达）':n?.status==='blocked'?'发送被安全规则阻断':'没有实际发送'}。`,'meta'));
  box.append(text('p',result.calendar?.nextYear||'日历边界待核验','footnote'));
  const pause=text('button',result.control?.paused?'恢复后续自动分析':'暂停后续自动分析');
  pause.addEventListener('click',async()=>{const paused=!result.control?.paused;if(!window.confirm(paused?'暂停后续自动分析和Bark？原任务保留，不删除报告。':'恢复下次计划运行？不会补跑或重发今天的报告。'))return;try{await request('automation-control',{paused,confirmed:true});await automationStatus();}catch(error){$('job').textContent=error.message;}});box.append(pause);
 }catch{$('automation-status').replaceChildren(text('h3','运行状态读取失败'),text('p','未推断任务已关闭，也未修改原任务。','meta'));}
}

function reportSignature(report){return report?JSON.stringify([report.id,report.status,(report.instruments||[]).map(i=>[i.quote?.quality?.status,i.scenarioAnalysis?.evidenceQuality?.level])]):'';}
async function refreshLatestView(){
 if(document.visibilityState!=='visible'||viewingHistory||!csrf)return;
 try{const result=await request('latest');if(reportSignature(result.report)!==renderedSignature)renderReport(result.report);$('connection').className='notice';$('connection').textContent='私人电脑已连接 · 新日报会自动显示 · 地址仅供自己保存';}
 catch{$('connection').className='notice warning';$('connection').textContent='电脑服务暂不可达；已显示内容仅供历史参考，不能当作当前行情。';}
}
setInterval(refreshLatestView,30000);
document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshLatestView();});

function lineChart(series){
 const box=text('div','','price-chart');if(!series?.length)return box;
 const values=series.map(x=>Number(x.close));if(values.some(x=>!Number.isFinite(x)))return box;
 const low=Math.min(...values),high=Math.max(...values),spread=high-low||1;
 const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 320 90');svg.setAttribute('role','img');svg.setAttribute('aria-label','最近完整交易日未复权价格走势');
 const line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',values.map((v,i)=>`${5+i*310/Math.max(1,values.length-1)},${80-(v-low)/spread*70}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke','#2a765c');line.setAttribute('stroke-width','2');svg.append(line);box.append(svg,text('p',`${series[0].date} — ${series.at(-1).date} · 未复权价格，不是含息收益`,'meta'));return box;
}
function renderResearch(report){
 const box=$('research-report');if(!box)return;box.replaceChildren();
 if(!report){box.append(empty('尚无研究报告','生成新报告后显示，旧数据不会伪装为今天。'));return;}
 for(const i of report.instruments||[]){const r=i.research,card=text('article','','card');card.append(text('h3',instrumentLabel(i.name,i.symbol)));
  if(!r){card.append(text('p','这份旧报告尚无新增研究插件，原报告内容仍完整保留。'));box.append(card);continue;}
  card.append(text('p',`完整交易日截止 ${show(r.through)}；插件结果生成于 ${date(r.generatedAt)}`,'meta'),lineChart(r.priceSeries));
  for(const plugin of r.plugins||[]){const detail=text('details','');detail.append(text('summary',plugin.summary));
   if(plugin.ma60)detail.append(financialTable(['指标','值'],[['MA20（元）',plugin.ma20],['MA60（元）',plugin.ma60],['收盘距MA60',percentDisplay(plugin.closeVsMa60Pct)],['MA20五日变化',percentDisplay(plugin.ma20FiveSessionSlopePct)],['突破前20日最高',plugin.breakoutPrior20High===true?'是（历史事实）':plugin.breakoutPrior20High===false?'否':'未知']]));
   for(const value of [plugin.trigger,plugin.invalidation,...(plugin.evidence||[]),...(plugin.risks||[])])if(value)detail.append(text('p',value,'basis'));
   detail.append(text('p',plugin.backtestVerified?'已验证范围见具体报告':'此插件尚未完成独立样本外回测，不借用旧策略结果宣称验证。','footnote'));card.append(detail);
  }
  if(r.risk60)card.append(text('p',`近60个完整日价格最大回撤 ${percentDisplay(r.risk60.maxPriceDrawdownPct)}；价格波动 ${percentDisplay(r.risk60.annualizedPriceVolatilityPct)}；价格序列夏普 ${show(r.risk60.priceSharpeRfZero)}（无风险利率0假设，不是账户/策略收益）。`,'basis'));
  const notes=risks(r.limitations);if(notes)card.append(notes);box.append(card);
 }
 if(report.portfolioRisk){const card=text('article','','card');card.append(text('h3','组合分散风险'));for(const pair of report.portfolioRisk.pairs||[])card.append(text('p',`${pair.symbols.join(' / ')}：${pair.observations}个共同交易日价格收益相关性 ${show(pair.correlation)}，截至${pair.through}。`));const notes=risks(report.portfolioRisk.warnings);if(notes)card.append(notes);box.append(card);}
}
async function loadLedger(){
 const box=$('ledger-state');if(!box)return;
 try{const value=await request('ledger');box.replaceChildren(text('h3','分券商核验与成交推演'));
  for(const a of value.accounts){const card=text('article','','card');card.append(text('h3',a.broker),text('p',`券商快照日期 ${a.baselineAsOf}；最后人工核验 ${date(a.lastUserVerifiedAt)}；${a.source}。`,'meta'),text('p',`基准之后已确认事件 ${a.eventCount}；同日未自动计入 ${a.sameDayUnapplied}。`));
   if(a.projectedQuantities)card.append(financialTable(['代码','成交推演数量（待对账）'],Object.entries(a.projectedQuantities).map(([s,q])=>[s,show(q)])));
   card.append(text('p',`流水算术桥（非账户余额） ${amount(a.cashReconciliationBridge)}；导入原报的区间已实现盈亏 ${amount(a.brokerReportedRealizedPnl)}。这两项不是可用资金或系统估算净利润。`,'basis'));const note=risks(a.warnings);if(note)card.append(note);box.append(card);
  }
  for(const b of value.batches||[]){const row=text('article','','history-item');row.append(text('p',`${b.broker} · ${b.coverageStart}—${b.coverageEnd} · ${b.eventCount}条`));const undo=text('button','撤销误导入（保留原记录）');undo.addEventListener('click',async()=>{if(!window.confirm('仅撤销此批次的推演作用，原成交文件记录仍保留。确认？'))return;try{await request('trade-undo',{batchId:b.batchId,confirmed:true});await loadLedger();await request('analyze',{});if(!polling)polling=setInterval(status,1800);await status();}catch(error){$('trade-message').textContent=error.message;}});row.append(undo);box.append(row);}
 }catch(error){box.replaceChildren(empty('成交推演暂停',error.message));}
}
function clearTrade(){ledgerPending=null;$('trade-preview').replaceChildren();$('trade-confirm').hidden=true;}
async function brokerAccess(){const box=$('broker-access');if(!box)return;try{const s=await request('broker-access');box.replaceChildren(text('p',s.currentFallback,'basis'));for(const b of s.brokers){const link=text('a','官方产品资料');link.href=b.url;link.target='_blank';link.rel='noopener noreferrer';box.append(text('h4',b.broker+' · '+b.eligibility),text('p',b.officialEvidence),link,text('p',b.contact,'meta'));}box.append(text('p',s.questionToBroker,'basis'));}catch{box.replaceChildren(text('p','接入状态暂不可读取，没有自动连接券商账户。'));}}
$('trade-form').addEventListener('input',clearTrade);$('broker').addEventListener('change',clearTrade);
$('trade-form').addEventListener('submit',async event=>{event.preventDefault();clearTrade();try{
 const file=$('trade-file').files[0],broker=$('broker').value;if(!broker||!file||file.size>5*1024*1024)throw new Error('请选择券商及不超过5MiB的成交CSV/XLSX。');
 const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=32768)binary+=String.fromCharCode(...bytes.subarray(i,i+32768));
 const result=await request('trade-preview',{broker,fileName:file.name,content:btoa(binary),coverageStart:$('trade-start').value,coverageEnd:$('trade-end').value,completeCoverage:$('trade-complete').checked});
 ledgerPending=result.previewId;const p=result.preview,c=result.eventCheck,box=$('trade-preview');box.append(text('h3',`${p.broker}：新增${c.newEvents.length}条，重复${c.duplicateIds.length}条，冲突${c.conflictIds.length}条`),financialTable(['日期/编号','业务/标的','数量','成交金额','总费用','资金发生额'],p.events.slice(0,30).map(e=>[e.date+' / '+e.id,e.kind+' / '+(e.symbol||'资金'),show(e.quantity),amount(e.gross),amount(e.fees),amount(e.netCash)])));const warn=risks(p.warnings);if(warn)box.append(warn);
 $('trade-confirm').hidden=!!c.conflictIds.length||!c.newEvents.length;$('trade-message').textContent=c.conflictIds.length?'编号冲突，停止保存，未覆盖旧记录。':p.events.length>30?'预览前30条，确认前请完整核对原文件。':'确认前没有写入正式记录。';
 }catch(error){$('trade-message').textContent=error.message;}});
$('trade-confirm').addEventListener('click',async()=>{if(!ledgerPending)return;try{const result=await request('confirm',{previewId:ledgerPending,confirmed:true});clearTrade();$('trade-message').textContent=`已追加${result.newCount}条，跳过${result.duplicateCount}条已存在事件；原持仓未改写。`;await loadLedger();await request('analyze',{});if(!polling)polling=setInterval(status,1800);await status();}catch(error){$('trade-message').textContent=error.message;}});
