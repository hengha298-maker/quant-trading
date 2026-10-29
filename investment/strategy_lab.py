"""Fixed-rule price research using the existing cents/lot/T+1 portfolio model.

No parameter search, broker account, network or simulator database. Signals use
only complete prior sessions; raw fills and provider-qfq indicators stay separate.
Historical held-out dates are not prospective or survivorship-free validation.
"""
from datetime import date,datetime,timezone
from decimal import Decimal as D,ROUND_CEILING,ROUND_FLOOR,localcontext
from pathlib import Path
import hashlib,json
from .holdings import _canonical,_safe_path
from .market import xshg_sessions
from goose_research.backtest import _prepare,_Portfolio,_metrics,_cents,_available

VERSION=1
MODEL_REVISION=3
STRATEGIES=('buy_hold','trend_20_60','breakout_20_10','relative_momentum_60','low_vol_drawdown')
CONFIG={'initialCash':100000000,'commissionBps':3,'slippageBps':5,'stampDutyBps':5,'minCommission':500,'maxPositionBps':10000,'buyBudgetBps':10000}
PARTITIONS={'train':{'from':'2024-09-02','to':'2025-06-30'},'validation':{'from':'2025-07-01','to':'2025-12-31'},'test':{'from':'2026-01-01','to':'2026-12-31'}}

def pct(x):return format((x*100).quantize(D('.01')),'f') if x is not None else None
def stats(values):
    if len(values)<2:return None
    mean=sum(values)/len(values);variance=sum((v-mean)**2 for v in values)/(len(values)-1)
    return variance.sqrt()*D(252).sqrt()

def indicators(rows,bench,index):
    # index is execution day; no access to that row's price/volume.
    if index<61:return None
    window=rows[index-61:index];close=[D(r['close']) for r in window];prices=close[-60:]
    returns=[close[i]/close[i-1]-1 for i in range(1,len(close))]
    peak=prices[0];draw=D(0)
    for p in prices:peak=max(peak,p);draw=max(draw,(peak-p)/peak)
    bm=bench[index-61:index];relative=close[-1]/close[0]/(D(bm[-1]['close'])/D(bm[0]['close']))-1
    return {'signalAsOf':window[-1]['date'],'close':close[-1],'ma20':sum(close[-20:])/20,'ma60':sum(prices)/60,
            'momentum20':close[-1]/close[-21]-1,'relative60':relative,
            'prior20High':max(D(r['high']) for r in window[-21:-1]),'prior10Low':min(D(r['low']) for r in window[-11:-1]),
            'vol60':stats(returns),'drawdown60':draw}

def decision(strategy,signal,kind):
    if signal is None:return None
    p=signal
    if strategy=='buy_hold':return 'BUY'
    if strategy=='trend_20_60':return 'BUY' if p['close']>p['ma20']>p['ma60'] else 'SELL'
    if strategy=='breakout_20_10':return 'BUY' if p['close']>p['prior20High'] else 'SELL' if p['close']<p['prior10Low'] else 'HOLD'
    if strategy=='relative_momentum_60':return 'BUY' if p['relative60']>0 and p['momentum20']>0 else 'SELL'
    if strategy=='low_vol_drawdown':return 'BUY' if p['vol60'] is not None and p['vol60']<=(D('.25') if kind=='etf' else D('.35')) and p['drawdown60']<=D('.10') and p['close']>p['ma60'] else 'SELL'
    raise ValueError('LAB_STRATEGY_UNKNOWN')

class TickPortfolio(_Portfolio):
    def __init__(self,cfg,kind,symbol,tick):super().__init__(cfg,kind,symbol);self.tick=D(tick)
    def _quote(self,side,quantity,opening):
        direction=1 if side=='BUY' else -1
        proposed=opening*(1+direction*D(self.cfg['slippageBps'])/10000)
        fill=(proposed/self.tick).to_integral_value(rounding=ROUND_CEILING if side=='BUY' else ROUND_FLOOR)*self.tick
        gross=_cents(fill*quantity*100);commission=max(self.cfg['minCommission'],_cents(D(gross)*self.cfg['commissionBps']/10000))
        tax=_cents(D(gross)*self.cfg['stampDutyBps']/10000) if side=='SELL' and self.kind=='stock' else 0
        return fill,gross,commission,tax,_cents(abs(fill-opening)*quantity*100)

def _measure(curve,trades):
    m=_metrics(curve,trades,CONFIG['initialCash'])
    if not m:return None
    days=(date.fromisoformat(curve[-1]['date'])-date.fromisoformat(curve[0]['date'])).days
    if len(curve)<252 or days<365:m['annualizedReturnBps']=None
    returns=[];previous=D(CONFIG['initialCash'])
    for r in curve:current=D(r['equity']);returns.append(current/previous-1);previous=current
    vol=stats(returns);mean=sum(returns)/len(returns)
    m['sharpeRfZero']=format((mean/vol*252).quantize(D('.01')),'f') if vol and len(curve)>=60 else None
    average=sum(D(r['equity']) for r in curve)/len(curve)
    m['oneWayTurnoverPct']=pct(sum((D(t['gross']) for t in trades),D(0))/average) if average else None
    m['turnoverBasis']='sum(buy+sell notional)/mean closing NAV; not halved'
    m['riskFreeRateAssumption']='0% annual; 252-session risk scaling'
    m['annualizationSuppressed']=m['annualizedReturnBps'] is None
    return m

def _run(strategy,bars,raw,signals,spec,indexes,actions):
    p=TickPortfolio(dict(CONFIG),spec['kind'],spec['symbol'],spec['tick']);curve=[];orders=[]
    for i in indexes:
        bar=bars[i];opening=p.cash+_cents(bar.open*p.quantity*100);signal=signals[i];side=decision(strategy,signal,spec['kind'])
        eligible=all(_available(bars[j],bar.date) for j in range(i-61,i))
        if not eligible:side=None
        if side=='BUY' and p.quantity or side=='SELL' and not p.quantity:side='HOLD'
        if side in {'BUY','SELL'}:
            old_close=bars[i-1].close;tick=D(spec['tick']);limit=D(spec['limitBps'])/10000
            upper=(old_close*(1+limit)/tick).to_integral_value(rounding=__import__('decimal').ROUND_HALF_UP)*tick
            lower=(old_close*(1-limit)/tick).to_integral_value(rounding=__import__('decimal').ROUND_HALF_UP)*tick
            blocked='unverified-corporate-action-limit-reference' if bar.date in actions else 'zero-volume-ex-post-no-fill' if bar.volume==0 else 'opening-price-limit-queue-unmodelled' if (side=='BUY' and bar.open>=upper or side=='SELL' and bar.open<=lower) else None
            fill=p._quote(side,100,bar.open)[0]
            if blocked is None and not lower<=fill<=upper:blocked='slippage-fill-outside-price-limits'
            order={'date':bar.date,'signalAsOf':signal['signalAsOf'],'side':side,'status':'not-filled','reason':blocked}
            if blocked is None:
                before=len(p.trades)
                if side=='BUY':p.buy(bar.date,bar.open,p.cash)
                else:p.sell(bar.date,bar.open)
                order.update(status='filled' if len(p.trades)>before else 'not-filled',reason='assumed-next-open-tick-rounded' if len(p.trades)>before else 'cash-lot-t1-fee-or-cap')
            orders.append(order)
        equity=p.cash+_cents(bar.close*p.quantity*100)
        curve.append({'date':bar.date,'equity':str(equity),'cash':str(p.cash),'quantity':p.quantity,'externalFlow':'0','openingEquityBeforeFlow':str(opening)})
    return {'metrics':_measure(curve,p.trades),'equityCurve':curve,'trades':p.trades,'orders':orders}

def run_suite(history,benchmark,spec,*,allow_synthetic=False):
    raw,adjusted=history['raw'],history['adjusted'];bm=benchmark['adjusted']
    synthetic=raw.get('quality',{}).get('status')=='synthetic' or benchmark['raw'].get('quality',{}).get('status')=='synthetic'
    if synthetic and not allow_synthetic:raise ValueError('LAB_SYNTHETIC_NOT_PRODUCTION')
    bars,quality=_prepare(raw);rows=adjusted['bars'];bench=bm['bars'];days=[b.date for b in bars]
    if raw['symbol']!=spec['symbol'] or raw['kind']!=spec['kind'] or adjusted.get('symbol')!=raw['symbol'] or bm.get('symbol')!=benchmark['raw']['symbol']:raise ValueError('LAB_INSTRUMENT_IDENTITY_MISMATCH')
    if adjusted.get('adjustment')!='qfq' or bm.get('adjustment')!='qfq' or [r['date'] for r in rows]!=days or [r['date'] for r in bench]!=days:raise ValueError('LAB_PRICE_BASIS_OR_DATES_MISMATCH')
    if any(d.get('quality',{}).get('status')!='validated-basis-only' or d.get('quality',{}).get('ohlcAndCalendarValidated') is not True for d in (adjusted,bm)):raise ValueError('LAB_ADJUSTED_BASIS_UNVALIDATED')
    _prepare({**raw,'bars':rows});_prepare({**benchmark['raw'],'bars':bench})
    if len(bars)<400:raise ValueError('LAB_HISTORY_TOO_SHORT_FOR_FIXED_HOLDOUT')
    signals={i:indicators(rows,bench,i) for i in range(61,len(rows))}
    actions=[]
    for i in range(1,len(rows)):
        a=D(raw['bars'][i]['close'])/D(raw['bars'][i-1]['close']);b=D(rows[i]['close'])/D(rows[i-1]['close'])
        rounding=2*D(spec['tick'])/min(D(rows[i]['close']),D(rows[i-1]['close']))+2*D(spec['tick'])/min(D(raw['bars'][i]['close']),D(raw['bars'][i-1]['close']))
        if abs(a/b-1)>max(D('.003'),rounding):actions.append(days[i])
    partitions={k:[i for i in range(61,len(bars)) if v['from']<=days[i]<=v['to']] for k,v in PARTITIONS.items()}
    if any(len(partitions[k])<60 for k in partitions) or len(partitions['test'])<126:raise ValueError('LAB_HOLDOUT_TOO_SHORT')
    full=[i for i in range(61,len(bars)) if days[i]>=PARTITIONS['train']['from']]
    strategies={}
    for strategy in STRATEGIES:
        result=_run(strategy,bars,raw,signals,spec,full,set(actions))
        result['applicable']=not(strategy=='relative_momentum_60' and spec['symbol']==benchmark['raw']['symbol'])
        if not result['applicable']:result['notApplicableReason']='基准不能与自身比较相对强弱；全现金0%不作为策略效果证据。'
        result['partitions']={k:{'from':days[ix[0]],'to':days[ix[-1]],**_run(strategy,bars,raw,signals,spec,ix,set(actions))} for k,ix in partitions.items()}
        strategies[strategy]=result
    return {'format':'goose-radar-strategy-lab','version':VERSION,'symbol':spec['symbol'],'kind':spec['kind'],'from':days[full[0]],'to':days[-1],
      'inputHash':input_hash(history,benchmark,spec),'modelRevision':MODEL_REVISION,'synthetic':synthetic,'config':CONFIG,'strategies':strategies,'corporateActionSuspectedDates':actions,'tradePriceUnit':'CNY-cents-per-share','cashAndCostUnit':'CNY-cents',
      'signalBasis':'provider-qfq complete prior sessions, ex-post revised series; not point-in-time certified',
      'valuationBasis':'unadjusted CNY prices and cents; excludes unpaid dividends and enterprise actions',
      'partitions':PARTITIONS,'parametersOptimized':False,'prospectiveOutOfSample':False,'universeSurvivorshipFree':False,'completeTotalReturn':False,
      'limitations':['固定参数；历史训练/验证/留出按时间分开，各段独立同额资金开局，禁止按留出收益选参数。','当前人工存续观察池有选择及幸存者偏差，不是历史全市场选股验证。','供应商前复权用于信号，原始价用于成交/估值，二者不混算；完整分红税和再投资现金流未核验。','假设普通沪市证券10%限价；历史特殊限价/排队/盘口成交不可还原，疑似除权日不建新单。','100份/股、T+1、费用与保守滑点按显式研究假设；不是用户实盘资金或实际费率。','不足252交易日或365自然日不输出年化收益；短段波动/夏普仍有估计误差。']}

def input_hash(history,benchmark,spec):
    return hashlib.sha256(_canonical({'version':VERSION,'modelRevision':MODEL_REVISION,'raw':history['raw']['bars'],'adjusted':history['adjusted']['bars'],
     'benchmark':benchmark['adjusted']['bars'],'spec':spec,'config':CONFIG,'partitions':PARTITIONS})).hexdigest()

def summarized(result,key):
    strategies={}
    for name,r in result['strategies'].items():
        curve=r['equityCurve'];step=max(1,len(curve)//30);selected=curve[::step]
        if curve and selected[-1]!=curve[-1]:selected.append(curve[-1])
        strategies[name]={'applicable':r['applicable'],'notApplicableReason':r.get('notApplicableReason'),'metrics':r['metrics'],'partitions':{k:{'from':p['from'],'to':p['to'],'metrics':p['metrics']} for k,p in r['partitions'].items()},
         'curvePreview':[{'date':p['date'],'equity':p['equity']} for p in selected],'unfilledOrders':sum(o['status']!='filled' for o in r['orders'])}
    return {k:v for k,v in result.items() if k!='strategies'}|{'strategies':strategies,'artifactKey':key}

def cached_suite(root,history,benchmark,spec,*,deadline=None):
    key=input_hash(history,benchmark,spec);folder=_safe_path(Path(root)/'investment/work/radar/labs',missing=True);folder.mkdir(parents=True,exist_ok=True);path=folder/(key+'.json')
    if path.exists():
        _safe_path(path,file=True);wrapper=json.loads(path.read_text(encoding='utf8'))
        if wrapper['sha256']!=hashlib.sha256(_canonical(wrapper['result'])).hexdigest() or wrapper['result'].get('inputHash')!=key or wrapper['result'].get('synthetic'):raise ValueError('LAB_CACHE_INVALID')
        result=wrapper['result']
    else:
        if deadline and datetime.now(timezone.utc)>=deadline:raise ValueError('LAB_TIME_BUDGET_EXHAUSTED')
        with localcontext() as ctx:ctx.prec=45;result=run_suite(history,benchmark,spec)
        wrapper={'sha256':hashlib.sha256(_canonical(result)).hexdigest(),'result':result}
        with path.open('xb') as f:f.write(_canonical(wrapper))
    return summarized(result,key)
