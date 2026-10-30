import {moneyDisplay,bpsDisplay,percentDisplay} from './presentation.js';
const names={buy_hold:'买入持有',trend_20_60:'MA20/60趋势',breakout_20_10:'20日突破/10日退出',relative_momentum_60:'60日相对动量',low_vol_drawdown:'低波/回撤控制'};
const node=(tag,value,cls)=>{const n=document.createElement(tag);if(value!=null)n.textContent=String(value);if(cls)n.className=cls;return n;};
function paragraph(box,value,cls='basis'){if(value)box.append(node('p',value,cls));}
function table(headers,rows){const box=node('div',null,'table-scroll'),t=node('table'),head=node('tr');headers.forEach(h=>head.append(node('th',h)));t.append(head);for(const row of rows){const tr=node('tr');row.forEach(v=>tr.append(node('td',v??'未知')));t.append(tr);}box.append(t);return box;}
function sampleChart(strategies,initial){
 const box=node('div',null,'price-chart'),svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 320 110');svg.setAttribute('role','img');svg.setAttribute('aria-label','同一研究本金的价格策略净值曲线，非含息总回报');
 const entries=Object.entries(strategies).filter(([,s])=>s.applicable!==false),values=entries.flatMap(([,s])=>s.curvePreview.map(r=>Number(r.equity)/Number(initial)));
 if(!values.length)return box;const low=Math.min(1,...values),high=Math.max(1,...values),spread=high-low||1,colors=['#244e40','#1469a8','#ae6624','#7b569d','#b04f5a'];
 entries.forEach(([name,s],n)=>{const line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('fill','none');line.setAttribute('stroke',colors[n%colors.length]);line.setAttribute('stroke-width','2');line.setAttribute('points',s.curvePreview.map((r,i)=>`${5+i*310/Math.max(1,s.curvePreview.length-1)},${100-(Number(r.equity)/Number(initial)-low)/spread*90}`).join(' '));const title=document.createElementNS(svg.namespaceURI,'title');title.textContent=names[name];line.append(title);svg.append(line);});
 box.append(svg,node('p','顺序：买入持有、趋势、突破、相对动量、低波；完整曲线与成交见下载。','meta'));return box;
}
export function renderRadar(report){
 const target=document.getElementById('opportunity-radar');if(!target)return;target.replaceChildren();const r=report?.opportunityRadar,card=node('article',null,'card');card.append(node('h2','每日投资机会雷达'));
 if(!r){paragraph(card,'这份历史报告尚无机会雷达，下次分析时生成；不把旧名单称为今天的机会。');target.append(card);return;}
 paragraph(card,`因子截至 ${r.through||'未取得有效日期'} · 最近完整交易日研究，非实时买卖信号。`,'meta');
 paragraph(card,'来源：腾讯公开报价及日线；同源字段/价格口径核验，尚无独立多源验证。','meta');
 if(r.factorBasis?.momentum20==='unadjusted-subject-price'&&r.factorBasis?.relative60==='unadjusted-price-both-sides')paragraph(card,'指标口径：20日标的动量与60日相对动量使用逐日对应的未复权价格；MA趋势、60/120日标的动量、突破、波动、回撤及持仓相关性保留前复权口径。价格动量不是总回报动量。','basis');
 else paragraph(card,'这份历史雷达未标记新版20日/60日未复权动量口径，原记录保留，不将旧分数当作修正后结果。','warning');
 if(r.synthetic){paragraph(card,'虚构界面测试，不能用于投资。','warning');target.append(card);return;}
 if(report.testData===true)paragraph(card,'完全虚构的隔离界面测试，不是真实行情或推荐。','warning');
 if(r.accountConstraints){paragraph(card,`持仓依据 ${r.accountConstraints.snapshotAsOf||'未知'}；证券内部最大集中度 ${percentDisplay(r.accountConstraints.securitiesConcentrationPct)}。`);paragraph(card,r.accountConstraints.message,'footnote');}
 for(const [kind,title] of [['etf','ETF优先观察'],['stock','A股优先观察']]){
  const group=r.groups?.[kind],coverage=r.coverage?.[kind];card.append(node('h3',title));
  if(coverage)paragraph(card,`维护${coverage.configured}只 · 历史通过${coverage.historyValidated}只 · 可比较${coverage.rankable}只`,'meta');
  const candidates=group?.candidates||[];
  if(!candidates.length)paragraph(card,group?.message||'暂无符合条件的机会','notice');
  for(const item of candidates){const d=node('details',null,'scenario');d.append(node('summary',`${item.name} ${item.symbol} · 研究分 ${item.score.value}/100`));
   const trust=item.trustResearch;paragraph(d,`数据可信等级 ${trust?.dataTrustGrade||'未评定'}：${trust?.dataTrustLabel||'证据尚未补齐'}。`,'warning');
   paragraph(d,'评分是条件研究排序，不是胜率或买入建议。');
   if(trust){paragraph(d,trust.sourceEvidence?.complete?'当前研究区间现金流覆盖完整；价格版本与点时证据仍需区分。':`当前完整含息回测未通过覆盖检查${trust.verifiedWindow?'；仅'+trust.verifiedWindow.from+'—'+trust.verifiedWindow.to+'有核验区间结果':''}。`);paragraph(d,trust.advantageConclusion||'尚未证明优于买入持有','warning');}
   const tests=item.strategyQuality||[];
   if(tests.length)d.append(table(['规则','留出价格收益超出买入持有','证据结论'],tests.map(s=>[names[s.strategy],s.folds.test?bpsDisplay(s.folds.test.excessReturnBps):'无完整留出',s.conclusion])));
   paragraph(d,`历史抓取 ${item.quality?.historyFetchedAt||'见研究文件'}；公开报价源时刻 ${item.quality?.quote?.sourceTimestamp||'未取得'}。`,'meta');
   for(const reason of item.reasons||[])paragraph(d,reason);paragraph(d,'触发关注：'+item.trigger);paragraph(d,'失效条件：'+item.invalidation);
   for(const risk of item.risks||[])paragraph(d,risk,'footnote');d.append(table(['评分项','得分','依据'],item.score.parts.map(p=>[p.factor,`${p.points}/${p.maxPoints}`,p.basis])));card.append(d);
  }
 }
 const all=node('details');all.append(node('summary','查看全部观察池与未入选原因'));
 all.append(table(['标的','类型/分数','数据日期/状态','未入选原因'],(r.items||[]).map(i=>[i.name+' '+i.symbol,i.kind+' / '+(i.score?.value??'未评分'),(i.through||'未知')+' / '+(i.quality?.status||'未知'),[...(i.quality?.codes||[]),...(i.rejections||[]),i.quarantineReview?.cause,i.quarantineReview?.remaining].filter(Boolean).join('；')||'通过研究门槛'])));card.append(all);
 if(r.strategyQuality)paragraph(card,'策略质量结论：'+r.strategyQuality.conclusion+'。同类标的收益相关，胜出次数不能当作独立统计证据。','warning');
 if(r.strategyQuality&&r.strategyQuality.version<3)paragraph(card,'此为基准口径修正前的旧版统计，原记录保留，不沿用为本次新结论。','warning');
 if(r.strategyQuality?.familyComplete===false)paragraph(card,'本次统计因耗时预算或比较集合不完整而未完成，不显示部分显著性；尚未证明优势，不等于证明策略无效。','footnote');
 for(const note of r.limitations||[])paragraph(card,note,'footnote');target.append(card);
}
export function renderLabs(report,request){
 const target=document.getElementById('research-report');if(!target)return;const r=report?.opportunityRadar;if(!r?.items?.length)return;
 const panel=node('article',null,'card');panel.append(node('h3','固定规则策略实验室 · 历史留出比较'));
 paragraph(panel,'各策略、各分区均独立以100万元研究本金起步；不是你的实际账户资金。成交使用原始开盘价。新版相对动量两侧均用未复权价格，是价格动量，不是总回报动量；其他规则保留原有指标口径。');
 paragraph(panel,'训练：2024-09起至2025-06；验证：2025下半年；留出：2026年至数据截止。固定参数，无测试期择优，仍有存续池选择偏差。','footnote');
 for(const item of r.items){if(!item.lab)continue;const lab=item.lab,detail=node('details');detail.append(node('summary',item.name+' '+item.symbol+' · 截至'+lab.to));
  if(lab.relativeMomentumBasis!=='unadjusted-price-both-sides')paragraph(detail,'历史旧版实验：相对动量使用旧供应商复权口径，非本次修正后的价格相对动量。原文件保留供复核。','warning');
  paragraph(detail,`统一假设：佣金${lab.config.commissionBps}bps、最低${moneyDisplay(String(lab.config.minCommission/100))}，滑点${lab.config.slippageBps}bps并按价格跳动保守取整；股票卖出印花税${lab.config.stampDutyBps}bps，ETF不收此税。`,'footnote');
  detail.append(sampleChart(lab.strategies,lab.config.initialCash));
  detail.append(table(['策略','全段价格收益','留出收益','留出回撤幅度','留出夏普(Rf=0)','留出次数/换手','留出费用+滑点'],Object.entries(lab.strategies).map(([name,s])=>{const m=s.partitions.test.metrics;return s.applicable===false?[names[name],'不适用',s.notApplicableReason,'—','—','—','—']:[names[name],bpsDisplay(s.metrics.totalReturnBps),bpsDisplay(m.totalReturnBps),bpsDisplay(m.maxDrawdownBps),m.sharpeRfZero??'未知',`${m.tradeCount} / ${percentDisplay(m.oneWayTurnoverPct)}`,moneyDisplay(String(Number(m.cost)/100))];})));
  const benchmark=r.benchmark?.comparison?.partitions?.test?.metrics;
  if(benchmark)paragraph(detail,`同区间市场代理（${r.benchmark.symbol}买入持有）留出价格收益 ${bpsDisplay(benchmark.totalReturnBps)}；不等于指数总回报。`);
  for(const [key,label] of [['train','训练'],['validation','验证'],['test','历史留出']]){
   const sub=node('details');sub.append(node('summary',label+'完整指标'));
   sub.append(table(['策略','区间','收益','年化收益','波动(252日换算)','回撤幅度','夏普(Rf=0)','次数','单边换手'],Object.entries(lab.strategies).filter(([,s])=>s.applicable!==false).map(([name,s])=>{const p=s.partitions[key],m=p.metrics;return[names[name],p.from+'—'+p.to,bpsDisplay(m.totalReturnBps),m.annualizedReturnBps==null?'期限不足不年化':bpsDisplay(m.annualizedReturnBps),bpsDisplay(m.volatilityBps),bpsDisplay(m.maxDrawdownBps),m.sharpeRfZero??'未知',m.tradeCount,percentDisplay(m.oneWayTurnoverPct)];})));detail.append(sub);
  }
  for(const note of lab.limitations)paragraph(detail,note,'footnote');const button=node('button','下载完整资金曲线、信号及成交记录');button.addEventListener('click',async()=>{try{const data=await request('radar-lab/'+lab.artifactKey);const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'})),a=node('a');a.href=url;a.download='goose-lab-'+item.symbol+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}catch{paragraph(detail,'研究文件暂不可读取，未用其他文件替代。','warning');}});detail.append(button);panel.append(detail);
 }
 target.prepend(panel);
 renderTrust(report,request);
}

function downloadStudy(box,study,request){
 const button=node('button','下载此区间完整含息／价格研究记录');
 button.addEventListener('click',async()=>{try{const data=await request('radar-lab/'+study.artifactKey);const url=URL.createObjectURL(new Blob([JSON.stringify(data)],{type:'application/json'})),a=node('a');a.href=url;a.download='goose-event-return-'+study.symbol+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);}catch{paragraph(box,'研究原件暂不可读取，未用其他记录代替。','warning');}});box.append(button);
}
function metricsByMode(box,study,request){
 paragraph(box,`同区间 ${study.from}—${study.to}；研究账户，与券商真实投资收益分开。`);
 if(study.synthetic)paragraph(box,'完全虚构的测试研究，不是真实证券表现。','warning');
 paragraph(box,study.taxAssumption,'footnote');
 if(study.indicatorActionCoverage&&!study.indicatorActionCoverage.complete)paragraph(box,'指标预热期企业行为证据不完整：依赖指标的含息/再投资结果已阻断；买入持有按自身账户区间单独核验。','warning');
 const policy=study.relativeMomentumPolicy;
 if(!policy)paragraph(box,'基准口径修正前的历史旧版记录：相对动量数字不作为当前结论，原件保留；需查看新研究。','warning');
 else{
  paragraph(box,`相对动量核验范围（包含预热）：${policy.requiredFrom}—${policy.requiredTo}。价格动量两侧均为未复权价，不是总回报动量。`);
  paragraph(box,'相对动量的价格列与含息列采用不同信号定义，差额同时包含交易时点变化，不能全部当作分红增益。','footnote');
  if(study.symbol===policy.benchmark)paragraph(box,'基准与自身比较不构成相对动量研究，已排除该项。','footnote');
  paragraph(box,policy.totalAllowed?'两侧企业行为和相关原件哈希通过：含息相对动量使用同一税前因果权益指数；账户税负另列。':'含息相对动量已阻断：标的或基准的完整覆盖、原件哈希、事前公告未通过；其他四种策略单独保留。','warning');
  for(const [k,title] of [['subjectCoverage','标的'],['benchmarkCoverage','基准']]){
   const s=policy[k];if(s?.missingIntervals?.length)paragraph(box,title+'缺失覆盖：'+s.missingIntervals.map(v=>v.from+'—'+v.to).join('；'),'footnote');
   if(s?.missingSourceIds?.length)paragraph(box,title+'原件缺失或哈希不符：'+s.missingSourceIds.join('、'),'warning');
  }
 }
 function resultText(name,mode,s){
  if(s?.status==='blocked'||s?.applicable===false)return '阻断：'+(s.notApplicableReason||'证据不足');
  if(name==='relative_momentum_60'){
   if(!policy)return '旧口径，待核验';
   if(s?.applicable===false||s?.status==='blocked'||mode!=='price'&&!policy.totalAllowed)return '阻断：两侧覆盖/哈希未通过';
   if(s?.signalBasis!==(mode==='price'?'unadjusted-price-both-sides':'gross-causal-total-return-both-sides'))return '阻断：口径未确认';
  }
  return s?.metrics?bpsDisplay(s.metrics.totalReturnBps):'证据不足不计算';
 }
 const modes=study.modes||{},price=modes.price||{},cash=modes.cash||{},reinvest=modes.reinvest||{};
 if(Object.values(cash).some(s=>s.cashSettledAtEnd===false))paragraph(box,'期末仍有按公告确认的未到账应收分红，计入研究净值但不可用于下单，不等于券商现金到账。','warning');
 box.append(table(['规则','含费用价格收益','含息留存收益','含息再投收益','留存回撤','留存夏普(Rf=0)','留存费用+滑点'],Object.entries(price).filter(([,s])=>s.applicable!==false).map(([key,p])=>{
  const suppressed=key==='relative_momentum_60'&&(!policy||!policy.totalAllowed),c=suppressed?null:cash[key]?.metrics;return[names[key],resultText(key,'price',p),resultText(key,'cash',cash[key]),resultText(key,'reinvest',reinvest[key]),c?bpsDisplay(c.maxDrawdownBps):'—',c?.sharpeRfZero??'—',c?moneyDisplay(String(Number(c.cost)/100)):'—'];
 })));
 if(modes['cash-before-dividend-tax'])box.append(table(['规则','分红税前留存收益(已计交易费)','统一税负假设后留存收益'],Object.entries(modes['cash-before-dividend-tax']).filter(([name,s])=>s.applicable!==false&&(name!=='relative_momentum_60'||policy?.totalAllowed)).map(([name,s])=>[names[name],bpsDisplay(s.metrics.totalReturnBps),bpsDisplay(cash[name].metrics.totalReturnBps)])));
 for(const [fold,label] of [['train','训练'],['validation','验证'],['test','历史留出']]){
  const detail=node('details');detail.append(node('summary',label+'：同区间价格与现金流比较'));
  detail.append(table(['规则','日期','价格收益','含息留存','含息再投','留存波动','留存换手','年化收益'],Object.entries(price).filter(([,s])=>s.applicable!==false).map(([name,s])=>{
   const p=s.partitions[fold],blocked=name==='relative_momentum_60'&&(!policy||!policy.totalAllowed),c=blocked?null:cash[name]?.partitions[fold],r=blocked?null:reinvest[name]?.partitions[fold];return[names[name],p?p.from+'—'+p.to:'无覆盖区间',p?resultText(name,'price',{...s,metrics:p.metrics}):'—',c?bpsDisplay(c.metrics.totalReturnBps):'未核验/阻断',r?bpsDisplay(r.metrics.totalReturnBps):'未核验/阻断',c?bpsDisplay(c.metrics.volatilityBps):'—',c?percentDisplay(c.metrics.oneWayTurnoverPct):'—',c?.metrics.annualizedReturnBps==null?'期限不足或未核验':bpsDisplay(c.metrics.annualizedReturnBps)];
  })));box.append(detail);
 }
 for(const note of study.limitations||[])paragraph(box,note,'footnote');downloadStudy(box,study,request);
}
export function renderTrust(report,request){
 const target=document.getElementById('research-report'),radar=report?.opportunityRadar;if(!target||!radar?.items?.some(i=>i.trustResearch))return;
 const panel=node('article',null,'card');panel.append(node('h3','可信回测 · 企业行为与现金分红'));
 paragraph(panel,'原价格回测保留在下方。新版价格相对动量两侧均用未复权价格；含息相对动量必须核验两侧完整预热及研究区间，使用一致税前因果权益指数；其他规则保持标的指标，实际成交用原始价，分红独立记账。');
 paragraph(panel,'尚未证明优于买入持有。价格历史版本、观察池点时成员、特殊成交规则与完整企业行为仍有限制；不显示高胜率标签。','warning');
 for(const item of radar.items){const trust=item.trustResearch;if(!trust)continue;const d=node('details');d.append(node('summary',item.name+' '+item.symbol+' · 可信等级'+(trust.dataTrustGrade||'待评定')));
  paragraph(d,trust.dataTrustLabel,'warning');const evidence=trust.sourceEvidence;
  if(evidence){paragraph(d,`核验范围 ${evidence.from}—${evidence.to}；已核验事件 ${evidence.verifiedEvents}；当前区间完整性 ${evidence.complete?'通过现金流覆盖检查':'未通过'}。`);
   if(evidence.missingIntervals?.length)paragraph(d,'缺失覆盖：'+evidence.missingIntervals.map(g=>g.from+'—'+g.to).join('；'),'footnote');
   if(evidence.missingSourceIds?.length)paragraph(d,'本机原始证据缺失或哈希不符，不能凭缓存宣称完整。','warning');
   const events=evidence.events||[];
   if(events.length)d.append(table(['事件','事前公告/可用日期','登记/除权日','发放/新增份额可用日','金额/比例'],events.map(e=>[e.type,e.announcementDate+' / '+e.availableFrom,e.recordDate?e.recordDate+' / '+e.exDate:(e.startDate+'—'+e.endDate),e.payDate||e.shareAvailableDate||e.resumeDate||'未知',e.cashPerShare?e.cashPerShare+'元/股(份)':e.shareMultiplier?'份额乘'+e.shareMultiplier:'不可交易区间'])));
   const sources=node('details');sources.append(node('summary','官方来源、定位与完整性依据'));
   for(const s of evidence.sources||[]){const p=node('p',null,'footnote'),a=node('a',s.publicationDate+' · '+s.locator);try{const u=new URL(s.url);if(u.protocol==='https:'&&!u.username&&!u.password){a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';}}catch{}p.append(a,node('span',' · SHA256 '+s.sha256.slice(0,12)+'…'));sources.append(p);}d.append(sources);
  }
  if(trust.current){const current=node('details');current.append(node('summary','当前期限：因果信号与价格回测'));metricsByMode(current,trust.current,request);d.append(current);}
  if(trust.verifiedWindow){const verified=node('details');verified.append(node('summary','已核验日期区间：现金分红留存／再投资'));metricsByMode(verified,trust.verifiedWindow,request);d.append(verified);}
  if(!trust.verifiedWindow)paragraph(d,'未取得完整企业行为区间，未输出该标的完整含息收益。','footnote');
  const quality=item.strategyQuality||[];
  if(quality.length){const q=node('details');q.append(node('summary',radar.strategyQuality?.version>=3?'历史留出不确定性：成对分块与多重比较':'历史旧版统计（基准口径未修正，仅供对照）'));
   q.append(table(['规则','留出价格超额收益','10日分块日超额均值95%区间(bps)','Holm校正p','20日分块敏感性','结论'],quality.map(s=>{const t=s.folds.test,a=t?.blocks?.[0],b=t?.blocks?.[1];return[names[s.strategy],t?bpsDisplay(t.excessReturnBps):'无完整留出',a?.meanDailyExcess95Bps?.join(' 至 ')??'样本不足',a?.holmAdjustedP??'未计算',b?.meanDailyExcess95Bps?.join(' 至 ')??'样本不足',s.conclusion];})));paragraph(q,'这是因果信号价格账户的回顾性比较，未冒充当前区间完整含息优势。证券收益并非独立；分块近似仍依赖平稳性。重复比较已作Holm校正，不能把简单胜出次数当作证明。','footnote');d.append(q);}
  if(item.quarantineReview){paragraph(d,'隔离复核：'+item.quarantineReview.cause);paragraph(d,'仍需：'+item.quarantineReview.remaining,'footnote');}
  for(const code of trust.codes||[])paragraph(d,'本次研究未就绪：'+code,'footnote');panel.append(d);
 }
 target.prepend(panel);
}
