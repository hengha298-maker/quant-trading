"""Event-aware research accounts. Never reads or writes a brokerage ledger.

Raw executions, causal indicators and actual-dated dividend entitlements are
separate. Cash is credited conservatively at payment-day close, available next
session. Tax and fee assumptions are explicit rather than broker facts.
"""
from datetime import date,datetime,timedelta,time,timezone
from decimal import Decimal as D,ROUND_HALF_UP,localcontext
from .corporate_actions import causal_series,valuation_rows,events_for,coverage,point_time,evidence_status
from .strategy_lab import TickPortfolio,CONFIG,PARTITIONS,STRATEGIES,indicators,decision,_measure
from goose_research.backtest import _prepare,_cents,_available
from .market import BJ

class EventPortfolio(TickPortfolio):
    def __init__(self,cfg,spec):
        super().__init__(cfg,spec['kind'],spec['symbol'],spec['tick']);self.bookCost=0;self.realizedPnl=0
    def _execute(self,side,day,quantity,opening):
        old=self.quantity;super()._execute(side,day,quantity,opening);t=self.trades[-1]
        if side=='BUY':self.bookCost+=int(t['gross'])+int(t['commission'])
        else:
            allocated=_cents(D(self.bookCost)*quantity/old);self.bookCost-=allocated
            self.realizedPnl+=int(t['gross'])-int(t['commission'])-int(t['stampDuty'])-allocated
    def sell(self,day,opening):
        available=sum(q for bought,q in self.lots if bought<day)
        quantity=available if available==self.quantity else available//100*100
        if quantity:
            _,gross,fee,tax,_=self._quote('SELL',quantity,opening)
            if self.cash+gross-fee-tax>=0:self._execute('SELL',day,quantity,opening)
    def shares(self,event,entitled):
        multiplier=D(event['shareMultiplier']);extra=D(entitled)*(multiplier-1)
        if extra!=extra.to_integral_value():raise ValueError('TOTAL_FRACTIONAL_SHARES_CASH_IN_LIEU_UNSUPPORTED')
        extra=int(extra)
        if extra<0:
            if entitled!=self.quantity:raise ValueError('TOTAL_REVERSE_SPLIT_RIGHTS_CONTINUITY_UNSUPPORTED')
            changed=[]
            for bought,qty in self.lots:
                q=D(qty)*multiplier
                if q!=q.to_integral_value():raise ValueError('TOTAL_FRACTIONAL_LOT_REVERSE_SPLIT_UNSUPPORTED')
                changed.append((bought,int(q)))
            self.lots=changed
        elif extra:
            available=event.get('shareAvailableDate')
            if available is None:raise ValueError('TOTAL_SHARE_LISTING_DATE_UNKNOWN')
            self.lots.append(((date.fromisoformat(available)-timedelta(days=1)).isoformat(),extra))
        self.quantity+=extra
        if self.quantity<0:raise ValueError('TOTAL_SHARE_CONTINUITY_FAILED')

def _limit_reference(previous,day,events):
    cash=D(0);ratio=D(1)
    for e in events:
        if e['type']=='suspension' or e['exDate']!=day:continue
        if not point_time(e,day):raise ValueError('TOTAL_EVENT_NOT_AVAILABLE_AT_OPEN')
        if e['type']=='cash-dividend':cash+=D(e['cashPerShare'])
        else:ratio*=D(e['shareMultiplier'])
    ref=(previous-cash)/ratio
    if ref<=0:raise ValueError('TOTAL_EX_REFERENCE_INVALID')
    return ref

def simulate(rows,bench,spec,events,strategy,indexes,*,mode='price',tax_bps=0,config=None,deadline=None):
    if mode not in {'price','cash','reinvest'} or type(tax_bps) is not int or not 0<=tax_bps<=10000:raise ValueError('TOTAL_POLICY_INVALID')
    cfg={**CONFIG,**(config or {})};p=EventPortfolio(cfg,spec);curve=[];orders=[];rights={};receivables={};flows=[];dividend_budget=0;gross_paid=tax_paid=0
    causal=causal_series(rows,events);bm=causal_series(bench['rows'],bench['events'])
    if causal['lateAnnouncementIds'] or bm['lateAnnouncementIds']:raise ValueError('TOTAL_POINT_TIME_EVENT_LATE')
    bars,_=_prepare({**bench.get('subjectDataset',bench['dataset']),'symbol':spec['symbol'],'kind':spec['kind'],'bars':rows,'calendar':{'name':'XSHG','sessions':[r['date'] for r in rows]}})
    benchmark_bars,_=_prepare({**bench['dataset'],'bars':bench['rows']})
    for i in indexes:
        if deadline and datetime.now(timezone.utc)>=deadline:raise ValueError('TOTAL_RESEARCH_BUDGET_EXHAUSTED')
        bar=bars[i];day=bar.date
        if rows[i].get('valuationCarriedForward') and any(e.get('exDate')==day for e in events):raise ValueError('TOTAL_ACTION_DURING_HALT_VALUATION_UNSUPPORTED')
        for e in events:
            if e['type']=='suspension' or e['exDate']!=day:continue
            qty=rights.get(e['id'],0)
            if e['type']=='share-ratio':p.shares(e,qty);flows.append({'eventId':e['id'],'date':day,'kind':'shares','entitledQuantity':qty,'quantityAfter':p.quantity,'bookCostUnchanged':str(p.bookCost)})
            elif mode!='price' and qty:
                gross=_cents(D(qty)*D(e['cashPerShare'])*100);tax=_cents(D(gross)*tax_bps/10000)
                if e.get('payDate') is None:raise ValueError('TOTAL_PAYMENT_DATE_UNKNOWN')
                receivables[e['id']]={'net':gross-tax,'gross':gross,'tax':tax,'payDate':e['payDate']}
                flows.append({'eventId':e['id'],'date':day,'kind':'dividend-entitlement','quantity':qty,'gross':str(gross),'taxAssumption':str(tax),'receivable':str(gross-tax)})
        opening=p.cash+_cents(bar.open*p.quantity*100)+sum(r['net'] for r in receivables.values())
        signal=indicators(causal['bars'],bm['bars'],i);side=decision(strategy,signal,spec['kind'])
        if not all(_available(bars[j],day) and _available(benchmark_bars[j],day) for j in range(i-61,i)):side='HOLD'
        if any(rows[j].get('valuationCarriedForward') or bench['rows'][j].get('valuationCarriedForward') for j in range(i-61,i)):side='HOLD'
        if side=='BUY' and p.quantity or side=='SELL' and not p.quantity:side='HOLD'
        reinvesting=mode=='reinvest' and p.quantity>0 and dividend_budget>0 and side!='SELL'
        if reinvesting:side='BUY'
        if side in {'BUY','SELL'}:
            ref=_limit_reference(bars[i-1].close,day,events);tick=D(spec['tick']);limit=D(spec['limitBps'])/10000
            upper=(ref*(1+limit)/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick;lower=(ref*(1-limit)/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
            fill=p._quote(side,100,bar.open)[0]
            reason='suspended-valuation-only' if not rows[i].get('tradable',True) else 'zero-volume-ex-post-no-fill' if bar.volume<=0 else 'opening-limit-queue-unmodelled' if side=='BUY' and bar.open>=upper or side=='SELL' and bar.open<=lower else 'fill-outside-limits' if not lower<=fill<=upper else 'fill-outside-observed-range' if not D(rows[i]['low'])<=fill<=D(rows[i]['high']) else None
            order={'date':day,'signalAsOf':signal['signalAsOf'],'side':side,'kind':'dividend-reinvestment' if reinvesting else 'strategy','status':'not-filled','reason':reason}
            if reason is None:
                previous=len(p.trades);old_cash=p.cash
                if side=='BUY':p.buy(day,bar.open,min(dividend_budget,p.cash) if reinvesting else p.cash)
                else:p.sell(day,bar.open)
                spent=max(0,old_cash-p.cash);dividend_budget=max(0,dividend_budget-spent)
                order.update(status='filled' if len(p.trades)>previous else 'not-filled',reason='next-open-raw-price' if len(p.trades)>previous else 'cash-lot-t1-fee-or-cap')
            orders.append(order)
        # Payment is not assumed usable at that day's open. Rights survive sales.
        for key,r in list(receivables.items()):
            if r['payDate']<=day:
                p.cash+=r['net'];dividend_budget+=r['net'];gross_paid+=r['gross'];tax_paid+=r['tax']
                flows.append({'eventId':key,'date':day,'kind':'cash-credited-at-close','gross':str(r['gross']),'taxAssumption':str(r['tax']),'net':str(r['net'])});del receivables[key]
        for e in events:
            if e.get('recordDate')==day:rights[e['id']]=p.quantity
        receivable=sum(r['net'] for r in receivables.values());equity=p.cash+_cents(bar.close*p.quantity*100)+receivable
        curve.append({'date':day,'equity':str(equity),'cash':str(p.cash),'quantity':p.quantity,'dividendReceivable':str(receivable),'bookCost':str(p.bookCost),'realizedPricePnl':str(p.realizedPnl),'externalFlow':'0','openingEquityBeforeFlow':str(opening)})
    # Shared metric arithmetic expects the declared research capital.
    from .strategy_lab import _metrics,stats,pct
    from math import sqrt
    m=_metrics(curve,p.trades,cfg['initialCash'])
    if m:
        span=(date.fromisoformat(curve[-1]['date'])-date.fromisoformat(curve[0]['date'])).days
        if len(curve)<252 or span<365:m['annualizedReturnBps']=None
        previous=D(cfg['initialCash']);returns=[]
        for row in curve:now=D(row['equity']);returns.append(now/previous-1);previous=now
        vol=stats(returns);mean=sum(returns)/len(returns);avg=sum(D(r['equity']) for r in curve)/len(curve)
        m.update(sharpeRfZero=format((mean/vol*252).quantize(D('.01')),'f') if vol and len(curve)>=60 else None,
          oneWayTurnoverPct=pct(sum((D(t['gross']) for t in p.trades),D(0))/avg) if avg else None,
          dividendGrossPaid=str(gross_paid),dividendTaxAssumed=str(tax_paid),dividendNetPaid=str(gross_paid-tax_paid),dividendReceivable=curve[-1]['dividendReceivable'],
          totalReturnIncludesExternalFunding=False,riskFreeRateAssumption='0% annual; 252-session risk scaling',annualizationSuppressed=m['annualizedReturnBps'] is None)
    return {'metrics':m,'equityCurve':curve,'trades':p.trades,'orders':orders,'eventCashFlows':flows,'mode':mode,'dividendTaxBpsAssumption':tax_bps,'cashSettledAtEnd':not receivables,'usesBrokerAccount':False}

def compare(dataset,benchmark,spec,doc,*,start=None,end=None,allow_synthetic=False,source_root=None,deadline=None):
    synthetic=dataset['quality']['status']=='synthetic' or benchmark['quality']['status']=='synthetic'
    if synthetic and not allow_synthetic:raise ValueError('TOTAL_SYNTHETIC_NOT_PRODUCTION')
    if dataset.get('symbol')!=spec['symbol'] or dataset.get('kind')!=spec['kind']:raise ValueError('TOTAL_INSTRUMENT_IDENTITY_MISMATCH')
    from .corporate_actions import validate
    validate(doc,synthetic=synthetic)
    events=events_for(doc,spec['symbol']);bevents=events_for(doc,benchmark['symbol']);rows=valuation_rows(dataset,events);brows=valuation_rows(benchmark,bevents)
    if [r['date'] for r in rows]!=[r['date'] for r in brows] or len(rows)<126:raise ValueError('TOTAL_HISTORY_OR_BENCHMARK_INSUFFICIENT')
    _prepare({**dataset,'bars':rows,'calendar':{'name':'XSHG','sessions':[r['date'] for r in rows]}});_prepare({**benchmark,'bars':brows,'calendar':{'name':'XSHG','sessions':[r['date'] for r in brows]}})
    start=start or PARTITIONS['train']['from'];end=min(end or rows[-1]['date'],rows[-1]['date']);indexes=[i for i in range(61,len(rows)) if start<=rows[i]['date']<=end]
    if not indexes:raise ValueError('TOTAL_NO_EVALUATION_SESSIONS')
    cov=coverage(doc,spec['symbol'],rows[indexes[0]]['date'],rows[indexes[-1]]['date'])
    if not synthetic:
        from pathlib import Path
        status=evidence_status(doc,spec['symbol'],rows[indexes[0]]['date'],rows[indexes[-1]]['date'],source_root or Path(__file__).resolve().parents[1])
        if not status['sourceFilesVerified']:cov['complete']=False;cov['missingSourceIds']=status['missingSourceIds']
    tax=0 if spec['kind']=='etf' else 2000
    # Price mode remains available with explicitly incomplete action coverage.
    modes=['price','cash','reinvest'] if cov['complete'] else ['price']
    if cov['complete'] and tax:modes+=['cash-before-dividend-tax','reinvest-before-dividend-tax']
    results={};bench_input={'rows':brows,'events':bevents,'dataset':benchmark,'subjectDataset':dataset}
    for mode in modes:
        results[mode]={}
        actual_mode=mode.split('-')[0];rate=0 if mode.endswith('before-dividend-tax') else tax
        for strategy in STRATEGIES:
            item=simulate(rows,bench_input,spec,events,strategy,indexes,mode=actual_mode,tax_bps=rate,deadline=deadline)
            item['applicable']=not(strategy=='relative_momentum_60' and spec['symbol']==benchmark['symbol'])
            item['partitions']={}
            for key,span in PARTITIONS.items():
                ix=[i for i in indexes if span['from']<=rows[i]['date']<=span['to']]
                item['partitions'][key]={'from':rows[ix[0]]['date'],'to':rows[ix[-1]]['date'],**simulate(rows,bench_input,spec,events,strategy,ix,mode=actual_mode,tax_bps=rate,deadline=deadline)} if ix else None
            results[mode][strategy]=item
    return {'format':'goose-event-return-study','version':1,'synthetic':synthetic,'symbol':spec['symbol'],'from':rows[indexes[0]]['date'],'to':rows[indexes[-1]]['date'],'modes':results,'coverage':cov,'config':CONFIG,
      'signalBasis':'causal-forward-event-index; raw published times assumed next session where absent','usesVendorQfq':False,'pointInTimeVintageCertified':False,'universeSurvivorshipFree':False,'completeCurrentHorizon':cov['complete'],
      'benchmarkActionCoverage':coverage(doc,benchmark['symbol'],rows[max(0,indexes[0]-61)]['date'],rows[indexes[-1]]['date']),
      'taxAssumption':('本次已核验区间的ETF现金分配按公告暂免所得税。' if spec['kind']=='etf' and cov['complete'] else '当前区间企业行为未完整核验，未输出含息结果或认证税负。' if not cov['complete'] else '股票统一预留20%仅为保守研究假设，并列0%税负对照，不是持有期差别税的真实复原。'),
      'limitations':['实际成交始终使用未复权价；因果研究指数不作为成交价格，也不再把前复权收益与现金分红相加。','股权登记日收盘确定权益；除息日确认应收，发放日收盘入现金，下个交易日才可能再投资；非券商到账证明。','留存/再投资使用同一信号和交易条件；再投资限100份/股及费用，现金不足保留，不制造小数份额。','历史行情缺少当时版本与发布时间证据，仍非严格点时认证；观察池有选择/幸存者偏差。','完整覆盖只针对明确核验的日期区间；缺失期间不补造分红、拆分或现金到账。','停牌日只作明确标记的滞后估值，不作可成交行情；含滞后标记的指标窗口暂停新信号。停牌期间发生除权而无法还原估值时阻断。']}
