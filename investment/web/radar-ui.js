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
 if(r.synthetic){paragraph(card,'虚构界面测试，不能用于投资。','warning');target.append(card);return;}
 if(report.testData===true)paragraph(card,'完全虚构的隔离界面测试，不是真实行情或推荐。','warning');
 if(r.accountConstraints){paragraph(card,`持仓依据 ${r.accountConstraints.snapshotAsOf||'未知'}；证券内部最大集中度 ${percentDisplay(r.accountConstraints.securitiesConcentrationPct)}。`);paragraph(card,r.accountConstraints.message,'footnote');}
 for(const [kind,title] of [['etf','ETF优先观察'],['stock','A股优先观察']]){
  const group=r.groups?.[kind],coverage=r.coverage?.[kind];card.append(node('h3',title));
  if(coverage)paragraph(card,`维护${coverage.configured}只 · 历史通过${coverage.historyValidated}只 · 可比较${coverage.rankable}只`,'meta');
  const candidates=group?.candidates||[];
  if(!candidates.length)paragraph(card,group?.message||'暂无符合条件的机会','notice');
  for(const item of candidates){const d=node('details',null,'scenario');d.append(node('summary',`${item.name} ${item.symbol} · 研究分 ${item.score.value}/100`));
   paragraph(d,`历史抓取 ${item.quality?.historyFetchedAt||'见研究文件'}；公开报价源时刻 ${item.quality?.quote?.sourceTimestamp||'未取得'}。`,'meta');
   for(const reason of item.reasons||[])paragraph(d,reason);paragraph(d,'触发关注：'+item.trigger);paragraph(d,'失效条件：'+item.invalidation);
   for(const risk of item.risks||[])paragraph(d,risk,'footnote');d.append(table(['评分项','得分','依据'],item.score.parts.map(p=>[p.factor,`${p.points}/${p.maxPoints}`,p.basis])));card.append(d);
  }
 }
 const all=node('details');all.append(node('summary','查看全部观察池与未入选原因'));
 all.append(table(['标的','类型/分数','数据日期/状态','未入选原因'],(r.items||[]).map(i=>[i.name+' '+i.symbol,i.kind+' / '+(i.score?.value??'未评分'),(i.through||'未知')+' / '+(i.quality?.status||'未知'),[...(i.quality?.codes||[]),...(i.rejections||[])].join('；')||'通过研究门槛'])));card.append(all);
 for(const note of r.limitations||[])paragraph(card,note,'footnote');target.append(card);
}
export function renderLabs(report,request){
 const target=document.getElementById('research-report');if(!target)return;const r=report?.opportunityRadar;if(!r?.items?.length)return;
 const panel=node('article',null,'card');panel.append(node('h3','固定规则策略实验室 · 历史留出比较'));
 paragraph(panel,'各策略、各分区均独立以100万元研究本金起步；不是你的实际账户资金。买入/卖出用下一交易日原始开盘价，前复权只用于信号；不是完整含息回报。');
 paragraph(panel,'训练：2024-09起至2025-06；验证：2025下半年；留出：2026年至数据截止。固定参数，无测试期择优，仍有存续池选择偏差。','footnote');
 for(const item of r.items){if(!item.lab)continue;const lab=item.lab,detail=node('details');detail.append(node('summary',item.name+' '+item.symbol+' · 截至'+lab.to));
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
}
