"""Pluggable descriptive research. No optimizer, order sizing or execution calls."""
from datetime import datetime,time,timezone,timedelta
from decimal import Decimal as D,localcontext
BJ=timezone(timedelta(hours=8))

def fmt(v):return format(v.quantize(D('.01')),'f') if v is not None else None
def complete_rows(dataset,clock):
 from goose_research.market import xshg_sessions
 if not dataset or dataset.get('adjustment')!='none' or dataset.get('quality',{}).get('status')!='validated':return []
 local=clock.astimezone(BJ);today=local.date().isoformat()
 rows=[r for r in dataset['bars'] if r['date']<today or r['date']==today and local.time().replace(tzinfo=None)>=time(15,30)]
 if not rows:return []
 recent=rows[-61:]
 if [r['date'] for r in recent]!=xshg_sessions(recent[0]['date'],recent[-1]['date']):return []
 return rows

def trend(context):
 rows=context['rows'];qrows=context['qrows'];n=len(rows)
 if n<60:return {'id':'trend-20-60','state':'data-insufficient','summary':'不足60个完整交易日，暂停MA60判断。','evidence':[],'backtestVerified':False}
 close=D(rows[-1]['close']);ma20=sum(D(r['close']) for r in rows[-20:])/20;ma60=sum(D(r['close']) for r in rows[-60:])/60
 prior_ma=sum(D(r['close']) for r in rows[-25:-5])/20
 high20=max(D(r['high']) for r in rows[-21:-1]) if all('high'in r for r in rows[-21:-1]) else None
 verified_qfq=len(qrows)>=60 and [r['date'] for r in qrows[-60:]]==[r['date'] for r in rows[-60:]]
 qtrend=None
 if verified_qfq:
  q20=sum(D(r['close']) for r in qrows[-20:])/20;q60=sum(D(r['close']) for r in qrows[-60:])/60;qclose=D(qrows[-1]['close'])
  qtrend='above' if qclose>q20>q60 else 'below' if qclose<q20<q60 else 'mixed'
 direction='above' if close>ma20>ma60 else 'below' if close<ma20<ma60 else 'mixed'
 conflict=verified_qfq and qtrend!=direction
 state='data-insufficient' if not verified_qfq or conflict else 'hold-research' if direction=='above' else 'reduce-research' if direction=='below' else 'watch'
 return {'id':'trend-20-60','state':state,'through':rows[-1]['date'],'close':str(close),'ma20':format(ma20,'.6f'),'ma60':format(ma60,'.6f'),
  'closeVsMa60Pct':fmt((close/ma60-1)*100),'ma20FiveSessionSlopePct':fmt((ma20/prior_ma-1)*100),
  'breakoutPrior20High':close>high20 if high20 else None,'prior20High':str(high20) if high20 else None,
  'adjustedDirection':qtrend,'adjustmentConflict':conflict,'backtestVerified':False,
  'summary':'复权证据缺失或方向冲突，暂停方向判断。' if state=='data-insufficient' else '完整日收盘、MA20、MA60同向向上；研究持有，集中度和资金条件另行核对。' if direction=='above' else '完整日收盘与双均线同向走弱；研究风险收缩条件，不是卖出指令。' if direction=='below' else 'MA20与MA60方向尚未一致，观察等待。',
  'evidence':[f"完整交易日{rows[-1]['date']}；MA20={ma20:.6f}，MA60={ma60:.6f}。",f"MA20相对5个交易日前变化{fmt((ma20/prior_ma-1)*100)}%。"],
  'trigger':'后续完整日继续维持同口径双均线趋势，并核对成交量、集中度与个人预算后才研究调整。',
  'invalidation':'跌回相应均线、复权口径冲突、行情失效或账户数据不完整时暂停原方向研究。',
  'risks':['MA20/60规则尚未完成独立样本外回测，不以旧MA20回测冒充本规则的历史验证。','原始价格突破可能受除权影响；前复权只是单独趋势对照，不是含息总回报。']}

def allocation(context):
 pf=context['portfolio'];s=context['symbol'];weight=next((w['weightPct'] for w in pf.get('symbolWeights',[]) if w['symbol']==s),None)
 return {'id':'low-frequency-allocation','state':'watch','summary':'先保留现金与目标仓位约束，再研究低频再平衡；不按每日涨跌机械调仓。',
  'importedSecuritiesWeightPct':weight,'availableCash':pf.get('availableCash') if pf.get('status')=='current-user-confirmed' else None,'cashSnapshotAsOf':pf.get('asOf'),'targetWeightPct':None,'singleTradeBudget':None,'maximumLossBudget':None,
  'trigger':'补充目标仓位、近期现金需求、应急现金及单笔可承受损失，再比较偏离幅度与交易成本。',
  'invalidation':'资金或目标未知、账户未对账、费用不清楚时不计算可下单数量。','backtestVerified':False,
  'risks':['证券内部占比不是家庭总资产仓位。','股票和ETF可能存在底层暴露重叠，未取得成分与权重前不量化行业占比。']}

def income(context):
 return {'id':'dividend-low-volatility','state':'data-insufficient','summary':'可比较价格波动与回撤；股息率、估值和完整含息收益仍缺已核验现金分红/税费证据。',
  'dividendYield':None,'valuation':None,'totalReturnVerified':False,'backtestVerified':False,
  'trigger':'按除息日、每股分配、持有税制与现金流对齐后，才计算真实分红收益和再投资情景。',
  'invalidation':'分红事件、复权系数或税款无法对齐时不输出总回报排名。',
  'risks':['低波或红利名称不保证未来低风险。','不把长江电力未计分红的价格收益称为完整投资收益。']}

STRATEGIES={'trend-20-60':trend,'low-frequency-allocation':allocation,'dividend-low-volatility':income}
def register_strategy(identifier,function):
 if identifier in STRATEGIES or not callable(function):raise ValueError('STRATEGY_REGISTRATION_INVALID')
 STRATEGIES[identifier]=function

def build_research(symbol,dataset,adjusted,portfolio,clock):
 rows=complete_rows(dataset,clock)
 qrows=[]
 if adjusted and adjusted.get('adjustment')=='qfq' and adjusted.get('quality',{}).get('status')=='validated-basis-only' and adjusted.get('quality',{}).get('ohlcAndCalendarValidated') is True and adjusted.get('symbol')==symbol:
  qrows=[r for r in adjusted['bars'] if rows and r['date']<=rows[-1]['date']]
 context={'symbol':symbol,'rows':rows,'qrows':qrows,'portfolio':portfolio}
 plugins=[fn(context) for fn in STRATEGIES.values()]
 stats=None
 if len(rows)>=60:
  from .indicators import statistics
  stats=statistics(rows[-60:]);returns=[D(rows[i]['close'])/D(rows[i-1]['close'])-1 for i in range(len(rows)-59,len(rows))]
  avg=sum(returns)/len(returns);variance=sum((r-avg)**2 for r in returns)/(len(returns)-1)
  stats['priceSharpeRfZero']=fmt(avg/variance.sqrt()*D(252).sqrt()) if variance>0 else None
  stats['sharpeBasis']='60-session-unadjusted-price-rf0-not-account-or-strategy-return'
 return {'version':1,'generatedAt':clock.isoformat(),'through':rows[-1]['date'] if rows else None,'plugins':plugins,'risk60':stats,'priceSeries':[{'date':r['date'],'close':r['close']} for r in rows[-60:]],
  'portfolioSuitability':{'quantitySource':'最新用户确认快照或明确标注的成交推演；不是券商自动同步','cashBudgetKnown':portfolio.get('status')=='current-user-confirmed' and portfolio.get('availableCash') is not None,'orderSizingEnabled':False},
  'limitations':['以上新增插件是可复核条件研究，未进行参数择优，不称已通过样本外验证。','已有四策略价格回测单独列出；不自动挑选收益最高者实盘应用。']}

def portfolio_risk(histories,clock):
 keys=sorted(histories);result=[]
 for n,a in enumerate(keys):
  for b in keys[n+1:]:
   ra=complete_rows(histories[a],clock);rb=complete_rows(histories[b],clock)
   def returns(rows):return {rows[i]['date']:D(rows[i]['close'])/D(rows[i-1]['close'])-1 for i in range(1,len(rows))}
   x,y=returns(ra),returns(rb);dates=sorted(set(x)&set(y))[-60:]
   if len(dates)<30:continue
   u=[x[d] for d in dates];v=[y[d] for d in dates];mu=sum(u)/len(u);mv=sum(v)/len(v)
   den=(sum((z-mu)**2 for z in u)*sum((z-mv)**2 for z in v)).sqrt()
   correlation=sum((z-mu)*(t-mv) for z,t in zip(u,v))/den if den else None
   result.append({'symbols':[a,b],'through':dates[-1],'observations':len(dates),'correlation':fmt(correlation),'basis':'same-date-unadjusted-price-returns-not-holdings-performance'})
 return {'pairs':result,'industryOverlap':None,'warnings':['相关性来自历史价格，不保证未来分散效果；成分权重未核验，行业重叠保持未知。']}
