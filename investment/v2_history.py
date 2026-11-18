"""Versioned research snapshots; never a brokerage/account ledger."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from decimal import Decimal as D
import json,re,hashlib
from .holdings import _safe_path
BJ=timezone(timedelta(hours=8))

def _dynamic_outcomes(observations,root,clock):
 """Reuse only hashed, successfully reviewed raw stock histories. No fetch."""
 from .signal_tracking import _next_sessions
 from .radar_market import complete_session
 from .market import _daily_parser
 base=_safe_path(Path(root)/'investment/work/market-discovery/history',missing=True);datasets={}
 for symbol in {i['symbol'] for i in observations}:
  files=sorted(base.glob(symbol+'-*.json')) if base.exists() else []
  if not files:continue
  try:
   doc=json.loads(_safe_path(files[-1],file=True).read_text(encoding='utf8'));sha=doc.pop('sha256')
   canonical=json.dumps(doc,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
   if hashlib.sha256(canonical).hexdigest()!=sha or doc.get('synthetic') or doc.get('review',{}).get('status')!='validated-isolated-price-research':continue
   fetched=doc['review']['fetchedAt']
   for p in (base/'runs').glob('*/observations.json'):
    for obs in json.loads(_safe_path(p,file=True).read_text(encoding='utf8')):
     if obs.get('symbol')!=symbol or obs.get('basis')!='none' or obs.get('receivedAt')!=fetched or not obs.get('rawFile'):continue
     raw=_safe_path(p.parent/obs['rawFile'],file=True).read_bytes()
     if hashlib.sha256(raw).hexdigest()!=obs.get('rawSha256'):continue
     dates=re.findall(rb'"(20\d\d-\d\d-\d\d)"',raw)
     if dates:datasets[symbol]=_daily_parser(raw,symbol,min(dates).decode(),max(dates).decode(),'none')
  except (ValueError,OSError,KeyError):continue
 through=complete_session(clock);results=[]
 for signal in observations:
  day=datetime.fromisoformat(signal['discoveredAt']).astimezone(BJ).date().isoformat();sessions=_next_sessions(day);rows={x['date']:x for x in datasets.get(signal['symbol'],[])};out={}
  for h in (1,5,20):
   due=sessions[h-1] if len(sessions)>=h else None;v={'through':due,'priceReturnPct':None,'maximumCloseDrawdownPct':None}
   if due and due>through:v.update(status='pending',reason='尚未经过观察期')
   elif not due or not all(d in rows and rows[d]['volume']>0 for d in sessions[:h]):v.update(status='unavailable',reason='缺少同口径后续日线或ETF交易制度/价格单位未审阅')
   else:
    entry=D(rows[sessions[0]]['open']);peak=entry;draw=D(0)
    for d in sessions[:h]:close=D(rows[d]['close']);peak=max(peak,close);draw=min(draw,close/peak-1)
    v.update(status='observed-price-only',priceReturnPct=format((D(rows[due]['close'])/entry-1)*100,'.4f'),maximumCloseDrawdownPct=format(draw*100,'.4f'),basis='none; next-session-open to horizon close; not net total return')
   out[str(h)]=v
  results.append({**signal,'horizons':out,'finalOutcome':'20日价格观察完成，非交易盈亏结论' if out['20']['status']=='observed-price-only' else '观察未完成或证据不足'})
 return results

def build(report,root,*,persist=False):
 clock=datetime.fromisoformat(report['generatedAt']);day=clock.astimezone(BJ).date().isoformat()
 previous=[]
 base=_safe_path(Path(root)/'investment/work/runs',missing=True)
 for p in base.glob('*/report.json') if base.exists() else []:
  try:
   if p.stat().st_size>4*1024*1024:continue
   r=json.loads(_safe_path(p,file=True).read_text(encoding='utf8'))
   when=datetime.fromisoformat(r['generatedAt'])
   if when<clock and not r.get('synthetic') and not r.get('testData'):previous.append(r)
  except (OSError,ValueError,KeyError):continue
 previous.sort(key=lambda x:x['generatedAt'],reverse=True)
 from .market import xshg_sessions
 sessions=xshg_sessions((clock-timedelta(days=30)).date().isoformat(),day)
 prior_day=next((d for d in reversed(sessions) if d<day),None)
 items=[]
 for i in report.get('instruments',[]):
  symbol=i['symbol'];q=i.get('quote') or {};t=(i.get('decisionAnalysis') or {}).get('facts',{}).get('technical',{})
  # Compare complete research sessions, not the wall-clock report date. A
  # midnight refresh of yesterday's close must not hide the previous session.
  candidates=[]
  for r in previous:
   for x in r.get('instruments',[]):
    xt=(x.get('decisionAnalysis') or {}).get('facts',{}).get('technical',{});through=xt.get('through')
    if x.get('symbol')==symbol and through and t.get('through') and through<t['through']:
     quote_day=((x.get('quote') or {}).get('sourceTimestamp') or '')[:10]
     candidates.append((through,quote_day==through,r['generatedAt'],r))
  prior=max(candidates,key=lambda x:x[:3])[3] if candidates else None
  research_days=xshg_sessions((clock-timedelta(days=30)).date().isoformat(),t.get('through') or day)
  prior_day=next((d for d in reversed(research_days) if d<(t.get('through') or day)),None)
  before=next((x for x in (prior or {}).get('instruments',[]) if x['symbol']==symbol),None)
  bq=(before or {}).get('quote') or {};bt=((before or {}).get('decisionAnalysis') or {}).get('facts',{}).get('technical',{})
  action=bool(q.get('corporateActionReviewRequired') or bq.get('corporateActionReviewRequired'))
  price_change=None
  if not action and all(x.get('quality',{}).get('unitVerified') is True for x in (q,bq)) and q.get('sourceTimestamp') and bq.get('sourceTimestamp'):
   if q['sourceTimestamp'][:10]!=bq['sourceTimestamp'][:10] and q.get('adjustment')==bq.get('adjustment')=='none':
    try:price_change=format((D(q['price'])/D(bq['price'])-1)*100,'.4f')
    except (KeyError,ArithmeticError):pass
  def relation(x):
   try:return 'above' if D(x['completedClose'])>D(x['ma20']) else 'below' if D(x['completedClose'])<D(x['ma20']) else 'equal'
   except (KeyError,TypeError,ArithmeticError):return None
  old,new=relation(bt),relation(t);change='无同口径较早完整日，未判断变化'
  if old and new and bt.get('through','')<t.get('through','') and not action:
   change='趋势改善（完整日由MA20下方转上方）' if old=='below' and new=='above' else '趋势恶化（完整日由MA20上方转下方）' if old=='above' and new=='below' else 'MA20位置未发生已识别跨越'
  positions=[p for p in report.get('portfolio',{}).get('positions',[]) if p['symbol']==symbol]
  price_return=None
  if not action and len(positions)==1 and positions[0].get('costPrice') and q.get('price') and q.get('quality',{}).get('unitVerified') is True:
   try:
    if D(positions[0]['costPrice'])>0:price_return=format((D(q['price'])/D(positions[0]['costPrice'])-1)*100,'.4f')
   except ArithmeticError:pass
  items.append({'symbol':symbol,'name':i.get('name'),'priorTradingDay':prior_day,'comparisonReportId':(prior or {}).get('id'),
   'priceOnlyVsDatedDisplayCostPct':price_return,'cumulativeTotalReturnPct':None,'returnBasis':'历史显示成本价差，非真实累计投资收益；现金、分红、税费、份额变化仍待对账',
   'riskAttention':'提高警惕并重核持有依据；不自动触发减仓或退出' if '恶化' in change or action else '按原条件继续研究，不因暂无变化而推断安全',
   'currentResearchThrough':t.get('through'),'previousResearchThrough':bt.get('through'),'comparisonIsConsecutive':bool(bt.get('through') and bt.get('through')==prior_day),'trendChange':change,'priceChangePct':price_change,
   'corporateActionReviewRequired':action,'corporateActionMeaning':'识别除权除息状态；未证明分红金额、到账或成本已调整' if action else '没有新增已识别前缀不等于企业行为完整',
   'accountStatus':report.get('portfolio',{}).get('status'),'snapshotAsOf':report.get('portfolio',{}).get('asOf'),
   'conditions':(i.get('decisionAnalysis') or {}).get('conditions',[]),'dataGaps':(i.get('decisionAnalysis') or {}).get('dataGaps',[]),
   'history':[{'reportId':r['id'],'generatedAt':r['generatedAt'],'signal':x.get('signal')} for r in previous[:5] for x in r.get('instruments',[]) if x['symbol']==symbol]})
 discoveries=[]
 for i in report.get('marketDiscovery',{}).get('items',[]):
  h=i.get('historyReview') or {};f=h.get('features') or {};qualified=h.get('status')=='validated-isolated-price-research'
  discoveries.append({'symbol':i['symbol'],'name':i['name'],'kind':i['kind'],'industry':i.get('industry'),
   'source':i.get('source'),'sourceTime':i.get('sourceTimestamp'),'fetchedAt':i.get('fetchedAt'),'price':i.get('price'),'adjustment':'none',
   'historyThrough':h.get('through'),'trend':'完整日趋势已核验，规则状态见明细' if qualified else '历史不足/隔离',
   'momentumRelative60Pct':h.get('relative60Pct'),'activityAmountCNY':i.get('amountCNY'),
   'breakout':(h.get('breakout') or {}).get('confirmedOnCompleteSession') if qualified else None,
   'pullback':(h.get('pullback') or {}).get('observedDayDeclineAboveMA20MA60') if qualified else None,
   'reversal':None,'reversalReason':'原规则没有经验证的反转判定，不新增阈值制造反转信号',
   'entryCondition':h.get('nextCondition') or i.get('nextCondition'),'invalidation':h.get('invalidation') or i.get('invalidation'),
   'tradeCandidate':False,'followUpStatus':'等待同口径后续完整日线；无数据时不计算表现'})
 result={'format':'goose-v2-research-history','version':2,'sourceReportId':report['id'],'generatedAt':report['generatedAt'],
  'holdings':items,'discoveries':discoveries,'stockDiscoveries':[i for i in discoveries if i['kind']=='stock'],'etfDiscoveries':[i for i in discoveries if i['kind']=='etf'],
  'coverageMeaning':'供应商广域列表发现与最多3股历史验证分开；不是每只证券都已完成策略分析。','accountSynced':False}
 if persist:
  if not re.fullmatch(r'[a-f0-9-]{36}',report.get('id','')) or report.get('synthetic') or report.get('testData'):raise ValueError('V2_SOURCE_REJECTED')
  folder=_safe_path(Path(root)/'investment/work/research-history-v2',missing=True);folder.mkdir(parents=True,exist_ok=True)
  file=folder/(report['id']+'.json')
  if not file.exists():
   with file.open('x',encoding='utf8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
  # First discovery means first recorded observation, not the quote's old date.
  first={};observations=[];seen=set()
  for p in folder.glob('*.json'):
   doc=json.loads(_safe_path(p,file=True).read_text(encoding='utf8'))
   for i in doc.get('discoveries',[]):
    first[i['symbol']]=min(first.get(i['symbol'],doc['generatedAt']),doc['generatedAt'])
    key=(i['symbol'],doc['generatedAt'][:10])
    if key not in seen:observations.append({**i,'discoveredAt':doc['generatedAt'],'sourceReportId':doc['sourceReportId']});seen.add(key)
  for i in discoveries:i['firstRecordedDiscoveryAt']=first[i['symbol']]
  result['dynamicTracking']=_dynamic_outcomes(observations,root,clock)
 return result
