"""Complete-session indicators, separate from live quotes and total-return claims."""
from datetime import datetime,time,timedelta
from decimal import Decimal as D,localcontext

def statistics(rows):
 with localcontext() as ctx:
  ctx.prec=60
  close=[D(row['close']) for row in rows];peak=close[0];draw=D(0)
  for value in close:peak=max(peak,value);draw=min(draw,value/peak-1)
  returns=[close[i]/close[i-1]-1 for i in range(1,len(close))]
  mean=sum(returns,D(0))/len(returns)
  variance=sum((r-mean)**2 for r in returns)/max(1,len(returns)-1)
  volumes=[D(row['volume']) for row in rows[-20:]];base=sum(volumes[:-1],D(0))/D(len(volumes)-1)
  return {'through':rows[-1]['date'],'close':str(close[-1]),'ma20':format(sum(close[-20:],D(0))/20,'.6f'),'maxPriceDrawdownPct':format(draw*100,'.2f'),'annualizedPriceVolatilityPct':format(variance.sqrt()*D(252).sqrt()*100,'.2f'),'volumeRatio':format(volumes[-1]/base,'.2f') if base else None}

def previous_session_metrics(dataset,day):
 from goose_research.market import xshg_sessions
 if not dataset or dataset.get('quality',{}).get('status')!='validated' or dataset.get('adjustment','none')!='none':return None
 sessions=xshg_sessions((datetime.fromisoformat(day)-timedelta(days=45)).date().isoformat(),day)
 previous=[x for x in sessions if x<day][-1]
 rows=[r for r in dataset['bars'] if r['date']<=previous]
 if len(rows)<21 or rows[-1]['date']!=previous:return None
 if [r['date'] for r in rows[-21:]]!=xshg_sessions(rows[-21]['date'],previous):return None
 values=statistics(rows);close=D(rows[-1]['close']);ma=D(values['ma20']);prevclose=D(rows[-2]['close']);prevma=sum(D(r['close']) for r in rows[-21:-1])/20
 return {'through':previous,'previousClose':str(close),'ma20':values['ma20'],'v1Comparison':'above' if close>ma else 'below' if close<ma else 'equal','cross':'up' if prevclose<=prevma and close>ma else 'down' if prevclose>=prevma and close<ma else 'none','maxPriceDrawdownPct':values['maxPriceDrawdownPct'],'annualizedPriceVolatilityPct':values['annualizedPriceVolatilityPct'],'latestVsPrior19VolumeRatio':values['volumeRatio'],'returnBasis':'unadjusted-price-only-dividend-events-not-accounted','corporateActionCaution':True}

def enrich_complete_session(metrics,dataset,adjusted,clock):
 from goose_research.market import BJ,xshg_sessions
 if not metrics:return metrics
 local=clock.astimezone(BJ);day=local.date().isoformat()
 latest=metrics['through']
 if local.time().replace(tzinfo=None)>=time(15,30) and dataset['bars'][-1]['date']==day:latest=day
 rows=[r for r in dataset['bars'] if r['date']<=latest]
 if len(rows)<20 or rows[-1]['date']!=latest:return metrics
 if [r['date'] for r in rows[-20:]]!=xshg_sessions(rows[-20]['date'],latest):return metrics
 result=dict(metrics);current=statistics(rows);ma=D(current['ma20'])
 current['maDistancePct']=format((D(current['close'])/ma-1)*100,'.2f')
 current['maxPriceDrawdownPct20d']=statistics(rows[-20:])['maxPriceDrawdownPct']
 current['basis']='complete-unadjusted-daily-price-only'
 if adjusted and adjusted.get('symbol')==dataset.get('symbol') and adjusted.get('adjustment')=='qfq' and adjusted.get('quality',{}).get('status')=='validated-basis-only' and adjusted.get('quality',{}).get('ohlcAndCalendarValidated') is True:
  qrows=[r for r in adjusted['bars'] if r['date']<=latest]
  if len(qrows)>=20 and [r['date'] for r in qrows]==[r['date'] for r in rows]:
   trend=statistics(qrows);trend.update(basis='separate-provider-qfq-trend-not-total-return',totalReturnVerified=False,through=latest)
   current['qfqTrendDetails']=trend
 result['latestCompletedSession']=current
 return result
