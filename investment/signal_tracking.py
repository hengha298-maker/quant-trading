"""Append-only research observations and dated forward price event studies.

Not an order book or a new strategy. Never synthesizes an entry/stop/target;
unavailable prices and not-yet-observed horizons remain None, never zero.
"""
from datetime import datetime, timezone, timedelta
from decimal import Decimal as D, DecimalException, ROUND_HALF_UP
from pathlib import Path
import hashlib,json,re
from .holdings import _safe_path,_canonical
from .radar_market import catalog,complete_session
from .market import xshg_sessions
from .signal_provenance import load_reports, classify, MAX_RECORD_SECONDS, REVISION as PROVENANCE_REVISION

BJ=timezone(timedelta(hours=8))
HORIZONS=(1,5,20)
PROSPECTIVE_RECORD_MAX_SECONDS=MAX_RECORD_SECONDS  # Existing daily report's six-minute validity window.
LIMITATIONS=[
 '这是研究条件的事件跟踪，不是实际交易或策略净收益。',
 '参考成交采用信号日之后的首个交易日原始开盘价；不假定可按信号收盘成交。',
 '价格事件收益不含完整分红、税款和份额变化；不把前复权与现金分红重复相加。',
 '留存池有选择/幸存者偏差；重建记录不是事前实盘记录，尚未证明策略有效。',
]

def _stamp(now=None):
 value=now or datetime.now(timezone.utc)
 if value.tzinfo is None:raise ValueError('AWARE_CLOCK_REQUIRED')
 return value

def _folder(root):return _safe_path(Path(root)/'investment/work/signals',missing=True)

def record(report,root,*,now=None,reconstructed=False):
 """One immutable file per report. Later versions never replace its inputs."""
 if report.get('format')!='goose-live-research' or report.get('version')!=1 or report.get('synthetic') or report.get('testData'):raise ValueError('SIGNAL_SOURCE_REJECTED')
 identifier=report.get('id','')
 if not re.fullmatch(r'[a-f0-9-]{36}',identifier):raise ValueError('SIGNAL_REPORT_ID_INVALID')
 clock=_stamp(now);generated=datetime.fromisoformat(report['generatedAt'])
 if generated.tzinfo is None or generated>clock:raise ValueError('SIGNAL_TIME_REJECTED')
 day=generated.astimezone(BJ).date().isoformat();known={s['symbol']:s for s in catalog()['instruments']}
 items=report.get('researchStates',{}).get('items',[]);signals=[]
 if len(items)!=len(known) or {i.get('symbol') for i in items}!=set(known):raise ValueError('SIGNAL_POOL_INCOMPLETE')
 for item in items:
  symbol=item.get('symbol')
  if symbol not in known:continue
  condition={k:item.get(k) for k in ['state','through','modelFingerprint','metConditions','unmetConditions','nextCondition','invalidation','tradeAdmission','history']}
  digest=hashlib.sha256(_canonical(condition)).hexdigest()
  signals.append({'signalId':hashlib.sha256((identifier+'\n'+symbol+'\n'+digest).encode()).hexdigest(),
   'symbol':symbol,'name':known[symbol]['name'],'kind':known[symbol]['kind'],'signalAt':generated.isoformat(),'signalDay':day,
   'state':item.get('state'),'researchThrough':item.get('through'),'sourceReportId':identifier,'sourceEvidenceSha256':digest,
   'modelFingerprint':item.get('modelFingerprint'),'entryCondition':item.get('nextCondition'),
   'metConditions':item.get('metConditions',[]),'unmetConditions':item.get('unmetConditions',[]),
   'invalidation':item.get('invalidation'),'referenceEntryRange':None,'stopPrice':None,'targetRange':None,'riskRewardRatio':None,
   'priceLevelsReason':'原规则没有经完整准入验证的入场区间、止损和目标，保留条件而不虚构价格或盈亏比。',
   'tradeCandidate':False,'tradeAdmission':item.get('tradeAdmission'),
   'retrospectiveEvidence':item.get('history'),'benchmarkSymbol':catalog()['benchmark']})
 retrospective=bool(reconstructed or report.get('cachedReport') or (clock-generated).total_seconds()>PROSPECTIVE_RECORD_MAX_SECONDS)
 from .signal_review import semantic_id
 for signal in signals:signal['signalId']=semantic_id(signal,'reconstructed' if retrospective else 'prospective')
 doc={'format':'goose-research-signal-book','version':1,'reportId':identifier,'signalAt':generated.isoformat(),
  'recordedAt':clock.isoformat(),'origin':'reconstructed-from-immutable-report' if retrospective else 'recorded-with-report',
  'sourceCached':report.get('cachedReport') is True,
  'prospective':not retrospective,'signals':signals,'limitations':LIMITATIONS}
 folder=_folder(root);folder.mkdir(parents=True,exist_ok=True);path=folder/(identifier+'.json')
 if path.exists():
  old=json.loads(_safe_path(path,file=True).read_text(encoding='utf8'))
  if old.get('reportId')!=identifier or [s['sourceEvidenceSha256'] for s in old['signals']]!=[s['sourceEvidenceSha256'] for s in signals]:raise ValueError('SIGNAL_SOURCE_CHANGED_NO_OVERWRITE')
  return old
 with path.open('x',encoding='utf8') as stream:json.dump(doc,stream,ensure_ascii=False,indent=2,allow_nan=False)
 return doc

def histories(root):
 """Reuse original checksum-verified raw cache; never fetch prices here."""
 from .radar_market import _cached
 found={};folder=_safe_path(Path(root)/'investment/work/radar/market',missing=True)
 for spec in catalog()['instruments']:
  try:
   doc=_cached(folder,spec['symbol'])
   if doc and doc['raw']['quality'].get('status')=='validated':found[spec['symbol']]=doc['raw']
  except (ValueError,OSError,KeyError):continue
 return found

def _returns(start,end):
 try:
  a,b=D(start),D(end)
  if not a.is_finite() or not b.is_finite() or a<=0 or b<=0:raise ValueError('POSITIVE_FINITE_PRICE_REQUIRED')
  return format((b/a-1)*100,'.4f')
 except DecimalException:raise ValueError('PRICE_RETURN_INVALID') from None


def _next_sessions(signal_day):
 from goose_research.market import calendar_supported_through
 start=datetime.fromisoformat(signal_day).date()+timedelta(days=1)
 end=min((start+timedelta(days=55)).isoformat(),calendar_supported_through())
 return xshg_sessions(start.isoformat(),end) if start.isoformat()<=end else []


def _validated(datasets,clock):
 """Validate each reused dataset once; bad symbols stay unavailable individually."""
 from goose_research.market import validate_dataset
 specs={s['symbol']:s for s in catalog()['instruments']};valid={};errors={}
 for symbol,value in datasets.items():
  try:
   if symbol not in specs or not isinstance(value,dict) or value.get('symbol')!=symbol or value.get('kind')!=specs[symbol]['kind']:raise ValueError('SIGNAL_DATASET_IDENTITY')
   doc=validate_dataset(value)
   if doc['quality']['status']!='validated' or doc['quality']['unitVerified'] is not True:raise ValueError('SIGNAL_DATASET_UNVALIDATED')
   fetched=datetime.fromisoformat(doc['fetchedAt'].replace('Z','+00:00'))
   if fetched>clock:raise ValueError('SIGNAL_DATASET_NOT_AVAILABLE_AT_EVALUATION')
   tick=D(specs[symbol]['tick'])
   if any(D(row[k])%tick for row in doc['bars'] for k in ('open','high','low','close')):raise ValueError('SIGNAL_PRICE_TICK_INVALID')
   valid[symbol]=doc
  except (ValueError,TypeError,KeyError,DecimalException):errors[symbol]='后续数据身份、单位、正有限OHLC、日期或可用时间未通过原数据校验。'
 return valid,errors

def _cost_scenario(ds,first,last,spec):
 """Reuse the exact existing lot/tick/slippage/fee/T+1 cost model."""
 from .strategy_lab import CONFIG,TickPortfolio
 def unavailable(reason):return {'status':'unavailable','reason':reason,'netPriceReturnPct':None}
 if first['date']==last['date']:return unavailable('次日开盘至当日收盘不足T+1，不假定可卖出')
 if ds.get('quality',{}).get('corporateActionsVerified') is not True:return unavailable('公司行为、分红或份额变化未完整核验，不给出可成交净收益情景')
 if any(int(b.get('volume',0))<=0 for b in (first,last)):return unavailable('零成交量不能证明可成交')
 rows=ds['bars'];indexes={r['date']:i for i,r in enumerate(rows)}
 p=TickPortfolio(dict(CONFIG),spec['kind'],spec['symbol'],spec['tick'])
 for side,bar,price in [('BUY',first,D(first['open'])),('SELL',last,D(last['close']))]:
  index=indexes.get(bar['date'])
  if index is None or index<1:return unavailable('缺少前收盘，涨跌停参考无法核验')
  previous=D(rows[index-1]['close']);tick=D(spec['tick']);limit=D(spec['limitBps'])/10000
  upper=(previous*(1+limit)/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
  lower=(previous*(1-limit)/tick).to_integral_value(rounding=ROUND_HALF_UP)*tick
  if side=='BUY' and price>=upper or side=='SELL' and price<=lower:return unavailable('涨跌停价位排队无法还原；沿用原模型不可成交门控')
  fill=p._quote(side,100,price)[0]
  if not lower<=fill<=upper:return unavailable('滑点与最小报价单位调整后的成交价越过涨跌停')
  if not D(bar['low'])<=fill<=D(bar['high']):return unavailable('滑点后的参考成交价超出已知日线高低范围')
 p.buy(first['date'],D(first['open']),p.cash)
 if not p.quantity:return {'status':'unavailable','reason':'资金/最小交易单位/费用模型无法成交','netPriceReturnPct':None}
 p.sell(last['date'],D(last['close']))
 if p.quantity:return {'status':'unavailable','reason':'T+1或模型可卖约束未满足','netPriceReturnPct':None}
 return {'status':'hypothetical-price-only','netPriceReturnPct':_returns(str(CONFIG['initialCash']),str(p.cash)),
  'feesCents':str(sum(int(t['commission'])+int(t['stampDuty']) for t in p.trades)),
  'slippageCents':str(sum(int(t['slippage']) for t in p.trades)),
  'config':CONFIG,'reason':'复用既有成本假设；未模拟队列、停复牌和完整企业行为，不代表实际可成交净收益。'}

def _evaluate(signal,datasets,errors,clock):
 base={'signalId':signal['signalId'],'horizons':{}}
 try:
  through=complete_session(clock);sessions=_next_sessions(signal['signalDay'])
 except (ValueError,IndexError):
  return {**base,'status':'unavailable','reason':'后续交易日历未核验','horizons':{str(h):{'status':'unavailable','priceReturnPct':None} for h in HORIZONS}}
 ds=datasets.get(signal['symbol']);benchmark=datasets.get(signal['benchmarkSymbol'])
 specs={s['symbol']:s for s in catalog()['instruments']}
 rows={r['date']:r for r in ds.get('bars',[])} if ds else {}
 bm={r['date']:r for r in benchmark.get('bars',[])} if benchmark else {}
 for h in HORIZONS:
  target=sessions[h-1] if len(sessions)>=h else None
  result={'through':target,'entryDay':sessions[0] if sessions else None,'priceReturnPct':None,'benchmarkPriceReturnPct':None,'excessVsBenchmarkPct':None,'totalReturnPct':None}
  if target is None:result.update(status='unavailable',reason='未核验的后续交易日历')
  elif target>through:result.update(status='pending',reason='尚未经过'+str(h)+'个完整交易日，不填零、不预测')
  elif not all(d in rows for d in sessions[:h]):result.update(status='unavailable',reason=errors.get(signal['symbol'],'同口径后续日线缺失或单位未核验，不向未来补值'))
  elif any(rows[d]['volume']<=0 for d in sessions[:h]):result.update(status='unavailable',reason='后续窗口有零成交量，停复牌或滞后估值未核验，不计作可比表现')
  elif any(r.get('publishedAt') is not None and datetime.fromisoformat(r['publishedAt'].replace('Z','+00:00'))>clock for r in (rows[d] for d in sessions[:h])):result.update(status='unavailable',reason='后续完整日线尚未发布，不读取未来发布数据')
  else:
   first,last=rows[sessions[0]],rows[target]
   result.update(status='observed-price-only',referenceEntryPrice=first['open'],referenceExitPrice=last['close'],priceReturnPct=_returns(first['open'],last['close']),
    priceBasis='none / first following session open to horizon close',source=ds.get('source'),fetchedAt=ds.get('fetchedAt'),
    buyHoldComparison='同持有期同标的事件收益与买持相同，不能单独证明择时优势；完整区间策略对照见原回测。',
    corporateActionsVerified=False,totalReturnReason='完整分红/拆分/停复牌证据不足，不能计算含息总收益。')
   peak=D(first['open']);draw=D(0)
   for day in sessions[:h]:
    close=D(rows[day]['close']);peak=max(peak,close);draw=min(draw,close/peak-1)
   result['maximumCloseDrawdownPct']=format(draw*100,'.4f')
   result['maximumFavorableExcursionPct']=format((max(D(rows[d]['high']) for d in sessions[:h])/D(first['open'])-1)*100,'.4f')
   result['maximumAdverseExcursionPct']=format((min(D(rows[d]['low']) for d in sessions[:h])/D(first['open'])-1)*100,'.4f')
   result['excursionMeaning']='已完成后续窗口的日线高低价事件幅度，不代表可成交收益；无日内先后路径。'
   try:result['costScenario']=_cost_scenario(ds,first,last,specs[signal['symbol']])
   except (ValueError,KeyError,TypeError,DecimalException):result['costScenario']={'status':'unavailable','netPriceReturnPct':None,'reason':'成本情景无法核验'}
   if all(d in bm and bm[d]['volume']>0 and (bm[d].get('publishedAt') is None or datetime.fromisoformat(bm[d]['publishedAt'].replace('Z','+00:00'))<=clock) for d in sessions[:h]):
    result['benchmarkPriceReturnPct']=_returns(bm[sessions[0]]['open'],bm[target]['close'])
    result['excessVsBenchmarkPct']=format(D(result['priceReturnPct'])-D(result['benchmarkPriceReturnPct']),'.4f')
    result['benchmarkBasis']='沪深300ETF价格代理 '+signal['benchmarkSymbol']+'，非指数含息收益'
  base['horizons'][str(h)]=result
 base['status']='research-only';return base


def evaluate(signal,datasets,*,now=None):
 """Use validated, available data for completed sessions after signal creation."""
 clock=_stamp(now);data,errors=_validated(datasets,clock)
 return _evaluate(signal,data,errors,clock)

def view(root,*,datasets=None,now=None,limit=32,current_report=None):
 if type(limit) is not int or not 1<=limit<=32:raise ValueError('SIGNAL_VIEW_LIMIT_1_TO_32')
 clock=_stamp(now)
 folder=_folder(root);books=[]
 for path in folder.glob('*.json') if folder.exists() else []:
  try:
   if path.stat().st_size>2*1024*1024:continue
   doc=json.loads(_safe_path(path,file=True).read_text(encoding='utf8'))
   if doc.get('format')=='goose-research-signal-book' and doc.get('version')==1:books.append(doc)
  except (ValueError,OSError):continue
 books.sort(key=lambda b:str(b.get('signalAt','')),reverse=True);raw_data=datasets if datasets is not None else histories(root)
 data,errors=_validated(raw_data,clock);all_signals=[];rejected=0;provenance_codes={}
 reports=load_reports(root,current_report)
 for b in books:
  try:
   if not isinstance(b.get('signals'),list) or any(not isinstance(s,dict) or not all(isinstance(s.get(k),str) for k in ('signalId','symbol','signalDay','benchmarkSymbol')) for s in b['signals']):continue
   original=reports.get(b.get('reportId'),({},None))[0]
   for signal in b['signals']:
    binding=classify(b,signal,original,clock)
    for code in binding['codes']:provenance_codes[code]=provenance_codes.get(code,0)+1
    if binding['cohort'] not in {'prospective','reconstructed'}:
     rejected+=1;continue
    prospective=binding['cohort']=='prospective'
    all_signals.append({**signal,'origin':'recorded-with-report' if prospective else 'reconstructed-from-immutable-report',
                        'prospective':prospective,'recordedAt':b['recordedAt'],'sourceProof':True})
  except (ValueError,TypeError,KeyError):continue
 # Keep one latest observation per symbol/day, while old source books remain
 # immutable. The display must reserve room for matured 1/5/20-day cohorts.
 unique=[];seen=set()
 for signal in all_signals:
  key=(signal['symbol'],signal['researchThrough'],signal.get('modelFingerprint'),signal['prospective'],signal.get('state'),signal.get('entryCondition'))
  if key in seen:continue
  seen.add(key);unique.append(signal)
 selected=[];chosen=set();latest_symbols=set()
 def add(signal,role):
  if signal['signalId'] in chosen or len(selected)>=limit:return False
  chosen.add(signal['signalId']);selected.append({**signal,'displayRole':role});return True
 for signal in unique:
  if signal['symbol'] in latest_symbols:continue
  latest_symbols.add(signal['symbol']);add(signal,'latest-observation')
  if len(selected)>=min(16,limit):break
 try:through=complete_session(clock)
 except ValueError:through=None
 dates={}
 for day in {s['signalDay'] for s in unique}:
  try:dates[day]=_next_sessions(day)
  except (ValueError,TypeError,KeyError):dates[day]=[]
 remaining=limit-len(selected);quotas={h:remaining//3+(j<remaining%3) for j,h in enumerate((20,5,1))}
 for h in (20,5,1):
  if quotas[h]<=0:continue
  count=0
  for signal in unique:
   ds=dates[signal['signalDay']]
   if not through or len(ds)<h or ds[h-1]>through:continue
   if add(signal,'matured-'+str(h)+'d'):
    count+=1
    if count>=quotas[h]:break
   if len(selected)>=limit:break
 for signal in unique:
  if len(selected)>=limit:break
  add(signal,'additional-history')
 first_seen={}
 for s in all_signals:first_seen[s['symbol']]=min(first_seen.get(s['symbol'],s['signalDay']),s['signalDay'])
 items=[{**s,'firstRecordedSignalDay':first_seen[s['symbol']],'followUp':_evaluate(s,data,errors,clock)} for s in selected]
 statistics=[]
 # All unique dated records, not only the compact display sample. Cohorts
 # never mix reconstructed evidence with genuinely recorded observations.
 for prospective in (True,False):
  cohort=[s for s in unique if s['prospective']==prospective]
  outcomes=[_evaluate(s,data,errors,clock) for s in cohort]
  for h in HORIZONS:
   rows=[o['horizons'][str(h)] for o in outcomes if o['horizons'][str(h)].get('status')=='observed-price-only']
   returns=[D(x['priceReturnPct']) for x in rows];wins=[v for v in returns if v>0];losses=[-v for v in returns if v<0]
   statistics.append({'cohort':'prospective-research' if prospective else 'reconstructed-research','horizon':h,'observations':len(cohort),'maturedUsable':len(returns),
    'positivePriceEventRatePct':format(D(len(wins))/len(returns)*100,'.2f') if returns else None,
    'meanPriceEventReturnPct':format(sum(returns)/len(returns),'.4f') if returns else None,
    'medianPriceEventReturnPct':format(sorted(returns)[len(returns)//2] if len(returns)%2 else (sorted(returns)[len(returns)//2-1]+sorted(returns)[len(returns)//2])/2,'.4f') if returns else None,
    'meanExcessVsBenchmarkPct':format(sum(D(x['excessVsBenchmarkPct']) for x in rows if x.get('excessVsBenchmarkPct') is not None)/sum(x.get('excessVsBenchmarkPct') is not None for x in rows),'.4f') if any(x.get('excessVsBenchmarkPct') is not None for x in rows) else None,
    'averageWinLossRatio':format((sum(wins)/len(wins))/(sum(losses)/len(losses)),'.4f') if wins and losses else None,
    'worstEventCloseDrawdownPct':min((D(x['maximumCloseDrawdownPct']) for x in rows),default=None),
    'strategyWinRate':None,'strategyEffectiveness':'尚无完整准入交易样本，不能将观察项上涨比例当作策略胜率。','basis':'unadjusted-price-event-study-not-net-total-return'})
 for row in statistics:
  if row['worstEventCloseDrawdownPct'] is not None:row['worstEventCloseDrawdownPct']=str(row['worstEventCloseDrawdownPct'])
 return {'format':'goose-signal-followup','version':1,'evaluatedAt':clock.isoformat(),'reportCount':len(books),'recordCount':len(all_signals),
  'displayedCount':len(items),'tradeCandidateCount':0,'prospectiveRecordCount':sum(s['prospective'] for s in all_signals),
  'provenanceRevision':PROVENANCE_REVISION,'provenanceRejectedCount':rejected,'provenanceCodes':provenance_codes,
  'selection':'latest-per-symbol-and-matured-1-5-20d-cohorts','statistics':statistics,
  'performanceConclusion':'研究记录刚建立，未形成足够前瞻样本；不报告胜率或宣称策略有效。','items':items,'limitations':LIMITATIONS}
